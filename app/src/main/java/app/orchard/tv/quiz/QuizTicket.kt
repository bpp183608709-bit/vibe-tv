package app.orchard.tv.quiz

import android.util.Base64
import org.json.JSONException
import org.json.JSONObject
import java.math.BigInteger
import java.security.AlgorithmParameters
import java.security.KeyFactory
import java.security.MessageDigest
import java.security.PublicKey
import java.security.Signature
import java.security.spec.ECGenParameterSpec
import java.security.spec.ECParameterSpec
import java.security.spec.ECPoint
import java.security.spec.ECPublicKeySpec
import java.security.spec.X509EncodedKeySpec
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The award ticket, verified on the television with no network (docs/QUIZ.md §7).
 *
 * The server signs `AQ1.<base64url payload>.<base64url signature>`; this file decides whether the
 * minutes it claims may be added to today. Everything the server checked is checked again here, and
 * that is not redundant: the television is the only party that knows what it has already spent, and
 * it is the party a copied backup or a pasted string arrives at.
 *
 * **The public key is the whole security model.** With ES256 the APK carries a public key, so
 * unpacking it yields nothing that can mint a ticket — which is why the format is not HMAC in
 * production (G3). The HMAC branch below exists for a development build and says so.
 *
 * Nothing here is Android-specific beyond base64: the tests drive it with a key pair generated in
 * the test, so the checks are exercised without a server.
 */
object QuizTicket {

    /** The only prefix this version understands; a ticket with another one is from the future. */
    const val PREFIX = "AQ1"

    const val PAYLOAD_VERSION = 1

    /** Thirty minutes, and nothing else: the same allow-list the server signs against (B2/B3). */
    const val AWARD_MS = 30 * 60_000L

    /** Clock difference tolerated between the TV and the server, for the `iat` check. */
    const val CLOCK_SKEW_MS = 5 * 60_000L

    /** Why a ticket was refused. For the log and the settings page, never for the child. */
    enum class Refusal {
        EMPTY, BAD_SHAPE, BAD_BASE64, BAD_SIGNATURE, BAD_PAYLOAD, BAD_VERSION, BAD_ALG,
        BAD_AWARD, WRONG_DEVICE, WRONG_DAY, BAD_TIME, NOT_YET, EXPIRED, BAD_JTI, SPENT,
    }

    class Refused(val reason: Refusal) : Exception(reason.name)

    /** The claims, once they have been paid for with a valid signature. */
    data class Ticket(
        val jti: String,
        val did: String,
        val day: String,
        val ms: Long,
        val iat: Long,
        val exp: Long,
    )

    /**
     * Checks a ticket belongs to this television, this day, and has not been spent.
     *
     * The order is the server's, and it matters: signature before claims (an edited payload must not
     * even be parsed as one), and the day before the expiry — a ticket from yesterday is void however
     * many hours it has left, because the counter it adds to was reset at four in the morning.
     *
     * [publicKey] is the ES256 key, [hmacSecret] the development fallback; exactly one is used,
     * chosen by the ticket's own `alg` only after the signature has been checked against it.
     */
    fun verify(
        raw: String,
        publicKey: PublicKey?,
        hmacSecret: ByteArray?,
        deviceId: String,
        dayKey: String,
        nowMs: Long,
        spentJti: Set<String> = emptySet(),
    ): Ticket {
        if (raw.isBlank()) throw Refused(Refusal.EMPTY)
        val parts = raw.split('.')
        if (parts.size != 3 || parts[0] != PREFIX) throw Refused(Refusal.BAD_SHAPE)

        val signedText = "${parts[0]}.${parts[1]}"
        val signature = decode(parts[2])

        // The signature is checked before the payload is read. Doing it the other way round would
        // mean parsing bytes nobody has vouched for — the classic way a "verify" becomes a parser.
        val alg = algorithmOf(parts[1])
        val ok = when (alg) {
            "ES256" -> publicKey != null && verifyEs256(publicKey, signedText.toByteArray(Charsets.US_ASCII), signature)
            // A build that carries a real public key never accepts a shared-secret ticket, even if it
            // was also given the secret: that combination is exactly the downgrade worth refusing.
            "HS256" -> publicKey == null && hmacSecret != null && verifyHmac(hmacSecret, signedText, signature)
            else -> false
        }
        if (!ok) throw Refused(Refusal.BAD_SIGNATURE)

        val claims = claimsOf(parts[1])
        if (claims.optInt("v", -1) != PAYLOAD_VERSION) throw Refused(Refusal.BAD_VERSION)
        // A ticket signed with one algorithm must not be accepted by the other's verifier, even when
        // both signatures happen to check out.
        if (claims.optString("alg") != alg) throw Refused(Refusal.BAD_ALG)

        if (claims.optLong("ms", -1L) != AWARD_MS) throw Refused(Refusal.BAD_AWARD)
        if (claims.optString("did") != deviceId) throw Refused(Refusal.WRONG_DEVICE)
        if (claims.optString("day") != dayKey) throw Refused(Refusal.WRONG_DAY)

        val iat = claims.optLong("iat", -1L)
        val exp = claims.optLong("exp", -1L)
        if (iat < 0 || exp < 0) throw Refused(Refusal.BAD_TIME)
        if (iat > nowMs + CLOCK_SKEW_MS) throw Refused(Refusal.NOT_YET)
        if (nowMs >= exp) throw Refused(Refusal.EXPIRED)

        val jti = claims.optString("jti")
        if (jti.isEmpty()) throw Refused(Refusal.BAD_JTI)
        if (jti in spentJti) throw Refused(Refusal.SPENT)

        return Ticket(jti = jti, did = claims.getString("did"), day = claims.getString("day"), ms = AWARD_MS, iat = iat, exp = exp)
    }

    /**
     * The `alg` of a ticket, read without trusting it: lenient parse of a payload that has not been
     * verified yet, used only to pick which key to try. A payload that will not parse selects no
     * algorithm and is therefore refused.
     */
    private fun algorithmOf(body: String): String? = try {
        JSONObject(String(decode(body), Charsets.UTF_8)).optString("alg").ifEmpty { null }
    } catch (e: JSONException) {
        null
    } catch (e: Refused) {
        null
    }

    private fun claimsOf(body: String): JSONObject = try {
        JSONObject(String(decode(body), Charsets.UTF_8))
    } catch (e: JSONException) {
        throw Refused(Refusal.BAD_PAYLOAD)
    } catch (e: Refused) {
        throw Refused(Refusal.BAD_PAYLOAD)
    }

    /** base64url, padding optional: the server writes it without `=`. */
    private fun decode(text: String): ByteArray = try {
        Base64.decode(text, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)
    } catch (e: IllegalArgumentException) {
        throw Refused(Refusal.BAD_BASE64)
    }

    private fun verifyEs256(key: PublicKey, message: ByteArray, signature: ByteArray): Boolean = try {
        Signature.getInstance("SHA256withECDSA").run {
            initVerify(key)
            update(message)
            verify(signature)
        }
    } catch (e: Exception) {
        // A malformed DER signature is a refusal, not a crash: the caller only ever wants yes or no.
        false
    }

    private fun verifyHmac(secret: ByteArray, message: String, signature: ByteArray): Boolean = try {
        val mac = Mac.getInstance("HmacSHA256")
        mac.init(SecretKeySpec(secret, "HmacSHA256"))
        MessageDigest.isEqual(mac.doFinal(message.toByteArray(Charsets.US_ASCII)), signature)
    } catch (e: Exception) {
        false
    }

    /**
     * An ES256 public key from the two forms a key generator hands out: a bare point (`X||Y`, 64
     * bytes, what the build injects) or a SubjectPublicKeyInfo (a `.pem` body, for a key pasted in
     * by hand).
     *
     * The curve parameters come from the standard name `secp256r1` rather than from constants
     * spelled out here: the numbers are the one thing that must not be transcribed by hand.
     */
    fun es256PublicKey(raw: ByteArray): PublicKey {
        if (raw.size != 64) {
            return KeyFactory.getInstance("EC").generatePublic(X509EncodedKeySpec(raw))
        }
        val params = AlgorithmParameters.getInstance("EC").run {
            init(ECGenParameterSpec("secp256r1"))
            getParameterSpec(ECParameterSpec::class.java)
        }
        val point = ECPoint(BigInteger(1, raw.copyOfRange(0, 32)), BigInteger(1, raw.copyOfRange(32, 64)))
        return KeyFactory.getInstance("EC").generatePublic(ECPublicKeySpec(point, params))
    }
}

package app.orchard.tv.quiz

import android.util.Base64
import java.math.BigInteger
import java.security.KeyPair
import java.security.KeyPairGenerator
import java.security.PrivateKey
import java.security.PublicKey
import java.security.Signature
import java.security.interfaces.ECPublicKey
import java.security.spec.ECGenParameterSpec
import org.json.JSONObject
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The ticket is the only thing standing between a child and thirty minutes, so what is checked here
 * is every way it can be wrong: another device, another day, yesterday, a spent one, an edited one,
 * and a signature under the wrong key.
 *
 * The key pair is generated in the test and the tickets are signed here, so this exercises the same
 * path the server's signatures take without needing a server. That the two produce the same bytes is
 * `server/tools/interop_es256.py`'s job (openssl cross-signing).
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [23])
class QuizTicketTest {

    private val now = 1_760_000_000_000L
    private val day = "2026-11-12"
    private val device = "01JC0DEVICE000000000000000"

    private val pair: KeyPair by lazy {
        KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
    }

    private fun b64(bytes: ByteArray) =
        Base64.encodeToString(bytes, Base64.URL_SAFE or Base64.NO_PADDING or Base64.NO_WRAP)

    private fun claims(
        ms: Long = QuizTicket.AWARD_MS,
        did: String = device,
        dayKey: String = day,
        iat: Long = now,
        exp: Long = now + 24 * 3_600_000L,
        jti: String = "01JC0TICKET00000000000000",
        alg: String = "ES256",
        version: Int = QuizTicket.PAYLOAD_VERSION,
    ) = JSONObject().apply {
        put("v", version)
        put("alg", alg)
        put("jti", jti)
        put("did", did)
        put("day", dayKey)
        put("ms", ms)
        put("iat", iat)
        put("exp", exp)
    }

    /** The server's `issue`, in three lines: sign `AQ1.<payload>` with the private key. */
    private fun issue(payload: JSONObject, key: PrivateKey = pair.private): String {
        val body = b64(payload.toString().toByteArray(Charsets.UTF_8))
        val signed = "AQ1.$body"
        val signature = Signature.getInstance("SHA256withECDSA").run {
            initSign(key)
            update(signed.toByteArray(Charsets.US_ASCII))
            sign()
        }
        return "$signed.${b64(signature)}"
    }

    private fun issueHmac(payload: JSONObject, secret: ByteArray): String {
        val body = b64(payload.toString().toByteArray(Charsets.UTF_8))
        val signed = "AQ1.$body"
        val mac = Mac.getInstance("HmacSHA256").apply { init(SecretKeySpec(secret, "HmacSHA256")) }
        return "$signed.${b64(mac.doFinal(signed.toByteArray(Charsets.US_ASCII)))}"
    }

    private fun verify(
        raw: String,
        key: PublicKey? = pair.public,
        hmac: ByteArray? = null,
        dayKey: String = day,
        did: String = device,
        at: Long = now + 1_000,
        spent: Set<String> = emptySet(),
    ) = QuizTicket.verify(raw, key, hmac, did, dayKey, at, spent)

    private fun refused(reason: QuizTicket.Refusal, block: () -> Unit) {
        val error = assertThrows(QuizTicket.Refused::class.java) { block() }
        assertEquals(reason, error.reason)
    }

    @Test
    fun aGoodTicketIsAccepted() {
        val ticket = verify(issue(claims()))
        assertEquals(QuizTicket.AWARD_MS, ticket.ms)
        assertEquals(device, ticket.did)
        assertEquals(day, ticket.day)
    }

    @Test
    fun theAwardIsTheOnlyNumberTheServerMaySign() {
        // 35 minutes: a server that tried to hand out more is not one this build trusts.
        refused(QuizTicket.Refusal.BAD_AWARD) { verify(issue(claims(ms = 35 * 60_000L))) }
        refused(QuizTicket.Refusal.BAD_AWARD) { verify(issue(claims(ms = 0L))) }
    }

    @Test
    fun aTicketForAnotherTelevisionIsRefused() {
        refused(QuizTicket.Refusal.WRONG_DEVICE) { verify(issue(claims(did = "01JC0OTHER0000000000000000"))) }
    }

    @Test
    fun aTicketFromYesterdayIsVoidEvenWithHoursLeft() {
        refused(QuizTicket.Refusal.WRONG_DAY) {
            verify(issue(claims(dayKey = "2026-11-11", exp = now + 20 * 3_600_000L)))
        }
    }

    @Test
    fun anExpiredTicketIsRefused() {
        refused(QuizTicket.Refusal.EXPIRED) { verify(issue(claims()), at = now + 24 * 3_600_000L) }
    }

    @Test
    fun aTicketFromTheFutureIsRefused() {
        refused(QuizTicket.Refusal.NOT_YET) { verify(issue(claims(iat = now + 10 * 60_000L))) }
    }

    @Test
    fun aTicketAlreadyTurnedIntoMinutesIsRefused() {
        val raw = issue(claims(jti = "01JC0SPENT0000000000000000"))
        refused(QuizTicket.Refusal.SPENT) { verify(raw, spent = setOf("01JC0SPENT0000000000000000")) }
    }

    @Test
    fun anEditedPayloadBreaksTheSignature() {
        val raw = issue(claims())
        val parts = raw.split('.')
        val edited = JSONObject(String(Base64.decode(parts[1], Base64.URL_SAFE or Base64.NO_WRAP)))
            .put("ms", 90 * 60_000L)
        val body = b64(edited.toString().toByteArray())
        // Same signature, different payload: the one thing the format exists to catch.
        refused(QuizTicket.Refusal.BAD_SIGNATURE) { verify("AQ1.$body.${parts[2]}") }
    }

    @Test
    fun anotherKeyCannotSignForThisOne() {
        val other = KeyPairGenerator.getInstance("EC").apply { initialize(ECGenParameterSpec("secp256r1")) }.generateKeyPair()
        refused(QuizTicket.Refusal.BAD_SIGNATURE) { verify(issue(claims(), other.private)) }
    }

    @Test
    fun anHmacTicketIsNotAcceptedByTheEs256Verifier() {
        val raw = issueHmac(claims(alg = "HS256"), DEV_SECRET)
        // Even with the HMAC key configured, a build that carries an ES256 key must not accept it.
        refused(QuizTicket.Refusal.BAD_SIGNATURE) { verify(raw, key = pair.public, hmac = DEV_SECRET) }
        // And a development build, which has only the HMAC key, still checks the HMAC properly.
        assertEquals(QuizTicket.AWARD_MS, verify(raw, key = null, hmac = DEV_SECRET).ms)
        refused(QuizTicket.Refusal.BAD_SIGNATURE) { verify(raw, key = null, hmac = "not the key".toByteArray()) }
    }

    @Test
    fun theWrappingIsCheckedBeforeAnythingElse() {
        refused(QuizTicket.Refusal.EMPTY) { verify("") }
        refused(QuizTicket.Refusal.BAD_SHAPE) { verify("AQ2.abc.def") }
        refused(QuizTicket.Refusal.BAD_SHAPE) { verify("AQ1.only.two.dots.here") }
        refused(QuizTicket.Refusal.BAD_SIGNATURE) { verify("AQ1.!!!.!!!") }
    }

    @Test
    fun aPayloadOfTheWrongVersionOrAlgorithmIsRefused() {
        // Signed correctly, but a version this build does not know: refuse rather than guess.
        val future = issue(claims(version = QuizTicket.PAYLOAD_VERSION + 1))
        refused(QuizTicket.Refusal.BAD_VERSION) { verify(future) }
        // The claim says ES256 while the signature is an HMAC over the same bytes — a mismatch the
        // signature check alone would let through if the alg claim were trusted first.
        val lying = issueHmac(claims(alg = "ES256"), DEV_SECRET)
        refused(QuizTicket.Refusal.BAD_SIGNATURE) { verify(lying, key = null, hmac = DEV_SECRET) }
    }

    @Test
    fun theKeyFromTheBuildIsTheSameAsTheGeneratedOne() {
        // QuizConfig feeds the verifier 64 bytes of X||Y; that path must land on the same key.
        val point = (pair.public as ECPublicKey).w
        val x = point.affineX.toFixed(32)
        val y = point.affineY.toFixed(32)
        val raw = x + y
        assertEquals(64, raw.size)
        val rebuilt = QuizTicket.es256PublicKey(raw)
        assertEquals(QuizTicket.AWARD_MS, QuizTicket.verify(issue(claims()), rebuilt, null, device, day, now + 1_000).ms)
    }

    @Test
    fun aSignatureFromThisKeyVerifiesUnderJavaItself() {
        // The other direction of the same fact: what we accept is what JCA produces, so the server's
        // signatures (proven against openssl) are readable here.
        val raw = issue(claims())
        val parts = raw.split('.')
        val ok = Signature.getInstance("SHA256withECDSA").run {
            initVerify(pair.public)
            update("${parts[0]}.${parts[1]}".toByteArray(Charsets.US_ASCII))
            verify(Base64.decode(parts[2], Base64.URL_SAFE or Base64.NO_WRAP))
        }
        assertTrue(ok)
    }

    private fun BigInteger.toFixed(size: Int): ByteArray {
        val bytes = toByteArray()
        // BigInteger drops leading zero bytes and may add one sign byte; a point coordinate is fixed.
        val trimmed = if (bytes.size > size) bytes.copyOfRange(bytes.size - size, bytes.size) else bytes
        val out = ByteArray(size)
        System.arraycopy(trimmed, 0, out, size - trimmed.size, trimmed.size)
        return out
    }

    private companion object {
        val DEV_SECRET = "a-development-secret-of-32-bytes".toByteArray()
    }
}

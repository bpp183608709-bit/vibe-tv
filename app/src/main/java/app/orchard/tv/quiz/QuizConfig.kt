package app.orchard.tv.quiz

import android.content.Context
import app.orchard.tv.BuildConfig
import app.orchard.tv.system.DiagLog
import java.security.PublicKey

/**
 * Where the quiz server is and which key is allowed to sign for it.
 *
 * All three values come from the build, not from a file on the television: the server's address and
 * its ticket key are decided by whoever deploys the server, and a value a child can edit with a file
 * manager is not a pin. They are read from `local/quiz.properties` when the APK is built (see
 * `AGENTS.md`); a checkout without that file builds a television where the quiz page says the server
 * is not configured, and nothing else changes.
 *
 * The two weapons here are worth stating plainly:
 *
 *  - [publicKey] is the ES256 key the server signs tickets with. Only the public half is in the APK.
 *  - [caPem] is the certificate of the CA that signed the server's TLS certificate. It is the only
 *    certificate trusted for the quiz connection (`QuizHttp`).
 */
object QuizConfig {

    /** e.g. `https://203.0.113.9` — no trailing slash needed. Empty when the build has no server. */
    val baseUrl: String get() = BuildConfig.QUIZ_BASE_URL

    val configured: Boolean get() = baseUrl.isNotEmpty() && (publicKey != null || devHmac != null)

    /**
     * The ticket key, decoded once. A build that carries a malformed key reports it as "no server
     * configured" rather than failing at the moment a child passes a group.
     */
    val publicKey: PublicKey? by lazy {
        val raw = BuildConfig.QUIZ_PUBKEY
        if (raw.isEmpty()) return@lazy null
        runCatching { QuizTicket.es256PublicKey(android.util.Base64.decode(raw, android.util.Base64.DEFAULT)) }
            .onFailure { DiagLog.i("quiz: ticket public key does not parse: ${it.message}") }
            .getOrNull()
    }

    /**
     * HMAC key for a development build only. Empty in any build that was given an ES256 key: with
     * both present, a stolen APK's HMAC key would let anyone mint minutes, which would undo the point
     * of using a public key at all.
     */
    val devHmac: ByteArray? by lazy {
        val raw = BuildConfig.QUIZ_DEV_HMAC
        if (raw.isEmpty() || BuildConfig.QUIZ_PUBKEY.isNotEmpty()) null else raw.toByteArray(Charsets.UTF_8)
    }

    /** The pinned CA, read from `res/raw` by name so a build without one still compiles. */
    fun caPem(context: Context): ByteArray? {
        val id = context.resources.getIdentifier("quiz_ca", "raw", context.packageName)
        if (id == 0) return null
        return runCatching { context.resources.openRawResource(id).use { it.readBytes() } }.getOrNull()
    }
}

package app.orchard.tv.quiz

import org.json.JSONException
import org.json.JSONObject
import java.io.ByteArrayInputStream
import java.io.InputStream
import java.net.HttpURLConnection
import java.net.SocketTimeoutException
import java.net.URL
import java.security.KeyStore
import java.security.cert.Certificate
import java.security.cert.CertificateFactory
import javax.net.ssl.HostnameVerifier
import javax.net.ssl.HttpsURLConnection
import javax.net.ssl.SSLContext
import javax.net.ssl.SSLSocketFactory
import javax.net.ssl.TrustManagerFactory

/**
 * The one way the television talks to the quiz server (docs/QUIZ.md §7, QUIZ.md §8).
 *
 * Three decisions worth knowing about:
 *
 *  - **HTTPS only, and the certificate is pinned.** The server is a box with an address and no
 *    domain, so its certificate is signed by a small CA of our own; the CA's certificate ships in the
 *    APK (`res/raw/quiz_ca.pem`) and is the only thing trusted on this connection. Pinning beats
 *    adding the CA to the system store: a private CA in the system store would be trusted by every
 *    app on the television, and by nothing here.
 *  - **`HttpURLConnection`, not a library.** One POST, one GET, no streaming, no interceptors. A
 *    dependency would be a supply chain and 2 MB of APK for forty lines (the hard constraint is no
 *    third-party SDK).
 *  - **No retries here.** A retry hides "the network is down" behind a delay the child reads as a
 *    frozen screen. The screens decide what to offer: 重试, or back to the lock.
 */
internal object QuizHttp {

    private const val CONNECT_TIMEOUT_MS = 6_000
    private const val READ_TIMEOUT_MS = 10_000

    /** What went wrong, in the terms the screens talk in. */
    enum class Kind {
        /** Nothing answered: no Wi-Fi, no route, a wrong address. */
        OFFLINE,

        /** The certificate was not the one pinned, or TLS itself failed. */
        UNTRUSTED,

        /** An answer came back, with a status the server chose. [code] carries its error string. */
        REFUSED,

        /** An answer came back that was not the JSON this client knows. */
        GARBLED,
    }

    class Failure(val kind: Kind, val status: Int = 0, val code: String = "", val detail: String = "") :
        Exception("${kind.name} $status $code $detail")

    /**
     * POSTs [body] to [path] and returns the parsed object.
     *
     * [caPem] is the pinned CA. Null means "no CA configured", which is a refusal rather than a
     * fallback to the system store — a client that quietly trusts everything when its pin is missing
     * is worse than one that does not run.
     */
    fun post(baseUrl: String, path: String, body: JSONObject, caPem: ByteArray?): JSONObject =
        exchange("POST", baseUrl, path, body, caPem)

    /** GETs [path]. Same trust rules as [post]; used by the two polls that carry no payload. */
    fun get(baseUrl: String, path: String, caPem: ByteArray?): JSONObject =
        exchange("GET", baseUrl, path, null, caPem)

    private fun exchange(
        method: String,
        baseUrl: String,
        path: String,
        body: JSONObject?,
        caPem: ByteArray?,
    ): JSONObject {
        if (baseUrl.isEmpty()) throw Failure(Kind.OFFLINE, detail = "no server configured")
        if (!baseUrl.startsWith("https://")) throw Failure(Kind.UNTRUSTED, detail = "base url is not https")
        if (caPem == null) throw Failure(Kind.UNTRUSTED, detail = "no pinned CA")

        val url = URL(baseUrl.trimEnd('/') + path)
        val connection = try {
            url.openConnection() as HttpURLConnection
        } catch (e: Exception) {
            throw Failure(Kind.OFFLINE, detail = e.message ?: "")
        }

        try {
            if (connection is HttpsURLConnection) {
                connection.sslSocketFactory = factoryFor(caPem)
                // The handshake has already proved the chain ends at our own CA, which signs nothing
                // else. Checking the host name on top of that would only reject the certificate the
                // day the address changes, for no security gained: nobody else can obtain a
                // certificate from this CA.
                connection.hostnameVerifier = HostnameVerifier { _, _ -> true }
            }
            connection.requestMethod = method
            connection.connectTimeout = CONNECT_TIMEOUT_MS
            connection.readTimeout = READ_TIMEOUT_MS
            connection.setRequestProperty("Accept", "application/json")
            connection.setRequestProperty("User-Agent", "Aerial-Quiz")

            if (body != null) {
                connection.doOutput = true
                connection.setRequestProperty("Content-Type", "application/json; charset=utf-8")
                val payload = body.toString().toByteArray(Charsets.UTF_8)
                connection.setFixedLengthStreamingMode(payload.size)
                connection.outputStream.use { it.write(payload) }
            }

            val status = connection.responseCode
            val text = readAll(if (status in 200..299) connection.inputStream else connection.errorStream)
            val json = try {
                JSONObject(text)
            } catch (e: JSONException) {
                throw Failure(Kind.GARBLED, status, detail = text.take(120))
            }
            if (status !in 200..299) {
                throw Failure(Kind.REFUSED, status, code = json.optString("error"), detail = json.optString("detail"))
            }
            return json
        } catch (e: Failure) {
            throw e
        } catch (e: SocketTimeoutException) {
            throw Failure(Kind.OFFLINE, detail = "timeout")
        } catch (e: javax.net.ssl.SSLException) {
            throw Failure(Kind.UNTRUSTED, detail = e.message ?: "")
        } catch (e: Exception) {
            throw Failure(Kind.OFFLINE, detail = e.message ?: "")
        } finally {
            connection.disconnect()
        }
    }

    private fun readAll(stream: InputStream?): String {
        if (stream == null) return ""
        return stream.use { it.readBytes().toString(Charsets.UTF_8) }
    }

    // The SSL context is expensive (a key store, a trust manager, a context) and the socket factory
    // is thread-safe, so it is built once per CA and kept.
    @Volatile
    private var cached: Pair<Int, SSLSocketFactory>? = null

    private fun factoryFor(caPem: ByteArray): SSLSocketFactory {
        val key = caPem.contentHashCode()
        cached?.let { if (it.first == key) return it.second }
        val certificate: Certificate = CertificateFactory.getInstance("X.509")
            .generateCertificate(ByteArrayInputStream(caPem))
        val store = KeyStore.getInstance(KeyStore.getDefaultType()).apply {
            load(null, null)
            setCertificateEntry("quiz-ca", certificate)
        }
        val managers = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm())
            .apply { init(store) }
            .trustManagers
        val factory = SSLContext.getInstance("TLS").apply { init(null, managers, null) }.socketFactory
        cached = key to factory
        return factory
    }
}

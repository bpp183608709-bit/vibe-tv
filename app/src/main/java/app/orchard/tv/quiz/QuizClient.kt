package app.orchard.tv.quiz

import android.content.Context
import app.orchard.tv.BuildConfig
import app.orchard.tv.parental.ParentalStore
import app.orchard.tv.system.DiagLog
import org.json.JSONArray
import org.json.JSONObject

/**
 * Everything the television asks the server, in the terms the screens need it.
 *
 * Three of them are the quiz itself (register, take a group, hand it in). The other three are the
 * conversation with the parent page: has my code been typed yet, have the limits changed, and here is
 * what today looks like ([QuizSync] drives the last two on a timer).
 *
 * Every call is blocking and belongs on a worker thread. The errors are translated here, once, so
 * that a screen never has to know an HTTP status: "the ten minutes are not up yet" and "the address
 * is wrong" are different screens, and the difference is made at this layer.
 */
object QuizClient {

    /** Why a call did not produce an answer the screen can draw. */
    sealed class Error {
        /** No network, or nothing listening. The child can retry. */
        data object Offline : Error()

        /** The certificate was not the pinned one. Retrying will not help; a parent has to look. */
        data object Untrusted : Error()

        /** Failed too recently; [seconds] is what the lock screen counts down. */
        data class Cooldown(val seconds: Int) : Error()

        /** Six groups today, and they are gone. */
        data object AttemptsExhausted : Error()

        /** Three passes or ninety minutes, already earned. */
        data object QuotaExhausted : Error()

        /** The question bank cannot fill a group for this curriculum position. */
        data object NoQuestions : Error()

        /** The server does not know this television; the next call registers again. */
        data object Unpaired : Error()

        /** A parent unbound this television. */
        data object Revoked : Error()

        /**
         * This build has no server address, no pinned CA, or no ticket key: the feature is switched
         * off rather than pointed at something unverified (see [QuizConfig]).
         */
        data object NotConfigured : Error()

        /** Anything else the server said, kept for the log. */
        data class Other(val code: String, val status: Int) : Error()
    }

    class Failed(val error: Error) : Exception(error.toString())

    /** Registers this television and returns its device id. The pair code is for the parent page (P3). */
    fun pair(context: Context): String {
        val body = JSONObject().apply {
            put("name", "Aerial TV")
            put("app_version", BuildConfig.VERSION_NAME)
        }
        // No secret on this one: it is the call that hands one out.
        val json = call(QuizConfig.baseUrl, "/v1/pair/start", body, QuizConfig.caPem(context))
        val device = json.getString("device_id")
        val code = json.optString("pair_code")
        val ttlSeconds = json.optLong("pair_ttl_seconds", 600L)
        QuizVault.saveDevice(context, device, code, System.currentTimeMillis() + ttlSeconds * 1_000L)
        QuizVault.saveSecret(context, json.optString("device_secret").ifEmpty { null })
        DiagLog.i("quiz: paired as $device, code $code")
        return device
    }

    /** A group for [dayKey], registering the television first if it has no id yet. */
    fun fetchGroup(context: Context, dayKey: String): QuizGroup {
        val device = QuizVault.deviceId(context) ?: pair(context)
        return try {
            nextGroup(context, device, dayKey)
        } catch (e: Failed) {
            // The server was rebuilt, or a different one is answering: one re-registration, then the
            // failure is real. Without this the television would be stuck for ever on a stale id.
            if (e.error !is Error.Unpaired) throw e
            DiagLog.i("quiz: device unknown to the server, registering again")
            QuizVault.forgetDevice(context)
            nextGroup(context, pair(context), dayKey)
        }
    }

    private fun nextGroup(context: Context, device: String, dayKey: String): QuizGroup =
        QuizWire.group(
            call(
                QuizConfig.baseUrl, "/v1/quiz/next", request(device, dayKey),
                QuizConfig.caPem(context), secret(context),
            ),
        )

    /** One answered group. The verdict's ticket is not yet minutes — [QuizVault.redeem] is. */
    fun submit(
        context: Context,
        sessionId: String,
        deviceId: String,
        answers: List<Answer>,
        dayKey: String,
    ): QuizVerdict {
        val array = JSONArray()
        for (answer in answers) {
            array.put(
                JSONObject().apply {
                    put("question_id", answer.questionId)
                    put("chosen", answer.chosen)
                    put("flagged_unknown", answer.flaggedUnknown)
                },
            )
        }
        val body = JSONObject().apply {
            put("device_id", deviceId)
            put("day_key", dayKey)
            put("answers", array)
        }
        return QuizWire.verdict(
            call(
                QuizConfig.baseUrl, "/v1/quiz/$sessionId/grade", body,
                QuizConfig.caPem(context), secret(context),
            ),
        )
    }

    /** One answer as it is sent: the label of a choice, the typed text of a fill-in. */
    data class Answer(val questionId: Int, val chosen: String, val flaggedUnknown: Boolean = false)

    // ---- the two polls the settings page and the parent page share (docs/PARENTAL.md, 设置下发) ----

    /** Whether a parent has typed this television's code on their phone yet. */
    data class PairState(val bound: Boolean, val name: String?)

    /**
     * The three numbers as the server holds them. [version] is what tells the two ends apart: the
     * television sends back the version it has applied, so whoever did not bump it is the one who is
     * behind. `null` from [pullConfig] means "nothing changed", which is the usual answer.
     */
    data class RemoteConfig(
        val version: Int,
        val dailyMinutes: Int,
        val sessionMinutes: Int,
        val intervalMinutes: Int,
    )

    /** What `/v1/heartbeat` answered: the counter the server kept, whether a phone is still bound,
     *  and the cooldown it is still enforcing (seconds; 0 when a group may start now). */
    data class Beat(val bound: Boolean, val configVersion: Int, val cooldownSeconds: Int = 0)

    fun pairStatus(context: Context): PairState {
        val device = QuizVault.deviceId(context) ?: throw Failed(Error.Unpaired)
        val json = get(
            QuizConfig.baseUrl, "/v1/pair/status?device_id=$device",
            QuizConfig.caPem(context), secret(context),
        )
        return PairState(bound = json.optBoolean("bound", false), name = json.optString("name").ifEmpty { null })
    }

    /**
     * A new code for the television that is already registered. Distinct from [pair] because that one
     * registers a *new* device and would throw away today's counters and the code's history.
     */
    fun renewPair(context: Context): String {
        val device = QuizVault.deviceId(context) ?: return pair(context)
        val body = JSONObject().apply { put("device_id", device) }
        val json = call(
            QuizConfig.baseUrl, "/v1/pair/renew", body, QuizConfig.caPem(context), secret(context),
        )
        val code = json.optString("pair_code")
        val ttlSeconds = json.optLong("pair_ttl_seconds", 600L)
        QuizVault.saveDevice(context, device, code, System.currentTimeMillis() + ttlSeconds * 1_000L)
        DiagLog.i("quiz: pairing code renewed for $device")
        return code
    }

    fun pullConfig(context: Context, knownVersion: Int): RemoteConfig? {
        val device = QuizVault.deviceId(context) ?: throw Failed(Error.Unpaired)
        val json = get(
            QuizConfig.baseUrl,
            "/v1/config?device_id=$device&version=$knownVersion",
            QuizConfig.caPem(context),
            secret(context),
        )
        if (!json.optBoolean("changed", false)) return null
        val config = json.optJSONObject("config") ?: return null
        return RemoteConfig(
            version = json.optInt("version", 0),
            dailyMinutes = config.optInt("daily_minutes", -1),
            sessionMinutes = config.optInt("session_minutes", -1),
            intervalMinutes = config.optInt("interval_minutes", -1),
        )
    }

    /**
     * "Still here, and this is today." [usedMs] is the television's own total for the day, sent as a
     * total rather than an increment so a lost or repeated beat cannot change the number.
     *
     * [configVersion] is the settings version this television has applied. The server compares it with
     * its own to decide who changed what last: equal means the television is the one that just moved a
     * number (its settings page was used) and the server should take it; older means the phone is
     * ahead and the television is about to be told so.
     */
    fun heartbeat(
        context: Context,
        dayKey: String,
        usedMs: Long,
        limits: ParentalStore.Limits,
        configVersion: Int,
    ): Beat {
        val device = QuizVault.deviceId(context) ?: throw Failed(Error.Unpaired)
        val body = JSONObject().apply {
            put("device_id", device)
            put("day_key", dayKey)
            put("used_ms", usedMs)
            put("app_version", BuildConfig.VERSION_NAME)
            put("config_version", configVersion)
            put(
                "config",
                JSONObject().apply {
                    put("daily_minutes", limits.dailyMinutes)
                    put("session_minutes", limits.sessionMinutes)
                    put("interval_minutes", limits.intervalMinutes)
                },
            )
        }
        val json = call(QuizConfig.baseUrl, "/v1/heartbeat", body, QuizConfig.caPem(context), secret(context))
        return Beat(
            bound = json.optBoolean("bound", true),
            configVersion = json.optInt("config_version", configVersion),
            cooldownSeconds = json.optInt("cooldown_seconds"),
        )
    }

    private fun get(baseUrl: String, path: String, ca: ByteArray?, secret: String?): JSONObject = try {
        QuizHttp.get(baseUrl, path, ca, secret)
    } catch (e: QuizHttp.Failure) {
        throw Failed(translate(e))
    }

    private fun secret(context: Context): String? = QuizVault.secret(context)

    private fun request(deviceId: String, dayKey: String) = JSONObject().apply {
        put("device_id", deviceId)
        put("day_key", dayKey)
    }

    private fun call(
        baseUrl: String,
        path: String,
        body: JSONObject,
        ca: ByteArray?,
        secret: String? = null,
    ): JSONObject = try {
        QuizHttp.post(baseUrl, path, body, ca, secret)
    } catch (e: QuizHttp.Failure) {
        throw Failed(translate(e))
    }

    private fun translate(failure: QuizHttp.Failure): Error = when (failure.kind) {
        QuizHttp.Kind.OFFLINE -> Error.Offline
        QuizHttp.Kind.UNTRUSTED, QuizHttp.Kind.GARBLED -> Error.Untrusted
        QuizHttp.Kind.REFUSED -> when (failure.code) {
            "cooldown" -> Error.Cooldown(secondsIn(failure.detail))
            "attempts-exhausted" -> Error.AttemptsExhausted
            "quota-exhausted" -> Error.QuotaExhausted
            "no-questions" -> Error.NoQuestions
            "unknown-device" -> Error.Unpaired
            // `bad-device` is "this id and this secret are not a pair the server knows". For the
            // television that means the same thing `unknown-device` used to: register again. It cannot
            // mean anything else for a stranger, who has no valid secret to send.
            "bad-device" -> Error.Unpaired
            "device-revoked" -> Error.Revoked
            else -> Error.Other(failure.code, failure.status)
        }
    }

    /** The server writes "612s left"; the lock screen wants the number alone. */
    private fun secondsIn(detail: String): Int =
        detail.takeWhile { it.isDigit() }.toIntOrNull() ?: 0
}

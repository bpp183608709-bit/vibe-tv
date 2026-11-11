package app.orchard.tv.parental

import android.content.Context
import android.os.SystemClock
import org.json.JSONObject

/**
 * The parental module over ADB: what `tools/selftest.sh` uses to see the counters and to reach a
 * lock screen in seconds, and what an owner uses to put a broken day back to zero.
 *
 * The settings page normally asks for the password before it shows a limit, and that is exactly what
 * a test does not have. The manifest requires `WRITE_SECURE_SETTINGS` of anything sending this
 * broadcast (see [app.orchard.tv.guard.ConfigReceiver]), which only the ADB shell holds, so nothing
 * on the TV can use these to lift a limit for itself.
 *
 * [watch] is the one that is not the module's own path: a real limit takes a quarter of an hour of
 * actual video, so the clock is fed a minute at a time instead. Everything below it — the timer, the
 * publish, the write to the store, the lock screen that follows — is the real thing.
 */
object ParentalProbe {

    /** The counters, the lock, and the settings, as one JSON object. */
    fun state(
        context: Context,
        nowElapsed: Long = SystemClock.elapsedRealtime(),
        nowWall: Long = System.currentTimeMillis(),
    ): String {
        Parental.init(context, nowElapsed, nowWall)
        val ui = Parental.ui
        return JSONObject()
            .put("state", ui.state.name)
            .put("locked", ui.locked)
            .put("hasPin", ui.hasPin)
            // Unrounded: the published reading moves once a minute, which reads as "not counting"
            // for the whole first minute of any measurement.
            .put("dailyUsed", Parental.usedNowMs())
            .put("dailyBudget", ui.dailyBudgetMs)
            // What the password added today, which is the budget minus the configured one.
            .put("dailyExtra", if (ui.dailyBudgetMs > 0L) ui.dailyBudgetMs - ui.limits.dailyMs else 0L)
            .put("sessionUsed", ui.sessionUsedMs)
            .put("sessionBudget", ui.sessionBudgetMs)
            .put("intervalMs", ui.intervalMs)
            .put("intervalLeft", Parental.intervalLeftSec(nowElapsed))
            .put("unrestricted", ui.unrestrictedToday)
            .put("blocked", Parental.blockedSec(nowWall))
            .put("limits", JSONObject()
                .put("daily", ui.limits.dailyMinutes)
                .put("session", ui.limits.sessionMinutes)
                .put("interval", ui.limits.intervalMinutes))
            // 严格模式: whether it is on, and the ticket the clock is following under it. A test
            // that cannot see the ticket cannot tell "the ticket ran out" from "the clock stopped".
            .put("strict", ParentalStore.strictWatch(context))
            .put("ticket", JSONObject()
                .put("holder", WatchTicket.holder() ?: "")
                .put("left", WatchTicket.leftSec(nowElapsed)))
            // How the counters are being told, and that the clock is running at all: without these
            // a test cannot tell "nothing was watched" from "nothing was counted".
            .put("level", ParentalClock.level?.name ?: "-")
            .put("ticks", ParentalClock.ticks)
            .toString()
    }

    /**
     * The three settings, as `{"daily":2,"session":0,"interval":10}`; a key left out keeps its value.
     * Applies to today's counters at once, exactly as the settings page does.
     */
    fun limits(context: Context, json: String?): String {
        Parental.init(context)
        val current = Parental.ui.limits
        val asked = runCatching { JSONObject(json ?: "") }.getOrNull()
            ?: return JSONObject().put("error", "not valid JSON").toString()
        val next = ParentalStore.Limits(
            dailyMinutes = asked.optInt("daily", current.dailyMinutes),
            sessionMinutes = asked.optInt("session", current.sessionMinutes),
            intervalMinutes = asked.optInt("interval", current.intervalMinutes),
        )
        Parental.applyLimits(next)
        return state(context)
    }

    /**
     * Counts [minutes] as if a video had been playing, a minute at a time: [WatchTimer] treats a
     * longer gap as the process having been away, which is right for a real clock and wrong here.
     *
     * The clock is handed back afterwards. The timer remembers the last tick it was given, and until
     * real time catches up every pass would count nothing (a negative gap is clamped to zero), so the
     * TV would quietly stop counting for as long as the test had run ahead.
     */
    fun watch(
        context: Context,
        minutes: Int,
        nowElapsed: Long = SystemClock.elapsedRealtime(),
        nowWall: Long = System.currentTimeMillis(),
    ): String {
        Parental.init(context, nowElapsed, nowWall)
        var at = nowElapsed
        repeat(minutes.coerceIn(0, MAX_MINUTES)) {
            at += STEP_MS
            Parental.tick(watching = true, nowElapsed = at, nowWall = nowWall)
        }
        Parental.tick(watching = false, nowElapsed = nowElapsed, nowWall = nowWall)
        return state(context, nowElapsed, nowWall)
    }

    /** What a lock screen button does once the password is in: `half`, `hour`, `two`, `lift`, `skip`. */
    fun act(context: Context, action: String): String {
        Parental.init(context)
        when (action) {
            "half" -> Parental.extendDaily(30L * MINUTE_MS)
            "hour" -> Parental.extendDaily(60L * MINUTE_MS)
            "two" -> Parental.extendDaily(120L * MINUTE_MS)
            "lift" -> Parental.liftDailyLimit()
            "skip" -> Parental.skipInterval()
            else -> return JSONObject().put("error", "no such action: $action").toString()
        }
        return state(context)
    }

    /**
     * The password, for a TV that has none: the settings page asks for it on the remote, and typing
     * eight digits with `input keyevent` before the checks start is a second thing that can go wrong.
     * Nothing here ever replaces one that is already set — the store keeps a hash, so a password put
     * back afterwards could not be the owner's own.
     */
    fun setPin(context: Context, digits: String?): String {
        Parental.init(context)
        val pin = digits?.trim().orEmpty()
        if (pin.length !in MIN_PIN..MAX_PIN || !pin.all { it in '0'..'9' }) {
            return JSONObject().put("error", "the password is $MIN_PIN to $MAX_PIN digits").toString()
        }
        if (Parental.hasPin) {
            return JSONObject().put("error", "this TV already has a password; it is not replaced").toString()
        }
        Parental.setPin(pin)
        return state(context)
    }

    /**
     * The ticket ended, as running out or leaving the app would end it. A 30-minute ticket is not
     * something a test can wait for, and what matters is what the guard does once it is gone.
     */
    fun clearTicket(context: Context): String {
        Parental.init(context)
        WatchTicket.clear()
        return state(context)
    }

    /** The password removed again, for the test's own clean-up on a TV that had none. */
    fun clearPin(context: Context): String {
        Parental.init(context)
        Parental.clearPin()
        return state(context)
    }

    /**
     * A ticket for [pkg], exactly as the password would hand one out under 严格模式. A test cannot
     * type eight digits on the remote before every measurement, and the ticket is the thing being
     * measured — the password path itself is covered by `parental_pin`.
     */
    fun ticket(context: Context, pkg: String): String {
        Parental.init(context)
        WatchTicket.grant(
            pkg.trim(),
            SystemClock.elapsedRealtime(),
            ParentalStore.ticketMinutes(context) * 60_000L,
        )
        return state(context)
    }

    /** Today back to zero and the lock gone; the limits and the password stay. */
    fun reset(context: Context): String {
        Parental.init(context)
        Parental.resetToday()
        Parental.forgetAttempts()
        return state(context)
    }

    /**
     * Counters put back as [state] reported them, for a test that has had to move them. Keys are the
     * ones [state] prints: a saved state can be handed back here unchanged.
     */
    fun restore(context: Context, json: String?): String {
        Parental.init(context)
        val j = runCatching { JSONObject(json ?: "") }.getOrNull()
            ?: return JSONObject().put("error", "not valid JSON").toString()
        Parental.restoreDay(
            dailyUsedMs = j.optLong("dailyUsed", 0L),
            dailyExtraMs = j.optLong("dailyExtra", 0L),
            unrestricted = j.optBoolean("unrestricted", false),
            sessionUsedMs = j.optLong("sessionUsed", 0L),
            state = WatchTimer.State.entries.firstOrNull { it.name == j.optString("state") } ?: WatchTimer.State.Normal,
            intervalLeftSec = j.optInt("intervalLeft", 0),
        )
        return state(context)
    }

    private const val STEP_MS = 60_000L
    private const val MINUTE_MS = 60_000L

    /** The settings page's own range for a password, 8 to 12 digits. */
    private const val MIN_PIN = 8
    private const val MAX_PIN = 12

    /** A whole day's top: past this the driver is doing something the module never would. */
    private const val MAX_MINUTES = ParentalStore.MAX_DAILY_MINUTES
}

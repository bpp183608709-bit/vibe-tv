package app.orchard.tv.parental

import android.content.Context
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import app.orchard.tv.parental.ParentalStore.Limits

/**
 * The parental module at runtime: one [WatchTimer] over a [ParentalStore], with the readings the
 * interface draws.
 *
 * The caller ticks it — the window's second passes, the screensaver and standby included — and
 * reads [ui]. Everything that decides *when* to tick and whether something is playing lives
 * outside: this object only knows what it was told, so it can be driven from a test, from Home, or
 * from a lock screen that has to know how long is left.
 *
 * The two clocks are kept apart on purpose: the counters run on elapsedRealtime, which cannot be
 * moved by the user, and the saved countdown deadline is wall-clock, because elapsedRealtime starts
 * over at boot.
 */
object Parental {

    /** What the interface draws. Published at minute resolution: a per-second Home is a waste. */
    data class Ui(
        val state: WatchTimer.State = WatchTimer.State.Normal,
        val dailyUsedMs: Long = 0L,
        val dailyBudgetMs: Long = 0L,
        val sessionUsedMs: Long = 0L,
        val sessionBudgetMs: Long = 0L,
        val intervalMs: Long = ParentalStore.INTERVAL_MIN_MINUTES * 60_000L,
        /** When the countdown ends, on elapsedRealtime; 0 outside [WatchTimer.State.Interval]. */
        val intervalEndsAt: Long = 0L,
        val unrestrictedToday: Boolean = false,
        val hasPin: Boolean = false,
        val limits: Limits = Limits(),
    ) {
        /** The lock screens show when this is anything but [WatchTimer.State.Normal]. */
        val locked: Boolean get() = state != WatchTimer.State.Normal
    }

    var ui by mutableStateOf(Ui())
        private set

    private var store: ParentalStore? = null
    private var attempts: Attempts? = null
    private val timer = WatchTimer()
    private var limits = Limits()
    private var day = ""
    private var lastSavedAt = 0L
    private var lastSavedWall = 0L

    /** Reads the settings and today's counters. Safe to call more than once. */
    fun init(context: Context, nowElapsed: Long = SystemClock.elapsedRealtime(), nowWall: Long = System.currentTimeMillis()) {
        if (store != null) return
        val s = ParentalStore(context)
        store = s
        attempts = Attempts(context)
        limits = s.limits()
        day = ParentalStore.dayKey(nowWall)
        timer.configure(limits.dailyMs, limits.sessionMs, limits.intervalMs, nowElapsed)
        s.progress()?.let { saved ->
            // Yesterday's counters are dropped; a countdown that crossed midnight is not, it was
            // already waited out (spec, P1-2).
            val fresh = saved.day != day
            timer.restore(
                WatchTimer.Snapshot(
                    dailyUsedMs = if (fresh) 0L else saved.dailyUsedMs,
                    dailyExtraMs = if (fresh) 0L else saved.dailyExtraMs,
                    dailyUnrestricted = !fresh && saved.unrestricted,
                    sessionUsedMs = if (fresh) 0L else saved.sessionUsedMs,
                    state = saved.state,
                    // A deadline in wall-clock, back on the clock the timer ticks with. Without one,
                    // a countdown that was running has no time left to show: it ends here.
                    intervalEndsAt = if (saved.state == WatchTimer.State.Interval && saved.intervalEndsWallMs > 0L)
                        deadlineOnElapsed(saved.intervalEndsWallMs, nowElapsed, nowWall) else 0L,
                ),
                nowElapsed,
            )
        }
        publish(force = true)
    }

    /**
     * One pass of the caller's clock. [watching] is whether something is playing, already resolved
     * by the caller (spec, 三级信号). Returns the state, so the caller can open the lock screen the
     * moment it changes.
     */
    fun tick(
        watching: Boolean,
        nowElapsed: Long = SystemClock.elapsedRealtime(),
        nowWall: Long = System.currentTimeMillis(),
    ): WatchTimer.State {
        if (store == null) return WatchTimer.State.Normal
        val today = ParentalStore.dayKey(nowWall)
        if (today != day) {
            day = today
            timer.rollOver(nowElapsed)
        }
        val before = timer.state
        val state = timer.tick(nowElapsed, watching)
        if (state != before || nowElapsed - lastSavedAt >= SAVE_EVERY_MS || nowWall < lastSavedWall) {
            persist(nowElapsed, nowWall)
        }
        publish()
        return state
    }

    /** The settings page: applies at once, to today's usage as it stands (spec, P1-3). */
    fun applyLimits(next: Limits, nowElapsed: Long = SystemClock.elapsedRealtime(), nowWall: Long = System.currentTimeMillis()) {
        limits = next.clamped()
        store?.saveLimits(limits)
        timer.configure(limits.dailyMs, limits.sessionMs, limits.intervalMs, nowElapsed)
        persist(nowElapsed, nowWall)
        publish(force = true)
    }

    /** The password's 30 minutes / 1 hour / 2 hours: added to today, the settings stay as they are. */
    fun extendDaily(extraMs: Long, nowElapsed: Long = SystemClock.elapsedRealtime(), nowWall: Long = System.currentTimeMillis()) {
        timer.extendDaily(extraMs, nowElapsed)
        persist(nowElapsed, nowWall)
        publish(force = true)
    }

    /** The password's "no limit today": the daily limit only, the session limit keeps running. */
    fun liftDailyLimit(nowElapsed: Long = SystemClock.elapsedRealtime(), nowWall: Long = System.currentTimeMillis()) {
        timer.liftDailyLimit(nowElapsed)
        persist(nowElapsed, nowWall)
        publish(force = true)
    }

    /** The password's "skip this interval": the session starts over at once. */
    fun skipInterval(nowElapsed: Long = SystemClock.elapsedRealtime(), nowWall: Long = System.currentTimeMillis()) {
        timer.skipInterval()
        persist(nowElapsed, nowWall)
        publish(force = true)
    }

    /** Written when Aerial goes to the background, or the process may be killed with a minuted owed. */
    fun flush(nowElapsed: Long = SystemClock.elapsedRealtime(), nowWall: Long = System.currentTimeMillis()) {
        if (store != null) persist(nowElapsed, nowWall)
    }

    /**
     * A repaired day: the counters back to zero, the lock gone with them, the limits and the password
     * as they were. For an owner whose count went wrong (a firmware that never reported playback, a
     * clock put back) and for `tools/selftest.sh`, which uses it to start from a known zero and puts
     * the real counters back with [restoreDay] when it is done.
     */
    fun resetToday(nowElapsed: Long = SystemClock.elapsedRealtime(), nowWall: Long = System.currentTimeMillis()) {
        restoreDay(0L, 0L, false, 0L, WatchTimer.State.Normal, 0, nowElapsed, nowWall)
    }

    /**
     * Today's counters put back exactly as they were — the other half of [resetToday], for a test
     * that has to move them and then leave the TV as it found it.
     */
    fun restoreDay(
        dailyUsedMs: Long,
        dailyExtraMs: Long,
        unrestricted: Boolean,
        sessionUsedMs: Long,
        state: WatchTimer.State,
        intervalLeftSec: Int,
        nowElapsed: Long = SystemClock.elapsedRealtime(),
        nowWall: Long = System.currentTimeMillis(),
    ) {
        if (store == null) return
        day = ParentalStore.dayKey(nowWall)
        val endsAt = if (state == WatchTimer.State.Interval) nowElapsed + intervalLeftSec.coerceAtLeast(0) * 1000L else 0L
        timer.restore(
            WatchTimer.Snapshot(dailyUsedMs, dailyExtraMs, unrestricted, sessionUsedMs, state, endsAt),
            nowElapsed,
        )
        persist(nowElapsed, nowWall)
        publish(force = true)
    }

    /** Five wrong entries are forgotten at once, without touching the password itself. */
    fun forgetAttempts() {
        attempts?.clear()
    }

    /**
     * Today's counted milliseconds, unrounded. [Ui.dailyUsedMs] is published at minute resolution
     * because that is all the interface shows; measuring whether a video is counted at all needs the
     * raw number, otherwise the first minute of every reading is a zero.
     */
    fun usedNowMs(): Long = if (store == null) 0L else timer.snapshot().dailyUsedMs

    /** Seconds left of an interval, for the lock screen's countdown. 0 when none is running. */
    fun intervalLeftSec(nowElapsed: Long = SystemClock.elapsedRealtime()): Int {
        val ends = ui.intervalEndsAt
        return if (ui.state == WatchTimer.State.Interval && ends > nowElapsed) ((ends - nowElapsed) / 1000L).toInt() else 0
    }

    /**
     * The clock, from a screen that does not count: the lock screen, or the guard asking whether it
     * should keep the lock in front. An interval whose deadline has passed ends here, so a lock can
     * let go even when Home's own tick is not running (its window destroyed, the app started by the
     * guard after a boot). Nothing is counted — see [WatchTimer.checkDeadline].
     */
    fun idle(nowElapsed: Long = SystemClock.elapsedRealtime()) {
        if (store == null) return
        timer.checkDeadline(nowElapsed)
        publish()
    }

    /** Whether a limit is used up as of this moment, ending an interval whose time is up first. */
    fun lockedNow(nowElapsed: Long = SystemClock.elapsedRealtime()): Boolean {
        if (store == null) return false
        idle(nowElapsed)
        return ui.locked
    }

    // ---- the password ----

    val hasPin: Boolean get() = store?.hasPin() == true

    fun setPin(pin: String) {
        store?.setPin(pin)
        publish(force = true)
    }

    /**
     * The password, with the failure rule on top (docs/PARENTAL.md, 密码): five wrong in a row close
     * it for five minutes, and a right one inside that wait is refused as well — otherwise the wait
     * would mean nothing.
     */
    fun verifyPin(pin: String): Boolean = answer { store?.verifyPin(pin) == true }

    /** The fixed phrase: the way back in when the password is forgotten, and what a change asks for. */
    fun verifyAdmin(phrase: String): Boolean = answer { store?.verifyAdmin(phrase) == true }

    /** Seconds the keypad stays shut, for the lock screen's message; 0 while it is open. */
    fun blockedSec(nowWall: Long = System.currentTimeMillis()): Int = attempts?.leftSec(nowWall) ?: 0

    fun clearPin() {
        store?.clearPin()
        publish(force = true)
    }

    /** One answer to the keypad, counted against the failure rule. */
    private fun answer(check: () -> Boolean): Boolean {
        val guard = attempts ?: return check()
        val now = System.currentTimeMillis()
        if (guard.leftSec(now) > 0) return false
        val ok = check()
        if (ok) guard.right() else guard.wrong(now)
        return ok
    }

    // ---- internals ----

    private fun persist(nowElapsed: Long, nowWall: Long) {
        val s = store ?: return
        val snap = timer.snapshot()
        val endsWall = if (snap.state == WatchTimer.State.Interval && snap.intervalEndsAt > 0L)
            deadlineOnWall(snap.intervalEndsAt, nowElapsed, nowWall) else 0L
        s.saveProgress(
            ParentalStore.Progress(
                day = day,
                dailyUsedMs = snap.dailyUsedMs,
                dailyExtraMs = snap.dailyExtraMs,
                unrestricted = snap.dailyUnrestricted,
                sessionUsedMs = snap.sessionUsedMs,
                state = snap.state,
                intervalEndsWallMs = endsWall,
            ),
        )
        lastSavedAt = nowElapsed
        lastSavedWall = nowWall
    }

    private fun publish(force: Boolean = false) {
        val snap = timer.snapshot()
        val next = Ui(
            state = snap.state,
            dailyUsedMs = snap.dailyUsedMs,
            dailyBudgetMs = timer.dailyBudgetMs,
            sessionUsedMs = snap.sessionUsedMs,
            sessionBudgetMs = timer.sessionBudgetMs,
            intervalMs = timer.intervalMs,
            intervalEndsAt = snap.intervalEndsAt,
            unrestrictedToday = snap.dailyUnrestricted,
            hasPin = store?.hasPin() == true,
            limits = limits,
        )
        val current = ui
        val changed = next.state != current.state ||
            next.dailyUsedMs / MINUTE_MS != current.dailyUsedMs / MINUTE_MS ||
            next.sessionUsedMs / MINUTE_MS != current.sessionUsedMs / MINUTE_MS ||
            next.dailyBudgetMs != current.dailyBudgetMs ||
            next.sessionBudgetMs != current.sessionBudgetMs ||
            next.intervalEndsAt != current.intervalEndsAt ||
            next.unrestrictedToday != current.unrestrictedToday ||
            next.hasPin != current.hasPin ||
            next.limits != current.limits
        if (force || changed) ui = next
    }

    private const val MINUTE_MS = 60_000L

    /** Every this often the counters are written even without a change, so a kill loses a minute. */
    private const val SAVE_EVERY_MS = 30_000L
}

/**
 * A countdown saved as wall-clock, put back on the clock the timer ticks with. The two clocks are
 * only ever related through a difference taken at the same moment, so a wrong clock shortens or
 * lengthens one countdown; it cannot reset a day (see [ParentalStore]).
 */
internal fun deadlineOnElapsed(endsWallMs: Long, nowElapsed: Long, nowWall: Long): Long =
    nowElapsed + (endsWallMs - nowWall)

/** The other way: a running countdown as wall-clock, for the next start. */
internal fun deadlineOnWall(endsAt: Long, nowElapsed: Long, nowWall: Long): Long =
    nowWall + (endsAt - nowElapsed)

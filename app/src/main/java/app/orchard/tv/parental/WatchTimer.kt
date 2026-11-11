package app.orchard.tv.parental

/**
 * The parental time limits, as a state machine with no Android dependencies: the caller ticks it
 * with elapsedRealtime (and says whether something is playing), and it answers what to show.
 * docs/PARENTAL.md is the spec this implements; change that first.
 *
 * The decisions worth knowing while reading the code:
 *  - The daily and the per-session budgets run at the same time, and whichever runs out first
 *    locks: the daily limit gets its own screen, the session limit gets the countdown.
 *  - A session that runs out when the day has nothing left goes straight to [State.DailyLimit]:
 *    making anyone wait out an interval they cannot use would only annoy them.
 *  - "No limit today" lifts the daily limit alone. The session limit and its intervals stay, so
 *    the button says 总时长 rather than 不再限制.
 *  - Extending adds to today only: [dailyExtraMs] is kept apart from the configured budget so a
 *    new day starts from the value in the settings.
 *  - [SESSION_GAP_MS] with nothing playing ends the session, so its budget starts over. Pausing
 *    inside a video app counts as nothing playing, which is deliberately lenient: the daily budget
 *    is the hard limit (see the spec's "已知的宽松点").
 */
class WatchTimer {

    enum class State {
        /** Watching is allowed and counted. */
        Normal,

        /** The session budget is used up: locked until the countdown ends or the password skips it. */
        Interval,

        /** Today's budget is used up: locked until the password extends it or lifts it. */
        DailyLimit,
    }

    /** The daily budget as configured, 0 = no limit. */
    private var dailyBaseMs = 0L
    private var sessionBaseMs = 0L
    private var intervalBaseMs = MIN_INTERVAL_MS

    /** What the password added today; cleared at roll-over, unlike the configured budget. */
    var dailyExtraMs = 0L
        private set

    /** The password's "no limit today": the daily limit only, so the session limit still runs. */
    var dailyUnrestricted = false
        private set

    var dailyUsedMs = 0L
        private set
    var sessionUsedMs = 0L
        private set

    var state = State.Normal
        private set

    /** When the countdown ends, on the same clock the caller ticks with; 0 outside [State.Interval]. */
    var intervalEndsAt = 0L
        private set

    /** Today's budget including what the password added; 0 means no limit. */
    val dailyBudgetMs: Long get() = if (dailyBaseMs == 0L) 0L else dailyBaseMs + dailyExtraMs

    val sessionBudgetMs: Long get() = sessionBaseMs

    val intervalMs: Long get() = intervalBaseMs

    private var lastTickAt = 0L
    private var quietSince = 0L

    /**
     * The three settings, then the state they imply. Called on start and whenever the settings page
     * saves: a changed budget applies to today's usage at once, not from tomorrow (spec, P1-3).
     */
    fun configure(dailyMs: Long, sessionMs: Long, interval: Long, now: Long) {
        dailyBaseMs = dailyMs.coerceAtLeast(0L)
        sessionBaseMs = sessionMs.coerceAtLeast(0L)
        intervalBaseMs = interval.coerceIn(MIN_INTERVAL_MS, MAX_INTERVAL_MS)
        reevaluate(now)
    }

    /**
     * One tick of the caller's clock. [watching] is whether a video is playing (the spec's three
     * signals, already resolved by the caller). Returns the state to show, so the caller can start
     * the lock screen the moment it changes.
     */
    fun tick(now: Long, watching: Boolean): State {
        // A gap longer than MAX_TICK_MS means the process was not running (standby, killed): the
        // time is not known to have been watched, so none of it is counted.
        val dt = if (lastTickAt == 0L) 0L else (now - lastTickAt).coerceIn(0L, MAX_TICK_MS)
        lastTickAt = now

        checkDeadline(now)

        if (state == State.Normal) {
            if (!watching) {
                if (quietSince == 0L) quietSince = now
                if (now - quietSince >= SESSION_GAP_MS) sessionUsedMs = 0L
            } else {
                quietSince = 0L
                // Counted even with "no limit today": the settings page still shows what was used.
                dailyUsedMs += dt
                if (sessionBaseMs > 0L) sessionUsedMs += dt
                reevaluate(now)
            }
        }
        return state
    }

    /** The password's "skip this interval": the session starts over, the day is untouched. */
    fun skipInterval() {
        if (state == State.Interval) endSession()
    }

    /**
     * The countdown as a clock, for a caller that does not count — the lock screen, the guard. An
     * interval whose deadline has passed ends here, so a lock can let go even when Home's own tick is
     * not running (its window destroyed, the app started by the guard after a boot).
     *
     * Deliberately not [tick]: that counts the time between Home's passes, and `lastTickAt` belongs
     * to the one caller that counts. Two tickers a second apart would count every second twice.
     */
    fun checkDeadline(now: Long) {
        if (state == State.Interval && now >= intervalEndsAt) endSession()
    }

    /** The password's 30 minutes / 1 hour / 2 hours: today only, the session limit stays. */
    fun extendDaily(extraMs: Long, now: Long) {
        if (dailyBaseMs == 0L) return
        dailyExtraMs += extraMs.coerceAtLeast(0L)
        reevaluate(now)
    }

    /** The password's "no limit today": the daily limit lifts, the session limit keeps running. */
    fun liftDailyLimit(now: Long) {
        if (dailyBaseMs == 0L) return
        dailyUnrestricted = true
        reevaluate(now)
    }

    /**
     * A new local day. Today's usage and extensions go; a running countdown does not, because the
     * wait was already spent (spec, P1-2).
     */
    fun rollOver(now: Long) {
        dailyUsedMs = 0L
        dailyExtraMs = 0L
        dailyUnrestricted = false
        if (state == State.DailyLimit) {
            state = State.Normal
            reevaluate(now)
        }
    }

    /**
     * What has to survive the process: the counters and the lock. The countdown deadline is kept in
     * elapsedRealtime here; ParentalStore stores it as wall-clock instead, since elapsedRealtime
     * restarts at boot.
     */
    data class Snapshot(
        val dailyUsedMs: Long,
        val dailyExtraMs: Long,
        val dailyUnrestricted: Boolean,
        val sessionUsedMs: Long,
        val state: State,
        val intervalEndsAt: Long,
    )

    fun snapshot() = Snapshot(dailyUsedMs, dailyExtraMs, dailyUnrestricted, sessionUsedMs, state, intervalEndsAt)

    fun restore(s: Snapshot, now: Long) {
        dailyUsedMs = s.dailyUsedMs
        dailyExtraMs = s.dailyExtraMs
        dailyUnrestricted = s.dailyUnrestricted
        sessionUsedMs = s.sessionUsedMs
        state = s.state
        intervalEndsAt = s.intervalEndsAt
        lastTickAt = now
        // A countdown whose deadline has already passed while the process was dead ends now.
        if (state == State.Interval && now >= intervalEndsAt) endSession()
    }

    /** Whichever limit is used up first wins; the daily one wins a tie, so nobody waits for nothing. */
    private fun reevaluate(now: Long) {
        if (state == State.Interval) {
            when {
                dailyExhausted() -> {
                    intervalEndsAt = 0L
                    state = State.DailyLimit
                }
                // The settings page raised or lifted the session budget: no point in waiting now.
                !sessionExhausted() -> endSession()
            }
            return
        }
        when {
            dailyExhausted() -> {
                intervalEndsAt = 0L
                state = State.DailyLimit
            }
            sessionExhausted() -> {
                intervalEndsAt = now + intervalBaseMs
                state = State.Interval
            }
            else -> state = State.Normal
        }
    }

    private fun endSession() {
        sessionUsedMs = 0L
        intervalEndsAt = 0L
        state = if (dailyExhausted()) State.DailyLimit else State.Normal
    }

    private fun dailyExhausted(): Boolean =
        !dailyUnrestricted && dailyBaseMs > 0L && dailyUsedMs >= dailyBudgetMs

    private fun sessionExhausted(): Boolean =
        sessionBaseMs > 0L && sessionUsedMs >= sessionBaseMs

    companion object {
        /** 10 minutes: the shortest interval the settings offer. */
        const val MIN_INTERVAL_MS = 10L * 60_000

        /** 60 minutes: the longest. */
        const val MAX_INTERVAL_MS = 60L * 60_000

        /** A tick longer than this means the process was not running: not counted. */
        const val MAX_TICK_MS = 60_000L

        /** Nothing playing for this long ends the session, so its budget starts over. */
        const val SESSION_GAP_MS = 5L * 60_000
    }
}

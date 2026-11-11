package app.orchard.tv.parental

import android.content.Context
import androidx.core.content.edit
import java.security.MessageDigest
import java.time.Instant
import java.time.ZoneId
import app.orchard.tv.BuildConfig

/**
 * What the parental module keeps between runs: the three limits, the counters, and the password.
 *
 * docs/PARENTAL.md is the spec. The fields follow from three rules there:
 *
 *  - **The day is a local date.** Counters are stamped with it, so a new day starts from the
 *    configured budgets rather than from what was watched yesterday (P1-2). A countdown that is
 *    running when the date changes is kept: the wait was already spent.
 *  - **The countdown's deadline is wall-clock here**, because [WatchTimer] ticks with
 *    elapsedRealtime, which restarts at boot. Wall clock is used for that one difference and for
 *    the date, never for counting: turning the TV's clock back cannot give anyone more time today,
 *    it can only make one countdown end sooner or later.
 *  - **"Extended" is kept apart from the budget.** The settings page's value stays what it is, and
 *    what the password added today is cleared at roll-over.
 */
class ParentalStore(context: Context) {

    private val prefs = context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

    /** The three settings, in minutes. 0 for the daily or the session one means no limit. */
    data class Limits(
        val dailyMinutes: Int = 0,
        val sessionMinutes: Int = 0,
        val intervalMinutes: Int = INTERVAL_MIN_MINUTES,
    ) {
        val dailyMs: Long get() = dailyMinutes * 60_000L
        val sessionMs: Long get() = sessionMinutes * 60_000L
        val intervalMs: Long get() = intervalMinutes * 60_000L

        /** Whatever the settings page hands over is made to fit the ranges it offers. */
        fun clamped() = Limits(
            dailyMinutes = dailyMinutes.coerceIn(0, MAX_DAILY_MINUTES),
            sessionMinutes = sessionMinutes.coerceIn(0, MAX_SESSION_MINUTES),
            intervalMinutes = intervalMinutes.coerceIn(INTERVAL_MIN_MINUTES, MAX_INTERVAL_MINUTES),
        )

        // ---- what one press of ← or → does on the settings page ----
        //
        // These are here rather than in the panel because they are the part with the awkward
        // corners (0 means "no limit", the day's top is 12 hours, the interval has no 0) and they
        // can be read and tested without a screen.

        /**
         * A whole hour either way. Out of 0 — "no limit" — → gives a quarter of an hour, which is
         * the first stop the panel has (spec, 界面: 0h0m must stay reachable and leaving it must not
         * mean jumping to 1 hour to get 15 minutes); ← has nowhere to go.
         */
        fun withDailyHours(delta: Int): Limits {
            if (delta == 0) return this
            if (dailyMinutes == 0) return if (delta > 0) copy(dailyMinutes = 15) else this
            val hours = (dailyMinutes / 60 + delta).coerceIn(0, MAX_HOURS)
            // Twelve hours is the top: a quarter of an hour does not fit after it, and the store
            // would clamp 12h45m down to 12h behind the panel's back.
            val rest = if (hours == MAX_HOURS) 0 else dailyMinutes % 60
            return copy(dailyMinutes = (hours * 60 + rest).coerceAtMost(MAX_DAILY_MINUTES))
        }

        /** A quarter of an hour either way, inside the hour. At 0h15 ← goes back to "no limit". */
        fun withDailyMinutes(delta: Int): Limits {
            if (delta == 0) return this
            val hours = dailyMinutes / 60
            val top = if (hours >= MAX_HOURS) 0 else 45
            val minutes = ((dailyMinutes % 60) + delta * 15).coerceIn(0, top)
            return copy(dailyMinutes = hours * 60 + minutes)
        }

        /** A quarter of an hour, 0 = no limit. */
        fun withSession(delta: Int): Limits =
            copy(sessionMinutes = stepped(sessionMinutes, delta, 15, 0, MAX_SESSION_MINUTES))

        /** Ten minutes, never 0: with no session limit there is nothing for the interval to follow. */
        fun withInterval(delta: Int): Limits =
            copy(intervalMinutes = stepped(intervalMinutes, delta, 10, INTERVAL_MIN_MINUTES, MAX_INTERVAL_MINUTES))

        private fun stepped(value: Int, delta: Int, unit: Int, min: Int, max: Int): Int {
            val stops = (max - min) / unit
            return min + ((value - min) / unit + delta).coerceIn(0, stops) * unit
        }
    }

    /**
     * The counters, as they stood when they were last written. [day] is the local date they belong
     * to; a different date on load means they are yesterday's and go.
     */
    data class Progress(
        val day: String,
        val dailyUsedMs: Long = 0L,
        val dailyExtraMs: Long = 0L,
        val unrestricted: Boolean = false,
        val sessionUsedMs: Long = 0L,
        val state: WatchTimer.State = WatchTimer.State.Normal,
        /** Wall-clock time the countdown ends, or 0 when none is running. */
        val intervalEndsWallMs: Long = 0L,
    )

    fun limits(): Limits = Limits(
        dailyMinutes = prefs.getInt(KEY_DAILY, 0),
        sessionMinutes = prefs.getInt(KEY_SESSION, 0),
        intervalMinutes = prefs.getInt(KEY_INTERVAL, INTERVAL_MIN_MINUTES),
    ).clamped()

    fun saveLimits(limits: Limits) {
        val fit = limits.clamped()
        prefs.edit {
            putInt(KEY_DAILY, fit.dailyMinutes)
            putInt(KEY_SESSION, fit.sessionMinutes)
            putInt(KEY_INTERVAL, fit.intervalMinutes)
        }
    }

    /** Null until something has been written: the first start has no counters at all. */
    fun progress(): Progress? {
        val day = prefs.getString(KEY_DAY, null) ?: return null
        return Progress(
            day = day,
            dailyUsedMs = prefs.getLong(KEY_DAILY_USED, 0L),
            dailyExtraMs = prefs.getLong(KEY_DAILY_EXTRA, 0L),
            unrestricted = prefs.getBoolean(KEY_UNRESTRICTED, false),
            sessionUsedMs = prefs.getLong(KEY_SESSION_USED, 0L),
            state = WatchTimer.State.entries.firstOrNull { it.name == prefs.getString(KEY_STATE, null) }
                ?: WatchTimer.State.Normal,
            intervalEndsWallMs = prefs.getLong(KEY_INTERVAL_ENDS, 0L),
        )
    }

    fun saveProgress(p: Progress) {
        prefs.edit {
            putString(KEY_DAY, p.day)
            putLong(KEY_DAILY_USED, p.dailyUsedMs)
            putLong(KEY_DAILY_EXTRA, p.dailyExtraMs)
            putBoolean(KEY_UNRESTRICTED, p.unrestricted)
            putLong(KEY_SESSION_USED, p.sessionUsedMs)
            putString(KEY_STATE, p.state.name)
            putLong(KEY_INTERVAL_ENDS, p.intervalEndsWallMs)
        }
    }

    fun clearProgress() {
        prefs.edit {
            remove(KEY_DAY)
            remove(KEY_DAILY_USED)
            remove(KEY_DAILY_EXTRA)
            remove(KEY_UNRESTRICTED)
            remove(KEY_SESSION_USED)
            remove(KEY_STATE)
            remove(KEY_INTERVAL_ENDS)
        }
    }

    // ---- the password ----

    fun hasPin(): Boolean = !prefs.getString(KEY_PIN, null).isNullOrEmpty()

    /** Only ever called with a password the pad has already checked for length. */
    fun setPin(pin: String) {
        prefs.edit { putString(KEY_PIN, digest(PIN_SCOPE, pin)) }
    }

    fun clearPin() {
        prefs.edit { remove(KEY_PIN) }
    }

    fun verifyPin(pin: String): Boolean {
        val stored = prefs.getString(KEY_PIN, null) ?: return false
        return same(stored, digest(PIN_SCOPE, pin))
    }

    /**
     * The fixed administrator phrase, which is the way back in when the password is forgotten
     * (spec, 忘记密码). Only its hash is here: the phrase itself is not in the APK's strings, and
     * the hash of a documented phrase is a lock on the front door rather than a secret. A build
     * injected from local/admin.properties (gitignored); see AGENTS.md.
     */
    fun verifyAdmin(phrase: String): Boolean =
        ADMIN_HASH.isNotEmpty() && same(ADMIN_HASH, digest(ADMIN_SCOPE, phrase))

    companion object {
        /** 12 hours: the top of the daily stepper. */
        const val MAX_DAILY_MINUTES = 12 * 60

        /** The daily top in whole hours: 12, what the hour half of the panel's stepper stops at. */
        private const val MAX_HOURS = MAX_DAILY_MINUTES / 60

        /** 60 minutes: the top of the session stepper. */
        const val MAX_SESSION_MINUTES = 60

        /** 10 minutes: the shortest interval, and the only value when the session limit is off. */
        const val INTERVAL_MIN_MINUTES = 10

        const val MAX_INTERVAL_MINUTES = 60

        private const val PREFS = "parental"
        private const val KEY_DAILY = "daily_minutes"
        private const val KEY_SESSION = "session_minutes"
        private const val KEY_INTERVAL = "interval_minutes"
        private const val KEY_DAY = "day"
        private const val KEY_DAILY_USED = "daily_used_ms"
        private const val KEY_DAILY_EXTRA = "daily_extra_ms"
        private const val KEY_UNRESTRICTED = "daily_unrestricted"
        private const val KEY_SESSION_USED = "session_used_ms"
        private const val KEY_STATE = "state"
        private const val KEY_INTERVAL_ENDS = "interval_ends_wall"
        private const val KEY_PIN = "pin_hash"
        private const val KEY_STRICT = "strict_watch"
        private const val KEY_TICKET = "ticket_minutes"

        private const val PIN_SCOPE = "aerial-parental:"
        private const val ADMIN_SCOPE = "aerial-admin:"

        /**
         * Hash of the administrator recovery phrase, injected at build time from
         * local/admin.properties (gitignored, see AGENTS.md). A checkout without
         * that file gets an empty string, which disables the recovery path.
         */
        private val ADMIN_HASH: String get() = BuildConfig.ADMIN_HASH

        /** The local date [millis] falls on, which is how a day is told from the next. */
        fun dayKey(millis: Long, zone: ZoneId = ZoneId.systemDefault()): String =
            Instant.ofEpochMilli(millis).atZone(zone).toLocalDate().toString()

        // ---- 严格模式 (docs/PARENTAL.md, 严格模式) ----

        /** A ticket's length in the choices the settings page offers. */
        val TICKET_CHOICES = listOf(15, 30, 45, 60)

        const val DEFAULT_TICKET_MINUTES = 30

        /**
         * Whether a video app needs a ticket too. Off by default: 记账 is what most homes want, and
         * this mode costs a parent being there — see the spec for what each one can and cannot do.
         */
        fun strictWatch(context: Context): Boolean = store(context).getBoolean(KEY_STRICT, false)

        /** Switching the mode off ends any ticket at once: nothing may outlive the mode it was for. */
        fun setStrictWatch(context: Context, on: Boolean) {
            store(context).edit { putBoolean(KEY_STRICT, on) }
            if (!on) WatchTicket.clear()
        }

        /** How long one ticket lasts; one of [TICKET_CHOICES]. */
        fun ticketMinutes(context: Context): Int =
            store(context).getInt(KEY_TICKET, DEFAULT_TICKET_MINUTES)
                .takeIf { it in TICKET_CHOICES } ?: DEFAULT_TICKET_MINUTES

        fun setTicketMinutes(context: Context, minutes: Int) {
            store(context).edit { putInt(KEY_TICKET, minutes.coerceIn(TICKET_CHOICES.first(), TICKET_CHOICES.last())) }
        }

        private fun store(context: Context) =
            context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)

        private fun digest(scope: String, text: String): String {
            val bytes = MessageDigest.getInstance("SHA-256").digest((scope + text).toByteArray(Charsets.UTF_8))
            return buildString(bytes.size * 2) {
                for (b in bytes) {
                    val v = b.toInt() and 0xFF
                    append(HEX[v ushr 4])
                    append(HEX[v and 0x0F])
                }
            }
        }

        private const val HEX = "0123456789abcdef"

        /** Compared as bytes so that a wrong guess takes the same time however wrong it is. */
        private fun same(a: String, b: String): Boolean =
            MessageDigest.isEqual(a.toByteArray(Charsets.UTF_8), b.toByteArray(Charsets.UTF_8))
    }
}

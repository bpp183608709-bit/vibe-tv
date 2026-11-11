package app.orchard.tv.parental

import android.content.Context

/**
 * The password's failure rule (docs/PARENTAL.md, 密码): five wrong entries in a row close the
 * password — and the administrator phrase with it — for five minutes, even to the right one. Without
 * it the keypad on the lock screen can be guessed at for as long as a child is willing to sit there,
 * and an eight digit password is only a million presses.
 *
 * It is kept beside [ParentalStore] rather than in it on purpose: the store holds what the parent
 * chose, this holds a guard against guessing, and the guard has to outlast a kill — a child who has
 * found the keypad would otherwise force-stop Aerial and start again. The deadline is wall clock, so
 * it also outlasts a reboot; [AttemptState.leftSec] never lets a clock put forward or back stretch
 * the wait.
 */
class Attempts(context: Context) {

    private val prefs = context.applicationContext.getSharedPreferences(FILE, Context.MODE_PRIVATE)

    fun state(): AttemptState = AttemptState(prefs.getInt(FAILS, 0), prefs.getLong(UNTIL, 0L))

    /** Seconds still to wait; 0 while entry is open. */
    fun leftSec(nowWall: Long = System.currentTimeMillis()): Int = state().leftSec(nowWall)

    /** One wrong entry: counted, or — on the fifth — the keypad shut for a while. */
    fun wrong(nowWall: Long = System.currentTimeMillis()) {
        val next = state().wrong(nowWall)
        prefs.edit().putInt(FAILS, next.fails).putLong(UNTIL, next.untilWall).apply()
    }

    /** A right one: the count starts over. */
    fun right() {
        prefs.edit().putInt(FAILS, 0).putLong(UNTIL, 0L).apply()
    }

    /**
     * The wait is over, whatever it was: for an owner whose five misses were their own (the ADB
     * route in [ParentalProbe.reset]) and for a test that has to leave the TV as it found it.
     */
    fun clear() {
        prefs.edit().putInt(FAILS, 0).putLong(UNTIL, 0L).apply()
    }

    private companion object {
        const val FILE = "parental_attempts"
        const val FAILS = "pin_fail"
        const val UNTIL = "pin_lock_until"
    }
}

/**
 * The same rule as arithmetic, so each case can be read and tested without a TV: [FAILS_ALLOWED]
 * wrong entries in a row close entry for [LOCK_MS]; a right one, or a wait that has run out, starts
 * the count over.
 */
data class AttemptState(val fails: Int = 0, val untilWall: Long = 0L) {

    /** Seconds to wait, rounded up; 0 while entry is open. */
    fun leftSec(nowWall: Long): Int {
        if (untilWall <= 0L) return 0
        // Never more than the rule allows: a clock set back must not add hours to the wait.
        val left = (untilWall - nowWall).coerceAtMost(LOCK_MS)
        return if (left > 0L) ((left + 999L) / 1000L).toInt() else 0
    }

    /**
     * The entry after a wrong one. The fifth closes entry and clears the count, so the next five
     * start from zero once the wait is over rather than shutting it again on the first miss.
     */
    fun wrong(nowWall: Long): AttemptState {
        val next = fails + 1
        return if (next >= FAILS_ALLOWED) AttemptState(0, nowWall + LOCK_MS) else AttemptState(next, 0L)
    }

    /** A right entry: everything resets. */
    fun right(): AttemptState = AttemptState()

    companion object {
        /** Five in a row. */
        const val FAILS_ALLOWED = 5

        /** And five minutes shut. */
        const val LOCK_MS = 5L * 60_000L
    }
}

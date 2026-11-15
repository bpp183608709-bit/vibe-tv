package app.orchard.tv.quiz

/**
 * The two clocks over a quiz page (docs/PARENTAL.md, 界面): ten minutes for the group, three for the
 * child to touch something.
 *
 * Arithmetic on purpose, and separate from [QuizRun]: the rule is "the page closes itself", and the
 * first time that rule is wrong a child loses a group they were halfway through. Two functions with
 * a boundary are worth more than that.
 *
 * The boundary is inclusive at the limit — at exactly ten minutes the page is over — because the
 * tick that checks it runs once a second and a strict comparison would give an extra second.
 */
object QuizLimits {

    /** Whole group, from the first question appearing (C4). */
    const val GROUP_LIMIT_MS = 10 * 60_000L

    /** No key pressed for this long and the page goes back to the lock (C17). */
    const val IDLE_LIMIT_MS = 3 * 60_000L

    /** Whether a group started at [startedAt] has run out of time. A start of 0 means "not started". */
    fun expired(startedAt: Long, now: Long): Boolean = startedAt > 0L && now - startedAt >= GROUP_LIMIT_MS

    /** Whether the child has left the page alone. A touch of 0 means "not touched yet". */
    fun idle(touchedAt: Long, now: Long): Boolean = touchedAt > 0L && now - touchedAt >= IDLE_LIMIT_MS

    /** Seconds left of the group, for the line in the corner. Never negative. */
    fun groupLeftSec(startedAt: Long, now: Long): Int =
        if (startedAt <= 0L) (GROUP_LIMIT_MS / 1_000L).toInt()
        else ((GROUP_LIMIT_MS - (now - startedAt)) / 1_000L).toInt().coerceAtLeast(0)
}

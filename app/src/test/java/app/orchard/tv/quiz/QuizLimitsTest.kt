package app.orchard.tv.quiz

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The two clocks over the quiz page (docs/PARENTAL.md: 整组 10 分钟、3 分钟无操作自动退出).
 *
 * The boundary is the whole point: one second early and a child loses a group they were still
 * answering, one second late and the page stays up with nobody at the remote.
 */
class QuizLimitsTest {

    private val t0 = 1_762_000_000_000L

    @Test
    fun aGroupEndsAtTenMinutes() {
        assertFalse(QuizLimits.expired(t0, t0))
        assertFalse(QuizLimits.expired(t0, t0 + QuizLimits.GROUP_LIMIT_MS - 1))
        assertTrue(QuizLimits.expired(t0, t0 + QuizLimits.GROUP_LIMIT_MS))
        assertTrue(QuizLimits.expired(t0, t0 + QuizLimits.GROUP_LIMIT_MS + 5_000))
    }

    @Test
    fun thePageClosesAfterThreeQuietMinutes() {
        assertFalse(QuizLimits.idle(t0, t0))
        assertFalse(QuizLimits.idle(t0, t0 + QuizLimits.IDLE_LIMIT_MS - 1))
        assertTrue(QuizLimits.idle(t0, t0 + QuizLimits.IDLE_LIMIT_MS))
    }

    @Test
    fun aClockThatHasNotStartedNeverFires() {
        // 0 is "no start yet": the page must not close itself before the first question arrives, and
        // an idle check on a run that has not been touched must not fire either.
        assertFalse(QuizLimits.expired(0L, t0))
        assertFalse(QuizLimits.idle(0L, t0))
        assertEquals((QuizLimits.GROUP_LIMIT_MS / 1_000L).toInt(), QuizLimits.groupLeftSec(0L, t0))
    }

    @Test
    fun theCornerClockCountsDownAndStopsAtZero() {
        assertEquals(600, QuizLimits.groupLeftSec(t0, t0))
        assertEquals(599, QuizLimits.groupLeftSec(t0, t0 + 1_000))
        assertEquals(1, QuizLimits.groupLeftSec(t0, t0 + 599_000))
        assertEquals(0, QuizLimits.groupLeftSec(t0, t0 + 600_000))
        // Past the limit the corner shows 00:00, never a negative number.
        assertEquals(0, QuizLimits.groupLeftSec(t0, t0 + 900_000))
    }
}

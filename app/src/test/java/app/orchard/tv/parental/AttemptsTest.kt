package app.orchard.tv.parental

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The password's failure rule (docs/PARENTAL.md, 密码, 验收 10): five wrong entries in a row lock the
 * keypad for five minutes, the count starts over afterwards, and a right entry clears it. Kept in
 * [AttemptState] as arithmetic so every case can be checked without a TV.
 */
class AttemptsTest {

    private val t0 = 1_700_000_000_000L

    private fun wrongs(n: Int): AttemptState =
        (1..n).fold(AttemptState()) { state, _ -> state.wrong(t0) }

    @Test
    fun fourWrongLeaveTheKeypadOpen() {
        val state = wrongs(4)
        assertEquals(4, state.fails)
        assertEquals(0, state.leftSec(t0))
        assertTrue(state.leftSec(t0) == 0)
    }

    @Test
    fun theFifthClosesItForFiveMinutes() {
        val state = wrongs(5)
        assertEquals(300, state.leftSec(t0))
        assertEquals(300, state.leftSec(t0 + 1))
    }

    @Test
    fun theWaitIsCountedInWholeSecondsRoundedUp() {
        val state = wrongs(5)
        // 4:59.001 left: a screen that showed 298 would say the wait is over a second early.
        assertEquals(300, state.leftSec(t0 - 999))
        assertEquals(1, state.leftSec(t0 + 299_001))
        assertEquals(0, state.leftSec(t0 + 300_000))
    }

    @Test
    fun theCountStartsOverOnceTheWaitIsOver() {
        // The fifth cleared the count, so the sixth miss does not shut the keypad again at once.
        val afterWait = AttemptState(0, t0 + 300_000)
        assertEquals(0, afterWait.leftSec(t0 + 300_000))
        assertEquals(1, afterWait.wrong(t0 + 300_001).fails)
    }

    @Test
    fun aRightEntryClearsEverything() {
        assertEquals(AttemptState(), wrongs(4).right())
    }

    @Test
    fun aClockSetBackDoesNotStretchTheWait() {
        // The deadline is wall clock, so a TV whose clock goes back a day would otherwise stay shut.
        val state = wrongs(5)
        assertEquals(300, state.leftSec(t0 - 24 * 60 * 60 * 1000L))
    }

    @Test
    fun aClockSetForwardEndsTheWait() {
        // The other direction is the accepted cost of a wall clock (spec, 管不到的): the child can
        // wait it out by moving the TV's clock, the same way they could reboot it.
        assertEquals(0, wrongs(5).leftSec(t0 + 24 * 60 * 60 * 1000L))
        assertFalse(AttemptState(0, t0 + 1000).leftSec(t0 + 2000) > 0)
    }
}

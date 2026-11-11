package app.orchard.tv.parental

import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * 严格模式's ticket: what it covers, when it stops, and how a child stepping out for a minute is
 * told from one going around the password.
 */
class WatchTicketTest {

    private val video = "com.ktcp.video"
    private val other = "com.gitvdemo.video"
    private val launcher = "com.mitv.tvhome"

    @After
    fun clear() = WatchTicket.clear()

    @Test
    fun withoutATicketNothingIsHeldOrCounted() {
        assertFalse(WatchTicket.holds(video, 0))
        assertFalse(WatchTicket.counting(video, 0))
        assertNull(WatchTicket.holder())
        assertEquals(0, WatchTicket.leftSec(0))
    }

    @Test
    fun aGrantedTicketHoldsAndCounts() {
        WatchTicket.grant(video, 1_000, MINUTES_30)
        assertTrue(WatchTicket.holds(video, 1_000))
        assertTrue(WatchTicket.counting(video, 1_000))
        assertEquals(30 * 60, WatchTicket.leftSec(1_000))
    }

    @Test
    fun theClockRunsEvenWhileNothingPlays() {
        // The difference from 记账: a child parked on the app's home screen is on the parent's time.
        WatchTicket.grant(video, 0, MINUTES_30)
        assertTrue(WatchTicket.counting(video, 5_000))
        assertTrue(WatchTicket.counting(video, 60_000))
    }

    @Test
    fun aTicketStopsWhenItIsSpent() {
        WatchTicket.grant(video, 0, MINUTES_30)
        assertTrue(WatchTicket.counting(video, MINUTES_30 - 1_000))
        assertFalse(WatchTicket.counting(video, MINUTES_30))
        assertFalse(WatchTicket.holds(video, MINUTES_30))
        assertNull(WatchTicket.holder())
    }

    @Test
    fun steppingOutIsForgivenForAMinute() {
        WatchTicket.grant(video, 0, MINUTES_30)
        // In the app, then to the launcher for 40 s, then back: the same ticket still holds.
        assertTrue(WatchTicket.counting(video, 10_000))
        assertTrue(WatchTicket.counting(launcher, 30_000))
        assertTrue(WatchTicket.counting(launcher, 50_000))
        assertTrue(WatchTicket.counting(video, 51_000))
        assertTrue(WatchTicket.holds(video, 51_000))
    }

    @Test
    fun steppingOutForLongerEndsIt() {
        WatchTicket.grant(video, 0, MINUTES_30)
        assertTrue(WatchTicket.counting(video, 10_000))
        // 61 s away: the ticket is gone, and coming back asks for the password.
        assertFalse(WatchTicket.counting(launcher, 71_000))
        assertNull(WatchTicket.holder())
        assertFalse(WatchTicket.counting(video, 72_000))
    }

    @Test
    fun theGraceIsCountedAsWatching() {
        // Otherwise a child could bank minutes by stepping out every few minutes.
        WatchTicket.grant(video, 0, MINUTES_30)
        WatchTicket.counting(video, 10_000)
        assertTrue(WatchTicket.counting(launcher, 40_000))
    }

    @Test
    fun anotherAppDoesNotCarryItOver() {
        WatchTicket.grant(video, 0, MINUTES_30)
        assertTrue(WatchTicket.holds(video, 1_000))
        assertFalse(WatchTicket.holds(other, 1_000))
        // The ticket itself is untouched by that question: the same app can still come back to it.
        assertTrue(WatchTicket.holds(video, 2_000))
    }

    @Test
    fun aNewTicketStartsTheClockOver() {
        WatchTicket.grant(video, 0, MINUTES_30)
        assertTrue(WatchTicket.counting(video, MINUTES_30 - 1_000))
        WatchTicket.grant(video, MINUTES_30, MINUTES_30)
        assertEquals(30 * 60, WatchTicket.leftSec(MINUTES_30))
        assertTrue(WatchTicket.counting(video, MINUTES_30 + 1_000))
    }

    @Test
    fun aTicketTooShortToBeWorthAPasswordIsRaised() {
        WatchTicket.grant(video, 0, 1_000)
        assertEquals((WatchTicket.MIN_TICKET_MS / 1000).toInt(), WatchTicket.leftSec(0))
    }

    @Test
    fun clearingEndsItAtOnce() {
        WatchTicket.grant(video, 0, MINUTES_30)
        WatchTicket.clear()
        assertFalse(WatchTicket.holds(video, 1_000))
        assertFalse(WatchTicket.counting(video, 1_000))
    }
}

private const val MINUTES_30 = 30 * 60_000L

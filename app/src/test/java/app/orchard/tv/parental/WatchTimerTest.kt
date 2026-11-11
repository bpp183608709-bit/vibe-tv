package app.orchard.tv.parental

import app.orchard.tv.parental.WatchTimer.State
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The table in docs/PARENTAL.md, one case each. Pure logic, so no Robolectric: the tests drive the
 * clock by hand.
 */
class WatchTimerTest {

    private val timer = WatchTimer()
    private var now = 10_000_000L

    /** Minutes in, milliseconds on the timer's clock. */
    private fun mins(value: Int): Long = value * 60_000L

    private fun configure(dailyMin: Int = 0, sessionMin: Int = 0, intervalMin: Int = 10) {
        timer.configure(mins(dailyMin), mins(sessionMin), mins(intervalMin), now)
        // Baselines the clock, so the first play() counts from here.
        timer.tick(now, watching = false)
    }

    private fun play(minutes: Int) {
        var left = mins(minutes)
        while (left > 0L) {
            val step = minOf(1_000L, left)
            now += step
            timer.tick(now, watching = true)
            left -= step
        }
    }

    private fun rest(minutes: Int) {
        var left = mins(minutes)
        while (left > 0L) {
            val step = minOf(1_000L, left)
            now += step
            timer.tick(now, watching = false)
            left -= step
        }
    }

    @Test
    fun dailyBudgetAloneLocksTheDay() {
        configure(dailyMin = 120)
        play(119)
        assertEquals(State.Normal, timer.state)
        play(1)
        assertEquals(State.DailyLimit, timer.state)
        assertEquals(mins(120), timer.dailyUsedMs)
    }

    @Test
    fun sessionBudgetGivesTheCountdownThenStartsOver() {
        configure(sessionMin = 30, intervalMin = 10)
        play(30)
        assertEquals(State.Interval, timer.state)
        rest(9)
        assertEquals(State.Interval, timer.state)
        rest(1)
        assertEquals(State.Normal, timer.state)
        assertEquals(0L, timer.sessionUsedMs)
        // The point of the reset: the next 30 minutes are usable again.
        play(29)
        assertEquals(State.Normal, timer.state)
    }

    @Test
    fun theLimitThatRunsOutFirstWins() {
        configure(dailyMin = 20, sessionMin = 30)
        play(20)
        assertEquals(State.DailyLimit, timer.state)
        // No interval: waiting for one would be waiting for nothing.
        assertEquals(0L, timer.intervalEndsAt)
    }

    @Test
    fun bothRunningOutAtOnceGoesToTheDay() {
        configure(dailyMin = 20, sessionMin = 20)
        play(20)
        assertEquals(State.DailyLimit, timer.state)
    }

    @Test
    fun aCountdownIsCutShortWhenTheDayRunsOut() {
        configure(dailyMin = 30, sessionMin = 10)
        play(10)
        assertEquals(State.Interval, timer.state)
        // The settings page lowers the daily budget under what is already used.
        timer.configure(mins(5), mins(10), mins(10), now)
        assertEquals(State.DailyLimit, timer.state)
        assertEquals(0L, timer.intervalEndsAt)
    }

    @Test
    fun extendingAddsToTodayOnly() {
        configure(dailyMin = 20)
        play(20)
        assertEquals(State.DailyLimit, timer.state)
        timer.extendDaily(mins(30), now)
        assertEquals(State.Normal, timer.state)
        play(30)
        assertEquals(State.DailyLimit, timer.state)
        // A new day drops what the password added, not the configured budget.
        timer.rollOver(now)
        assertEquals(State.Normal, timer.state)
        assertEquals(0L, timer.dailyExtraMs)
        play(20)
        assertEquals(State.DailyLimit, timer.state)
    }

    @Test
    fun extendingIntoASpentSessionGivesTheCountdown() {
        configure(dailyMin = 20, sessionMin = 20)
        play(20)
        assertEquals(State.DailyLimit, timer.state)
        timer.extendDaily(mins(30), now)
        // The day has room again, the session does not: the session limit still applies.
        assertEquals(State.Interval, timer.state)
    }

    @Test
    fun noLimitTodayLiftsTheDayOnly() {
        configure(dailyMin = 20, sessionMin = 30)
        play(20)
        assertEquals(State.DailyLimit, timer.state)
        timer.liftDailyLimit(now)
        assertEquals(State.Normal, timer.state)
        play(10)
        assertEquals(State.Interval, timer.state)
        // Still counted, for the settings page to show.
        assertEquals(mins(30), timer.dailyUsedMs)
    }

    @Test
    fun skippingTheCountdownStartsTheSessionOver() {
        configure(sessionMin = 30, intervalMin = 30)
        play(30)
        assertEquals(State.Interval, timer.state)
        timer.skipInterval()
        assertEquals(State.Normal, timer.state)
        assertEquals(0L, timer.sessionUsedMs)
        play(10)
        assertEquals(State.Normal, timer.state)
    }

    @Test
    fun anUnlimitedSessionNeverStartsAnInterval() {
        configure(sessionMin = 0, intervalMin = 10)
        play(300)
        assertEquals(State.Normal, timer.state)
    }

    @Test
    fun timeWithoutAVideoIsNotCounted() {
        configure(dailyMin = 60)
        rest(120)
        assertEquals(State.Normal, timer.state)
        assertEquals(0L, timer.dailyUsedMs)
    }

    @Test
    fun aGapTooLongToBeATickIsNotCounted() {
        configure(dailyMin = 120)
        // Five minutes pass in one tick: the process was not running, so only one tick counts.
        now += mins(5)
        timer.tick(now, watching = true)
        assertEquals(WatchTimer.MAX_TICK_MS, timer.dailyUsedMs)
    }

    @Test
    fun fiveQuietMinutesStartANewSession() {
        configure(sessionMin = 30)
        play(20)
        rest(6)
        assertEquals(0L, timer.sessionUsedMs)
        play(10)
        assertEquals(State.Normal, timer.state)
    }

    @Test
    fun aRunningCountdownSurvivesTheNight() {
        configure(sessionMin = 30, intervalMin = 10)
        play(30)
        val deadline = timer.intervalEndsAt
        timer.rollOver(now)
        assertEquals(State.Interval, timer.state)
        assertEquals(deadline, timer.intervalEndsAt)
    }

    @Test
    fun rollOverClearsTheDay() {
        configure(dailyMin = 20)
        play(20)
        assertEquals(State.DailyLimit, timer.state)
        timer.rollOver(now)
        assertEquals(State.Normal, timer.state)
        assertEquals(0L, timer.dailyUsedMs)
        play(15)
        assertEquals(State.Normal, timer.state)
    }

    @Test
    fun aCountdownRestoredAfterAKillKeepsItsRemainingTime() {
        configure(sessionMin = 30, intervalMin = 30)
        play(30)
        val saved = timer.snapshot()
        val deadline = timer.intervalEndsAt

        val revived = WatchTimer()
        revived.configure(mins(0), mins(30), mins(30), now)
        revived.restore(saved, now + mins(10))
        assertEquals(State.Interval, revived.state)
        assertEquals(mins(20), deadline - (now + mins(10)))
    }

    @Test
    fun aCountdownThatRanOutWhileDeadEndsOnRestore() {
        configure(sessionMin = 30, intervalMin = 30)
        play(30)
        val saved = timer.snapshot()

        val revived = WatchTimer()
        revived.configure(mins(0), mins(30), mins(30), now)
        revived.restore(saved, now + mins(45))
        assertEquals(State.Normal, revived.state)
        assertEquals(0L, revived.sessionUsedMs)
    }

    @Test
    fun noLimitAnywhereNeverLocks() {
        configure()
        play(600)
        assertEquals(State.Normal, timer.state)
        assertTrue(timer.dailyBudgetMs == 0L && timer.sessionBudgetMs == 0L)
    }

    // ---- the lock screen's clock: checkDeadline, which must move no counter ----

    /** The lock screen's own pass, once a second: the clock, and nothing else. */
    private fun watchTheClock(seconds: Int) {
        repeat(seconds) {
            now += 1_000L
            timer.checkDeadline(now)
        }
    }

    @Test
    fun theLockClockEndsTheCountdownItWasWaitingOn() {
        configure(sessionMin = 30, intervalMin = 10)
        play(30)
        assertEquals(State.Interval, timer.state)
        watchTheClock(9 * 60)
        assertEquals(State.Interval, timer.state)
        // One second short of the deadline is still the lock.
        watchTheClock(59)
        assertEquals(State.Interval, timer.state)
        watchTheClock(1)
        assertEquals(State.Normal, timer.state)
        assertEquals(0L, timer.sessionUsedMs)
        // Waiting is not watching: the day is exactly where the session left it.
        assertEquals(mins(30), timer.dailyUsedMs)
    }

    @Test
    fun theLockClockCountsNothingWhenNothingIsLocked() {
        configure(dailyMin = 120, sessionMin = 30)
        // No tick at all while the clock runs: whatever it does, it cannot add to the counters.
        watchTheClock(20 * 60)
        assertEquals(State.Normal, timer.state)
        assertEquals(0L, timer.dailyUsedMs)
        assertEquals(0L, timer.sessionUsedMs)
    }

    @Test
    fun theLockClockLeavesTheDailyLimitAlone() {
        configure(dailyMin = 30)
        play(30)
        assertEquals(State.DailyLimit, timer.state)
        // However long the screen is up, the day does not come back by itself; only the password
        // or a new day does that.
        watchTheClock(12 * 60 * 60)
        assertEquals(State.DailyLimit, timer.state)
        assertEquals(mins(30), timer.dailyUsedMs)
    }

    @Test
    fun theLockClockDoesNotMoveHomesBaseline() {
        configure(dailyMin = 60)
        // The lock runs between two of Home's passes; the next pass still counts from the last one,
        // so the seconds spent behind the lock are not quietly dropped from a watched minute.
        watchTheClock(10)
        now += 1_000L
        timer.tick(now, watching = true)
        assertEquals(11_000L, timer.dailyUsedMs)
    }
}

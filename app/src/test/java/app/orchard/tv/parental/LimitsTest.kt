package app.orchard.tv.parental

import app.orchard.tv.parental.ParentalStore.Limits
import org.junit.Assert.assertEquals
import org.junit.Test

/**
 * The settings page's steppers, which are the part of the parental module with the awkward
 * corners: 0 means "no limit" rather than "zero minutes", the day tops out at 12 hours, and the
 * interval has no 0 at all. Kept in [Limits] so they can be checked without a screen.
 */
class LimitsTest {

    private fun daily(minutes: Int) = Limits(dailyMinutes = minutes)

    // ---- the day, hour half ----

    @Test
    fun outOfUnlimitedRightGivesAQuarterOfAnHour() {
        // Not one hour: 15 minutes would otherwise be three presses away through 1 hour.
        assertEquals(15, daily(0).withDailyHours(1).dailyMinutes)
    }

    @Test
    fun outOfUnlimitedLeftStays() {
        assertEquals(0, daily(0).withDailyHours(-1).dailyMinutes)
    }

    @Test
    fun anHourIsAnHourWhateverTheQuarterIs() {
        assertEquals(45 + 60, daily(45).withDailyHours(1).dailyMinutes)
        assertEquals(90 + 60, daily(90).withDailyHours(1).dailyMinutes)
        assertEquals(90 - 60, daily(90).withDailyHours(-1).dailyMinutes)
    }

    @Test
    fun goingDownToZeroHoursKeepsTheQuarter() {
        assertEquals(30, daily(90).withDailyHours(-1).dailyMinutes)
    }

    @Test
    fun theTwelfthHourDropsTheQuarter() {
        // 11h45m → 12h, not 12h45m: the store would clamp that to 12h behind the panel.
        assertEquals(12 * 60, daily(11 * 60 + 45).withDailyHours(1).dailyMinutes)
    }

    @Test
    fun twelveHoursIsTheTop() {
        assertEquals(12 * 60, daily(12 * 60).withDailyHours(1).dailyMinutes)
    }

    // ---- the day, minute half ----

    @Test
    fun aQuarterOfAnHourEitherWay() {
        assertEquals(60 + 30, daily(60 + 15).withDailyMinutes(1).dailyMinutes)
        assertEquals(60 + 45, daily(60 + 30).withDailyMinutes(1).dailyMinutes)
        assertEquals(60 + 15, daily(60 + 30).withDailyMinutes(-1).dailyMinutes)
    }

    @Test
    fun theQuarterStopsAtItsEnds() {
        assertEquals(45, daily(45).withDailyMinutes(1).dailyMinutes)
        assertEquals(60, daily(60).withDailyMinutes(-1).dailyMinutes)
    }

    @Test
    fun backFromTheFirstQuarterIsUnlimited() {
        assertEquals(0, daily(15).withDailyMinutes(-1).dailyMinutes)
    }

    @Test
    fun noQuarterAfterTheTwelfthHour() {
        assertEquals(12 * 60, daily(12 * 60).withDailyMinutes(1).dailyMinutes)
    }

    // ---- the session ----

    @Test
    fun theSessionStepsByAQuarterOfAnHour() {
        assertEquals(15, Limits(sessionMinutes = 0).withSession(1).sessionMinutes)
        assertEquals(30, Limits(sessionMinutes = 45).withSession(-1).sessionMinutes)
        assertEquals(60, Limits(sessionMinutes = 45).withSession(1).sessionMinutes)
        assertEquals(60, Limits(sessionMinutes = 60).withSession(1).sessionMinutes)
    }

    // ---- the interval ----

    @Test
    fun theIntervalStepsByTenMinutesAndHasNoZero() {
        assertEquals(10, Limits(intervalMinutes = 10).withInterval(-1).intervalMinutes)
        assertEquals(20, Limits(intervalMinutes = 10).withInterval(1).intervalMinutes)
        assertEquals(60, Limits(intervalMinutes = 50).withInterval(1).intervalMinutes)
        assertEquals(60, Limits(intervalMinutes = 60).withInterval(1).intervalMinutes)
    }

    @Test
    fun aPressOfNothingChangesNothing() {
        val l = Limits(dailyMinutes = 90, sessionMinutes = 30, intervalMinutes = 20)
        assertEquals(l, l.withDailyHours(0))
        assertEquals(l, l.withDailyMinutes(0))
        assertEquals(l, l.withSession(0))
        assertEquals(l, l.withInterval(0))
    }

    @Test
    fun whatTheSteppersProduceIsAlreadyInRange() {
        val pressed = daily(11 * 60 + 45)
            .withDailyHours(1)
            .withDailyMinutes(1)
            .withSession(9)
            .withInterval(9)
        assertEquals(pressed, pressed.clamped())
    }
}

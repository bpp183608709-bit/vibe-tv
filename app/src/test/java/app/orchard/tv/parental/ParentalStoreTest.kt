package app.orchard.tv.parental

import android.content.Context
import app.orchard.tv.parental.ParentalStore.Limits
import app.orchard.tv.parental.ParentalStore.Progress
import java.time.LocalDateTime
import java.time.ZoneId
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.annotation.Config
import app.orchard.tv.BuildConfig

/**
 * The store is where a mistake costs a parent their settings or a child their limits, so what is
 * checked here is the edges: the ranges the settings page can hand over, a day that changes, and a
 * password that must not come back out.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [23])
class ParentalStoreTest {

    private val context: Context = RuntimeEnvironment.getApplication()
    private val store get() = ParentalStore(context)

    @Test
    fun aFreshInstallHasNoLimits() {
        assertEquals(Limits(dailyMinutes = 0, sessionMinutes = 0, intervalMinutes = 10), store.limits())
        assertNull(store.progress())
        assertFalse(store.hasPin())
    }

    @Test
    fun everyLimitIsKept() {
        val s = store
        s.saveLimits(Limits(dailyMinutes = 2 * 60 + 15, sessionMinutes = 45, intervalMinutes = 30))
        assertEquals(Limits(135, 45, 30), s.limits())
    }

    @Test
    fun valuesFromOutsideTheSteppersAreMadeToFit() {
        val s = store
        s.saveLimits(Limits(dailyMinutes = -5, sessionMinutes = 999, intervalMinutes = 0))
        assertEquals(Limits(dailyMinutes = 0, sessionMinutes = 60, intervalMinutes = 10), s.limits())

        s.saveLimits(Limits(dailyMinutes = 99 * 60, sessionMinutes = 5, intervalMinutes = 999))
        assertEquals(Limits(dailyMinutes = 12 * 60, sessionMinutes = 5, intervalMinutes = 60), s.limits())
    }

    @Test
    fun countersSurviveARestart() {
        val day = ParentalStore.dayKey(System.currentTimeMillis())
        val saved = Progress(
            day = day,
            dailyUsedMs = 95 * 60_000L,
            dailyExtraMs = 30 * 60_000L,
            unrestricted = true,
            sessionUsedMs = 12 * 60_000L,
            state = WatchTimer.State.Interval,
            intervalEndsWallMs = 1_780_000_000_000L,
        )
        store.saveProgress(saved)
        assertEquals(saved, store.progress())
    }

    @Test
    fun aCountdownIsSavedOnlyWhileThereIsOne() {
        val day = ParentalStore.dayKey(System.currentTimeMillis())
        val saved = Progress(day = day, state = WatchTimer.State.Interval, intervalEndsWallMs = 0L)
        store.saveProgress(saved)
        assertEquals(0L, store.progress()?.intervalEndsWallMs)

        store.saveProgress(saved.copy(state = WatchTimer.State.Normal, intervalEndsWallMs = 42L))
        assertEquals(42L, store.progress()?.intervalEndsWallMs)
    }

    @Test
    fun clearingLeavesNothingBehind() {
        store.saveProgress(Progress(day = ParentalStore.dayKey(System.currentTimeMillis()), dailyUsedMs = 1_000L))
        store.clearProgress()
        assertNull(store.progress())
        // The limits and the password are not progress: clearing it must not touch them.
        store.saveLimits(Limits(dailyMinutes = 60))
        store.setPin("12345678")
        store.clearProgress()
        assertEquals(60, store.limits().dailyMinutes)
        assertTrue(store.hasPin())
    }

    @Test
    fun theDayChangesAtFourInTheMorning() {
        val zone = ZoneId.of("Asia/Shanghai")
        // Half past three is still the evening before: a film that started at eleven is not cut in
        // two by the date, and nobody gets a fresh budget at midnight.
        val before = LocalDateTime.of(2026, 10, 30, 3, 59, 59).atZone(zone).toInstant().toEpochMilli()
        val after = LocalDateTime.of(2026, 10, 30, 4, 0, 1).atZone(zone).toInstant().toEpochMilli()
        assertEquals("2026-10-29", ParentalStore.dayKey(before, zone))
        assertEquals("2026-10-30", ParentalStore.dayKey(after, zone))

        // Everything on the earlier evening belongs to that day, right up to the four o'clock edge.
        val lateEvening = LocalDateTime.of(2026, 10, 29, 23, 59, 59).atZone(zone).toInstant().toEpochMilli()
        val midnight = LocalDateTime.of(2026, 10, 30, 0, 0, 1).atZone(zone).toInstant().toEpochMilli()
        assertEquals("2026-10-29", ParentalStore.dayKey(lateEvening, zone))
        assertEquals("2026-10-29", ParentalStore.dayKey(midnight, zone))
    }

    @Test
    fun theRolloverStaysAtFourAcrossADaylightSavingChange() {
        // Berlin springs forward at 02:00 on 2026-03-29. The day must still turn at four on the wall
        // clock, not at five: taking four hours off the instant instead of the civil time moved the
        // roll-over by the summer-time offset, which is the bug this test exists for.
        val zone = ZoneId.of("Europe/Berlin")
        val justBefore = LocalDateTime.of(2026, 3, 29, 3, 59).atZone(zone).toInstant().toEpochMilli()
        val justAfter = LocalDateTime.of(2026, 3, 29, 4, 1).atZone(zone).toInstant().toEpochMilli()
        assertEquals("2026-03-28", ParentalStore.dayKey(justBefore, zone))
        assertEquals("2026-03-29", ParentalStore.dayKey(justAfter, zone))
    }

    @Test
    fun thePasswordNeverComesBackOut() {
        val s = store
        s.setPin("61803398")
        assertTrue(s.hasPin())
        assertTrue(s.verifyPin("61803398"))
        assertFalse(s.verifyPin("61803399"))
        assertFalse(s.verifyPin(""))
        // A shorter entry is a different password, not a prefix match.
        assertFalse(s.verifyPin("6180339"))
        assertFalse(s.verifyPin("000000000"))
        s.clearPin()
        assertFalse(s.hasPin())
        assertFalse(s.verifyPin("61803398"))
    }

    @Test
    fun theRecoveryPhraseIsCheckedAgainstItsHash() {
        val s = store
        // The recovery phrase is a household secret injected at build time from
        // local/admin.properties (gitignored). A checkout without that file has no
        // admin path at all. Supply AERIAL_ADMIN_PHRASE locally to exercise the
        // positive branch without ever putting the phrase in the source tree.
        val phrase = System.getenv("AERIAL_ADMIN_PHRASE")
        if (phrase.isNullOrEmpty() || BuildConfig.ADMIN_HASH.isEmpty()) {
            assertFalse(s.verifyAdmin(""))
            assertFalse(s.verifyAdmin("000000000"))
            return
        }
        assertTrue(s.verifyAdmin(phrase))
        assertFalse(s.verifyAdmin(phrase + " "))
        assertFalse(s.verifyAdmin(""))
        assertFalse(s.verifyAdmin("000000000"))
    }

    @Test
    fun theRecoveryPhraseCanBeTypedOnTheRemote() {
        // It is entered on the same pad as the password, which only types digits, so a phrase with
        // letters or an underscore could never be typed in (docs/PARENTAL.md, 四处要定 1). Eight to
        // twelve is the pad's range; those two numbers live in the parental sheet.
        val phrase = System.getenv("AERIAL_ADMIN_PHRASE") ?: "000000000"
        assertTrue(phrase.all { it in '0'..'9' })
        assertTrue(phrase.length in 8..12)
    }

    @Test
    fun aCountdownKeepsItsRemainingTimeAcrossARestart() {
        // 10 minutes left when the process goes away at wall 1_000_000 on elapsed 50_000.
        val endsAt = 50_000L + 10 * 60_000L
        val endsWall = deadlineOnWall(endsAt, nowElapsed = 50_000L, nowWall = 1_000_000L)

        // Back after 4 minutes of standby: same wall clock, elapsedRealtime restarted at boot.
        val afterReboot = deadlineOnElapsed(endsWall, nowElapsed = 20_000L, nowWall = 1_000_000L + 4 * 60_000L)
        assertEquals(20_000L + 6 * 60_000L, afterReboot)
    }

    @Test
    fun aDeadlineAlreadyPastReadsAsPast() {
        val endsWall = deadlineOnWall(100_000L, nowElapsed = 100_000L, nowWall = 1_000_000L)
        val restored = deadlineOnElapsed(endsWall, nowElapsed = 0L, nowWall = 1_000_000L + 60_000L)
        assertTrue(restored < 0L)
    }
}

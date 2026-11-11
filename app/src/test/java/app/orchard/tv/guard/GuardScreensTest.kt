package app.orchard.tv.guard

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * Which stock-launcher windows the guard takes back. The boot cover matters beyond a cold boot:
 * MiTV-MFTP0 showed it again at every wake, with 儿童模式 behind it (2026-11-01, documented in
 * [GuardScreens]).
 */
class GuardScreensTest {

    private val homes = mapOf(
        GuardScreens.PATCHWALL to setOf("com.mitv.tvhome.MainActivityUserMode"),
        "com.google.android.tvlauncher" to setOf("com.google.android.tvlauncher.MainActivity"),
    )

    @Test
    fun theHomeActivityOfALauncherIsTakenBack() {
        assertTrue(GuardScreens.isLauncherFront(GuardScreens.PATCHWALL, "com.mitv.tvhome.MainActivityUserMode", homes))
        assertTrue(
            GuardScreens.isLauncherFront(
                "com.google.android.tvlauncher", "com.google.android.tvlauncher.MainActivity", homes,
            ),
        )
    }

    @Test
    fun patchwallsBootCoverIsTakenBackAtAWakeToo() {
        // Not a home activity, and shown again long after a boot: the guard used to take it only in
        // the first minutes after one, which is why a wake left 儿童模式 in front.
        assertTrue(GuardScreens.isLauncherFront(GuardScreens.PATCHWALL, GuardScreens.PATCHWALL_BOOT, homes))
    }

    @Test
    fun anotherLaunchersBootCoverIsNotGuessedAt() {
        assertFalse(GuardScreens.isLauncherFront("com.google.android.tvlauncher", "com.google.android.tvlauncher.Boot", homes))
    }

    @Test
    fun akidsDeskScreenOfTheSamePackageIsNotTreatedAsTheLauncherItself() {
        // Aerial opens 儿童模式 from its own tile; the visit stays until Aerial is back (launchedGuest),
        // so the rest of the package's screens are not taken over by name.
        assertFalse(GuardScreens.isLauncherFront(GuardScreens.PATCHWALL, "com.mitv.tvhome.business.kiddesk.KidsHomeActivity", homes))
        assertFalse(GuardScreens.isLauncherFront(GuardScreens.PATCHWALL, "com.mitv.tvhome.business.kidcontrols.LockControlsActivity", homes))
    }

    @Test
    fun aerialAndPlainAppsAreNeverTakenBackByName() {
        assertFalse(GuardScreens.isLauncherFront("app.orchard.tv", "app.orchard.tv.MainActivity", homes))
        assertFalse(GuardScreens.isLauncherFront("com.ktcp.video", "com.ktcp.video.MainActivity", homes))
    }

    @Test
    fun aWindowWithoutAClassSaysNothing() {
        assertFalse(GuardScreens.isLauncherFront(GuardScreens.PATCHWALL, null, homes))
    }
}

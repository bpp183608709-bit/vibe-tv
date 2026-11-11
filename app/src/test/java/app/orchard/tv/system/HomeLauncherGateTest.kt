package app.orchard.tv.system

import android.content.ComponentName
import android.content.Intent
import android.content.IntentFilter
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.RuntimeEnvironment
import org.robolectric.Shadows.shadowOf

/**
 * The maker's own desktop asks for the parental password, like the store does: its inputs, its app
 * manager and the paid channels are one press apart (docs/PARENTAL.md, 商店与应用管理).
 *
 * Read from the package manager, not from the guard's own list: the guard fills that list when its
 * service connects, and the password has to be asked with it stopped as well.
 */
@RunWith(RobolectricTestRunner::class)
class HomeLauncherGateTest {
    private val context = RuntimeEnvironment.getApplication()

    /** A package that declares itself a Home, as PatchWall does. */
    private fun registerHome(pkg: String) {
        val component = ComponentName(pkg, "$pkg.MainActivity")
        val shadows = shadowOf(context.packageManager)
        shadows.addActivityIfNotPresent(component)
        shadows.addIntentFilterForActivity(
            component,
            IntentFilter(Intent.ACTION_MAIN).apply { addCategory(Intent.CATEGORY_HOME) },
        )
    }

    @Test fun theStockDesktopIsGated() {
        // On MiTV-MFTP0: 桌面 in the grid, PatchWall behind it.
        registerHome("com.mitv.tvhome")
        assertTrue(SystemProbe.isHomeLauncher(context, "com.mitv.tvhome"))
    }

    @Test fun anAppThatIsNoHomeIsNotGated() {
        registerHome("com.mitv.tvhome")
        assertFalse(SystemProbe.isHomeLauncher(context, "com.duokan.videodaily"))
        assertFalse(SystemProbe.isHomeLauncher(context, "com.dangbeimarket"))
    }

    @Test fun aerialItselfIsNeverGated() {
        // Its own Home filter is how the guard finds it; asking for the password on the way back
        // would lock the viewer out of the desktop itself.
        assertFalse(SystemProbe.isHomeLauncher(context, context.packageName))
    }
}

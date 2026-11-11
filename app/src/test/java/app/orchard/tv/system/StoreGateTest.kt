package app.orchard.tv.system

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner

/**
 * Which apps ask for the parental password: anything that installs apps. A store left out is not a
 * cosmetic bug — 当贝市场 was, and one press in it installs a video app the daily limit then has to
 * chase (docs/PARENTAL.md, 商店与应用管理).
 *
 * Robolectric because SystemProbe's initializer reads the device profile, which needs a Context.
 */
@RunWith(RobolectricTestRunner::class)
class StoreGateTest {
    @Test fun theMakerStoresAreGated() {
        assertTrue(SystemProbe.isStoreOrManager("com.xiaomi.mitv.appstore"))
        assertTrue(SystemProbe.isStoreOrManager("com.xiaomi.mitv.tvmanager"))
        assertTrue(SystemProbe.isStoreOrManager("com.xiaomi.mitv.shop"))
        assertTrue(SystemProbe.isStoreOrManager("com.mitv.shoplugin"))
    }

    @Test fun storesTheMakerDidNotShipAreGatedToo() {
        // On MiTV-MFTP0, sideloaded: it installs any APK with one press.
        assertTrue(SystemProbe.isStoreOrManager("com.dangbeimarket"))
        // 沙发管家 and the like, by name rather than by list.
        assertTrue(SystemProbe.isStoreOrManager("com.shafa.market"))
        assertTrue(SystemProbe.isStoreOrManager("com.someone.appstore"))
    }

    @Test fun playersAreNotGated() {
        // These install nothing; asking for the password on a video app would make the limit
        // unusable. com.gitvdemo.video does declare REQUEST_INSTALL_PACKAGES (its own updater),
        // which is why the permission is not the test.
        assertFalse(SystemProbe.isStoreOrManager("com.duokan.videodaily"))
        assertFalse(SystemProbe.isStoreOrManager("com.gitvdemo.video"))
        assertFalse(SystemProbe.isStoreOrManager("com.tencent.karaoketv"))
        assertFalse(SystemProbe.isStoreOrManager("com.hpplay.happyplay.aw"))
        assertFalse(SystemProbe.isStoreOrManager("app.orchard.tv"))
    }
}

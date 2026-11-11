package app.orchard.tv

import android.app.Application
import app.orchard.tv.guard.GuardSetup
import app.orchard.tv.guard.HomeGuardService
import app.orchard.tv.guard.HomeLock
import app.orchard.tv.parental.ParentalClock

/**
 * What every start of Aerial's process does, whichever component started it.
 *
 * Xiaomi kills Aerial in standby and starts it again (the sync adapter, the notification listener)
 * while the screen is still dark. Nothing then answers the screen coming on until the guard has
 * been bound again, so the TV stayed on whatever the firmware restored: on MiTV-MFTP0 (2026-11-01)
 * the screen lit at 12:57:46 on PatchWall's boot cover, 儿童模式 followed, and Aerial only came
 * back at 12:57:59 when the sync framework revived the guard — 13 s. Arming the screen-on watch
 * here puts the guard back within a second of the light.
 *
 * The same start is when the precise tier starts learning whether it can be used: tools/install.sh
 * enables the notification listener, but Xiaomi rewrites secure settings around standby, and without
 * the listener the parental module counts by the coarse signal instead (see SessionVerdict).
 */
class AerialApp : Application() {

    override fun onCreate() {
        super.onCreate()
        // The clock belongs to the process: a window's composition stops as soon as a video app is
        // in front, which is when the limit has to count.
        runCatching { ParentalClock.start(this) }
        if (!HomeGuardService.isRunning) runCatching { GuardSetup.watchForScreenOn(this) }
        // A no-op unless a TV owner made Aerial the device owner over ADB: then Home is answered by
        // Aerial itself and the guard has nothing left to take back (features/HomeLock). This MiTV
        // has no DevicePolicyManagerService to set one with, so on it this never runs.
        runCatching { HomeLock.claim(this) }
    }
}

package app.orchard.tv.guard

import android.app.admin.DevicePolicyManager
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import app.orchard.tv.MainActivity
import app.orchard.tv.system.DiagLog

/**
 * Aerial as the TV's Home, held by device owner status instead of by intent-filter priority.
 *
 * The two ordinary ways of asking for Home both fail on MiTV-MFTP0: the maker's launcher declares
 * `priority=100` and the package manager ignores Aerial's own (a reinstall with
 * `priority=1000` still resolved to PatchWall, before and after a reboot), and the preferred
 * activity `cmd package set-home-activity` writes is stored with `MATCH_CATEGORY_HOME` only, which
 * the Home intent — resolved with `MATCH_DEFAULT_ONLY` — skips.
 *
 * The device owner route does not work on this firmware either, and is kept for TVs whose
 * DevicePolicyManagerService is whole: `dpm set-active-admin` reports success and
 * `dpm set-device-owner` refuses, while `dumpsys device_policy` shows only eleven lines — no
 * "Registered Device Admins", no owner — so the service is a stub here. Nothing below can run.
 *
 * What actually keeps Aerial on this TV is [HomeGuardService] alone.
 */
object HomeLock {

    private fun admin(context: Context) = ComponentName(context, AerialDeviceAdmin::class.java)

    /** Whether Aerial holds device owner status, and may write the persistent Home preference. */
    fun isOwner(context: Context): Boolean = runCatching {
        val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as? DevicePolicyManager ?: return false
        dpm.isDeviceOwnerApp(context.packageName)
    }.getOrDefault(false)

    /** Answers the Home intent with Aerial from now on, across reboots. True when it was written. */
    fun claim(context: Context): Boolean {
        if (!isOwner(context)) return false
        val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as? DevicePolicyManager ?: return false
        val filter = IntentFilter(Intent.ACTION_MAIN).apply {
            addCategory(Intent.CATEGORY_HOME)
            addCategory(Intent.CATEGORY_DEFAULT)
        }
        val home = ComponentName(context, MainActivity::class.java)
        val ok = runCatching {
            dpm.addPersistentPreferredActivity(admin(context), filter, home)
            true
        }.getOrDefault(false)
        DiagLog.i("home lock: device owner=true, persistent preferred written=$ok")
        return ok
    }

    /** Gives Home back to the firmware; the device owner status itself stays. */
    fun release(context: Context): Boolean = runCatching {
        val dpm = context.getSystemService(Context.DEVICE_POLICY_SERVICE) as? DevicePolicyManager ?: return false
        if (!isOwner(context)) return false
        dpm.clearPackagePersistentPreferredActivities(admin(context), context.packageName)
        DiagLog.i("home lock: persistent preferred cleared")
        true
    }.getOrDefault(false)
}

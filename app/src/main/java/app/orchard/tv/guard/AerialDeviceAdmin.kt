package app.orchard.tv.guard

import android.app.admin.DeviceAdminReceiver

/**
 * The component `dpm set-device-owner` binds, so that Aerial can hold the TV's Home for itself.
 *
 * Nothing in the app uses the admin powers: the only thing the device owner status buys here is
 * [android.app.admin.DevicePolicyManager.addPersistentPreferredActivity], which answers the Home
 * intent with Aerial even on a firmware that ignores intent-filter priority and its own preferred
 * activity list — both of which this MiTV does (see docs/PARENTAL.md, 原机桌面).
 *
 * A device owner can only be set on a TV with no accounts on it, and stays set until it is removed
 * with `dpm remove-active-admin` or the app is uninstalled by its owner; the receiver itself does
 * nothing at runtime.
 */
class AerialDeviceAdmin : DeviceAdminReceiver()

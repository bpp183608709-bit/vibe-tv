package app.orchard.tv.guard

/**
 * Which windows of a stock launcher count as "the launcher itself is in front", i.e. the ones the
 * guard takes back. Pure, so the rule can be read and tested without a TV.
 *
 * Two kinds:
 *  - **The home activity** — the screen the Home key opens.
 *  - **PatchWall's boot cover** ([PATCHWALL_BOOT]) — Xiaomi shows it over Aerial not only at a cold
 *    boot but again at every wake from standby, and the TV wakes in whatever user mode it went to
 *    standby in. Measured on MiTV-MFTP0 (Android 9, 2026-11-01): screen lit 12:57:46 on
 *    `BootActivity`, kids desk (`MainActivityUserMode`, which *is* PatchWall's home activity)
 *    12:57:51, Aerial only at 12:57:59 — and the 13 s in between were 儿童模式.
 *
 * The package's other screens stay: they open from its home, which is either the guest Aerial
 * started ([HomeGuardService.launchAllow], `launchedGuest`) or one the guard already took back.
 */
internal object GuardScreens {

    /** Xiaomi PatchWall, the only launcher whose boot cover the guard knows by name. */
    const val PATCHWALL = "com.mitv.tvhome"

    /**
     * PatchWall's boot cover: comes up over Aerial some 20 s into a boot (MiTV4 pulpfiction,
     * Android 6.0.1) and again at every wake (MiTV-MFTP0). Not a home activity, so it is named.
     */
    const val PATCHWALL_BOOT = "com.mitv.tvhome.BootActivity"

    /**
     * Whether [pkg]/[cls] is a stock launcher screen the guard takes back, given the launchers and
     * their home activities ([homes], the guard's `stockHomes`).
     */
    fun isLauncherFront(pkg: String, cls: String?, homes: Map<String, Set<String>>): Boolean {
        val activity = cls ?: return false
        if (pkg !in homes) return false
        if (activity in homes[pkg].orEmpty()) return true
        return pkg == PATCHWALL && activity == PATCHWALL_BOOT
    }
}

package app.orchard.tv.parental

import android.content.ComponentName
import android.content.Context
import android.media.session.MediaSessionManager
import android.media.session.PlaybackState
import android.os.PowerManager
import android.os.SystemClock
import android.app.usage.UsageEvents
import android.app.usage.UsageStatsManager
import androidx.core.content.getSystemService
import app.orchard.tv.guard.RevivalListener

/**
 * Whether something is being watched right now, from the three signals of docs/PARENTAL.md (计时):
 *
 *  1. **Precise** — a media session is in `STATE_PLAYING`. Aerial's install script enables
 *     [RevivalListener], which is the notification listener `getActiveSessions` wants. It answers
 *     precisely only where the apps publish sessions; on the TVs here they do not, and an empty
 *     list falls through to the coarser tiers ([SessionVerdict]).
 *  2. **The foreground app** — a known video app is in front. Only used when no session can be
 *     read.
 *  3. **Roughly** — the screen is on and the app in front is neither Aerial, a launcher nor
 *     settings. Its error leans towards counting too much, which is the safe direction for a
 *     limit.
 *
 * The decisions are in [resolve] over a [Source] rather than in Android calls, so every branch can
 * be read and tested without a TV. [AndroidSource] is the only part that touches the system.
 *
 * Deliberate choices:
 *
 *  - **A readable session is trusted even when nothing is playing — once this device has shown it
 *    actually publishes sessions.** That is what makes pausing a video for five minutes end the
 *    session (WatchTimer's SESSION_GAP_MS) instead of counting the pause. An *empty* list is not
 *    trusted: see [SessionVerdict]. When no list can be read, the coarser signals take over, and a
 *    paused video app then still counts: over-counting is the known, accepted cost (spec, 累加的
 *    细节).
 *  - **Nothing playing and nothing known counts as nothing watched.** Guessing "the screen is on,
 *    so it is being watched" would lock a child out for sitting on the launcher, which is worse
 *    than a limit that misses.
 *  - **Aerial never enables the notification listener itself.** `tools/install.sh` does, and the
 *    grant can be missing (Xiaomi rewrites secure settings around standby: `enabled_notification_
 *    listeners` was null on MiTV-MFTP0, 2026-11-01). With the listener enabled, `getActiveSessions`
 *    *answers* — with an empty list, since the TV's video apps publish no media session — so before
 *    [SessionVerdict] an enabled listener turned every reading into "paused" (measured: 120 s of
 *    云视听极光 in front counted 0; with the listener off, the same 60 s counted 60217 ms). The
 *    verdict now reads that empty list as "unknown" on such a TV, which makes the grant harmless
 *    instead of fatal; it is still not Aerial's to hand out, because enabling it changes what this
 *    module can honestly claim on a device that *does* publish sessions.
 */
class PlaybackSignal(
    private val source: Source,
    /** How often the system is asked. A tick is about a second; an IPC every second is not worth it. */
    private val sampleMs: Long = 2_000L,
) {

    /** Which of the three a [Reading] came from, coarsest last. The settings page shows it. */
    enum class Level {
        Precise,
        Foreground,
        Usage,

        /**
         * None of them: 严格模式 is on and the counters follow [WatchTicket] instead of the screen.
         * A parent reading 统计方式 needs to see that difference — the same minute is counted with a
         * video playing or with the app's home screen up.
         */
        Ticket,
    }

    /** What the counters are told: whether something is playing, and how exactly that is known. */
    data class Reading(val watching: Boolean, val level: Level)

    /** What the device can say, with `null` where it cannot say anything. */
    interface Source {
        /** Aerial's own package: its wallpaper and screensaver never count. */
        fun ownPackage(): String

        /**
         * True/false when the session list can be read and says something; null when it cannot be
         * read (no listener permission), or when it is readable but empty on a device that has
         * never reported a session — see [SessionVerdict].
         */
        fun mediaPlaying(): Boolean?

        /** The package in front, or null when it is not known (no usage stats). */
        fun foreground(): String?

        /** Screen on and not in standby. */
        fun screenOn(): Boolean
    }

    private var sampledAt = Long.MIN_VALUE
    private var sampled = Reading(false, Level.Usage)

    /** The last reading, for the settings page's 统计方式 line. */
    val last: Reading get() = sampled

    /**
     * Whether the screen is on. The caller needs it separately under 严格模式, where the reading is
     * replaced by [WatchTicket] but a dark screen still counts as nothing watched.
     */
    fun screenOn(): Boolean = source.screenOn()

    /** The package the last [resolve] saw in front, for the diagnostic log. Null when unknown. */
    @Volatile
    var frontSeen: String? = null
        private set

    /** One read, at most every [sampleMs] of [nowElapsed]. */
    fun read(nowElapsed: Long = SystemClock.elapsedRealtime()): Reading {
        val fresh = sampledAt != Long.MIN_VALUE && nowElapsed - sampledAt in 0 until sampleMs
        if (!fresh) {
            sampled = resolve()
            sampledAt = nowElapsed
        }
        return sampled
    }

    /** The three signals in order, with the level saying which one answered. */
    fun resolve(): Reading {
        val playing = source.mediaPlaying()
        val front = source.foreground()
        val on = source.screenOn()
        frontSeen = front
        val level = when {
            playing != null -> Level.Precise
            front != null && front in VIDEO_APPS -> Level.Foreground
            else -> Level.Usage
        }
        val watching = when {
            // Standby and a dark screen are excluded whatever else says otherwise (spec, 必须排除的).
            !on -> false
            front == source.ownPackage() -> false
            playing != null -> playing
            front == null -> false
            front in QUIET -> false
            // Level 3: on a lit screen, anything that is not a launcher, settings, or Aerial counts.
            else -> true
        }
        return Reading(watching, level)
    }

    companion object {
        /**
         * Apps that play video, checked against the TVs this project is built for: the same list
         * `apps/LayoutStore.kt` puts first on a fresh Home (its streaming half). A quarter of
         * streaming apps publish no media session on some firmwares, which is what this level is
         * for. Extend it from the package a real TV reports in `DiagLog`, not from a guess.
         */
        private val VIDEO_APPS = setOf(
            "com.ktcp.video",                        // 云视听极光
            "com.gitvdemo.video",                    // 银河奇异果
            "com.cibn.tv",                           // CIBN 酷喵
            "com.hunantv.license",                   // 芒果 TV
            "com.xiaodianshi.tv.yst",                // 云视听小电视
            "com.mitv.mivideoplayer",                // 小米视频
            "com.mitv.videoplayer",                  // 小米电视播放器（本地 U 盘视频）
            "com.quark.yun.tv",                      // 夸克网盘
            "com.netflix.ninja",
            "com.google.android.youtube.tv",
            "com.apple.atve.androidtv.appletv",
            "com.disney.disneyplus",
            "com.amazon.amazonvideo.livingroom",
            "com.hbo.hbonow",
        )

        /**
         * Where watching cannot be happening even on a lit screen: the launchers and the settings
         * apps, Aerial itself (its own package is asked of the source), and the system UI, which
         * draws over whatever else is there.
         *
         * Xiaomi's 儿童模式 is no separate package: its desk *is* PatchWall's home activity
         * (`com.mitv.tvhome.MainActivityUserMode`, measured on MiTV-MFTP0), so at this tier it reads
         * as "not watching". Its videos are counted only where the app playing them publishes a
         * session: 云视听极光 does not on this TV (see [SessionVerdict]), so they are missed — a miss
         * in the safe direction, not something the coarse tiers can recover.
         */
        private val QUIET = setOf(
            "com.mitv.tvhome",                       // 小米 PatchWall
            "com.google.android.tvlauncher",
            "com.google.android.apps.tv.launcherx",
            "com.dangbei.tvlauncher",                // 当贝桌面
            "com.xiaomi.mitv.settings",
            "com.android.tv.settings",
            "com.android.settings",
            "com.xiaomi.mitv.tvmanager",
            "com.android.systemui",
        )

        /** The signal as this device can give it. */
        fun of(context: Context): PlaybackSignal = PlaybackSignal(AndroidSource(context.applicationContext))

        /**
         * Whether [pkg] is one of the video apps — the ones 严格模式 puts behind a ticket. Checked
         * against the same list level 2 uses, because a package missing here would be a way around
         * the ticket; extend it from a real TV's `DiagLog`, not from a guess.
         */
        fun isVideoApp(pkg: String): Boolean = pkg in VIDEO_APPS
    }
}

/**
 * How a session list is read — and specifically how an *empty* one is.
 *
 * `getActiveSessions` answering with nothing is ambiguous on these TVs: either nothing is playing,
 * or the app in front publishes no media session at all. On MiTV-MFTP0 the second is what happens —
 * with the notification listener enabled, 120 s of 云视听极光 in front counted 0, because every tick
 * read the empty list as "paused" (2026-11-01). That is not something a different system call
 * fixes: on such a device the list is not evidence either way.
 *
 * So an empty list is believed only after this device answered with a session at least once. That
 * first non-empty answer proves the apps here do publish sessions, which makes every later empty
 * list a real "nothing is playing": the precise tier keeps working and a five-minute pause still
 * ends the session. Until then — and whenever the list cannot be read at all — the answer is null,
 * "unknown", and the coarser signals decide.
 *
 * The proof is per process on purpose: a TV that starts publishing sessions after a firmware update
 * gets the precise tier on the next start with no migration, while one that never does is never
 * trusted in the direction that silently stops counting.
 */
internal class SessionVerdict {

    /** True once this device has reported a session. Not persisted; see the class comment. */
    @Volatile
    private var answered = false

    /**
     * @param playing One entry per active session, Aerial's own already excluded: whether it is
     *                playing. Empty when the system listed no session. An *unreadable* list is not
     *                passed here — that is the caller's `runCatching`, which answers null for the
     *                same reason.
     */
    fun of(playing: List<Boolean>): Boolean? {
        if (playing.isNotEmpty()) {
            answered = true
            return playing.any { it }
        }
        return if (answered) false else null
    }
}

/** [PlaybackSignal.Source] over the real system: sessions, usage stats, and the screen. */
private class AndroidSource(private val context: Context) : PlaybackSignal.Source {

    private val sessions = context.getSystemService<MediaSessionManager>()

    /** Whether an empty session list may be read as "nothing is playing" on this device. */
    private val verdict = SessionVerdict()

    /** The listener whose approval `getActiveSessions` checks; see RevivalListener. */
    private val listener = ComponentName(context, RevivalListener::class.java)

    override fun ownPackage(): String = context.packageName

    override fun mediaPlaying(): Boolean? {
        val manager = sessions ?: return null
        // A SecurityException here means the notification listener is not enabled on this TV: the
        // signal simply is not available, which is not an error worth logging every tick.
        return runCatching {
            verdict.of(
                manager.getActiveSessions(listener)
                    // Aerial's own films (the wallpaper, the screensaver) are never counted.
                    .filter { it.packageName != context.packageName }
                    .map { it.playbackState?.state == PlaybackState.STATE_PLAYING },
            )
        }.getOrNull()
    }

    override fun foreground(): String? = runCatching {
        val manager = context.getSystemService<UsageStatsManager>() ?: return null
        val now = System.currentTimeMillis()
        val events = manager.queryEvents(now - LOOKBACK_MS, now)
        var last: String? = null
        val event = UsageEvents.Event()
        while (events.hasNextEvent()) {
            events.getNextEvent(event)
            @Suppress("DEPRECATION")
            // ACTIVITY_RESUMED is the same value on newer versions; this one still arrives on the
            // Android 8–11 TVs here and is what HomeGuardService reads for the same reason.
            if (event.eventType == UsageEvents.Event.MOVE_TO_FOREGROUND) last = event.packageName
        }
        // Kept: usage stats record *moves*, so a video watched for longer than the lookback leaves
        // no event at all, and answering null then said "nothing is playing" — the limit stopped
        // counting after half a minute of every video, which is the one case it exists for
        // (MiTV-MFTP0, 2026-11-01: 100 s of 云视听极光 in front, nothing counted).
        if (last != null) lastFront = last
        lastFront
    }.getOrNull()

    /** The last package usage stats named as being in front; null until they name one. */
    @Volatile
    private var lastFront: String? = null

    override fun screenOn(): Boolean =
        context.getSystemService<PowerManager>()?.isInteractive == true

    private companion object {
        /** Long enough to see the move that put the current app in front, short enough to stay cheap. */
        const val LOOKBACK_MS = 30_000L
    }
}

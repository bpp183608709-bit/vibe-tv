package app.orchard.tv.parental

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import android.util.Log
import app.orchard.tv.LockActivity
import app.orchard.tv.guard.GuardSetup
import app.orchard.tv.guard.HomeGuardService

/**
 * The parental clock: one pass a second, for as long as Aerial's process lives.
 *
 * It used to be a `LaunchedEffect` in Home's composition, which stops as soon as the window is not
 * visible — Compose resumes delayed work on the next frame, and a background window raises none. So
 * the module counted nothing at all while a video app was in front, which is the only time it is
 * meant to count. Measured on MiTV-MFTP0 (2026-11-01): 50 s of 云视听极光 in front, `dailyUsed` 0.
 * A Handler loop belongs to the process, not to a window, so the counting survives Aerial being
 * behind the app it is limiting.
 *
 * Started from [app.orchard.tv.AerialApp] (every start of the process) and again from Home, which
 * is idempotent: two clocks a second apart would count every second twice.
 */
object ParentalClock {

    private const val TAG = "AerialParental"
    private const val TICK_MS = 1_000L

    private val handler = Handler(Looper.getMainLooper())

    private var signal: PlaybackSignal? = null
    private var app: Context? = null

    /** The level the last pass read, for the settings page's 统计方式 line. */
    @Volatile
    var level: PlaybackSignal.Level? = null
        private set

    /** Passes run since this process started: what `--ez parental_state` reports as `ticks`. */
    @Volatile
    var ticks = 0L
        private set

    private var lastWatching: Boolean? = null
    private var lastLevel: PlaybackSignal.Level? = null

    private val tick = object : Runnable {
        override fun run() {
            handler.postDelayed(this, TICK_MS)
            val context = app ?: return
            val signal = signal ?: return
            val reading = signal.read()
            val now = SystemClock.elapsedRealtime()
            ticks++
            // 严格模式 replaces the reading with the ticket: the time is the parent's to hand out, so
            // a ticket in hand counts whether or not a video is playing (docs/PARENTAL.md, 严格模式).
            // A dark screen is still nothing watched — standby is nobody's watching.
            val strict = ParentalStore.strictWatch(context)
            val watching = if (strict) {
                signal.screenOn() && WatchTicket.counting(signal.frontSeen, now)
            } else {
                reading.watching
            }
            level = if (strict) PlaybackSignal.Level.Ticket else reading.level
            Parental.tick(watching)
            // Logged on every change of the reading, and every ten passes besides: the raw count
            // (`used`) is unrounded, so "is this app being counted" can be answered from the log
            // alone instead of from a screenshot of the TV.
            val changed = watching != lastWatching || level != lastLevel
            if (changed || ticks % 10 == 0L) {
                Log.i(
                    TAG,
                    "tick $ticks watching=$watching level=$level" +
                        " front=${signal.frontSeen} used=${Parental.usedNowMs()}" +
                        " state=${Parental.ui.state}" +
                        (if (strict) " ticket=${WatchTicket.leftSec(now)}s" else "") +
                        (if (changed) "  <- changed" else ""),
                )
                lastWatching = watching
                lastLevel = level
            }
            // The moment a limit runs out the lock goes up, over this process's screen or over the
            // video app that is counting (docs/PARENTAL.md, 锁屏实现).
            if (Parental.ui.locked) LockActivity.bringUp(context)
        }
    }

    /** Starts the clock, once per process. Before the user is unlocked there are no counters to read. */
    fun start(context: Context) {
        if (signal != null) return
        val appContext = context.applicationContext ?: return
        if (!GuardSetup.isUserUnlocked(appContext)) return
        signal = PlaybackSignal.of(appContext)
        app = appContext
        runCatching { Parental.init(appContext) }
        // The guard's front check is gated on the password, which it could not see before this line
        // (Parental.hasPin is false until the store is read): ask it to start looking now.
        runCatching { HomeGuardService.onParentalReady() }
        handler.removeCallbacks(tick)
        handler.post(tick)
        Log.i(TAG, "parental clock started")
    }
}

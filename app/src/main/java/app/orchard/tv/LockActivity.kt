package app.orchard.tv

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.SystemClock
import android.provider.Settings
import android.view.KeyEvent
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.ComposeView
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import app.orchard.tv.dream.AerialDream
import app.orchard.tv.parental.Parental
import app.orchard.tv.system.DiagLog
import app.orchard.tv.system.Sounds
import app.orchard.tv.ui.components.UhdCanvas
import app.orchard.tv.ui.parental.ParentalLockScreen
import app.orchard.tv.ui.theme.LocalReducedMotion
import app.orchard.tv.ui.theme.canvasDensity
import kotlinx.coroutines.delay

/**
 * The screen a used-up limit puts in front (docs/PARENTAL.md, 界面; 锁屏实现).
 *
 * An activity of its own rather than an overlay in Home, for the reason the spec gives: an overlay
 * lives inside Aerial's window, so the HOME key and any other app's start go straight past it. This
 * one is the front-most window, and it takes Back and Menu for itself.
 *
 * It does not count the time. Home's composition is the one clock over [Parental] — the counters are
 * moved once a second by whichever tick runs, and two tickers would count every second twice (see
 * `WatchTimer.checkDeadline`). What this screen does is keep the clock's *deadline* honest while it
 * is up: an interval that runs out here ends, even if Home's window is gone, and then so does the
 * lock — the one way out that needs no password.
 *
 * `singleInstance` with `excludeFromRecents`, as the spec says: one lock, never in the recent apps.
 * Closing it opens Home, not whatever app was underneath, so the child starts over from Aerial.
 */
class LockActivity : ComponentActivity() {

    private var lettingGo = false

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        DiagLog.i("lock screen up: ${Parental.ui.state}")
        // Reading the settings and today's counters; safe to call more than once, and the guard may
        // have started this activity with nothing else of Aerial running.
        Parental.init(this)
        if (!Parental.ui.locked) {
            // The limit went away between the start and this line (an interval that ended, a parent
            // who lifted it from the settings page in the other window).
            letGo()
            return
        }
        if (!Parental.hasPin) {
            // A limit with no password on it: nobody could ever lift this screen, and the day limit
            // would hold the TV until midnight. The settings page cannot produce this (it asks for
            // the password before it shows the limits), so it is a broken store rather than a
            // choice — let go, and say so, instead of locking the television.
            DiagLog.i("a parental limit is used up but no password is set; the lock lets go")
            letGo()
            return
        }
        Sounds.init(this)
        // A screensaver would be behind the lock and come back when it closes; its film would also
        // keep decoding the whole time (spec, 锁屏实现).
        AerialDream.endForLock()
        // Over the system keyguard too, on firmware that keeps one.
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED)
        }
        hideSystemBars()
        // Back must not take the lock away. BackHandler in the keypad goes on top of this while the
        // password is up, so Back there closes the keypad and nothing else.
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { /* the lock stays */ }
        })

        val reducedMotion = Settings.Global.getFloat(contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
        val uhd = UhdCanvas.scale(this)
        val lock: @Composable () -> Unit = {
            // The same 960×540 canvas as Home: the lock is another Aerial screen, not a dialog.
            val canvas = Density(density = canvasDensity(resources.displayMetrics), fontScale = LocalDensity.current.fontScale)
            CompositionLocalProvider(LocalReducedMotion provides reducedMotion, LocalDensity provides canvas) {
                var leftSec by remember { mutableStateOf(Parental.intervalLeftSec()) }
                LaunchedEffect(Unit) {
                    while (true) {
                        // The deadline, not the counters: see the class comment.
                        Parental.idle()
                        leftSec = Parental.intervalLeftSec()
                        if (!Parental.ui.locked) {
                            letGo()
                            break
                        }
                        delay(500L)
                    }
                }
                ParentalLockScreen(ui = Parental.ui, leftSec = leftSec)
            }
        }
        if (Build.VERSION.SDK_INT >= 29 && uhd > 1f) {
            setContentView(UhdCanvas.host(this, ComposeView(this).apply { setContent(lock) }, uhd))
        } else {
            setContent(content = lock)
        }
    }

    /**
     * Menu has nothing to open here, and Home is taken by the guard. Everything else — the arrows,
     * the number keys, OK, and Back where the dispatcher has it — belongs to the screen.
     */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean = when (event.keyCode) {
        KeyEvent.KEYCODE_MENU, KeyEvent.KEYCODE_HOME -> true
        else -> super.dispatchKeyEvent(event)
    }

    override fun onResume() {
        super.onResume()
        inFront = true
    }

    override fun onPause() {
        inFront = false
        super.onPause()
    }

    override fun onDestroy() {
        inFront = false
        DiagLog.i("lock screen down")
        super.onDestroy()
    }

    /** Closes the lock and puts Home back in front, whichever way the limit ended. */
    private fun letGo() {
        if (lettingGo) return
        lettingGo = true
        // Aerial's task, not whatever was under the lock: after a limit the child starts again from
        // Home (spec, 验收 5–7), and Home's own tick goes on from there.
        runCatching { startActivity(MainActivity.bringBackIntent(this)) }
            .onFailure { DiagLog.i("could not bring Home back after the lock: ${it.message}") }
        finish()
    }

    private fun hideSystemBars() {
        WindowCompat.setDecorFitsSystemWindows(window, false)
        WindowInsetsControllerCompat(window, window.decorView).apply {
            hide(WindowInsetsCompat.Type.systemBars())
            systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
        }
    }

    companion object {
        /**
         * Whether the lock is the window in front; main thread only, like the activity. A lock that
         * is alive but covered — the system's own Home start, a firmware overlay — has to be brought
         * back, while one already in front must not be started again every second.
         */
        private var inFront = false

        /** How often a refused start is tried again; Home asks once a second. */
        private const val RETRY_MS = 2_000L

        private var lastAskAt = 0L

        /**
         * Puts the lock in front when a limit is used up, and says whether it did something. Called
         * every second from Home's clock and by the guard on a Home press or another app coming to
         * the front: a limit reached while a video app is in front still needs a screen of its own,
         * and a lock that something covered has to come back.
         *
         * Starting the activity again is enough to bring it to the front: it is `singleInstance`, so
         * the system delivers it to the instance already running.
         */
        fun bringUp(context: Context): Boolean {
            if (inFront) return false
            if (!Parental.lockedNow()) return false
            // Nothing could lift a lock nobody has the password for: see onCreate.
            if (!Parental.hasPin) return false
            val now = SystemClock.uptimeMillis()
            if (now - lastAskAt < RETRY_MS) return false
            lastAskAt = now
            return runCatching {
                context.startActivity(Intent(context, LockActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK))
            }.onFailure {
                // A background start can be refused by Android 10 and later without the overlay
                // permission; the next pass tries again.
                DiagLog.i("could not put the lock in front: ${it.message}")
            }.isSuccess
        }
    }
}

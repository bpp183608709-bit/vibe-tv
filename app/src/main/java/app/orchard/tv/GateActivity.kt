package app.orchard.tv

import android.content.Context
import android.content.Intent
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.PowerManager
import android.os.SystemClock
import android.provider.Settings
import android.view.KeyEvent
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.OnBackPressedCallback
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.Density
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import androidx.core.content.getSystemService
import app.orchard.tv.dream.AerialDream
import app.orchard.tv.guard.HomeGuardService
import app.orchard.tv.parental.Parental
import app.orchard.tv.system.DiagLog
import app.orchard.tv.ui.home.PinMode
import app.orchard.tv.ui.home.PinPad
import app.orchard.tv.ui.parental.MAX_PIN
import app.orchard.tv.ui.parental.MIN_PIN
import app.orchard.tv.ui.theme.LocalReducedMotion
import app.orchard.tv.ui.theme.canvasDensity

/**
 * The password in front of an app that Aerial did not open itself.
 *
 * [MainActivity.launch] asks for the password on the way to a tile, which covers every start Aerial
 * makes and none of the ones it does not. The maker's recent-apps manager is the hole: a double
 * Home on PatchWall opens it, and a task restored from there comes to the front through the task
 * manager, with Aerial's code never running — a store or the stock desktop that was unlocked once
 * earlier in the day opens again with no password at all (reported on MiTV-MFTP0, 2026-11-01).
 *
 * So the guard asks as well, and from the other side: whatever is *in front* and is meant to be
 * behind the password needs a grant from this session ([HomeGuardService.unlockAllow]). The grant
 * dies the moment that app leaves the front, which is what makes a restored task ask again while a
 * parent reading a store page for a minute is not interrupted.
 *
 * Back and Home leave for Aerial, not for the app underneath: a child who cannot produce the
 * password starts over from Home, as after the limit lock.
 */
class GateActivity : ComponentActivity() {

    /** Set once the gate has done its job (opened or let go), so [onPause] stops putting it back. */
    private var done = false

    /** How many times this instance has put itself back over an app that would not stay behind it. */
    private var relists = 0

    private val handler = Handler(Looper.getMainLooper())

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        val target = intent.getStringExtra(EXTRA_PACKAGE).orEmpty()
        DiagLog.i("password gate up for $target")
        // Reading the settings and today's counters; safe to call more than once, and the guard may
        // have started this activity with nothing else of Aerial running.
        Parental.init(this)
        if (!Parental.hasPin) {
            // The password went away between the start and this line: nothing could ever lift this
            // screen, so it lets go rather than holding the television.
            DiagLog.i("password gate: no password is set; letting go")
            leave()
            return
        }
        // A screensaver would be behind the gate and come back when it closes.
        AerialDream.endForLock()
        if (Build.VERSION.SDK_INT >= 27) {
            setShowWhenLocked(true)
        } else {
            @Suppress("DEPRECATION")
            window.addFlags(WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED)
        }
        hideSystemBars()
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() = leave()
        })

        val reducedMotion = Settings.Global.getFloat(contentResolver, Settings.Global.ANIMATOR_DURATION_SCALE, 1f) == 0f
        setContent {
            val canvas = Density(density = canvasDensity(resources.displayMetrics), fontScale = LocalDensity.current.fontScale)
            CompositionLocalProvider(LocalReducedMotion provides reducedMotion, LocalDensity provides canvas) {
                Box(Modifier.fillMaxSize().background(Color(0xFF040608))) {
                    PinPad(
                        visible = true,
                        mode = PinMode.Unlock,
                        minLength = MIN_PIN,
                        maxLength = MAX_PIN,
                        allowArrows = false,
                        verify = { Parental.verifyPin(it) },
                        onDone = {
                            DiagLog.i("password gate: $target unlocked for this visit")
                            done = true
                            HomeGuardService.unlockAllow(target)
                            finish()
                        },
                        onCancel = { leave() },
                    )
                }
            }
        }
    }

    /** Nothing here opens a menu, and Home leaves rather than letting the app underneath stay. */
    override fun dispatchKeyEvent(event: KeyEvent): Boolean = when (event.keyCode) {
        KeyEvent.KEYCODE_MENU -> true
        KeyEvent.KEYCODE_HOME -> {
            leave()
            true
        }
        else -> super.dispatchKeyEvent(event)
    }

    override fun onResume() {
        super.onResume()
        inFront = true
        relists = 0
    }

    override fun onPause() {
        inFront = false
        super.onPause()
        // A video app that was still starting when the gate came up brings itself back over it: 云视听极光
        // opens its own home activity about 400 ms after the first screen (measured on MiTV-MFTP0,
        // 2026-11-03), so the gate was up and gone within the same second and the child walked in.
        // Put it back — a few times at most, because an app that keeps coming back is a fight the
        // television would lose, and the guard's next pass will ask again anyway.
        if (done || isFinishing || relists >= MAX_RELISTS) return
        val power = getSystemService<PowerManager>()
        if (power?.isInteractive == false) return
        relists++
        handler.postDelayed({ if (!done && !isFinishing) relist() }, RELIST_MS)
    }

    override fun onDestroy() {
        inFront = false
        done = true
        handler.removeCallbacksAndMessages(null)
        DiagLog.i("password gate down")
        super.onDestroy()
    }

    /** Brings this instance back to the front after something covered it; see [onPause]. */
    private fun relist() {
        runCatching {
            startActivity(
                Intent(this, GateActivity::class.java)
                    .putExtra(EXTRA_PACKAGE, intent.getStringExtra(EXTRA_PACKAGE))
                    .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK or Intent.FLAG_ACTIVITY_SINGLE_TOP),
            )
        }.onFailure { DiagLog.i("could not put the password gate back in front: ${it.message}") }
    }

    /** Closes the gate and puts Aerial back in front, whether or not the password was given. */
    private fun leave() {
        done = true
        runCatching { startActivity(MainActivity.bringBackIntent(this)) }
            .onFailure { DiagLog.i("could not bring Home back after the gate: ${it.message}") }
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
        private const val EXTRA_PACKAGE = "app.orchard.tv.extra.GATE_PACKAGE"

        /** Whether the gate is the window in front; main thread only, like the activity. */
        private var inFront = false

        /** How often a refused start is tried again; the guard looks once a second. */
        private const val RETRY_MS = 2_000L

        /** How long to wait before putting the gate back over an app that covered it; see [onPause]. */
        private const val RELIST_MS = 250L

        /**
         * How many times one gate will put itself back. More than this is a fight with the app that
         * keeps bringing itself forward, and the guard asks again on its next pass.
         */
        private const val MAX_RELISTS = 4

        private var lastAskAt = 0L

        /**
         * Puts the password in front of [pkg], which the guard found in front with no grant. Says
         * whether it did something, so the caller does not take the app back in the same pass.
         */
        fun bringUp(context: Context, pkg: String): Boolean {
            if (inFront) return false
            if (!Parental.hasPin) return false
            val now = SystemClock.uptimeMillis()
            if (now - lastAskAt < RETRY_MS) return false
            lastAskAt = now
            return runCatching {
                context.startActivity(
                    Intent(context, GateActivity::class.java)
                        .putExtra(EXTRA_PACKAGE, pkg)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK),
                )
            }.onFailure {
                // A background start can be refused by Android 10 and later without the overlay
                // permission; the next pass tries again.
                DiagLog.i("could not put the password gate in front: ${it.message}")
            }.isSuccess
        }
    }
}

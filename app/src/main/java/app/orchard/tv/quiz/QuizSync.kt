package app.orchard.tv.quiz

import android.content.Context
import android.os.Handler
import android.os.Looper
import android.os.SystemClock
import app.orchard.tv.parental.Parental
import app.orchard.tv.parental.ParentalStore
import app.orchard.tv.system.DiagLog
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean

/**
 * The television's half of the conversation with the parent page, on a timer.
 *
 * Two jobs, both one-way (docs/PARENTAL.md, 设置下发):
 *
 *  - **Pull the limits** every five minutes, by version. Nothing changed is the usual answer and
 *    costs one small body.
 *  - **Say what today looks like** every thirty minutes, when the lock goes up, and once after the
 *    04:00 rollover. Watching time is counted here and nowhere else, so this call is the only reason
 *    the parent page can show a number at all.
 *
 * Three constraints shaped this file:
 *
 *  - **It never blocks the clock.** The tick that drives it runs once a second on the main thread;
 *    the network runs on one worker, and the result is posted back before it touches [Parental],
 *    whose timer belongs to the main thread.
 *  - **A failure is not an error state.** No signal means no Wi-Fi, a television that is off, or a
 *    server that is not running. None of those should change what plays, so every failure is logged
 *    and forgotten, and the next beat tries again.
 *  - **Nothing here mints time.** The settings are the parent's; the ticket in `QuizVault` is the only
 *    thing that adds a minute.
 */
object QuizSync {

    private const val CONFIG_EVERY_MS = 5 * 60_000L
    private const val BEAT_EVERY_MS = 30 * 60_000L

    private val main = Handler(Looper.getMainLooper())
    private val busy = AtomicBoolean(false)
    private val worker = Executors.newSingleThreadExecutor { runnable ->
        Thread(runnable, "quiz-sync").apply { isDaemon = true }
    }

    // Wall-clock marks rather than elapsed ones: a beat that skipped the moment the television was
    // suspended should not fire the instant it wakes, but five minutes later, like any other.
    private var lastConfigAt = 0L
    private var lastBeatAt = 0L
    private var lastDay: String? = null

    /** Called once a second from [app.orchard.tv.parental.ParentalClock]. Cheap when nothing is due. */
    fun tick(context: Context) {
        val day = ParentalStore.dayKey(System.currentTimeMillis())
        val now = SystemClock.elapsedRealtime()
        val dayChanged = lastDay != null && lastDay != day
        lastDay = day

        val dueBeat = dayChanged || now - lastBeatAt >= BEAT_EVERY_MS
        val dueConfig = now - lastConfigAt >= CONFIG_EVERY_MS
        if (!dueBeat && !dueConfig) return

        // No server address, or a television that has never registered: nothing to talk to yet.
        if (!QuizConfig.configured) return
        if (QuizVault.deviceId(context) == null) return
        val app = context.applicationContext ?: return

        // One pass at a time. The mark is set before the work starts, so a slow network does not
        // queue five identical beats behind it.
        if (!busy.compareAndSet(false, true)) return
        if (dueBeat) lastBeatAt = now
        if (dueConfig) lastConfigAt = now
        worker.execute {
            try {
                if (dueConfig) pull(app)
                if (dueBeat) beat(app, day)
            } catch (e: QuizClient.Failed) {
                // Logged at debug weight: an offline television would otherwise fill the buffer with
                // the same line twice a minute.
                DiagLog.i("quiz: sync skipped (${e.error})")
            } catch (e: Exception) {
                DiagLog.i("quiz: sync failed (${e.javaClass.simpleName})")
            } finally {
                busy.set(false)
            }
        }
    }

    /** A beat for a moment the parent is likely to look: the lock just went up. */
    fun beatNow(context: Context) {
        val app = context.applicationContext ?: return
        if (!QuizConfig.configured) return
        if (QuizVault.deviceId(app) == null) return
        if (!busy.compareAndSet(false, true)) return
        lastBeatAt = SystemClock.elapsedRealtime()
        val day = ParentalStore.dayKey(System.currentTimeMillis())
        worker.execute {
            try {
                beat(app, day)
            } catch (e: Exception) {
                DiagLog.i("quiz: beat skipped (${e.javaClass.simpleName})")
            } finally {
                busy.set(false)
            }
        }
    }

    private fun pull(app: Context) {
        val known = QuizVault.configVersion(app)
        val remote = QuizClient.pullConfig(app, known) ?: return
        // A negative number is the server's way of saying it did not send that key; keep what we have.
        if (remote.dailyMinutes < 0 || remote.sessionMinutes < 0 || remote.intervalMinutes < 0) return

        val next = ParentalStore.Limits(
            dailyMinutes = remote.dailyMinutes,
            sessionMinutes = remote.sessionMinutes,
            intervalMinutes = remote.intervalMinutes,
        ).clamped()
        main.post {
            // Applied here, on the main thread, because the timer is driven by the one-second tick.
            // Changing limits mid-tick from a worker is the kind of race that shows up once a week as
            // "it counted a minute twice".
            if (next != Parental.ui.limits) Parental.applyLimits(next)
            QuizVault.setConfigVersion(app, remote.version)
        }
        DiagLog.i(
            "quiz: config v${remote.version} applied " +
                "daily=${next.dailyMinutes} session=${next.sessionMinutes} interval=${next.intervalMinutes}",
        )
    }

    private fun beat(app: Context, day: String) {
        val limits = Parental.ui.limits
        val beat = QuizClient.heartbeat(
            context = app,
            dayKey = day,
            usedMs = Parental.usedNowMs(),
            limits = limits,
            configVersion = QuizVault.configVersion(app),
        )
        // The settings row's label is a copy of this: a parent who unbound the television from the
        // phone should not still read 已连接 on it.
        QuizVault.setBound(app, beat.bound)
        // The cooldown is the server's, and this is the one place it gets corrected without a quiz
        // being taken: a television restarted mid-wait still shows the same numbers.
        QuizVault.setCooldown(app, beat.cooldownSeconds)
        // The server answers with the version it holds. If it is higher, the phone has changed
        // something and the next pull will bring it, so the wait for the five minutes is cancelled.
        if (beat.configVersion > QuizVault.configVersion(app)) lastConfigAt = 0L
        DiagLog.i("quiz: beat day=$day used=${Parental.usedNowMs() / 60_000}min v${beat.configVersion}")
    }
}

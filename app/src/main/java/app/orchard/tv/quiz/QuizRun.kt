package app.orchard.tv.quiz

import android.content.Context
import android.os.Handler
import android.os.Looper
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import app.orchard.tv.parental.ParentalStore
import app.orchard.tv.system.DiagLog
import java.util.concurrent.Executors

/**
 * One group of questions, from the request to the minutes (docs/PARENTAL.md, 做题换时长).
 *
 * The screens only draw [stage]; everything that can go wrong is decided here. That split is what
 * makes the feature testable without a television: the state machine has no Compose and no traffic,
 * and the two halves that do (the screen, [QuizClient]) are thin.
 *
 * Four rules from the spec are enforced here rather than in the screen:
 *
 *  - **Answers are final and in order.** Every question is answered before the group is submitted,
 *    in the order it was served, matching the `question_ids` the server recorded for the session.
 *  - **The prediction is not the verdict.** [QuizWire.predictsCorrect] colours the screen; only the
 *    server's answer, and the ticket it signs, can add a minute.
 *  - **The group has ten minutes and the child has three.** Both are checked by the caller each
 *    second ([expired], [idle]); running out closes the page rather than submitting half a group.
 *  - **"没学过" is a marker, not a skip.** The question is still answered, and the marker travels
 *    with the answer so the server can leave it out of the failure count (B7).
 *
 * Threading: calls that touch the network run on one worker thread and come back to the main thread
 * through [main], because the state is read by Compose and must change on the main thread only.
 */
class QuizRun(context: Context) {

    /** What the screen is doing. */
    sealed interface Stage {
        data object Loading : Stage

        /** A group is on its way to the server. */
        data object Submitting : Stage

        /** [question] is on screen; [feedback] is filled for the moment after an answer. */
        data class Asking(
            val index: Int,
            val question: QuizQuestion,
            val feedback: Feedback? = null,
            val unknown: Boolean = false,
            /** What was answered, so the screen can mark the wrong option the child chose. */
            val picked: String? = null,
        ) : Stage {
            /** 1-based, for the "第 2 / 5 题" line. */
            val number: Int get() = index + 1
        }

        /** The verdict is in. [awardMinutes] is what was actually added today, if anything. */
        data class Done(
            val verdict: QuizVerdict,
            val awardMinutes: Int = 0,
            val refusal: QuizTicket.Refusal? = null,
        ) : Stage

        /**
         * Nothing to draw but an explanation. `retry` decides whether 重试 is offered, and `resubmit`
         * whether that retry sends the same answers again (the submission never arrived) or starts a
         * new group — the difference between "the network dropped" and "the server said no".
         */
        data class Failed(
            val error: QuizClient.Error,
            val retry: Boolean = true,
            val resubmit: Boolean = false,
        ) : Stage
    }

    /** The instant the screen shows after an answer. */
    sealed interface Feedback {
        data object Right : Feedback
        data class Wrong(val expected: String?) : Feedback
    }

    private val app = context.applicationContext
    private val main = Handler(Looper.getMainLooper())
    private val worker = Executors.newSingleThreadExecutor { Thread(it, "quiz").apply { isDaemon = true } }

    var stage by mutableStateOf<Stage>(Stage.Loading)
        private set

    /** Today's allowance, as of the last answer the server gave. */
    var quota by mutableStateOf(QuizQuota())
        private set

    private var group: QuizGroup? = null
    private val answers = mutableListOf<QuizClient.Answer>()

    /** Wall-clock start and last key press, for the ten-minute group and the three-minute idle. */
    var startedAt: Long = 0L
        private set
    var touchedAt: Long = 0L
        private set


    fun start() {
        startedAt = System.currentTimeMillis()
        touchedAt = startedAt
        if (!QuizConfig.configured || QuizConfig.caPem(app) == null) {
            // Said once, plainly: a television built for a house with no server should not look broken.
            stage = Stage.Failed(QuizClient.Error.NotConfigured, retry = false)
            return
        }
        stage = Stage.Loading
        worker.execute {
            val day = ParentalStore.dayKey(System.currentTimeMillis())
            val result = runCatching { QuizClient.fetchGroup(app, day) }
            main.post {
                result.onSuccess { loaded ->
                    group = loaded
                    answers.clear()
                    quota = loaded.quota
                    if (loaded.questions.isEmpty()) {
                        stage = Stage.Failed(QuizClient.Error.NoQuestions, retry = false)
                    } else {
                        stage = Stage.Asking(0, loaded.questions.first())
                    }
                    DiagLog.i("quiz: group ${loaded.sessionId}, ${loaded.questions.size} questions")
                }.onFailure { error ->
                    val mapped = (error as? QuizClient.Failed)?.error ?: QuizClient.Error.Offline
                    // A refusal that names a wait is worth remembering: the lock screen counts it
                    // down, and the child's next try should not be a guess.
                    if (mapped is QuizClient.Error.Cooldown) QuizVault.setCooldown(app, mapped.seconds)
                    stage = Stage.Failed(mapped, retry = mapped != QuizClient.Error.QuotaExhausted &&
                        mapped != QuizClient.Error.AttemptsExhausted && mapped != QuizClient.Error.Revoked)
                }
            }
        }
    }

    /** The child chose an option: record it, colour the screen, and move on by themselves. */
    fun choose(label: String) {
        val current = stage as? Stage.Asking ?: return
        if (current.feedback != null) return
        touch()
        answers += QuizClient.Answer(current.question.id, label.trim(), current.unknown)
        val right = QuizWire.predictsCorrect(current.question, label)
        stage = current.copy(
            picked = label.trim(),
            feedback = if (right) Feedback.Right else Feedback.Wrong(current.question.expected),
        )
        advanceAfterFeedback()
    }

    /** The child typed a fill-in answer. An empty one is not an answer: the keypad stays up. */
    fun type(text: String) {
        if (text.isBlank()) return
        val current = stage as? Stage.Asking ?: return
        if (current.feedback != null) return
        touch()
        answers += QuizClient.Answer(current.question.id, text.trim(), current.unknown)
        val right = QuizWire.predictsCorrect(current.question, text)
        stage = current.copy(
            picked = text.trim(),
            feedback = if (right) Feedback.Right else Feedback.Wrong(current.question.expected),
        )
        advanceAfterFeedback()
    }

    /** How many questions this group has, for the "第 2 / 5 题" line. */
    fun questionCount(): Int = group?.questions?.size ?: 0

    /**
     * When another group may start, as a wall clock millisecond, or 0 when one may start now.
     *
     * Read from [QuizVault] rather than kept in this object: the lock screen and the quiz page are
     * two panes over one activity, and the number has to be the same in both.
     */
    fun cooldownUntil(): Long = QuizVault.cooldownUntil(app)

    /** 没学过 — a marker for the parent's report. The question is still answered (B7). */
    fun toggleUnknown() {
        val current = stage as? Stage.Asking ?: return
        if (current.feedback != null) return
        touch()
        stage = current.copy(unknown = !current.unknown)
    }

    fun touch() {
        touchedAt = System.currentTimeMillis()
    }

    /** Whether the group's ten minutes are up. */
    fun expired(now: Long = System.currentTimeMillis()): Boolean = QuizLimits.expired(startedAt, now)

    /** Whether the child has left the page alone for three minutes. */
    fun idle(now: Long = System.currentTimeMillis()): Boolean = QuizLimits.idle(touchedAt, now)

    private fun advanceAfterFeedback() {
        val loaded = group ?: return
        main.postDelayed({
            // The page may have been closed while the mark was fading in.
            val current = stage as? Stage.Asking ?: return@postDelayed
            val next = current.index + 1
            if (next < loaded.questions.size) {
                stage = Stage.Asking(next, loaded.questions[next])
            } else {
                submit()
            }
        }, FEEDBACK_MS)
    }

    private fun submit() {
        val loaded = group ?: return
        val device = QuizVault.deviceId(app)
        if (device == null) {
            stage = Stage.Failed(QuizClient.Error.Unpaired)
            return
        }
        stage = Stage.Submitting
        val sent = answers.toList()
        worker.execute {
            val day = ParentalStore.dayKey(System.currentTimeMillis())
            val result = runCatching { QuizClient.submit(app, loaded.sessionId, device, sent, day) }
            main.post {
                result.onSuccess { verdict ->
                    quota = quota.copy(
                        successCount = quota.successCount + if (verdict.passed) 1 else 0,
                        awardAvailable = quota.awardAvailable && !verdict.passed,
                    )
                    // The cooldown is the server's answer, not a local ten minutes: it is what the
                    // lock screen counts down, and a pass clears whatever was left.
                    QuizVault.setCooldown(app, verdict.cooldownSeconds)
                    // The minutes are added here, not on the screen: one path from a signed ticket to
                    // the counter, and it is the path [QuizVault] guards with the spent list.
                    val ticket = verdict.ticket
                    if (verdict.passed && ticket != null) {
                        when (val redeemed = QuizVault.redeem(app, ticket)) {
                            is QuizVault.Redeem.Added -> stage = Stage.Done(verdict, redeemed.minutes)
                            is QuizVault.Redeem.Refused -> stage = Stage.Done(verdict, 0, redeemed.reason)
                            QuizVault.Redeem.Unverifiable -> stage = Stage.Done(verdict, 0, QuizTicket.Refusal.BAD_SIGNATURE)
                        }
                    } else {
                        stage = Stage.Done(verdict, 0)
                    }
                }.onFailure { error ->
                    val mapped = (error as? QuizClient.Failed)?.error ?: QuizClient.Error.Offline
                    // The attempt is already counted on the server, so a retry here re-submits the
                    // same session rather than starting a new one; the server answers it idempotently.
                    stage = Stage.Failed(mapped, retry = mapped is QuizClient.Error.Offline, resubmit = true)
                }
            }
        }
    }

    /**
     * Answers the group again after a submission that never arrived. The session id and the answers
     * are unchanged, so the server sees a repeat rather than a second attempt.
     */
    fun retrySubmit() {
        if (answers.isEmpty()) {
            start()
        } else {
            submit()
        }
    }

    /** The page is going away. Nothing is written: an unfinished group is simply abandoned. */
    fun close() {
        main.removeCallbacksAndMessages(null)
        worker.shutdownNow()
    }

    companion object {
        /** Whole group: ten minutes, from the first question appearing (C4). */
        const val GROUP_LIMIT_MS = QuizLimits.GROUP_LIMIT_MS

        /** No key pressed for this long and the page goes back to the lock (C17). */
        const val IDLE_LIMIT_MS = QuizLimits.IDLE_LIMIT_MS

        /** How long the right/wrong mark stays before the next question. */
        private const val FEEDBACK_MS = 1_300L
    }
}

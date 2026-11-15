package app.orchard.tv.quiz

import org.json.JSONArray
import org.json.JSONObject

/** One answer the child may pick. `correct` arrives with the question (G2) and is used for feedback only. */
data class QuizOption(val label: String, val text: String, val correct: Boolean)

/**
 * A question as the television received it.
 *
 * `accepted` is the list of spellings the server would accept for a fill-in answer. It is here for
 * the same reason `correct` is: the child should learn whether an answer was right while the question
 * is still on screen, not three seconds later. The server's own comparison is the one that decides
 * the award; this list only decides what the screen says.
 */
data class QuizQuestion(
    val id: Int,
    val topicId: Int,
    val qtype: String,
    val stem: String,
    val angle: String,
    val difficulty: Int,
    val options: List<QuizOption>,
    val accepted: List<String>,
) {
    val isChoice: Boolean get() = qtype == "choice"

    /** The answer as the local prediction sees it: the label of the correct option, or the first accepted spelling. */
    val expected: String? get() = if (isChoice) options.firstOrNull { it.correct }?.label else accepted.firstOrNull()
}

/** What is left of today's quiz allowance, as the server counts it. */
data class QuizQuota(
    val successCount: Int = 0,
    val maxSuccess: Int = 0,
    val attemptsLeft: Int = 0,
    val awardAvailable: Boolean = false,
    val awardedMs: Long = 0L,
)

/** A served group: the session to submit against, and the questions in the order they must be answered. */
data class QuizGroup(
    val sessionId: String,
    val dayKey: String,
    val questions: List<QuizQuestion>,
    val quota: QuizQuota,
)

/**
 * One wrong answer from the group just handed in — what the result page's 错题本 draws.
 *
 * [chosen] and [correct] arrive as display strings (`B. 3/8`), already turned into something a child
 * can read, because the letters alone mean nothing once the question is off screen. [explanation] is
 * empty when the bank has none, and the page drops the line rather than printing a placeholder.
 */
data class QuizReview(
    val questionId: Int,
    val subject: String,
    val topic: String,
    val stem: String,
    val chosen: String,
    val correct: String,
    val explanation: String,
)

/** What the server decided. `ticket` is the only thing that becomes minutes, and only after [QuizTicket]. */
data class QuizVerdict(
    val score: Int,
    val total: Int,
    val passLine: Int,
    val passed: Boolean,
    val awardMs: Long,
    val ticket: String?,
    val cappedByDailyLimit: Boolean,
    /** Seconds the server will refuse another group for; 0 when there is no cooldown. */
    val cooldownSeconds: Int = 0,
    val review: List<QuizReview> = emptyList(),
)

/**
 * Reading the server's JSON, and the one prediction the television is allowed to make.
 *
 * Kept apart from the HTTP call so both halves can be tested from a string, and so that a field the
 * server renames shows up as one failing parse rather than a screen that half-works.
 */
object QuizWire {

    fun group(json: JSONObject): QuizGroup {
        val questions = mutableListOf<QuizQuestion>()
        val array = json.optJSONArray("questions") ?: JSONArray()
        for (i in 0 until array.length()) questions += question(array.getJSONObject(i))
        return QuizGroup(
            sessionId = json.getString("session_id"),
            dayKey = json.optString("day_key"),
            questions = questions,
            quota = quota(json.optJSONObject("quota")),
        )
    }

    fun question(json: JSONObject): QuizQuestion {
        val options = mutableListOf<QuizOption>()
        json.optJSONArray("options")?.let { array ->
            for (i in 0 until array.length()) {
                val o = array.getJSONObject(i)
                options += QuizOption(
                    label = o.optString("label"),
                    text = o.optString("text"),
                    correct = o.optBoolean("correct"),
                )
            }
        }
        val accepted = mutableListOf<String>()
        json.optJSONArray("accepted")?.let { array ->
            for (i in 0 until array.length()) accepted += array.getString(i)
        }
        return QuizQuestion(
            id = json.getInt("id"),
            topicId = json.optInt("topic_id"),
            qtype = json.optString("qtype"),
            stem = json.optString("stem"),
            angle = json.optString("angle"),
            difficulty = json.optInt("difficulty"),
            options = options,
            accepted = accepted,
        )
    }

    fun quota(json: JSONObject?): QuizQuota {
        if (json == null) return QuizQuota()
        return QuizQuota(
            successCount = json.optInt("success_count"),
            maxSuccess = json.optInt("max_success"),
            attemptsLeft = json.optInt("attempts_left"),
            awardAvailable = json.optBoolean("award_available", true),
            awardedMs = json.optLong("awarded_ms"),
        )
    }

    fun verdict(json: JSONObject): QuizVerdict = QuizVerdict(
        score = json.optInt("score"),
        total = json.optInt("total"),
        passLine = json.optInt("pass_line"),
        passed = json.optBoolean("passed"),
        awardMs = json.optLong("award_ms"),
        ticket = json.optString("ticket").ifEmpty { null },
        cappedByDailyLimit = json.optJSONObject("flags")?.optBoolean("capped") ?: false,
        cooldownSeconds = json.optInt("cooldown_seconds"),
        review = review(json.optJSONArray("review")),
    )

    /** The wrong answers of the group, in the order the server graded them. */
    fun review(array: JSONArray?): List<QuizReview> {
        if (array == null) return emptyList()
        val items = mutableListOf<QuizReview>()
        for (i in 0 until array.length()) {
            val o = array.getJSONObject(i)
            items += QuizReview(
                questionId = o.optInt("question_id"),
                subject = o.optString("subject"),
                topic = o.optString("topic"),
                stem = o.optString("stem"),
                chosen = o.optString("chosen"),
                correct = o.optString("correct"),
                explanation = o.optString("explanation"),
            )
        }
        return items
    }

    /**
     * Whether [answer] is what the question asks for — the prediction the screen shows at once.
     *
     * This never decides an award; [QuizTicket] and the server do. It is deliberately not stricter
     * than the server: an answer the server would accept but this rejects would teach a child that a
     * correct answer was wrong, which is worse than the reverse.
     */
    fun predictsCorrect(question: QuizQuestion, answer: String): Boolean =
        if (question.isChoice) {
            question.options.firstOrNull { it.label.equals(answer.trim(), ignoreCase = true) }?.correct == true
        } else {
            val normal = normalise(answer)
            normal.isNotEmpty() && question.accepted.any { normalise(it) == normal }
        }

    /**
     * What a fill-in answer looks like before comparison: no spaces, no full-width characters, no
     * trailing full stop, lower case. `3/4.` and `３／４` are the same answer to a child, so they are
     * the same answer here.
     */
    fun normalise(text: String): String {
        val out = StringBuilder(text.length)
        for (ch in text) {
            var c = ch
            // Full-width ASCII (！ through ～) is the same character shifted by a fixed offset.
            if (c in '\uFF01'..'\uFF5E') c = (c.code - 0xFEE0).toChar()
            if (c == '\u3000' || c == ' ' || c == '\t' || c == '\n' || c == '\r') continue
            if (c == '。' || c == '、' || c == '，') continue
            out.append(c.lowercaseChar())
        }
        return out.toString().trimEnd('.', ',')
    }
}

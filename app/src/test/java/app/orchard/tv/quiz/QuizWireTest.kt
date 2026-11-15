package app.orchard.tv.quiz

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The one prediction the television is allowed to make (B1): what the screen says right after an
 * answer, before the server has seen it.
 *
 * The cases that matter are the ones a child will actually type — a fraction, the same fraction as a
 * decimal, a full-width digit from a keyboard that was left in Chinese, and a trailing full stop.
 * Being wrong in the strict direction ("you were right but it says wrong") is the one failure this
 * test is here to prevent.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [23])
class QuizWireTest {

    private fun choice(vararg options: Pair<String, String>, correct: String): QuizQuestion {
        val json = JSONObject().apply {
            put("id", 7)
            put("qtype", "choice")
            put("stem", "1/2 + 1/4 = ?")
            put("angle", "apply")
            put("difficulty", 2)
            put("options", JSONArray().apply {
                options.forEach { (label, text) ->
                    put(JSONObject().apply {
                        put("label", label)
                        put("text", text)
                        put("correct", label == correct)
                    })
                }
            })
        }
        return QuizWire.question(json)
    }

    private fun fill(vararg accepted: String): QuizQuestion {
        val json = JSONObject().apply {
            put("id", 8)
            put("qtype", "fill")
            put("stem", "3/4 = ? （填小数）")
            put("accepted", JSONArray().apply { accepted.forEach { put(it) } })
        }
        return QuizWire.question(json)
    }

    @Test
    fun aChoiceIsRightOnlyForTheCorrectLabel() {
        val question = choice("A" to "1/4", "B" to "3/4", "C" to "2/4", "D" to "1", correct = "B")
        assertTrue(QuizWire.predictsCorrect(question, "B"))
        assertTrue(QuizWire.predictsCorrect(question, " b "))
        assertFalse(QuizWire.predictsCorrect(question, "A"))
        assertFalse(QuizWire.predictsCorrect(question, ""))
    }

    @Test
    fun aFillInAcceptsEverySpellingTheServerListed() {
        val question = fill("3/4", "0.75")
        assertTrue(QuizWire.predictsCorrect(question, "3/4"))
        assertTrue(QuizWire.predictsCorrect(question, "0.75"))
        assertTrue(QuizWire.predictsCorrect(question, " 0.75 "))
        assertTrue(QuizWire.predictsCorrect(question, "3/4。"))
        assertFalse(QuizWire.predictsCorrect(question, "0.7"))
        assertFalse(QuizWire.predictsCorrect(question, ""))
    }

    @Test
    fun fullWidthCharactersAreTheSameAnswer() {
        // A Chinese IME produces these; the server normalises them too, so the prediction must agree.
        assertEquals("3/4", QuizWire.normalise("３／４"))
        assertEquals("0.75", QuizWire.normalise("０.７５"))
        assertTrue(QuizWire.predictsCorrect(fill("3/4"), "３／４"))
    }

    @Test
    fun theGroupIsReadFromTheWireShape() {        val json = JSONObject().apply {
            put("session_id", "01JC0SESSION000000000000000")
            put("day_key", "2026-11-12")
            put("questions", JSONArray().apply {
                put(JSONObject().apply {
                    put("id", 11)
                    put("qtype", "fill")
                    put("stem", "1/2 + 1/4")
                    put("accepted", JSONArray().put("3/4").put("0.75"))
                })
            })
            put("quota", JSONObject().apply {
                put("success_count", 1)
                put("max_success", 3)
                put("attempts_left", 4)
                put("award_available", true)
            })
        }
        val group = QuizWire.group(json)
        assertEquals("01JC0SESSION000000000000000", group.sessionId)
        assertEquals(1, group.questions.size)
        assertEquals(listOf("3/4", "0.75"), group.questions[0].accepted)
        assertEquals(4, group.quota.attemptsLeft)
        assertTrue(group.quota.awardAvailable)
    }

    @Test
    fun theVerdictCarriesThisGroupsMistakesAndTheServersCooldown() {
        val json = JSONObject().apply {
            put("score", 3)
            put("total", 5)
            put("pass_line", 4)
            put("passed", false)
            put("award_ms", 0)
            put("ticket", JSONObject.NULL)
            put("cooldown_seconds", 600)
            put("review", JSONArray().put(
                JSONObject().apply {
                    put("question_id", 11)
                    put("subject", "数学")
                    put("topic", "分数的乘法")
                    put("stem", "1/2 × 1/4 = ?")
                    put("chosen", "A. 1/8")
                    put("correct", "B. 1/6")
                    put("explanation", "分子乘分子，分母乘分母")
                },
            ))
        }
        val verdict = QuizWire.verdict(json)
        assertEquals(600, verdict.cooldownSeconds)
        assertEquals(1, verdict.review.size)
        assertEquals("B. 1/6", verdict.review[0].correct)
        assertEquals("A. 1/8", verdict.review[0].chosen)
        assertEquals("分子乘分子，分母乘分母", verdict.review[0].explanation)
    }

    @Test
    fun aVerdictWithoutAReviewIsEmptyRatherThanBroken() {
        // A group that was all right, and a server from before this field existed: both are pages
        // with one button, not a parse error.
        val verdict = QuizWire.verdict(JSONObject().apply { put("score", 5); put("total", 5) })
        assertTrue(verdict.review.isEmpty())
        assertEquals(0, verdict.cooldownSeconds)
    }
}

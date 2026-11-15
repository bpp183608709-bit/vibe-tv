package app.orchard.tv.quiz

import android.util.Base64
import java.security.KeyPairGenerator
import java.security.spec.ECGenParameterSpec
import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config

/**
 * The one claim that matters, checked across the language boundary: **a ticket this project's server
 * signed actually verifies in this project's television code.**
 *
 * The vector in `src/test/resources/quiz-ticket.json` is not hand-written. `server/tools/make_tv_fixture.py`
 * registers a device against a running server over TLS, answers a group correctly, and records what
 * `server/app/ticket.py` signed — so this test fails the moment the two implementations stop agreeing
 * on base64url, on the signed range, on DER, or on a claim name.
 *
 * Without it, both sides could pass their own tests for ever while disagreeing with each other, and
 * the discovery would happen on a television at nine in the evening.
 */
@RunWith(RobolectricTestRunner::class)
@Config(sdk = [23])
class ServerTicketInteropTest {

    private val fixture: JSONObject by lazy {
        val text = checkNotNull(javaClass.classLoader?.getResourceAsStream("quiz-ticket.json")) {
            "quiz-ticket.json is missing; run server/tools/make_tv_fixture.py against a running server"
        }.use { it.readBytes().toString(Charsets.UTF_8) }
        JSONObject(text)
    }

    private val key = QuizTicket.es256PublicKey(
        Base64.decode(fixture.getString("public_key_b64"), Base64.DEFAULT),
    )

    @Test
    fun aTicketTheServerSignedIsAcceptedHere() {
        val ticket = QuizTicket.verify(
            raw = fixture.getString("ticket"),
            publicKey = key,
            hmacSecret = null,
            deviceId = fixture.getString("device_id"),
            dayKey = fixture.getString("day_key"),
            nowMs = fixture.getLong("now_ms"),
        )
        assertEquals(QuizTicket.AWARD_MS, ticket.ms)
        assertEquals(fixture.getString("device_id"), ticket.did)
        assertEquals(fixture.getString("day_key"), ticket.day)
    }

    @Test
    fun theSameTicketIsRefusedOnAnotherDay() {
        // The clock in the fixture is fixed, so "tomorrow" is a day after the day it carries.
        val refused = assertThrows(QuizTicket.Refused::class.java) {
            QuizTicket.verify(
                fixture.getString("ticket"), key, null,
                fixture.getString("device_id"), "2026-11-14", fixture.getLong("now_ms"),
            )
        }
        assertEquals(QuizTicket.Refusal.WRONG_DAY, refused.reason)
    }

    @Test
    fun theSameTicketIsRefusedUnderAnotherKey() {
        val other = KeyPairGenerator.getInstance("EC")
            .apply { initialize(ECGenParameterSpec("secp256r1")) }
            .generateKeyPair()
        val refused = assertThrows(QuizTicket.Refused::class.java) {
            QuizTicket.verify(
                fixture.getString("ticket"), other.public, null,
                fixture.getString("device_id"), fixture.getString("day_key"), fixture.getLong("now_ms"),
            )
        }
        assertEquals(QuizTicket.Refusal.BAD_SIGNATURE, refused.reason)
    }
}

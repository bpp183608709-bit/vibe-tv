package app.orchard.tv.parental

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/**
 * The one ambiguity in the precise tier: an empty session list. On the TVs this project runs on it
 * means "no app publishes a session", not "nothing is playing" — see [SessionVerdict].
 */
class SessionVerdictTest {

    @Test
    fun anEmptyListOnADeviceThatNeverAnsweredIsUnknown() {
        // MiTV-MFTP0 with the notification listener enabled: 120 s of 云视听极光 in front counted 0,
        // because this used to answer false. Unknown hands the decision to the coarse tiers.
        assertNull(SessionVerdict().of(emptyList()))
        assertNull(SessionVerdict().of(emptyList()))
    }

    @Test
    fun aSessionProvesTheListIsWorthReading() {
        assertEquals(false, SessionVerdict().of(listOf(false)))
    }

    @Test
    fun afterAProofAnEmptyListIsAPause() {
        // A five-minute pause has to end the session instead of counting as watching.
        val verdict = SessionVerdict()
        assertEquals(true, verdict.of(listOf(true)))
        assertEquals(false, verdict.of(emptyList()))
    }

    @Test
    fun onePlayingSessionAmongPausedOnesCounts() {
        val verdict = SessionVerdict()
        assertEquals(true, verdict.of(listOf(false, true)))
        assertEquals(false, verdict.of(listOf(false, false)))
    }

    @Test
    fun theProofSurvivesThePauseItBroughtAbout() {
        val verdict = SessionVerdict()
        verdict.of(listOf(true))
        assertEquals(false, verdict.of(emptyList()))
        assertEquals(true, verdict.of(listOf(true)))
        assertEquals(false, verdict.of(emptyList()))
    }
}

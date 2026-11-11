package app.orchard.tv.parental

import app.orchard.tv.parental.PlaybackSignal.Level
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * The three signals, over a device that can be told what to answer. The order matters: a readable
 * session outranks the foreground app, and a dark screen outranks everything.
 */
class PlaybackSignalTest {

    private class Fake(
        private val playing: Boolean? = null,
        private val front: String? = null,
        private val on: Boolean = true,
    ) : PlaybackSignal.Source {
        var asked = 0
        override fun ownPackage() = OURS
        override fun mediaPlaying(): Boolean? { asked++; return playing }
        override fun foreground() = front
        override fun screenOn() = on
    }

    private fun signal(fake: Fake) = PlaybackSignal(fake).resolve()

    @Test
    fun aSessionPlayingIsWatched() {
        // Also the 儿童模式 case: its desk is PatchWall's home activity, so only a session tells that
        // something is playing there (the coarse tier reads the launcher and stops counting).
        val r = signal(Fake(playing = true, front = "com.mitv.tvhome"))
        assertTrue(r.watching)
        assertEquals(Level.Precise, r.level)
    }

    @Test
    fun aSessionThatIsReadableButNotPlayingIsAPause() {
        // This is what makes a five-minute pause end the session rather than count as watching.
        val r = signal(Fake(playing = false, front = "com.ktcp.video"))
        assertFalse(r.watching)
        assertEquals(Level.Precise, r.level)
    }

    @Test
    fun withoutSessionsAVideoAppInFrontCounts() {
        val r = signal(Fake(playing = null, front = "com.ktcp.video"))
        assertTrue(r.watching)
        assertEquals(Level.Foreground, r.level)
    }

    @Test
    fun aDarkScreenCountsNothingEvenWithASessionPlaying() {
        val r = signal(Fake(playing = true, front = "com.ktcp.video", on = false))
        assertFalse(r.watching)
    }

    @Test
    fun aLauncherIsNeverWatched() {
        assertEquals(false, signal(Fake(playing = null, front = "com.mitv.tvhome")).watching)
        assertEquals(false, signal(Fake(playing = null, front = "com.google.android.tvlauncher")).watching)
        assertEquals(false, signal(Fake(playing = null, front = "com.dangbei.tvlauncher")).watching)
    }

    @Test
    fun settingsAreNeverWatched() {
        assertEquals(false, signal(Fake(playing = null, front = "com.xiaomi.mitv.settings")).watching)
        assertEquals(false, signal(Fake(playing = null, front = "com.android.tv.settings")).watching)
    }

    @Test
    fun aerialItselfIsNeverWatched() {
        assertEquals(false, signal(Fake(playing = null, front = OURS)).watching)
    }

    @Test
    fun anyOtherAppOnALitScreenCountsRoughly() {
        val r = signal(Fake(playing = null, front = "com.example.somewhere"))
        assertTrue(r.watching)
        assertEquals(Level.Usage, r.level)
    }

    @Test
    fun knowingNothingCountsNothing() {
        // Not "the screen is on, so it must be watched": that would lock a child out for sitting on
        // the launcher, which is worse than a limit that misses.
        val r = signal(Fake(playing = null, front = null))
        assertFalse(r.watching)
        assertEquals(Level.Usage, r.level)
    }

    @Test
    fun repeatingAReadWithinTheSampleWindowAsksOnce() {
        val fake = Fake(playing = null, front = "com.ktcp.video")
        val signal = PlaybackSignal(fake, sampleMs = 2_000)
        signal.read(10_000)
        signal.read(11_500)
        assertEquals(1, fake.asked)
        signal.read(12_500)
        assertEquals(2, fake.asked)
    }
}

private const val OURS = "app.orchard.tv"

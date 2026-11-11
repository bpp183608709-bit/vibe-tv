package app.orchard.tv.parental

/**
 * The ticket of 严格模式: one password entry, one stretch of watching.
 *
 * With the mode on, a video app is treated like the store — it needs the parental password before it
 * comes to the front (see `HomeGuardService.needsPin`). What the password buys is this ticket, and a
 * ticket is what the counters follow: while one is held, the clock runs whether or not a video is
 * playing, because the time has been handed to the child rather than observed on the screen. That is
 * the whole difference from 记账, where the clock follows [PlaybackSignal] and a child parked on a
 * video app's home screen is not counted at all (docs/PARENTAL.md, 严格模式).
 *
 * Three rules, all of them the parent's rather than the child's:
 *
 *  - **It runs out.** The ticket is a duration, not a flag: when it is spent the clock stops and the
 *    guard asks for the password again, so a parent who is out of the house has handed out exactly
 *    the minutes they meant to.
 *  - **Leaving ends it, but not at once.** [GRACE_MS] covers a trip to the kitchen; past that the
 *    ticket is dead and coming back asks for the password. The grace is counted as watching — the
 *    time is still the child's, and counting it is what keeps the grace from being a way to bank
 *    minutes by stepping out every few minutes.
 *  - **Switching apps does not carry it over.** A ticket is for one package; the next video app asks
 *    again.
 *
 * Kept out of Android on purpose: the clock is handed in, so the states can be read and tested
 * without a TV. Only two callers: [ParentalClock] advances it every second, `HomeGuardService` reads
 * [holds] to decide whether to put the password up.
 */
object WatchTicket {

    /**
     * How long the ticket survives its holder leaving the front — long enough for the toilet or a
     * snack, short enough that stepping out to the TV's launcher is not a way around the password.
     */
    const val GRACE_MS = 60_000L

    /** The shortest ticket worth a password; a shorter one is not offered by the settings page. */
    const val MIN_TICKET_MS = 5 * 60_000L

    @Volatile
    private var holder: String? = null

    @Volatile
    private var grantedAt = 0L

    @Volatile
    private var durationMs = 0L

    /** When the holder was last seen in front: what the grace is measured from. */
    @Volatile
    private var atFrontAt = 0L

    /** A new ticket for [pkg], good for [durationMs] from [now]. One ticket at a time: a new one replaces an old one. */
    @Synchronized
    fun grant(pkg: String, now: Long, durationMs: Long) {
        holder = pkg
        grantedAt = now
        this.durationMs = durationMs.coerceAtLeast(MIN_TICKET_MS)
        atFrontAt = now
    }

    /**
     * Whether [pkg] may stay in front on the ticket it holds: same package, not spent, and not gone
     * from the front for longer than [GRACE_MS]. Read by the guard, which asks many times a second,
     * so this changes nothing but a spent ticket, which is dropped here.
     */
    @Synchronized
    fun holds(pkg: String, now: Long): Boolean {
        val current = holder ?: return false
        if (expired(now)) {
            drop()
            return false
        }
        if (current != pkg) return false
        // Gone from the front longer than the grace: the ticket dies here rather than lingering as a
        // number the settings page would still show.
        if (now - atFrontAt > GRACE_MS) {
            drop()
            return false
        }
        return true
    }

    /**
     * Whether the clock should run: a ticket is held and not spent, and the child is either in the
     * app it was given for or only just out of it. Called once a second by [ParentalClock], which is
     * what moves [atFrontAt] — the guard never does, so a guard that stops running cannot keep a
     * ticket alive.
     */
    @Synchronized
    fun counting(front: String?, now: Long): Boolean {
        holder ?: return false
        if (expired(now)) {
            drop()
            return false
        }
        if (front == holder) {
            atFrontAt = now
            return true
        }
        if (now - atFrontAt <= GRACE_MS) return true
        // Away for longer than the grace: the ticket is gone, not merely paused. A number left over
        // would let a child come back to the same ticket minutes later.
        drop()
        return false
    }

    /** Seconds left on the ticket; 0 when there is none. For the settings page and the log. */
    @Synchronized
    fun leftSec(now: Long): Int {
        holder ?: return 0
        if (expired(now)) {
            drop()
            return 0
        }
        return ((durationMs - (now - grantedAt)) / 1000L).toInt()
    }

    /** The package the ticket is for, or null. */
    @Synchronized
    fun holder(): String? = holder

    /** Ends the ticket: the mode was switched off, or the parent ended it from the settings page. */
    @Synchronized
    fun clear() = drop()

    private fun expired(now: Long): Boolean = holder != null && now - grantedAt >= durationMs

    private fun drop() {
        holder = null
        grantedAt = 0L
        durationMs = 0L
        atFrontAt = 0L
    }
}

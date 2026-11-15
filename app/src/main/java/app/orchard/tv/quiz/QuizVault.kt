package app.orchard.tv.quiz

import android.content.Context
import androidx.core.content.edit
import app.orchard.tv.parental.Parental
import app.orchard.tv.parental.ParentalStore
import app.orchard.tv.system.DiagLog

/**
 * What the television remembers about the quiz: which device it is, which tickets it has already
 * turned into minutes, and the last group it was served.
 *
 * **The spent list is the point of this file.** A ticket carries its own `jti`, and a ticket that has
 * been redeemed once must never work again — not after a reboot, not after the same string is pasted
 * twice, not after the settings were restored from a backup. So the `jti` is written *before* the
 * minutes are added: a crash in between loses thirty minutes, which a parent can see and replace with
 * the password, while the other order would hand out thirty minutes for free every crash.
 *
 * **The device id is a name, not a credential.** It is what the server files a day's counters under.
 * Losing it means re-pairing and starting today's quiz allowance from zero, which is why it is stored
 * on the television rather than derived from something that changes.
 */
object QuizVault {

    private const val PREFS = "quiz"
    private const val KEY_DEVICE = "device_id"
    private const val KEY_PAIR_CODE = "pair_code"
    private const val KEY_PAIR_UNTIL = "pair_until"
    private const val KEY_SPENT = "spent"
    private const val KEY_CONFIG_VERSION = "config_version"
    private const val KEY_BOUND = "bound"
    private const val KEY_COOLDOWN_UNTIL = "cooldown_until"

    fun deviceId(context: Context): String? = prefs(context).getString(KEY_DEVICE, null)?.ifEmpty { null }

    fun pairCode(context: Context): String? = prefs(context).getString(KEY_PAIR_CODE, null)?.ifEmpty { null }

    /** When the code on screen stops working, as a wall clock millisecond (0 when there is none). */
    fun pairUntil(context: Context): Long = prefs(context).getLong(KEY_PAIR_UNTIL, 0L)

    /**
     * Whether a phone has been bound, as of the last time the server said so.
     *
     * A copy, not the truth: the device row holds that. It exists so the settings page can label the
     * row without a network call every time it is drawn, and it is refreshed by the pairing page and by
     * every heartbeat.
     */
    fun bound(context: Context): Boolean = prefs(context).getBoolean(KEY_BOUND, false)

    fun setBound(context: Context, bound: Boolean) {
        prefs(context).edit { putBoolean(KEY_BOUND, bound) }
    }

    /**
     * The settings version this television has applied (docs/PARENTAL.md, 设置下发).
     *
     * It is half of "last write wins": the heartbeat sends it back, and the server treats a matching
     * version with different numbers as "the television changed these" rather than "the phone did".
     * Without it the five-minute poll would overwrite a change made on the TV with the copy the
     * server still held, every time.
     */
    fun configVersion(context: Context): Int = prefs(context).getInt(KEY_CONFIG_VERSION, 0)

    fun setConfigVersion(context: Context, version: Int) {
        prefs(context).edit { putInt(KEY_CONFIG_VERSION, version) }
    }

    /**
     * When the server will take another group, as a wall-clock millisecond; 0 when it will now.
     *
     * A copy of the server's number, kept only so the lock screen can count down without asking: the
     * seconds come from the grade response, from the 429 that refused a group, or from a heartbeat.
     * The television never invents "ten minutes" — it would drift from the cooldown the server is
     * actually enforcing, and the screen would promise a retry the server refuses.
     *
     * Wall clock rather than elapsed time because the lock screen is reopened by a different process
     * path than the one that learned the number; a deadline that survives a restart is what matters,
     * and a clock moved by hand only ever shortens a wait the server still enforces.
     */
    fun cooldownUntil(context: Context, nowWall: Long = System.currentTimeMillis()): Long {
        val until = prefs(context).getLong(KEY_COOLDOWN_UNTIL, 0L)
        return if (until > nowWall) until else 0L
    }

    /** Records a cooldown of [seconds] from now. Zero or less forgets it. */
    fun setCooldown(context: Context, seconds: Int, nowWall: Long = System.currentTimeMillis()) {
        prefs(context).edit {
            if (seconds <= 0) remove(KEY_COOLDOWN_UNTIL) else putLong(KEY_COOLDOWN_UNTIL, nowWall + seconds * 1_000L)
        }
    }

    /**
     * The whole seconds left on the cooldown, rounded up: 600 becomes 600 and 1 becomes 1, so the
     * lock screen never says 0:00 while the server would still refuse a group.
     */
    fun cooldownSec(context: Context, nowWall: Long = System.currentTimeMillis()): Int {
        val left = cooldownUntil(context, nowWall) - nowWall
        return if (left <= 0L) 0 else ((left + 999L) / 1_000L).toInt()
    }

    fun saveDevice(context: Context, deviceId: String, code: String, untilWallMs: Long) {
        prefs(context).edit {
            putString(KEY_DEVICE, deviceId)
            putString(KEY_PAIR_CODE, code)
            putLong(KEY_PAIR_UNTIL, untilWallMs)
        }
    }

    /**
     * Forgets the device, so the next group registers again.
     *
     * Two callers, one meaning: "the row the server files my day under is not mine any more". Either
     * the server was rebuilt and no longer knows the id, or the parent chose 换一台手机 because the
     * phone is gone. Both end in `pair/start`, and a fresh row starts at version 0 with nobody bound,
     * so the settings version and the bound flag go with it — keeping the old version would leave the
     * television asking for "version 5" of a device that has none, and keeping the flag would leave
     * the settings page saying 已连接 with no phone behind it.
     *
     * The spent list is kept: a ticket the old row signed was still a ticket this television has
     * already paid out, and re-registering must not turn it into a second thirty minutes.
     */
    fun forgetDevice(context: Context) {
        prefs(context).edit {
            remove(KEY_DEVICE)
            remove(KEY_PAIR_CODE)
            remove(KEY_PAIR_UNTIL)
            remove(KEY_CONFIG_VERSION)
            remove(KEY_BOUND)
            // A cooldown belongs to the row the server filed it under; a fresh row has none.
            remove(KEY_COOLDOWN_UNTIL)
        }
    }

    /** Tickets already turned into minutes on [dayKey]. Anything from another day is dropped. */
    fun spentToday(context: Context, dayKey: String): Set<String> =
        prefs(context).getStringSet(KEY_SPENT, emptySet()).orEmpty()
            .filter { it.startsWith("$dayKey|") }
            .map { it.substringAfter('|') }
            .toSet()

    private fun markSpent(context: Context, dayKey: String, jti: String) {
        // Only today's entries are kept: a ticket from another day cannot be redeemed (`day` is
        // checked), so keeping its jti would grow a set for ever.
        val keep = prefs(context).getStringSet(KEY_SPENT, emptySet()).orEmpty()
            .filter { it.startsWith("$dayKey|") }
            .toMutableSet()
        keep += "$dayKey|$jti"
        prefs(context).edit { putStringSet(KEY_SPENT, keep) }
    }

    /** Why a ticket was not turned into minutes. */
    sealed interface Redeem {
        /** Added to today. [minutes] is what the lock screen says. */
        data class Added(val minutes: Int) : Redeem

        /** The signature, the day, the device or the spent list refused it. */
        data class Refused(val reason: QuizTicket.Refusal) : Redeem

        /** A ticket was offered but this build has no key to check it with. */
        data object Unverifiable : Redeem
    }

    /**
     * Turns a ticket into minutes today.
     *
     * The verification is [QuizTicket]'s, with this television's id, its own 04:00 day, and the
     * tickets it has already spent. [Parental.extendDaily] is called only after all of that passes,
     * and its signature is deliberately unchanged: the ticket decides *whether*, the timer decides
     * *how much is left*.
     */
    fun redeem(context: Context, ticketText: String, nowWall: Long = System.currentTimeMillis()): Redeem {
        val device = deviceId(context) ?: return Redeem.Refused(QuizTicket.Refusal.WRONG_DEVICE)
        val publicKey = QuizConfig.publicKey
        val hmac = QuizConfig.devHmac
        if (publicKey == null && hmac == null) return Redeem.Unverifiable

        val day = ParentalStore.dayKey(nowWall)
        val ticket = try {
            QuizTicket.verify(
                raw = ticketText,
                publicKey = publicKey,
                hmacSecret = hmac,
                deviceId = device,
                dayKey = day,
                nowMs = nowWall,
                spentJti = spentToday(context, day),
            )
        } catch (e: QuizTicket.Refused) {
            DiagLog.i("quiz: ticket refused (${e.reason})")
            return Redeem.Refused(e.reason)
        }

        markSpent(context, day, ticket.jti)
        // The minutes are reported as the change they actually made, not as what the ticket asked
        // for. With no daily limit set, `extendDaily` adds nothing — the day is already unlimited —
        // and saying "+30 分钟" there would be a lie the child could check on the lock screen.
        val before = Parental.ui.dailyBudgetMs
        Parental.extendDaily(ticket.ms, nowWall = nowWall)
        val addedMs = (Parental.ui.dailyBudgetMs - before).coerceAtLeast(0L)
        DiagLog.i("quiz: ticket ${ticket.jti} spent, +${addedMs / 60_000} min of ${ticket.ms / 60_000}")
        return Redeem.Added((addedMs / 60_000L).toInt())
    }

    private fun prefs(context: Context) =
        context.applicationContext.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
}

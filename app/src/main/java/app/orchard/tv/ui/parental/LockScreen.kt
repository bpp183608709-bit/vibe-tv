package app.orchard.tv.ui.parental

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.orchard.tv.R
import app.orchard.tv.parental.Parental
import app.orchard.tv.parental.WatchTimer
import app.orchard.tv.ui.components.Pressable
import app.orchard.tv.ui.home.PinMode
import app.orchard.tv.ui.home.PinPad
import app.orchard.tv.ui.theme.Glyph
import app.orchard.tv.ui.theme.GlyphIcon
import app.orchard.tv.ui.theme.Palette
import app.orchard.tv.ui.theme.Type

/**
 * The two lock screens of docs/PARENTAL.md (界面), drawn after the prototype's `锁屏` scenes.
 *
 * Which one is up follows the timer's state, not the caller: an interval has a countdown and one way
 * out of it, the daily limit has four ways and no clock. The password is asked for every one of them
 * — a way past a limit that did not need it would be a way past the limit.
 *
 * The screens do not tick and do not open or close the lock: `LockActivity` owns that, so there is
 * exactly one clock over the counters (Home's) and one place that decides when the lock is over.
 */
@Composable
fun ParentalLockScreen(ui: Parental.Ui, leftSec: Int) {
    val breaking = ui.state == WatchTimer.State.Interval
    var pending by remember { mutableStateOf<Unlock?>(null) }
    val first = remember { FocusRequester() }
    val context = LocalContext.current
    // Back from the keypad, or after a limit is lifted under the pad: focus belongs on the first
    // action again, never nowhere.
    LaunchedEffect(pending) {
        if (pending == null) {
            withFrameNanos { }
            runCatching { first.requestFocus() }
        }
    }

    Box(
        Modifier
            .fillMaxSize()
            .drawBehind {
                // The prototype's radial wash: a hint of teal at the top, going to near black. Drawn
                // rather than laid on as a colour, so it reads the same at 720p and at 4K.
                drawRect(
                    Brush.radialGradient(
                        colorStops = arrayOf(
                            0f to Color(0xFF1C2C34),
                            0.62f to Color(0xFF040608),
                            1f to Color(0xFF040608),
                        ),
                        center = Offset(size.width / 2f, 0f),
                        radius = size.height * 1.2f,
                    ),
                )
            },
    ) {
        Tag(
            text = if (breaking) R.string.parental_lock_tag_break else R.string.parental_lock_tag_daily,
            modifier = Modifier.align(Alignment.TopCenter).padding(top = 24.dp),
        )

        Column(
            Modifier.align(Alignment.Center).width(560.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // The lock for the day, the moon for a break: the same pair the settings page uses.
            GlyphIcon(
                if (breaking) Glyph.Moon else Glyph.Shield,
                Palette.Label.copy(alpha = 0.4f),
                34.dp,
            )
            Spacer(Modifier.height(18.dp))
            BasicText(
                stringResource(if (breaking) R.string.parental_lock_break_title else R.string.parental_lock_daily_title),
                // BasicText takes its alignment from the style, not an argument.
                style = Type.LargeTitle.copy(fontSize = 30.sp, fontWeight = FontWeight.W600, textAlign = TextAlign.Center),
            )
            Spacer(Modifier.height(8.dp))
            BasicText(
                emphasised(
                    if (breaking) {
                        stringResource(R.string.parental_lock_break_why, durationText(ui.sessionUsedMs), durationText(ui.intervalMs))
                    } else {
                        stringResource(R.string.parental_lock_daily_why, durationText(ui.dailyUsedMs), durationText(ui.dailyBudgetMs))
                    },
                ),
                style = Type.Body.copy(color = Palette.SecondaryLabel, textAlign = TextAlign.Center),
            )
            if (breaking) {
                Spacer(Modifier.height(10.dp))
                // Tabular digits: without them the countdown shifts sideways on every tick.
                BasicText(
                    clockText(leftSec),
                    style = Type.LargeTitle.copy(fontSize = 64.sp, fontWeight = FontWeight.W700, fontFeatureSettings = "tnum"),
                )
                Spacer(Modifier.height(6.dp))
                BasicText(
                    stringResource(R.string.parental_lock_left),
                    style = Type.Caption.copy(color = Palette.TertiaryLabel),
                )
            }
            Spacer(Modifier.height(26.dp))
            Column(Modifier.width(330.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                val actions = if (breaking) {
                    listOf(Unlock.Skip to R.string.parental_lock_skip)
                } else {
                    listOf(
                        Unlock.Half to R.string.parental_lock_extend_30,
                        Unlock.Hour to R.string.parental_lock_extend_60,
                        Unlock.TwoHours to R.string.parental_lock_extend_120,
                        Unlock.Lift to R.string.parental_lock_lift,
                    )
                }
                actions.forEachIndexed { i, (action, label) ->
                    LockButton(
                        label = label,
                        // The last one is the one for the evening being over, and it says so in the
                        // prototype's orange: it is the only action that outlives the limit setting.
                        danger = action == Unlock.Lift,
                        // While the password is up the actions go dark to the remote: the keypad is
                        // a full-screen overlay in the same tree, and a focusable button under it
                        // takes the arrow keys the moment the search runs out of keys inside the
                        // pad — the same reason Home disables its grid under an overlay.
                        enabled = pending == null,
                        focusRequester = if (i == 0) first else null,
                        onClick = { pending = action },
                    )
                }
            }
        }

        BasicText(
            stringResource(R.string.parental_lock_foot),
            style = Type.Caption.copy(color = Palette.Label.copy(alpha = 0.32f), textAlign = TextAlign.Center),
            modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 26.dp),
        )
    }

    // The way past every one of the actions: the parent's password, then the action itself. The
    // keypad is the module's own (8 to 12 digits), and it counts wrong entries like every other.
    val blockedAtOpen = if (pending != null) Parental.blockedSec() else 0
    PinPad(
        visible = pending != null,
        mode = PinMode.Unlock,
        minLength = MIN_PIN,
        maxLength = MAX_PIN,
        allowArrows = false,
        hintOverride = if (blockedAtOpen > 0) R.string.parental_lock_blocked_hint else R.string.parental_lock_pin_hint,
        verify = { Parental.verifyPin(it) },
        wrongText = {
            val left = Parental.blockedSec()
            if (left > 0) context.getString(R.string.parental_lock_blocked, clockString(context, left)) else null
        },
        onDone = {
            pending?.let(::carryOut)
            pending = null
        },
        onCancel = { pending = null },
    )
}

/** The four ways past the daily limit and the one way past a break, before the password is asked. */
private enum class Unlock { Half, Hour, TwoHours, Lift, Skip }

/**
 * Applies what the parent chose. Nothing here closes the lock: [Parental] publishes the new state,
 * and `LockActivity` lets go the moment a limit is no longer used up — one path out, whatever the
 * reason.
 */
private fun carryOut(action: Unlock) = when (action) {
    Unlock.Half -> Parental.extendDaily(30L * 60_000)
    Unlock.Hour -> Parental.extendDaily(60L * 60_000)
    Unlock.TwoHours -> Parental.extendDaily(120L * 60_000)
    Unlock.Lift -> Parental.liftDailyLimit()
    Unlock.Skip -> Parental.skipInterval()
}

/** 家长模式已锁定 / 休息时间, the pill at the top: orange, because the lock is not an error. */
@Composable
private fun Tag(text: Int, modifier: Modifier = Modifier) {
    Row(
        modifier
            .clip(RoundedCornerShape(999.dp))
            .background(Palette.Orange.copy(alpha = 0.14f))
            .height(26.dp)
            .padding(horizontal = 12.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        Box(Modifier.size(6.dp).clip(CircleShape).background(Palette.Orange))
        BasicText(
            stringResource(text),
            style = Type.Caption.copy(color = Palette.Orange, fontWeight = FontWeight.W600),
        )
    }
}

/** One action: its name on the left, what it costs on the right. */
@Composable
private fun LockButton(
    label: Int,
    danger: Boolean = false,
    enabled: Boolean = true,
    focusRequester: FocusRequester? = null,
    onClick: () -> Unit,
) {
    Pressable(onClick = onClick, enabled = enabled, focusRequester = focusRequester, focusedScale = 1.03f) { state ->
        val bg = if (state.focused) Palette.Focus else Palette.Fill
        val fg = when {
            state.focused && danger -> Color(0xFF7A4A00)
            state.focused -> Palette.OnFocus
            danger -> Palette.Orange
            else -> Palette.Label
        }
        Row(
            Modifier
                .fillMaxWidth()
                .height(44.dp)
                .clip(RoundedCornerShape(12.dp))
                .background(bg)
                .padding(horizontal = 18.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            BasicText(stringResource(label), style = Type.Body.copy(color = fg), modifier = Modifier.weight(1f), maxLines = 1)
            BasicText(
                stringResource(R.string.parental_lock_needs_pin),
                style = Type.Caption.copy(color = fg.copy(alpha = 0.5f)),
                maxLines = 1,
            )
        }
    }
}

/** The countdown as a string for the keypad's warning, which is built outside a composable. */
private fun clockString(context: android.content.Context, seconds: Int): String =
    if (seconds >= 3600) {
        context.getString(R.string.parental_clock_hours, seconds / 3600, seconds % 3600 / 60, seconds % 60)
    } else {
        context.getString(R.string.parental_clock, seconds / 60, seconds % 60)
    }

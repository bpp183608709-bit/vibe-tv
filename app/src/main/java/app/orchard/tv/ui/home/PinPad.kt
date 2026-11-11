package app.orchard.tv.ui.home

import android.view.KeyEvent as NativeKeyEvent
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.scaleIn
import androidx.compose.animation.scaleOut
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.focusable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.withFrameNanos
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.focusRequester
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.onPreviewKeyEvent
import androidx.compose.ui.input.key.type
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.orchard.tv.R
import app.orchard.tv.settings.AerialSettings
import app.orchard.tv.ui.components.Pressable
import app.orchard.tv.ui.components.consumeTaps
import app.orchard.tv.ui.components.dismissOnTap
import app.orchard.tv.ui.components.liquidGlass
import app.orchard.tv.ui.theme.Glyph
import app.orchard.tv.ui.theme.GlyphIcon
import app.orchard.tv.ui.theme.Motion
import app.orchard.tv.ui.theme.Palette
import app.orchard.tv.ui.theme.Type

enum class PinMode { Unlock, Create }

/**
 * The PIN (issue #10), and the parental password that took it over (docs/PARENTAL.md).
 *
 * Four digits by default, from the remote's number keys or the keypad on screen (most remotes here
 * have no number keys), or four arrow keys (AerialSettings.pinArrows). [PinMode.Create] asks which
 * kind, then asks twice and stores it.
 *
 * The parental module wants 8 to 12 characters instead, so the length is [minLength] to
 * [maxLength] rather than a constant. A fixed length keeps the old behaviour exactly: the panel
 * submits itself on the last digit. A range cannot, because nothing says when the last digit is
 * in, so the keypad grows an OK key and the dots grow past [minLength] as they are typed — which
 * is also the one part of this panel a remote can reach.
 *
 * [verify] and [onSave] let a caller keep the password somewhere else than AerialSettings: the
 * parental module stores its own, and the entries still behave the same. [wrongText] is for a caller
 * whose refusal needs a number in it — the parental module shuts the keypad for a while after five
 * wrong entries, and "wrong password" alone would leave the parent pressing keys at a panel that is
 * only going to refuse them.
 *
 * [footer] is one line under the keypad, the last thing a remote reaches by going down. The parental
 * module puts its way back in there (修改密码 with the administrator phrase, docs/PARENTAL.md), so a
 * parent who has forgotten the password is not locked out of their own TV.
 */
@Composable
fun PinPad(
    visible: Boolean,
    mode: PinMode?,
    minLength: Int = 4,
    maxLength: Int = 4,
    allowArrows: Boolean = true,
    hintOverride: Int? = null,
    verify: ((String) -> Boolean)? = null,
    onSave: ((String) -> Unit)? = null,
    wrongOverride: Int? = null,
    wrongText: (() -> String?)? = null,
    footer: (@Composable () -> Unit)? = null,
    onDone: () -> Unit,
    onCancel: () -> Unit,
) {
    BackHandler(enabled = visible, onBack = onCancel)
    // The mode is gone once the overlay changes; keep the last one while the panel fades out.
    var shown by remember { mutableStateOf(mode) }
    if (mode != null) shown = mode
    // Each opening starts empty. A panel's own state otherwise survives Back and the next open —
    // the two runs of digits joined into a password nobody typed, which also spends another of the
    // five tries. The key is changed on the way in, not on the way out, so the fade still shows
    // what was typed.
    var opening by remember { mutableStateOf(0) }
    LaunchedEffect(visible) { if (visible) opening++ }
    val low = minLength.coerceIn(1, 32)
    val high = maxLength.coerceIn(low, 32)
    AnimatedVisibility(
        visible = visible,
        enter = fadeIn(tween(200, easing = Motion.EaseOut)) + scaleIn(tween(300, easing = Motion.Drawer), initialScale = 0.94f),
        exit = fadeOut(tween(150, easing = Motion.EaseOut)) + scaleOut(tween(150, easing = Motion.EaseOut), targetScale = 0.97f),
        modifier = Modifier.fillMaxSize(),
    ) {
        // Each opening starts empty: the panel leaves composition while hidden.
        shown?.let { shownMode ->
            key(opening) {
                PinPanel(
                    mode = shownMode,
                    minLength = low,
                    maxLength = high,
                    allowArrows = allowArrows,
                    hintOverride = hintOverride,
                    verify = verify,
                    onSave = onSave,
                    wrongOverride = wrongOverride,
                    wrongText = wrongText,
                    footer = footer,
                    onDone = onDone,
                    onCancel = onCancel,
                )
            }
        }
    }
}

@Composable
private fun PinPanel(
    mode: PinMode,
    minLength: Int,
    maxLength: Int,
    allowArrows: Boolean,
    hintOverride: Int?,
    verify: ((String) -> Boolean)?,
    onSave: ((String) -> Unit)?,
    wrongOverride: Int?,
    wrongText: (() -> String?)?,
    footer: (@Composable () -> Unit)?,
    onDone: () -> Unit,
    onCancel: () -> Unit,
) {
    var entered by remember { mutableStateOf("") }
    var first by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    // Resolved here rather than when a wrong entry is made: the text is a resource, and [complete]
    // is not a composable.
    val wrong = stringResource(wrongOverride ?: R.string.pin_wrong)
    val mismatch = stringResource(R.string.pin_mismatch)
    val tooShort = stringResource(R.string.pin_too_short, minLength)
    // A fixed length submits itself on the last character, as it always has; a range waits for OK,
    // because there is no way to tell the last character from a shorter one. Arrows can only be a
    // fixed four, so a range never offers them.
    val fixed = minLength == maxLength
    // Null while a new PIN's kind is still to be chosen. A caller that only takes digits skips
    // that question, and a Create there never offers arrows (@see allowArrows).
    var arrows by remember { mutableStateOf(when { !allowArrows || !fixed -> false; mode == PinMode.Unlock -> AerialSettings.pinArrows; else -> null }) }
    val one = remember { FocusRequester() }
    val choice = remember { FocusRequester() }
    val pad = remember { FocusRequester() }
    LaunchedEffect(arrows) {
        withFrameNanos { }
        runCatching {
            when (arrows) {
                null -> choice.requestFocus()
                true -> pad.requestFocus()
                false -> one.requestFocus()
            }
        }
    }

    fun complete(pin: String) {
        when (mode) {
            PinMode.Unlock -> if (verify?.invoke(pin) ?: AerialSettings.unlock(pin)) onDone()
                else error = wrongText?.invoke() ?: wrong
            PinMode.Create -> when (val earlier = first) {
                null -> { first = pin; error = null }
                pin -> { if (onSave != null) onSave(pin) else AerialSettings.choosePin(pin, arrows = arrows == true); onDone() }
                else -> { first = null; error = mismatch }
            }
        }
        entered = ""
    }

    // A fixed length submits itself on the last character, as it always has; a range waits for OK,
    // because there is no way to tell the last character from a shorter one.
    fun type(digit: Char) {
        if (entered.length >= maxLength) return
        entered += digit
        if (fixed && entered.length == maxLength) complete(entered)
    }

    fun erase() { entered = entered.dropLast(1) }

    val title = when {
        mode == PinMode.Unlock -> R.string.pin_enter
        first == null -> R.string.pin_create
        else -> R.string.pin_confirm
    }
    val hint = hintOverride ?: when {
        arrows == null -> R.string.pin_kind_hint
        arrows == true -> if (mode == PinMode.Create) R.string.pin_arrows_create_hint else R.string.pin_arrows_hint
        mode == PinMode.Create -> R.string.pin_create_hint
        else -> R.string.pin_enter_hint
    }

    Box(
        Modifier
            .fillMaxSize()
            .dismissOnTap(onCancel)
            .background(Color.Black.copy(alpha = 0.45f))
            .onPreviewKeyEvent { e ->
                val code = e.nativeKeyEvent.keyCode
                if (arrows == true) {
                    // Every arrow is an entry, never a move; OK does nothing. Ups go too, so no
                    // control below sees a key it did not see go down.
                    val symbol = ARROWS[code]
                    if (symbol == null && code != NativeKeyEvent.KEYCODE_DPAD_CENTER && code != NativeKeyEvent.KEYCODE_ENTER) return@onPreviewKeyEvent false
                    if (e.type == KeyEventType.KeyDown && e.nativeKeyEvent.repeatCount == 0 && symbol != null) type(symbol)
                    return@onPreviewKeyEvent true
                }
                if (e.type != KeyEventType.KeyDown || arrows == null) return@onPreviewKeyEvent false
                when (code) {
                    in NativeKeyEvent.KEYCODE_0..NativeKeyEvent.KEYCODE_9 -> { type('0' + (code - NativeKeyEvent.KEYCODE_0)); true }
                    NativeKeyEvent.KEYCODE_DEL -> { erase(); true }
                    else -> false
                }
            },
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier
                .consumeTaps()
                .liquidGlass(28.dp)
                .padding(horizontal = 36.dp, vertical = 28.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            BasicText(stringResource(title), style = Type.Title)
            Spacer(Modifier.height(6.dp))
            BasicText(
                error ?: stringResource(hint),
                style = Type.Caption.copy(color = if (error != null) Palette.Red else Palette.SecondaryLabel, textAlign = TextAlign.Center),
                modifier = Modifier.widthIn(max = 340.dp),
            )
            if (arrows == null) {
                Spacer(Modifier.height(22.dp))
                Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    PinKey(onClick = { arrows = false }, focusRequester = choice, width = 150.dp) { fg ->
                        BasicText(stringResource(R.string.pin_kind_digits), style = Type.Headline.copy(color = fg))
                    }
                    PinKey(onClick = { arrows = true }, width = 150.dp) { fg ->
                        BasicText(stringResource(R.string.pin_kind_arrows), style = Type.Headline.copy(color = fg))
                    }
                }
                return@Column
            }
            Spacer(Modifier.height(18.dp))
            // The dots start at the shortest accepted PIN and grow one per character past it, so a
            // range shows both "at least this many" and how far the entry has got.
            val dots = maxOf(minLength, entered.length.coerceAtMost(maxLength))
            // Focus sits on the dots while arrows are typed, so the keys reach the panel.
            Row(
                horizontalArrangement = Arrangement.spacedBy(12.dp),
                modifier = if (arrows == true) Modifier.focusRequester(pad).focusable() else Modifier,
            ) {
                repeat(dots) { i ->
                    Box(
                        Modifier.size(14.dp).clip(CircleShape)
                            .background(if (i < entered.length) Palette.Label else Color.Transparent)
                            .border(1.5.dp, Palette.Label, CircleShape),
                    )
                }
            }
            if (arrows == true) return@Column
            Spacer(Modifier.height(22.dp))
            val keys = listOf("123", "456", "789", " 0<")
            // Centred, not left: the OK key below is four keys wide while the digit rows are three,
            // so a start-aligned column pushed the digits off to the left by half a key.
            Column(
                verticalArrangement = Arrangement.spacedBy(12.dp),
                horizontalAlignment = Alignment.CenterHorizontally,
            ) {
                keys.forEach { row ->
                    Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        row.forEach { key ->
                            when (key) {
                                ' ' -> Spacer(Modifier.size(64.dp, 48.dp))
                                '<' -> PinKey(onClick = ::erase) { fg -> GlyphIcon(Glyph.ChevronLeft, fg, 18.dp) }
                                else -> PinKey(onClick = { type(key) }, focusRequester = if (key == '1') one else null) { fg ->
                                    BasicText(key.toString(), style = Type.Title.copy(color = fg))
                                }
                            }
                        }
                    }
                }
                // A range cannot tell its last character from a shorter entry, so it says when it
                // is done. It is the one wide key, under the keypad, where a remote gets to it by
                // going down from 0.
                if (!fixed) {
                    val enough = entered.length >= minLength
                    PinKey(
                        onClick = {
                            // Reachable at any length: a key that only appears once the entry is
                            // long enough would leave the remote with nothing to move to down
                            // here — the trap this keypad was once reported in (focus stuck on
                            // the 0 key with no way onward and no word of why). Too short says
                            // so instead.
                            if (enough) complete(entered) else error = tooShort
                        },
                        width = 64.dp * 4 + 12.dp * 3,
                        dimmed = !enough,
                        accent = true,
                    ) { fg -> BasicText(stringResource(R.string.pin_ok), style = Type.Headline.copy(color = fg)) }
                }
            }
            // Under the keys, so it is the last thing going down reaches: a way out that sits in
            // front of the keypad would be pressed by mistake far more often than it is wanted.
            if (footer != null) {
                Spacer(Modifier.height(14.dp))
                footer()
            }
        }
    }
}

/** How an arrow key is kept in an arrow PIN. */
private val ARROWS = mapOf(
    NativeKeyEvent.KEYCODE_DPAD_UP to 'U',
    NativeKeyEvent.KEYCODE_DPAD_DOWN to 'D',
    NativeKeyEvent.KEYCODE_DPAD_LEFT to 'L',
    NativeKeyEvent.KEYCODE_DPAD_RIGHT to 'R',
)

@Composable
private fun PinKey(
    onClick: () -> Unit,
    focusRequester: FocusRequester? = null,
    width: Dp = 64.dp,
    enabled: Boolean = true,
    dimmed: Boolean = false,
    accent: Boolean = false,
    content: @Composable (Color) -> Unit,
) {
    Pressable(onClick = onClick, enabled = enabled, focusRequester = focusRequester, focusedScale = 1.08f) { state ->
        // The OK key is the only green one, and dims rather than hiding while the entry is too
        // short: a key that comes and goes moves the others under the remote's focus. It stays
        // focusable either way, so the remote can always reach the bottom of the keypad.
        val rest = if (accent) Palette.Green.copy(alpha = if (dimmed) 0.35f else 1f) else Palette.Fill
        val bg by animateColorAsState(if (state.focused) Palette.Focus else rest, tween(140, easing = Motion.EaseOut), label = "pin-key")
        Box(Modifier.size(width, 48.dp).clip(RoundedCornerShape(12.dp)).background(bg), contentAlignment = Alignment.Center) {
            content(if (state.focused) Palette.OnFocus else Palette.Label)
        }
    }
}

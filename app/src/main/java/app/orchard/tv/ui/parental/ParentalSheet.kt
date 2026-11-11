package app.orchard.tv.ui.parental

import android.view.KeyEvent as NativeKeyEvent
import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.core.tween
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
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
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.focus.onFocusChanged
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.input.key.KeyEvent
import androidx.compose.ui.input.key.KeyEventType
import androidx.compose.ui.input.key.type
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.orchard.tv.R
import app.orchard.tv.parental.Parental
import app.orchard.tv.parental.ParentalStore
import app.orchard.tv.parental.ParentalStore.Limits
import app.orchard.tv.parental.PlaybackSignal
import app.orchard.tv.ui.components.Pressable
import app.orchard.tv.ui.components.consumeTaps
import app.orchard.tv.ui.components.liquidGlass
import app.orchard.tv.ui.home.PinMode
import app.orchard.tv.ui.home.PinPad
import app.orchard.tv.ui.theme.Glyph
import app.orchard.tv.ui.theme.GlyphIcon
import app.orchard.tv.ui.theme.Motion
import app.orchard.tv.ui.theme.Palette
import app.orchard.tv.ui.theme.Type

/**
 * The parental module's settings page (docs/PARENTAL.md, 界面), and the door in front of it.
 *
 * The door is the password: nothing is drawn until it is right, and the password is asked for again
 * every time the page is opened — leaving it is enough to lock it (spec, 密码: 每次离开模块即失效).
 * The first visit has no parental password yet, so it asks for one instead of checking one.
 *
 * The door also carries the way back in for a parent who has forgotten the password (spec, 忘记密码):
 * 修改密码 under the keypad asks for the administrator phrase instead and then sets a new one. Without
 * it a forgotten password could only be cleared over ADB, which is not something to ask of a parent.
 *
 * The page itself follows the prototype in `docs/prototype/parental.html`, including the two rules
 * that are easy to get wrong:
 *
 *  - **0 h 0 min is "no limit", and must stay reachable on the remote.** The hour half shows 不限
 *    rather than hiding the row, and → out of it gives a quarter of an hour — not an hour, which
 *    would make 15 minutes three presses away. See [Limits.withDailyHours].
 *  - **With no session limit the interval never triggers**, so its row is greyed and cannot take
 *    focus, with a note saying why: a limit the parent set that silently never fires is worse than
 *    no limit at all (spec, 设置项).
 *
 * Every change is written as it is made ([Parental.applyLimits]) rather than on the way out: the
 * spec wants a new budget to count against today's usage at once.
 */
@Composable
fun ParentalSheet(
    visible: Boolean,
    onDismiss: () -> Unit,
    /** Which of the three signals is feeding the counters, as of the last tick. */
    signal: PlaybackSignal.Level? = null,
) {
    val context = LocalContext.current
    var stage by remember { mutableStateOf(Stage.Gate) }
    // Where the way in through the administrator phrase was started from, so backing out of it
    // returns there rather than always to the door.
    var adminFrom by remember { mutableStateOf(Stage.Gate) }

    LaunchedEffect(visible) {
        if (!visible) return@LaunchedEffect
        // Reading the settings and today's counters; safe to call more than once.
        Parental.init(context)
        stage = if (Parental.hasPin) Stage.Gate else Stage.Create
    }

    // Only while the page itself is up: the pad brings its own, and two would fight for Back.
    BackHandler(enabled = visible && stage == Stage.Settings, onBack = onDismiss)

    AnimatedVisibility(
        visible = visible && stage == Stage.Settings,
        enter = fadeIn(tween(220, easing = Motion.EaseOut)),
        exit = fadeOut(tween(180, easing = Motion.EaseOut)),
        modifier = Modifier.fillMaxSize(),
    ) {
        SettingsPage(
            ui = Parental.ui,
            signal = signal,
            onLimits = { Parental.applyLimits(it) },
            onChangePin = { adminFrom = Stage.Settings; stage = Stage.Admin },
            onExit = onDismiss,
        )
    }

    // One pad per stage, all with the module's own length: 8 to 12 digits cannot submit itself, so
    // each one grows the OK key (PinPad, 区间模式).
    ParentalPad(
        visible = visible && stage == Stage.Gate,
        mode = PinMode.Unlock,
        hint = R.string.parental_pin_hint,
        verify = { Parental.verifyPin(it) },
        onDone = { stage = Stage.Settings },
        onCancel = onDismiss,
        // The last thing going down from the keypad, so it is found when wanted and not before.
        footer = { ChangePasswordLink(onClick = { adminFrom = Stage.Gate; stage = Stage.Admin }) },
    )
    ParentalPad(
        visible = visible && stage == Stage.Create,
        mode = PinMode.Create,
        hint = R.string.parental_pin_new_hint,
        onSave = { Parental.setPin(it) },
        onDone = { stage = Stage.Settings },
        onCancel = onDismiss,
    )
    // Changing the password, from either door (spec, 密码: 修改密码; 忘记密码). The administrator
    // phrase is what it asks for, not the password in use: the password opens the page, the phrase is
    // what can replace it — otherwise a child who learns the password could shut the parent out.
    // Nine digits, so it is typed on this same pad; a phrase with letters could never be entered.
    ParentalPad(
        visible = visible && stage == Stage.Admin,
        mode = PinMode.Unlock,
        hint = R.string.parental_admin_hint,
        verify = { Parental.verifyAdmin(it) },
        wrongOverride = R.string.parental_admin_wrong,
        onDone = { stage = Stage.ResetPin },
        onCancel = { stage = adminFrom },
    )
    // The new password is the same pad as a first visit; where it goes afterwards is not. Cancelling
    // returns to wherever the phrase was asked from, so the page is not lost by a change of mind.
    ParentalPad(
        visible = visible && stage == Stage.ResetPin,
        mode = PinMode.Create,
        hint = R.string.parental_pin_new_hint,
        onSave = { Parental.setPin(it) },
        onDone = { stage = Stage.Settings },
        onCancel = { stage = adminFrom },
    )
}

/**
 * Where the module is: the door, the page, and the one way past the door for a change — the
 * administrator phrase, which opens the new-password pad ([ResetPin]).
 */
private enum class Stage { Gate, Create, Admin, ResetPin, Settings }

/**
 * [PinPad] with the parental module's rules fixed: 8 to 12 digits, no arrow sequences (an arrow
 * sequence can only be four long), and the password kept in the module's own store.
 */
@Composable
private fun ParentalPad(
    visible: Boolean,
    mode: PinMode,
    hint: Int,
    verify: ((String) -> Boolean)? = null,
    onSave: ((String) -> Unit)? = null,
    wrongOverride: Int? = null,
    footer: (@Composable () -> Unit)? = null,
    onDone: () -> Unit,
    onCancel: () -> Unit,
) = PinPad(
    visible = visible,
    mode = mode,
    minLength = MIN_PIN,
    maxLength = MAX_PIN,
    allowArrows = false,
    hintOverride = hint,
    verify = verify,
    onSave = onSave,
    wrongOverride = wrongOverride,
    footer = footer,
    onDone = onDone,
    onCancel = onCancel,
)

/**
 * 修改密码, under the keypad of the door. A remote reaches it by going down past the last row, which is
 * where a way out belongs: pressing it by accident opens a page that asks for the administrator phrase,
 * so an accidental press costs nothing.
 */
@Composable
private fun ChangePasswordLink(onClick: () -> Unit) {
    Pressable(onClick = onClick, focusedScale = 1.04f) { state ->
        val bg by animateColorAsState(
            if (state.focused) Palette.Focus else Color.Transparent,
            tween(140, easing = Motion.EaseOut), label = "change-pin-link",
        )
        val fg = if (state.focused) Palette.OnFocus else Palette.SecondaryLabel
        Row(
            Modifier.clip(RoundedCornerShape(10.dp)).background(bg).height(32.dp).padding(horizontal = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(6.dp),
        ) {
            BasicText(stringResource(R.string.parental_change_pin), style = Type.Body.copy(color = fg))
            GlyphIcon(Glyph.ChevronRight, fg.copy(alpha = 0.5f), 14.dp)
        }
    }
}

@Composable
private fun SettingsPage(
    ui: Parental.Ui,
    signal: PlaybackSignal.Level?,
    onLimits: (Limits) -> Unit,
    onChangePin: () -> Unit,
    onExit: () -> Unit,
) {
    // The note under the cards follows the row that has focus, as in tvOS Settings. The daily row
    // is the only one whose arrows do something other than the obvious, so its note is the one
    // drawn in the brighter grey.
    var note by remember { mutableStateOf(R.string.parental_note_daily) }
    var strong by remember { mutableStateOf(true) }
    // 严格模式 and its ticket length, read when the page is opened. Written straight to the store as
    // the row is used — the guard and the clock read the same two values, so a page-local copy would
    // only let the two drift.
    val context = LocalContext.current
    var strict by remember { mutableStateOf(ParentalStore.strictWatch(context)) }
    var ticketMinutes by remember { mutableStateOf(ParentalStore.ticketMinutes(context)) }
    val dailyFocus = remember { FocusRequester() }
    // Entering puts focus on 每日观看时长, so the page is usable without hunting for a first control.
    LaunchedEffect(Unit) {
        withFrameNanos { }
        runCatching { dailyFocus.requestFocus() }
    }

    Box(
        Modifier.fillMaxSize().background(Color.Black.copy(alpha = 0.45f)),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier
                .width(660.dp)
                // Taps on the glass must not reach Home underneath; the page closes on Back or
                // on 退出家长模块, not on a stray tap (unlike the pad, which cancels on one).
                .consumeTaps()
                .liquidGlass(28.dp)
                .padding(horizontal = 26.dp, vertical = 24.dp),
        ) {
            PageHeader(ui)
            Spacer(Modifier.height(16.dp))

            Group {
                DailyRow(
                    minutes = ui.limits.dailyMinutes,
                    focusRequester = dailyFocus,
                    onChange = { onLimits(ui.limits.copy(dailyMinutes = it)) },
                    onFocused = { note = R.string.parental_note_daily; strong = true },
                )
                SettingRow(R.string.parental_session, first = false) {
                    val text = if (ui.limits.sessionMinutes == 0) {
                        stringResource(R.string.parental_unlimited)
                    } else {
                        stringResource(R.string.parental_minutes, ui.limits.sessionMinutes)
                    }
                    Stepper(text = text, enabled = true, onAdjust = { onLimits(ui.limits.withSession(it)) }) {
                        note = R.string.parental_note_session
                        strong = false
                    }
                }
                // Greyed and unfocusable while the session time is unlimited: there would be
                // nothing for the interval to follow (spec, 设置项).
                val intervalOff = ui.limits.sessionMinutes == 0
                SettingRow(R.string.parental_interval, first = false, dimmed = intervalOff) {
                    Stepper(
                        text = stringResource(R.string.parental_minutes, ui.limits.intervalMinutes),
                        enabled = !intervalOff,
                        onAdjust = { onLimits(ui.limits.withInterval(it)) },
                    ) {
                        note = if (intervalOff) R.string.parental_note_interval_off else R.string.parental_note_interval
                        strong = false
                    }
                }
                // 严格模式 (docs/PARENTAL.md, 严格模式): a video app needs the password too, and the
                // password hands out a ticket the clock follows. Off by default — 记账 suits a home
                // where nobody is there to type it, and this one costs exactly that.
                ActionRow(
                    label = R.string.parental_strict,
                    value = stringResource(if (strict) R.string.parental_on else R.string.parental_off),
                    first = false,
                    onClick = {
                        strict = !strict
                        ParentalStore.setStrictWatch(context, strict)
                    },
                ) { note = R.string.parental_note_strict; strong = false }
                // Greyed while the mode is off: a ticket length nothing would ever use is noise.
                SettingRow(R.string.parental_ticket, first = false, dimmed = !strict) {
                    Stepper(
                        text = stringResource(R.string.parental_minutes, ticketMinutes),
                        enabled = strict,
                        onAdjust = { delta ->
                            val choices = ParentalStore.TICKET_CHOICES
                            val next = choices[(choices.indexOf(ticketMinutes).coerceAtLeast(0) + delta)
                                .coerceIn(0, choices.lastIndex)]
                            ticketMinutes = next
                            ParentalStore.setTicketMinutes(context, next)
                        },
                    ) {
                        note = R.string.parental_note_ticket
                        strong = false
                    }
                }
            }

            Spacer(Modifier.height(10.dp))

            Group {
                ActionRow(
                    label = R.string.parental_change_pin,
                    value = stringResource(R.string.parental_change_pin_value),
                    first = true,
                    onClick = onChangePin,
                ) { note = R.string.parental_note_change_pin; strong = false }
                ActionRow(
                    label = R.string.parental_exit,
                    value = stringResource(R.string.parental_exit_value),
                    first = false,
                    onClick = onExit,
                ) { note = R.string.parental_note_exit; strong = false }
            }

            // Two lines of room, so the cards above do not shift as the note changes. The height is
            // outside the padding: two 11sp lines need the 34dp the padding leaves.
            Box(Modifier.fillMaxWidth().height(42.dp).padding(start = 4.dp, top = 8.dp)) {
                BasicText(
                    stringResource(note),
                    style = Type.Caption.copy(color = if (strong) Palette.SecondaryLabel else Palette.TertiaryLabel),
                    maxLines = 2,
                    overflow = TextOverflow.Ellipsis,
                )
            }

            // Which of the three signals is feeding the counters: without this line a parent has no
            // way to tell whether the limit is being counted exactly or roughly (spec, 计时).
            if (signal != null) {
                BasicText(
                    stringResource(R.string.parental_signal, stringResource(signalText(signal))),
                    style = Type.Caption.copy(color = Palette.TertiaryLabel),
                    modifier = Modifier.padding(start = 4.dp),
                )
            }
        }
    }
}

/** The lock, the page's name, and today's two readings. */
@Composable
private fun PageHeader(ui: Parental.Ui) {
    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
        Box(
            Modifier.size(38.dp).clip(RoundedCornerShape(12.dp)).background(Palette.Orange.copy(alpha = 0.16f)),
            contentAlignment = Alignment.Center,
        ) {
            GlyphIcon(Glyph.Shield, Palette.Orange, 19.dp)
        }
        Spacer(Modifier.width(14.dp))
        Column(Modifier.weight(1f)) {
            BasicText(stringResource(R.string.parental_title), style = Type.Title)
            Spacer(Modifier.height(2.dp))
            BasicText(stringResource(R.string.parental_subtitle), style = Type.Caption, maxLines = 1)
        }
        Column(horizontalAlignment = Alignment.End) {
            Reading(R.string.parental_used_today, durationText(ui.dailyUsedMs), null)
            Spacer(Modifier.height(3.dp))
            // With no session limit there is no "/ 45 分钟" to show, and "0 分钟" would read as a
            // budget of zero.
            val limit = if (ui.sessionBudgetMs == 0L) stringResource(R.string.parental_unlimited) else durationText(ui.sessionBudgetMs)
            Reading(R.string.parental_session_used, durationText(ui.sessionUsedMs), limit)
        }
    }
}

/** One reading: its name in grey, its number in white, and what it is out of, if anything. */
@Composable
private fun Reading(label: Int, value: String, of: String?) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        BasicText(stringResource(label), style = Type.Caption)
        Spacer(Modifier.width(6.dp))
        BasicText(value, style = Type.Caption.copy(color = Palette.Label))
        if (of != null) {
            Spacer(Modifier.width(6.dp))
            BasicText(stringResource(R.string.parental_of, of), style = Type.Caption.copy(color = Palette.TertiaryLabel))
        }
    }
}

/**
 * 每日观看时长: hours and minutes in one control, as the spec insists (its keys are unlike every
 * other row's). Left and right move the value, up and down — and OK, which is easier to press on a
 * remote — move between the two halves. Because left and right are taken, only one half is white at
 * a time, and the note below says how it works.
 */
@Composable
private fun DailyRow(
    minutes: Int,
    focusRequester: FocusRequester,
    onChange: (Int) -> Unit,
    onFocused: () -> Unit,
) {
    val hours = minutes / 60
    val mins = minutes % 60
    val minutesFocus = remember { FocusRequester() }
    SettingRow(R.string.parental_daily, first = true) {
        Row(
            Modifier.clip(RoundedCornerShape(11.dp)).background(Palette.Fill).padding(2.dp),
            horizontalArrangement = Arrangement.spacedBy(2.dp),
        ) {
            StepSegment(
                // 0 h 0 min says what it means here rather than two zeroes.
                text = if (minutes == 0) stringResource(R.string.parental_unlimited) else stringResource(R.string.parental_hours, hours),
                focusRequester = focusRequester,
                onFocused = onFocused,
                onKey = { e ->
                    val down = e.type == KeyEventType.KeyDown
                    when (e.nativeKeyEvent.keyCode) {
                        NativeKeyEvent.KEYCODE_DPAD_LEFT -> { if (down) onChange(stepHours(minutes, -1)); true }
                        NativeKeyEvent.KEYCODE_DPAD_RIGHT -> { if (down) onChange(stepHours(minutes, 1)); true }
                        // Down and OK go to the minutes half when there is one; at 不限 there is
                        // none, so down falls through to the row below.
                        NativeKeyEvent.KEYCODE_DPAD_DOWN -> if (minutes == 0) false else { if (down) runCatching { minutesFocus.requestFocus() }; true }
                        NativeKeyEvent.KEYCODE_DPAD_CENTER, NativeKeyEvent.KEYCODE_ENTER, NativeKeyEvent.KEYCODE_NUMPAD_ENTER ->
                            if (minutes == 0) true else { if (down) runCatching { minutesFocus.requestFocus() }; true }
                        else -> false
                    }
                },
            )
            if (minutes > 0) {
                StepSegment(
                    text = stringResource(R.string.parental_minutes, mins),
                    focusRequester = minutesFocus,
                    onFocused = onFocused,
                    onKey = { e ->
                        val down = e.type == KeyEventType.KeyDown
                        when (e.nativeKeyEvent.keyCode) {
                            NativeKeyEvent.KEYCODE_DPAD_LEFT -> { if (down) onChange(stepMinutes(minutes, -1)); true }
                            NativeKeyEvent.KEYCODE_DPAD_RIGHT -> { if (down) onChange(stepMinutes(minutes, 1)); true }
                            // Up goes back to the hours half, as the spec's key map says; down is
                            // left alone so focus can leave the row downwards.
                            NativeKeyEvent.KEYCODE_DPAD_UP ->
                                { if (down) runCatching { focusRequester.requestFocus() }; true }
                            NativeKeyEvent.KEYCODE_DPAD_CENTER, NativeKeyEvent.KEYCODE_ENTER, NativeKeyEvent.KEYCODE_NUMPAD_ENTER ->
                                { if (down) runCatching { focusRequester.requestFocus() }; true }
                            else -> false
                        }
                    },
                )
            }
        }
    }
}

/** Half of the day's control: arrows on both sides of the value, white while it holds the focus. */
@Composable
private fun StepSegment(
    text: String,
    focusRequester: FocusRequester,
    onFocused: () -> Unit,
    onKey: (KeyEvent) -> Boolean,
) {
    // No scale on focus: the segment turning white is the cue, and growing it would push the other
    // half of the pair around.
    Pressable(
        onClick = {},
        focusRequester = focusRequester,
        focusedScale = 1f,
        onKey = onKey,
    ) { state ->
        LaunchedEffect(state.focused) { if (state.focused) onFocused() }
        val bg by animateColorAsState(
            if (state.focused) Palette.Focus else Color.Transparent,
            tween(140, easing = Motion.EaseOut), label = "daily-segment",
        )
        val fg = if (state.focused) Palette.OnFocus else Palette.Label
        Row(
            Modifier.clip(RoundedCornerShape(9.dp)).background(bg).height(30.dp).padding(horizontal = 3.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            GlyphIcon(Glyph.ChevronLeft, fg.copy(alpha = 0.5f), 12.dp)
            BasicText(
                text,
                style = Type.Body.copy(color = fg, textAlign = TextAlign.Center),
                modifier = Modifier.width(68.dp),
                maxLines = 1,
            )
            GlyphIcon(Glyph.ChevronRight, fg.copy(alpha = 0.5f), 12.dp)
        }
    }
}

/**
 * A value that only steps: left and right change it, and OK does nothing — the same key it is on
 * steppers elsewhere on this TV. Greyed and unfocusable when [enabled] is false.
 */
@Composable
private fun Stepper(
    text: String,
    enabled: Boolean,
    onAdjust: (Int) -> Unit,
    onFocused: () -> Unit,
) {
    Pressable(
        onClick = {},
        enabled = enabled,
        focusedScale = 1f,
        onKey = { e ->
            val down = e.type == KeyEventType.KeyDown
            when (e.nativeKeyEvent.keyCode) {
                NativeKeyEvent.KEYCODE_DPAD_LEFT -> { if (down) onAdjust(-1); true }
                NativeKeyEvent.KEYCODE_DPAD_RIGHT -> { if (down) onAdjust(1); true }
                // Taken so that a press of OK does not click an empty control.
                NativeKeyEvent.KEYCODE_DPAD_CENTER, NativeKeyEvent.KEYCODE_ENTER, NativeKeyEvent.KEYCODE_NUMPAD_ENTER -> true
                else -> false
            }
        },
    ) { state ->
        LaunchedEffect(state.focused) { if (state.focused) onFocused() }
        val bg by animateColorAsState(
            when {
                state.focused -> Palette.Focus
                enabled -> Palette.Fill
                else -> Palette.Fill.copy(alpha = 0.45f)
            },
            tween(140, easing = Motion.EaseOut), label = "stepper",
        )
        val fg = when {
            state.focused -> Palette.OnFocus
            enabled -> Palette.Label
            else -> Palette.TertiaryLabel
        }
        Row(
            Modifier.clip(RoundedCornerShape(10.dp)).background(bg).height(34.dp).padding(horizontal = 6.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            GlyphIcon(Glyph.ChevronLeft, fg.copy(alpha = if (enabled) 0.5f else 0.25f), 12.dp)
            BasicText(
                text,
                style = Type.Body.copy(color = fg, textAlign = TextAlign.Center),
                modifier = Modifier.width(74.dp),
                maxLines = 1,
            )
            GlyphIcon(Glyph.ChevronRight, fg.copy(alpha = if (enabled) 0.5f else 0.25f), 12.dp)
        }
    }
}

/** A row that goes somewhere rather than setting a value. */
@Composable
private fun ActionRow(
    label: Int,
    value: String,
    first: Boolean,
    onClick: () -> Unit,
    onFocused: () -> Unit,
) {
    if (!first) Hairline()
    Row(
        Modifier
            .fillMaxWidth()
            .height(54.dp)
            .padding(start = 18.dp, end = 14.dp)
            .onFocusChanged { if (it.hasFocus) onFocused() },
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BasicText(stringResource(label), style = Type.Body, modifier = Modifier.weight(1f), maxLines = 1)
        Pressable(onClick = onClick, focusedScale = 1.04f) { state ->
            val bg by animateColorAsState(
                if (state.focused) Palette.Focus else Palette.Fill,
                tween(140, easing = Motion.EaseOut), label = "action-row",
            )
            val fg = if (state.focused) Palette.OnFocus else Palette.Label
            Row(
                Modifier.clip(RoundedCornerShape(10.dp)).background(bg).height(34.dp).padding(start = 12.dp, end = 8.dp),
                verticalAlignment = Alignment.CenterVertically,
                horizontalArrangement = Arrangement.spacedBy(6.dp),
            ) {
                BasicText(value, style = Type.Body.copy(color = fg))
                GlyphIcon(Glyph.ChevronRight, fg.copy(alpha = 0.5f), 14.dp)
            }
        }
    }
}

/** A row: the setting's name on the left, its control on the right, a hairline above all but the first. */
@Composable
private fun SettingRow(
    label: Int,
    first: Boolean,
    dimmed: Boolean = false,
    control: @Composable () -> Unit,
) {
    if (!first) Hairline()
    Row(
        Modifier.fillMaxWidth().height(54.dp).padding(start = 18.dp, end = 14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        BasicText(
            stringResource(label),
            style = Type.Body.copy(color = if (dimmed) Palette.TertiaryLabel else Palette.Label),
            modifier = Modifier.weight(1f),
            maxLines = 1,
        )
        control()
    }
}

@Composable
private fun Hairline() {
    Box(Modifier.padding(start = 18.dp).fillMaxWidth().height(1.dp).background(Palette.Hairline))
}

/** A card of rows, as in tvOS Settings: one surface, hairlines between the rows. */
@Composable
private fun Group(rows: @Composable ColumnScope.() -> Unit) {
    Column(
        Modifier.fillMaxWidth().clip(RoundedCornerShape(16.dp)).background(Color.White.copy(alpha = 0.05f)),
        content = rows,
    )
}

// ---- the numbers ----

/**
 * ← or → on the hours half: a whole hour, except out of 不限, where → gives the first quarter of an
 * hour. The rounding rules live in [Limits] with their tests; these two only put them in order.
 */
private fun stepHours(minutes: Int, dir: Int): Int = Limits(dailyMinutes = minutes).withDailyHours(dir).dailyMinutes

private fun stepMinutes(minutes: Int, dir: Int): Int = Limits(dailyMinutes = minutes).withDailyMinutes(dir).dailyMinutes

/** The three signals as the page names them: how exactly the count can be trusted. */
@Composable
private fun signalText(level: PlaybackSignal.Level): Int = when (level) {
    PlaybackSignal.Level.Precise -> R.string.parental_signal_precise
    PlaybackSignal.Level.Foreground -> R.string.parental_signal_foreground
    PlaybackSignal.Level.Usage -> R.string.parental_signal_usage
    PlaybackSignal.Level.Ticket -> R.string.parental_signal_ticket
}

/** 8 to 12 digits: the remote has nothing but number keys, and the pad grows an OK key for the range. */
internal const val MIN_PIN = 8
internal const val MAX_PIN = 12

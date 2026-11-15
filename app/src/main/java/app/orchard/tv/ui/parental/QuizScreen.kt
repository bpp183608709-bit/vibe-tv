package app.orchard.tv.ui.parental

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Column
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.orchard.tv.R
import app.orchard.tv.quiz.QuizClient
import app.orchard.tv.quiz.QuizOption
import app.orchard.tv.quiz.QuizQuestion
import app.orchard.tv.quiz.QuizReview
import app.orchard.tv.quiz.QuizRun
import app.orchard.tv.quiz.QuizTicket
import app.orchard.tv.ui.components.Pressable
import app.orchard.tv.ui.theme.Glyph
import app.orchard.tv.ui.theme.GlyphIcon
import app.orchard.tv.ui.theme.Palette
import app.orchard.tv.ui.theme.Type
import kotlinx.coroutines.delay

/**
 * The quiz page: the third way past the daily limit (docs/PARENTAL.md, 做题换时长).
 *
 * It is a pane of the lock, not an activity of its own. That is a deliberate change from the spec's
 * wording ("做题 Activity"): the lock is what puts itself back in front whenever a limit is used up,
 * and a separate activity would be pushed behind it a second after it opened. The one screen that
 * must not be covered is the one doing the covering.
 *
 * Remote only, as the rest of the lock is: up and down move between options, OK chooses, and Back
 * goes back to the lock screen. A fill-in answer is typed on the keypad, which is also how a
 * television without a number row is used.
 *
 * **The page is a test, and looks like one** (2026-11-13): the group's progress is a row of segments
 * under the header rather than a word, the options carry their letters in boxes so a child can say
 * "B" out loud, and the verdict is a ring filled to the score. All of it is the module's own
 * language ([Ink], [CountdownRing]) — the quiz is what the lock offers, not a different place.
 *
 * Nothing here decides anything: the group, the grading and the minutes all come from [QuizRun].
 */
@Composable
fun QuizScreen(run: QuizRun, onExit: () -> Unit, onContinue: () -> Unit) {
    // Back leaves the quiz and lands on the lock again — the group is abandoned, which the server
    // already counted as an attempt. The activity's own Back callback keeps the lock itself in
    // place; this one is added later, so it wins while the page is up. The review page adds one more
    // on top, which is why it comes after this one in the tree.
    BackHandler(enabled = true) { onExit() }

    // One clock for the group's ten minutes and the child's three. It only asks; QuizRun answers.
    var now by remember { mutableStateOf(System.currentTimeMillis()) }
    LaunchedEffect(Unit) {
        while (true) {
            delay(1_000L)
            now = System.currentTimeMillis()
            if (run.stage is QuizRun.Stage.Asking && (run.expired(now) || run.idle(now))) {
                onExit()
                break
            }
        }
    }

    // Which wrong answer is on screen, or null when the verdict is. The list comes from the grade
    // response, so it is always this group and never a page of history.
    var reviewAt by remember { mutableStateOf<Int?>(null) }
    BackHandler(enabled = reviewAt != null) { reviewAt = null }

    AmbientBackdrop {
        val at = reviewAt
        when (val stage = run.stage) {
            is QuizRun.Stage.Loading -> Notice(
                title = R.string.parental_quiz_loading,
                body = null,
                modifier = Modifier.align(Alignment.Center),
            )

            is QuizRun.Stage.Submitting -> Notice(
                title = R.string.parental_quiz_submitting,
                body = null,
                modifier = Modifier.align(Alignment.Center),
            )

            is QuizRun.Stage.Failed -> Failure(
                error = stage.error,
                retry = stage.retry,
                coolSec = ((run.cooldownUntil() - now) / 1_000L).toInt().coerceAtLeast(0),
                // A submission that never arrived is sent again as the same session; anything else
                // asks the server for a new group. Getting this backwards would cost the child an
                // attempt every time the Wi-Fi blinked at the wrong moment.
                onRetry = { if (stage.resubmit) run.retrySubmit() else run.start() },
                onExit = onExit,
            )

            is QuizRun.Stage.Asking -> Asking(
                run = run,
                stage = stage,
                leftSec = ((QuizRun.GROUP_LIMIT_MS - (now - run.startedAt)) / 1_000L).toInt(),
            )

            is QuizRun.Stage.Done -> {
                val mistakes = stage.verdict.review
                if (at != null && at in mistakes.indices) {
                    Review(
                        item = mistakes[at],
                        index = at,
                        count = mistakes.size,
                        onMove = { reviewAt = (at + it).coerceIn(0, mistakes.lastIndex) },
                        onBack = { reviewAt = null },
                    )
                } else {
                    Verdict(
                        stage = stage,
                        onContinue = onContinue,
                        onExit = onExit,
                        onReview = { reviewAt = 0 },
                    )
                }
            }
        }
    }
}

@Composable
private fun Asking(run: QuizRun, stage: QuizRun.Stage.Asking, leftSec: Int) {
    Column(Modifier.fillMaxSize().padding(horizontal = 96.dp, vertical = 20.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Chip(text = stringResource(R.string.parental_quiz_tag), tint = Palette.Green, glyph = Glyph.Kids)
            Spacer(Modifier.width(12.dp))
            BasicText(
                stringResource(R.string.parental_quiz_progress, stage.number, run.questionCount()),
                style = Type.Caption.copy(color = Palette.Label.copy(alpha = 0.44f), fontFeatureSettings = "tnum"),
            )
            Spacer(Modifier.weight(1f))
            // The group's own clock. Without it the page ending by itself looks like a bug.
            BasicText(
                stringResource(R.string.parental_quiz_left, clockText(leftSec.coerceAtLeast(0))),
                style = Type.Caption.copy(
                    color = if (leftSec <= 60) Palette.Green else Palette.SecondaryLabel,
                    fontFeatureSettings = "tnum",
                    fontWeight = FontWeight.W600,
                ),
            )
        }

        Spacer(Modifier.height(12.dp))
        // Where the child is in the group, as a row of segments: a count of ten is read at a glance,
        // "第 3 / 10 题" needs to be read. Both are here, one for each distance.
        QuestionBar(total = run.questionCount(), at = stage.number)

        // The question and its answers sit in the middle of what is left of the screen rather than
        // under the progress bar. Pinned to the top, a short stem and four options left a third of a
        // 540dp canvas empty under them — which on a television reads as a page that failed to load,
        // not as a page with room to spare. Centred, the block is where the eye already is.
        Column(
            Modifier.fillMaxWidth().weight(1f),
            verticalArrangement = Arrangement.Center,
        ) {
            Reveal(0) {
                BasicText(
                    stage.question.stem,
                    style = Type.Title.copy(fontSize = 24.sp, textAlign = TextAlign.Start),
                    maxLines = 3,
                )
            }
            Spacer(Modifier.height(18.dp))

            if (stage.question.isChoice) {
                Choices(run = run, stage = stage)
            } else {
                Fill(run = run, stage = stage)
            }
        }
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            // 没学过 is a marker the parent sees, not a way past the question (B7): the answer still
            // has to be given, and the server leaves a marked question out of the failure count.
            Footnote(
                text = stringResource(if (stage.unknown) R.string.parental_quiz_unknown_on else R.string.parental_quiz_unknown),
                tint = if (stage.unknown) Palette.Green else Palette.SecondaryLabel,
                onClick = { run.toggleUnknown() },
            )
            Spacer(Modifier.weight(1f))
            BasicText(
                stringResource(R.string.parental_quiz_exit_hint),
                style = Type.Caption.copy(color = Palette.Label.copy(alpha = 0.30f)),
            )
        }
    }
}

/**
 * The group as segments, one per question. A counted group can be long; past two dozen the segments
 * would be hairlines, so a plain meter takes over — the reading is still the same question.
 */
@Composable
private fun QuestionBar(total: Int, at: Int) {
    val shown = at.coerceIn(0, total.coerceAtLeast(0))
    if (total in 1..24) {
        Row(Modifier.fillMaxWidth().height(3.dp), horizontalArrangement = Arrangement.spacedBy(5.dp)) {
            repeat(total) { i ->
                val done = i + 1 < shown
                val current = i + 1 == shown
                Box(
                    Modifier
                        .weight(1f)
                        .fillMaxSize()
                        .clip(RoundedCornerShape(2.dp))
                        .background(
                            when {
                                current -> Palette.Green
                                done -> Palette.Green.copy(alpha = 0.42f)
                                else -> Ink.Track
                            },
                        ),
                )
            }
        }
    } else {
        Meter(fraction = if (total <= 0) 0f else shown.toFloat() / total, tint = Palette.Green)
    }
}

@Composable
private fun Choices(run: QuizRun, stage: QuizRun.Stage.Asking) {
    val first = remember(stage.index) { FocusRequester() }
    LaunchedEffect(stage.index) {
        withFrameNanos { }
        runCatching { first.requestFocus() }
    }
    // Five options are rare but possible; the rows give up a few dp each rather than the page
    // giving up its bottom line off the 540dp canvas.
    val dense = stage.question.options.size > 4
    Column(Modifier.width(620.dp), verticalArrangement = Arrangement.spacedBy(if (dense) 8.dp else 9.dp)) {
        stage.question.options.forEachIndexed { i, option ->
            Reveal(i.coerceAtMost(3)) {
                OptionRow(
                    option = option,
                    state = optionState(stage, option),
                    enabled = stage.feedback == null,
                    focusRequester = if (i == 0) first else null,
                    height = if (dense) 42.dp else 46.dp,
                    onClick = { run.choose(option.label) },
                )
            }
        }
    }
    Feedback(stage)
}

/** How one option is drawn: the chosen one carries the mark, the others fade once answered. */
private enum class OptionState { Idle, ChosenRight, ChosenWrong, Right, Faded }

private fun optionState(stage: QuizRun.Stage.Asking, option: QuizOption): OptionState {
    val feedback = stage.feedback ?: return OptionState.Idle
    return when {
        feedback is QuizRun.Feedback.Right && option.correct -> OptionState.ChosenRight
        feedback is QuizRun.Feedback.Wrong && option.label == stage.picked -> OptionState.ChosenWrong
        // After a wrong answer the right one is shown: knowing which was right is the whole point of
        // answering it a second time in a child's head.
        feedback is QuizRun.Feedback.Wrong && option.correct -> OptionState.Right
        else -> OptionState.Faded
    }
}

@Composable
private fun OptionRow(
    option: QuizOption,
    state: OptionState,
    enabled: Boolean,
    focusRequester: FocusRequester?,
    height: Dp = 46.dp,
    onClick: () -> Unit,
) {
    Pressable(onClick = onClick, enabled = enabled, focusRequester = focusRequester, focusedScale = 1.02f) { press ->
        val focused = press.focused
        val bg = when {
            focused -> Palette.Focus
            state == OptionState.ChosenRight -> Palette.Green.copy(alpha = 0.17f)
            state == OptionState.ChosenWrong -> Palette.Red.copy(alpha = 0.17f)
            state == OptionState.Right -> Palette.Green.copy(alpha = 0.08f)
            else -> Ink.Plate
        }
        val edge = when {
            focused -> Color.Transparent
            state == OptionState.ChosenRight || state == OptionState.Right -> Palette.Green.copy(alpha = 0.30f)
            state == OptionState.ChosenWrong -> Palette.Red.copy(alpha = 0.30f)
            else -> Ink.Edge
        }
        val fg = when {
            focused -> Palette.OnFocus
            state == OptionState.Faded -> Palette.Label.copy(alpha = 0.42f)
            else -> Palette.Label
        }
        // The letter sits in its own box, so B is something a child can point at rather than a
        // character at the start of a sentence.
        val letterFg = when {
            focused -> Palette.OnFocus
            state == OptionState.ChosenRight || state == OptionState.Right -> Palette.Green
            state == OptionState.ChosenWrong -> Palette.Red
            else -> Palette.SecondaryLabel
        }
        val letterBg = when {
            focused -> Palette.OnFocus.copy(alpha = 0.10f)
            state == OptionState.ChosenRight -> Palette.Green.copy(alpha = 0.18f)
            state == OptionState.ChosenWrong -> Palette.Red.copy(alpha = 0.18f)
            state == OptionState.Right -> Palette.Green.copy(alpha = 0.12f)
            else -> Color.White.copy(alpha = 0.07f)
        }
        Row(
            Modifier
                .fillMaxWidth()
                .height(height)
                .clip(RoundedCornerShape(13.dp))
                .background(bg)
                .border(1.dp, edge, RoundedCornerShape(13.dp))
                .padding(start = 9.dp, end = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(13.dp),
        ) {
            Box(
                Modifier.size(28.dp).clip(RoundedCornerShape(9.dp)).background(letterBg),
                contentAlignment = Alignment.Center,
            ) {
                BasicText(option.label, style = Type.Body.copy(color = letterFg, fontWeight = FontWeight.W700))
            }
            BasicText(
                option.text,
                style = Type.Body.copy(color = fg, fontWeight = FontWeight.W500),
                modifier = Modifier.weight(1f),
                maxLines = 2,
            )
            // The verdict on this option, only where there is one: the trailing edge is where a
            // child looks after answering.
            when (state) {
                OptionState.ChosenRight -> GlyphIcon(Glyph.Check, if (focused) Palette.OnFocus else Palette.Green, 16.dp)
                OptionState.ChosenWrong -> GlyphIcon(Glyph.Info, if (focused) Palette.OnFocus else Palette.Red, 16.dp)
                OptionState.Right -> GlyphIcon(Glyph.Check, Palette.Green.copy(alpha = 0.7f), 15.dp)
                else -> Unit
            }
        }
    }
}

@Composable
private fun Fill(run: QuizRun, stage: QuizRun.Stage.Asking) {
    var typed by remember(stage.index) { mutableStateOf("") }
    // The keypad is the only way in, so it takes focus as soon as a fill-in question arrives.
    val first = remember(stage.index) { FocusRequester() }
    LaunchedEffect(stage.index) {
        withFrameNanos { }
        runCatching { first.requestFocus() }
    }

    Row(verticalAlignment = Alignment.Top) {
        Column(Modifier.width(430.dp)) {
            Row(
                Modifier
                    .fillMaxWidth()
                    // The field lights its own edge as soon as there is an answer in it: the one
                    // piece of state on this screen a child cannot otherwise see.
                    .height(54.dp)
                    .clip(RoundedCornerShape(13.dp))
                    .background(Ink.Plate)
                    .border(
                        1.dp,
                        if (typed.isEmpty()) Ink.EdgeLit else Palette.Green.copy(alpha = 0.45f),
                        RoundedCornerShape(13.dp),
                    )
                    .padding(horizontal = 16.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                BasicText(
                    typed.ifEmpty { stringResource(R.string.parental_quiz_fill_hint) },
                    style = Type.Title.copy(
                        fontSize = 24.sp,
                        color = if (typed.isEmpty()) Palette.TertiaryLabel else Palette.Label,
                        fontFeatureSettings = "tnum",
                    ),
                )
            }
            Spacer(Modifier.height(10.dp))
            Keypad(
                enabled = stage.feedback == null,
                first = first,
                onKey = { typed += it; run.touch() },
                onBackspace = { typed = typed.dropLast(1); run.touch() },
                onDone = { run.type(typed) },
            )
        }
        Spacer(Modifier.width(26.dp))
        Column {
            Feedback(stage)
            if (stage.feedback == null && typed.isNotEmpty()) {
                Spacer(Modifier.height(10.dp))
                BasicText(
                    stringResource(R.string.parental_quiz_fill_ready),
                    style = Type.Caption.copy(color = Palette.Label.copy(alpha = 0.40f)),
                )
            }
        }
    }
}

/** Digits, a decimal point, a fraction bar, backspace and 确定 — 4 by 4, reachable with the D-pad. */
@Composable
private fun Keypad(enabled: Boolean, first: FocusRequester, onKey: (String) -> Unit, onBackspace: () -> Unit, onDone: () -> Unit) {
    // Null is a gap in the grid, so the keys keep their columns and 确定 sits under the operators
    // where a child's thumb expects it.
    val rows: List<List<Key?>> = listOf(
        listOf(Key("7"), Key("8"), Key("9"), Key(kind = KeyKind.Backspace)),
        listOf(Key("4"), Key("5"), Key("6"), Key(".")),
        listOf(Key("1"), Key("2"), Key("3"), Key("/")),
        listOf(Key("0"), null, null, Key(kind = KeyKind.Done)),
    )
    Column(verticalArrangement = Arrangement.spacedBy(7.dp)) {
        rows.forEachIndexed { r, row ->
            Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                row.forEachIndexed { c, key ->
                    if (key == null) {
                        Spacer(Modifier.width(82.dp).height(40.dp))
                    } else {
                        KeyCell(
                            key = key,
                            enabled = enabled,
                            focusRequester = if (r == 0 && c == 0) first else null,
                            onClick = {
                                when (key.kind) {
                                    KeyKind.Backspace -> onBackspace()
                                    KeyKind.Done -> onDone()
                                    KeyKind.Digit -> onKey(key.label)
                                }
                            },
                        )
                    }
                }
            }
        }
    }
}

private enum class KeyKind { Digit, Backspace, Done }

private data class Key(val label: String = "", val kind: KeyKind = KeyKind.Digit)

@Composable
private fun KeyCell(key: Key, enabled: Boolean, focusRequester: FocusRequester?, onClick: () -> Unit) {
    Pressable(onClick = onClick, enabled = enabled, focusRequester = focusRequester, focusedScale = 1.04f) { press ->
        val action = key.kind != KeyKind.Digit
        val done = key.kind == KeyKind.Done
        val label = when (key.kind) {
            KeyKind.Backspace -> stringResource(R.string.parental_quiz_key_back)
            KeyKind.Done -> stringResource(R.string.parental_quiz_key_done)
            KeyKind.Digit -> key.label
        }
        Box(
            Modifier
                .width(if (action) 100.dp else 82.dp)
                .height(40.dp)
                .clip(RoundedCornerShape(11.dp))
                .background(
                    when {
                        press.focused -> Palette.Focus
                        // 确定 is the one key that is always the right next press: it is filled with
                        // the accent rather than tinted by it, so it reads as a key and not a note.
                        done -> Palette.Green
                        else -> Ink.Plate
                    },
                )
                .then(
                    if (done && !press.focused) Modifier.border(1.dp, Palette.Green.copy(alpha = 0.5f), RoundedCornerShape(11.dp))
                    else Modifier,
                ),
            contentAlignment = Alignment.Center,
        ) {
            BasicText(
                label,
                style = Type.Body.copy(
                    color = when {
                        press.focused -> Palette.OnFocus
                        done -> Color(0xFF06230E)
                        else -> Palette.Label
                    },
                    fontSize = if (action) 13.sp else 18.sp,
                    fontWeight = FontWeight.W600,
                ),
            )
        }
    }
}

/** The line under the options: right, wrong (with the answer), or nothing while thinking. */
@Composable
private fun Feedback(stage: QuizRun.Stage.Asking) {
    val feedback = stage.feedback ?: return
    Spacer(Modifier.height(14.dp))
    val (text, colour) = when (feedback) {
        QuizRun.Feedback.Right -> stringResource(R.string.parental_quiz_right) to Palette.Green
        is QuizRun.Feedback.Wrong ->
            stringResource(R.string.parental_quiz_wrong, feedback.expected.orEmpty()) to Palette.Red
    }
    Row(verticalAlignment = Alignment.CenterVertically) {
        Row(
            Modifier
                .clip(RoundedCornerShape(999.dp))
                .background(colour.copy(alpha = 0.13f))
                .border(1.dp, colour.copy(alpha = 0.24f), RoundedCornerShape(999.dp))
                .padding(horizontal = 12.dp, vertical = 7.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(8.dp),
        ) {
            GlyphIcon(if (feedback is QuizRun.Feedback.Right) Glyph.Check else Glyph.Info, colour, 14.dp)
            BasicText(text, style = Type.Caption.copy(color = colour, fontWeight = FontWeight.W600))
        }
    }
}

/** A caption that can be pressed: the 没学过 marker. */
@Composable
private fun Footnote(text: String, tint: Color, onClick: () -> Unit) {
    Pressable(onClick = onClick, focusedScale = 1.05f) { press ->
        Box(
            Modifier
                .clip(RoundedCornerShape(9.dp))
                .background(if (press.focused) Palette.Focus else Ink.Plate)
                .border(1.dp, if (press.focused) Color.Transparent else Ink.Edge, RoundedCornerShape(9.dp))
                .padding(horizontal = 12.dp, vertical = 7.dp),
        ) {
            BasicText(
                text,
                style = Type.Caption.copy(color = if (press.focused) Palette.OnFocus else tint, fontWeight = FontWeight.W600),
            )
        }
    }
}

/** The verdict: the score, the one button worth pressing next, and the mistakes if there are any. */
@Composable
private fun Verdict(stage: QuizRun.Stage.Done, onContinue: () -> Unit, onExit: () -> Unit, onReview: () -> Unit) {
    val verdict = stage.verdict
    val first = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        withFrameNanos { }
        runCatching { first.requestFocus() }
    }
    val passed = verdict.passed
    // A pass is the accent; a fail is not an error, so it steps back to grey rather than shouting
    // in red at a child who has just missed the mark.
    val tint = if (passed) Palette.Green else Palette.SecondaryLabel
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.align(Alignment.Center).width(560.dp), horizontalAlignment = Alignment.CenterHorizontally) {
            // The ring is the score: how much of the group was answered right, before the number is
            // read. A pass fills it, a fail does not, and both look deliberate.
            Reveal(0) {
                CountdownRing(
                    progress = if (verdict.total <= 0) 0f else verdict.score.toFloat() / verdict.total,
                    tint = tint,
                    size = 118.dp,
                    stroke = 4.dp,
                ) {
                    GlyphIcon(if (passed) Glyph.Check else Glyph.Info, tint, 38.dp)
                }
            }
            Spacer(Modifier.height(16.dp))
            Reveal(1) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    BasicText(
                        stringResource(if (passed) R.string.parental_quiz_pass_title else R.string.parental_quiz_fail_title),
                        style = Type.LargeTitle.copy(fontSize = 28.sp, textAlign = TextAlign.Center),
                    )
                    Spacer(Modifier.height(6.dp))
                    BasicText(
                        stringResource(R.string.parental_quiz_score, verdict.score, verdict.total),
                        style = Type.Body.copy(color = Palette.SecondaryLabel, fontFeatureSettings = "tnum"),
                    )
                }
            }
            Spacer(Modifier.height(8.dp))
            val line = when {
                stage.awardMinutes > 0 -> stringResource(R.string.parental_quiz_award, stage.awardMinutes)
                passed && stage.refusal != null -> stringResource(R.string.parental_quiz_award_refused)
                passed && verdict.cappedByDailyLimit -> stringResource(R.string.parental_quiz_award_capped)
                else -> stringResource(R.string.parental_quiz_fail_why)
            }
            Reveal(2) {
                if (stage.awardMinutes > 0) {
                    Chip(text = line, tint = Palette.Green, glyph = Glyph.Check)
                } else {
                    BasicText(
                        line,
                        style = Type.Body.copy(color = Palette.SecondaryLabel, textAlign = TextAlign.Center),
                    )
                }
            }
            Spacer(Modifier.height(24.dp))
            val continueWatching = stage.awardMinutes > 0
            Reveal(3) {
                Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                    QuizButton(
                        label = if (continueWatching) R.string.parental_quiz_continue else R.string.parental_quiz_back,
                        focusRequester = first,
                        primary = continueWatching,
                        onClick = if (continueWatching) onContinue else onExit,
                    )
                    // 错题本, the child's version: this group only, and only when something was wrong.
                    // The phone still holds the seven-day book; a television is not where history lives.
                    if (verdict.review.isNotEmpty()) {
                        QuizButton(
                            label = R.string.parental_quiz_review,
                            labelArg = verdict.review.size,
                            primary = false,
                            onClick = onReview,
                        )
                    }
                }
            }
        }
        if (stage.refusal != null) {
            // The one case a parent has to act on: the ticket did not check out, so the minutes were
            // not added. The reason is a code because it is for the log, not for the child.
            BasicText(
                stringResource(R.string.parental_quiz_refused, stage.refusal.name.lowercase()),
                style = Type.Caption.copy(color = Palette.Red.copy(alpha = 0.8f), textAlign = TextAlign.Center),
                modifier = Modifier.align(Alignment.BottomCenter).padding(bottom = 24.dp),
            )
        }
    }
}

/**
 * One wrong answer, a screen at a time: subject and topic, the question, what the child answered,
 * what the right answer was, and why when the bank says.
 *
 * A page rather than a list because this is a remote: five cards stacked at 540dp would scroll under
 * a focus the child cannot see, while one card and two arrows is the same thing at any count.
 */
@Composable
private fun Review(
    item: QuizReview,
    index: Int,
    count: Int,
    onMove: (Int) -> Unit,
    onBack: () -> Unit,
) {
    val first = remember(index) { FocusRequester() }
    val hasNext = index < count - 1
    LaunchedEffect(index) {
        withFrameNanos { }
        runCatching { first.requestFocus() }
    }
    Column(Modifier.fillMaxSize().padding(horizontal = 96.dp, vertical = 20.dp)) {
        Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
            Chip(text = stringResource(R.string.parental_quiz_review_title), tint = Palette.Green)
            Spacer(Modifier.width(12.dp))
            BasicText(
                stringResource(R.string.parental_quiz_review_count, index + 1, count),
                style = Type.Caption.copy(color = Palette.Label.copy(alpha = 0.44f), fontFeatureSettings = "tnum"),
            )
            Spacer(Modifier.weight(1f))
            // Subject · topic, in a plate rather than loose: it is a stamp on the question, not part
            // of the sentence.
            val where = listOf(item.subject, item.topic).filter { it.isNotEmpty() }.joinToString(" · ")
            if (where.isNotEmpty()) {
                Box(
                    Modifier
                        .clip(RoundedCornerShape(7.dp))
                        .background(Ink.Plate)
                        .padding(horizontal = 9.dp, vertical = 4.dp),
                ) {
                    BasicText(where, style = Type.Caption.copy(color = Palette.SecondaryLabel), maxLines = 1)
                }
            }
        }

        Spacer(Modifier.height(16.dp))
        BasicText(
            item.stem,
            style = Type.Title.copy(fontSize = 24.sp, textAlign = TextAlign.Start),
            maxLines = 3,
        )
        Spacer(Modifier.height(18.dp))
        Column(Modifier.fillMaxWidth().cardSurface(16.dp).padding(horizontal = 16.dp, vertical = 14.dp)) {
            BasicText(
                stringResource(R.string.parental_quiz_review_yours, item.chosen),
                style = Type.Body.copy(color = Palette.Red, fontWeight = FontWeight.W600),
                maxLines = 2,
            )
            Spacer(Modifier.height(8.dp))
            BasicText(
                stringResource(R.string.parental_quiz_review_correct, item.correct),
                style = Type.Body.copy(color = Palette.Green, fontWeight = FontWeight.W600),
                maxLines = 2,
            )
            // No explanation in the bank means no line: the placeholder reads as a promise.
            if (item.explanation.isNotEmpty()) {
                Spacer(Modifier.height(10.dp))
                BasicText(
                    stringResource(R.string.parental_quiz_review_why, item.explanation),
                    style = Type.Caption.copy(color = Palette.SecondaryLabel),
                    maxLines = 3,
                )
            }
        }

        Spacer(Modifier.weight(1f))
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            // On the last card there is nowhere further to go, so 返回 takes the focus instead.
            QuizButton(label = R.string.parental_quiz_back, focusRequester = if (hasNext) null else first, primary = false, onClick = onBack)
            if (index > 0) {
                QuizButton(label = R.string.parental_quiz_review_prev, primary = false, onClick = { onMove(-1) })
            }
            if (hasNext) {
                QuizButton(label = R.string.parental_quiz_review_next, focusRequester = first, primary = true, onClick = { onMove(1) })
            }
        }
    }
}

@Composable
private fun BoxScope.Failure(error: QuizClient.Error, retry: Boolean, coolSec: Int, onRetry: () -> Unit, onExit: () -> Unit) {
    val first = remember { FocusRequester() }
    LaunchedEffect(Unit) {
        withFrameNanos { }
        runCatching { first.requestFocus() }
    }
    // A refusal that names a wait is drawn as one: the seconds count down here, and 重试 only comes
    // back when they are gone. Offering a button the server is certain to refuse is how a page
    // teaches a child to press it twice.
    val waiting = error is QuizClient.Error.Cooldown && coolSec > 0
    val body = if (error is QuizClient.Error.Cooldown) {
        stringResource(R.string.parental_quiz_error_cooldown_body, clockText(coolSec))
    } else {
        bodyOf(error)?.let { stringResource(it) }
    }
    Column(Modifier.align(Alignment.Center).width(560.dp), horizontalAlignment = Alignment.CenterHorizontally) {
        Reveal(0) { ScreenIcon(Glyph.Info, Palette.Green, box = 64.dp, glyphSize = 26.dp) }
        Spacer(Modifier.height(16.dp))
        Reveal(1) {
            BasicText(
                stringResource(titleOf(error)),
                style = Type.LargeTitle.copy(fontSize = 26.sp, textAlign = TextAlign.Center),
            )
        }
        body?.let {
            Spacer(Modifier.height(8.dp))
            Reveal(2) {
                BasicText(
                    it,
                    style = Type.Body.copy(color = Palette.SecondaryLabel, textAlign = TextAlign.Center),
                )
            }
        }
        if (waiting) {
            Spacer(Modifier.height(14.dp))
            // Tabular digits, like the lock screen's countdown: no sideways shift per tick.
            BasicText(
                clockText(coolSec),
                style = Type.LargeTitle.copy(
                    fontSize = 40.sp,
                    fontWeight = FontWeight.W700,
                    fontFeatureSettings = "tnum",
                    color = Palette.Green,
                ),
            )
        }
        Spacer(Modifier.height(26.dp))
        Reveal(3) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                if (retry) {
                    QuizButton(
                        label = R.string.parental_quiz_retry,
                        focusRequester = if (waiting) null else first,
                        primary = true,
                        enabled = !waiting,
                        onClick = onRetry,
                    )
                }
                QuizButton(
                    label = R.string.parental_quiz_back,
                    focusRequester = if (retry && !waiting) null else first,
                    primary = !retry,
                    onClick = onExit,
                )
            }
        }
    }
}

@Composable
private fun Notice(title: Int, body: Int?, modifier: Modifier = Modifier) {
    Column(modifier, horizontalAlignment = Alignment.CenterHorizontally) {
        BasicText(stringResource(title), style = Type.Title.copy(fontSize = 22.sp))
        body?.let {
            Spacer(Modifier.height(8.dp))
            BasicText(stringResource(it), style = Type.Caption.copy(color = Palette.SecondaryLabel))
        }
    }
}

@Composable
private fun QuizButton(
    label: Int,
    labelArg: Int? = null,
    focusRequester: FocusRequester? = null,
    primary: Boolean,
    enabled: Boolean = true,
    onClick: () -> Unit,
) {
    Pressable(onClick = onClick, enabled = enabled, focusRequester = focusRequester, focusedScale = 1.03f) { press ->
        Box(
            Modifier
                .width(200.dp)
                .height(46.dp)
                .clip(RoundedCornerShape(13.dp))
                .background(
                    when {
                        press.focused -> Palette.Focus
                        !enabled -> Ink.Plate.copy(alpha = 0.5f)
                        primary -> Color.White.copy(alpha = 0.14f)
                        else -> Color.White.copy(alpha = 0.07f)
                    },
                )
                .border(
                    1.dp,
                    when {
                        press.focused -> Color.Transparent
                        primary && enabled -> Color.White.copy(alpha = 0.20f)
                        else -> Ink.Edge
                    },
                    RoundedCornerShape(13.dp),
                ),
            contentAlignment = Alignment.Center,
        ) {
            BasicText(
                if (labelArg != null) stringResource(label, labelArg) else stringResource(label),
                style = Type.Body.copy(
                    color = when {
                        press.focused -> Palette.OnFocus
                        enabled -> Palette.Label
                        // Dimmed rather than hidden: a button that comes and goes moves the others
                        // under the remote's focus.
                        else -> Palette.Label.copy(alpha = 0.35f)
                    },
                    fontWeight = FontWeight.W600,
                ),
            )
        }
    }
}

/** The error, as the sentence a child or a parent needs. */
private fun titleOf(error: QuizClient.Error): Int = when (error) {
    QuizClient.Error.Offline -> R.string.parental_quiz_error_offline
    QuizClient.Error.Untrusted -> R.string.parental_quiz_error_untrusted
    is QuizClient.Error.Cooldown -> R.string.parental_quiz_error_cooldown_title
    QuizClient.Error.AttemptsExhausted -> R.string.parental_quiz_error_attempts
    QuizClient.Error.QuotaExhausted -> R.string.parental_quiz_error_quota
    QuizClient.Error.NoQuestions -> R.string.parental_quiz_error_no_questions
    QuizClient.Error.Unpaired, QuizClient.Error.Revoked -> R.string.parental_quiz_error_unpaired
    QuizClient.Error.NotConfigured -> R.string.parental_quiz_error_config
    is QuizClient.Error.Other -> R.string.parental_quiz_error_other
}

private fun bodyOf(error: QuizClient.Error): Int? = when (error) {
    QuizClient.Error.Untrusted -> R.string.parental_quiz_error_untrusted_body
    QuizClient.Error.NotConfigured -> R.string.parental_quiz_error_config_body
    else -> null
}

/** Sentinel keys of the keypad grid; not text, so they never reach an answer. */
private const val KEYS_BACK = "\u0008"
private const val KEYS_DONE = "\u000d"

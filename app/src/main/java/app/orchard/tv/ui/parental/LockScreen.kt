package app.orchard.tv.ui.parental

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
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
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.orchard.tv.R
import app.orchard.tv.parental.Parental
import app.orchard.tv.parental.WatchTimer
import app.orchard.tv.ui.home.PinMode
import app.orchard.tv.ui.home.PinPad
import app.orchard.tv.ui.theme.Glyph
import app.orchard.tv.ui.theme.Palette
import app.orchard.tv.ui.theme.Type

/**
 * The two lock screens of docs/PARENTAL.md (界面), drawn after the prototype's `锁屏` scenes.
 *
 * Which one is up follows the timer's state, not the caller: an interval has a countdown and one way
 * out of it, the daily limit has two doors and no clock.
 *
 * **The daily screen has two doors, not four buttons** (2026-11-12). `去做题` is the child's — no
 * password, because the whole point of it is that the child can take it. Everything a parent does
 * sits behind `家长入口`: the password, then 30 minutes / an hour / no limit for today. A screen that
 * lists "no limit today" in front of a nine-year-old is a screen that has told him there is a
 * password which solves everything; one pane back, it is simply the parent's business.
 *
 * **The cooldown counts down here** ([coolSec]). After a group that did not pass the server refuses
 * the next one for ten minutes, and a greyed-out quiz button with no reason is the sort of thing a
 * child reads as "broken". The seconds are the server's, passed in by `LockActivity`, which reads
 * the deadline the quiz page wrote down.
 *
 * **The interval's countdown is a ring** (2026-11-13): the unfilled arc is the wait that is left, so
 * the screen answers "how long" before the digits are read, from the sofa. The daily screen has
 * nothing left to count, so it leads with the icon and the sentence instead — two screens, two
 * answers, one shape.
 *
 * The screens do not tick and do not open or close the lock: `LockActivity` owns that, so there is
 * exactly one clock over the counters (Home's) and one place that decides when the lock is over.
 */
@Composable
fun ParentalLockScreen(ui: Parental.Ui, leftSec: Int, coolSec: Int, onQuiz: () -> Unit) {
    val breaking = ui.state == WatchTimer.State.Interval
    // Which pane is up: the screen's own doors, or the parent's three actions.
    var portal by remember { mutableStateOf(false) }
    var pending by remember { mutableStateOf<Action?>(null) }
    val first = remember { FocusRequester() }
    val portalFirst = remember { FocusRequester() }
    val context = LocalContext.current

    // Back leaves the parent's pane; the lock itself stays (LockActivity's own callback keeps it).
    BackHandler(enabled = portal && pending == null) { portal = false }

    // Back from the keypad, or after a limit is lifted under the pad: focus belongs on the first
    // action again, never nowhere.
    LaunchedEffect(pending, portal) {
        if (pending == null) {
            // Two frames: the keypad's exit animation is still holding focus on the first one.
            withFrameNanos { }
            withFrameNanos { }
            runCatching { if (portal) portalFirst.requestFocus() else first.requestFocus() }
        }
    }

    AmbientBackdrop {
        Reveal(0, Modifier.align(Alignment.TopCenter).padding(top = 22.dp)) {
            Chip(
                text = stringResource(
                    if (breaking) R.string.parental_lock_tag_break else R.string.parental_lock_tag_daily,
                ),
                tint = Palette.Green,
            )
        }

        if (portal) {
            ParentActions(ui = ui, first = portalFirst, onAction = ::carryOut, onBack = { portal = false })
        } else {
            LockBody(
                ui = ui,
                breaking = breaking,
                leftSec = leftSec,
                coolSec = coolSec,
                pending = pending != null,
                first = first,
                onQuiz = onQuiz,
                onPortal = { pending = Action.Portal },
                onSkip = { pending = Action.Skip },
            )
        }

        Reveal(5, Modifier.align(Alignment.BottomCenter).padding(bottom = 22.dp)) {
            BasicText(
                stringResource(R.string.parental_lock_foot),
                style = Type.Caption.copy(color = Palette.Label.copy(alpha = 0.30f), textAlign = TextAlign.Center),
            )
        }
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
            // 家长入口 is the one action that is not carried out by the password: it opens the pane
            // the password was for.
            when (val action = pending) {
                Action.Portal -> portal = true
                null -> Unit
                else -> carryOut(action)
            }
            pending = null
        },
        onCancel = { pending = null },
    )
}

/**
 * The lock itself: the interval's ring, or the daily limit's sentence, then the doors.
 *
 * Nothing here decides anything; it draws [ui] and hands presses back up.
 */
@Composable
private fun BoxScope.LockBody(
    ui: Parental.Ui,
    breaking: Boolean,
    leftSec: Int,
    coolSec: Int,
    pending: Boolean,
    first: FocusRequester,
    onQuiz: () -> Unit,
    onPortal: () -> Unit,
    onSkip: () -> Unit,
) {
    Column(
        Modifier.align(Alignment.Center).width(620.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        if (breaking) {
            // The countdown is the subject of this screen, so it leads: a small label over it, the
            // ring with the reading inside, and the reason under it.
            Reveal(1) {
                BasicText(
                    stringResource(R.string.parental_lock_break_title),
                    style = Type.Title.copy(fontSize = 21.sp, color = Palette.SecondaryLabel, fontWeight = FontWeight.W600),
                )
            }
            Spacer(Modifier.height(14.dp))
            Reveal(2) {
                val left = (leftSec * 1_000f / ui.intervalMs.coerceAtLeast(1L)).coerceIn(0f, 1f)
                CountdownRing(progress = left, tint = Palette.Green, size = 152.dp) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        // Tabular digits: without them the countdown shifts sideways on every tick.
                        BasicText(
                            clockText(leftSec),
                            style = Type.LargeTitle.copy(
                                fontSize = 42.sp,
                                fontWeight = FontWeight.W700,
                                fontFeatureSettings = "tnum",
                            ),
                        )
                        Spacer(Modifier.height(2.dp))
                        BasicText(
                            stringResource(R.string.parental_lock_left),
                            style = Type.Caption.copy(color = Palette.SecondaryLabel),
                        )
                    }
                }
            }
            Spacer(Modifier.height(16.dp))
            Reveal(3) {
                BasicText(
                    emphasised(
                        stringResource(
                            R.string.parental_lock_break_why,
                            durationText(ui.sessionUsedMs),
                            durationText(ui.intervalMs),
                        ),
                    ),
                    style = Type.Body.copy(color = Palette.SecondaryLabel, textAlign = TextAlign.Center),
                )
            }
        } else {
            Reveal(1) { ScreenIcon(Glyph.Shield, Palette.Green) }
            Spacer(Modifier.height(18.dp))
            Reveal(2) {
                BasicText(
                    stringResource(R.string.parental_lock_daily_title),
                    // BasicText takes its alignment from the style, not an argument.
                    style = Type.LargeTitle.copy(fontSize = 30.sp, fontWeight = FontWeight.W600, textAlign = TextAlign.Center),
                )
            }
            Spacer(Modifier.height(10.dp))
            Reveal(3) {
                BasicText(
                    emphasised(
                        stringResource(R.string.parental_lock_daily_why, durationText(ui.dailyUsedMs), durationText(ui.dailyBudgetMs)),
                    ),
                    style = Type.Body.copy(color = Palette.SecondaryLabel, textAlign = TextAlign.Center),
                )
            }
        }

        Spacer(Modifier.height(26.dp))
        Reveal(4) {
            Doors(
                breaking = breaking,
                quizOffered = ui.dailyBudgetMs > 0L && !ui.unrestrictedToday,
                coolSec = coolSec,
                pending = pending,
                first = first,
                onQuiz = onQuiz,
                onPortal = onPortal,
                onSkip = onSkip,
            )
        }
        if (coolSec > 0) {
            Spacer(Modifier.height(14.dp))
            Reveal(5) {
                // A disabled button with no reason is the complaint this pill answers.
                Chip(
                    text = stringResource(R.string.parental_lock_cooling, clockText(coolSec)),
                    tint = Palette.Green,
                )
            }
        }
    }
}

/**
 * The two doors of the daily screen, and the one of the interval screen. The quiz is offered first
 * and without a password because it is the path that costs effort rather than a secret; `去做题` is
 * also the only one a child can take, and the whole feature assumes he will.
 */
@Composable
private fun Doors(
    breaking: Boolean,
    quizOffered: Boolean,
    coolSec: Int,
    pending: Boolean,
    first: FocusRequester,
    onQuiz: () -> Unit,
    onPortal: () -> Unit,
    onSkip: () -> Unit,
) {
    val cooling = coolSec > 0
    // Only offered when there is a daily limit to add to. With 每日 = 不限 the day is already
    // unlimited and a pass buys nothing, so the button would be a promise the screen cannot keep.
    val actions = buildList {
        if (quizOffered) add(Action.Quiz)
        add(if (breaking) Action.Skip else Action.Portal)
    }
    val focusable = actions.firstOrNull { it != Action.Quiz || !cooling }
    Column(Modifier.width(340.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
        actions.forEach { action ->
            val quizCooling = action == Action.Quiz && cooling
            MenuButton(
                title = stringResource(actionLabel(action)),
                onClick = {
                    when (action) {
                        Action.Quiz -> onQuiz()
                        Action.Skip -> onSkip()
                        else -> onPortal()
                    }
                },
                hint = when (action) {
                    // A disabled button with no reason is the complaint this line answers.
                    Action.Quiz -> if (quizCooling) {
                        stringResource(R.string.parental_lock_quiz_cooling_hint, clockText(coolSec))
                    } else {
                        stringResource(R.string.parental_lock_quiz_hint)
                    }
                    else -> stringResource(R.string.parental_lock_needs_pin)
                },
                glyph = when (action) {
                    Action.Quiz -> Glyph.Kids
                    Action.Skip -> Glyph.Check
                    else -> Glyph.Shield
                },
                // The quiz is the effort the child can spend instead of a password: it reads as the
                // way forward, and the parent's door stays a normal row.
                tier = if (action == Action.Quiz) ButtonTier.Prominent else ButtonTier.Normal,
                // While the password is up the actions go dark to the remote: the keypad is a
                // full-screen overlay in the same tree, and a focusable button under it takes the
                // arrow keys the moment the search runs out of keys inside the pad — the same
                // reason Home disables its grid under an overlay.
                enabled = !pending && !quizCooling,
                // The first button that can actually take focus; a disabled one would leave the
                // screen with nothing focused.
                focusRequester = if (action == focusable) first else null,
            )
        }
    }
}

/** What a parent sees after the password: the three ways to add time, and the way back. */
@Composable
private fun BoxScope.ParentActions(
    ui: Parental.Ui,
    first: FocusRequester,
    onAction: (Action) -> Unit,
    onBack: () -> Unit,
) {
    Column(
        Modifier.align(Alignment.Center).width(620.dp),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Reveal(0) { ScreenIcon(Glyph.Shield, Palette.Green, box = 64.dp, glyphSize = 26.dp) }
        Spacer(Modifier.height(14.dp))
        Reveal(1) {
            BasicText(
                stringResource(R.string.parental_lock_portal_title),
                style = Type.LargeTitle.copy(fontSize = 26.sp, fontWeight = FontWeight.W600, textAlign = TextAlign.Center),
            )
        }
        Spacer(Modifier.height(8.dp))
        // The same line the screen behind it carries: a parent who has just typed the password still
        // needs to know what today looks like before choosing a number.
        Reveal(2) {
            BasicText(
                emphasised(
                    stringResource(R.string.parental_lock_daily_why, durationText(ui.dailyUsedMs), durationText(ui.dailyBudgetMs)),
                ),
                style = Type.Body.copy(color = Palette.SecondaryLabel, textAlign = TextAlign.Center),
            )
        }
        Spacer(Modifier.height(22.dp))
        Reveal(3) {
            Column(Modifier.width(340.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                MenuButton(
                    title = stringResource(R.string.parental_lock_extend_30),
                    onClick = { onAction(Action.Half) },
                    focusRequester = first,
                )
                MenuButton(
                    title = stringResource(R.string.parental_lock_extend_60),
                    onClick = { onAction(Action.Hour) },
                )
                // The last one ends the evening, and it is the only action that outlives the limit
                // setting — so it is set apart by its place in the list, not by a colour of its own.
                MenuButton(
                    title = stringResource(R.string.parental_lock_lift),
                    onClick = { onAction(Action.Lift) },
                )
                Spacer(Modifier.height(2.dp))
                MenuButton(
                    title = stringResource(R.string.parental_lock_back),
                    onClick = onBack,
                    glyph = Glyph.ChevronLeft,
                    tier = ButtonTier.Quiet,
                )
            }
        }
    }
}

/** What a lock screen can offer: the quiz, the way into the parent's three actions, the three
 * themselves, and the one way past a break. */
private enum class Action { Quiz, Portal, Half, Hour, Lift, Skip }

private fun actionLabel(action: Action): Int = when (action) {
    Action.Quiz -> R.string.parental_lock_quiz
    Action.Skip -> R.string.parental_lock_skip
    Action.Half -> R.string.parental_lock_extend_30
    Action.Hour -> R.string.parental_lock_extend_60
    Action.Lift -> R.string.parental_lock_lift
    Action.Portal -> R.string.parental_lock_portal
}

/**
 * Applies what the parent chose. Nothing here closes the lock: [Parental] publishes the new state,
 * and `LockActivity` lets go the moment a limit is no longer used up — one path out, whatever the
 * reason.
 */
private fun carryOut(action: Action) = when (action) {
    Action.Half -> Parental.extendDaily(30L * 60_000)
    Action.Hour -> Parental.extendDaily(60L * 60_000)
    Action.Lift -> Parental.liftDailyLimit()
    Action.Skip -> Parental.skipInterval()
    // The quiz opens its own page without asking for a password, and the portal is opened by the
    // keypad's success rather than by an action of its own — see the callers.
    Action.Quiz, Action.Portal -> Unit
}

/** The countdown as a string for the keypad's warning, which is built outside a composable. */
private fun clockString(context: android.content.Context, seconds: Int): String =
    if (seconds >= 3600) {
        context.getString(R.string.parental_clock_hours, seconds / 3600, seconds % 3600 / 60, seconds % 60)
    } else {
        context.getString(R.string.parental_clock, seconds / 60, seconds % 60)
    }

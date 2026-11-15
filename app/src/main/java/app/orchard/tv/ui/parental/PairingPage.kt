package app.orchard.tv.ui.parental

import android.content.Context
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableLongStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import app.orchard.tv.R
import app.orchard.tv.quiz.QuizClient
import app.orchard.tv.quiz.QuizConfig
import app.orchard.tv.quiz.QuizVault
import app.orchard.tv.ui.components.Pressable
import app.orchard.tv.ui.components.consumeTaps
import app.orchard.tv.ui.components.liquidGlass
import app.orchard.tv.ui.theme.Glyph
import app.orchard.tv.ui.theme.GlyphIcon
import app.orchard.tv.ui.theme.Palette
import app.orchard.tv.ui.theme.Type
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/**
 * 手机配对 (docs/PARENTAL.md, 家长 H5): the six characters a parent types on their phone.
 *
 * The code lives here and not on the lock screen because the lock screen is what the child stares at
 * all evening; a key taped to the door is not a key. A parent already has to type the password to get
 * this far, and pairing is a thing done twice a year.
 *
 * Three behaviours worth knowing:
 *
 *  - **The page polls every three seconds** while a code is on screen, so a parent who types it sees
 *    the television change by itself instead of having to go back and check.
 *  - **A new code keeps the device id.** `pair()` registers a *second* television and leaves today's
 *    counters behind; [QuizClient.renewPair] only replaces the six characters.
 *  - **None of it is needed to watch.** No server, no Wi-Fi, no pairing: the lock screen still offers
 *    the password, which is what a household without a phone should fall back to.
 */
@Composable
fun PairingPage(visible: Boolean, onClose: () -> Unit) {
    val context = LocalContext.current
    val scope = rememberCoroutineScope()
    var state by remember { mutableStateOf<State>(State.Busy) }
    var now by remember { mutableLongStateOf(System.currentTimeMillis()) }

    // The countdown, once a second, only while the page is up. Cancelled by the key changing.
    LaunchedEffect(visible) {
        if (!visible) return@LaunchedEffect
        while (true) {
            now = System.currentTimeMillis()
            delay(1_000)
        }
    }

    LaunchedEffect(visible) {
        if (!visible) return@LaunchedEffect
        state = if (QuizConfig.configured) prepare(context) else State.Off
    }

    // While a code is on screen: has it been typed yet? Three seconds is a poll the server can serve
    // all day, and quick enough that the television looks like it reacts to the phone.
    val waiting = state as? State.Waiting
    LaunchedEffect(visible, waiting?.code) {
        if (!visible || waiting == null) return@LaunchedEffect
        while (true) {
            delay(3_000)
            val seen = withContext(Dispatchers.IO) { runCatching { QuizClient.pairStatus(context) } }
            val status = seen.getOrNull()
            if (status != null && status.bound) {
                QuizVault.setBound(context, true)
                state = State.Done(status.name ?: "Aerial TV")
                break
            }
        }
    }

    Box(
        Modifier.fillMaxSize().background(Palette.Background.copy(alpha = 0.55f)),
        contentAlignment = Alignment.Center,
    ) {
        Column(
            Modifier
                .width(660.dp)
                .consumeTaps()
                .liquidGlass(28.dp)
                .padding(horizontal = 28.dp, vertical = 24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            // The same heading the settings sheet it is opened from has: a badge, then the page's
            // name. This page is a pane of that sheet's flow, and a bare headline in the middle of
            // the glass made it look like a dialog from somewhere else.
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Box(
                    Modifier
                        .size(34.dp)
                        .clip(RoundedCornerShape(11.dp))
                        .background(Palette.Green.copy(alpha = 0.16f)),
                    contentAlignment = Alignment.Center,
                ) {
                    GlyphIcon(Glyph.Globe, Palette.Green, 18.dp)
                }
                Spacer(Modifier.width(12.dp))
                BasicText(
                    stringResource(R.string.parental_pair_title),
                    style = Type.Title,
                    maxLines = 1,
                )
            }
            Spacer(Modifier.height(14.dp))

            when (val current = state) {
                State.Off -> Message(
                    title = stringResource(R.string.parental_pair_off),
                    body = stringResource(R.string.parental_quiz_error_config_body),
                    tone = Palette.SecondaryLabel,
                )

                State.Busy -> Message(
                    title = stringResource(R.string.parental_pair_busy),
                    body = "",
                    tone = Palette.SecondaryLabel,
                )

                State.Failed -> Message(
                    title = stringResource(R.string.parental_pair_failed),
                    body = stringResource(R.string.parental_quiz_error_offline),
                    tone = Palette.Red,
                )

                is State.Done -> Message(
                    title = stringResource(R.string.parental_pair_done, current.name),
                    body = stringResource(R.string.parental_pair_done_body),
                    tone = Palette.Green,
                )

                is State.Waiting -> Code(
                    code = current.code,
                    secondsLeft = ((current.until - now) / 1_000L).toInt().coerceAtLeast(0),
                )
            }

            Spacer(Modifier.height(20.dp))

            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                // Offered only when there is something to renew: a live code needs no replacement.
                val expired = (state as? State.Waiting)?.let { it.until <= now } == true
                val renewable = expired || state is State.Failed
                if (renewable) {
                    PageButton(stringResource(R.string.parental_pair_again), primary = true) {
                        state = State.Busy
                        scope.launch {
                            state = if (QuizConfig.configured) prepare(context) else State.Off
                        }
                    }
                }
                // How a second parent joins, and how a lost phone is replaced (docs/ACCOUNT.md, A2).
                // The page is already behind the parent password, so this is the parent asking, and
                // what it costs is a code, not the television: since accounts exist the server hands a
                // code to a bound device again, so the device id, today's counters and the history all
                // stay. (Before accounts this button wiped the device and registered a new row — the
                // only way past "already bound, no code" when a phone could hold a TV exclusively.)
                if (state is State.Done) {
                    PageButton(stringResource(R.string.parental_pair_replace), primary = false) {
                        state = State.Busy
                        scope.launch { state = renewNow(context) }
                    }
                }
                PageButton(stringResource(R.string.parental_pair_close), primary = !renewable, onClick = onClose)
            }
        }
    }
}

/**
 * What the page opens on: bound, a code still running, or a new code.
 *
 * The order matters. Asking the server first means a television that is already paired says so rather
 * than showing a code nobody needs, and it catches the case where the phone was bound while this page
 * was closed. A cached code is shown again rather than replaced, because replacing it would restart
 * the ten minutes the parent is part-way through typing.
 */
private suspend fun prepare(context: Context): State = withContext(Dispatchers.IO) {
    if (QuizVault.deviceId(context) != null) {
        val seen = runCatching { QuizClient.pairStatus(context) }
        val status = seen.getOrNull()
        if (status != null && status.bound) {
            QuizVault.setBound(context, true)
            return@withContext State.Done(status.name ?: "Aerial TV")
        }

        val error = (seen.exceptionOrNull() as? QuizClient.Failed)?.error
        when (error) {
            // The server was rebuilt and no longer knows this id: register again rather than show a
            // code for a device that does not exist.
            is QuizClient.Error.Unpaired -> QuizVault.forgetDevice(context)
            // Unreachable, wrongly signed, or unbound by a parent: none of these are cured by asking
            // for a code, and the last one must not be undone by pairing a fresh device.
            is QuizClient.Error.Offline, is QuizClient.Error.Untrusted,
            is QuizClient.Error.Revoked, is QuizClient.Error.NotConfigured -> return@withContext State.Failed
            else -> Unit
        }

        val until = QuizVault.pairUntil(context)
        val cached = QuizVault.pairCode(context)
        if (cached != null && until > System.currentTimeMillis()) {
            return@withContext State.Waiting(cached, until)
        }
    }

    val issued = runCatching {
        if (QuizVault.deviceId(context) != null) QuizClient.renewPair(context) else QuizClient.pair(context)
    }
    // The two calls do not return the same thing: [QuizClient.pair] hands back the new device id (it
    // also registers the television for quizzes), [QuizClient.renewPair] hands back the code. Both
    // store the code in the vault, so that is what the page reads — taking the return value instead
    // put a ULID on screen the first time a fresh television was paired. (Found on the television,
    // 2026-11-12.)
    issued.getOrNull() ?: return@withContext State.Failed
    val code = QuizVault.pairCode(context) ?: return@withContext State.Failed
    State.Waiting(code, QuizVault.pairUntil(context))
}

/** A fresh code for a television that is already paired; the device id is kept. */
private suspend fun renewNow(context: Context): State = withContext(Dispatchers.IO) {
    val issued = runCatching { QuizClient.renewPair(context) }
    if (issued.isFailure) return@withContext State.Failed
    // The code is what the vault holds, not what the call returns: see `prepare`.
    val code = QuizVault.pairCode(context) ?: return@withContext State.Failed
    State.Waiting(code, QuizVault.pairUntil(context))
}

@Composable
private fun Code(code: String, secondsLeft: Int) {
    val expired = secondsLeft <= 0
    Column(horizontalAlignment = Alignment.CenterHorizontally) {
        Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            for (ch in code) {
                Box(
                    Modifier
                        .size(width = 54.dp, height = 68.dp)
                        .clip(RoundedCornerShape(14.dp))
                        .background(if (expired) Color.White.copy(alpha = 0.05f) else Ink.Plate),
                    contentAlignment = Alignment.Center,
                ) {
                    BasicText(
                        ch.toString(),
                        style = Type.LargeTitle.copy(color = if (expired) Palette.Label.copy(alpha = 0.40f) else Palette.Label),
                        maxLines = 1,
                    )
                }
            }
        }
        Spacer(Modifier.height(14.dp))
        BasicText(
            stringResource(R.string.parental_pair_how, QuizConfig.baseUrl),
            style = Type.Callout.copy(color = Palette.SecondaryLabel),
            maxLines = 2,
            overflow = TextOverflow.Ellipsis,
            modifier = Modifier.fillMaxWidth(),
        )
        Spacer(Modifier.height(10.dp))
        BasicText(
            if (expired) {
                stringResource(R.string.parental_pair_expired)
            } else {
                stringResource(R.string.parental_pair_left, secondsLeft)
            },
            // An expired code is the one line on this page a parent has to act on.
            style = Type.Caption.copy(color = if (expired) Palette.Red else Palette.Label.copy(alpha = 0.46f)),
            maxLines = 1,
        )
        // The alphabet keeps its case, so the page must say so: a phone's keyboard capitalises the
        // first letter by itself and the code would look broken.
        if (!expired) {
            Spacer(Modifier.height(4.dp))
            BasicText(
                stringResource(R.string.parental_pair_case),
                style = Type.Caption.copy(color = Palette.Label.copy(alpha = 0.40f)),
                maxLines = 1,
            )
        }
    }
}

@Composable
private fun Message(title: String, body: String, tone: Color) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        BasicText(
            title,
            style = Type.Title.copy(color = tone, textAlign = TextAlign.Center),
            maxLines = 2,
        )
        if (body.isNotEmpty()) {
            Spacer(Modifier.height(8.dp))
            BasicText(
                body,
                style = Type.Callout.copy(color = Palette.SecondaryLabel, textAlign = TextAlign.Center),
                maxLines = 3,
            )
        }
    }
}

@Composable
private fun PageButton(label: String, primary: Boolean, onClick: () -> Unit) {
    Pressable(onClick = onClick, focusedScale = 1.03f) { state ->
        // The module's two button weights (ParentalStyle.MenuButton) in the one shape this page
        // needs: a plate that is lit, not painted, until the remote lands on it and it turns white.
        val bg = when {
            state.focused -> Palette.Focus
            primary -> Color.White.copy(alpha = 0.10f)
            else -> Color.White.copy(alpha = 0.04f)
        }
        val fg = when {
            state.focused -> Palette.OnFocus
            primary -> Palette.Label
            else -> Palette.Label.copy(alpha = 0.72f)
        }
        val edge = when {
            state.focused -> Color.Transparent
            primary -> Ink.EdgeLit
            else -> Ink.Edge
        }
        Box(
            Modifier
                .clip(RoundedCornerShape(14.dp))
                .background(bg)
                .border(1.dp, edge, RoundedCornerShape(14.dp))
                .height(46.dp)
                .padding(horizontal = 24.dp),
            contentAlignment = Alignment.Center,
        ) {
            BasicText(
                label,
                style = Type.Body.copy(color = fg, fontWeight = FontWeight.W600),
                maxLines = 1,
            )
        }
    }
}

/** What the page is showing. [Waiting] carries the code and the moment it stops working. */
private sealed interface State {
    data object Off : State
    data object Busy : State
    data object Failed : State
    data class Waiting(val code: String, val until: Long) : State
    data class Done(val name: String) : State
}

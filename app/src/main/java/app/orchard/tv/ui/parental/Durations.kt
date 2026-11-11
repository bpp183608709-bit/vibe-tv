package app.orchard.tv.ui.parental

import androidx.compose.runtime.Composable
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.AnnotatedString
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.font.FontWeight
import app.orchard.tv.R
import app.orchard.tv.ui.theme.Palette

/**
 * The readings and phrases the parental module writes, in one place: the settings page and the lock
 * screen both need them, and two copies of "2 小时 15 分钟" drift apart.
 */

/** A count of minutes as the module writes it: 不限, 45 分钟, 2 小时, 2 小时 15 分钟. */
@Composable
internal fun durationText(minutes: Int): String = when {
    minutes <= 0 -> stringResource(R.string.parental_unlimited)
    minutes < 60 -> stringResource(R.string.parental_minutes, minutes)
    minutes % 60 == 0 -> stringResource(R.string.parental_hours, minutes / 60)
    else -> stringResource(R.string.parental_hours_minutes, minutes / 60, minutes % 60)
}

/** The same, from a counter: whole minutes, rounded down, so a minute is not shown before it is up. */
@Composable
internal fun durationText(millis: Long): String = durationText((millis / 60_000L).toInt())

/** The lock screen's countdown, m:ss — and past an hour h:mm:ss, which the interval cannot reach. */
@Composable
internal fun clockText(seconds: Int): String {
    val s = seconds.coerceAtLeast(0)
    return if (s >= 3600) {
        stringResource(R.string.parental_clock_hours, s / 3600, s % 3600 / 60, s % 60)
    } else {
        stringResource(R.string.parental_clock, s / 60, s % 60)
    }
}

/**
 * A sentence with the numbers that matter picked out in white, the way the prototype writes the lock
 * screen's reason: 今天已看 **2 小时 15 分钟**，上限 **2 小时**. The markers sit in the string, so a
 * translation decides where the emphasis goes; the alternative — styling whatever looks like a
 * duration — reads "2 小时" out of "2 小时 15 分钟" and lights up the wrong half of the sentence.
 *
 * Asterisks rather than `<b>`: Android's own string resources eat `<b>` before Compose ever sees the
 * text, and the marker would have to be written differently in each of the five languages' files.
 */
internal fun emphasised(text: String): AnnotatedString = buildAnnotatedString {
    var rest = text
    while (rest.isNotEmpty()) {
        val open = rest.indexOf(MARK)
        if (open < 0) { append(rest); return@buildAnnotatedString }
        append(rest.substring(0, open))
        val close = rest.indexOf(MARK, open + 1)
        val end = if (close < 0) rest.length else close
        pushStyle(SpanStyle(color = Palette.Label, fontWeight = FontWeight.W600))
        append(rest.substring(open + 1, end))
        pop()
        rest = if (close < 0) "" else rest.substring(close + 1)
    }
}

/** The marker [emphasised] reads; one character, so a translator can see it and keep it in pairs. */
private const val MARK = "*"

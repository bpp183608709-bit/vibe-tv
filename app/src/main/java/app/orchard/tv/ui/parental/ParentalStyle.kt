package app.orchard.tv.ui.parental

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.BoxScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicText
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.draw.drawWithCache
import androidx.compose.ui.focus.FocusRequester
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import app.orchard.tv.ui.components.Pressable
import app.orchard.tv.ui.theme.Glyph
import app.orchard.tv.ui.theme.GlyphIcon
import app.orchard.tv.ui.theme.LocalReducedMotion
import app.orchard.tv.ui.theme.Motion
import app.orchard.tv.ui.theme.Palette
import app.orchard.tv.ui.theme.Type

/*
 * The look of the parental module's three screens: the settings sheet, the lock, and the quiz.
 *
 * **One direction, no new language.** Aerial is tvOS dark — near-black, white focus plates, one
 * accent. These screens were drawn flat inside that language (a 5% white rectangle for a card,
 * a bare number for a countdown), which reads as unfinished next to Home's glass. So the work here
 * is refinement, not a second style:
 *
 *  - the backdrop is a single ink-teal wash with the warm light coming from above, rather than black;
 *  - surfaces are gradients with a lit top edge, so a card has a top and a bottom;
 *  - the accent (green, the app's own) means exactly one thing on these screens — a limit is in
 *    force, a quiz is being taken, a clock is counting — and nothing else is coloured;
 *  - every number is tabular and every countdown is set large: the reading is the point of the
 *    screen, and a digit that shifts sideways every second is a bug you can watch.
 *
 * **What this file must not cost.** These run on the same weak GPUs as Home, where fill rate is the
 * whole budget (docs/ARCHITECTURE.md, 性能约束): the backdrop is *one* full-screen fill, the glow
 * behind an icon is a few hundred dp at most, and nothing here animates while it sits still. The
 * one orchestrated entrance (`Reveal`) is alpha and translation only, and it collapses under
 * `LocalReducedMotion` like every other motion in Aerial.
 */

/** The module's own ink. Not in [Palette]: these three screens are the only place it is used. */
internal object Ink {
    /** The wash, top to bottom. Four stops in one fill, so the light comes from above. */
    val Top = Color(0xFF1A2A32)
    val Upper = Color(0xFF0B1317)
    val Lower = Color(0xFF05090B)
    val Bottom = Color(0xFF010203)

    /** A card's surface: lit at the top edge, shaded at the bottom. */
    val CardTop = Color(0x14FFFFFF)
    val CardBottom = Color(0x07FFFFFF)

    /** The 1dp edge that tells a card where it ends. */
    val EdgeLit = Color(0x22FFFFFF)
    val Edge = Color(0x0AFFFFFF)

    /** A plate under white text that is not a card: option rows, keys, steppers. */
    val Plate = Color(0x10FFFFFF)

    /** The track of a ring or a meter before anything is filled in. */
    val Track = Color(0x1FFFFFFF)
}

/** One card's surface: gradient, hairline edge, real corners. 16dp is the radius a grouped list
 *  has elsewhere in Aerial (the Personalize sheet's rows), so a card here is the same object. */
internal fun Modifier.cardSurface(radius: Dp = 16.dp): Modifier = this
    .clip(RoundedCornerShape(radius))
    .drawBehind {
        drawRoundRect(
            brush = Brush.verticalGradient(listOf(Ink.CardTop, Ink.CardBottom)),
            cornerRadius = CornerRadius(radius.toPx()),
        )
    }
    .border(
        width = 1.dp,
        brush = Brush.verticalGradient(listOf(Ink.EdgeLit, Ink.Edge)),
        shape = RoundedCornerShape(radius),
    )

/**
 * The wash both lock screens and the quiz are drawn on. One full-screen fill — the rule Home lives
 * by applies here too. The light in it comes from `ScreenIcon`'s glow, which is drawn outside
 * its own bounds and so costs a few hundred dp rather than a second screen's worth of paint.
 */
@Composable
internal fun AmbientBackdrop(content: @Composable BoxScope.() -> Unit) {
    Box(
        Modifier
            .fillMaxSize()
            .drawBehind {
                drawRect(
                    Brush.verticalGradient(
                        0f to Ink.Top,
                        0.34f to Ink.Upper,
                        0.72f to Ink.Lower,
                        1f to Ink.Bottom,
                    ),
                )
            },
        content = content,
    )
}

/**
 * The countdown as a ring: what is left of the ring is what is left of the wait. The digits inside
 * are the same reading as a number, because a ring alone cannot be read from the sofa.
 *
 * No animation between ticks: the value only changes once a second, and a 1 s tween would redraw the
 * ring on every frame of the wait for a change nobody can see.
 */
@Composable
internal fun CountdownRing(
    progress: Float,
    tint: Color,
    size: Dp,
    stroke: Dp = 5.dp,
    content: @Composable BoxScope.() -> Unit,
) {
    Box(Modifier.size(size), contentAlignment = Alignment.Center) {
        Box(
            Modifier
                .fillMaxSize()
                .drawWithCache {
                    val s = stroke.toPx()
                    val inset = s / 2f
                    val arc = Size(this.size.width - s, this.size.height - s)
                    val sweep = 360f * progress.coerceIn(0f, 1f)
                    val brush = Brush.linearGradient(listOf(tint.copy(alpha = 0.65f), tint))
                    onDrawBehind {
                        drawArc(
                            color = Ink.Track,
                            startAngle = -90f,
                            sweepAngle = 360f,
                            useCenter = false,
                            topLeft = Offset(inset, inset),
                            size = arc,
                            style = Stroke(width = s, cap = StrokeCap.Round),
                        )
                        if (sweep > 0.5f) {
                            drawArc(
                                brush = brush,
                                startAngle = -90f,
                                sweepAngle = sweep,
                                useCenter = false,
                                topLeft = Offset(inset, inset),
                                size = arc,
                                style = Stroke(width = s, cap = StrokeCap.Round),
                            )
                        }
                    }
                },
        )
        content()
    }
}

/** A used-against-budget line, four dp tall. The reading above it carries the numbers. */
@Composable
internal fun Meter(
    fraction: Float,
    tint: Color,
    modifier: Modifier = Modifier.fillMaxWidth(),
    height: Dp = 3.dp,
) {
    val f = fraction.coerceIn(0f, 1f)
    Box(
        modifier
            .height(height)
            .clip(RoundedCornerShape(height / 2))
            .background(Ink.Track)
            .drawBehind {
                if (f > 0f) {
                    drawRoundRect(
                        brush = Brush.horizontalGradient(listOf(tint.copy(alpha = 0.72f), tint)),
                        size = Size(this.size.width * f, this.size.height),
                        cornerRadius = CornerRadius(this.size.height / 2),
                    )
                }
            },
    )
}

/** A status word in a pill: 休息时间, 做题换时长, 上一组没通过… */
@Composable
internal fun Chip(
    text: String,
    tint: Color,
    glyph: Glyph? = null,
    modifier: Modifier = Modifier,
) {
    Row(
        modifier
            .clip(RoundedCornerShape(999.dp))
            .background(tint.copy(alpha = 0.13f))
            .border(1.dp, tint.copy(alpha = 0.22f), RoundedCornerShape(999.dp))
            .height(26.dp)
            .padding(horizontal = 11.dp),
        verticalAlignment = Alignment.CenterVertically,
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        if (glyph != null) {
            GlyphIcon(glyph, tint, 12.dp)
        } else {
            Box(Modifier.size(5.dp).clip(CircleShape).background(tint))
        }
        BasicText(text, style = Type.Caption.copy(color = tint, fontWeight = FontWeight.W600))
    }
}

/**
 * One orchestrated entrance: the screen arrives in four or five staggered blocks instead of all at
 * once or not at all. Alpha and translation only, and nothing at all when the TV has animations off.
 */
@Composable
internal fun Reveal(index: Int, modifier: Modifier = Modifier, content: @Composable () -> Unit) {
    val reduced = LocalReducedMotion.current
    var shown by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { shown = true }
    val at by animateFloatAsState(
        targetValue = if (shown || reduced) 1f else 0f,
        animationSpec = tween(
            durationMillis = 420,
            delayMillis = if (reduced) 0 else index * 70,
            easing = Motion.EaseOut,
        ),
        label = "parental-reveal",
    )
    Box(
        modifier.graphicsLayer {
            alpha = at
            translationY = (1f - at) * 12.dp.toPx()
        },
    ) { content() }
}

/** How loud a button is: the way forward, a normal choice, or the quiet way back. */
internal enum class ButtonTier { Prominent, Normal, Quiet }

/**
 * A row a remote can press: a glyph, its name, what it costs, and where it goes.
 *
 * One component for the lock's doors, the parent's three actions and the quiz's buttons, so that a
 * button is the same object on every screen of the module. Focus is tvOS's: the whole plate turns
 * near-white and its content turns dark — a change of material rather than a ring around it, which
 * at three metres is the difference between "selected" and "there is a box here".
 */
@Composable
internal fun MenuButton(
    title: String,
    onClick: () -> Unit,
    modifier: Modifier = Modifier,
    hint: String? = null,
    glyph: Glyph? = null,
    tier: ButtonTier = ButtonTier.Normal,
    enabled: Boolean = true,
    focusRequester: FocusRequester? = null,
    height: Dp = 50.dp,
) {
    Pressable(
        onClick = onClick,
        modifier = modifier,
        enabled = enabled,
        focusRequester = focusRequester,
        focusedScale = 1.03f,
    ) { state ->
        val focused = state.focused
        val fg = when {
            focused -> Palette.OnFocus
            !enabled -> Palette.Label.copy(alpha = 0.38f)
            else -> Palette.Label
        }
        val secondary = when {
            focused -> fg.copy(alpha = 0.52f)
            // A disabled row is the one the reason is for: the quiz is greyed while it is cooling
            // down, and "10:00 后可做" has to be readable rather than dimmed with the title.
            !enabled -> Palette.Label.copy(alpha = 0.42f)
            // Only the way forward is tinted. 今晚不再限制 is a drastic action, not a suggestion, so
            // it stays a plain row: green there would read as "press me".
            tier == ButtonTier.Prominent -> Palette.Green
            else -> Palette.Label.copy(alpha = 0.42f)
        }
        val plate = when {
            focused -> Palette.Focus
            tier == ButtonTier.Prominent -> Palette.Green.copy(alpha = 0.13f)
            tier == ButtonTier.Quiet -> Color.White.copy(alpha = 0.04f)
            else -> Color.White.copy(alpha = 0.07f)
        }
        val edge = when {
            focused -> Color.Transparent
            tier == ButtonTier.Prominent -> Palette.Green.copy(alpha = 0.26f)
            else -> Ink.EdgeLit
        }
        Row(
            Modifier
                .fillMaxWidth()
                .height(height)
                .clip(RoundedCornerShape(14.dp))
                .background(plate)
                .border(1.dp, edge, RoundedCornerShape(14.dp))
                .padding(start = 10.dp, end = 14.dp),
            verticalAlignment = Alignment.CenterVertically,
            horizontalArrangement = Arrangement.spacedBy(12.dp),
        ) {
            // Always a 30dp cell, glyph or not: a list where the names start at two different x is
            // the kind of thing that reads as sloppy without anyone being able to say why.
            Box(
                Modifier
                    .size(30.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(
                        when {
                            glyph == null -> Color.Transparent
                            focused -> Palette.OnFocus.copy(alpha = 0.08f)
                            else -> fg.copy(alpha = 0.12f)
                        },
                    ),
                contentAlignment = Alignment.Center,
            ) {
                if (glyph != null) GlyphIcon(glyph, fg, 16.dp)
            }
            BasicText(title, style = Type.Body.copy(color = fg, fontWeight = FontWeight.W600), maxLines = 1)
            Spacer(Modifier.weight(1f))
            if (hint != null) {
                BasicText(hint, style = Type.Caption.copy(color = secondary), maxLines = 1)
            }
            // A navigation row points somewhere with a chevron. 返回 already carries a left chevron
            // in its glyph, and two of them pointing opposite ways is a riddle, not a hint.
            if (tier == ButtonTier.Normal) {
                GlyphIcon(Glyph.ChevronRight, secondary, 14.dp)
            }
        }
    }
}

/**
 * The heading of a screen: one glyph, with the accent lit behind it. The glow is drawn outside the
 * node's bounds (nothing in this chain clips until the badge itself), so the icon keeps its size in
 * the layout and the light still has room to fall off.
 */
@Composable
internal fun ScreenIcon(glyph: Glyph, tint: Color, box: Dp = 76.dp, glyphSize: Dp = 32.dp) {
    val corner = RoundedCornerShape(box * 0.32f)
    Box(
        Modifier
            .size(box)
            .drawBehind {
                val r = size.minDimension * 1.6f
                drawCircle(
                    brush = Brush.radialGradient(
                        colorStops = arrayOf(
                            0f to tint.copy(alpha = 0.15f),
                            0.5f to tint.copy(alpha = 0.04f),
                            1f to Color.Transparent,
                        ),
                        center = center,
                        radius = r,
                    ),
                    radius = r,
                    center = center,
                )
            }
            .clip(corner)
            .background(tint.copy(alpha = 0.12f))
            .border(1.dp, tint.copy(alpha = 0.22f), corner),
        contentAlignment = Alignment.Center,
    ) {
        GlyphIcon(glyph, tint, glyphSize)
    }
}

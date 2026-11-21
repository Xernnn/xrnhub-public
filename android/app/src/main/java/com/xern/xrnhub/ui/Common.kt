package com.xern.xrnhub.ui

import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.IntrinsicSize
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyListScope
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Text
import androidx.compose.material3.pulltorefresh.PullToRefreshBox
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import com.xern.xrnhub.R
import com.xern.xrnhub.data.AppModel
import com.xern.xrnhub.data.gaugeShare
import com.xern.xrnhub.data.moment
import com.xern.xrnhub.data.serviceName
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.serialization.KSerializer
import kotlinx.serialization.serializer

val LocalModel = staticCompositionLocalOf<AppModel> { error("No model") }

const val REFRESH_MS = 60_000L
val CARD_SHAPE = RoundedCornerShape(18.dp)
val RANGES = listOf("1h", "6h", "24h", "7d", "30d", "90d").map { it to it }

// ---- pages -------------------------------------------------------------------------------------

/** A page's copy of one API answer. The last copy stays up while the next is on its way. */
class Doc<T : Any>(initial: T?) {
    var value by mutableStateOf(initial)
    var failed by mutableStateOf<String?>(null)
    var loading by mutableStateOf(false)
    var asks by mutableIntStateOf(0)
    private var answered = 0

    fun refresh() {
        asks += 1
    }

    /** The spinner shows only for the round a pull asked for, never for the timed ones after it. */
    fun startRound() {
        loading = asks > answered
        answered = asks
    }
}

/** Keeps a document fresh while the app is on screen, and stops asking when it isn't. */
@Composable
fun <T : Any> rememberDocOf(path: String, serializer: KSerializer<T>, everyMs: Long): Doc<T> {
    val model = LocalModel.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    val doc = remember { Doc<T>(model.cached(path)) }
    LaunchedEffect(path, doc.asks, everyMs) {
        model.cached<T>(path)?.let { doc.value = it }
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (isActive) {
                doc.startRound()
                try {
                    doc.value = model.fetch(path, serializer)
                    doc.failed = null
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    doc.failed = e.message ?: "Something went wrong"
                }
                doc.loading = false
                delay(everyMs)
            }
        }
    }
    return doc
}

@Composable
inline fun <reified T : Any> rememberDoc(path: String, everyMs: Long = REFRESH_MS): Doc<T> =
    rememberDocOf(path, serializer<T>(), everyMs)

/** A scrolling page with its 16 dp gutter, which a pull refreshes. */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun Page(
    refreshing: Boolean,
    onRefresh: () -> Unit,
    state: LazyListState = rememberLazyListState(),
    content: LazyListScope.() -> Unit,
) {
    PullToRefreshBox(refreshing, onRefresh, Modifier.fillMaxSize()) {
        LazyColumn(
            Modifier.fillMaxSize(),
            state = state,
            contentPadding = PaddingValues(start = 16.dp, end = 16.dp, top = 8.dp, bottom = 20.dp),
            verticalArrangement = Arrangement.spacedBy(12.dp),
            content = content,
        )
    }
}

@Composable
fun Empty(text: String, modifier: Modifier = Modifier) {
    Text(text, modifier.padding(vertical = 6.dp), style = Type.body, color = colors.muted)
}

/** A whole page with nothing yet to show: still loading, or why not. */
@Composable
fun Waiting(failed: String?) {
    Box(Modifier.fillMaxSize().padding(24.dp), Alignment.Center) {
        Text(failed ?: "Loading", style = Type.body, color = colors.muted)
    }
}

// ---- parts -------------------------------------------------------------------------------------

@Composable
fun HubCard(
    title: String? = null,
    modifier: Modifier = Modifier,
    action: @Composable (RowScope.() -> Unit)? = null,
    content: @Composable ColumnScope.() -> Unit,
) {
    Column(modifier.fillMaxWidth().clip(CARD_SHAPE).background(colors.surface).padding(14.dp)) {
        if (title != null || action != null) {
            Row(Modifier.fillMaxWidth().padding(bottom = 8.dp), verticalAlignment = Alignment.CenterVertically) {
                Text(title.orEmpty(), Modifier.weight(1f), style = Type.cardTitle, color = colors.text)
                action?.invoke(this)
            }
        }
        content()
    }
}

enum class Tone { Plain, Warn, Bad }

class TileData(
    val label: String,
    val value: String,
    val sub: String? = null,
    val tone: Tone = Tone.Plain,
    val onClick: (() -> Unit)? = null,
    val extra: (@Composable () -> Unit)? = null,
    /** The app the value names, whose tile goes before it. */
    val logo: String? = null,
)

/**
 * One figure with its label, a line under it and, in a set that has them, a small picture of it at
 * the foot. Every tile in a set has the same parts, so a row of them is one height and their pictures
 * line up.
 */
@Composable
private fun Tile(tile: TileData, modifier: Modifier, withSub: Boolean, withExtra: Boolean) {
    val base = modifier.clip(CARD_SHAPE).background(colors.surface)
    val pressable = tile.onClick?.let { base.clickable(onClick = it) } ?: base
    Column(pressable.padding(horizontal = 14.dp, vertical = 12.dp)) {
        Text(tile.label, style = Type.label, color = colors.muted)
        val tint = when (tile.tone) {
            Tone.Plain -> colors.text
            Tone.Warn -> colors.warn
            Tone.Bad -> colors.bad
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
            tile.logo?.takeIf(::hasLogo)?.let {
                Logo(it, 22.dp)
                Spacer(Modifier.width(7.dp))
            }
            Text(tile.value, style = Type.value, color = tint, maxLines = 1, overflow = TextOverflow.Ellipsis)
        }
        if (withSub) Text(tile.sub.orEmpty(), style = Type.small, color = colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
        if (withExtra) {
            Spacer(Modifier.weight(1f).height(6.dp))
            Box(Modifier.fillMaxWidth().height(22.dp), Alignment.BottomStart) { tile.extra?.invoke() }
        }
    }
}

/** Two to a row, each row as tall as its tallest. One left over takes the whole row. */
@Composable
fun Tiles(tiles: List<TileData>) {
    val withSub = tiles.any { !it.sub.isNullOrEmpty() }
    val withExtra = tiles.any { it.extra != null }
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        tiles.chunked(2).forEach { row ->
            Row(Modifier.fillMaxWidth().height(IntrinsicSize.Min), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                row.forEach { Tile(it, Modifier.weight(1f).fillMaxHeight(), withSub, withExtra) }
            }
        }
    }
}

/** A row of choices, one pressed. It fills the width, or with `fill` off it takes only what it needs. */
@Composable
fun Segments(
    options: List<Pair<String, String>>,
    current: String,
    onPick: (String) -> Unit,
    modifier: Modifier = Modifier,
    fill: Boolean = true,
) {
    Row(
        (if (fill) modifier.fillMaxWidth() else modifier).clip(RoundedCornerShape(12.dp)).background(colors.surface2).padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
    ) {
        options.forEach { (id, label) ->
            val on = id == current
            Text(
                label,
                (if (fill) Modifier.weight(1f) else Modifier)
                    .clip(RoundedCornerShape(9.dp))
                    .background(if (on) colors.surface else Color.Transparent)
                    .clickable { onPick(id) }
                    .padding(horizontal = if (fill) 2.dp else 12.dp, vertical = 7.dp),
                style = Type.label,
                color = if (on) colors.text else colors.muted,
                textAlign = TextAlign.Center,
                maxLines = 1,
            )
        }
    }
}

@Composable
fun Dot(on: Boolean, modifier: Modifier = Modifier, off: Color = colors.bad) {
    Box(modifier.size(9.dp).clip(CircleShape).background(if (on) colors.good else off))
}

@DrawableRes
private fun logoOf(service: String): Int? = when (service) {
    "calendar" -> R.drawable.logo_xrncal
    "xrnloops" -> R.drawable.logo_xrnloops
    "xrnrule" -> R.drawable.logo_xrnrule
    "xrntube" -> R.drawable.logo_xrntube
    "xrnhome" -> R.drawable.logo_xrnhome
    "xrnwal" -> R.drawable.logo_xrnwal
    "xrninsta" -> R.drawable.logo_xrninsta
    "xrnphotos" -> R.drawable.logo_xrnphotos
    "xrnfit" -> R.drawable.logo_xrnfit
    "xrnhub" -> R.drawable.logo_xrnhub
    "tailscale" -> R.drawable.logo_tailscale
    "ssh" -> R.drawable.logo_ssh
    else -> null
}

fun hasLogo(service: String): Boolean = logoOf(service) != null

/** Decorative: the name beside it says what it is. */
@Composable
fun Logo(service: String, size: Dp = 20.dp) {
    val logo = logoOf(service) ?: return
    Image(painterResource(logo), null, Modifier.size(size).clip(RoundedCornerShape(size * 0.24f)))
}

@Composable
fun ServiceLabel(name: String, modifier: Modifier = Modifier, style: androidx.compose.ui.text.TextStyle = Type.number) {
    Row(modifier, verticalAlignment = Alignment.CenterVertically) {
        Logo(name)
        Spacer(Modifier.width(7.dp))
        Text(serviceName(name), style = style, color = colors.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
    }
}

@Composable
fun Gauge(share: Double, color: Color = colors.series) {
    Box(Modifier.fillMaxWidth().height(5.dp).clip(RoundedCornerShape(3.dp)).background(colors.surface2)) {
        Box(Modifier.fillMaxWidth(gaugeShare(share)).fillMaxHeight().clip(RoundedCornerShape(3.dp)).background(color))
    }
}

class RankRow(
    val label: String,
    val value: String,
    val share: Double,
    val sub: String? = null,
    val color: Color? = null,
    val service: String? = null,
)

/** Bars longest first, each with its own figure. */
@Composable
fun RankList(rows: List<RankRow>, empty: String = "None") {
    if (rows.isEmpty()) return Empty(empty)
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        rows.forEach { row ->
            Column {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    row.service?.let {
                        Logo(it, 18.dp)
                        Spacer(Modifier.width(6.dp))
                    }
                    Text(row.label, Modifier.weight(1f), style = Type.small, color = colors.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Spacer(Modifier.width(8.dp))
                    Text(row.value, style = Type.number, color = colors.text)
                }
                Spacer(Modifier.height(4.dp))
                Box(Modifier.fillMaxWidth().height(5.dp).clip(RoundedCornerShape(3.dp)).background(colors.surface2)) {
                    Box(
                        Modifier
                            .fillMaxWidth(row.share.coerceIn(0.01, 1.0).toFloat())
                            .fillMaxHeight()
                            .clip(RoundedCornerShape(3.dp))
                            .background(row.color ?: colors.series),
                    )
                }
                row.sub?.let { Text(it, Modifier.padding(top = 2.dp), style = Type.tiny, color = colors.muted) }
            }
        }
    }
}

@Composable
fun Link(text: String, onClick: () -> Unit) {
    Text(text, Modifier.clip(RoundedCornerShape(6.dp)).clickable(onClick = onClick).padding(horizontal = 6.dp, vertical = 2.dp), style = Type.label, color = colors.series)
}

/** Rows of text; the first column takes what's left, with each row's app tile when `logos` names one. */
@Composable
fun Table(head: List<String>, rows: List<List<String>>, logos: List<String?> = emptyList()) {
    val wide = maxOf(head.size, 2)
    Column(Modifier.fillMaxWidth()) {
        Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
            head.forEachIndexed { i, text -> TableCell(text, i, wide, colors.muted, Type.label) }
        }
        rows.forEachIndexed { r, row ->
            Box(Modifier.fillMaxWidth().height(1.dp).background(colors.line))
            Row(Modifier.fillMaxWidth().padding(vertical = 5.dp), verticalAlignment = Alignment.CenterVertically) {
                logos.getOrNull(r)?.takeIf(::hasLogo)?.let {
                    Logo(it, 14.dp)
                    Spacer(Modifier.width(6.dp))
                }
                row.forEachIndexed { i, text -> TableCell(text, i, wide, colors.text, Type.small) }
            }
        }
    }
}

@Composable
private fun RowScope.TableCell(text: String, index: Int, columns: Int, color: Color, style: androidx.compose.ui.text.TextStyle) {
    Text(
        text,
        Modifier.weight(if (index == 0) 1.3f else 1f),
        style = style,
        color = color,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
        textAlign = if (index == 0) androidx.compose.ui.text.style.TextAlign.Start else androidx.compose.ui.text.style.TextAlign.End,
    )
    if (index < columns - 1) Spacer(Modifier.width(6.dp))
}

// ---- rows of figures --------------------------------------------------------------------------

/**
 * A column of [DataRows]: its heading, a fixed `width` or a share of what's left (`weight`), figures
 * set to the right with `end`, and what each row shows in it.
 */
class Col<T>(
    val label: String? = null,
    val width: Dp? = null,
    val weight: Float = 1f,
    val end: Boolean = false,
    val cell: @Composable (T) -> Unit,
)

/**
 * Rows of figures that line up, as the web page's dataRows: a heading row where any column has one,
 * a line between rows, a tap where `onClick` gives one, and `after`, shown under a row across every
 * column (a strip, a message's lines).
 */
@Composable
fun <T> DataRows(
    columns: List<Col<T>>,
    rows: List<T>,
    empty: String = "None",
    onClick: ((T) -> (() -> Unit)?)? = null,
    after: (@Composable (T) -> Unit)? = null,
) {
    if (rows.isEmpty()) return Empty(empty)
    val p = colors
    Column(Modifier.fillMaxWidth()) {
        if (columns.any { it.label != null }) {
            Row(Modifier.fillMaxWidth().padding(bottom = 6.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                columns.forEach { c ->
                    Box(sizedOf(c), contentAlignment = if (c.end) Alignment.CenterEnd else Alignment.CenterStart) {
                        Text(c.label.orEmpty(), style = Type.label, color = p.muted, maxLines = 1)
                    }
                }
            }
        }
        rows.forEachIndexed { i, row ->
            if (i > 0 || columns.any { it.label != null }) Box(Modifier.fillMaxWidth().height(1.dp).background(p.line))
            val tap = onClick?.invoke(row)
            Column(Modifier.fillMaxWidth().then(if (tap != null) Modifier.clickable(onClick = tap) else Modifier)) {
                Row(
                    Modifier.fillMaxWidth().heightIn(min = 38.dp).padding(vertical = 6.dp),
                    horizontalArrangement = Arrangement.spacedBy(10.dp),
                    verticalAlignment = Alignment.CenterVertically,
                ) {
                    columns.forEach { c ->
                        Box(sizedOf(c), contentAlignment = if (c.end) Alignment.CenterEnd else Alignment.CenterStart) { c.cell(row) }
                    }
                }
                after?.invoke(row)
            }
        }
    }
}

private fun <T> RowScope.sizedOf(c: Col<T>): Modifier = if (c.width != null) Modifier.width(c.width) else Modifier.weight(c.weight)

/** Plain words or a figure in a cell, cut short rather than wrapped. */
@Composable
fun Cell(text: String, strong: Boolean = false, muted: Boolean = false, code: Boolean = false, color: Color? = null) {
    Text(
        text,
        style = when {
            code -> Type.code
            strong -> Type.number
            else -> Type.small
        },
        color = color ?: if (muted) colors.muted else colors.text,
        maxLines = 1,
        overflow = TextOverflow.Ellipsis,
    )
}

/** An app's tile and name in a cell. */
@Composable
fun NameCell(service: String, code: String? = null) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        if (hasLogo(service)) {
            Logo(service, 18.dp)
            Spacer(Modifier.width(7.dp))
        }
        if (code != null) Cell(code, code = true) else Cell(serviceName(service), strong = true)
    }
}

enum class Mood { Plain, Good, Warn, Bad }

/** A word or code on its colour: a sign-in in or failed, an answer's status, a log line's level. */
@Composable
fun Code(text: String, mood: Mood) {
    val p = colors
    val (back, ink) = when (mood) {
        Mood.Good -> p.goodBg to p.good
        Mood.Warn -> p.warnBg to p.warn
        Mood.Bad -> p.badBg to p.bad
        Mood.Plain -> p.surface2 to p.muted
    }
    Text(
        text,
        Modifier.clip(RoundedCornerShape(6.dp)).background(back).padding(horizontal = 6.dp, vertical = 1.dp),
        style = Type.label.copy(fontFeatureSettings = "tnum"),
        color = ink,
        maxLines = 1,
    )
}

/** The mood of an HTTP status, by its class. */
fun moodOf(status: Int): Mood = when (status / 100) {
    2 -> Mood.Good
    4 -> Mood.Warn
    5 -> Mood.Bad
    else -> Mood.Plain
}

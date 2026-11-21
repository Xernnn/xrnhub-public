package com.xern.xrnhub.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectHorizontalDragGestures
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.RowScope
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Rect
import androidx.compose.ui.geometry.RoundRect
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.PathEffect
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.StrokeJoin
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.layout.onSizeChanged
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.data.DASH
import com.xern.xrnhub.data.Lane
import com.xern.xrnhub.data.Mark
import com.xern.xrnhub.data.niceTop
import com.xern.xrnhub.data.niceTopBytes
import com.xern.xrnhub.data.nowSeconds
import com.xern.xrnhub.data.segments
import com.xern.xrnhub.data.serviceName
import com.xern.xrnhub.data.stackTop
import com.xern.xrnhub.data.timeLabel
import com.xern.xrnhub.data.tipTime
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.min
import kotlin.math.roundToInt
import kotlinx.coroutines.delay

// The charts, timelines and the small pictures on tiles and in tables. There's no pointer to hover
// with on a phone and a finger hides whatever is under it, so a press or a drag moves a crosshair and
// the figures change in the key line above the plot, in the same place every time.
//
// Every chart card has the same parts at the same heights, as the web page's: a title row, one line
// of key, the plot with its scale on the right, and its time axis. A key that doesn't fit scrolls
// sideways rather than wrapping, so a chart with many series is no taller than one with one, and its
// table takes exactly the chart's place.

private const val READOUT_MS = 4000L
private const val DENSE_BARS = 60
private val LINE_WIDTH = 2.dp
private val PLOT_HEIGHT = 150.dp
private val KEY_HEIGHT = 20.dp
private val AXIS_HEIGHT = 16.dp
private val LANE_HEIGHT = 22.dp
private val LANE_NAME = 88.dp
private val LANE_FIGURE = 30.dp
private const val TICK_SHARE = 0.004 // a mark shorter than this share of the span is drawn as a tick
private const val WEEK_DAYS = 7
private const val DAY_S = 86_400.0

@Immutable
class Line(val name: String, val values: List<Double?>, val color: Color, val dashed: Boolean = false, val service: String? = null)

/** Lines over time. A missing value, or a pause longer than `gap` ms, leaves a hole. */
@Immutable
class LineData(val at: List<Long>, val from: Long, val to: Long, val gap: Long?, val lines: List<Line>)

@Immutable
class Stack(val name: String, val values: List<Double?>, val color: Color, val service: String? = null)

/** Stacked columns: what each slot holds, what it's called on the axis and in full. */
@Immutable
class StackData(val labels: List<String>, val tips: List<String>, val stacks: List<Stack>)

// ---- the key ----------------------------------------------------------------------------------

/** One thing in a key: an app's tile, or a swatch and a name, then its figure. */
@Immutable
class KeyEntry(val name: String, val value: String?, val color: Color?, val service: String? = null)

@Composable
fun KeyItem(entry: KeyEntry) {
    Row(verticalAlignment = Alignment.CenterVertically) {
        val logo = entry.service?.takeIf(::hasLogo)
        if (logo != null) {
            Logo(logo, 16.dp)
            Spacer(Modifier.width(5.dp))
        } else if (entry.color != null) {
            Box(Modifier.size(9.dp).clip(RoundedCornerShape(3.dp)).background(entry.color))
            Spacer(Modifier.width(5.dp))
            if (entry.name.isNotEmpty()) {
                Text(entry.name, style = Type.small, color = colors.muted, maxLines = 1)
                Spacer(Modifier.width(5.dp))
            }
        }
        entry.value?.let { Text(it, style = Type.number, color = colors.text, maxLines = 1) }
    }
}

/** The key's one line: a time while a finger is on the plot, then each series' figure. */
@Composable
fun KeyLine(time: String?, entries: List<KeyEntry>, modifier: Modifier = Modifier) {
    Row(
        modifier.fillMaxWidth().height(KEY_HEIGHT).horizontalScroll(rememberScrollState()),
        horizontalArrangement = Arrangement.spacedBy(14.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        time?.let { Text(it, style = Type.small, color = colors.muted, maxLines = 1) }
        entries.forEach { KeyItem(it) }
    }
}

// ---- the card ---------------------------------------------------------------------------------

/** What a chart's table shows: its column names and rows, newest first. */
class ChartTable(val head: List<String>, val rows: List<List<String>>)

/**
 * A card holding a chart, with its figures one tap away in exactly the chart's space. `side` sits in
 * the title row before the Table link.
 */
@Composable
fun ChartCard(
    title: String,
    table: () -> ChartTable,
    side: (@Composable RowScope.() -> Unit)? = null,
    chart: @Composable () -> Unit,
) {
    var showTable by rememberSaveable { mutableStateOf(false) }
    var tall by remember { mutableIntStateOf(0) }
    val density = LocalDensity.current
    HubCard(title, action = {
        side?.invoke(this)
        Link(if (showTable) "Chart" else "Table") { showTable = !showTable }
    }) {
        if (showTable && tall > 0) {
            val t = table()
            Box(Modifier.fillMaxWidth().height(with(density) { tall.toDp() }).verticalScroll(rememberScrollState())) {
                Table(t.head, t.rows)
            }
        } else {
            Column(Modifier.fillMaxWidth().onSizeChanged { tall = it.height }) { chart() }
        }
    }
}

/** The plot with its scale in a column of its own on the right, so no figure sits over a bar or a line. */
@Composable
private fun Plotted(top: String?, middle: String?, axis: List<String>, plot: @Composable (Modifier) -> Unit) {
    val measurer = rememberTextMeasurer()
    val style = Type.tiny
    val density = LocalDensity.current
    val widest = listOfNotNull(top, middle).maxOfOrNull { measurer.measure(it, style).size.width } ?: 0
    val gutter: Dp = with(density) { widest.toDp() } + 6.dp
    Row(Modifier.fillMaxWidth().height(PLOT_HEIGHT)) {
        plot(Modifier.weight(1f).fillMaxHeight())
        Box(Modifier.width(gutter).fillMaxHeight()) {
            // Each figure is centred on its gridline; the top one hangs just above the plot's top.
            top?.let { Text(it, Modifier.align(Alignment.TopEnd).offset(y = (-7).dp), style = style, color = colors.muted, maxLines = 1) }
            middle?.let { Text(it, Modifier.align(Alignment.CenterEnd), style = style, color = colors.muted, maxLines = 1) }
        }
    }
    Row(
        Modifier.fillMaxWidth().padding(top = 4.dp, end = gutter).height(AXIS_HEIGHT),
        horizontalArrangement = Arrangement.SpaceBetween,
    ) {
        axis.forEach { Text(it, style = style, color = colors.muted, maxLines = 1, overflow = TextOverflow.Clip) }
    }
}

/** A plot that follows a finger: `pick` turns a share of the width into a slot, or -1. */
private fun Modifier.follow(key: Any?, pick: (Float) -> Int, go: (Int) -> Unit, leave: () -> Unit): Modifier =
    pointerInput(key) { detectTapGestures { go(pick(it.x / size.width)) } }
        .pointerInput(key) {
            detectHorizontalDragGestures(
                onDragEnd = leave,
                onDragCancel = leave,
                onHorizontalDrag = { change, _ -> go(pick(change.position.x / size.width)) },
            )
        }

@Composable
private fun rememberTouch(): androidx.compose.runtime.MutableIntState {
    val touched = remember { mutableIntStateOf(-1) }
    LaunchedEffect(touched.intValue) {
        if (touched.intValue >= 0) {
            delay(READOUT_MS)
            touched.intValue = -1
        }
    }
    return touched
}

// ---- lines ------------------------------------------------------------------------------------

@Composable
fun LineCard(
    title: String,
    data: LineData,
    format: (Double) -> String,
    floorValue: Double = 0.0,
    zero: Boolean = true,
    inBytes: Boolean = false,
) {
    val span = data.to - data.from
    ChartCard(title, {
        ChartTable(
            listOf("When") + data.lines.map { it.name.ifEmpty { title } },
            data.at.indices.reversed().map { i ->
                listOf(tipTime(data.at[i], span)) + data.lines.map { line -> line.values.getOrNull(i)?.let(format) ?: DASH }
            },
        )
    }) { LinePlot(title, data, format, floorValue, zero, inBytes) }
}

@Composable
private fun LinePlot(title: String, data: LineData, format: (Double) -> String, floorValue: Double, zero: Boolean, inBytes: Boolean) {
    val touch = rememberTouch()
    val shown = touch.intValue.takeIf { it in data.at.indices }
    val span = data.to - data.from
    val seen = remember(data) { data.lines.flatMap { it.values }.filterNotNull() }
    val highest = seen.maxOrNull() ?: 0.0
    val low = if (zero) 0.0 else floor(min(floorValue, seen.minOrNull() ?: floorValue) / 5) * 5
    val top = (low + (if (inBytes) niceTopBytes(highest - low) else niceTop(highest - low))).let { if (it > low) it else low + 1 }
    val runs = remember(data) { data.lines.map { segments(data.at, it.values, data.gap) } }
    val many = data.lines.size > 1

    KeyLine(
        shown?.let { tipTime(data.at[it], span) },
        data.lines.map { line ->
            val value = if (shown != null) line.values.getOrNull(shown) else line.values.lastOrNull { it != null }
            KeyEntry(line.name, value?.let(format) ?: DASH, line.color.takeIf { many }, line.service)
        },
        Modifier.padding(bottom = 8.dp),
    )
    val grid = colors.line
    val base = colors.outline
    val cross = colors.muted
    val dotRing = colors.surface
    val any = seen.isNotEmpty()
    Plotted(
        if (any) format(top) else null,
        if (any) format((top + low) / 2) else null,
        listOf(timeLabel(data.from, span), timeLabel((data.from + data.to) / 2, span), timeLabel(data.to, span)),
    ) { modifier ->
        Box(
            modifier
                .semantics { contentDescription = "$title chart" }
                .follow(data, { share -> nearest(data, share) }, { touch.intValue = it }, { touch.intValue = -1 }),
        ) {
            Canvas(Modifier.fillMaxWidth().height(PLOT_HEIGHT)) {
                val stroke = LINE_WIDTH.toPx()
                val plotTop = stroke
                val plotHeight = size.height - stroke * 1.5f
                drawLine(grid, Offset(0f, plotTop), Offset(size.width, plotTop), 1f)
                drawLine(grid, Offset(0f, plotTop + plotHeight / 2), Offset(size.width, plotTop + plotHeight / 2), 1f)
                drawLine(base, Offset(0f, plotTop + plotHeight), Offset(size.width, plotTop + plotHeight), 1f)
                if (!any) return@Canvas
                val xOf = { i: Int -> ((data.at[i] - data.from).toFloat() / (data.to - data.from).coerceAtLeast(1).toFloat()) * size.width }
                val yOf = { v: Double -> plotTop + plotHeight * (1f - ((v - low) / (top - low)).toFloat()) }
                data.lines.forEachIndexed { l, line ->
                    for (run in runs[l]) {
                        val path = Path()
                        run.forEachIndexed { n, i ->
                            val x = xOf(i)
                            val y = yOf(line.values[i]!!)
                            if (n == 0) path.moveTo(x, y) else path.lineTo(x, y)
                        }
                        if (run.size == 1) {
                            drawCircle(line.color, stroke, Offset(xOf(run[0]), yOf(line.values[run[0]]!!)))
                            continue
                        }
                        if (!many) {
                            val area = Path().apply {
                                addPath(path)
                                lineTo(xOf(run.last()), plotTop + plotHeight)
                                lineTo(xOf(run[0]), plotTop + plotHeight)
                                close()
                            }
                            drawPath(area, line.color.copy(alpha = 0.12f))
                        }
                        val dash = if (line.dashed && many) PathEffect.dashPathEffect(floatArrayOf(stroke * 3.5f, stroke * 2.5f)) else null
                        drawPath(path, line.color, style = Stroke(stroke, cap = StrokeCap.Round, join = StrokeJoin.Round, pathEffect = dash))
                    }
                }
                shown?.let { i ->
                    val x = xOf(i)
                    drawLine(cross, Offset(x, 0f), Offset(x, size.height), 1.dp.toPx())
                    data.lines.forEach { line ->
                        line.values.getOrNull(i)?.let { v ->
                            drawCircle(dotRing, 5.dp.toPx(), Offset(x, yOf(v)))
                            drawCircle(line.color, 3.5.dp.toPx(), Offset(x, yOf(v)))
                        }
                    }
                }
            }
            if (!any) Text("None", Modifier.align(Alignment.Center), style = Type.small, color = colors.muted)
        }
    }
}

private fun nearest(data: LineData, share: Float): Int {
    val wanted = data.from + share * (data.to - data.from)
    var best = -1
    var gap = Double.MAX_VALUE
    data.at.forEachIndexed { i, t ->
        val away = abs(t - wanted.toDouble())
        if (away < gap && data.lines.any { it.values.getOrNull(i) != null }) {
            gap = away
            best = i
        }
    }
    return best
}

// ---- stacks -----------------------------------------------------------------------------------

@Composable
fun StackCard(
    title: String,
    data: StackData,
    inBytes: Boolean = false,
    side: (@Composable RowScope.() -> Unit)? = null,
    format: (Double) -> String,
) {
    ChartCard(title, {
        ChartTable(
            listOf("When") + data.stacks.map { it.name.ifEmpty { title } },
            data.labels.indices.reversed().map { i -> listOf(data.tips[i]) + data.stacks.map { format(it.values.getOrNull(i) ?: 0.0) } },
        )
    }, side) { StackPlot(title, data, format, inBytes) }
}

@Composable
private fun StackPlot(title: String, data: StackData, format: (Double) -> String, inBytes: Boolean) {
    val touch = rememberTouch()
    val n = data.labels.size
    val shown = touch.intValue.takeIf { it in 0 until n }
    val totals = remember(data) { List(n) { i -> data.stacks.sumOf { it.values.getOrNull(i) ?: 0.0 } } }
    val top = remember(totals, inBytes) { stackTop(totals, inBytes) }
    val many = data.stacks.size > 1

    // Until a column is pressed, each figure is the series' total across the chart; pressed, the
    // column's total comes first, then each series in it.
    val entries = if (shown == null) {
        data.stacks.map { s -> KeyEntry(s.name, format(s.values.sumOf { it ?: 0.0 }), s.color.takeIf { many }, s.service) }
    } else {
        val inColumn = data.stacks.filter { (it.values.getOrNull(shown) ?: 0.0) > 0 }
        (if (many) listOf(KeyEntry("", format(totals[shown]), null)) else emptyList()) +
            (if (many) inColumn else data.stacks).map { s ->
                KeyEntry(s.name, format(s.values.getOrNull(shown) ?: 0.0), s.color.takeIf { many }, s.service)
            }
    }
    KeyLine(shown?.let { data.tips[it] }, entries, Modifier.padding(bottom = 8.dp))
    val any = totals.any { it > 0 }
    val grid = colors.line
    val base = colors.outline
    val cross = colors.surface2
    Plotted(
        if (any) format(top) else null,
        if (any) format(top / 2) else null,
        if (n > 0) listOf(data.labels[0], data.labels[n / 2], data.labels[n - 1]) else emptyList(),
    ) { modifier ->
        Box(
            modifier
                .semantics { contentDescription = "$title chart" }
                .follow(data, { share -> if (n == 0) -1 else minOf(n - 1, (share * n).toInt()).coerceAtLeast(0) }, { touch.intValue = it }, { touch.intValue = -1 }),
        ) {
            Canvas(Modifier.fillMaxWidth().height(PLOT_HEIGHT)) {
                drawLine(grid, Offset(0f, 0f), Offset(size.width, 0f), 1f)
                drawLine(grid, Offset(0f, size.height / 2), Offset(size.width, size.height / 2), 1f)
                drawLine(base, Offset(0f, size.height), Offset(size.width, size.height), 1f)
                if (!any) return@Canvas
                val slot = size.width / n
                val gap = (if (n > DENSE_BARS) 0f else 2.dp.toPx()).coerceAtMost(slot * 0.3f)
                val bar = (slot - gap).coerceAtLeast(1f)
                val seam = 1.dp.toPx()
                val corner = minOf(3.dp.toPx(), bar / 2)
                shown?.let { drawRect(cross, Offset(it * slot, 0f), Size(slot, size.height)) }
                for (i in 0 until n) {
                    var y = size.height
                    val x = i * slot + gap / 2
                    val parts = data.stacks.filter { (it.values.getOrNull(i) ?: 0.0) > 0 }
                    parts.forEachIndexed { p, stack ->
                        val tall = ((stack.values[i]!! / top) * size.height).toFloat().coerceAtLeast(1f)
                        if (p == parts.lastIndex) {
                            val rect = Rect(x, y - tall, x + bar, y)
                            drawPath(Path().apply { addRoundRect(RoundRect(rect, topLeft = CornerRadius(corner), topRight = CornerRadius(corner))) }, stack.color)
                        } else {
                            // The surface shows through between segments, so neighbours stay apart.
                            drawRect(stack.color, Offset(x, y - tall + seam), Size(bar, (tall - seam).coerceAtLeast(1f)))
                        }
                        y -= tall
                    }
                }
            }
            if (!any) Text("None", Modifier.align(Alignment.Center), style = Type.small, color = colors.muted)
        }
    }
}

// ---- small pictures ---------------------------------------------------------------------------

/** A small line for a tile. */
@Composable
fun Spark(values: List<Double?>, color: Color = colors.series) = Sparks(listOf(values to color))

/** Several small lines on one scale: a network tile's in and out. */
@Composable
fun Sparks(lines: List<Pair<List<Double?>, Color>>) {
    val seen = remember(lines) { lines.flatMap { it.first }.filterNotNull() }
    Canvas(Modifier.fillMaxWidth().height(22.dp)) {
        if (seen.size < 2) return@Canvas
        val low = seen.min()
        val range = (seen.max() - low).takeIf { it > 0 } ?: 1.0
        lines.forEach { (values, color) ->
            val step = size.width / (values.size - 1).coerceAtLeast(1)
            val path = Path()
            var open = false
            values.forEachIndexed { i, value ->
                if (value == null) {
                    open = false
                    return@forEachIndexed
                }
                val y = size.height * (1f - 0.1f - 0.8f * ((value - low) / range).toFloat())
                if (open) path.lineTo(i * step, y) else path.moveTo(i * step, y)
                open = true
            }
            drawPath(path, color, style = Stroke(1.5.dp.toPx(), cap = StrokeCap.Round, join = StrokeJoin.Round))
        }
    }
}

/** A row of marks, one each, in a colour: services up or down, days since a reboot. */
@Composable
fun Marks(tones: List<Color>) {
    Row(Modifier.fillMaxWidth().height(8.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
        tones.forEach { Box(Modifier.weight(1f).fillMaxHeight().clip(RoundedCornerShape(2.dp)).background(it)) }
    }
}

/** Four bars, as a phone shows its signal. */
@Composable
fun Signal(lit: Int) {
    Row(Modifier.height(22.dp), horizontalArrangement = Arrangement.spacedBy(3.dp), verticalAlignment = Alignment.Bottom) {
        (1..4).forEach { n ->
            Box(Modifier.width(7.dp).fillMaxHeight(n / 4f).clip(RoundedCornerShape(2.dp)).background(if (n <= lit) colors.series else colors.surface2))
        }
    }
}

/** A bar for a table cell: `share` of the longest, in `color`; from the right under a figure set to the right. */
@Composable
fun Bar(share: Double, color: Color, modifier: Modifier = Modifier, tall: Dp = 8.dp, fromEnd: Boolean = false) {
    Box(modifier.fillMaxWidth().height(tall), contentAlignment = if (fromEnd) Alignment.CenterEnd else Alignment.CenterStart) {
        if (share > 0) {
            Box(Modifier.fillMaxWidth(share.coerceIn(0.015, 1.0).toFloat()).fillMaxHeight().clip(RoundedCornerShape(tall / 2)).background(color))
        }
    }
}

/** A bar of parts side by side, each a share of a whole that is itself `length` of the longest. */
@Composable
fun SplitBar(parts: List<Pair<Double, Color>>, length: Double = 1.0, modifier: Modifier = Modifier, tall: Dp = 8.dp) {
    Box(modifier.fillMaxWidth().height(tall)) {
        Row(Modifier.fillMaxWidth(length.coerceIn(0.015, 1.0).toFloat()).fillMaxHeight().clip(RoundedCornerShape(tall / 2))) {
            parts.filter { it.first > 0 }.forEach { (share, color) -> Box(Modifier.weight(share.toFloat()).fillMaxHeight().background(color)) }
        }
    }
}

/** The whole split by share, on a track: what the server holds, how requests were answered. */
@Composable
fun ShareBar(parts: List<Pair<Double, Color>>) {
    Row(Modifier.fillMaxWidth().height(12.dp).clip(RoundedCornerShape(6.dp)).background(colors.surface2)) {
        parts.filter { it.first > 0 }.forEach { (share, color) -> Box(Modifier.weight(share.toFloat()).fillMaxHeight().background(color)) }
    }
}

/** A figure with a thin bar under it: its share of the column's largest. */
@Composable
fun FigureBar(text: String, share: Double, color: Color) {
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.End) {
        Text(text, style = Type.small, color = colors.text, maxLines = 1)
        Spacer(Modifier.height(3.dp))
        Bar(share, color, tall = 3.dp, fromEnd = true)
    }
}

/** A cell for each day: all up, partly, down, or not watched. `onPick` gets the day pressed. */
@Composable
fun UptimeStrip(days: List<Double?>, modifier: Modifier = Modifier, onPick: ((Int) -> Unit)? = null) {
    val up = colors.good
    val some = colors.warn
    val out = colors.bad
    val none = colors.surface2
    val press = if (onPick == null) Modifier else Modifier.pointerInput(days) {
        detectTapGestures { onPick(((it.x / size.width) * days.size).toInt().coerceIn(0, days.lastIndex)) }
    }
    Canvas(modifier.fillMaxWidth().height(14.dp).then(press)) {
        if (days.isEmpty()) return@Canvas
        val gap = 2.dp.toPx()
        val cell = (size.width - gap * (days.size - 1)) / days.size
        val corner = CornerRadius(2.dp.toPx())
        days.forEachIndexed { i, share ->
            val color = when {
                share == null -> none
                share >= 0.999 -> up
                share > 0 -> some
                else -> out
            }
            drawRoundRect(color, Offset(i * (cell + gap), 0f), Size(cell, size.height), corner)
        }
    }
}

// ---- timelines --------------------------------------------------------------------------------

/** Each kind of mark's colour: what happened to a service, or how a request was answered. */
@Composable
fun kindColor(kind: String): Color {
    val p = colors
    return when (kind) {
        "down", "s5" -> p.bad
        "restart", "s4" -> p.warn
        "problem" -> p.charts[1]
        "reboot" -> p.charts[0]
        "gap" -> p.outline
        "s2" -> p.good
        else -> p.series
    }
}

/** What a tapped mark was, in the key line's place. */
@Composable
private fun MarkLine(mark: Mark) {
    Row(Modifier.fillMaxWidth().height(KEY_HEIGHT).horizontalScroll(rememberScrollState()), verticalAlignment = Alignment.CenterVertically) {
        Box(Modifier.size(9.dp).clip(RoundedCornerShape(3.dp)).background(kindColor(mark.kind)))
        Spacer(Modifier.width(6.dp))
        Text(mark.title, style = Type.number, color = colors.text, maxLines = 1)
        mark.words.forEach {
            Spacer(Modifier.width(10.dp))
            Text(it, style = Type.small, color = colors.muted, maxLines = 1)
        }
    }
}

/**
 * Marks along a stretch of time, a lane each, from `from` to `to` in seconds. A mark too short to
 * see is a tick; one still going runs to now. A tap on a mark puts what it was in the key line,
 * which otherwise holds `key`; or, given `onPick`, hands it on and keeps no line of its own. `axis`
 * labels the foot, and `weeks` draws a line a week.
 */
@Composable
fun Timeline(
    lanes: List<Lane>,
    from: Double,
    to: Double,
    key: List<KeyEntry>?,
    axis: List<String>?,
    weeks: Boolean = true,
    now: Double = nowSeconds(),
    onPick: ((Mark?) -> Unit)? = null,
) {
    // Not keyed to the lanes: the live feed's change every few seconds, and a picked mark should stay.
    var picked by remember { mutableStateOf<Mark?>(null) }
    LaunchedEffect(picked) {
        if (picked != null) {
            delay(READOUT_MS * 2)
            picked = null
        }
    }
    val named = lanes.any { it.name != null || it.place != null }
    val counted = lanes.any { it.figure != null }
    Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
        val mark = picked
        if (onPick == null) {
            if (mark != null) MarkLine(mark) else if (key != null) KeyLine(null, key)
        }
        lanes.forEach { lane ->
            Row(Modifier.fillMaxWidth().height(LANE_HEIGHT), verticalAlignment = Alignment.CenterVertically) {
                if (named) {
                    Row(Modifier.width(LANE_NAME), verticalAlignment = Alignment.CenterVertically) {
                        if (lane.name != null && hasLogo(lane.name)) {
                            Logo(lane.name, 18.dp)
                            Spacer(Modifier.width(6.dp))
                        }
                        Text(
                            lane.place ?: lane.name?.let(::serviceName).orEmpty(),
                            style = Type.small.copy(fontWeight = FontWeight.SemiBold),
                            color = colors.text, maxLines = 1, overflow = TextOverflow.Ellipsis,
                        )
                    }
                    Spacer(Modifier.width(8.dp))
                }
                Track(lane.marks, from, to, now, weeks, mark) {
                    picked = it
                    onPick?.invoke(it)
                }
                if (counted) {
                    Text(lane.figure.orEmpty(), Modifier.width(LANE_FIGURE), style = Type.number, color = colors.text, textAlign = TextAlign.End, maxLines = 1)
                }
            }
        }
        if (axis != null) {
            Row(Modifier.fillMaxWidth().height(AXIS_HEIGHT)) {
                if (named) Spacer(Modifier.width(LANE_NAME + 8.dp))
                Row(Modifier.weight(1f), horizontalArrangement = Arrangement.SpaceBetween) {
                    axis.forEach { Text(it, style = Type.tiny, color = colors.muted, maxLines = 1) }
                }
                if (counted) Spacer(Modifier.width(LANE_FIGURE))
            }
        }
    }
}

/** Where a mark starts and ends along a track `width` wide. */
private fun spanOf(m: Mark, from: Double, to: Double, now: Double, width: Float): Pair<Float, Float> {
    val span = (to - from).coerceAtLeast(1.0)
    val start = ((maxOf(m.from, from) - from) / span * width).toFloat()
    val end = ((minOf(m.to ?: now, to) - from) / span * width).toFloat()
    return start to maxOf(start, end)
}

@Composable
private fun RowScope.Track(marks: List<Mark>, from: Double, to: Double, now: Double, weeks: Boolean, picked: Mark?, pick: (Mark?) -> Unit) {
    val p = colors
    val days = ((to - from) / DAY_S).roundToInt()
    val tones = marks.map { kindColor(it.kind) }
    Canvas(
        Modifier
            .weight(1f)
            .fillMaxHeight()
            .clip(RoundedCornerShape(5.dp))
            .background(p.surface2)
            .pointerInput(marks, from, to) {
                detectTapGestures { at ->
                    // The mark nearest the finger, if one is within a fingertip of it.
                    val reach = 14.dp.toPx()
                    val away = { m: Mark ->
                        val (start, end) = spanOf(m, from, to, now, size.width.toFloat())
                        if (at.x in start..end) 0f else minOf(abs(at.x - start), abs(at.x - end))
                    }
                    pick(marks.minByOrNull(away)?.takeIf { away(it) <= reach })
                }
            },
    ) {
        if (weeks && days > WEEK_DAYS) {
            // A line each week, so a mark can be placed by eye.
            for (w in 1..((days - 1) / WEEK_DAYS)) {
                val x = (1f - (w * WEEK_DAYS.toFloat()) / days) * size.width
                drawLine(p.surface, Offset(x, 0f), Offset(x, size.height), 1.dp.toPx())
            }
        }
        val inset = 4.dp.toPx()
        val tick = 4.dp.toPx()
        marks.forEachIndexed { i, m ->
            if ((m.to ?: now) < from || m.from > to) return@forEachIndexed
            val (x0, x1) = spanOf(m, from, to, now, size.width)
            val wide = (x1 - x0) / size.width > TICK_SHARE
            val left = if (wide) x0 else x0 - tick / 2
            val width = if (wide) (x1 - x0).coerceAtLeast(tick) else tick
            val top = if (wide) inset else inset - 1.dp.toPx()
            val tall = size.height - top * 2
            drawRoundRect(tones[i], Offset(left, top), Size(width, tall), CornerRadius(3.dp.toPx()))
            if (m == picked) {
                val pad = 2.dp.toPx()
                drawRoundRect(p.text, Offset(left - pad, top - pad), Size(width + pad * 2, tall + pad * 2), CornerRadius(4.dp.toPx()), style = Stroke(1.5.dp.toPx()))
            }
        }
    }
}

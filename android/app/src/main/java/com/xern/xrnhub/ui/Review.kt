package com.xern.xrnhub.ui

import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyListState
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.Text
import androidx.compose.material3.rememberModalBottomSheetState
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.graphics.lerp
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.SpanStyle
import androidx.compose.ui.text.buildAnnotatedString
import androidx.compose.ui.text.drawText
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.rememberTextMeasurer
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.withStyle
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.R
import com.xern.xrnhub.data.Body
import com.xern.xrnhub.data.DayDoc
import com.xern.xrnhub.data.DayHabit
import com.xern.xrnhub.data.DayHealth
import com.xern.xrnhub.data.Hours
import com.xern.xrnhub.data.ReviewDoc
import com.xern.xrnhub.data.Temptations
import com.xern.xrnhub.data.clock
import com.xern.xrnhub.data.count
import com.xern.xrnhub.data.dayPlain
import com.xern.xrnhub.data.dayShort
import com.xern.xrnhub.data.duration
import com.xern.xrnhub.data.levelOf
import com.xern.xrnhub.data.logText
import com.xern.xrnhub.data.money
import com.xern.xrnhub.data.monthOf
import com.xern.xrnhub.data.plain
import com.xern.xrnhub.data.weekChange
import java.time.LocalDate

private const val REVIEW_MS = 5 * 60_000L
private const val YEAR_WEEKS = 53
private const val WEEK_DAYS = 7
private const val FARTHEST_DAY = 398L
private val CELL = 15.dp
private val CELL_GAP = 3.dp
private val MONTHS_HEIGHT = 16.dp
private val MONTH_NAME = 12.dp
private val HABIT_COLUMN = 42.dp
private val BODY_COLUMN = 58.dp
private val WEEKDAYS = listOf("M", "", "W", "", "F", "", "S")

private fun addDays(iso: String, days: Long): String = LocalDate.parse(iso).plusDays(days).toString()

@Composable
private fun Stats(doc: ReviewDoc) {
    val t = doc.temptations
    val trend = doc.trend
    Tiles(listOf(
        TileData(
            "Habits this week", trend?.let { "${it.thisWeek}%" } ?: "–", trend?.let { weekChange(it.thisWeek, it.lastWeek, "pts") },
            extra = trend?.let { { Gauge(it.thisWeek / 100.0, colors.good) } },
        ),
        TileData(
            "Days logged", "${doc.loggedDays}/${doc.windowDays}",
            extra = { Gauge(doc.loggedDays.toDouble() / doc.windowDays, colors.good) },
        ),
        TileData("Stopped this week", "${t.thisWeek}", weekChange(t.thisWeek, t.lastWeek, "")),
    ))
}

// ---- the year ----------------------------------------------------------------------------------

@Composable
private fun YearCard(doc: ReviewDoc, open: (String) -> Unit) {
    val p = colors
    val levels = p.levels
    HubCard("Every day") {
        val today = remember(doc.day) { LocalDate.parse(doc.day) }
        val shift = remember(doc.day) { (today.dayOfWeek.value - 1).toLong() }
        val lastMonday = remember(doc.day) { today.minusDays(shift) }
        val firstMonday = remember(doc.day) { lastMonday.minusWeeks(YEAR_WEEKS - 1L) }
        val byDay = remember(doc.year) { doc.year.associate { it.day to it.share } }
        val measurer = rememberTextMeasurer()
        val scroll = rememberScrollState()
        val density = LocalDensity.current
        LaunchedEffect(doc.day, scroll.maxValue) { scroll.scrollTo(scroll.maxValue) }
        val step = with(density) { (CELL + CELL_GAP).toPx() }
        val monthsTall = with(density) { MONTHS_HEIGHT.toPx() }
        Row {
            Column(Modifier.padding(top = MONTHS_HEIGHT, end = 5.dp)) {
                WEEKDAYS.forEach { letter ->
                    Box(Modifier.height(CELL + CELL_GAP), Alignment.CenterStart) {
                        Text(letter, style = Type.tiny, color = p.muted)
                    }
                }
            }
            Box(Modifier.weight(1f).horizontalScroll(scroll)) {
                Canvas(
                    Modifier
                        // A month that starts in the last column still needs room for its name.
                        .size((CELL + CELL_GAP) * YEAR_WEEKS + MONTH_NAME, MONTHS_HEIGHT + (CELL + CELL_GAP) * WEEK_DAYS)
                        .semantics { contentDescription = "Habits done each day for the past year" }
                        .pointerInput(doc.day) {
                            detectTapGestures { at ->
                                val week = (at.x / step).toInt()
                                val weekday = ((at.y - monthsTall) / step).toInt()
                                if (at.y < monthsTall || week !in 0 until YEAR_WEEKS || weekday !in 0 until WEEK_DAYS) return@detectTapGestures
                                val day = firstMonday.plusDays(week * 7L + weekday)
                                if (!day.isAfter(today)) open(day.toString())
                            }
                        },
                ) {
                    val cell = CELL.toPx()
                    val corner = CornerRadius(3.dp.toPx())
                    var lastMonth = -1
                    var lastLabel = -9
                    for (w in 0 until YEAR_WEEKS) {
                        val monday = firstMonday.plusWeeks(w.toLong())
                        if (monday.monthValue != lastMonth && w - lastLabel >= 3) {
                            drawText(measurer, monthOf(monday), Offset(w * step, 0f), Type.tiny.copy(color = p.muted))
                            lastLabel = w
                        }
                        lastMonth = monday.monthValue
                        for (d in 0 until WEEK_DAYS) {
                            val day = monday.plusDays(d.toLong())
                            if (day.isAfter(today)) continue
                            val at = Offset(w * step, monthsTall + d * step)
                            drawRoundRect(levels[levelOf(byDay[day.toString()])], at, Size(cell, cell), corner)
                            if (day == today) {
                                drawRoundRect(p.text, at, Size(cell, cell), corner, style = Stroke(1.5.dp.toPx()))
                            }
                        }
                    }
                }
            }
        }
    }
}

// ---- habits ------------------------------------------------------------------------------------

@Composable
private fun HabitsCard(doc: ReviewDoc) {
    val items = doc.habits.items
    if (items.isEmpty()) return
    val p = colors
    HubCard("Habits") {
        Row(Modifier.fillMaxWidth().padding(bottom = 4.dp)) {
            Spacer(Modifier.weight(1f))
            listOf("30d", "Streak", "Best").forEach {
                Text(it, Modifier.width(HABIT_COLUMN), style = Type.tiny, color = p.muted, textAlign = TextAlign.End)
            }
        }
        items.forEach { row ->
            val tint = habitColor(row.color)
            Column(Modifier.padding(vertical = 5.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(row.name, Modifier.weight(1f), style = Type.small, color = p.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(row.pct?.let { "$it%" } ?: "–", Modifier.width(HABIT_COLUMN), style = Type.number, color = p.text, textAlign = TextAlign.End)
                    Text(if (row.streak > 0) "${row.streak}" else "–", Modifier.width(HABIT_COLUMN), style = Type.number, color = p.text, textAlign = TextAlign.End)
                    Text("${row.best}", Modifier.width(HABIT_COLUMN), style = Type.number, color = p.muted, textAlign = TextAlign.End)
                }
                Spacer(Modifier.height(3.dp))
                Canvas(
                    Modifier
                        .fillMaxWidth()
                        .height(10.dp)
                        .semantics { contentDescription = "${row.name}: ${row.strip.count { it == 1 }} of 30 days done" },
                ) {
                    val gap = 2.dp.toPx()
                    val n = row.strip.size.coerceAtLeast(1)
                    val cell = (size.width - gap * (n - 1)) / n
                    val corner = CornerRadius(2.dp.toPx())
                    row.strip.forEachIndexed { i, value ->
                        val color = when (value) {
                            1 -> tint
                            0 -> p.bad.copy(alpha = 0.35f)
                            2 -> p.outline
                            else -> p.surface2
                        }
                        drawRoundRect(color, Offset(i * (cell + gap), 0f), Size(cell, size.height), corner)
                    }
                }
            }
        }
    }
}

// ---- the body ----------------------------------------------------------------------------------

private fun minutes(n: Double): String = duration(n * 60)

/**
 * A measure on the health card. Steps and sleep are amounts, drawn as bars from zero; the rest are
 * levels, drawn as dots between the month's lowest and highest. Each strip is its own scale.
 */
private class BodyLine(val key: String, val label: String, val amount: Boolean, val text: (Double) -> String)

private val BODY_LINES = listOf(
    BodyLine("steps", "Steps", amount = true) { count(it) },
    BodyLine("asleep", "Asleep", amount = true, ::minutes),
    BodyLine("resting", "Resting HR", amount = false) { count(it) },
    BodyLine("weightKg", "Weight, kg", amount = false) { plain(it, 1) },
    BodyLine("spo2", "SpO2, %", amount = false) { count(it) },
    BodyLine("stress", "Stress", amount = false) { count(it) },
)

@Composable
private fun BodyCard(body: Body, open: (String) -> Unit) {
    val p = colors
    val lines = BODY_LINES.filter { it.key in body.summary }
    if (lines.isEmpty()) return
    HubCard("Health", action = {
        listOf("30d avg", "Latest").forEach {
            Text(it, Modifier.width(BODY_COLUMN), style = Type.tiny, color = p.muted, textAlign = TextAlign.End)
        }
    }) {
        lines.forEach { line ->
            val numbers = body.summary.getValue(line.key)
            val values = body.days.map { it.value(line.key) }
            Column(Modifier.padding(vertical = 5.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(line.label, Modifier.weight(1f), style = Type.small, color = p.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(numbers.avg?.let(line.text) ?: "–", Modifier.width(BODY_COLUMN), style = Type.small, color = p.muted, textAlign = TextAlign.End)
                    Text(numbers.latest?.let(line.text) ?: "–", Modifier.width(BODY_COLUMN), style = Type.number, color = p.text, textAlign = TextAlign.End)
                }
                Spacer(Modifier.height(4.dp))
                BodyStrip(line, values, p.series, p.line) { i -> body.days.getOrNull(i)?.let { open(it.date) } }
            }
        }
    }
}

@Composable
private fun BodyStrip(line: BodyLine, values: List<Double?>, tone: Color, base: Color, tap: (Int) -> Unit) {
    val known = values.filterNotNull()
    val top = known.maxOrNull() ?: 1.0
    val low = known.minOrNull() ?: 0.0
    Canvas(
        Modifier
            .fillMaxWidth()
            .height(22.dp)
            .semantics { contentDescription = "${line.label}, the last 30 days" }
            .pointerInput(values) {
                detectTapGestures { at -> tap(((at.x / size.width) * values.size).toInt().coerceIn(0, values.lastIndex)) }
            },
    ) {
        val gap = 2.dp.toPx()
        val n = values.size.coerceAtLeast(1)
        val cell = (size.width - gap * (n - 1)) / n
        if (line.amount) drawRect(base, Offset(0f, size.height - 1.dp.toPx()), Size(size.width, 1.dp.toPx()))
        values.forEachIndexed { i, value ->
            if (value == null) return@forEachIndexed
            val x = i * (cell + gap)
            if (line.amount) {
                val tall = (value / (if (top > 0) top else 1.0)).toFloat().coerceAtLeast(0.04f) * size.height
                drawRoundRect(tone, Offset(x, size.height - tall), Size(cell, tall), CornerRadius(2.dp.toPx()))
            } else {
                val share = if (top == low) 0.5f else ((value - low) / (top - low)).toFloat()
                val y = size.height * (1f - (0.08f + share * 0.84f))
                drawCircle(tone, radius = (cell / 2).coerceAtMost(3.dp.toPx()), center = Offset(x + cell / 2, y))
            }
        }
    }
}

// ---- hours and temptations ---------------------------------------------------------------------

@Composable
private fun HoursCard(hours: Hours) {
    if (hours.series.isEmpty()) return
    val names = hours.weeks.map(::dayPlain)
    val outline = colors.outline
    val tips = hours.weeks.map { "Week of ${dayPlain(it)}" }
    StackCard(
        "Hours",
        StackData(names, tips, hours.series.map { Stack(it.name, it.hours, calendarColor(it.color) ?: outline) }),
    ) { "${plain(it, 1)} h" }
}

@Composable
private fun TemptationsCard(t: Temptations) {
    if (t.total == 0) return
    val p = colors
    var picked by remember { mutableIntStateOf(-1) }
    HubCard("Stopped", action = { Text("${t.total} · ${t.days}d", style = Type.small, color = p.muted) }) {
        val most = t.apps.maxOfOrNull { it.count }?.takeIf { it > 0 } ?: 1
        RankList(t.apps.map { RankRow(it.name, "${it.count}", it.count.toDouble() / most) })
        Spacer(Modifier.height(10.dp))
        Row {
            Text("By hour", Modifier.weight(1f), style = Type.label, color = p.muted)
            t.hours.getOrNull(picked)?.let { Text("$picked:00 · $it", style = Type.small, color = p.text) }
        }
        Spacer(Modifier.height(4.dp))
        val peak = (t.hours.maxOrNull() ?: 1).coerceAtLeast(1)
        val tone = p.series
        Canvas(
            Modifier
                .fillMaxWidth()
                .height(56.dp)
                .semantics { contentDescription = "Stopped apps by hour of the day" }
                .pointerInput(t.hours) {
                    detectTapGestures { picked = ((it.x / size.width) * t.hours.size).toInt().coerceIn(0, t.hours.lastIndex) }
                },
        ) {
            val slot = size.width / t.hours.size.coerceAtLeast(1)
            val bar = slot - 2.dp.toPx()
            t.hours.forEachIndexed { hour, n ->
                val tall = (n.toFloat() / peak) * size.height
                if (n > 0) {
                    drawRoundRect(tone, Offset(hour * slot, size.height - tall), Size(bar, tall), CornerRadius(2.dp.toPx()))
                }
            }
        }
        // Each label starts where its hour's bar does, a quarter of the way along at a time.
        Row(Modifier.fillMaxWidth().padding(top = 3.dp)) {
            listOf(0, 6, 12, 18).forEach { Text("$it:00", Modifier.weight(1f), style = Type.tiny, color = p.muted) }
        }
    }
}

// ---- one day -----------------------------------------------------------------------------------

private fun habitWord(h: DayHabit): String = when {
    h.done -> "Done"
    h.skipped -> "Skipped"
    else -> "Not done"
}

private val AGENDA_TIME = java.time.format.DateTimeFormatter.ofPattern("HH:mm")

private val STAGE_NAMES = listOf("Deep", "Light", "REM", "Asleep", "Awake")

/** Deep darkest, light paler, REM apart, and time awake in amber. */
@Composable
private fun stageColor(name: String): Color {
    val p = colors
    return when (name) {
        "Deep" -> p.charts[1]
        "Light" -> lerp(p.charts[1], p.surface, 0.45f)
        "REM" -> p.charts[0]
        "Awake" -> p.warn
        else -> p.outline
    }
}

private fun range(low: Double, high: Double): String? = if (low == high) null else "${count(low)}–${count(high)}"

@Composable
private fun HealthRow(label: String, value: String, sub: String? = null) {
    val p = colors
    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(label, style = Type.body, color = p.text)
        sub?.let { Text("  $it", Modifier.weight(1f), style = Type.small, color = p.muted, maxLines = 1, overflow = TextOverflow.Ellipsis) }
            ?: Spacer(Modifier.weight(1f))
        Text(value, style = Type.number, color = p.text)
    }
}

/** The day's steps, sleep and readings; the sleep's stages where the source said them. */
@Composable
private fun HealthRows(day: DayHealth) {
    val p = colors
    day.steps?.let { HealthRow("Steps", count(it)) }
    day.sleep?.let { sleep ->
        HealthRow("Asleep", minutes(sleep.minutes), "${clock(sleep.start)}–${clock(sleep.end)}")
        val stages = listOf(sleep.deep, sleep.light, sleep.rem, sleep.asleep, sleep.awake).zip(STAGE_NAMES)
            .filter { (spent, name) -> spent > 0 && !(name == "Asleep" && spent == sleep.minutes) }
        if (stages.isNotEmpty()) {
            val tones = stages.map { (_, name) -> stageColor(name) }
            // The night end to end, each stage as long as it lasted; each name below is in its colour.
            Row(Modifier.fillMaxWidth().padding(top = 2.dp, bottom = 5.dp).height(8.dp), horizontalArrangement = Arrangement.spacedBy(2.dp)) {
                stages.forEachIndexed { i, (spent, _) ->
                    Box(Modifier.weight(spent.toFloat()).fillMaxHeight().clip(RoundedCornerShape(2.dp)).background(tones[i]))
                }
            }
            Text(
                buildAnnotatedString {
                    stages.forEachIndexed { i, (spent, name) ->
                        if (i > 0) append("   ")
                        withStyle(SpanStyle(color = tones[i], fontWeight = FontWeight.Medium)) { append(name) }
                        append(" ${minutes(spent)}")
                    }
                },
                style = Type.small,
                color = p.muted,
            )
        }
    }
    day.heartRate?.resting?.let { HealthRow("Resting HR", "${count(it)} bpm") }
    day.heartRate?.let { heart ->
        val avg = heart.avg ?: return@let
        HealthRow("Heart rate", "${count(avg)} bpm", if (heart.min != null && heart.max != null) range(heart.min, heart.max) else null)
    }
    day.weightKg?.let { HealthRow("Weight", "${plain(it, 1)} kg") }
    day.spo2?.let { HealthRow("SpO2", "${count(it.avg)}%", range(it.min, it.max)) }
    day.stress?.let { HealthRow("Stress", count(it.avg), range(it.min, it.max)) }
}

private fun agendaTime(start: String): String =
    runCatching { AGENDA_TIME.format(java.time.LocalDateTime.parse(start)) }.getOrDefault("")

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun DaySheet(first: String, last: String, opened: String, close: () -> Unit) {
    var day by remember { mutableStateOf(opened) }
    val doc = rememberDoc<DayDoc>("api/day?date=$day")
    val sheet = rememberModalBottomSheetState(skipPartiallyExpanded = true)
    ModalBottomSheet({ close() }, sheetState = sheet, containerColor = colors.surface) {
        DayContent(day, first, last, doc.value?.takeIf { it.day == day }, doc.failed) { day = it }
    }
}

/** One day's sheet, from what's been read; `onDay` moves to another. */
@Composable
internal fun DayContent(day: String, first: String, last: String, shown: DayDoc?, failed: String?, onDay: (String) -> Unit) {
    val p = colors
    Column(
        Modifier.fillMaxWidth().verticalScroll(rememberScrollState()).navigationBarsPadding().padding(start = 16.dp, end = 16.dp, bottom = 20.dp),
    ) {
        val counted = shown?.habits?.items.orEmpty().filter { it.counts }
        Row(verticalAlignment = Alignment.CenterVertically) {
            IconButton({ onDay(addDays(day, -1)) }, enabled = day > first) {
                Icon(painterResource(R.drawable.ic_back), "Day before", tint = if (day > first) p.text else p.outline)
            }
            IconButton({ onDay(addDays(day, 1)) }, enabled = day < last) {
                Icon(painterResource(R.drawable.ic_forward), "Day after", tint = if (day < last) p.text else p.outline)
            }
            Spacer(Modifier.width(6.dp))
            Text(dayShort(day), Modifier.weight(1f), style = Type.heading, color = p.text)
            if (counted.isNotEmpty()) Text("${counted.count { it.done }}/${counted.size}", style = Type.cardTitle, color = p.muted)
        }
        if (shown == null) {
            Empty(failed ?: "Loading", Modifier.padding(top = 10.dp))
        } else {
            if (shown.habits.items.isEmpty() && shown.calendar.items.isEmpty() && shown.log.isEmpty() && shown.health == null) {
                Empty("None", Modifier.padding(top = 10.dp))
            }
            shown.habits.items.forEach { h ->
                Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.size(10.dp).clip(CircleShape).background(habitColor(h.color)))
                    Spacer(Modifier.width(9.dp))
                    Text(h.name, Modifier.weight(1f), style = Type.body, color = p.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(habitWord(h), style = Type.small, color = if (h.done) p.good else p.muted)
                }
            }
            if (shown.calendar.items.isNotEmpty()) {
                Text("Calendar", Modifier.padding(top = 12.dp, bottom = 4.dp), style = Type.label, color = p.muted)
                shown.calendar.items.forEach { e ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(if (e.allDay) "" else agendaTime(e.start), Modifier.width(46.dp), style = Type.small, color = p.muted)
                        Box(Modifier.size(9.dp).clip(CircleShape).background(calendarColor(e.color) ?: p.outline))
                        Spacer(Modifier.width(8.dp))
                        Text(e.title, Modifier.weight(1f), style = Type.body, color = p.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
            shown.health?.let { health ->
                Text("Health", Modifier.padding(top = 12.dp, bottom = 4.dp), style = Type.label, color = p.muted)
                HealthRows(health)
            }
            if (shown.log.isNotEmpty()) {
                Text("Log", Modifier.padding(top = 12.dp, bottom = 4.dp), style = Type.label, color = p.muted)
                shown.log.forEach { e ->
                    Row(Modifier.fillMaxWidth().padding(vertical = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                        Text(clock(e.at.toLong()), Modifier.width(46.dp), style = Type.small, color = p.muted)
                        Text(logText(e), Modifier.weight(1f), style = Type.body, color = p.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        e.cents?.let { Text(money(it), style = Type.small, color = p.muted) }
                    }
                }
            }
        }
    }
}

// ---- the page ----------------------------------------------------------------------------------

@Composable
fun ReviewPage() {
    val doc = rememberDoc<ReviewDoc>("api/review", REVIEW_MS)
    val data = doc.value ?: return Waiting(doc.failed)
    var day by rememberSaveable { mutableStateOf<String?>(null) }
    val list = rememberLazyListState()

    ReviewBody(data, list, { day = it }, doc.loading, doc::refresh)
    day?.let { opened ->
        DaySheet(addDays(data.day, -FARTHEST_DAY), data.day, opened) { day = null }
    }
}

@Composable
internal fun ReviewBody(
    data: ReviewDoc,
    list: LazyListState = rememberLazyListState(),
    openDay: (String) -> Unit = {},
    refreshing: Boolean = false,
    refresh: () -> Unit = {},
) {
    Page(refreshing, refresh, list) {
        item { Stats(data) }
        item { YearCard(data, openDay) }
        item { HabitsCard(data) }
        data.health?.let { body -> item { BodyCard(body, openDay) } }
        data.hours?.let { hours -> item { HoursCard(hours) } }
        item { TemptationsCard(data.temptations) }
    }
}

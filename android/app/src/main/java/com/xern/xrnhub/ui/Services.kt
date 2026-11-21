package com.xern.xrnhub.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.R
import com.xern.xrnhub.data.DASH
import com.xern.xrnhub.data.FIT
import com.xern.xrnhub.data.FIT_KEPT_BY
import com.xern.xrnhub.data.FitCard
import com.xern.xrnhub.data.HistoryDoc
import com.xern.xrnhub.data.KINDS
import com.xern.xrnhub.data.KIND_NAMES
import com.xern.xrnhub.data.Lane
import com.xern.xrnhub.data.LogGroup
import com.xern.xrnhub.data.LogRow
import com.xern.xrnhub.data.LogStatsDoc
import com.xern.xrnhub.data.LogsDoc
import com.xern.xrnhub.data.Mark
import com.xern.xrnhub.data.NowDoc
import com.xern.xrnhub.data.ServiceRow
import com.xern.xrnhub.data.ServicesDoc
import com.xern.xrnhub.data.besideHub
import com.xern.xrnhub.data.bytes
import com.xern.xrnhub.data.count
import com.xern.xrnhub.data.dayOf
import com.xern.xrnhub.data.dayPlain
import com.xern.xrnhub.data.duration
import com.xern.xrnhub.data.fitAgo
import com.xern.xrnhub.data.incidentLanes
import com.xern.xrnhub.data.kindOf
import com.xern.xrnhub.data.levelValues
import com.xern.xrnhub.data.markOf
import com.xern.xrnhub.data.messageWords
import com.xern.xrnhub.data.moment
import com.xern.xrnhub.data.percent
import com.xern.xrnhub.data.plain
import com.xern.xrnhub.data.sentDay
import com.xern.xrnhub.data.serviceName
import com.xern.xrnhub.data.sinceNow
import com.xern.xrnhub.data.stripDays
import com.xern.xrnhub.data.uptimeShare
import java.net.URLEncoder
import java.time.LocalDate
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch

// Services: each one's state, then one in full, with the only button here that changes anything.
// xrnfit is listed too, after the hub that keeps what it sends. What went wrong is a timeline, a lane
// per service over the strips' thirty days: an outage is a bar, a restart a tick, its words a tap away.

private const val TOAST_MS = 4000L
private const val SHOWN_MESSAGES = 8
private const val MESSAGE_LINES = 12

private fun upFor(s: ServiceRow): String = if (s.up) s.since?.let { sinceNow(it) } ?: "Up" else "Down"

private fun encoded(text: String) = URLEncoder.encode(text, "UTF-8").replace("+", "%20")

/** Green while xrnfit sends, amber once six hours pass with nothing, grey before it ever has. */
@Composable
fun FitDot(card: FitCard?) {
    Dot(card?.lastIn != null && !card.late, off = if (card?.lastIn == null) colors.surface2 else colors.warn)
}

/** A row of the services' table: a service, or xrnfit, which has no process here. */
private class ServiceItem(val name: String, val s: ServiceRow?, val fit: FitCard?, val requests: Double?)

/** The services as rows that line up: up for, memory and requests an hour, each with its bar, and the strip under. */
@Composable
internal fun ServiceRows(now: NowDoc?, doc: ServicesDoc?, go: Go) {
    val p = colors
    val rows = now?.services.orEmpty()
    if (rows.isEmpty()) return Empty("Loading")
    val fit = now?.health
    val hour = now?.hour.orEmpty()
    val items = besideHub(rows.map { it.name }, rows.map { s -> ServiceItem(s.name, s, null, hour[s.name]?.let { it.reqs - it.polls }) },
        fit?.let { ServiceItem(FIT, null, it, hour[FIT]?.let { t -> t.reqs - t.polls }) })
    val mostRss = rows.maxOfOrNull { it.rss ?: 0.0 }?.coerceAtLeast(1.0) ?: 1.0
    val mostReqs = items.maxOfOrNull { it.requests ?: 0.0 }?.coerceAtLeast(1.0) ?: 1.0
    DataRows(
        listOf<Col<ServiceItem>>(
            Col("Service") { item ->
                Row(verticalAlignment = Alignment.CenterVertically) {
                    if (item.s != null) Dot(item.s.up) else FitDot(item.fit)
                    Spacer(Modifier.width(8.dp))
                    NameCell(item.name)
                }
            },
            Col("Up for", width = 58.dp, end = true) { item ->
                when {
                    item.s != null -> Cell(upFor(item.s), muted = item.s.up, color = if (item.s.up) null else p.bad)
                    else -> Cell(item.fit?.let(::fitAgo) ?: DASH, muted = item.fit?.late != true, color = if (item.fit?.late == true) p.warn else null)
                }
            },
            Col("Memory", width = 56.dp, end = true) { item ->
                if (item.s != null) FigureBar(bytes(item.s.rss), (item.s.rss ?: 0.0) / mostRss, colorOf(item.name)) else Cell(DASH, muted = true)
            },
            Col("Req/h", width = 48.dp, end = true) { item ->
                FigureBar(item.requests?.let { count(it) } ?: DASH, (item.requests ?: 0.0) / mostReqs, colorOf(item.name))
            },
        ),
        items,
        onClick = { item -> { go("services", item.name) } },
        after = { item ->
            val days = if (item.s != null) doc?.uptime?.get(item.name) else doc?.fitDays?.takeIf { it.isNotEmpty() }?.map { sentDay(it).first }
            days?.let { UptimeStrip(it, Modifier.padding(start = 17.dp, bottom = 8.dp)) }
        },
    )
}

/** The kinds of thing that happen, as the timeline colours them, for its key. */
@Composable
private fun kindKey(): List<KeyEntry> = KINDS.map { KeyEntry(KIND_NAMES.getValue(it), null, kindColor(it)) }

/** Thirty days of incidents, a lane for each service or place anything happened to. */
@Composable
fun IncidentTimeline(lanes: List<Lane>) {
    if (lanes.isEmpty()) return Empty("None")
    val (from, to) = stripDays()
    Timeline(lanes, from, to, kindKey(), listOf(dayOf(from), dayOf((from + to) / 2), dayOf(to - 1)))
}

@Composable
fun ServicesPage(go: Go) {
    val model = LocalModel.current
    val doc = rememberDoc<ServicesDoc>("api/server/services")
    ServicesBody(model.now, doc.value, go, doc.loading, doc::refresh)
}

@Composable
internal fun ServicesBody(now: NowDoc?, doc: ServicesDoc?, go: Go, refreshing: Boolean = false, refresh: () -> Unit = {}) {
    Page(refreshing, refresh, rememberLazyListState()) {
        item { HubCard { ServiceRows(now, doc, go) } }
        item {
            HubCard("Incidents") {
                val names = now?.services.orEmpty().map { it.name }.ifEmpty { doc?.uptime?.keys?.toList().orEmpty() }
                val order = besideHub(names, names, FIT)
                IncidentTimeline(incidentLanes(doc?.timeline ?: doc?.incidents.orEmpty(), order))
            }
        }
    }
}

@Composable
fun LogLine(row: LogRow, withService: Boolean) {
    val tint = when (row.level) {
        "error" -> colors.bad
        "warn" -> colors.warn
        else -> colors.text
    }
    Column(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text(moment(row.at / 1000), style = Type.tiny, color = colors.muted)
            if (withService) {
                Spacer(Modifier.width(8.dp))
                Logo(row.service, 14.dp)
                Spacer(Modifier.width(4.dp))
                Text(serviceName(row.service), style = Type.tiny, color = colors.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
            }
        }
        Text(row.text, style = Type.code, color = tint)
    }
}

// ---- warnings by message ----------------------------------------------------------------------

/** A message's newest lines, fetched when it's opened. */
@Composable
private fun MessageLines(group: LogGroup) {
    val doc = rememberDoc<LogsDoc>("api/server/logs?service=${encoded(group.service)}&sig=${encoded(group.sig)}&limit=$MESSAGE_LINES")
    Column(Modifier.fillMaxWidth().padding(start = 8.dp, bottom = 8.dp)) {
        val lines = doc.value?.lines
        when {
            lines == null -> Empty(doc.failed ?: "Loading")
            lines.isEmpty() -> Empty("None")
            else -> lines.forEach { LogLine(it, false) }
        }
    }
}

/**
 * Warnings and errors by what they say, the most pressing few with all of them a tap away; a tap
 * on one opens its newest lines.
 */
@Composable
fun MessageRows(groups: List<LogGroup>, withService: Boolean) {
    var all by rememberSaveable { mutableStateOf(false) }
    var open by remember { mutableStateOf(emptySet<String>()) }
    val most = groups.maxOfOrNull { it.count }?.coerceAtLeast(1.0) ?: 1.0
    val keyOf = { g: LogGroup -> "${g.service}\n${g.level}\n${g.sig}" }
    val shown = if (all) groups else groups.take(SHOWN_MESSAGES)
    DataRows(
        listOfNotNull<Col<LogGroup>>(
            Col(width = 44.dp) { g -> Code(if (g.level == "error") "Error" else "Warn", if (g.level == "error") Mood.Bad else Mood.Warn) },
            if (withService) Col(width = 18.dp) { g -> Logo(g.service, 18.dp) } else null,
            Col { g ->
                Column {
                    Cell(messageWords(g), code = true)
                    Text(moment(g.last / 1000), style = Type.tiny, color = colors.muted, maxLines = 1)
                }
            },
            Col(width = 40.dp, end = true) { g ->
                Column(horizontalAlignment = Alignment.End) {
                    Cell(count(g.count), strong = true)
                    Bar(g.count / most, if (g.level == "error") colors.bad else colors.warn, tall = 3.dp, fromEnd = true)
                }
            },
        ),
        shown,
        onClick = { g ->
            {
                val k = keyOf(g)
                open = if (k in open) open - k else open + k
            }
        },
        after = { g -> if (keyOf(g) in open) MessageLines(g) },
    )
    if (groups.size > SHOWN_MESSAGES) {
        Row(Modifier.fillMaxWidth().padding(top = 6.dp)) {
            Link(if (all) "Fewer" else "All ${groups.size}") { all = !all }
        }
    }
}

/** A service's warnings and errors over the range, from the log's counts. */
@Composable
private fun warningStacks(stats: LogStatsDoc, levels: Map<String, List<Double>>?): StackData {
    val (labels, tips) = bucketLabels(stats.at, stats.bucket)
    return StackData(labels, tips, listOf(
        Stack("Errors", levelValues(stats, levels, "error"), colors.bad),
        Stack("Warnings", levelValues(stats, levels, "warn"), colors.warn),
    ))
}

@Composable
private fun WarningsCard(stats: LogStatsDoc?, logs: () -> Unit) {
    HubCard("Warnings and errors", action = { Link("Logs", logs) }) {
        val groups = stats?.groups
        if (groups == null) Empty("Loading") else MessageRows(groups, withService = false)
    }
}

// ---- the thirty days card ---------------------------------------------------------------------

/**
 * A cell a day, then the service's own lane, with the month's figures in the card's head. A tap on a
 * day or a mark puts what it was in the head's place for a moment.
 */
@Composable
private fun StripCard(sums: List<Pair<String, String>>, days: List<Double?>?, dayWords: (Double?) -> String, marks: List<Mark>?) {
    var picked by remember { mutableStateOf<String?>(null) }
    LaunchedEffect(picked) {
        if (picked != null) {
            delay(TOAST_MS * 2)
            picked = null
        }
    }
    HubCard("Last 30 days", action = {
        val shownNow = picked
        if (shownNow != null) {
            Text(shownNow, style = Type.small, color = colors.text, maxLines = 1, overflow = TextOverflow.Ellipsis)
        } else {
            sums.forEach { (value, label) ->
                Text(value, Modifier.padding(start = 10.dp), style = Type.number, color = colors.text)
                Text(" $label", style = Type.small, color = colors.muted)
            }
        }
    }) {
        if (days == null) {
            Empty("None")
        } else {
            UptimeStrip(days.map { it }) { i ->
                val day = LocalDate.now().minusDays((days.lastIndex - i).toLong()).toString()
                picked = "${dayPlain(day)} · ${dayWords(days[i])}"
            }
        }
        if (marks != null) {
            val (from, to) = stripDays()
            Spacer(Modifier.height(6.dp))
            Timeline(listOf(Lane(null, null, null, marks)), from, to, key = null, axis = null) { mark ->
                picked = mark?.let { listOf(it.title, *it.words.toTypedArray()).joinToString(" · ") }
            }
        }
    }
}

private fun uptimeWords(share: Double?): String = when {
    share == null -> "Not watched"
    share >= 0.999 -> "Up all day"
    else -> "Up ${plain(share * 100, 1)}%"
}

// ---- xrnfit's page ----------------------------------------------------------------------------

/** xrnfit in full: when it last sent, what it sent and read, its thirty days and its warnings. */
@Composable
fun FitDetail(go: Go) {
    val model = LocalModel.current
    var range by rememberSaveable { mutableStateOf("24h") }
    val history = rememberDoc<HistoryDoc>("api/server/history?range=$range&service=$FIT")
    val services = rememberDoc<ServicesDoc>("api/server/services")
    val stats = rememberDoc<LogStatsDoc>("api/server/logstats?range=$range&service=$FIT")
    FitDetailBody(
        model.now, history.value, services.value, stats.value, range, { range = it }, go,
        history.loading, { history.refresh(); services.refresh(); stats.refresh() },
    )
}

@Composable
internal fun FitDetailBody(
    now: NowDoc?,
    past: HistoryDoc?,
    servicesDoc: ServicesDoc?,
    stats: LogStatsDoc?,
    range: String,
    onRange: (String) -> Unit,
    go: Go,
    refreshing: Boolean = false,
    refresh: () -> Unit = {},
) {
    val card = now?.health
    val hub = now?.services?.find { it.name == FIT_KEPT_BY }
    val hour = now?.hour?.get(FIT)
    val p = colors

    Page(refreshing, refresh, rememberLazyListState()) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton({ go("services", null) }) { Icon(painterResource(R.drawable.ic_back), "Back to services", tint = p.text) }
                FitDot(card)
                Spacer(Modifier.width(8.dp))
                ServiceLabel(FIT, Modifier.weight(1f), Type.cardTitle)
            }
        }
        item { Segments(RANGES, range, onRange) }
        item {
            Tiles(listOf(
                TileData(
                    "Last sent", card?.lastIn?.let { sinceNow(it) } ?: DASH, card?.lastIn?.let { moment(it) },
                    tone = if (card?.late == true) Tone.Warn else Tone.Plain,
                ),
                TileData("Readings · 7d", card?.week?.let { count(it) } ?: DASH),
                TileData("Requests · 1h", hour?.let { count(it.reqs) } ?: DASH),
                TileData("Server", serviceName(FIT_KEPT_BY), hub?.let(::upFor), onClick = { go("services", FIT_KEPT_BY) }, logo = FIT_KEPT_BY),
            ))
        }
        past?.let { doc ->
            item {
                val (labels, tips) = bucketLabels(doc.at, doc.bucket)
                val traffic = doc.traffic[FIT]
                val writes = traffic?.writes ?: doc.at.map { null }
                val reads = traffic?.reqs.orEmpty().mapIndexed { i, n -> n?.let { it - (writes.getOrNull(i) ?: 0.0) } }
                StackCard("Requests", StackData(labels, tips, listOf(Stack("Sent", writes, p.charts[0]), Stack("Read", reads, p.charts[1])))) { count(it) }
            }
        }
        item {
            val days = servicesDoc?.fitDays?.takeIf { it.isNotEmpty() }
            val sent = days.orEmpty().filterNotNull()
            StripCard(
                listOf(count(sent.count { it > 0 }.toDouble()) to "days sent", count(sent.sum()) to "readings"),
                days?.map { sentDay(it).first },
                { share -> if (share == null) "Nothing yet" else if (share > 0) "Sent" else "Nothing sent" },
                null,
            )
        }
        item { WarningsCard(stats) { go("logs", FIT) } }
    }
}

// ---- a service's page -------------------------------------------------------------------------

@Composable
fun ServiceDetail(name: String, go: Go) {
    val model = LocalModel.current
    val scope = rememberCoroutineScope()
    var range by rememberSaveable { mutableStateOf("24h") }
    var asking by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    var said by remember { mutableStateOf<String?>(null) }
    val history = rememberDoc<HistoryDoc>("api/server/history?range=$range&service=$name")
    val services = rememberDoc<ServicesDoc>("api/server/services")
    val stats = rememberDoc<LogStatsDoc>("api/server/logstats?range=$range&service=$name")

    LaunchedEffect(said) {
        if (said != null) {
            delay(TOAST_MS)
            said = null
        }
    }
    if (asking) {
        AlertDialog(
            onDismissRequest = { asking = false },
            icon = { Logo(name, 32.dp) },
            title = { Text("Restart ${serviceName(name)}?", style = Type.heading) },
            confirmButton = {
                TextButton({
                    asking = false
                    busy = true
                    scope.launch {
                        said = model.restart(name)
                        busy = false
                        stats.refresh()
                    }
                }) { Text("Restart") }
            },
            dismissButton = { TextButton({ asking = false }) { Text("Cancel") } },
        )
    }
    ServiceDetailBody(
        name, model.now, history.value, services.value, stats.value, range, { range = it }, said, busy, { asking = true }, go,
        history.loading, { history.refresh(); services.refresh(); stats.refresh() },
    )
}

@Composable
internal fun ServiceDetailBody(
    name: String,
    now: NowDoc?,
    past: HistoryDoc?,
    servicesDoc: ServicesDoc?,
    stats: LogStatsDoc?,
    range: String,
    onRange: (String) -> Unit,
    said: String?,
    busy: Boolean,
    askRestart: () -> Unit,
    go: Go,
    refreshing: Boolean = false,
    refresh: () -> Unit = {},
) {
    val s = now?.services?.find { it.name == name }
    val hour = now?.hour?.get(name)
    val color = colorOf(name)

    Page(refreshing, refresh, rememberLazyListState()) {
        item {
            Row(verticalAlignment = Alignment.CenterVertically) {
                IconButton({ go("services", null) }) { Icon(painterResource(R.drawable.ic_back), "Back to services", tint = colors.text) }
                Dot(s?.up == true)
                Spacer(Modifier.width(8.dp))
                ServiceLabel(name, Modifier.weight(1f), Type.cardTitle)
                if (s?.restartable == true) {
                    OutlinedButton(askRestart, enabled = !busy, shape = RoundedCornerShape(10.dp)) {
                        Text(if (busy) "Restarting" else "Restart", style = Type.label)
                    }
                }
            }
        }
        said?.let { words -> item { Text(words, style = Type.body, color = colors.text) } }
        item { Segments(RANGES, range, onRange) }
        item {
            Tiles(listOf(
                TileData("Up for", s?.let(::upFor) ?: DASH, s?.since?.let { "Since ${moment(it)}" }, tone = if (s != null && !s.up) Tone.Bad else Tone.Plain),
                TileData("CPU", percent(s?.cpu)),
                TileData("Memory", bytes(s?.rss), s?.threads?.let { "$it threads" }),
                TileData("Requests · 1h", hour?.let { count(it.reqs) } ?: DASH),
                TileData("CPU time", duration(s?.cpuTotal)),
                TileData("Read", bytes(s?.read)),
                TileData("Written", bytes(s?.written)),
                TileData("To storage", bytes(s?.diskWritten)),
            ))
        }
        past?.takeIf { it.at.isNotEmpty() }?.let { doc ->
            val at = doc.at.map { it * 1000 }
            val to = at.last() + doc.bucket * 1000
            val series = doc.services[name]
            val blank = at.map { null }
            item { LineCard("CPU", LineData(at, at.first(), to, null, listOf(Line("", series?.cpu ?: blank, color))), { percent(it) }) }
            item { LineCard("Memory", LineData(at, at.first(), to, null, listOf(Line("", series?.rss ?: blank, color))), { bytes(it) }, inBytes = true) }
            item {
                val (labels, tips) = bucketLabels(doc.at, doc.bucket)
                StackCard("Requests", StackData(labels, tips, listOf(Stack("", doc.traffic[name]?.reqs ?: blank, color)))) { count(it) }
            }
        }
        stats?.let { counted -> item { StackCard("Warnings", warningStacks(counted, counted.services[name])) { count(it) } } }
        item {
            val own = (servicesDoc?.timeline ?: servicesDoc?.incidents).orEmpty().filter { it.target == name }
            val days = servicesDoc?.uptime?.get(name)
            StripCard(
                listOf(
                    (uptimeShare(days)?.let { "$it%" } ?: DASH) to "up",
                    count(own.count { kindOf(it) == "restart" }.toDouble()) to "restarts",
                    count(own.count { kindOf(it) == "down" }.toDouble()) to "down",
                ),
                days,
                ::uptimeWords,
                own.map(::markOf),
            )
        }
        item { WarningsCard(stats) { go("logs", name) } }
    }
}

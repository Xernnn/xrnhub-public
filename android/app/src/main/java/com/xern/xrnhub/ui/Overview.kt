package com.xern.xrnhub.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.data.DASH
import com.xern.xrnhub.data.FIT
import com.xern.xrnhub.data.HistoryDoc
import com.xern.xrnhub.data.LIVE_GAP_MS
import com.xern.xrnhub.data.LIVE_WINDOW_MS
import com.xern.xrnhub.data.NowDoc
import com.xern.xrnhub.data.Point
import com.xern.xrnhub.data.SERVICE_ORDER
import com.xern.xrnhub.data.besideHub
import com.xern.xrnhub.data.bucketValues
import com.xern.xrnhub.data.bytes
import com.xern.xrnhub.data.count
import com.xern.xrnhub.data.degrees
import com.xern.xrnhub.data.duration
import com.xern.xrnhub.data.fitAgo
import com.xern.xrnhub.data.moment
import com.xern.xrnhub.data.nowSeconds
import com.xern.xrnhub.data.percent
import com.xern.xrnhub.data.perSecond
import com.xern.xrnhub.data.plain
import com.xern.xrnhub.data.problemWords
import com.xern.xrnhub.data.serviceName
import com.xern.xrnhub.data.signalBars
import com.xern.xrnhub.data.sinceNow
import com.xern.xrnhub.data.timeLabel
import com.xern.xrnhub.data.tipTime
import com.xern.xrnhub.data.upDays
import com.xern.xrnhub.data.wifiWord

internal const val LIVE = "live"
private const val STALE_MS = 150_000L
private const val TWO_POINTS = 2
private const val FULL_DISK = 0.9
private val OVERVIEW_RANGES = listOf(LIVE to "Live") + RANGES

/** A slice of one measure for a chart, live or from the history. */
private class Slice(val at: List<Long>, val values: List<Double?>, val from: Long, val to: Long, val gap: Long?)

private fun liveSlice(ring: List<Point>, pick: (Point) -> Double?): Slice {
    val to = ring.lastOrNull()?.at ?: System.currentTimeMillis()
    val points = ring.filter { it.at >= to - LIVE_WINDOW_MS }
    return Slice(points.map { it.at }, points.map(pick), to - LIVE_WINDOW_MS, to, LIVE_GAP_MS)
}

private fun historySlice(doc: HistoryDoc?, column: String, perBucket: Boolean): Slice {
    if (doc == null || doc.at.isEmpty()) return Slice(emptyList(), emptyList(), 0, 1, null)
    val at = doc.at.map { it * 1000 }
    val values = bucketValues(doc, column, perBucket, System.currentTimeMillis())
    return Slice(at, values, at.first(), at.last() + doc.bucket * 1000, null)
}

fun bucketLabels(doc: HistoryDoc): Pair<List<String>, List<String>> = bucketLabels(doc.at, doc.bucket)

/** The labels for a run of time buckets, `at` in seconds as the server sends them: on the axis and in full. */
fun bucketLabels(at: List<Long>, bucket: Long): Pair<List<String>, List<String>> {
    val span = at.size * bucket * 1000
    return at.map { timeLabel(it * 1000, span) } to at.map { tipTime(it * 1000, span) }
}

@Composable
fun requestStacks(doc: HistoryDoc, hideTimers: Boolean = false): StackData {
    val (labels, tips) = bucketLabels(doc)
    val names = SERVICE_ORDER.filter { it in doc.traffic } + doc.traffic.keys.filter { it !in SERVICE_ORDER }
    return StackData(labels, tips, names.map { name ->
        val series = doc.traffic.getValue(name)
        val values = series.reqs.mapIndexed { i, reqs ->
            val all = reqs ?: 0.0
            if (hideTimers) all - (series.polls.getOrNull(i) ?: 0.0) else all
        }
        Stack(serviceName(name), values, colorOf(name), name)
    })
}

/** What's wrong right now, a chip each, as the web page's: nothing at all when all is well. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
fun Alerts(doc: NowDoc?, misses: Int, serverMs: Long = System.currentTimeMillis()) {
    val problems = doc?.problems.orEmpty()
    val quiet = doc?.at?.let { serverMs - it > STALE_MS } == true
    if (misses < TWO_POINTS && problems.isEmpty() && !quiet) return
    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        when {
            misses >= TWO_POINTS -> AlertChip(null, "Can't reach the phone", null, warn = false)
            problems.isNotEmpty() -> problems.forEach { problem ->
                AlertChip(problem.target, problemWords(problem), listOf(problem.detail, sinceNow(problem.started)).filter { it.isNotEmpty() }.joinToString(" · "), warn = false)
            }
            else -> AlertChip(null, "Monitor quiet", sinceNow(doc!!.at!! / 1000.0, serverMs / 1000.0), warn = true)
        }
    }
}

@Composable
private fun AlertChip(service: String?, words: String, detail: String?, warn: Boolean) {
    val p = colors
    val ink = if (warn) p.warn else p.bad
    Row(
        Modifier.heightIn(min = 34.dp).clip(RoundedCornerShape(10.dp)).background(if (warn) p.warnBg else p.badBg).padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        if (service != null && hasLogo(service)) {
            Logo(service, 18.dp)
        } else {
            Box(Modifier.size(7.dp).clip(CircleShape).background(ink))
        }
        Spacer(Modifier.width(8.dp))
        Text(words, style = Type.cardTitle, color = ink)
        if (!detail.isNullOrEmpty()) {
            Spacer(Modifier.width(8.dp))
            Text(detail, style = Type.body.copy(fontFeatureSettings = "tnum"), color = ink.copy(alpha = 0.85f))
        }
    }
}

/** A mark a service, in the list's order with xrnfit after the hub: which one is down shows at a glance. */
@Composable
private fun ServiceMarks(doc: NowDoc?) {
    val p = colors
    val rows = doc?.services.orEmpty()
    val fit = doc?.health.takeIf { rows.isNotEmpty() }
    val tones = rows.map { if (it.up) p.good else p.bad }
    Marks(besideHub(rows.map { it.name }, tones, fit?.let { card -> if (card.lastIn == null) p.surface2 else if (card.late) p.warn else p.good }))
}

@Composable
fun OverviewPage(go: Go) {
    val model = LocalModel.current
    var range by rememberSaveable { mutableStateOf(LIVE) }
    val history = rememberDoc<HistoryDoc>("api/server/history?range=${if (range == LIVE) "1h" else range}")
    OverviewBody(
        model.now, model.ring, model.misses, model.serverNow(), history.value, range, { range = it },
        go, history.loading, history::refresh,
    )
}

/** The Overview from what's been read, so a preview can draw it with made-up numbers. */
@Composable
internal fun OverviewBody(
    doc: NowDoc?,
    ring: List<Point>,
    misses: Int,
    serverMs: Long,
    past: HistoryDoc?,
    range: String,
    onRange: (String) -> Unit,
    go: Go,
    refreshing: Boolean = false,
    refresh: () -> Unit = {},
) {
    val list = rememberLazyListState()

    fun slice(pick: (Point) -> Double?, column: String, perBucket: Boolean = false) =
        if (range == LIVE) liveSlice(ring, pick) else historySlice(past, column, perBucket)

    val load = slice({ it.load }, "load1")
    val speed = slice({ it.speed }, "speed")
    val cpu = slice({ it.cpu }, "cpu")
    val memory = slice({ it.memUsed }, "mem_used")
    val cpuHeat = slice({ it.cpuTemp }, "cpu_temp")
    val batteryHeat = slice({ it.battTemp }, "batt_temp")
    val netIn = slice({ it.netIn }, "net_in", perBucket = true)
    val netOut = slice({ it.netOut }, "net_out", perBucket = true)
    val p = colors
    val single = { slice: Slice -> LineData(slice.at, slice.from, slice.to, slice.gap, listOf(Line("", slice.values, p.series))) }
    val wrong = misses >= TWO_POINTS || doc?.problems.orEmpty().isNotEmpty() || doc?.at?.let { serverMs - it > STALE_MS } == true

    Page(refreshing, refresh, list) {
        if (wrong) item { Alerts(doc, misses, serverMs) }
        item { Segments(OVERVIEW_RANGES, range, onRange) }
        item {
            val mem = doc?.memory
            val disk = doc?.disk
            val battery = doc?.battery
            val wifi = doc?.wifi
            val running = doc?.services?.count { it.state == "run" } ?: 0
            val temps = doc?.temps
            val system = doc?.system
            val full = disk?.let { it.used / it.total > FULL_DISK } == true
            Tiles(listOf(
                TileData("Load", doc?.system?.load?.firstOrNull()?.let { plain(it, 2) } ?: DASH, doc?.about?.cores?.let { "$it cores" }, extra = { Spark(load.values) }),
                TileData("CPU speed", doc?.cores?.speed?.let { percent(it * 100) } ?: DASH, extra = { Spark(speed.values) }),
                TileData("Memory", bytes(mem?.used), mem?.let { "of ${bytes(it.total)}" }, extra = mem?.let { { Gauge(it.used / it.total) } }),
                TileData("Temperature", degrees(temps?.cpu), temps?.battery?.let { "Battery ${degrees(it)}" }, extra = { Spark(cpuHeat.values) }),
                TileData(
                    "Battery",
                    battery?.percent?.let { "${it.toInt()}%" } ?: DASH,
                    battery?.let { if (!it.plugged) "On battery" else if (it.status == "FULL") "Full" else "Charging" },
                    extra = battery?.percent?.let { level -> { Gauge(level / 100, if (level < 30) p.bad else p.series) } },
                ),
                TileData(
                    "Storage", bytes(disk?.used), disk?.let { "of ${bytes(it.total)}" }, onClick = { go("data", null) },
                    extra = disk?.let { { Gauge(it.used / it.total, if (full) p.bad else p.series) } },
                ),
                TileData(
                    "Network", "↓ ${perSecond(doc?.speed?.down)}", "↑ ${perSecond(doc?.speed?.up)}", onClick = { go("traffic", null) },
                    extra = { Sparks(listOf(netIn.values to p.charts[0], netOut.values to p.charts[1])) },
                ),
                TileData(
                    "Wi-Fi", wifi?.rssi?.let(::wifiWord) ?: DASH, wifi?.let { "${it.linkMbps?.toInt() ?: DASH} Mbps · ${it.ghz ?: DASH} GHz" },
                    extra = { Signal(signalBars(wifi?.rssi)) },
                ),
                TileData(
                    "Up for", duration(system?.uptime), system?.uptime?.let { "Since ${moment(nowSeconds() - it)}" },
                    extra = system?.uptime?.let { up -> { Marks(upDays(up).map { if (it) p.good else p.surface2 }) } },
                ),
                TileData("Services", "$running/${doc?.services?.size ?: 0}", onClick = { go("services", null) }, extra = { ServiceMarks(doc) }),
            ))
        }
        item { LineCard("Load", single(load), { plain(it, 2) }) }
        item { LineCard("Services' CPU", single(cpu), { percent(it) }) }
        item { LineCard("Memory", single(memory), { bytes(it) }, inBytes = true) }
        item {
            val lines = listOf(Line("CPU", cpuHeat.values, p.charts[1]), Line("Battery", batteryHeat.values, p.charts[0]))
            LineCard("Temperature", LineData(cpuHeat.at, cpuHeat.from, cpuHeat.to, cpuHeat.gap, lines), { degrees(it) }, floorValue = 20.0, zero = false)
        }
        item {
            val lines = listOf(Line("In", netIn.values, p.charts[0]), Line("Out", netOut.values, p.charts[1]))
            LineCard("Network", LineData(netIn.at, netIn.from, netIn.to, netIn.gap, lines), { perSecond(it) }, inBytes = true)
        }
        if (past != null) item { StackCard("Requests", requestStacks(past)) { count(it) } }
        item {
            HubCard("Services") {
                val rows = doc?.services.orEmpty()
                val fit = doc?.health.takeIf { rows.isNotEmpty() }
                besideHub(rows.map { it.name }, rows.map { it.name }, fit?.let { FIT }).forEach { name ->
                    val s = rows.find { it.name == name }
                    Row(
                        Modifier.fillMaxWidth().clip(CARD_SHAPE).clickable { go("services", name) }.padding(vertical = 7.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        if (s != null) Dot(s.up) else FitDot(fit)
                        Spacer(Modifier.width(9.dp))
                        ServiceLabel(name, Modifier.weight(1f))
                        Text(
                            when {
                                s == null -> fit?.let { fitAgo(it) }.orEmpty()
                                s.up -> s.since?.let { sinceNow(it) }.orEmpty()
                                else -> "Down"
                            },
                            style = Type.small,
                            color = when {
                                s == null -> if (fit?.late == true) p.warn else p.muted
                                s.up -> p.muted
                                else -> p.bad
                            },
                        )
                    }
                }
            }
        }
    }
}

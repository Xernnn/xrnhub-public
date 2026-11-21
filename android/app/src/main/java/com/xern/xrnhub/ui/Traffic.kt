package com.xern.xrnhub.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.data.CLASSES
import com.xern.xrnhub.data.ClientRow
import com.xern.xrnhub.data.FeedRow
import com.xern.xrnhub.data.HistoryDoc
import com.xern.xrnhub.data.PathRow
import com.xern.xrnhub.data.SERVICE_ORDER
import com.xern.xrnhub.data.Totals
import com.xern.xrnhub.data.TrafficDoc
import com.xern.xrnhub.data.clock
import com.xern.xrnhub.data.count
import com.xern.xrnhub.data.feedLanes
import com.xern.xrnhub.data.percent
import com.xern.xrnhub.data.statusClasses
import com.xern.xrnhub.data.summed

// Traffic: what's calling the server, for what, and how it was answered. The last fifteen minutes are
// a lane per service with a mark per request, coloured by its answer, not a list of lines.

private const val FEED_S = 15 * 60.0

private fun share(part: Double, whole: Double): String = if (whole > 0) percent(part / whole * 100) else "–"

/** Each status class's colour: answered, unchanged, refused, failed. */
@Composable
private fun classColor(c: String): Color = kindColor("s$c")

/** One bar split by how requests were answered, with each class's share and count under it. */
@OptIn(ExperimentalLayoutApi::class)
@Composable
private fun Answers(mix: Map<String, Double>) {
    val classes = statusClasses(mix)
    val all = classes.sumOf { it.second }
    if (all <= 0) return Empty("None")
    ShareBar(classes.map { (c, n) -> n / all to classColor(c) })
    Spacer(Modifier.height(8.dp))
    FlowRow(horizontalArrangement = Arrangement.spacedBy(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        classes.forEach { (c, n) -> KeyItem(KeyEntry("${c}xx", "${percent(n / all * 100)} · ${count(n)}", classColor(c))) }
    }
}

@Composable
fun TrafficPage(go: Go) {
    val model = LocalModel.current
    var range by rememberSaveable { mutableStateOf("1h") }
    val doc = rememberDoc<TrafficDoc>("api/server/traffic?range=$range")
    val history = rememberDoc<HistoryDoc>("api/server/history?range=$range")
    TrafficBody(
        doc.value, history.value, model.now?.feed.orEmpty(), range, { range = it }, go,
        refreshing = doc.loading, refresh = { doc.refresh(); history.refresh() },
    )
}

@Composable
internal fun TrafficBody(
    traffic: TrafficDoc?,
    pastDoc: HistoryDoc?,
    feed: List<FeedRow>,
    range: String,
    onRange: (String) -> Unit,
    go: Go,
    nowMs: Double = System.currentTimeMillis().toDouble(),
    refreshing: Boolean = false,
    refresh: () -> Unit = {},
) {
    var hideTimers by rememberSaveable { mutableStateOf(true) }
    var feedTimers by rememberSaveable { mutableStateOf(false) }
    val p = colors

    Page(refreshing, refresh, rememberLazyListState()) {
        item { Segments(RANGES, range, onRange) }
        traffic?.let { t ->
            val all = t.totals.values
            item {
                val reqs = all.sumOf { it.reqs }
                val refused = all.sumOf { it.refused }
                val errors = all.sumOf { it.errors }
                Tiles(listOf(
                    TileData(
                        "Requests", count(reqs), "${count(all.sumOf { it.polls })} timers",
                        extra = pastDoc?.let { past -> { Spark(summed(past) { it.reqs }) } },
                    ),
                    TileData("Saved", count(all.sumOf { it.writes }), extra = pastDoc?.let { past -> { Spark(summed(past) { it.writes }) } }),
                    TileData("Refused", count(refused), share(refused, reqs), tone = if (refused > 0) Tone.Warn else Tone.Plain, onClick = { go("security", null) }),
                    TileData("Failed", count(errors), share(errors, reqs), tone = if (errors > 0) Tone.Bad else Tone.Plain),
                ))
            }
        }
        pastDoc?.let { past ->
            item {
                StackCard("Requests", requestStacks(past, hideTimers), side = { ToggleChip("Timers", !hideTimers) { hideTimers = !it } }) { count(it) }
            }
        }
        traffic?.let { t ->
            item { HubCard("Answers") { Answers(t.status) } }
            item {
                HubCard("Services") {
                    val named = t.totals.entries.sortedByDescending { it.value.reqs }.map { it.key to it.value }
                    val most = named.maxOfOrNull { it.second.reqs }?.coerceAtLeast(1.0) ?: 1.0
                    DataRows(
                        listOf<Col<Pair<String, Totals>>>(
                            Col("Service") { (name, _) -> NameCell(name) },
                            Col("Requests", width = 64.dp, end = true) { (name, row) -> FigureBar(count(row.reqs), row.reqs / most, colorOf(name)) },
                            Col("Refused", width = 52.dp, end = true) { (_, row) -> if (row.refused > 0) Code(count(row.refused), Mood.Warn) else Cell("0", muted = true) },
                            Col("Failed", width = 44.dp, end = true) { (_, row) -> if (row.errors > 0) Code(count(row.errors), Mood.Bad) else Cell("0", muted = true) },
                        ),
                        named,
                        onClick = { (name, _) -> if (name in SERVICE_ORDER) { { go("services", name) } } else null },
                    )
                }
            }
            item {
                HubCard("Paths") {
                    val top = t.paths.firstOrNull()?.count?.takeIf { it > 0 } ?: 1.0
                    DataRows(
                        listOf<Col<PathRow>>(
                            Col("Path") { r -> NameCell(r.service, code = r.path) },
                            Col(width = 48.dp) { r -> Bar(r.count / top, colorOf(r.service)) },
                            Col("Requests", width = 64.dp, end = true) { r -> Cell(count(r.count), strong = true) },
                        ),
                        t.paths,
                    )
                }
            }
            item {
                HubCard("Callers") {
                    val busiest = t.clients.firstOrNull()?.reqs?.takeIf { it > 0 } ?: 1.0
                    DataRows(
                        listOf<Col<ClientRow>>(
                            Col("Caller") { c -> Cell(c.ip, code = true) },
                            Col(width = 48.dp) { c ->
                                SplitBar(listOf((c.reqs - c.refused) / c.reqs to p.series, c.refused / c.reqs to classColor("4")), c.reqs / busiest)
                            },
                            Col("Requests", width = 64.dp, end = true) { c -> Cell(count(c.reqs), strong = true) },
                            Col("Refused", width = 52.dp, end = true) { c -> if (c.refused > 0) Code(count(c.refused), Mood.Warn) else Cell("0", muted = true) },
                        ),
                        t.clients,
                    )
                }
            }
        }
        item {
            HubCard("Last 15 minutes", action = { ToggleChip("Timers", feedTimers) { feedTimers = it } }) {
                val lanes = feedLanes(feed, nowMs, feedTimers, FEED_S)
                if (lanes.isEmpty()) {
                    Empty("None")
                } else {
                    val to = nowMs / 1000
                    val from = to - FEED_S
                    Timeline(
                        lanes, from, to,
                        CLASSES.map { KeyEntry("${it}xx", null, classColor(it)) },
                        listOf(from, (from + to) / 2, to).map { clock((it * 1000).toLong()) },
                        weeks = false,
                        now = to,
                    )
                }
            }
        }
    }
}


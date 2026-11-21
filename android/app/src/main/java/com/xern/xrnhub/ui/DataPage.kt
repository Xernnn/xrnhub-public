package com.xern.xrnhub.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.data.AppRow
import com.xern.xrnhub.data.DASH
import com.xern.xrnhub.data.DailyRecords
import com.xern.xrnhub.data.DataDoc
import com.xern.xrnhub.data.ProcessRow
import com.xern.xrnhub.data.SERVICE_ORDER
import com.xern.xrnhub.data.bytes
import com.xern.xrnhub.data.count
import com.xern.xrnhub.data.dayOf
import com.xern.xrnhub.data.dayPlain
import com.xern.xrnhub.data.duration
import com.xern.xrnhub.data.moment
import com.xern.xrnhub.data.runwayWords
import com.xern.xrnhub.data.serviceName
import java.time.LocalDate
import java.time.ZoneId
import kotlinx.coroutines.delay

private const val BACKUP_OK_S = 3 * 86400.0
private const val PENDING_MS = 4000L
private const val DAY_MS = 86_400_000L

@Composable
private fun appStacks(days: List<String>, apps: Map<String, List<Double?>>): StackData {
    val names = days.map(::dayPlain)
    return StackData(names, names, SERVICE_ORDER.filter { it in apps }.map {
        Stack(serviceName(it), apps.getValue(it), colorOf(it), it)
    })
}

@Composable
private fun growthLines(growth: DailyRecords): LineData {
    val at = growth.days.map { LocalDate.parse(it).atTime(12, 0).atZone(ZoneId.systemDefault()).toInstant().toEpochMilli() }
    val from = at.firstOrNull() ?: 0
    val to = maxOf(at.lastOrNull() ?: 1, from + DAY_MS)
    return LineData(at, from, to, null, SERVICE_ORDER.filter { it in growth.apps }.map {
        Line(serviceName(it), growth.apps.getValue(it), colorOf(it), dashedOf(it), it)
    })
}

@Composable
fun DataPage() {
    val doc = rememberDoc<DataDoc>("api/server/data")
    val data = doc.value
    LaunchedEffect(data?.pending) {
        if (data?.pending == true) {
            delay(PENDING_MS)
            doc.refresh()
        }
    }
    if (data == null || data.pending) return Waiting(doc.failed ?: if (data?.pending == true) "Counting" else null)
    DataBody(data, doc.loading, doc::refresh)
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun DataBody(data: DataDoc, refreshing: Boolean = false, refresh: () -> Unit = {}) {
    val p = colors

    Page(refreshing, refresh, rememberLazyListState()) {
        item {
            val knownGrowth = data.apps.any { it.grew != null }
            val grew = data.apps.sumOf { it.grew ?: 0.0 }
            val added = data.apps.sumOf { it.added ?: 0.0 }
            // What the server holds: each app in its colour, then what isn't an app's in greys.
            val parts = data.apps.filter { it.bytes > 0 }.map { KeyEntry(serviceName(it.name), bytes(it.bytes), colorOf(it.name), it.name) to it.bytes } +
                listOf(Triple("App files", data.extra, p.outline), Triple("Logs", data.logs, p.faint), Triple("Monitor", data.monitorBytes, p.muted))
                    .filter { it.second > 0 }.map { (name, size, tone) -> KeyEntry(name, bytes(size), tone) to size }
            val whole = parts.sumOf { it.second }.takeIf { it > 0 } ?: 1.0
            Column(Modifier.padding(horizontal = 4.dp, vertical = 2.dp)) {
                Text(bytes(data.total), style = Type.hero, color = p.text)
                Text(
                    if (knownGrowth) "${if (grew >= 0) "+" else ""}${bytes(grew)} · ${if (added >= 0) "+" else ""}${count(added)} records · 7d"
                    else "${count(data.apps.sumOf { it.records })} records",
                    style = Type.small,
                    color = p.muted,
                )
                Spacer(Modifier.height(10.dp))
                ShareBar(parts.map { (entry, size) -> size / whole to entry.color!! })
                FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(14.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    parts.forEach { KeyItem(it.first) }
                }
            }
        }
        item {
            val backupAge = data.backup?.let { data.at - it }
            Tiles(listOf(
                TileData("Received", bytes(data.network?.down), extra = { Spark(data.daily.down, p.charts[0]) }),
                TileData("Sent", bytes(data.network?.up), extra = { Spark(data.daily.up, p.charts[1]) }),
                TileData("Disk free", bytes(data.disk?.free), data.disk?.let { "of ${bytes(it.total)}" }, extra = data.disk?.let { { Gauge(it.used / it.total) } }),
                TileData("Lasts", runwayWords(data), data.runway?.perDay?.takeIf { it > 0 }?.let { "${bytes(it)}/day" }),
                TileData(
                    "Last backup",
                    backupAge?.let { "${duration(it)} ago" } ?: "None",
                    data.backup?.let { moment(it) },
                    tone = if (backupAge == null || backupAge > BACKUP_OK_S) Tone.Bad else Tone.Plain,
                ),
            ))
        }
        item {
            HubCard("By app") {
                val top = data.apps.maxOfOrNull { it.bytes }?.coerceAtLeast(1.0) ?: 1.0
                DataRows(
                    listOf<Col<AppRow>>(
                        Col("App", weight = 1.1f) { a -> NameCell(a.name) },
                        Col("Size", width = 64.dp, end = true) { a -> FigureBar(bytes(a.bytes), a.bytes / top, colorOf(a.name)) },
                        Col("Holds", end = true) { a ->
                            Column(horizontalAlignment = Alignment.End) {
                                Cell("${count(a.records)} ${a.unit}")
                                a.grew?.takeIf { it != 0.0 }?.let { Text("${if (it > 0) "+" else ""}${bytes(it)} · 7d", style = Type.tiny, color = p.muted, maxLines = 1) }
                            }
                        },
                    ),
                    data.apps,
                )
            }
        }
        item { StackCard("Came in, per day", appStacks(data.fed.days, data.fed.apps)) { count(it) } }
        item { StackCard("Saved, per day", appStacks(data.daily.days, data.daily.writes)) { count(it) } }
        item {
            val names = data.daily.days.map(::dayPlain)
            StackCard(
                "Network, per day",
                StackData(names, names, listOf(Stack("In", data.daily.down, p.charts[0]), Stack("Out", data.daily.up, p.charts[1]))),
                inBytes = true,
            ) { bytes(it) }
        }
        item { LineCard("Size", growthLines(data.growth), { bytes(it) }, inBytes = true) }
        item {
            HubCard("Per service") {
                val most = { pick: (ProcessRow) -> Double? -> data.services.maxOfOrNull { pick(it) ?: 0.0 }?.coerceAtLeast(1.0) ?: 1.0 }
                val written = most { it.written }
                val stored = most { it.diskWritten }
                DataRows(
                    listOf<Col<ProcessRow>>(
                        Col("Service") { s -> NameCell(s.name) },
                        Col("Written", width = 60.dp, end = true) { s -> FigureBar(bytes(s.written), (s.written ?: 0.0) / written, colorOf(s.name)) },
                        Col("To storage", width = 64.dp, end = true) { s -> FigureBar(bytes(s.diskWritten), (s.diskWritten ?: 0.0) / stored, colorOf(s.name)) },
                        // The date alone, so a start this week reads as one last month does.
                        Col("Started", width = 46.dp, end = true) { s -> Cell(s.since?.let { dayOf(it) } ?: DASH, muted = true) },
                    ),
                    data.services,
                )
            }
        }
    }
}

package com.xern.xrnhub.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.data.LogRow
import com.xern.xrnhub.data.LogStatsDoc
import com.xern.xrnhub.data.LogsDoc
import com.xern.xrnhub.data.SERVICE_ORDER
import com.xern.xrnhub.data.serviceName
import java.net.URLEncoder
import kotlinx.coroutines.delay

// Logs: how much each service logs and at what level, its warnings and errors by what they say, and
// the newest lines. Requests answered 2xx or 3xx stay out unless asked for: Traffic counts those.

private const val FOLLOW_MS = 5000L
private const val TYPE_PAUSE_MS = 300L
private const val LINES = 100
private val LOG_RANGES = listOf("24h" to "24h", "7d" to "7d", "30d" to "30d")
private val LEVELS = listOf("info" to "All", "warn" to "Warnings", "error" to "Errors")
/** The services the Logs page can pick, by tile, as the web page's. */
val LOG_PICKS = listOf("calendar", "xrnhub", "xrnfit", "xrnloops", "xrnrule", "xrntube", "xrnhome", "xrnwal", "xrninsta", "ssh")

private fun encoded(text: String) = URLEncoder.encode(text, "UTF-8").replace("+", "%20")

/** Which service, by its tile, after "All"; the tile carries the name for TalkBack. */
@Composable
private fun ServicePicker(current: String, pick: (String) -> Unit) {
    val p = colors
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).clip(RoundedCornerShape(12.dp)).background(p.surface2).padding(3.dp),
        horizontalArrangement = Arrangement.spacedBy(2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        (listOf("") + LOG_PICKS).forEach { name ->
            val on = name == current
            Box(
                Modifier
                    .height(34.dp)
                    .clip(RoundedCornerShape(9.dp))
                    .background(if (on) p.surface else p.surface2)
                    .clickable(role = Role.RadioButton) { pick(name) }
                    .semantics { contentDescription = if (name.isEmpty()) "All" else serviceName(name) }
                    .padding(horizontal = if (name.isEmpty()) 12.dp else 8.dp),
                contentAlignment = Alignment.Center,
            ) {
                if (name.isEmpty()) Text("All", style = Type.label, color = if (on) p.text else p.muted) else Logo(name, 20.dp)
            }
        }
    }
}

/** A switch as a pressed word: on, it reads as pressed. */
@Composable
fun ToggleChip(label: String, on: Boolean, change: (Boolean) -> Unit) {
    val p = colors
    Text(
        label,
        Modifier
            .clip(RoundedCornerShape(9.dp))
            .background(if (on) p.series else p.surface2)
            .clickable(role = Role.Switch) { change(!on) }
            .padding(horizontal = 12.dp, vertical = 7.dp),
        style = Type.label,
        color = if (on) p.onAccent else p.muted,
    )
}

@Composable
fun LogsPage(picked: String = "") {
    var range by rememberSaveable { mutableStateOf("24h") }
    var service by rememberSaveable { mutableStateOf(picked.takeIf { it in LOG_PICKS }.orEmpty()) }
    var level by rememberSaveable { mutableStateOf("info") }
    var routine by rememberSaveable { mutableStateOf(false) }
    var typed by rememberSaveable { mutableStateOf("") }
    var search by rememberSaveable { mutableStateOf("") }
    LaunchedEffect(typed) {
        delay(TYPE_PAUSE_MS)
        search = typed.trim()
    }
    val stats = rememberDoc<LogStatsDoc>("api/server/logstats?range=$range${if (service.isEmpty()) "" else "&service=$service"}")
    val doc = rememberDoc<LogsDoc>(
        "api/server/logs?service=$service&level=$level&q=${encoded(search)}&limit=$LINES&routine=${if (routine) 1 else 0}",
        FOLLOW_MS,
    )
    val empty = doc.failed ?: if (doc.value == null) "Loading" else "None"
    LogsBody(
        range, { range = it }, service, { service = it }, stats.value, level, { level = it }, routine, { routine = it },
        typed, { typed = it }, doc.value?.lines.orEmpty(), empty, stats.loading, { stats.refresh(); doc.refresh() },
    )
}

@Composable
internal fun LogsBody(
    range: String,
    onRange: (String) -> Unit,
    service: String,
    onService: (String) -> Unit,
    stats: LogStatsDoc?,
    level: String,
    onLevel: (String) -> Unit,
    routine: Boolean,
    onRoutine: (Boolean) -> Unit,
    typed: String,
    onTyped: (String) -> Unit,
    lines: List<LogRow>,
    empty: String,
    refreshing: Boolean = false,
    refresh: () -> Unit = {},
) {
    val p = colors
    Page(refreshing, refresh, rememberLazyListState()) {
        item { Segments(LOG_RANGES, range, onRange) }
        item { ServicePicker(service, onService) }
        stats?.let { s ->
            item {
                val (labels, tips) = bucketLabels(s.at, s.bucket)
                val of = { level: String -> s.at.indices.map { s.levels[level]?.getOrNull(it) ?: 0.0 } }
                StackCard("By level", StackData(labels, tips, listOf(
                    Stack("Errors", of("error"), p.bad), Stack("Warnings", of("warn"), p.warn), Stack("Other", of("info"), p.faint),
                ))) { com.xern.xrnhub.data.count(it) }
            }
            item {
                val (labels, tips) = bucketLabels(s.at, s.bucket)
                val named = (SERVICE_ORDER + "ssh").filter { it in s.services }
                StackCard("Warnings by service", StackData(labels, tips, named.map { name ->
                    val series = s.services.getValue(name)
                    Stack(serviceName(name), s.at.indices.map { (series["warn"]?.getOrNull(it) ?: 0.0) + (series["error"]?.getOrNull(it) ?: 0.0) }, colorOf(name), name)
                })) { com.xern.xrnhub.data.count(it) }
            }
            item { HubCard("Warnings and errors") { MessageRows(s.groups, withService = service.isEmpty()) } }
        }
        item {
            HubCard("Latest") {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween, verticalAlignment = Alignment.CenterVertically) {
                    Segments(LEVELS, level, onLevel, fill = false)
                    ToggleChip("Requests", routine, onRoutine)
                }
                Box(
                    Modifier.fillMaxWidth().padding(top = 8.dp, bottom = 4.dp).clip(RoundedCornerShape(12.dp)).background(p.surface2)
                        .padding(horizontal = 12.dp, vertical = 9.dp),
                ) {
                    if (typed.isEmpty()) Text("Search", style = Type.body, color = p.muted)
                    BasicTextField(
                        typed,
                        onTyped,
                        Modifier.fillMaxWidth().semantics { contentDescription = "Search logs" },
                        textStyle = Type.body.copy(color = p.text),
                        singleLine = true,
                        cursorBrush = SolidColor(p.text),
                        keyboardOptions = KeyboardOptions(autoCorrectEnabled = false),
                    )
                }
                if (lines.isEmpty()) Empty(empty)
                lines.forEach { LogLine(it, service.isEmpty()) }
            }
        }
    }
}

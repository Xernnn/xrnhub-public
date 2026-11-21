package com.xern.xrnhub.ui

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.data.ClientRow
import com.xern.xrnhub.data.DASH
import com.xern.xrnhub.data.Device
import com.xern.xrnhub.data.LoginRow
import com.xern.xrnhub.data.PathRow
import com.xern.xrnhub.data.SecurityDoc
import com.xern.xrnhub.data.count
import com.xern.xrnhub.data.moment

/** A chip of words: a path open to the internet, Tailscale's version. */
@Composable
internal fun PathChip(text: String) {
    Text(text, Modifier.clip(RoundedCornerShape(7.dp)).background(colors.surface2).padding(horizontal = 7.dp, vertical = 3.dp), style = Type.code, color = colors.text)
}

@Composable
fun SecurityPage() {
    val doc = rememberDoc<SecurityDoc>("api/server/security")
    val data = doc.value ?: return Waiting(doc.failed)
    SecurityBody(data, doc.loading, doc::refresh)
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun SecurityBody(data: SecurityDoc, refreshing: Boolean = false, refresh: () -> Unit = {}) {
    val p = colors

    Page(refreshing, refresh, rememberLazyListState()) {
        item {
            val refusals = data.status.filterKeys { it.startsWith("4") }.values.sum()
            Tiles(listOf(
                TileData("Failed sign-ins · 24h", count(data.day.refused), "${count(data.week.refused)} · 7d", tone = if (data.day.refused > 0) Tone.Warn else Tone.Plain),
                TileData("Refused · 7d", count(refusals)),
                TileData("Locked out · 7d", count(data.status["429"] ?: 0.0)),
                TileData("Sign-ins · 7d", count(data.week.ok)),
            ))
        }
        item {
            HubCard("Sign-ins") {
                DataRows(
                    listOf<Col<LoginRow>>(
                        Col(width = 52.dp) { r -> Code(if (r.ok) "In" else "Failed", if (r.ok) Mood.Good else Mood.Bad) },
                        Col { r ->
                            Column {
                                Cell(r.who?.ifEmpty { null } ?: DASH, strong = true, color = if (r.ok) null else p.bad)
                                Text(listOfNotNull(moment(r.at / 1000), r.ip).joinToString(" · "), style = Type.tiny, color = p.muted, maxLines = 1, overflow = TextOverflow.Ellipsis)
                            }
                        },
                        Col(width = 18.dp) { r -> Logo(r.service, 18.dp) },
                        Col(width = 28.dp, end = true) { r -> Cell(count(r.count.toDouble()), strong = true) },
                    ),
                    data.logins,
                )
            }
        }
        item {
            HubCard("Refused paths · 7d") {
                val most = data.refused.firstOrNull()?.count?.takeIf { it > 0 } ?: 1.0
                DataRows(
                    listOf<Col<PathRow>>(
                        Col { r -> NameCell(r.service, code = r.path) },
                        Col(width = 40.dp) { r -> Code("${r.status}", moodOf(r.status)) },
                        Col(width = 40.dp) { r -> Bar(r.count / most, colorOf(r.service)) },
                        Col(width = 32.dp, end = true) { r -> Cell(count(r.count), strong = true) },
                    ),
                    data.refused,
                )
            }
        }
        item {
            HubCard("Refused callers · 7d") {
                val worst = data.clients.maxOfOrNull { it.refused }?.coerceAtLeast(1.0) ?: 1.0
                DataRows(
                    listOf<Col<ClientRow>>(
                        Col("Caller") { c -> Cell(c.ip, code = true) },
                        Col(width = 48.dp) { c -> Bar(c.refused / worst, kindColor("s4")) },
                        Col("Refused", width = 56.dp, end = true) { c -> Cell(count(c.refused), strong = true) },
                        Col("Of", width = 44.dp, end = true) { c -> Cell(count(c.reqs), muted = true) },
                        Col("Days", width = 36.dp, end = true) { c -> Cell("${c.days}", muted = true) },
                    ),
                    data.clients,
                )
            }
        }
        item {
            HubCard("Devices") {
                val net = data.tailnet
                if (net == null) {
                    Empty("Tailscale isn't answering")
                } else {
                    DataRows(
                        listOf<Col<Device>>(
                            Col { d ->
                                Row(verticalAlignment = Alignment.CenterVertically) {
                                    Dot(d.online, off = p.outline)
                                    Spacer(Modifier.width(8.dp))
                                    Cell(d.name, strong = true)
                                }
                            },
                            Col(weight = 0.6f) { d -> Cell(d.os.orEmpty(), muted = true) },
                            Col(weight = 0.7f, end = true) { d ->
                                Cell(if (!d.online) "Offline" else if (d.direct) "Direct" else "Relay ${d.relay}", strong = d.online, muted = !d.online)
                            },
                        ),
                        net.devices,
                    )
                    FlowRow(Modifier.padding(top = 8.dp), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        PathChip("Tailscale ${net.version}")
                        PathChip(net.state)
                        PathChip("${net.ingress} relays")
                    }
                }
            }
        }
        item {
            HubCard("Public paths") {
                val paths = data.funnel
                if (paths == null) {
                    Empty(DASH)
                } else {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                        paths.forEach { PathChip(it) }
                    }
                }
            }
        }
    }
}

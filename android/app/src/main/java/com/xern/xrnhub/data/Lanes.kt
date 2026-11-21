package com.xern.xrnhub.data

import java.time.LocalDate
import java.time.ZoneId
import kotlin.math.ceil
import kotlin.math.min

// The arithmetic behind the Server pages' timelines, strips and marks, apart from any drawing. The
// same as web/charts.js, web/server.js and web/server-services.js, so the page and the app agree.

const val STRIP_DAYS = 30
private const val DAY_S = 86_400.0

/** What happened, as the timeline colours it. */
val KINDS = listOf("down", "restart", "problem", "reboot", "gap")
val KIND_NAMES = mapOf("down" to "Down", "restart" to "Restart", "problem" to "Problem", "reboot" to "Reboot", "gap" to "No data")
private val KIND_OF = mapOf("down" to "down", "restart" to "restart", "restarted" to "restart", "reboot" to "reboot", "gap" to "gap")

fun kindOf(i: Incident): String = KIND_OF[i.kind] ?: "problem"

/** A thing on a lane: from and to in seconds (`to` null is still going), with its words for a tap. */
data class Mark(val from: Double, val to: Double?, val kind: String, val title: String, val words: List<String>)

/** A lane of a timeline: a service's (`name`) or a place's (`place`), with a figure at its end. */
data class Lane(val name: String?, val place: String?, val figure: String?, val marks: List<Mark>)

// Lanes for what isn't a service, after the services.
private val PLACES = linkedMapOf("phone" to "Phone", "monitor" to "Monitor", "laptop" to "Laptop", "network" to "Network")

fun markOf(i: Incident): Mark {
    val lasted = i.ended?.takeIf { it > i.started }?.let { duration(it - i.started) }
    val first = listOfNotNull(moment(i.started), if (i.ended == null) "Ongoing" else lasted).joinToString(" · ")
    return Mark(i.started, i.ended, kindOf(i), incidentWords(i), listOf(first, i.detail).filter { it.isNotEmpty() })
}

/**
 * A lane for each service or place anything happened to: the services in [order], then the phone,
 * the monitor and the rest. A gap with no target is the monitor's, anything else without one the
 * phone's.
 */
fun incidentLanes(incidents: List<Incident>, order: List<String>): List<Lane> {
    val by = linkedMapOf<String, MutableList<Mark>>()
    for (i in incidents) {
        val target = i.target.ifEmpty { if (i.kind == "gap") "monitor" else "phone" }
        by.getOrPut(target) { mutableListOf() }.add(markOf(i))
    }
    val names = order.filter { it in by } + PLACES.keys.filter { it in by } + by.keys.filter { it !in order && it !in PLACES }
    return names.map { name ->
        val marks = by.getValue(name)
        Lane(name.takeIf { it !in PLACES }, PLACES[name], count(marks.size.toDouble()), marks)
    }
}

/** The status classes, as Traffic colours them: 2xx answered, 3xx unchanged, 4xx refused, 5xx failed. */
val CLASSES = listOf("2", "3", "4", "5")

/** Each status class's total over a mix of codes, in CLASSES' order, those with none left out. */
fun statusClasses(mix: Map<String, Double>): List<Pair<String, Double>> {
    val groups = mutableMapOf<String, Double>()
    for ((code, total) in mix) if (code.isNotEmpty()) groups[code.take(1)] = (groups[code.take(1)] ?: 0.0) + total
    return CLASSES.mapNotNull { c -> groups[c]?.takeIf { it > 0 }?.let { c to it } }
}

/** The last [seconds] of live requests, a lane per service with a mark per request in its status's class. */
fun feedLanes(feed: List<FeedRow>, nowMs: Double, withTimers: Boolean, seconds: Double = 15 * 60.0): List<Lane> {
    val from = nowMs / 1000 - seconds
    val by = linkedMapOf<String, MutableList<FeedRow>>()
    feed.filter { (withTimers || !it.poll) && it.at / 1000 >= from }.forEach { by.getOrPut(it.service) { mutableListOf() }.add(it) }
    val names = SERVICE_ORDER.filter { it in by } + by.keys.filter { it !in SERVICE_ORDER }
    return names.map { name ->
        val rows = by.getValue(name)
        Lane(name, null, count(rows.size.toDouble()), rows.map { f ->
            val at = f.at / 1000
            Mark(at, at, "s${f.status.toString().take(1)}", "${f.method} ${f.path}", listOf("${f.status} · ${clock(f.at.toLong())}", f.ip).filter { it.isNotEmpty() })
        })
    }
}

/** The start of the day [count] - 1 days ago and the end of today, in seconds: the strips' days. */
fun stripDays(count: Int = STRIP_DAYS, today: LocalDate = LocalDate.now(), zone: ZoneId = ZoneId.systemDefault()): Pair<Double, Double> {
    val first = today.minusDays(count - 1L).atStartOfDay(zone).toEpochSecond().toDouble()
    val end = today.plusDays(1).atStartOfDay(zone).toEpochSecond().toDouble()
    return first to end
}

/** The share of watched days' time a service was up, as its figure: "100", "99.8", or null unwatched. */
fun uptimeShare(days: List<Double?>?): String? {
    val seen = days.orEmpty().filterNotNull()
    if (seen.isEmpty()) return null
    val share = seen.average() * 100
    return if (share >= 99.95) "100" else plain(share, 1)
}

/** The phone's days up, out of the last thirty: true for each day since it last started. */
fun upDays(uptime: Double, days: Int = STRIP_DAYS): List<Boolean> {
    val up = min(ceil(uptime / DAY_S).toInt(), days)
    return List(days) { it >= days - up }
}

/** Wi-Fi as a phone shows it: one to four bars, none without a reading. */
fun signalBars(rssi: Int?): Int = when {
    rssi == null -> 0
    rssi >= -55 -> 4
    rssi >= -65 -> 3
    rssi >= -75 -> 2
    else -> 1
}

private val REQUEST_LINE = Regex("^\\S+ \"[A-Z]+ ")

/** A message's words: a refused request reads best as its method, path and status; anything else as its latest line. */
fun messageWords(g: LogGroup): String = if (REQUEST_LINE.containsMatchIn(g.text)) g.sig else g.text

/** A service's errors and warnings a bucket at a time, from the log's counts. */
fun levelValues(stats: LogStatsDoc, levels: Map<String, List<Double>>?, level: String): List<Double?> =
    stats.at.indices.map { levels?.get(level)?.getOrNull(it) ?: 0.0 }

/** A date with no time, so a column of them reads alike: "16 Nov". */
fun dayOf(seconds: Double, zone: ZoneId = ZoneId.systemDefault()): String =
    dayPlain(java.time.Instant.ofEpochMilli((seconds * 1000).toLong()).atZone(zone).toLocalDate().toString())

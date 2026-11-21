package com.xern.xrnhub.data

import java.math.BigDecimal
import java.math.RoundingMode
import java.text.NumberFormat
import java.time.Instant
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Currency
import java.util.Locale
import kotlin.math.abs
import kotlin.math.floor
import kotlin.math.roundToLong

// How numbers and events read. These match the web page's, so the two never disagree.

const val DASH = "–"
private val UNITS = listOf("B", "KB", "MB", "GB", "TB")
private const val KILO = 1024.0
private const val MINUTE_S = 60.0
private const val HOUR_S = 3600.0
private const val DAY_S = 86400.0
private const val DAY_MS = 86_400_000L
private const val WEEK_DAYS = 7

private val CLOCK = DateTimeFormatter.ofPattern("HH:mm")
private val DAY_MONTH = DateTimeFormatter.ofPattern("d MMM")
private val WEEKDAY = DateTimeFormatter.ofPattern("EEE, d MMM")
private val MONTH = DateTimeFormatter.ofPattern("MMM")

private fun decimal(value: Double, digits: Int): BigDecimal =
    BigDecimal(value).setScale(digits, RoundingMode.HALF_UP)

fun bytes(n: Double?): String {
    if (n == null || !n.isFinite()) return DASH
    var value = abs(n)
    var unit = 0
    while (value >= KILO && unit < UNITS.lastIndex) {
        value /= KILO
        unit += 1
    }
    val digits = if (value >= 100 || unit == 0) 0 else if (value >= 10) 1 else 2
    val shown = decimal(value, digits).stripTrailingZeros().toPlainString()
    return "${if (n < 0) "−" else ""}$shown ${UNITS[unit]}"
}

fun perSecond(n: Double?): String = if (n == null) DASH else "${bytes(n)}/s"

fun count(n: Double?): String = if (n == null) DASH else NumberFormat.getIntegerInstance().format(n.roundToLong())

fun percent(n: Double?): String = when {
    n == null -> DASH
    n < 10 -> "${decimal(n, 1).toPlainString()}%"
    else -> "${n.roundToLong()}%"
}

fun degrees(n: Double?): String = if (n == null) DASH else "${n.roundToLong()}°"

fun plain(n: Double, digits: Int): String = decimal(n, digits).toPlainString()

fun duration(seconds: Double?): String = when {
    seconds == null -> DASH
    seconds < MINUTE_S -> "${seconds.roundToLong()}s"
    seconds < HOUR_S -> "${(seconds / MINUTE_S).roundToLong()}m"
    seconds < DAY_S -> {
        val hours = floor(seconds / HOUR_S).toLong()
        val minutes = ((seconds % HOUR_S) / MINUTE_S).roundToLong()
        if (minutes > 0) "${hours}h ${minutes}m" else "${hours}h"
    }
    else -> {
        val days = floor(seconds / DAY_S).toLong()
        if (days == 1L) "1 day" else "$days days"
    }
}

private fun zoned(ms: Long, zone: ZoneId): ZonedDateTime = Instant.ofEpochMilli(ms).atZone(zone)

fun clock(ms: Long, zone: ZoneId = ZoneId.systemDefault()): String = CLOCK.format(zoned(ms, zone))

/** Seconds since the epoch as the shortest thing that's clear. */
fun moment(seconds: Double, now: ZonedDateTime = ZonedDateTime.now()): String {
    val date = zoned((seconds * 1000).toLong(), now.zone)
    if (date.toLocalDate() == now.toLocalDate()) return CLOCK.format(date)
    val day = DAY_MONTH.format(date)
    val recent = now.toEpochSecond() - date.toEpochSecond() < WEEK_DAYS * DAY_S
    return if (recent) "$day ${CLOCK.format(date)}" else day
}

fun nowSeconds(): Double = System.currentTimeMillis() / 1000.0

fun sinceNow(seconds: Double, now: Double = nowSeconds()): String = duration(now - seconds)

/** A chart's axis: times across two days or less, dates beyond that. */
fun timeLabel(ms: Long, span: Long, zone: ZoneId = ZoneId.systemDefault()): String =
    if (span <= 2 * DAY_MS) clock(ms, zone) else DAY_MONTH.format(zoned(ms, zone))

/** A chart's readout, which has room for both when the span needs them. */
fun tipTime(ms: Long, span: Long, zone: ZoneId = ZoneId.systemDefault()): String {
    val date = zoned(ms, zone)
    return when {
        span <= DAY_MS -> CLOCK.format(date)
        span <= WEEK_DAYS * DAY_MS + DAY_MS -> "${DAY_MONTH.format(date)} ${CLOCK.format(date)}"
        else -> DAY_MONTH.format(date)
    }
}

fun dayPlain(iso: String): String = runCatching { DAY_MONTH.format(LocalDate.parse(iso)) }.getOrDefault(iso)

fun dayShort(iso: String): String = runCatching { WEEKDAY.format(LocalDate.parse(iso)) }.getOrDefault(iso)

fun monthOf(day: LocalDate): String = MONTH.format(day)

fun money(cents: Double): String {
    val format = NumberFormat.getCurrencyInstance()
    format.currency = Currency.getInstance("AUD")
    format.maximumFractionDigits = if (abs(cents) >= 100_000) 0 else 2
    return format.format(cents / 100)
}

fun wifiWord(rssi: Int): String = if (rssi >= -60) "Strong" else if (rssi >= -72) "Good" else "Weak"

/** Fixed for each service wherever it appears, so a filter never repaints one. */
val SERVICE_ORDER = listOf("xrnloops", "xrnrule", "xrnhome", "xrntube", "xrnwal", "xrninsta", "xrnhub", "calendar", "xrnfit")
val LOG_SERVICES = listOf("calendar", "xrnhub", "xrnfit", "xrnloops", "xrnrule", "xrntube", "xrnhome", "xrnwal", "xrninsta", "tailscale", "ssh")

// The calendar service is Radicale, the server xrncal syncs with. It goes by the app's name on
// screen; the API and the stored history keep calling it `calendar`.
fun serviceName(name: String): String = when (name) {
    "calendar" -> "xrncal"
    "tailscale" -> "Tailscale"
    else -> name
}

fun problemWords(i: Incident): String = when (i.kind) {
    "down" -> "${serviceName(i.target)} is down"
    "unplugged" -> "Phone unplugged"
    "battery" -> "Battery low"
    "hot" -> "Phone running hot"
    "disk" -> "Disk nearly full"
    "backup" -> "Backup is old"
    "tailscale" -> "Tailscale not running"
    "supervisor" -> if (i.detail.isNotEmpty()) "More than one runsvdir" else "No runsvdir: stopped services stay stopped"
    "unsupervised" -> "${serviceName(i.target)} is running unsupervised"
    else -> i.kind
}

/**
 * For a notification, which lands on the phone in your hand: "Battery low" there would read as that
 * phone's battery, so anything about the server says so.
 */
fun alertWords(i: Incident): String = when (i.kind) {
    "down" -> "${serviceName(i.target)} is down"
    "unplugged" -> "Server unplugged"
    "battery" -> "Server battery low"
    "hot" -> "Server running hot"
    "disk" -> "Server disk nearly full"
    "backup" -> "Backup is old"
    "tailscale" -> "Tailscale isn't running"
    "supervisor" -> if (i.detail.isNotEmpty()) "Server: more than one runsvdir" else "Server: nothing restarts its services"
    "unsupervised" -> "${serviceName(i.target)} is running unsupervised"
    else -> i.kind
}

fun clearedWords(kind: String, target: String): String = when (kind) {
    "down" -> "${serviceName(target)} is back"
    "unplugged" -> "Server plugged back in"
    "battery" -> "Server battery is fine again"
    "hot" -> "Server cooled down"
    "disk" -> "Server disk has room again"
    "backup" -> "Backup is up to date"
    "tailscale" -> "Tailscale is back"
    "supervisor" -> "Server services are supervised again"
    "unsupervised" -> "${serviceName(target)} is supervised again"
    else -> "$kind is over"
}

fun incidentWords(i: Incident): String = when (i.kind) {
    "down" -> "${serviceName(i.target)} down"
    "restart" -> "${serviceName(i.target)} restarted"
    "restarted" ->
        if (i.detail == "failed") "Restart of ${serviceName(i.target)} failed" else "${serviceName(i.target)} restarted by you"
    "reboot" -> "Phone rebooted"
    "gap" -> "Monitor wasn't running"
    "supervisor" -> if (i.detail.isNotEmpty()) "More than one runsvdir" else "runsvdir missing"
    "unsupervised" -> "${serviceName(i.target)} unsupervised"
    else -> problemWords(i)
}

fun runwayWords(doc: DataDoc): String {
    val days = doc.runway?.days
    return when {
        doc.runway == null -> DASH
        days == null || days <= 0 -> "Not growing"
        days >= 365 -> {
            val years = days / 365
            if (years >= 10) "${years.roundToLong()} years" else "${plain(years, 1)} years"
        }
        else -> "${days.roundToLong()} days"
    }
}

/** A week's change as a figure: "▲ 5 pts", "▼ 3", "±0". The week it's measured against goes unsaid. */
fun weekChange(now: Int, before: Int, unit: String = ""): String {
    val gap = now - before
    val tail = if (unit.isEmpty()) "" else " $unit"
    return if (gap == 0) "±0$tail" else "${if (gap > 0) "▲" else "▼"} ${abs(gap)}$tail"
}

// ---- xrnfit among the services, as serverkit.js says it -----------------------------------------

/** xrnfit has no service of its own: the hub keeps what it sends, so it's listed right after it. */
const val FIT = "xrnfit"
const val FIT_KEPT_BY = "xrnhub"

/** Where a service's figure says how long it's been up, xrnfit's says when it last sent. */
fun fitAgo(card: FitCard, now: Double = nowSeconds()): String = card.lastIn?.let { "${sinceNow(it, now)} ago" } ?: DASH

fun readingsWords(n: Double): String = "${count(n)} ${if (n == 1.0) "reading" else "readings"}"

/** A day on xrnfit's strip, as a share for the strip to colour (1 sent, 0 nothing) and its words. */
fun sentDay(n: Double?): Pair<Double?, String> = when {
    n == null -> null to "Nothing yet"
    n > 0 -> 1.0 to readingsWords(n)
    else -> 0.0 to "Nothing sent"
}

/** [items], one a service in [names]' order, with [extra] put in after the hub's. */
fun <T> besideHub(names: List<String>, items: List<T>, extra: T?): List<T> {
    if (extra == null) return items
    val at = names.indexOf(FIT_KEPT_BY)
    return items.toMutableList().apply { add(if (at < 0) size else at + 1, extra) }
}

fun logText(e: DayLog): String = when (e.kind) {
    "blocked" -> "Stopped ${e.title.orEmpty()}"
    "unlock" -> "Unlocked ${e.title.orEmpty()}${e.value?.content?.takeIf { it.isNotEmpty() && it != "null" }?.let { " for ${it}m" }.orEmpty()}"
    "spent" -> e.title ?: "Spent"
    "earned" -> e.title ?: "Earned"
    "batch" -> "${e.value?.content ?: "0"} mail delivered"
    "early" -> "Mail delivered early"
    else -> e.title ?: e.kind
}

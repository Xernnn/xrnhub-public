package com.xern.xrnhub.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive

/**
 * What the hub's API answers with, as far as the app reads it. A reading the phone can't take comes
 * through as null, so every number that may be missing is nullable and shows as a dash. Numbers are
 * Double unless they're counted in ones, because Python sends whichever of 3 and 3.5 it has.
 */
val docJson = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    explicitNulls = false
}

@Serializable
data class SystemInfo(val uptime: Double? = null, val load: List<Double> = emptyList())

@Serializable
data class Memory(val total: Double = 0.0, val available: Double = 0.0) {
    val used get() = total - available
}

@Serializable
data class Temps(val cpu: Double? = null, val battery: Double? = null)

@Serializable
data class Cores(val speed: Double? = null)

@Serializable
data class Disk(val total: Double = 0.0, val free: Double = 0.0, val used: Double = 0.0)

@Serializable
data class Speed(@SerialName("in") val down: Double? = null, @SerialName("out") val up: Double? = null)

@Serializable
data class Battery(val percent: Double? = null, val plugged: Boolean = false, val status: String = "")

@Serializable
data class Wifi(val rssi: Int? = null, val linkMbps: Double? = null, val ghz: Double? = null)

@Serializable
data class About(val cores: Int? = null)

@Serializable
data class ServiceRow(
    val name: String,
    val state: String = "",
    val since: Double? = null,
    val restartable: Boolean = false,
    val answers: Boolean? = null,
    val cpu: Double? = null,
    val rss: Double? = null,
    val threads: Int? = null,
    val cpuTotal: Double? = null,
    val read: Double? = null,
    val written: Double? = null,
    val diskWritten: Double? = null,
) {
    /** Running isn't enough: one that stopped answering on its port counts as down. */
    val up get() = state == "run" && answers != false
}

@Serializable
data class Totals(
    val reqs: Double = 0.0,
    val polls: Double = 0.0,
    val writes: Double = 0.0,
    val refused: Double = 0.0,
    val errors: Double = 0.0,
)

@Serializable
data class Incident(
    val kind: String,
    val target: String = "",
    val started: Double = 0.0,
    val ended: Double? = null,
    val detail: String = "",
)

/** One moment on the live charts. */
@Serializable
data class Point(
    val at: Long,
    val load: Double? = null,
    val speed: Double? = null,
    val cpu: Double? = null,
    val memUsed: Double? = null,
    val cpuTemp: Double? = null,
    val battTemp: Double? = null,
    val netIn: Double? = null,
    val netOut: Double? = null,
)

@Serializable
data class FeedRow(
    val at: Double = 0.0,
    val service: String = "",
    val method: String = "",
    val path: String = "",
    val status: Int = 0,
    val ip: String = "",
    val poll: Boolean = false,
)

@Serializable
data class NowDoc(
    val at: Long? = null,
    val system: SystemInfo? = null,
    val memory: Memory? = null,
    val temps: Temps? = null,
    val cores: Cores? = null,
    val disk: Disk? = null,
    val services: List<ServiceRow> = emptyList(),
    val speed: Speed? = null,
    val battery: Battery? = null,
    val wifi: Wifi? = null,
    val hour: Map<String, Totals> = emptyMap(),
    val about: About? = null,
    val problems: List<Incident> = emptyList(),
    val samples: List<Point> = emptyList(),
    val feed: List<FeedRow>? = null,
    val health: FitCard? = null,
)

/**
 * xrnfit on the Overview. It has no service of its own, so it comes beside the services rather than
 * among them. `lastIn` is in seconds, as on the Apps page; the figures' times are epoch ms. `late`
 * means nothing has come in for six hours, and `week` is null until the inventory has counted.
 */
@Serializable
data class FitCard(
    val lastIn: Double? = null,
    val late: Boolean = false,
    val week: Double? = null,
    val day: String = "",
    val steps: Double? = null,
    val sleep: FitSleep? = null,
    val resting: FitResting? = null,
    val weight: FitWeight? = null,
    val spo2: FitSpo2? = null,
    val stress: FitStress? = null,
)

@Serializable
data class FitSleep(val day: String = "", val minutes: Double = 0.0)

@Serializable
data class FitResting(val day: String = "", val bpm: Double = 0.0)

@Serializable
data class FitWeight(val at: Long = 0, val kg: Double = 0.0)

@Serializable
data class FitSpo2(val at: Long = 0, val percent: Double = 0.0)

@Serializable
data class FitStress(val at: Long = 0, val level: Double = 0.0)

@Serializable
data class ServiceSeries(val cpu: List<Double?> = emptyList(), val rss: List<Double?> = emptyList())

@Serializable
data class TrafficSeries(
    val reqs: List<Double?> = emptyList(),
    val polls: List<Double?> = emptyList(),
    val writes: List<Double?> = emptyList(),
)

@Serializable
data class HistoryDoc(
    val bucket: Long = 60,
    val at: List<Long> = emptyList(),
    val system: Map<String, List<Double?>> = emptyMap(),
    val services: Map<String, ServiceSeries> = emptyMap(),
    val traffic: Map<String, TrafficSeries> = emptyMap(),
)

@Serializable
data class ServicesDoc(
    val uptime: Map<String, List<Double?>> = emptyMap(),
    val incidents: List<Incident> = emptyList(),
    /** xrnfit's strip in place of an uptime one: readings that came in each day, null before the first. */
    val fitDays: List<Double?> = emptyList(),
    /** Everything over the strips' days, for the timeline: `incidents` is only the latest. Absent from
     * a hub before 2026-11-19, when the timeline falls back to `incidents`. */
    val timeline: List<Incident>? = null,
)

@Serializable
data class PathRow(val service: String = "", val path: String = "", val count: Double = 0.0, val status: Int = 0)

@Serializable
data class ClientRow(val ip: String = "", val reqs: Double = 0.0, val refused: Double = 0.0, val days: Int = 0)

@Serializable
data class TrafficDoc(
    val totals: Map<String, Totals> = emptyMap(),
    val paths: List<PathRow> = emptyList(),
    val clients: List<ClientRow> = emptyList(),
    val status: Map<String, Double> = emptyMap(),
)

@Serializable
data class AppRow(
    val name: String,
    val bytes: Double = 0.0,
    val records: Double = 0.0,
    val unit: String = "",
    val grew: Double? = null,
    val added: Double? = null,
)

@Serializable
data class DailyRecords(val days: List<String> = emptyList(), val apps: Map<String, List<Double?>> = emptyMap())

@Serializable
data class DailyTraffic(
    val days: List<String> = emptyList(),
    val writes: Map<String, List<Double?>> = emptyMap(),
    @SerialName("in") val down: List<Double?> = emptyList(),
    @SerialName("out") val up: List<Double?> = emptyList(),
)

@Serializable
data class Network(@SerialName("in") val down: Double? = null, @SerialName("out") val up: Double? = null)

@Serializable
data class ProcessRow(
    val name: String,
    val read: Double? = null,
    val written: Double? = null,
    val diskWritten: Double? = null,
    val since: Double? = null,
)

@Serializable
data class Runway(val perDay: Double? = null, val days: Double? = null)

@Serializable
data class DataDoc(
    val pending: Boolean = false,
    val at: Double = 0.0,
    val total: Double = 0.0,
    val extra: Double = 0.0,
    val logs: Double = 0.0,
    val backup: Double? = null,
    val apps: List<AppRow> = emptyList(),
    val fed: DailyRecords = DailyRecords(),
    val growth: DailyRecords = DailyRecords(),
    val daily: DailyTraffic = DailyTraffic(),
    val disk: Disk? = null,
    val network: Network? = null,
    val services: List<ProcessRow> = emptyList(),
    val runway: Runway? = null,
    val monitorBytes: Double = 0.0,
)

@Serializable
data class LoginCounts(val ok: Double = 0.0, val refused: Double = 0.0)

@Serializable
data class LoginRow(
    val at: Double = 0.0,
    val service: String = "",
    val ok: Boolean = false,
    val who: String? = null,
    val ip: String? = null,
    val count: Int = 1,
)

@Serializable
data class Device(
    val name: String = "",
    val os: String? = null,
    val online: Boolean = false,
    val direct: Boolean = false,
    val relay: String = "",
)

@Serializable
data class Tailnet(
    val state: String = "",
    val version: String = "",
    val devices: List<Device> = emptyList(),
    val ingress: Int = 0,
)

@Serializable
data class SecurityDoc(
    val day: LoginCounts = LoginCounts(),
    val week: LoginCounts = LoginCounts(),
    val logins: List<LoginRow> = emptyList(),
    val refused: List<PathRow> = emptyList(),
    val status: Map<String, Double> = emptyMap(),
    val clients: List<ClientRow> = emptyList(),
    val tailnet: Tailnet? = null,
    val funnel: List<String>? = null,
)

@Serializable
data class LogRow(val at: Double = 0.0, val service: String = "", val level: String = "info", val text: String = "")

@Serializable
data class LogsDoc(val lines: List<LogRow> = emptyList())

/** One message's warnings or errors over the range: how often, when last (ms), and its newest line. */
@Serializable
data class LogGroup(
    val service: String = "",
    val level: String = "warn",
    val sig: String = "",
    val count: Double = 0.0,
    val last: Double = 0.0,
    val text: String = "",
)

/** The Logs page's figures: lines by level, in total and by service, on history's buckets. */
@Serializable
data class LogStatsDoc(
    val bucket: Long = 3600,
    val at: List<Long> = emptyList(),
    val levels: Map<String, List<Double>> = emptyMap(),
    val services: Map<String, Map<String, List<Double>>> = emptyMap(),
    val groups: List<LogGroup> = emptyList(),
)

@Serializable
data class YearDay(val day: String, val share: Double? = null)

@Serializable
data class HabitRow(
    val name: String = "",
    val color: Int? = null,
    val pct: Int? = null,
    val streak: Int = 0,
    val best: Int = 0,
    val strip: List<Int?> = emptyList(),
)

@Serializable
data class HabitsDoc(val first: String? = null, val items: List<HabitRow> = emptyList())

@Serializable
data class HourSeries(val name: String = "", val color: String? = null, val hours: List<Double> = emptyList())

@Serializable
data class Hours(val weeks: List<String> = emptyList(), val series: List<HourSeries> = emptyList())

@Serializable
data class AppCount(val name: String = "", val count: Int = 0)

@Serializable
data class Temptations(
    val days: Int = 0,
    val total: Int = 0,
    val thisWeek: Int = 0,
    val lastWeek: Int = 0,
    val apps: List<AppCount> = emptyList(),
    val hours: List<Int> = emptyList(),
)

@Serializable
data class Trend(val thisWeek: Int = 0, val lastWeek: Int = 0)

/** One day of the health card: each measure's number, or null when nothing measured it. */
@Serializable
data class BodyDay(
    val date: String = "",
    val steps: Double? = null,
    val asleep: Double? = null,
    val resting: Double? = null,
    val weightKg: Double? = null,
    val spo2: Double? = null,
    val stress: Double? = null,
) {
    fun value(key: String): Double? = when (key) {
        "steps" -> steps
        "asleep" -> asleep
        "resting" -> resting
        "weightKg" -> weightKg
        "spo2" -> spo2
        "stress" -> stress
        else -> null
    }
}

@Serializable
data class BodyNumbers(val avg: Double? = null, val latest: Double? = null, val day: String? = null)

/** The same days as the habit strips, and each measure's month: only those that came in are listed. */
@Serializable
data class Body(
    val first: String = "",
    val days: List<BodyDay> = emptyList(),
    val summary: Map<String, BodyNumbers> = emptyMap(),
)

@Serializable
data class ReviewDoc(
    val day: String = "",
    val year: List<YearDay> = emptyList(),
    val habits: HabitsDoc = HabitsDoc(),
    val hours: Hours? = null,
    val temptations: Temptations = Temptations(),
    val health: Body? = null,
    val trend: Trend? = null,
    val loggedDays: Int = 0,
    val windowDays: Int = 0,
)

@Serializable
data class DayHabit(
    val name: String = "",
    val color: Int? = null,
    val done: Boolean = false,
    val skipped: Boolean = false,
    val counts: Boolean = false,
)

@Serializable
data class DayHabits(val items: List<DayHabit> = emptyList())

@Serializable
data class AgendaRow(
    val start: String = "",
    val allDay: Boolean = false,
    val title: String = "",
    val color: String? = null,
)

@Serializable
data class DayCalendar(val items: List<AgendaRow> = emptyList())

@Serializable
data class DayLog(
    val at: Double = 0.0,
    val kind: String = "",
    val title: String? = null,
    val value: JsonPrimitive? = null,
    val cents: Double? = null,
)

@Serializable
data class DaySleep(
    val minutes: Double = 0.0,
    val deep: Double = 0.0,
    val light: Double = 0.0,
    val rem: Double = 0.0,
    val asleep: Double = 0.0,
    val awake: Double = 0.0,
    val start: Long = 0,
    val end: Long = 0,
)

@Serializable
data class DayHeart(val min: Double? = null, val avg: Double? = null, val max: Double? = null, val resting: Double? = null)

/** The lowest, mean and highest of a day's samples. */
@Serializable
data class Spread(val min: Double = 0.0, val avg: Double = 0.0, val max: Double = 0.0)

@Serializable
data class DayHealth(
    val steps: Double? = null,
    val sleep: DaySleep? = null,
    val heartRate: DayHeart? = null,
    val weightKg: Double? = null,
    val spo2: Spread? = null,
    val stress: Spread? = null,
)

@Serializable
data class DayDoc(
    val day: String = "",
    val habits: DayHabits = DayHabits(),
    val calendar: DayCalendar = DayCalendar(),
    val log: List<DayLog> = emptyList(),
    val health: DayHealth? = null,
)

@Serializable
data class RestartAnswer(val ok: Boolean = false, val said: String = "")

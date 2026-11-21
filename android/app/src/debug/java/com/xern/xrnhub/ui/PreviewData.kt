package com.xern.xrnhub.ui

// Made-up numbers for the page previews, so every page renders without a phone or a server. None
// of it is real: the names, addresses and figures are invented. Debug only, beside the previews.

import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import com.xern.xrnhub.data.About
import com.xern.xrnhub.data.AgendaRow
import com.xern.xrnhub.data.AppCount
import com.xern.xrnhub.data.AppRow
import com.xern.xrnhub.data.Battery
import com.xern.xrnhub.data.Body
import com.xern.xrnhub.data.BodyDay
import com.xern.xrnhub.data.BodyNumbers
import com.xern.xrnhub.data.ClientRow
import com.xern.xrnhub.data.Cores
import com.xern.xrnhub.data.DailyRecords
import com.xern.xrnhub.data.DailyTraffic
import com.xern.xrnhub.data.DataDoc
import com.xern.xrnhub.data.DayCalendar
import com.xern.xrnhub.data.DayDoc
import com.xern.xrnhub.data.DayHabit
import com.xern.xrnhub.data.DayHabits
import com.xern.xrnhub.data.DayHealth
import com.xern.xrnhub.data.DayHeart
import com.xern.xrnhub.data.DayLog
import com.xern.xrnhub.data.DaySleep
import com.xern.xrnhub.data.Device
import com.xern.xrnhub.data.Disk
import com.xern.xrnhub.data.FeedRow
import com.xern.xrnhub.data.FitCard
import com.xern.xrnhub.data.FitResting
import com.xern.xrnhub.data.FitSleep
import com.xern.xrnhub.data.FitSpo2
import com.xern.xrnhub.data.FitStress
import com.xern.xrnhub.data.FitWeight
import com.xern.xrnhub.data.HabitRow
import com.xern.xrnhub.data.HabitsDoc
import com.xern.xrnhub.data.HistoryDoc
import com.xern.xrnhub.data.HourSeries
import com.xern.xrnhub.data.Hours
import com.xern.xrnhub.data.Incident
import com.xern.xrnhub.data.LogGroup
import com.xern.xrnhub.data.LogStatsDoc
import com.xern.xrnhub.data.LoginCounts
import com.xern.xrnhub.data.LoginRow
import com.xern.xrnhub.data.LogRow
import com.xern.xrnhub.data.LogsDoc
import com.xern.xrnhub.data.Memory
import com.xern.xrnhub.data.Network
import com.xern.xrnhub.data.NowDoc
import com.xern.xrnhub.data.PathRow
import com.xern.xrnhub.data.Point
import com.xern.xrnhub.data.ProcessRow
import com.xern.xrnhub.data.ReviewDoc
import com.xern.xrnhub.data.Runway
import com.xern.xrnhub.data.SecurityDoc
import com.xern.xrnhub.data.ServiceRow
import com.xern.xrnhub.data.ServiceSeries
import com.xern.xrnhub.data.ServicesDoc
import com.xern.xrnhub.data.Speed
import com.xern.xrnhub.data.Spread
import com.xern.xrnhub.data.SystemInfo
import com.xern.xrnhub.data.Tailnet
import com.xern.xrnhub.data.Temps
import com.xern.xrnhub.data.Temptations
import com.xern.xrnhub.data.Totals
import com.xern.xrnhub.data.TrafficDoc
import com.xern.xrnhub.data.TrafficSeries
import com.xern.xrnhub.data.Trend
import com.xern.xrnhub.data.Wifi
import com.xern.xrnhub.data.YearDay
import java.time.LocalDate
import kotlinx.serialization.json.JsonPrimitive
import kotlin.math.abs
import kotlin.math.sin

internal object Fake {
    private const val MB = 1024.0 * 1024
    private const val GB = MB * 1024
    private const val DAY_S = 86_400.0
    private const val BUCKET = 900L
    private const val BUCKETS = 96

    val nowMs = System.currentTimeMillis()
    val nowS = nowMs / 1000.0
    val today: LocalDate = LocalDate.now()
    private fun daysAgo(n: Long) = today.minusDays(n).toString()

    /** A wobbly but repeatable line around `base`. */
    fun wave(n: Int, base: Double, swing: Double, phase: Double = 0.0): List<Double?> =
        List(n) { i -> (base + swing * sin(i / 7.0 + phase) + swing * 0.4 * sin(i / 2.3 + phase * 2)).coerceAtLeast(0.0) }

    val names = listOf("calendar", "xrnloops", "xrnrule", "xrntube", "xrnhome", "xrnwal", "xrninsta", "xrnhub")

    val services = names.mapIndexed { i, name ->
        ServiceRow(
            name, state = "run", since = nowS - (3 + i * 3) * DAY_S, restartable = true, answers = name != "xrntube",
            cpu = 0.3 + i * 0.4, rss = (18.0 + i * 7) * MB, threads = 3 + i, cpuTotal = 2400.0 * (i + 1),
            read = (40.0 + i * 13) * MB, written = (12.0 + i * 5) * MB, diskWritten = (6.0 + i * 2) * MB,
        )
    }

    val fit = FitCard(
        lastIn = nowS - 720, week = 4870.0, day = today.toString(), steps = 8631.0, sleep = FitSleep(today.toString(), 363.0),
        resting = FitResting(today.toString(), 61.0), weight = FitWeight(nowMs - 5 * 3_600_000L, 78.4),
        spo2 = FitSpo2(nowMs - 8 * 3_600_000L, 97.0), stress = FitStress(nowMs - 1_800_000L, 31.0),
    )

    val hour = names.mapIndexed { i, name -> name to Totals(reqs = 40.0 + i * 25, polls = 30.0 + i * 10, writes = 2.0 + i) }.toMap() +
        ("xrnfit" to Totals(reqs = 110.0, writes = 96.0))

    /** Fifteen minutes of requests: mostly answered, a few unchanged, two refused, one failed. */
    val feed = List(60) { i ->
        val service = listOf("xrnloops", "xrnrule", "xrnfit", "xrnhub", "calendar", "xrnwal")[(i * 7) % 6]
        val status = when {
            i == 17 || i == 41 -> 401
            i == 29 -> 500
            i % 5 == 0 -> 304
            else -> 200
        }
        FeedRow(
            nowMs - (i * 14_500.0 + (i % 3) * 4_000), service, if (i % 3 == 0) "POST" else "GET",
            "/$service/api/${if (i % 2 == 0) "sync" else "changes"}", status, if (i % 4 == 0) "198.51.100.7" else "192.0.2.2", poll = i % 5 == 0,
        )
    }

    val ring = List(120) { i ->
        Point(
            at = nowMs - (120 - i) * 5_000L, load = 1.2 + 0.4 * sin(i / 9.0), speed = 0.4 + 0.2 * sin(i / 5.0), cpu = 3.0 + 2 * sin(i / 6.0),
            memUsed = 3.4 * GB + 0.2 * GB * sin(i / 11.0), cpuTemp = 38 + 3 * sin(i / 8.0), battTemp = 31 + sin(i / 12.0),
            netIn = 14_000 + 9_000 * abs(sin(i / 4.0)), netOut = 3_000 + 2_000 * abs(sin(i / 3.0)),
        )
    }

    val now = NowDoc(
        at = nowMs - 2_000, system = SystemInfo(uptime = 26 * DAY_S, load = listOf(1.42, 1.31, 1.2)),
        memory = Memory(5.6 * GB, 2.1 * GB), temps = Temps(38.0, 31.0), cores = Cores(0.42), disk = Disk(110 * GB, 71 * GB, 39 * GB),
        services = services, speed = Speed(14_000.0, 3_200.0), battery = Battery(82.0, true, "CHARGING"), wifi = Wifi(-58, 433.0, 5.0),
        hour = hour, about = About(8), samples = ring, feed = feed, health = fit,
    )

    val history = HistoryDoc(
        bucket = BUCKET,
        at = List(BUCKETS) { i -> nowS.toLong() - (BUCKETS - i) * BUCKET },
        system = mapOf(
            "load1" to wave(BUCKETS, 1.3, 0.5), "speed" to wave(BUCKETS, 0.4, 0.2, 1.0), "cpu" to wave(BUCKETS, 3.0, 2.0, 2.0),
            "mem_used" to wave(BUCKETS, 3.4 * GB, 0.3 * GB, 0.5), "cpu_temp" to wave(BUCKETS, 38.0, 3.0), "batt_temp" to wave(BUCKETS, 31.0, 1.0, 1.0),
            "net_in" to wave(BUCKETS, 12 * MB, 8 * MB), "net_out" to wave(BUCKETS, 3 * MB, 2 * MB, 2.0),
        ),
        services = (names + "xrnfit").associateWith { ServiceSeries(cpu = wave(BUCKETS, 1.0, 0.6, it.length.toDouble()), rss = wave(BUCKETS, 30 * MB, 4 * MB)) },
        traffic = (names + "xrnfit").mapIndexed { i, name ->
            name to TrafficSeries(
                reqs = wave(BUCKETS, 30.0 + i * 6, 15.0, i.toDouble()), polls = wave(BUCKETS, 20.0, 5.0, i.toDouble()),
                writes = wave(BUCKETS, 3.0 + i, 2.0, i.toDouble()),
            )
        }.toMap(),
    )

    val servicesDoc = ServicesDoc(
        uptime = names.associateWith { name -> List(8) { null } + List(22) { i -> if (name == "xrntube" && i == 18) 0.93 else 1.0 } },
        incidents = listOf(
            Incident("down", "xrntube", nowS - 3 * 3600, null),
            Incident("restarted", "xrnrule", nowS - 2 * DAY_S, nowS - 2 * DAY_S + 4),
            Incident("down", "xrnhome", nowS - 4 * DAY_S, nowS - 4 * DAY_S + 380),
            Incident("reboot", "", nowS - 6 * DAY_S, nowS - 6 * DAY_S + 90),
        ),
        fitDays = List(22) { null } + listOf(4517.0, 2210.0, 0.0, 3480.0, 2970.0, 3109.0, 2640.0, 1206.0),
        timeline = listOf(
            Incident("down", "xrntube", nowS - 3 * 3600, null),
            Incident("restart", "xrntube", nowS - 9 * DAY_S, nowS - 9 * DAY_S),
            Incident("down", "xrntube", nowS - 16 * DAY_S, nowS - 16 * DAY_S + 5 * 3600),
            Incident("restarted", "xrnrule", nowS - 2 * DAY_S, nowS - 2 * DAY_S + 4),
            Incident("restart", "xrnrule", nowS - 21 * DAY_S, nowS - 21 * DAY_S),
            Incident("down", "xrnhome", nowS - 4 * DAY_S, nowS - 4 * DAY_S + 380),
            Incident("restart", "xrnhub", nowS - 12 * DAY_S, nowS - 12 * DAY_S),
            Incident("reboot", "", nowS - 6 * DAY_S, nowS - 6 * DAY_S + 90),
            Incident("battery", "", nowS - 18 * DAY_S, nowS - 18 * DAY_S + 7 * 3600, "18%"),
            Incident("gap", "monitor", nowS - 25 * DAY_S, nowS - 25 * DAY_S + 9 * 3600),
        ),
    )

    private val stamps = List(BUCKETS) { i -> nowS.toLong() - (BUCKETS - i) * BUCKET }
    private fun counts(seed: Int, scale: Double) =
        List(BUCKETS) { i -> kotlin.math.floor(abs(sin((i + seed) / 5.0)) * scale + if ((i * seed) % 7 == 0) scale else 0.0) }

    /** The log's figures: lines by level and service, and warnings and errors by message. */
    fun logStats(service: String?): LogStatsDoc {
        val logged = (service?.let { listOf(it) } ?: listOf("xrnloops", "xrnrule", "xrntube", "xrnhub", "calendar", "ssh"))
        val services = logged.mapIndexed { i, name ->
            name to mapOf("info" to counts(i + 1, 6.0), "warn" to counts(i + 3, 2.0), "error" to counts(i + 5, 0.6))
        }.toMap()
        val levels = listOf("info", "warn", "error").associateWith { level -> List(BUCKETS) { b -> services.values.sumOf { it.getValue(level)[b] } } }
        val groups = listOf(
            LogGroup("xrntube", "error", "error: upstream timed out", 14.0, nowMs - 600_000.0, "error: upstream timed out after 30s"),
            LogGroup("ssh", "error", "error: kex_exchange_identification", 5.0, nowMs - 3_600_000.0, "error: kex_exchange_identification: read: Connection reset"),
            LogGroup("xrnloops", "warn", "\"POST /changes\" 409", 23.0, nowMs - 240_000.0, "192.0.2.2 \"POST /changes HTTP/1.1\" 409 -"),
            LogGroup("xrnrule", "warn", "warning: slow query", 11.0, nowMs - 1_200_000.0, "warning: slow query took 1.2s"),
            LogGroup("calendar", "warn", "\"PROPFIND /\" 401", 9.0, nowMs - 5_400_000.0, "198.51.100.7 \"PROPFIND / HTTP/1.1\" 401 -"),
            LogGroup("xrnhub", "warn", "warning: phone sample late", 6.0, nowMs - 7_200_000.0, "warning: phone sample late by 41s"),
            LogGroup("xrntube", "warn", "\"GET /state\" 404", 4.0, nowMs - 9_000_000.0, "198.51.100.200 \"GET /state HTTP/1.1\" 404 -"),
            LogGroup("xrnrule", "warn", "warning: report retried", 3.0, nowMs - 20_000_000.0, "warning: report retried"),
            LogGroup("xrnloops", "warn", "warning: habit sync behind", 2.0, nowMs - 30_000_000.0, "warning: habit sync behind by 2 versions"),
            LogGroup("calendar", "warn", "warning: slow PROPFIND", 1.0, nowMs - 40_000_000.0, "warning: slow PROPFIND took 2.1s"),
        ).filter { service == null || it.service == service }
        return LogStatsDoc(BUCKET, stamps, levels, services, groups)
    }

    fun logs(service: String?) = LogsDoc(List(10) { i ->
        val name = service ?: names[i % names.size]
        LogRow(nowMs - i * 240_000.0, name, if (i == 2) "warn" else if (i == 5) "error" else "info", "$name: ${if (i == 5) "sync refused: stale version" else "GET /api/changes 200 12ms"}")
    })

    val traffic = TrafficDoc(
        totals = (names + "xrnfit").mapIndexed { i, name -> name to Totals(reqs = 300.0 + i * 140, polls = 200.0 + i * 40, writes = 10.0 + i * 3, refused = if (i == 2) 4.0 else 0.0, errors = if (i == 3) 1.0 else 0.0) }.toMap(),
        paths = listOf(
            PathRow("xrnloops", "/xrnloops/api/changes", 1840.0), PathRow("xrnrule", "/xrnrule/api/sync", 1210.0),
            PathRow("xrnfit", "/xrnhub/api/health", 980.0), PathRow("calendar", "/", 620.0), PathRow("xrnhome", "/xrnhome/api/state", 300.0),
        ),
        clients = listOf(ClientRow("192.0.2.2", 3120.0), ClientRow("192.0.2.3", 1904.0, 4.0), ClientRow("203.0.113.40", 12.0, 12.0)),
        status = mapOf("200" to 4100.0, "204" to 600.0, "304" to 820.0, "401" to 16.0, "404" to 9.0, "503" to 1.0),
    )

    private val days14 = List(14) { daysAgo(13L - it) }

    val data = DataDoc(
        at = nowS, total = 412 * MB, extra = 96 * MB, logs = 18 * MB, backup = nowS - 7 * 3600, monitorBytes = 41 * MB,
        apps = listOf(
            AppRow("xrnwal", 120 * MB, 4210.0, "entries", 1.2 * MB, 64.0), AppRow("xrnloops", 64 * MB, 9600.0, "check-ins", 0.4 * MB, 140.0),
            AppRow("calendar", 31 * MB, 1890.0, "events", 0.1 * MB, 12.0), AppRow("xrnfit", 22 * MB, 52_000.0, "readings", 2.1 * MB, 19_000.0),
            AppRow("xrnrule", 8 * MB, 270.0, "reports", 0.0, 13.0),
        ),
        fed = DailyRecords(days14, mapOf("xrnloops" to wave(14, 20.0, 8.0), "xrnwal" to wave(14, 9.0, 4.0, 1.0), "xrnfit" to wave(14, 2700.0, 900.0, 2.0))),
        growth = DailyRecords(days14, mapOf("xrnwal" to List(14) { 100 * MB + it * 1.4 * MB }, "xrnloops" to List(14) { 58 * MB + it * 0.5 * MB }, "xrnfit" to List(14) { 4 * MB + it * 1.3 * MB })),
        daily = DailyTraffic(days14, mapOf("xrnloops" to wave(14, 22.0, 6.0), "xrnwal" to wave(14, 8.0, 3.0, 1.0)), wave(14, 380 * MB, 120 * MB), wave(14, 90 * MB, 40 * MB, 1.0)),
        disk = Disk(110 * GB, 71 * GB, 39 * GB), network = Network(41 * GB, 9.2 * GB),
        services = services.map { ProcessRow(it.name, it.read, it.written, it.diskWritten, it.since) },
        runway = Runway(5.2 * MB, 13_900.0),
    )

    val security = SecurityDoc(
        day = LoginCounts(ok = 2.0, refused = 1.0), week = LoginCounts(ok = 14.0, refused = 6.0),
        logins = listOf(
            LoginRow(nowMs - 3_600_000.0, "xrnhub", true, "owner", "192.0.2.2"),
            LoginRow(nowMs - 5_400_000.0, "xrnloops", false, "admin", "203.0.113.40", 3),
            LoginRow(nowMs - 2 * DAY_S * 1000, "calendar", true, "owner", "192.0.2.3"),
        ),
        refused = listOf(PathRow("xrnhub", "/xrnhub/api/session", 9.0, 401), PathRow("calendar", "/.well-known/caldav", 4.0, 404)),
        status = mapOf("200" to 9000.0, "401" to 22.0, "404" to 9.0, "429" to 2.0),
        clients = listOf(ClientRow("203.0.113.40", 31.0, 18.0, 3), ClientRow("198.51.100.7", 6.0, 4.0, 1)),
        tailnet = Tailnet("Running", "1.80.2", listOf(
            Device("laptop", "linux", online = true, direct = true), Device("phone", "android", online = true, relay = "syd"),
            Device("old-tablet", "android", online = false),
        ), ingress = 3),
        funnel = listOf("/", "/xrnloops", "/xrnrule", "/xrnhub", "/xrnwal"),
    )

    private const val HABIT_DAYS = 30
    private fun strip(seed: Int) = List(HABIT_DAYS) { i -> when ((i * 7 + seed * 3) % 9) { 0 -> 0; 1 -> 2; 2 -> null; else -> 1 } }

    val review = ReviewDoc(
        day = today.toString(),
        year = List(370) { i -> YearDay(daysAgo(369L - i), if (i % 11 == 0) null else ((i * 37) % 100) / 100.0) },
        habits = HabitsDoc(daysAgo(400), listOf(
            HabitRow("Stretch", 3, 52, 0, 9, strip(1)), HabitRow("Read", 7, 71, 4, 16, strip(2)),
            HabitRow("Walk", 11, 83, 6, 21, strip(3)), HabitRow("Water", 15, 94, 12, 40, strip(4)),
        )),
        hours = Hours(List(8) { daysAgo(7L * (7 - it)) }, listOf(
            HourSeries("Work", "#2563eb", List(8) { 18.0 + (it * 5) % 7 }), HourSeries("Study", "#16a34a", List(8) { 9.0 + (it * 3) % 5 }),
            HourSeries("Sport", "#f97316", List(8) { 3.0 + it % 3 }),
        )),
        temptations = Temptations(30, 46, 9, 12, listOf(AppCount("Video app", 21), AppCount("Forum", 14), AppCount("Game", 11)), List(24) { h -> if (h in 8..23) (h * 5) % 7 else 0 }),
        health = Body(daysAgo(29), List(30) { i ->
            BodyDay(daysAgo(29L - i), steps = 4000.0 + (i * 1371) % 7000, asleep = 330.0 + (i * 17) % 90, resting = 60.0 + i % 5, weightKg = 78.2 + (i % 4) * 0.2,
                spo2 = 96.0 + i % 3, stress = 25.0 + (i * 7) % 20)
        }, mapOf(
            "steps" to BodyNumbers(7420.0, 8631.0), "asleep" to BodyNumbers(371.0, 363.0), "resting" to BodyNumbers(62.0, 61.0),
            "weightKg" to BodyNumbers(78.6, 78.4), "spo2" to BodyNumbers(97.0, 97.0), "stress" to BodyNumbers(33.0, 31.0),
        )),
        trend = Trend(74, 69), loggedDays = 27, windowDays = 30,
    )

    val day = DayDoc(
        day = today.toString(),
        habits = DayHabits(listOf(
            DayHabit("Water", 15, done = true, counts = true), DayHabit("Walk", 11, done = true, counts = true),
            DayHabit("Read", 7, counts = true), DayHabit("Stretch", 3, skipped = true, counts = true),
        )),
        calendar = DayCalendar(listOf(
            AgendaRow("${today}T09:30", title = "Lecture", color = "#2563eb"), AgendaRow("${today}T18:00", title = "Gym", color = "#f97316"),
        )),
        log = listOf(
            DayLog(nowMs - 4 * 3_600_000.0, "blocked", "Video app"), DayLog(nowMs - 3 * 3_600_000.0, "unlock", "Video app", JsonPrimitive(15)),
            DayLog(nowMs - 2 * 3_600_000.0, "spent", "Lunch", cents = 1450.0),
        ),
        health = DayHealth(
            steps = 8631.0,
            sleep = DaySleep(363.0, 82.0, 196.0, 61.0, 0.0, 24.0, nowMs - 16 * 3_600_000L, nowMs - 10 * 3_600_000L),
            heartRate = DayHeart(55.0, 72.0, 128.0, 61.0), weightKg = 78.4, spo2 = Spread(93.0, 97.0, 99.0), stress = Spread(12.0, 31.0, 66.0),
        ),
    )
}

/** A page as the app shows it, on the page's own background. */
@Composable
internal fun PageFrame(content: @Composable () -> Unit) = HubTheme {
    Box(Modifier.fillMaxSize().background(colors.bg)) { content() }
}

internal val nowhere: Go = { _, _ -> }

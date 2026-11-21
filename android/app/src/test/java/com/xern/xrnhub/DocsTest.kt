package com.xern.xrnhub

import com.xern.xrnhub.data.DataDoc
import com.xern.xrnhub.data.DayDoc
import com.xern.xrnhub.data.HistoryDoc
import com.xern.xrnhub.data.LogStatsDoc
import com.xern.xrnhub.data.LogsDoc
import com.xern.xrnhub.data.NowDoc
import com.xern.xrnhub.data.ReviewDoc
import com.xern.xrnhub.data.SecurityDoc
import com.xern.xrnhub.data.ServiceRow
import com.xern.xrnhub.data.ServicesDoc
import com.xern.xrnhub.data.TrafficDoc
import com.xern.xrnhub.data.docJson
import kotlinx.serialization.KSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The files here are the real hub's own answers: tests/demo_server.py --fixtures writes them. */
class DocsTest {
    private fun <T> read(name: String, serializer: KSerializer<T>): T {
        val text = javaClass.getResource("/$name.json")!!.readText()
        return docJson.decodeFromString(serializer, text)
    }

    @Test fun the_live_answer_reads_in_full() {
        val doc = read("now", NowDoc.serializer())
        assertEquals(10, doc.services.size) // the monitor has watched xrninsta too since 2026-11-15
        assertTrue(doc.services.all { it.up })
        assertNotNull(doc.memory)
        assertNotNull(doc.disk)
        assertEquals(86.0, doc.battery?.percent)
        assertEquals("CHARGING", doc.battery?.status)
        assertEquals(-58, doc.wifi?.rssi)
        assertEquals(433.0, doc.wifi?.linkMbps)
        assertTrue(doc.samples.isNotEmpty())
        assertTrue(doc.services.first { it.name == "calendar" }.restartable)
        assertFalse(doc.services.first { it.name == "xrnhub" }.restartable)
        assertTrue(doc.hour.isNotEmpty())
        assertTrue(doc.feed!!.isNotEmpty())
        assertTrue(doc.feed!!.all { it.path.startsWith("/") && it.status > 0 })
    }

    @Test fun xrnfits_card_comes_with_the_live_answer() {
        val card = read("now", NowDoc.serializer()).health!!
        assertNotNull(card.lastIn)
        assertFalse(card.late)
        assertTrue(card.week!! > 0)
        assertTrue(card.steps!! > 0)
        assertEquals(card.day, card.sleep?.day)
        assertTrue(card.sleep!!.minutes > 0)
        assertTrue(card.resting!!.bpm > 0)
        assertTrue(card.weight!!.kg > 0)
        assertTrue(card.spo2!!.percent > 0)
        assertTrue(card.stress!!.level > 0)
    }

    @Test fun a_live_answer_without_xrnfits_card_still_reads() {
        assertNull(docJson.decodeFromString(NowDoc.serializer(), """{"services":[]}""").health)
    }

    @Test fun rates_arrive_under_their_python_names() {
        val doc = read("now", NowDoc.serializer())
        assertNotNull(doc.speed?.down)
        assertNotNull(doc.speed?.up)
    }

    @Test fun history_keeps_its_holes() {
        val doc = read("history", HistoryDoc.serializer())
        assertTrue(doc.at.isNotEmpty())
        assertEquals(doc.at.size, doc.system.getValue("load1").size)
        assertEquals(doc.at.size, doc.traffic.values.first().reqs.size)
    }

    @Test fun one_service_has_its_own_series() {
        val doc = read("history-service", HistoryDoc.serializer())
        assertEquals(setOf("xrnloops"), doc.services.keys)
        assertTrue(doc.services.getValue("xrnloops").cpu.any { it != null })
    }

    @Test fun services_carry_thirty_days_and_what_went_wrong() {
        val doc = read("services", ServicesDoc.serializer())
        assertEquals(30, doc.uptime.getValue("xrnrule").size)
        assertEquals(30, doc.fitDays.size) // xrnfit's readings a day, in place of an uptime strip
        assertTrue(doc.fitDays.last()!! > 0)
        assertTrue(doc.incidents.any { it.kind == "down" && it.target == "xrnrule" })
    }

    @Test fun traffic_reads_with_its_status_codes_as_keys() {
        val doc = read("traffic", TrafficDoc.serializer())
        assertTrue(doc.totals.isNotEmpty())
        assertTrue(doc.paths.isNotEmpty())
        assertTrue(doc.clients.isNotEmpty())
        assertTrue(doc.status.containsKey("200"))
    }

    @Test fun data_reads_with_in_and_out_under_their_python_names() {
        val doc = read("data", DataDoc.serializer())
        assertFalse(doc.pending)
        assertTrue(doc.apps.isNotEmpty())
        assertEquals(30, doc.daily.days.size)
        assertEquals(30, doc.daily.down.size)
        assertEquals(30, doc.daily.up.size)
        assertTrue(doc.fed.apps.isNotEmpty())
        assertNotNull(doc.network?.down)
    }

    @Test fun data_counts_xrnfits_health_as_its_own() {
        val doc = read("data", DataDoc.serializer())
        val fit = doc.apps.first { it.name == "xrnfit" }
        assertEquals("readings", fit.unit)
        assertTrue(fit.records > 0 && fit.bytes > 0)
        assertTrue("xrnfit" in doc.fed.apps)
    }

    @Test fun a_page_still_counting_says_so() {
        assertTrue(docJson.decodeFromString(DataDoc.serializer(), """{"pending":true}""").pending)
    }

    @Test fun security_reads_devices_and_paths() {
        val doc = read("security", SecurityDoc.serializer())
        assertEquals(listOf("laptop", "old-phone"), doc.tailnet?.devices?.map { it.name })
        assertEquals(2, doc.tailnet?.ingress)
        assertEquals(listOf("/", "/xrnloops", "/xrnhub"), doc.funnel)
        assertTrue(doc.logins.any { !it.ok })
    }

    @Test fun logs_read_with_their_levels() {
        val doc = read("logs", LogsDoc.serializer())
        assertEquals(5, doc.lines.size)
        assertTrue(doc.lines.all { it.level in setOf("info", "warn", "error") })
    }

    @Test fun review_reads_every_card() {
        val doc = read("review", ReviewDoc.serializer())
        assertTrue(doc.year.isNotEmpty())
        assertEquals(4, doc.habits.items.size)
        assertEquals(30, doc.habits.items[0].strip.size)
        assertTrue(doc.hours!!.series.isNotEmpty())
        assertEquals("#1c6fd8", doc.hours!!.series.first { it.name == "Uni" }.color)
        assertEquals(24, doc.temptations.hours.size)
        assertNotNull(doc.trend)
    }

    @Test fun review_reads_the_health_card_on_the_habit_strips_days() {
        val doc = read("review", ReviewDoc.serializer())
        val body = doc.health!!
        assertEquals(30, body.days.size)
        assertEquals(doc.habits.first, body.first)
        assertEquals(setOf("steps", "asleep", "resting", "weightKg", "spo2", "stress"), body.summary.keys)
        assertTrue(body.days.any { it.value("steps") != null && it.value("spo2") != null })
        assertNotNull(body.summary.getValue("weightKg").avg)
    }

    @Test fun a_day_reads_with_its_calendar_and_log() {
        val doc = read("day", DayDoc.serializer())
        assertEquals(4, doc.habits.items.size)
        assertTrue(doc.calendar.items.isNotEmpty())
        assertTrue(doc.log.isNotEmpty())
    }

    @Test fun a_day_reads_with_its_health() {
        val health = read("day", DayDoc.serializer()).health!!
        assertNotNull(health.steps)
        val sleep = health.sleep!!
        assertTrue(sleep.minutes > 0 && sleep.end > sleep.start)
        assertEquals(sleep.minutes, sleep.deep + sleep.light + sleep.rem + sleep.asleep, 1.0)
        assertNotNull(health.heartRate?.resting)
        val spo2 = health.spo2!!
        assertTrue(spo2.min <= spo2.avg && spo2.avg <= spo2.max)
        assertNull(docJson.decodeFromString(DayDoc.serializer(), """{"day":"2026-11-15","health":null}""").health)
    }

    @Test fun a_reading_the_phone_could_not_take_comes_through_as_null() {
        val doc = docJson.decodeFromString(NowDoc.serializer(), """{"memory":null,"disk":null,"wifi":null,"services":[]}""")
        assertNull(doc.memory)
        assertNull(doc.wifi)
    }

    @Test fun a_field_added_later_does_no_harm() {
        val row = docJson.decodeFromString(ServiceRow.serializer(), """{"name":"x","state":"run","brandNew":{"a":1}}""")
        assertEquals("x", row.name)
    }

    @Test fun running_is_not_enough_to_count_as_up() {
        assertTrue(ServiceRow("x", state = "run", answers = true).up)
        assertTrue(ServiceRow("x", state = "run", answers = null).up)
        assertFalse(ServiceRow("x", state = "run", answers = false).up)
        assertFalse(ServiceRow("x", state = "down").up)
    }

    @Test fun the_live_feed_names_each_caller() {
        val feed = read("now", NowDoc.serializer()).feed!!
        assertTrue(feed.all { it.ip.isNotEmpty() })
    }

    @Test fun services_carry_thirty_days_of_incidents_for_the_timeline() {
        val doc = read("services", ServicesDoc.serializer())
        val timeline = doc.timeline!!
        assertTrue(timeline.size >= doc.incidents.size)
        assertTrue(timeline.any { it.kind == "restart" && it.ended == it.started })
        assertTrue(timeline.any { it.kind == "down" && it.ended!! > it.started })
        // A gap is the monitor's, named or not.
        assertTrue(timeline.filter { it.kind == "gap" }.all { it.target in setOf("", "monitor") })
        // A hub from before the timeline still reads.
        assertNull(docJson.decodeFromString(ServicesDoc.serializer(), """{"uptime":{},"incidents":[]}""").timeline)
    }

    @Test fun log_stats_count_lines_by_level_and_service_on_one_set_of_buckets() {
        val doc = read("logstats", LogStatsDoc.serializer())
        assertTrue(doc.at.isNotEmpty())
        assertEquals(setOf("info", "warn", "error"), doc.levels.keys)
        assertTrue(doc.levels.values.all { it.size == doc.at.size })
        assertTrue(doc.services.values.all { lv -> lv.values.all { it.size == doc.at.size } })
        // Each level's total is its services' together.
        for (level in doc.levels.keys) {
            assertEquals(doc.levels.getValue(level).sum(), doc.services.values.sumOf { it.getValue(level).sum() }, 0.0)
        }
        val group = doc.groups.first()
        assertEquals("error", group.level) // errors come first
        assertTrue(group.count > 0 && group.last > 1e12 && group.sig.isNotEmpty())
    }

    @Test fun one_services_log_stats_are_its_own() {
        val doc = read("logstats-service", LogStatsDoc.serializer())
        assertEquals(setOf("xrnloops"), doc.services.keys)
        assertEquals(3600L, doc.bucket)
        assertTrue(doc.groups.all { it.service == "xrnloops" })
    }

    @Test fun logs_without_routine_leave_out_answered_requests() {
        val lines = read("logs-quiet", LogsDoc.serializer()).lines
        assertTrue(lines.isNotEmpty())
        assertTrue(lines.none { Regex("\" HTTP/[0-9.]+\" [23][0-9][0-9] ").containsMatchIn(it.text) })
    }
}

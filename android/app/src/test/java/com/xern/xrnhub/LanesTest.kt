package com.xern.xrnhub

import com.xern.xrnhub.data.FeedRow
import com.xern.xrnhub.data.Incident
import com.xern.xrnhub.data.LogGroup
import com.xern.xrnhub.data.LogStatsDoc
import com.xern.xrnhub.data.dayOf
import com.xern.xrnhub.data.feedLanes
import com.xern.xrnhub.data.incidentLanes
import com.xern.xrnhub.data.kindOf
import com.xern.xrnhub.data.levelValues
import com.xern.xrnhub.data.messageWords
import com.xern.xrnhub.data.signalBars
import com.xern.xrnhub.data.statusClasses
import com.xern.xrnhub.data.stripDays
import com.xern.xrnhub.data.upDays
import com.xern.xrnhub.data.uptimeShare
import java.time.LocalDate
import java.time.ZoneId
import java.time.ZonedDateTime
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class LanesTest {
    private val zone = ZoneId.of("Australia/Sydney")

    @Test fun each_kind_of_incident_has_its_mark() {
        assertEquals("down", kindOf(Incident("down", "xrnrule")))
        assertEquals("restart", kindOf(Incident("restart", "xrnrule")))
        assertEquals("restart", kindOf(Incident("restarted", "xrnrule")))
        assertEquals("reboot", kindOf(Incident("reboot")))
        assertEquals("gap", kindOf(Incident("gap")))
        assertEquals("problem", kindOf(Incident("battery")))
    }

    @Test fun incidents_go_in_lanes_services_first_then_the_phone_and_the_monitor() {
        val lanes = incidentLanes(
            listOf(
                Incident("gap", "", 100.0, 200.0), Incident("down", "xrnrule", 300.0, 900.0), Incident("reboot", "", 400.0, 450.0),
                Incident("restart", "calendar", 500.0, 500.0), Incident("down", "xrnrule", 1000.0, null), Incident("gap", "monitor", 50.0, 60.0),
            ),
            listOf("xrnloops", "xrnrule", "calendar"),
        )
        assertEquals(listOf("xrnrule", "calendar", null, null), lanes.map { it.name })
        assertEquals(listOf(null, null, "Phone", "Monitor"), lanes.map { it.place })
        assertEquals(listOf("2", "1", "1", "2"), lanes.map { it.figure })
        val outage = lanes[0].marks
        assertEquals(listOf(900.0, null), outage.map { it.to })
        assertEquals("xrnrule down", outage[0].title)
        assertEquals("Ongoing", outage[1].words.single().substringAfter(" · "))
    }

    @Test fun the_last_minutes_are_a_lane_per_service_with_a_mark_per_request() {
        val now = 1_800_000_000_000.0
        val feed = listOf(
            FeedRow(now - 60_000, "xrnrule", "GET", "/sync", 200, "203.0.113.9"),
            FeedRow(now - 30_000, "xrnloops", "POST", "/changes", 401, "198.51.100.7"),
            FeedRow(now - 20_000, "xrnloops", "GET", "/changes", 304, poll = true),
            FeedRow(now - 20 * 60_000, "xrnrule", "GET", "/old", 500),
        )
        val lanes = feedLanes(feed, now, withTimers = false)
        assertEquals(listOf("xrnloops", "xrnrule"), lanes.map { it.name })
        assertEquals(listOf("s4"), lanes[0].marks.map { it.kind })
        assertEquals("POST /changes", lanes[0].marks[0].title)
        assertEquals("198.51.100.7", lanes[0].marks[0].words.last())
        assertEquals(listOf("s4", "s3"), feedLanes(feed, now, withTimers = true)[0].marks.map { it.kind })
    }

    @Test fun answers_split_into_status_classes_in_order() {
        assertEquals(
            listOf("2" to 80.0, "3" to 15.0, "5" to 1.0),
            statusClasses(mapOf("204" to 20.0, "503" to 1.0, "200" to 60.0, "304" to 15.0, "" to 9.0)),
        )
        assertEquals(emptyList<Pair<String, Double>>(), statusClasses(emptyMap()))
    }

    @Test fun the_strips_days_run_from_a_month_ago_to_tonight() {
        val (from, to) = stripDays(30, LocalDate.of(2026, 11, 19), zone)
        assertEquals(ZonedDateTime.of(2026, 10, 21, 0, 0, 0, 0, zone).toEpochSecond().toDouble(), from, 0.0)
        assertEquals(ZonedDateTime.of(2026, 11, 20, 0, 0, 0, 0, zone).toEpochSecond().toDouble(), to, 0.0)
    }

    @Test fun uptime_reads_as_a_share_of_the_days_watched() {
        assertNull(uptimeShare(listOf(null, null)))
        assertEquals("100", uptimeShare(listOf(null, 1.0, 0.9996)))
        assertEquals("99.5", uptimeShare(listOf(1.0, 0.99)))
    }

    @Test fun the_phones_days_up_fill_from_the_end() {
        assertEquals(listOf(false, false, true, true), upDays(1.5 * 86400, 4))
        assertEquals(List(4) { true }, upDays(90 * 86400.0, 4))
    }

    @Test fun wifi_reads_as_bars() {
        assertEquals(0, signalBars(null))
        assertEquals(4, signalBars(-50))
        assertEquals(3, signalBars(-60))
        assertEquals(2, signalBars(-70))
        assertEquals(1, signalBars(-85))
    }

    @Test fun a_refused_request_reads_as_its_signature_anything_else_as_its_line() {
        val refused = LogGroup("xrntube", "warn", "\"GET /state\" 404", text = "198.51.100.200 \"GET /state HTTP/1.1\" 404 -")
        assertEquals("\"GET /state\" 404", messageWords(refused))
        assertEquals("error: upstream timed out", messageWords(LogGroup(sig = "error: upstream", text = "error: upstream timed out")))
    }

    @Test fun a_services_levels_fill_its_buckets_with_zeros() {
        val stats = LogStatsDoc(at = listOf(0, 60, 120))
        assertEquals(listOf(1.0, 0.0, 2.0), levelValues(stats, mapOf("error" to listOf(1.0, 0.0, 2.0)), "error"))
        assertEquals(listOf(0.0, 0.0, 0.0), levelValues(stats, null, "warn"))
    }

    @Test fun a_start_reads_as_its_date_alone_however_recent() {
        val recent = ZonedDateTime.of(2026, 11, 16, 0, 22, 0, 0, zone).toEpochSecond().toDouble()
        val older = ZonedDateTime.of(2026, 11, 10, 9, 5, 0, 0, zone).toEpochSecond().toDouble()
        assertEquals("16 Nov", dayOf(recent, zone))
        assertEquals("10 Nov", dayOf(older, zone))
    }
}

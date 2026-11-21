package com.xern.xrnhub

import com.xern.xrnhub.data.DASH
import com.xern.xrnhub.data.DataDoc
import com.xern.xrnhub.data.DayLog
import com.xern.xrnhub.data.FitCard
import com.xern.xrnhub.data.Incident
import com.xern.xrnhub.data.Runway
import com.xern.xrnhub.data.bytes
import com.xern.xrnhub.data.degrees
import com.xern.xrnhub.data.duration
import com.xern.xrnhub.data.besideHub
import com.xern.xrnhub.data.fitAgo
import com.xern.xrnhub.data.incidentWords
import com.xern.xrnhub.data.logText
import com.xern.xrnhub.data.moment
import com.xern.xrnhub.data.perSecond
import com.xern.xrnhub.data.percent
import com.xern.xrnhub.data.problemWords
import com.xern.xrnhub.data.runwayWords
import com.xern.xrnhub.data.sentDay
import com.xern.xrnhub.data.serviceName
import com.xern.xrnhub.data.timeLabel
import com.xern.xrnhub.data.tipTime
import com.xern.xrnhub.data.weekChange
import com.xern.xrnhub.data.wifiWord
import java.time.ZoneId
import java.time.ZonedDateTime
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Test

class WordsTest {
    private val zone = ZoneId.of("Australia/Sydney")
    private val now = ZonedDateTime.of(2026, 11, 6, 15, 30, 0, 0, zone)
    private fun ago(seconds: Long) = (now.toEpochSecond() - seconds).toDouble()

    @Test fun bytes_read_like_the_web_page() {
        assertEquals(DASH, bytes(null))
        assertEquals("0 B", bytes(0.0))
        assertEquals("512 B", bytes(512.0))
        assertEquals("1 KB", bytes(1024.0))
        assertEquals("1.5 KB", bytes(1536.0))
        assertEquals("44.1 MB", bytes(44.1 * 1024 * 1024))
        assertEquals("203 GB", bytes(203.0 * 1024 * 1024 * 1024))
        assertEquals("−2 KB", bytes(-2048.0))
    }

    @Test fun rates_and_percents_keep_a_digit_only_when_small() {
        assertEquals("1.5 KB/s", perSecond(1536.0))
        assertEquals(DASH, perSecond(null))
        assertEquals("5.3%", percent(5.3315))
        assertEquals("86%", percent(86.4))
        assertEquals(DASH, percent(null))
        assertEquals("28°", degrees(28.4))
    }

    @Test fun duration_picks_the_largest_unit_that_reads_well() {
        assertEquals("45s", duration(45.0))
        assertEquals("12m", duration(720.0))
        assertEquals("2h", duration(7200.0))
        assertEquals("2h 5m", duration(7500.0))
        assertEquals("1 day", duration(90000.0))
        assertEquals("22 days", duration(22 * 86400.0))
    }

    @Test fun a_moment_says_only_what_it_needs_to() {
        assertEquals("14:30", moment(ago(3600), now))
        assertEquals("4 Nov 15:30", moment(ago(2 * 86400), now))
        assertEquals("10 Oct", moment(ago(27 * 86400), now))
    }

    @Test fun chart_times_grow_with_the_span() {
        val at = now.toInstant().toEpochMilli()
        assertEquals("15:30", timeLabel(at, 86_400_000, zone))
        assertEquals("6 Nov", timeLabel(at, 7 * 86_400_000L, zone))
        assertEquals("15:30", tipTime(at, 3_600_000, zone))
        assertEquals("6 Nov 15:30", tipTime(at, 7 * 86_400_000L, zone))
        assertEquals("6 Nov", tipTime(at, 30 * 86_400_000L, zone))
    }

    @Test fun radicale_goes_by_xrncal_on_screen() {
        assertEquals("xrncal", serviceName("calendar"))
        assertEquals("Tailscale", serviceName("tailscale"))
        assertEquals("xrnloops", serviceName("xrnloops"))
        assertEquals("xrncal is down", problemWords(Incident("down", "calendar")))
        assertEquals("xrncal restarted", incidentWords(Incident("restart", "calendar")))
        assertEquals("Restart of xrnrule failed", incidentWords(Incident("restarted", "xrnrule", detail = "failed")))
        assertEquals("Phone rebooted", incidentWords(Incident("reboot")))
    }

    @Test fun an_unknown_kind_still_says_something() {
        assertEquals("flood", problemWords(Incident("flood")))
        assertEquals("flood", incidentWords(Incident("flood")))
    }

    @Test fun wifi_words_follow_the_signal() {
        assertEquals("Strong", wifiWord(-52))
        assertEquals("Good", wifiWord(-70))
        assertEquals("Weak", wifiWord(-80))
    }

    @Test fun the_disk_runway_reads_in_years_once_it_can() {
        assertEquals(DASH, runwayWords(DataDoc()))
        assertEquals("Not growing", runwayWords(DataDoc(runway = Runway(0.0, null))))
        assertEquals("120 days", runwayWords(DataDoc(runway = Runway(1.0, 120.0))))
        assertEquals("2.5 years", runwayWords(DataDoc(runway = Runway(1.0, 912.5))))
        assertEquals("27 years", runwayWords(DataDoc(runway = Runway(1.0, 9900.0))))
    }

    @Test fun a_weeks_change_is_an_arrow_and_a_figure() {
        assertEquals("▲ 5 pts", weekChange(74, 69, "pts"))
        assertEquals("▼ 3", weekChange(9, 12))
        assertEquals("±0 pts", weekChange(70, 70, "pts"))
        assertEquals("±0", weekChange(4, 4))
    }

    @Test fun a_day_log_reads_as_a_sentence() {
        assertEquals("Stopped Game", logText(DayLog(kind = "blocked", title = "Game")))
        assertEquals("Unlocked Game for 15m", logText(DayLog(kind = "unlock", title = "Game", value = JsonPrimitive(15))))
        assertEquals("Unlocked Game", logText(DayLog(kind = "unlock", title = "Game")))
        assertEquals("Spent", logText(DayLog(kind = "spent")))
    }

    @Test fun xrnfits_row_says_when_it_last_sent() {
        val seconds = now.toEpochSecond().toDouble()
        assertEquals(DASH, fitAgo(FitCard(), seconds))
        assertEquals("12m ago", fitAgo(FitCard(lastIn = ago(720)), seconds))
    }

    @Test fun xrnfit_is_listed_right_after_the_hub_that_keeps_its_data() {
        val names = listOf("calendar", "xrnhub", "xrninsta", "ssh")
        assertEquals(listOf("calendar", "xrnhub", "xrnfit", "xrninsta", "ssh"), besideHub(names, names, "xrnfit"))
        assertEquals(names, besideHub(names, names, null))
        // With no hub listed it goes last rather than nowhere.
        assertEquals(listOf("ssh", "xrnfit"), besideHub(listOf("ssh"), listOf("ssh"), "xrnfit"))
    }

    @Test fun a_day_on_xrnfits_strip_is_green_with_readings_red_without_and_grey_before_the_first() {
        assertEquals(null to "Nothing yet", sentDay(null))
        assertEquals(0.0 to "Nothing sent", sentDay(0.0))
        assertEquals(1.0 to "1 reading", sentDay(1.0))
        assertEquals(1.0 to "4,517 readings", sentDay(4517.0))
    }
}

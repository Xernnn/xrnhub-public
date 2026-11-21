package com.xern.xrnhub

import com.xern.xrnhub.data.AlertBook
import com.xern.xrnhub.data.Incident
import com.xern.xrnhub.data.KEY_BLOCKED
import com.xern.xrnhub.data.KEY_CUT_OFF
import com.xern.xrnhub.data.KEY_QUIET
import com.xern.xrnhub.data.NowDoc
import com.xern.xrnhub.data.alertWords
import com.xern.xrnhub.data.heardFrom
import com.xern.xrnhub.data.notAsked
import com.xern.xrnhub.data.notHeard
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class AlertsTest {
    private val now = 1_800_000_000_000L
    private val rule = Incident("down", "xrnrule")
    private val cable = Incident("unplugged", "phone", detail = "64%")

    private fun doc(vararg problems: Incident, at: Long = now - 30_000) = NowDoc(at = at, problems = problems.toList())

    @Test fun nothing_wrong_says_nothing() {
        val turn = heardFrom(AlertBook(), doc(), now)
        assertEquals(emptyList<Any>(), turn.alerts)
        assertEquals(now, turn.book.heardAt)
    }

    @Test fun a_new_problem_is_said_out_loud_with_its_detail() {
        val alert = heardFrom(AlertBook(), doc(cable), now).alerts.single()
        assertEquals("Server unplugged", alert.title)
        assertEquals("64%", alert.text)
        assertTrue(alert.loud)
    }

    @Test fun a_problem_still_open_is_not_said_again() {
        val first = heardFrom(AlertBook(), doc(rule), now)
        val later = now + 900_000
        assertEquals(emptyList<Any>(), heardFrom(first.book, doc(rule, at = later - 30_000), later).alerts)
    }

    @Test fun a_second_problem_is_said_without_repeating_the_first() {
        val first = heardFrom(AlertBook(), doc(rule), now)
        assertEquals(listOf("Server unplugged"), heardFrom(first.book, doc(rule, cable), now).alerts.map { it.title })
    }

    @Test fun when_a_problem_ends_its_note_is_replaced_quietly() {
        val first = heardFrom(AlertBook(), doc(rule), now)
        val alert = heardFrom(first.book, doc(), now).alerts.single()
        assertEquals("xrnrule is back", alert.title)
        assertEquals(first.alerts.single().key, alert.key)
        assertFalse(alert.loud)
    }

    @Test fun radicale_goes_by_xrncal_in_both_directions() {
        val first = heardFrom(AlertBook(), doc(Incident("down", "calendar")), now)
        assertEquals("xrncal is down", first.alerts.single().title)
        assertEquals("xrncal is back", heardFrom(first.book, doc(), now).alerts.single().title)
    }

    @Test fun the_same_problem_coming_back_is_said_again() {
        val down = heardFrom(AlertBook(), doc(rule), now)
        val up = heardFrom(down.book, doc(), now)
        assertTrue(heardFrom(up.book, doc(rule), now).alerts.single().loud)
    }

    @Test fun anything_about_the_server_says_so_since_it_lands_on_another_phone() {
        listOf("unplugged", "battery", "hot", "disk", "supervisor").forEach {
            assertTrue(it, alertWords(Incident(it, "phone")).startsWith("Server"))
        }
    }

    @Test fun supervision_problems_have_words_of_their_own() {
        assertEquals("Server: nothing restarts its services", alertWords(Incident("supervisor", "phone")))
        assertEquals("Server: more than one runsvdir", alertWords(Incident("supervisor", "phone", detail = "2 running")))
        assertEquals("xrnloops is running unsupervised", alertWords(Incident("unsupervised", "xrnloops", detail = "pid 8474")))
        assertEquals("xrncal is supervised again", heardFrom(heardFrom(AlertBook(), doc(Incident("unsupervised", "calendar")), now).book, doc(), now).alerts.single().title)
    }

    @Test fun one_missed_check_is_not_an_outage() {
        val turn = notHeard(AlertBook(heardAt = now))
        assertEquals(emptyList<Any>(), turn.alerts)
        assertEquals(1, turn.book.misses)
    }

    @Test fun two_missed_checks_are() {
        val second = notHeard(notHeard(AlertBook(heardAt = now)).book)
        val alert = second.alerts.single()
        assertEquals("Can't reach the server", alert.title)
        assertTrue(alert.text, alert.text.startsWith("Last heard from "))
        assertTrue(alert.loud)
    }

    @Test fun an_outage_is_said_once_however_long_it_lasts() {
        var book = notHeard(notHeard(AlertBook()).book).book
        repeat(5) {
            val turn = notHeard(book)
            assertEquals(emptyList<Any>(), turn.alerts)
            book = turn.book
        }
    }

    @Test fun never_having_heard_from_it_gives_no_time() {
        assertEquals("", notHeard(notHeard(AlertBook()).book).alerts.single().text)
    }

    @Test fun getting_through_again_closes_the_outage() {
        val cut = notHeard(notHeard(AlertBook(heardAt = now)).book)
        val turn = heardFrom(cut.book, doc(), now)
        val alert = turn.alerts.single()
        assertEquals("Server is back", alert.title)
        assertEquals(KEY_CUT_OFF, alert.key)
        assertFalse(alert.loud)
        assertEquals(0, turn.book.misses)
    }

    @Test fun an_outage_does_not_end_the_problems_already_open() {
        val first = heardFrom(AlertBook(), doc(rule), now)
        val cut = notHeard(notHeard(first.book).book)
        assertEquals(listOf("Server is back"), heardFrom(cut.book, doc(rule), now).alerts.map { it.title })
    }

    @Test fun a_blink_between_checks_leaves_no_trace() {
        val miss = notHeard(AlertBook(heardAt = now))
        val turn = heardFrom(miss.book, doc(), now)
        assertEquals(emptyList<Any>(), turn.alerts)
        assertEquals(0, turn.book.misses)
    }

    @Test fun a_server_answering_with_old_readings_has_a_stuck_monitor() {
        val turn = heardFrom(AlertBook(), doc(at = now - 6 * 60_000), now)
        assertEquals(KEY_QUIET, turn.alerts.single().key)
        assertEquals("Monitor is back", heardFrom(turn.book, doc(), now).alerts.single().title)
    }

    @Test fun a_phone_clock_ahead_of_the_servers_does_not_quieten_the_monitor() {
        val serverNow = now - 10 * 60_000 // the server's clock is ten minutes behind this phone's
        assertEquals(emptyList<Any>(), heardFrom(AlertBook(), doc(at = serverNow - 30_000), now, serverNow).alerts)
        val stuck = heardFrom(AlertBook(), doc(at = serverNow - 6 * 60_000), now, serverNow)
        assertEquals(KEY_QUIET, stuck.alerts.single().key)
        assertEquals(now, stuck.book.heardAt) // the phone's own gaps stay on the phone's clock
    }

    @Test fun android_blocking_a_check_says_nothing_about_the_server() {
        val book = AlertBook(open = listOf("down|xrnrule"), misses = 1, heardAt = now)
        val turn = notAsked(book, now + 3_600_000)
        assertEquals(emptyList<Any>(), turn.alerts)
        assertEquals(book, turn.book)
    }

    @Test fun being_blocked_for_hours_is_said_once_and_ends_when_a_check_gets_through() {
        val later = now + 7 * 3_600_000
        val first = notAsked(AlertBook(heardAt = now), later)
        assertEquals(KEY_BLOCKED, first.alerts.single().key)
        assertEquals(emptyList<Any>(), notAsked(first.book, later + 900_000).alerts)
        val back = heardFrom(first.book, doc(at = later - 30_000), later).alerts.single()
        assertEquals("Server checks are back", back.title)
        assertFalse(back.loud)
    }

    @Test fun never_having_got_through_is_not_a_long_gap() {
        assertEquals(emptyList<Any>(), notAsked(AlertBook(), now).alerts)
    }

    @Test fun a_session_old_enough_gets_renewed() {
        val week = 7 * 86_400_000L
        assertFalse(com.xern.xrnhub.data.Session("a", "b", "c", now - week + 1).isOld(now))
        assertTrue(com.xern.xrnhub.data.Session("a", "b", "c", now - week).isOld(now))
    }
}

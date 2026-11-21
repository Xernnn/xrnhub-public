package com.xern.xrnhub

import com.xern.xrnhub.data.HistoryDoc
import com.xern.xrnhub.data.Point
import com.xern.xrnhub.data.RING_SIZE
import com.xern.xrnhub.data.TrafficSeries
import com.xern.xrnhub.data.bucketValues
import com.xern.xrnhub.data.gaugeShare
import com.xern.xrnhub.data.levelOf
import com.xern.xrnhub.data.merged
import com.xern.xrnhub.data.niceTop
import com.xern.xrnhub.data.niceTopBytes
import com.xern.xrnhub.data.segments
import com.xern.xrnhub.data.stackTop
import com.xern.xrnhub.data.summed
import org.junit.Assert.assertEquals
import org.junit.Test

class SeriesTest {
    @Test fun a_gauge_with_nothing_to_measure_is_empty() {
        assertEquals(0f, gaugeShare(0.0 / 0.0), 0f) // a total of zero
        assertEquals(0f, gaugeShare(5.0 / 0.0), 0f)
        assertEquals(0f, gaugeShare(-0.2), 0f)
        assertEquals(0.4f, gaugeShare(0.4), 1e-6f)
        assertEquals(1f, gaugeShare(1.3), 0f)
    }

    @Test fun the_top_of_a_chart_is_a_round_number_above_the_highest() {
        assertEquals(1.0, niceTop(0.0), 0.0)
        assertEquals(1.2, niceTop(1.0), 1e-9)
        assertEquals(50.0, niceTop(46.0), 1e-9)
        assertEquals(200.0, niceTop(190.0), 1e-9)
    }

    @Test fun a_byte_axis_is_round_in_the_unit_it_reads_in() {
        assertEquals(1.0, niceTopBytes(0.0), 0.0)
        assertEquals(150 * 1024.0, niceTopBytes(146 * 1024.0), 1e-6)
        assertEquals(50 * 1024.0 * 1024, niceTopBytes(46 * 1024.0 * 1024), 1e-6)
        assertEquals(1.2 * 1024, niceTopBytes(1024.0), 1e-6)
        assertEquals(800.0, niceTopBytes(700.0), 1e-6)
    }

    @Test fun a_missing_value_splits_a_line() {
        val at = listOf(0L, 10, 20, 30, 40)
        val values = listOf(1.0, 2.0, null, 4.0, 5.0)
        assertEquals(listOf(listOf(0, 1), listOf(3, 4)), segments(at, values, null))
    }

    @Test fun a_long_pause_splits_a_line() {
        val at = listOf(0L, 10, 20, 30, 300, 310)
        val values = at.map { 1.0 }
        assertEquals(listOf(listOf(0, 1, 2, 3), listOf(4, 5)), segments(at, values, null))
    }

    @Test fun a_stated_gap_beats_the_usual_step() {
        val at = listOf(0L, 60_000, 63_000, 66_000)
        assertEquals(listOf(listOf(0, 1, 2, 3)), segments(at, at.map { 1.0 }, 150_000))
    }

    @Test fun a_lone_point_is_its_own_run() {
        assertEquals(listOf(listOf(0)), segments(listOf(5L), listOf(1.0), null))
    }

    @Test fun nothing_measured_draws_nothing() {
        assertEquals(emptyList<List<Int>>(), segments(emptyList(), emptyList(), null))
        assertEquals(emptyList<List<Int>>(), segments(listOf(0L, 1), listOf(null, null), null))
    }

    @Test fun new_samples_join_the_ring_and_the_oldest_fall_off() {
        val ring = List(RING_SIZE) { Point(it.toLong()) }
        val after = merged(ring, listOf(Point(9999)), fresh = false)
        assertEquals(RING_SIZE, after.size)
        assertEquals(1L, after.first().at)
        assertEquals(9999L, after.last().at)
    }

    @Test fun a_fresh_look_replaces_a_stale_ring() {
        val after = merged(listOf(Point(1)), listOf(Point(5), Point(6)), fresh = true)
        assertEquals(listOf(5L, 6L), after.map { it.at })
    }

    @Test fun totals_become_rates_over_each_bucket() {
        val doc = HistoryDoc(bucket = 60, at = listOf(0, 60), system = mapOf("net_in" to listOf(6000.0, null)))
        assertEquals(listOf(100.0, null), bucketValues(doc, "net_in", perBucket = true, nowMs = 90_000))
    }

    @Test fun the_newest_bucket_is_divided_by_what_has_passed() {
        val doc = HistoryDoc(bucket = 60, at = listOf(0, 60), system = mapOf("net_in" to listOf(6000.0, 300.0)))
        assertEquals(10.0, bucketValues(doc, "net_in", perBucket = true, nowMs = 90_000)[1]!!, 1e-9)
    }

    @Test fun gauges_stay_as_they_are() {
        val doc = HistoryDoc(bucket = 60, at = listOf(0), system = mapOf("load1" to listOf(0.5)))
        assertEquals(listOf(0.5), bucketValues(doc, "load1", perBucket = false, nowMs = 90_000))
    }

    @Test fun a_day_shades_by_how_much_got_done() {
        assertEquals(0, levelOf(null))
        assertEquals(1, levelOf(0.0))
        assertEquals(2, levelOf(0.3))
        assertEquals(4, levelOf(0.8))
        assertEquals(4, levelOf(1.0))
    }

    @Test fun a_tiles_spark_sums_every_service_a_bucket_at_a_time() {
        val doc = HistoryDoc(
            at = listOf(0, 60, 120),
            traffic = mapOf(
                "xrnloops" to TrafficSeries(reqs = listOf(3.0, null, 1.0), writes = listOf(1.0, null, null)),
                "xrnrule" to TrafficSeries(reqs = listOf(2.0, null, 4.0)),
            ),
        )
        assertEquals(listOf(5.0, null, 5.0), summed(doc) { it.reqs })
        // A service that sent no column at all is no number, not a zero.
        assertEquals(listOf(1.0, null, null), summed(doc) { it.writes })
        assertEquals(emptyList<Double?>(), summed(HistoryDoc()) { it.reqs })
    }

    @Test fun a_chart_of_counts_halves_into_whole_numbers() {
        assertEquals(2.0, stackTop(listOf(0.0, 1.0)), 0.0)
        assertEquals(30.0, stackTop(listOf(3.0, 24.0)), 0.0) // niceTop says 25, which halves to 12.5
        assertEquals(400.0, stackTop(listOf(120.0, 350.0)), 0.0)
        assertEquals(1.2, stackTop(listOf(0.4, 1.1)), 1e-9) // not counts: any round number
        assertEquals(2.0, stackTop(emptyList()), 0.0)
    }
}

package com.xern.xrnhub.data

import kotlin.math.floor
import kotlin.math.log10
import kotlin.math.pow

// The arithmetic behind the charts, apart from any drawing.

const val RING_SIZE = 240
const val LIVE_WINDOW_MS = 10 * 60_000L
const val LIVE_GAP_MS = 150_000L // points may be a minute apart until someone's watching
private const val GAP_STEPS = 3.5 // this many usual steps with no sample is a hole, not a slope
private val NICE = listOf(1.0, 1.2, 1.5, 2.0, 2.5, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)

private const val KIBI = 1024.0

/** How full a gauge is drawn. A share of a zero total is NaN, which would fail the layout: it's empty. */
fun gaugeShare(share: Double): Float = if (share.isFinite()) share.coerceIn(0.0, 1.0).toFloat() else 0f

fun niceTop(value: Double): Double {
    if (value <= 0 || !value.isFinite()) return 1.0
    val base = 10.0.pow(floor(log10(value)))
    return (NICE.firstOrNull { it * base >= value * 1.02 } ?: 10.0) * base
}

/** The same in bytes, which count in 1024s: 146 KB/s on an axis should read 150, not 149.8. */
fun niceTopBytes(value: Double): Double {
    if (value <= 0 || !value.isFinite()) return 1.0
    var unit = 1.0
    while (value / unit >= KIBI) unit *= KIBI
    return niceTop(value / unit) * unit
}

/**
 * The top of a stacked chart. Counts read as whole numbers on the axis too: 0, 1, 2 rather than 0,
 * 0.6, 1.2, and 30 over 15 rather than 25 over 12.5, so a column of counts has a top that halves evenly.
 */
fun stackTop(totals: List<Double>, inBytes: Boolean = false): Double {
    val highest = totals.maxOrNull()?.coerceAtLeast(0.0) ?: 0.0
    if (inBytes) return niceTopBytes(highest)
    val whole = totals.all { it == kotlin.math.floor(it) }
    var top = maxOf(niceTop(highest), if (whole) 2.0 else 0.0)
    while (whole && (top / 2) != kotlin.math.floor(top / 2)) top = niceTop(top)
    return top
}

/** The runs of points a line joins: a missing value or a long pause leaves a hole. */
fun segments(at: List<Long>, values: List<Double?>, gap: Long?): List<List<Int>> {
    val steps = at.zipWithNext { a, b -> b - a }.sorted()
    val usual = gap ?: ((steps.getOrElse(steps.size / 2) { 0 }) * GAP_STEPS).toLong()
    val found = mutableListOf<MutableList<Int>>()
    var run: MutableList<Int>? = null
    for (i in values.indices) {
        if (values[i] == null || i >= at.size) {
            run = null
            continue
        }
        val last = run?.last()
        if (last != null && usual > 0 && at[i] - at[last] > usual) run = null
        if (run == null) run = mutableListOf<Int>().also(found::add)
        run.add(i)
    }
    return found
}

/** Live samples: adds what's new, keeps the newest, and starts over after a long look away. */
fun merged(ring: List<Point>, samples: List<Point>, fresh: Boolean): List<Point> =
    (if (fresh) samples else ring + samples).takeLast(RING_SIZE)

/** A value in each of `history`'s buckets. `perBucket` columns are totals, so they become rates. */
fun bucketValues(doc: HistoryDoc, column: String, perBucket: Boolean, nowMs: Long): List<Double?> {
    val raw = doc.system[column].orEmpty()
    if (!perBucket) return raw
    return raw.mapIndexed { i, value ->
        // The newest bucket is still filling: over its whole length it would read as a sudden drop.
        val newest = i == doc.at.lastIndex
        val seconds = if (newest) maxOf((nowMs / 1000.0) - doc.at[i], 1.0) else doc.bucket.toDouble()
        value?.div(seconds)
    }
}

/** One traffic column summed over every service, a bucket at a time; null where none has a number. */
fun summed(doc: HistoryDoc, column: (TrafficSeries) -> List<Double?>): List<Double?> = doc.at.indices.map { i ->
    val found = doc.traffic.values.mapNotNull { column(it).getOrNull(i) }
    if (found.isEmpty()) null else found.sum()
}

/** What share of the way from low to high each cell of a day's habits sits, 0 for nothing logged. */
fun levelOf(share: Double?): Int = if (share == null) 0 else 1 + minOf(3, floor(share * 4).toInt())

/** The next round number above the busiest week. */
fun niceHours(value: Double): Double {
    if (value <= 0) return 10.0
    val base = 10.0.pow(floor(log10(value)))
    return (listOf(1.0, 2.0, 2.5, 5.0, 10.0).firstOrNull { value <= it * base } ?: 10.0) * base
}

package com.xern.xrnhub.data

import kotlinx.serialization.Serializable

const val KEY_CUT_OFF = "cutoff|"
const val KEY_QUIET = "quiet|"
const val KEY_SIGNED_OUT = "signedout|"
const val KEY_BLOCKED = "blocked|"

private const val MISSES_BEFORE_ALERT = 2 // one failure is often just the network blinking
private const val GONE_QUIET_MS = 5 * 60_000L
private const val BLOCKED_TOO_LONG_MS = 6 * 3_600_000L

/** What the phone has already said about the server, kept between checks. */
@Serializable
data class AlertBook(val open: List<String> = emptyList(), val misses: Int = 0, val heardAt: Long = 0)

/** One notification to post, or to replace if its key is already showing. */
data class Alert(val key: String, val title: String, val text: String, val loud: Boolean)

class AlertTurn(val book: AlertBook, val alerts: List<Alert>)

private fun keyOf(i: Incident) = "${i.kind}|${i.target}"

private fun clearedAlert(key: String): Alert {
    val title = when (key) {
        KEY_CUT_OFF -> "Server is back"
        KEY_QUIET -> "Monitor is back"
        KEY_BLOCKED -> "Server checks are back"
        else -> clearedWords(key.substringBefore('|'), key.substringAfter('|'))
    }
    return Alert(key, title, "", loud = false)
}

/**
 * The server answered. Each problem is said once, when it's new, and quietly closed when it's over;
 * one that's been open for days doesn't repeat every check. `serverMs` is the server's clock at the
 * answer, which the monitor's last sample is measured against: the old phone's clock can drift.
 */
fun heardFrom(book: AlertBook, doc: NowDoc, nowMs: Long, serverMs: Long = nowMs): AlertTurn {
    val wrong = doc.problems.associate { keyOf(it) to Alert(keyOf(it), alertWords(it), it.detail, loud = true) }.toMutableMap()
    doc.at?.takeIf { serverMs - it > GONE_QUIET_MS }?.let {
        wrong[KEY_QUIET] = Alert(KEY_QUIET, "Monitor has gone quiet", "Last sample ${moment(it / 1000.0)}", loud = true)
    }
    val news = wrong.filterKeys { it !in book.open }.values
    val over = book.open.filter { it !in wrong }.map(::clearedAlert)
    return AlertTurn(AlertBook(wrong.keys.toList(), misses = 0, heardAt = nowMs), news + over)
}

/**
 * Android wouldn't let the check out, which says nothing about the server, so nothing changes. A
 * monitor that stops without a word is the worst way for one to fail, though, so a long gap is said.
 */
fun notAsked(book: AlertBook, nowMs: Long): AlertTurn {
    val tooLong = book.heardAt > 0 && nowMs - book.heardAt > BLOCKED_TOO_LONG_MS
    if (!tooLong || KEY_BLOCKED in book.open) return AlertTurn(book, emptyList())
    val alert = Alert(KEY_BLOCKED, "Android is blocking server checks", "Last got through ${moment(book.heardAt / 1000.0)}", loud = true)
    return AlertTurn(book.copy(open = book.open + KEY_BLOCKED), listOf(alert))
}

/** No answer. What was already wrong stays open: nothing has said it's over. */
fun notHeard(book: AlertBook): AlertTurn {
    val next = book.copy(misses = book.misses + 1)
    if (next.misses < MISSES_BEFORE_ALERT || KEY_CUT_OFF in book.open) return AlertTurn(next, emptyList())
    val since = if (book.heardAt > 0) "Last heard from ${moment(book.heardAt / 1000.0)}" else ""
    return AlertTurn(
        next.copy(open = book.open + KEY_CUT_OFF),
        listOf(Alert(KEY_CUT_OFF, "Can't reach the server", since, loud = true)),
    )
}

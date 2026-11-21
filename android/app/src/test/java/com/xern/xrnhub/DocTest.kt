package com.xern.xrnhub

import com.xern.xrnhub.ui.Doc
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class DocTest {
    /** One round of a page's loop: what the spinner shows while it asks, then the answer. */
    private fun Doc<String>.round(): Boolean {
        startRound()
        val shown = loading
        loading = false
        return shown
    }

    @Test fun the_spinner_shows_only_for_the_round_a_pull_asked_for() {
        val doc = Doc<String>(null)
        assertFalse("opening the page", doc.round())
        assertFalse("a timed refresh", doc.round())
        doc.refresh()
        assertTrue("the pull", doc.round())
        assertFalse("the next timed refresh", doc.round())
        assertFalse("and the one after", doc.round())
        doc.refresh()
        assertTrue("a second pull", doc.round())
        assertFalse(doc.round())
    }
}

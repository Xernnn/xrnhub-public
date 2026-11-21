package com.xern.xrnhub

import com.xern.xrnhub.data.Session
import com.xern.xrnhub.data.renewed
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class SessionTest {
    private val held = Session("https://home.example/xrnhub", "alex", "old", madeAt = 1_000)

    @Test fun a_renewal_replaces_the_session_it_started_from() {
        assertEquals(held.copy(cookie = "new", madeAt = 9_000), renewed(held, held, "new", 9_000))
    }

    @Test fun a_renewal_that_ends_after_signing_out_is_dropped() {
        assertNull(renewed(null, held, "new", 9_000))
    }

    @Test fun a_renewal_that_ends_after_signing_in_again_is_dropped() {
        assertNull(renewed(held.copy(cookie = "typed in since", madeAt = 5_000), held, "new", 9_000))
        assertNull(renewed(held.copy(address = "https://other.example/xrnhub"), held, "new", 9_000))
    }
}

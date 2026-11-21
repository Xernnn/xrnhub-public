package com.xern.xrnhub

import com.xern.xrnhub.data.PageCache
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

class PageCacheTest {
    @Test fun an_hour_of_live_polls_keeps_one_copy() {
        val cache = PageCache()
        val start = 1_763_000_000_000L
        // One hour of polls three seconds apart, each asking for what came after the last.
        repeat(1200) { cache["api/server/now?since=${start + it * 3000L}&feed=1"] = it }
        assertEquals(1, cache.size)
        assertEquals(1199, cache["api/server/now?since=0&feed=1"])
        cache["api/server/now?since=5"] = "without the feed"
        assertEquals(2, cache.size)
        assertEquals("without the feed", cache["api/server/now?since=9"])
    }

    @Test fun only_the_pages_used_last_are_kept() {
        val cache = PageCache(limit = 3)
        cache["api/review"] = "review"
        cache["api/day?date=2026-11-01"] = 1
        cache["api/day?date=2026-11-02"] = 2
        assertEquals("review", cache["api/review"]) // used again, so it stays
        cache["api/server/logs?q=error"] = "search"
        assertEquals(3, cache.size)
        assertNull(cache["api/day?date=2026-11-01"])
        assertEquals("review", cache["api/review"])
        assertEquals(2, cache["api/day?date=2026-11-02"])
        repeat(500) { cache["api/server/logs?q=search$it"] = it }
        assertEquals(3, cache.size)
    }

    @Test fun other_queries_stay_their_own_pages() {
        val cache = PageCache()
        cache["api/server/history?range=1h"] = "hour"
        cache["api/server/history?range=24h"] = "day"
        cache["api/server/history?range=24h&service=xrnloops"] = "one service"
        assertEquals("hour", cache["api/server/history?range=1h"])
        assertEquals("day", cache["api/server/history?range=24h"])
        assertEquals("one service", cache["api/server/history?range=24h&service=xrnloops"])
        cache.clear()
        assertEquals(0, cache.size)
    }
}

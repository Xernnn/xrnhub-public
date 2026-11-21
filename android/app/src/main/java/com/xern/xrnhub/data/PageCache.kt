package com.xern.xrnhub.data

private const val PAGES_KEPT = 32
private const val SINCE = "since="

/**
 * The last copy of each page, for showing at once when it opens again.
 *
 * A live poll's `since=` changes on every poll, so it is no part of the key: otherwise each 3-second
 * answer would be one more copy, kept until sign-out. Each search and each day opened is a page of
 * its own, so only the ones used last are kept.
 */
class PageCache(private val limit: Int = PAGES_KEPT) {
    private val pages = object : LinkedHashMap<String, Any>(16, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Any>?) = size > limit
    }

    val size: Int @Synchronized get() = pages.size

    @Synchronized operator fun get(path: String): Any? = pages[keyOf(path)]

    @Synchronized operator fun set(path: String, doc: Any) {
        pages[keyOf(path)] = doc
    }

    @Synchronized fun clear() = pages.clear()

    private fun keyOf(path: String): String {
        val query = path.substringAfter('?', "")
        if (SINCE !in query) return path
        val kept = query.split('&').filterNot { it.startsWith(SINCE) }
        return path.substringBefore('?') + if (kept.isEmpty()) "" else "?" + kept.joinToString("&")
    }
}

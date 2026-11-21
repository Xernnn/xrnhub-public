package com.xern.xrnhub.data

import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable

// The Apps page: what `/api/apps` answers, and how it reads. The words match web/apps.js, so the
// page and the app never disagree; change both together.

@Serializable
data class AppGroup(val id: String, val title: String = "")

@Serializable
data class Release(val kind: String = "", val version: String = "", val code: Long? = null)

/** One project. The live fields come only to the server's owner; to anyone else they're null. */
@Serializable
data class HubApp(
    val name: String,
    val group: String = "",
    val runs: List<String> = emptyList(),
    val repo: String = "",
    val repoUrl: String = "",
    @SerialName("public") val isPublic: Boolean = false,
    val service: String? = null,
    /** The service it talks to on the home server: its own, or the hub, which keeps xrnfit's data. */
    val server: String? = null,
    @SerialName("package") val packageName: String? = null,
    val web: String? = null,
    val up: Boolean? = null,
    val since: Double? = null,
    val records: Long? = null,
    val unit: String? = null,
    val week: Long? = null,
    val lastIn: Double? = null,
    val backup: Double? = null,
    val released: List<Release> = emptyList(),
)

@Serializable
data class AppsDoc(
    val at: Double = 0.0,
    val counted: Double? = null,
    val owner: Boolean = false,
    val groups: List<AppGroup> = emptyList(),
    val apps: List<HubApp> = emptyList(),
)

/** What PackageManager says this phone has. */
data class Installed(val name: String, val code: Long)

private const val BACKUP_OK_S = 3 * 86400.0 // the monitor's own limit (watcher.BACKUP_STALE_S)
private val RELEASE_WORDS = mapOf("phone" to "Phone app", "extension" to "Extension")

/** Up or down, with how long for, as the Server page says it. */
fun serviceWords(app: HubApp, now: Double = nowSeconds()): String = when {
    app.up == null -> "Not heard from yet"
    app.up == false -> "Down"
    app.since != null -> "Up · ${sinceNow(app.since, now)}"
    else -> "Up"
}

fun holdsWords(app: HubApp): String? {
    val records = app.records ?: return null
    val held = "${count(records.toDouble())} ${app.unit.orEmpty()}".trim()
    return if (app.week != null && app.week > 0) "$held · ${count(app.week.toDouble())} this week" else held
}

fun agoWords(seconds: Double, now: Double = nowSeconds()): String = "${sinceNow(seconds, now)} ago"

fun backupIsOld(seconds: Double, now: Double = nowSeconds()): Boolean = now - seconds > BACKUP_OK_S

fun releaseWords(released: List<Release>): String =
    released.joinToString(" · ") { "${RELEASE_WORDS[it.kind] ?: it.kind} ${it.version}" }

/**
 * The cards in the groups' order, with no heading between them. An app in a group the answer
 * doesn't list still shows, after the rest, and so does every app if the groups ever go.
 */
fun appsInOrder(doc: AppsDoc): List<HubApp> {
    val rank = doc.groups.withIndex().associate { (i, group) -> group.id to i }
    return doc.apps.sortedBy { rank[it.group] ?: rank.size }
}

/**
 * -1, 0 or 1, comparing dotted versions number by number: 0.2.10 is after 0.2.9, and 1.1 is 1.1.0.
 * Each part counts its leading digits, so a build suffix such as "-debug" changes nothing.
 */
fun compareVersions(a: String, b: String): Int {
    fun parts(text: String) = text.trim().split('.').map { part -> part.takeWhile(Char::isDigit).toLongOrNull() ?: 0L }
    val left = parts(a)
    val right = parts(b)
    for (i in 0 until maxOf(left.size, right.size)) {
        val diff = left.getOrElse(i) { 0L }.compareTo(right.getOrElse(i) { 0L })
        if (diff != 0) return diff.coerceIn(-1, 1)
    }
    return 0
}

/**
 * The phone release the server holds that's newer than what's installed, or null. Android itself
 * only takes a higher versionCode as an update, so the codes decide when both are known; otherwise
 * the names do.
 */
fun newerRelease(app: HubApp, installed: Installed?): Release? {
    installed ?: return null
    val release = app.released.firstOrNull { it.kind == "phone" } ?: return null
    val code = release.code
    val newer = if (code != null && installed.code > 0) code > installed.code else compareVersions(release.version, installed.name) > 0
    return release.takeIf { newer }
}

fun installedWords(installed: Installed?): String = when {
    installed == null -> "Not installed"
    installed.name.isEmpty() -> "Installed"
    else -> installed.name
}

/** Under "On this phone", which already says what's installed. */
fun updateWords(release: Release): String = "Update: ${release.version}"

/**
 * One line of a card's facts. `dot` is up (true) or down (false) where it's a service; `bad` reads in
 * red; `logo` is the service the value names, whose tile goes before it.
 */
data class Fact(val label: String, val value: String, val dot: Boolean? = null, val bad: Boolean = false, val logo: String? = null)

/** A card's facts, in the web page's order, with this phone's version last where the app has a package. */
fun factsOf(app: HubApp, installed: Installed?, now: Double = nowSeconds()): List<Fact> = buildList {
    // A hub from before it named the server has only the app's own service.
    (app.server ?: app.service)?.let { add(Fact("Server", "${serviceName(it)} · ${serviceWords(app, now)}", dot = app.up, logo = it)) }
    holdsWords(app)?.let { add(Fact("Holds", it)) }
    app.lastIn?.let { add(Fact("Last sync", agoWords(it, now))) }
    app.backup?.let { add(Fact("Last backup", agoWords(it, now), bad = backupIsOld(it, now))) }
    if (app.released.isNotEmpty()) add(Fact("Released", releaseWords(app.released)))
    if (app.packageName != null) add(Fact("On this phone", installedWords(installed)))
}

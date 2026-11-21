package com.xern.xrnhub.data

import java.io.IOException
import java.net.HttpURLConnection
import java.net.URI
import java.net.URL
import java.util.Base64

/** What can go wrong asking the hub, each with what the page does about it. */
sealed class HubProblem(message: String) : IOException(message)
class SignedOut : HubProblem("Signed out")
class NotOwner : HubProblem("Owner only")
class TooSoon : HubProblem("Too many tries")
class WrongLogin : HubProblem("Wrong username or password")
class Unreachable(cause: IOException? = null) : HubProblem("Can't reach the server") {
    init {
        if (cause != null) initCause(cause)
    }
}
class Refused(code: Int) : HubProblem("The server said $code")

private const val SESSION_COOKIE = "xrn_session"
private const val CLIENT_HEADER = "X-Xrn-Client"
private const val CLIENT = "xrnhub-android"
private const val CONNECT_TIMEOUT = 10_000
private const val READ_TIMEOUT = 20_000
private const val END_TIMEOUT = 5_000 // signing out goes on without the server
private const val DEFAULT_PATH = "/xrnhub"
private val LOOPBACK = setOf("127.0.0.1", "localhost")

/**
 * The hub's address from whatever was pasted: a bare host, a page's URL with a #fragment, or the
 * full thing. Anything that isn't https is refused, since a password and a session go over it. A
 * debug build may also use this device's own loopback, for a test server over adb reverse.
 */
fun normalAddress(text: String, allowLoopback: Boolean = false): String? {
    val raw = text.trim().trimEnd('/')
    if (raw.isEmpty()) return null
    val uri = runCatching { URI(if ("://" in raw) raw else "https://$raw") }.getOrNull() ?: return null
    val host = uri.host ?: return null
    val secure = uri.scheme == "https" || (uri.scheme == "http" && allowLoopback && host in LOOPBACK)
    if (!secure) return null
    val port = if (uri.port > 0) ":${uri.port}" else ""
    val path = uri.path.orEmpty().trimEnd('/').ifEmpty { DEFAULT_PATH }
    return "${uri.scheme}://$host$port$path"
}

private fun open(url: String, method: String): HttpURLConnection {
    val connection = URL(url).openConnection() as HttpURLConnection
    connection.requestMethod = method
    connection.instanceFollowRedirects = false // a session isn't for wherever a redirect points
    connection.useCaches = false
    connection.connectTimeout = CONNECT_TIMEOUT
    connection.readTimeout = READ_TIMEOUT
    connection.setRequestProperty(CLIENT_HEADER, CLIENT)
    return connection
}

private fun HttpURLConnection.send(body: ByteArray) {
    doOutput = true
    setFixedLengthStreamingMode(body.size)
    outputStream.use { it.write(body) }
}

/** Every call waits on the network, so run them off the main thread. */
private fun <T> reaching(call: () -> T): T = try {
    call()
} catch (e: HubProblem) {
    throw e
} catch (e: IOException) {
    throw Unreachable(e)
}

private fun HttpURLConnection.answer(): String {
    // Reading to the end, and never disconnecting, leaves the connection open for the next poll,
    // which saves a TLS handshake every three seconds.
    when (val code = responseCode) {
        in 200..299 -> return inputStream.bufferedReader().use { it.readText() }
        else -> {
            errorStream?.use { it.readBytes() }
            throw when (code) {
                401 -> SignedOut()
                403 -> NotOwner()
                429 -> TooSoon()
                else -> Refused(code)
            }
        }
    }
}

private val COOKIE_VALUE = Regex("(?:^|[;,\\s])$SESSION_COOKIE=([^;\\s]+)")

private fun HttpURLConnection.sessionCookie(): String =
    headerFields.filterKeys { it.equals("Set-Cookie", ignoreCase = true) }.values.flatten()
        .firstNotNullOfOrNull { COOKIE_VALUE.find(it)?.groupValues?.get(1) }
        ?.takeIf { it.isNotEmpty() } ?: throw Refused(responseCode)

/** Trades a password for a session; only the session is kept. */
fun askForSession(address: String, user: String, password: String): String = reaching {
    val connection = open("$address/api/session", "POST")
    val pair = Base64.getEncoder().encodeToString("$user:$password".toByteArray())
    connection.setRequestProperty("Authorization", "Basic $pair")
    connection.send(ByteArray(0))
    try {
        connection.answer()
    } catch (_: SignedOut) {
        throw WrongLogin()
    }
    connection.sessionCookie()
}

class Hub(private val address: String, private val cookie: String) {
    /**
     * The server's clock minus this phone's, from the Date header of the last answer, or null before
     * one. The monitor's times are the server's, so they're compared with its clock, not the phone's.
     */
    @Volatile var clockGapMs: Long? = null
        private set

    private fun request(path: String, method: String): HttpURLConnection =
        open("$address/$path", method).apply { setRequestProperty("Cookie", "$SESSION_COOKIE=$cookie") }

    fun get(path: String): String = reaching {
        val connection = request(path, "GET")
        val body = connection.answer()
        connection.date.takeIf { it > 0 }?.let { clockGapMs = it - System.currentTimeMillis() }
        body
    }

    fun post(path: String, body: String): String = reaching {
        val connection = request(path, "POST")
        connection.setRequestProperty("Content-Type", "application/json")
        connection.send(body.toByteArray())
        connection.answer()
    }

    /** A new session for a live one, so an app in regular use is never signed out. */
    fun renew(): String = reaching {
        val connection = request("api/session", "POST")
        connection.send(ByteArray(0))
        connection.answer()
        connection.sessionCookie()
    }

    /** Ends this session on the server, so a copy of its cookie stops working too. */
    fun endSession() {
        reaching {
            val connection = request("api/session", "DELETE")
            connection.connectTimeout = END_TIMEOUT
            connection.readTimeout = END_TIMEOUT
            connection.answer()
        }
    }
}

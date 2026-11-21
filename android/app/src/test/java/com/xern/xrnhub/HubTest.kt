package com.xern.xrnhub

import com.sun.net.httpserver.HttpExchange
import com.sun.net.httpserver.HttpServer
import com.xern.xrnhub.data.Hub
import com.xern.xrnhub.data.NotOwner
import com.xern.xrnhub.data.Refused
import com.xern.xrnhub.data.SignedOut
import com.xern.xrnhub.data.TooSoon
import com.xern.xrnhub.data.Unreachable
import com.xern.xrnhub.data.WrongLogin
import com.xern.xrnhub.data.askForSession
import com.xern.xrnhub.data.normalAddress
import java.net.InetAddress
import java.net.InetSocketAddress
import java.net.ServerSocket
import java.time.ZoneOffset
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Base64
import java.util.concurrent.atomic.AtomicReference
import kotlin.concurrent.thread
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test

class HubTest {
    private lateinit var server: HttpServer
    private lateinit var address: String
    private val seen = AtomicReference<HttpExchange?>()
    private val bodies = AtomicReference("")
    private var reply: (HttpExchange) -> Unit = { it.answer(200, "{}") }

    private fun HttpExchange.answer(code: Int, body: String, vararg headers: Pair<String, String>) {
        headers.forEach { (k, v) -> responseHeaders.add(k, v) }
        val bytes = body.toByteArray()
        sendResponseHeaders(code, if (bytes.isEmpty()) -1 else bytes.size.toLong())
        if (bytes.isNotEmpty()) responseBody.use { it.write(bytes) } else close()
    }

    @Before fun start() {
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
        server.createContext("/") {
            seen.set(it)
            bodies.set(it.requestBody.readBytes().decodeToString())
            reply(it)
        }
        server.start()
        address = "http://127.0.0.1:${server.address.port}/xrnhub"
    }

    @After fun stop() = server.stop(0)

    private fun header(name: String): String? = seen.get()?.requestHeaders?.getFirst(name)

    @Test fun signing_in_sends_the_password_once_and_keeps_only_the_session() {
        reply = { it.answer(200, """{"ok":true}""", "Set-Cookie" to "xrn_session=abc.def; Path=/; Max-Age=2592000; HttpOnly; Secure") }
        assertEquals("abc.def", askForSession(address, "alex", "open sesame"))
        assertEquals("POST", seen.get()?.requestMethod)
        assertEquals("/xrnhub/api/session", seen.get()?.requestURI?.path)
        val pair = String(Base64.getDecoder().decode(header("Authorization")!!.removePrefix("Basic ")))
        assertEquals("alex:open sesame", pair)
        assertTrue(header("X-Xrn-Client")!!.isNotEmpty())
    }

    @Test fun a_wrong_password_says_so() {
        reply = { it.answer(401, "login required") }
        assertThrows(WrongLogin::class.java) { askForSession(address, "alex", "nope") }
    }

    @Test fun a_lockout_says_so() {
        reply = { it.answer(429, "too many") }
        assertThrows(TooSoon::class.java) { askForSession(address, "alex", "nope") }
    }

    @Test fun an_answer_with_no_session_is_not_a_sign_in() {
        reply = { it.answer(200, """{"ok":true}""") }
        assertThrows(Refused::class.java) { askForSession(address, "alex", "x") }
    }

    @Test fun a_session_goes_with_the_header_the_hub_wants() {
        reply = { it.answer(200, "hello") }
        assertEquals("hello", Hub(address, "tok").get("api/server/now?since=0"))
        assertEquals("xrn_session=tok", header("Cookie"))
        assertTrue(header("X-Xrn-Client")!!.isNotEmpty())
        assertNull(header("Authorization"))
        assertEquals("/xrnhub/api/server/now", seen.get()?.requestURI?.path)
        assertEquals("since=0", seen.get()?.requestURI?.query)
    }

    @Test fun a_restart_posts_its_json() {
        reply = { it.answer(200, """{"ok":true,"said":""}""") }
        Hub(address, "tok").post("api/server/restart", """{"service":"xrnloops"}""")
        assertEquals("POST", seen.get()?.requestMethod)
        assertEquals("""{"service":"xrnloops"}""", bodies.get())
        assertEquals("application/json", header("Content-Type"))
    }

    @Test fun refusals_become_what_the_page_needs_to_know() {
        reply = { it.answer(401, "") }
        assertThrows(SignedOut::class.java) { Hub(address, "tok").get("api/review") }
        reply = { it.answer(403, "owner only") }
        assertThrows(NotOwner::class.java) { Hub(address, "tok").get("api/server/now") }
        reply = { it.answer(429, "") }
        assertThrows(TooSoon::class.java) { Hub(address, "tok").post("api/server/restart", "{}") }
        reply = { it.answer(500, "oops") }
        assertThrows(Refused::class.java) { Hub(address, "tok").get("api/review") }
    }

    @Test fun a_redirect_is_not_followed_with_the_session() {
        var hits = 0
        reply = {
            hits += 1
            it.answer(302, "", "Location" to "$address/elsewhere")
        }
        assertThrows(Refused::class.java) { Hub(address, "tok").get("api/review") }
        assertEquals(1, hits)
    }

    @Test fun renewing_trades_the_session_for_a_new_one() {
        reply = { it.answer(200, "{}", "Set-Cookie" to "xrn_session=newer; Path=/") }
        assertEquals("newer", Hub(address, "old").renew())
        assertEquals("xrn_session=old", header("Cookie"))
        assertNull(header("Authorization"))
    }

    @Test fun an_answer_says_how_far_the_servers_clock_is_from_the_phones() {
        // A bare socket, since the JDK's test server writes its own Date header over any other.
        val behind = DateTimeFormatter.RFC_1123_DATE_TIME.format(ZonedDateTime.now(ZoneOffset.UTC).minusMinutes(10))
        ServerSocket(0, 1, InetAddress.getLoopbackAddress()).use { socket ->
            thread {
                socket.accept().use { peer ->
                    val reader = peer.getInputStream().bufferedReader()
                    while (reader.readLine().orEmpty().isNotEmpty()) Unit
                    peer.getOutputStream().write("HTTP/1.1 200 OK\r\nDate: $behind\r\nContent-Length: 2\r\nConnection: close\r\n\r\n{}".toByteArray())
                }
            }
            val hub = Hub("http://127.0.0.1:${socket.localPort}/xrnhub", "tok")
            assertNull(hub.clockGapMs)
            assertEquals("{}", hub.get("api/server/now?since=0"))
            val gap = hub.clockGapMs!!
            assertTrue("$gap", gap in -602_000L..-598_000L)
        }
    }

    @Test fun signing_out_ends_the_session_on_the_server() {
        reply = { it.answer(200, """{"ok":true}""", "Set-Cookie" to "xrn_session=; Max-Age=0; Path=/") }
        Hub(address, "tok").endSession()
        assertEquals("DELETE", seen.get()?.requestMethod)
        assertEquals("/xrnhub/api/session", seen.get()?.requestURI?.path)
        assertEquals("xrn_session=tok", header("Cookie"))
        assertTrue(header("X-Xrn-Client")!!.isNotEmpty())
    }

    @Test fun signing_out_goes_on_when_the_server_does_not_answer() {
        val port = server.address.port
        server.stop(0)
        assertThrows(Unreachable::class.java) { Hub("http://127.0.0.1:$port/xrnhub", "tok").endSession() }
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    }

    @Test fun a_server_that_is_off_is_unreachable() {
        val port = server.address.port
        server.stop(0)
        assertThrows(Unreachable::class.java) { Hub("http://127.0.0.1:$port/xrnhub", "tok").get("api/review") }
        server = HttpServer.create(InetSocketAddress("127.0.0.1", 0), 0)
    }

    @Test fun an_address_can_be_pasted_in_any_shape() {
        assertEquals("https://home.example/xrnhub", normalAddress("home.example"))
        assertEquals("https://home.example/xrnhub", normalAddress(" https://home.example/ "))
        assertEquals("https://home.example/xrnhub", normalAddress("https://home.example/xrnhub/"))
        assertEquals("https://home.example/xrnhub", normalAddress("https://home.example/xrnhub/#server/traffic"))
        assertEquals("https://home.example/xrnhub", normalAddress("https://home.example/xrnhub?a=1"))
        assertEquals("https://home.example:8443/hub", normalAddress("https://home.example:8443/hub"))
    }

    @Test fun a_password_is_never_sent_in_the_clear() {
        assertNull(normalAddress("http://home.example/xrnhub"))
        assertNull(normalAddress("http://home.example/xrnhub", allowLoopback = true))
        assertNull(normalAddress("http://127.0.0.1:15238/xrnhub"))
        assertNull(normalAddress("ftp://home.example"))
        assertNull(normalAddress(""))
        assertNull(normalAddress("   "))
    }

    @Test fun only_a_debug_build_may_use_its_own_loopback() {
        assertEquals("http://127.0.0.1:15238/xrnhub", normalAddress("http://127.0.0.1:15238/xrnhub", allowLoopback = true))
        assertFalse(normalAddress("https://127.0.0.1/xrnhub").isNullOrEmpty())
    }
}

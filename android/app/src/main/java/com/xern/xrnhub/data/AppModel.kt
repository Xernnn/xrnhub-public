package com.xern.xrnhub.data

import android.app.Application
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.xern.xrnhub.BuildConfig
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.serialization.KSerializer
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

private const val RING_STALE_MS = 2 * 60_000L

/** The sign-in, the live numbers, and the last copy of each page for showing at once. */
class AppModel(app: Application) : AndroidViewModel(app) {
    private val store = SessionStore(app)
    private val pages = PageCache()

    var session by mutableStateOf(store.load())
        private set
    var now by mutableStateOf<NowDoc?>(null)
        private set
    var ring by mutableStateOf(emptyList<Point>())
        private set
    var misses by mutableIntStateOf(0)
        private set
    var ownerOnly by mutableStateOf(false)
        private set
    private var clockGapMs = 0L

    val address: String get() = store.lastAddress ?: BuildConfig.DEFAULT_ADDRESS
    val user: String get() = store.lastUser ?: BuildConfig.DEFAULT_USER

    private val hub: Hub? get() = session?.let { Hub(it.address, it.cookie) }

    /** The server's clock, as of its last answer: what the monitor's sample times are measured by. */
    fun serverNow(): Long = System.currentTimeMillis() + clockGapMs

    init {
        if (session != null) Watch.start(app)
        renewIfOld()
    }

    private fun renewIfOld() {
        val held = session ?: return
        if (!held.isOld(System.currentTimeMillis())) return
        viewModelScope.launch {
            // A failed renewal changes nothing: the session holds until it really expires.
            runCatching { withContext(Dispatchers.IO) { Hub(held.address, held.cookie).renew() } }
                .onSuccess { cookie -> store.renew(held, cookie, System.currentTimeMillis())?.let { session = it } }
        }
    }

    private fun keep(next: Session) {
        store.save(next)
        session = next
    }

    /** Null when signed in, otherwise what to tell the person. */
    suspend fun signIn(address: String, user: String, password: String): String? {
        val where = normalAddress(address, BuildConfig.DEBUG) ?: return "That isn't an https address"
        val name = user.trim()
        return try {
            val cookie = withContext(Dispatchers.IO) { askForSession(where, name, password) }
            keep(Session(where, name, cookie, System.currentTimeMillis()))
            Watch.start(getApplication())
            null
        } catch (_: TooSoon) {
            "Too many wrong passwords. Try again in 15 minutes."
        } catch (e: HubProblem) {
            e.message
        }
    }

    /** Ends the session on the server as well as here; without the server it still ends here. */
    fun signOut() {
        session?.let { held ->
            // Started before the cookie is forgotten, and not waited for.
            viewModelScope.launch(Dispatchers.IO) { runCatching { Hub(held.address, held.cookie).endSession() } }
        }
        forget()
    }

    /** The server has ended the session already, or is being told to. */
    private fun forget() {
        Watch.stop(getApplication())
        store.forgetCookie()
        session = null
        now = null
        ring = emptyList()
        misses = 0
        ownerOnly = false
        pages.clear()
    }

    @Suppress("UNCHECKED_CAST")
    fun <T : Any> cached(path: String): T? = pages[path] as T?

    suspend fun <T : Any> fetch(path: String, serializer: KSerializer<T>): T {
        val asking = hub ?: throw SignedOut()
        try {
            val doc = withContext(Dispatchers.IO) { docJson.decodeFromString(serializer, asking.get(path)) }
            asking.clockGapMs?.let { clockGapMs = it }
            pages[path] = doc
            return doc
        } catch (e: SignedOut) {
            forget()
            throw e
        } catch (e: NotOwner) {
            ownerOnly = true
            throw e
        }
    }

    /** One look at the phone. Reading it is what makes it sample every few seconds. */
    suspend fun poll(feed: Boolean) {
        val newest = ring.lastOrNull()?.at
        // After a while away, the server's minute rows fill the charts better than the old ring.
        val fresh = newest == null || serverNow() - newest > RING_STALE_MS
        val since = if (fresh) 0 else newest
        try {
            val doc = fetch("api/server/now?since=$since${if (feed) "&feed=1" else ""}", NowDoc.serializer())
            now = doc
            misses = 0
            ring = merged(ring, doc.samples, fresh)
        } catch (e: CancellationException) {
            throw e
        } catch (_: NotOwner) {
            // Said on the page instead.
        } catch (_: SignedOut) {
            // Back to the sign-in page.
        } catch (_: Exception) {
            misses += 1
        }
    }

    /** What to show once it's done. */
    suspend fun restart(name: String): String {
        val asking = hub ?: return "Signed out"
        val body = buildJsonObject { put("service", name) }.toString()
        return try {
            val said = withContext(Dispatchers.IO) { asking.post("api/server/restart", body) }
            val answer = docJson.decodeFromString(RestartAnswer.serializer(), said)
            if (answer.ok) "${serviceName(name)} restarted" else "Restart failed: ${answer.said}"
        } catch (_: TooSoon) {
            "Restarted a moment ago"
        } catch (e: SignedOut) {
            forget()
            e.message.orEmpty()
        } catch (e: HubProblem) {
            e.message.orEmpty()
        }
    }
}

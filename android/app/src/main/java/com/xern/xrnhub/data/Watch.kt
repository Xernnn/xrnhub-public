package com.xern.xrnhub.data

import android.app.PendingIntent
import android.app.job.JobInfo
import android.app.job.JobParameters
import android.app.job.JobScheduler
import android.app.job.JobService
import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.net.ConnectivityManager
import android.net.NetworkInfo
import android.util.Log
import androidx.core.app.NotificationChannelCompat
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import com.xern.xrnhub.R
import java.util.concurrent.ConcurrentHashMap
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.launch

private const val CHECKS = 1
private const val CHECK_NOW = 2
private const val CHECK_EVERY_MS = 15 * 60_000L // the shortest gap Android allows
private const val CHECK_WINDOW_MS = 5 * 60_000L // and the narrowest window within it
private const val RETRY_AFTER_MS = 2 * 60_000L // a first failure is checked again soon, not in 15 minutes
private const val BLOCKED_WAIT_MS = 3_000L
private const val PROBLEMS = "problems"
private const val RECOVERED = "recovered"
private const val SLATE = 0xFF475569.toInt()
private const val RECOVERED_SHOWN_MS = 6 * 3_600_000L
private const val BOOK = "alerts"
private const val BOOK_KEY = "book"
private const val TAG = "xrnhub"

/**
 * Keeps an eye on the server while the app is closed. Signing in starts it, signing out ends it.
 *
 * This uses JobScheduler rather than WorkManager on purpose. While the app's process is alive, even
 * idle, WorkManager runs due work inside it instead of as a job, and Android blocks the network for
 * idle apps, so the check fails and reads as an outage.
 */
object Watch {
    private fun jobs(context: Context) = context.getSystemService(JobScheduler::class.java)

    private fun check(context: Context, id: Int) =
        JobInfo.Builder(id, ComponentName(context, ServerCheck::class.java))
            .setRequiredNetworkType(JobInfo.NETWORK_TYPE_ANY)
            .setBackoffCriteria(RETRY_AFTER_MS, JobInfo.BACKOFF_POLICY_LINEAR)

    fun start(context: Context) {
        // Scheduling it again would restart the 15 minutes every time the app opens.
        if (jobs(context).getPendingJob(CHECKS) == null) {
            val job = check(context, CHECKS).setPeriodic(CHECK_EVERY_MS, CHECK_WINDOW_MS).setPersisted(true)
            jobs(context).schedule(job.build())
        }
        NotificationManagerCompat.from(context).cancel(KEY_SIGNED_OUT, 1)
    }

    /** One extra check, right away. */
    fun checkNow(context: Context) {
        jobs(context).schedule(check(context, CHECK_NOW).build())
    }

    fun stopChecking(context: Context) = jobs(context).cancelAll()

    fun stop(context: Context) {
        stopChecking(context)
        NotificationManagerCompat.from(context).cancelAll()
        book(context).edit().clear().apply()
    }

    internal fun book(context: Context) = context.getSharedPreferences(BOOK, Context.MODE_PRIVATE)
}

class ServerCheck : JobService() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private val running = ConcurrentHashMap<Int, Job>()
    private val notes by lazy { NotificationManagerCompat.from(this) }

    override fun onStartJob(params: JobParameters): Boolean {
        running[params.jobId] = scope.launch {
            val again = try {
                check()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Nobody is watching a background job, so a crash here would only repeat, unseen.
                Log.w(TAG, "check stopped: ${e.javaClass.simpleName}")
                false
            }
            jobFinished(params, again)
        }
        return true
    }

    override fun onStopJob(params: JobParameters): Boolean {
        running.remove(params.jobId)?.cancel()
        return true
    }

    override fun onDestroy() {
        scope.cancel()
        super.onDestroy()
    }

    /** Whether this app is being refused the network, which says nothing about the server. */
    @Suppress("DEPRECATION") // no newer call says this about the calling app
    private fun blocked(): Boolean =
        getSystemService(ConnectivityManager::class.java).activeNetworkInfo?.detailedState ==
            NetworkInfo.DetailedState.BLOCKED

    private fun read(hub: Hub, now: Long): NowDoc =
        // `since` is now because no points are wanted, only the readings and the problems.
        docJson.decodeFromString(NowDoc.serializer(), hub.get("api/server/now?since=$now"))

    private suspend fun ask(hub: Hub, now: Long): NowDoc {
        try {
            return read(hub, now)
        } catch (e: Unreachable) {
            if (!blocked()) throw e
        }
        delay(BLOCKED_WAIT_MS) // Android can be a moment behind the job starting
        return read(hub, now)
    }

    /** Returns whether to try again soon. */
    private suspend fun check(): Boolean {
        // Turning xrnhub's notifications off in Android also stops it asking the server.
        if (!notes.areNotificationsEnabled()) return false
        val store = SessionStore(this)
        val session = store.load() ?: return false
        val hub = Hub(session.address, session.cookie)
        val book = readBook()
        val now = System.currentTimeMillis()
        val turn = try {
            val doc = ask(hub, now)
            Log.i(TAG, "heard: ${doc.problems.size} open")
            heardFrom(book, doc, now, now + (hub.clockGapMs ?: 0))
        } catch (e: CancellationException) {
            throw e
        } catch (_: SignedOut) {
            endedSession(store)
            return false
        } catch (_: NotOwner) {
            // Only the owner can watch the server, so there's nothing to do here.
            Watch.stopChecking(this)
            return false
        } catch (_: TooSoon) {
            return false
        } catch (e: Exception) {
            val shut = blocked()
            Log.w(TAG, "check failed: ${e.javaClass.simpleName} <- ${e.cause?.javaClass?.simpleName} blocked=$shut")
            if (shut) notAsked(book, now) else notHeard(book)
        }
        currentCoroutineContext().ensureActive() // told to stop mid-check: what it saw doesn't count
        writeBook(turn.book)
        turn.alerts.forEach(::post)
        if (turn.book.misses == 0 && turn.book.heardAt == now && session.isOld(now)) renew(store, session, hub, now)
        return turn.book.misses == 1
    }

    /** Runs here as well as in the app, so alerts outlast a month of not opening it. */
    private suspend fun renew(store: SessionStore, session: Session, hub: Hub, now: Long) {
        // A failed renewal changes nothing: the session holds until it really expires.
        val cookie = runCatching { hub.renew() }.getOrNull() ?: return
        currentCoroutineContext().ensureActive() // stopped by signing out meanwhile
        store.renew(session, cookie, now)
    }

    private fun endedSession(store: SessionStore) {
        store.forgetCookie()
        Watch.stopChecking(this)
        post(Alert(KEY_SIGNED_OUT, "Sign in to xrnhub again", "Alerts are off until you do", loud = true))
    }

    private fun readBook(): AlertBook =
        Watch.book(this).getString(BOOK_KEY, null)
            ?.let { runCatching { docJson.decodeFromString(AlertBook.serializer(), it) }.getOrNull() }
            ?: AlertBook()

    private fun writeBook(book: AlertBook) {
        val text = docJson.encodeToString(AlertBook.serializer(), book)
        Watch.book(this).edit().putString(BOOK_KEY, text).apply()
    }

    private fun post(alert: Alert) {
        notes.createNotificationChannelsCompat(
            listOf(
                NotificationChannelCompat.Builder(PROBLEMS, NotificationManagerCompat.IMPORTANCE_HIGH)
                    .setName("Server problems").build(),
                NotificationChannelCompat.Builder(RECOVERED, NotificationManagerCompat.IMPORTANCE_LOW)
                    .setName("Back to normal").build(),
            ),
        )
        val note = NotificationCompat.Builder(this, if (alert.loud) PROBLEMS else RECOVERED)
            .setSmallIcon(R.drawable.ic_notify)
            .setColor(SLATE)
            .setContentTitle(alert.title)
            .setContentIntent(openApp())
            .setAutoCancel(true)
            .setShowWhen(true)
        if (alert.text.isNotEmpty()) note.setContentText(alert.text)
        if (!alert.loud) note.setTimeoutAfter(RECOVERED_SHOWN_MS)
        // Permission was checked at the top of the check, and a refusal here would only lose this note.
        runCatching { notes.notify(alert.key, 1, note.build()) }
    }

    private fun openApp(): PendingIntent {
        val intent = packageManager.getLaunchIntentForPackage(packageName)
            ?: Intent()
        return PendingIntent.getActivity(this, 0, intent, PendingIntent.FLAG_IMMUTABLE)
    }
}

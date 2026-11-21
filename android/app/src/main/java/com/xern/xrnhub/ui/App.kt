package com.xern.xrnhub.ui

import android.Manifest
import android.content.pm.PackageManager
import android.os.Build
import androidx.activity.compose.BackHandler
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.unit.dp
import androidx.core.content.ContextCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import com.xern.xrnhub.R
import com.xern.xrnhub.data.AppModel
import com.xern.xrnhub.data.FIT
import com.xern.xrnhub.data.Watch
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive

private const val POLL_MS = 3000L
private const val SERVER = "server"
private const val REVIEW = "review"
private const val APPS = "apps"
private const val OVERVIEW = "overview"

private val SECTIONS = listOf(
    OVERVIEW to "Overview", "services" to "Services", "traffic" to "Traffic",
    "data" to "Data", "security" to "Security", "logs" to "Logs",
)
private val LIVE_SECTIONS = setOf(OVERVIEW, "services", "traffic")

/** Where to go inside the Server tab: a section, and one service within Services. */
typealias Go = (section: String, service: String?) -> Unit

@Composable
fun App(model: AppModel = viewModel()) {
    CompositionLocalProvider(LocalModel provides model) {
        Box(Modifier.fillMaxSize().background(colors.bg)) {
            if (model.session == null) SignIn() else Shell()
        }
    }
}

/** Alerts need permission from Android 13. Asked here, so a person sees why: they just signed in. */
@Composable
private fun AskToNotify() {
    if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
    val context = LocalContext.current
    // The first check may have run while this was on screen, and been able to say nothing.
    val ask = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { allowed ->
        if (allowed) Watch.checkNow(context)
    }
    LaunchedEffect(Unit) {
        val held = ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS)
        if (held != PackageManager.PERMISSION_GRANTED) ask.launch(Manifest.permission.POST_NOTIFICATIONS)
    }
}

@Composable
private fun Shell() {
    AskToNotify()
    var tab by rememberSaveable { mutableStateOf(SERVER) }
    var section by rememberSaveable { mutableStateOf(OVERVIEW) }
    var service by rememberSaveable { mutableStateOf<String?>(null) }
    val saved = rememberSaveableStateHolder()
    val go: Go = { nextSection, nextService ->
        section = nextSection
        service = nextService
    }

    BackHandler(tab != SERVER || section != OVERVIEW || service != null) {
        when {
            tab != SERVER -> tab = SERVER
            service != null -> service = null
            else -> section = OVERVIEW
        }
    }

    val live = tab == SERVER && (section in LIVE_SECTIONS)
    LivePolling(live, feed = section == "traffic" && service == null)

    Column(Modifier.fillMaxSize()) {
        val model = LocalModel.current
        TopBar(model::signOut)
        if (tab == SERVER) SectionTabs(section) { go(it, null) }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            val where = if (tab == SERVER) "$section/${service.orEmpty()}" else tab
            saved.SaveableStateProvider(where) {
                when {
                    tab == REVIEW -> ReviewPage()
                    tab == APPS -> AppsPage()
                    LocalModel.current.ownerOnly -> Waiting("Owner only")
                    section == "services" && service == FIT -> FitDetail(go)
                    section == "services" && service != null -> ServiceDetail(service!!, go)
                    section == "services" -> ServicesPage(go)
                    section == "traffic" -> TrafficPage(go)
                    section == "data" -> DataPage()
                    section == "security" -> SecurityPage()
                    section == "logs" -> LogsPage(service.orEmpty())
                    else -> OverviewPage(go)
                }
            }
        }
        BottomBar(tab) { tab = it }
    }
}

/** Reads the phone every few seconds, only while someone's looking at a live page. */
@Composable
private fun LivePolling(on: Boolean, feed: Boolean) {
    val model = LocalModel.current
    val lifecycle = LocalLifecycleOwner.current.lifecycle
    LaunchedEffect(on, feed) {
        if (!on) return@LaunchedEffect
        lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (isActive && !model.ownerOnly) {
                model.poll(feed)
                delay(POLL_MS)
            }
        }
    }
}

@Composable
internal fun TopBar(signOut: () -> Unit) {
    var menu by remember { mutableStateOf(false) }
    var sure by remember { mutableStateOf(false) }
    Row(
        Modifier.fillMaxWidth().statusBarsPadding().padding(start = 16.dp, end = 4.dp, top = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        // The family's rule: the app's tile stands for its name, which stays for TalkBack.
        Image(painterResource(R.drawable.logo_xrnhub), "xrnhub", Modifier.size(30.dp).clip(RoundedCornerShape(7.dp)))
        Spacer(Modifier.weight(1f))
        Box {
            IconButton({
                sure = false
                menu = true
            }) {
                Icon(painterResource(R.drawable.ic_more), "More", tint = colors.muted)
            }
            DropdownMenu(menu, { menu = false }) {
                // Two taps: a stray one only turns the item into the question.
                val label = if (sure) "Confirm sign out" else "Sign out"
                DropdownMenuItem({ Text(label, style = Type.body, color = if (sure) colors.bad else colors.text) }, {
                    if (!sure) {
                        sure = true
                    } else {
                        menu = false
                        signOut()
                    }
                })
            }
        }
    }
}

@Composable
private fun SectionTabs(current: String, pick: (String) -> Unit) {
    Row(
        Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 12.dp, vertical = 6.dp),
        horizontalArrangement = Arrangement.spacedBy(4.dp),
    ) {
        SECTIONS.forEach { (id, label) ->
            val on = id == current
            Text(
                label,
                Modifier
                    .clip(RoundedCornerShape(10.dp))
                    .background(if (on) colors.surface2 else Color.Transparent)
                    .clickable { pick(id) }
                    .padding(horizontal = 12.dp, vertical = 7.dp),
                style = Type.label,
                color = if (on) colors.text else colors.muted,
            )
        }
    }
}

@Composable
private fun BottomBar(tab: String, pick: (String) -> Unit) {
    NavigationBar(containerColor = colors.surface) {
        listOf(
            Triple(SERVER, "Server", R.drawable.ic_tab_server),
            Triple(REVIEW, "Review", R.drawable.ic_tab_review),
            Triple(APPS, "Apps", R.drawable.ic_tab_apps),
        ).forEach { (id, label, icon) ->
            NavigationBarItem(
                selected = tab == id,
                onClick = { pick(id) },
                icon = { Icon(painterResource(icon), null) },
                label = { Text(label, style = Type.label) },
                colors = NavigationBarItemDefaults.colors(
                    selectedIconColor = colors.text,
                    selectedTextColor = colors.text,
                    unselectedIconColor = colors.muted,
                    unselectedTextColor = colors.muted,
                    indicatorColor = colors.surface2,
                ),
            )
        }
    }
}

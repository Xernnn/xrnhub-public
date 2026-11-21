package com.xern.xrnhub.ui

// The Apps page's cards with made-up data, for rendering without a phone or a server:
//   android preview render "?:app" --fqn=com.xern.xrnhub.ui.AppsPreviewKt.AppsLight
// Debug only, since @Preview comes with ui-tooling, which only the debug build has.

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import com.xern.xrnhub.data.HubApp
import com.xern.xrnhub.data.Installed
import com.xern.xrnhub.data.Release

private val now = System.currentTimeMillis() / 1000.0
private val rule = HubApp(
    "xrnrule", "time",
    listOf("Phone", "Laptop", "Browser", "Server"), "xrnrule", "https://example.test/xrnrule", true, "xrnrule",
    packageName = "com.xern.xrnrule", web = "/xrnrule/", up = true, since = now - 23 * 86400, records = 27, unit = "reports", week = 13, lastIn = now - 1300,
    released = listOf(Release("phone", "0.2.17", 17), Release("extension", "2.3.6")),
)
private val loops = HubApp(
    "xrnloops", "time",
    listOf("Phone", "Laptop", "Server"), "xrnloops", "https://example.test/xrnloops", true, "xrnloops",
    packageName = "com.xern.xrnloops", web = "/xrnloops/", up = false, records = 960, unit = "check-ins", week = 22, lastIn = now - 400,
)
private val social = HubApp(
    "xrnsoc", "focus",
    listOf("Browser"), "xrnsoc", "https://example.test/xrnsoc", true,
)
private val insta = HubApp(
    "xrninsta", "play", listOf("Server", "Telegram"), "xrninsta",
    "https://example.test/xrninsta", true, "xrninsta", web = "/xrninsta/", up = true, since = now - 2 * 86400, records = 90,
    unit = "songs", week = 7, lastIn = now - 5000,
)
private val server = HubApp(
    "home-server", "server", listOf("Laptop"), "home-server",
    "https://example.test/home-server", false, backup = now - 4 * 86400,
)
private val fit = HubApp(
    "xrnfit", "life", listOf("Phone", "Server"), "xrnfit", "https://example.test/xrnfit", false,
    server = "xrnhub", packageName = "com.xern.xrnfit", up = true, since = now - 26 * 86400, lastIn = now - 90,
)

@Composable
private fun Cards() {
    Column(Modifier.background(colors.bg).padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        AppCard(rule, Installed("0.2.16", 16), "https://example.test/xrnhub")
        AppCard(loops, Installed("2.3.3", 20303), "https://example.test/xrnhub")
        AppCard(fit, null, null)
        AppCard(social, null, null)
        AppCard(insta, null, "https://example.test/xrnhub")
        AppCard(server, null, null)
    }
}

@Preview(widthDp = 390, heightDp = 2200)
@Composable
fun AppsLight() = HubTheme { Cards() }

@Preview(widthDp = 390, heightDp = 2200, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun AppsDark() = HubTheme { Cards() }

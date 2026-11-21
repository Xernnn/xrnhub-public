package com.xern.xrnhub.ui

// The Overview's alerts and the whole page, made-up data, for rendering without a phone or a server:
//   android preview render "?:app" --fqn=com.xern.xrnhub.ui.OverviewPreviewKt.AlertsLight
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
import com.xern.xrnhub.data.Incident

// What's wrong, a chip each: a service down and the battery low, the phone out of reach, the monitor quiet.
@Composable
private fun Wrong() {
    Column(Modifier.background(colors.bg).padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        val problems = listOf(Incident("down", "xrntube", Fake.nowS - 420), Incident("battery", "", Fake.nowS - 1800, detail = "18%"))
        Alerts(Fake.now.copy(problems = problems), 0, Fake.nowMs)
        Alerts(Fake.now, 3, Fake.nowMs)
        Alerts(Fake.now.copy(at = Fake.nowMs - 400_000), 0, Fake.nowMs)
        // All well: nothing at all.
        Alerts(Fake.now, 0, Fake.nowMs)
    }
}

@Preview(widthDp = 390, heightDp = 260)
@Composable
fun AlertsLight() = HubTheme { Wrong() }

@Preview(widthDp = 390, heightDp = 260, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun AlertsDark() = HubTheme { Wrong() }

// The whole Overview page, on the last 24 hours.
@Composable
private fun Whole() = PageFrame { OverviewBody(Fake.now, Fake.ring, 0, Fake.nowMs, Fake.history, "24h", {}, nowhere) }

@Preview(widthDp = 390, heightDp = 3000)
@Composable
fun OverviewLight() = Whole()

@Preview(widthDp = 390, heightDp = 3000, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun OverviewDark() = Whole()

package com.xern.xrnhub.ui

// The Logs page, made-up data (PreviewData.kt), for rendering without a phone or a server:
//   android preview render "?:app" --fqn=com.xern.xrnhub.ui.LogsPreviewKt.LogsLight
// Debug only, since @Preview comes with ui-tooling, which only the debug build has.

import android.content.res.Configuration
import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Composable
private fun Logs() = PageFrame {
    LogsBody("24h", {}, "", {}, Fake.logStats(null), "info", {}, false, {}, "", {}, Fake.logs(null).lines, "")
}

@Composable
private fun NoLogs() = PageFrame {
    LogsBody("7d", {}, "xrntube", {}, Fake.logStats("xrntube"), "error", {}, true, {}, "stale", {}, emptyList(), "None")
}

@Preview(widthDp = 390, heightDp = 2300)
@Composable
fun LogsLight() = Logs()

@Preview(widthDp = 390, heightDp = 1300)
@Composable
fun LogsEmptyLight() = NoLogs()

package com.xern.xrnhub.ui

// The Data page, made-up data (PreviewData.kt), for rendering without a phone or a server:
//   android preview render "?:app" --fqn=com.xern.xrnhub.ui.DataPreviewKt.DataLight
// Debug only, since @Preview comes with ui-tooling, which only the debug build has.

import android.content.res.Configuration
import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Composable
private fun Data() = PageFrame { DataBody(Fake.data) }

@Preview(widthDp = 390, heightDp = 2300)
@Composable
fun DataLight() = Data()

@Preview(widthDp = 390, heightDp = 2300, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun DataDark() = Data()

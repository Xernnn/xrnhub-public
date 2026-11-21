package com.xern.xrnhub.ui

// The Security page, made-up data (PreviewData.kt), for rendering without a phone or a server:
//   android preview render "?:app" --fqn=com.xern.xrnhub.ui.SecurityPreviewKt.SecurityLight
// Debug only, since @Preview comes with ui-tooling, which only the debug build has.

import android.content.res.Configuration
import androidx.compose.runtime.Composable
import androidx.compose.ui.tooling.preview.Preview

@Composable
private fun Security() = PageFrame { SecurityBody(Fake.security) }

@Preview(widthDp = 390, heightDp = 1150)
@Composable
fun SecurityLight() = Security()

@Preview(widthDp = 390, heightDp = 1150, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun SecurityDark() = Security()

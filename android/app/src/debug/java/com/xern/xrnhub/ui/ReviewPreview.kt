package com.xern.xrnhub.ui

// The Review page and a day's sheet, made-up data (PreviewData.kt), for rendering without a phone or a server:
//   android preview render "?:app" --fqn=com.xern.xrnhub.ui.ReviewPreviewKt.ReviewLight
// Debug only, since @Preview comes with ui-tooling, which only the debug build has.

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview

@Composable
private fun Review() = PageFrame { ReviewBody(Fake.review) }

@Composable
private fun Day() = PageFrame {
    Box(Modifier.background(colors.surface)) {
        DayContent(Fake.day.day, Fake.review.year.first().day, Fake.day.day, Fake.day, null) {}
    }
}

@Preview(widthDp = 390, heightDp = 1900)
@Composable
fun ReviewLight() = Review()

@Preview(widthDp = 390, heightDp = 1900, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun ReviewDark() = Review()

@Preview(widthDp = 390, heightDp = 760)
@Composable
fun DayLight() = Day()

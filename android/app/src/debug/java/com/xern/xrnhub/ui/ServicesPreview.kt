package com.xern.xrnhub.ui

// The Services page, a service's and xrnfit's, made-up data, for rendering without a phone or a server:
//   android preview render "?:app" --fqn=com.xern.xrnhub.ui.ServicesPreviewKt.ServicesLight
// Debug only, since @Preview comes with ui-tooling, which only the debug build has.

import android.content.res.Configuration
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.padding
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
// The Services page, one service's page, and xrnfit's.
@Composable
private fun Listing() = PageFrame { ServicesBody(Fake.now, Fake.servicesDoc, nowhere) }

@Composable
private fun One() = PageFrame {
    ServiceDetailBody("xrnrule", Fake.now, Fake.history, Fake.servicesDoc, Fake.logStats("xrnrule"), "24h", {}, null, false, {}, nowhere)
}

@Composable
private fun Fit() = PageFrame { FitDetailBody(Fake.now, Fake.history, Fake.servicesDoc, Fake.logStats("xrnfit"), "24h", {}, nowhere) }

@Preview(widthDp = 390, heightDp = 1150)
@Composable
fun ServicesLight() = Listing()

@Preview(widthDp = 390, heightDp = 2200)
@Composable
fun ServiceLight() = One()

@Preview(widthDp = 390, heightDp = 1500)
@Composable
fun FitDetailLight() = Fit()

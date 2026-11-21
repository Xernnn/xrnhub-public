package com.xern.xrnhub.ui

// The app's own name and face, made-up data, for rendering without a phone or a server:
//   android preview render "?:app" --fqn=com.xern.xrnhub.ui.BrandPreviewKt.TopLight
// The top bar and sign-in page, every text style with an "@" in it (the static MiSans Latin Bold
// once drew it as "&"), and the launcher icon's layers as a launcher masks them.
// Debug only, since @Preview comes with ui-tooling, which only the debug build has.

import android.content.res.Configuration
import android.widget.ImageView
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.requiredSize
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.ColorFilter
import androidx.compose.ui.graphics.Shape
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import com.xern.xrnhub.R
import com.xern.xrnhub.data.Incident
import com.xern.xrnhub.data.incidentLanes

@Composable
private fun Top() = HubTheme {
    Column(Modifier.background(colors.bg)) {
        TopBar {}
        Box(Modifier.fillMaxWidth().height(12.dp))
    }
}

@Preview(widthDp = 390, heightDp = 64)
@Composable
fun TopLight() = Top()

@Preview(widthDp = 390, heightDp = 64, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun TopDark() = Top()

@Composable
private fun Sign() = PageFrame {
    SignInBody("https://hub.example.test", {}, "owner", {}, "secret", {}, false, "Wrong username or password", true, {})
}

@Preview(widthDp = 390, heightDp = 1000)
@Composable
fun SignInLight() = Sign()

@Preview(widthDp = 390, heightDp = 1000, uiMode = Configuration.UI_MODE_NIGHT_YES)
@Composable
fun SignInDark() = Sign()

private const val SAMPLE = "owner@example.test 0123456789"

@Composable
private fun Fonts() = PageFrame {
    val p = colors
    val styles: List<Pair<String, TextStyle>> = listOf(
        "body" to Type.body, "small" to Type.small, "tiny" to Type.tiny, "label" to Type.label, "cardTitle" to Type.cardTitle,
        "value" to Type.value, "hero" to Type.hero, "heading" to Type.heading, "number" to Type.number, "code" to Type.code,
        "m3 bodyLarge" to MaterialTheme.typography.bodyLarge, "m3 labelLarge" to MaterialTheme.typography.labelLarge,
        "m3 titleSmall" to MaterialTheme.typography.titleSmall, "m3 headlineMedium" to MaterialTheme.typography.headlineMedium,
    )
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        styles.forEach { (name, style) ->
            Text(name, style = Type.tiny, color = p.muted)
            Text(SAMPLE, style = style, color = p.text, maxLines = 1)
        }
        listOf(FontWeight.Normal, FontWeight.Medium, FontWeight.SemiBold, FontWeight.Bold, FontWeight.ExtraBold).forEach { weight ->
            Text("${weight.weight}  @ 1,234", style = Type.heading.copy(fontWeight = weight), color = p.text)
        }
    }
}

@Preview(widthDp = 390, heightDp = 1100)
@Composable
fun FontsLight() = Fonts()

/** The two layers at 108 dp, of which a launcher shows the middle 72 dp through its mask. */
@Composable
private fun Masked(shape: Shape) {
    Box(Modifier.size(72.dp).clip(shape), Alignment.Center) {
        Image(painterResource(R.drawable.ic_launcher_background), null, Modifier.requiredSize(108.dp))
        Image(painterResource(R.drawable.ic_launcher_foreground), null, Modifier.requiredSize(108.dp))
    }
}

@Composable
private fun Launcher() = PageFrame {
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(16.dp)) {
        Row(horizontalArrangement = Arrangement.spacedBy(16.dp), verticalAlignment = Alignment.CenterVertically) {
            Masked(CircleShape)
            Masked(RoundedCornerShape(24))
            Masked(RoundedCornerShape(12.dp))
            // Themed icons: the launcher tints the monochrome layer on a pale disc.
            Box(Modifier.size(72.dp).clip(CircleShape).background(Color(0xFFDCE3F0)), Alignment.Center) {
                Image(
                    painterResource(R.drawable.ic_launcher_monochrome), null, Modifier.requiredSize(108.dp),
                    colorFilter = ColorFilter.tint(Color(0xFF2B3A55)),
                )
            }
        }
        // The real adaptive icon, as Android itself draws it.
        AndroidView({ ImageView(it).apply { setImageResource(R.mipmap.ic_launcher) } }, Modifier.size(108.dp))
        // The layers unmasked, with the 66 dp safe zone ringed.
        Box(Modifier.size(108.dp), Alignment.Center) {
            Image(painterResource(R.drawable.ic_launcher_background), null, Modifier.requiredSize(108.dp))
            Image(painterResource(R.drawable.ic_launcher_foreground), null, Modifier.requiredSize(108.dp))
            Box(Modifier.size(66.dp).clip(CircleShape).background(Color(0x33FFFFFF)))
        }
    }
}

@Preview(widthDp = 390, heightDp = 360)
@Composable
fun LauncherLight() = Launcher()

/** Places that name another app in a sentence: the banner on a problem, and the incidents. */
@Composable
private fun Names() = PageFrame {
    Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Alerts(Fake.now.copy(problems = listOf(Incident("down", "xrntube", Fake.nowS - 400))), 0, Fake.nowMs)
        HubCard("Incidents") { IncidentTimeline(incidentLanes(Fake.servicesDoc.timeline.orEmpty(), Fake.names)) }
    }
}

@Preview(widthDp = 390, heightDp = 420)
@Composable
fun NamesLight() = Names()

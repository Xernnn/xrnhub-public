package com.xern.xrnhub.ui

import android.content.pm.PackageManager
import android.os.Build
import androidx.annotation.DrawableRes
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.offset
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Icon
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalUriHandler
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.pm.PackageInfoCompat
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LifecycleEventEffect
import com.xern.xrnhub.R
import com.xern.xrnhub.data.AppsDoc
import com.xern.xrnhub.data.HubApp
import com.xern.xrnhub.data.Installed
import com.xern.xrnhub.data.Fact
import com.xern.xrnhub.data.appsInOrder
import com.xern.xrnhub.data.factsOf
import com.xern.xrnhub.data.newerRelease
import com.xern.xrnhub.data.updateWords
import java.net.URI
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext

@DrawableRes
private fun appLogo(name: String): Int? = when (name) {
    "xrncal" -> R.drawable.logo_xrncal
    "xrnloops" -> R.drawable.logo_xrnloops
    "xrnrule" -> R.drawable.logo_xrnrule
    "xrnwal" -> R.drawable.logo_xrnwal
    "xrnhome" -> R.drawable.logo_xrnhome
    "xrnphotos" -> R.drawable.logo_xrnphotos
    "xrnfit" -> R.drawable.logo_xrnfit
    "xrnmail" -> R.drawable.logo_xrnmail
    "xrntube" -> R.drawable.logo_xrntube
    "xrnsoc" -> R.drawable.logo_xrnsoc
    "xrnsheet" -> R.drawable.logo_xrnsheet
    "xrninsta" -> R.drawable.logo_xrninsta
    "xrnchips" -> R.drawable.logo_xrnchips
    "xrnhub" -> R.drawable.logo_xrnhub
    "home-server" -> R.drawable.logo_home_server
    else -> null
}

@DrawableRes
private fun placeIcon(place: String): Int? = when (place) {
    "Phone" -> R.drawable.ic_place_phone
    "Laptop" -> R.drawable.ic_place_laptop
    "Browser" -> R.drawable.ic_place_browser
    "Server" -> R.drawable.ic_tab_server
    "Telegram" -> R.drawable.ic_place_telegram
    else -> null
}

/**
 * Each package's version on this phone. Android 11 on answers only for the packages the manifest's
 * <queries> names; one it can't see reads as not installed.
 */
private fun installedOn(pm: PackageManager, packages: List<String>): Map<String, Installed> =
    packages.mapNotNull { name ->
        val info = try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU) {
                pm.getPackageInfo(name, PackageManager.PackageInfoFlags.of(0))
            } else {
                @Suppress("DEPRECATION")
                pm.getPackageInfo(name, 0)
            }
        } catch (_: PackageManager.NameNotFoundException) {
            null
        }
        info?.let { name to Installed(it.versionName.orEmpty(), PackageInfoCompat.getLongVersionCode(it)) }
    }.toMap()

@Composable
fun AppsPage() {
    val doc = rememberDoc<AppsDoc>("api/apps")
    val context = LocalContext.current
    var installed by remember { mutableStateOf(emptyMap<String, Installed>()) }
    // Read again each time the app comes back to the front: an update may have gone in meanwhile.
    var resumes by remember { mutableIntStateOf(0) }
    LifecycleEventEffect(Lifecycle.Event.ON_RESUME) { resumes += 1 }
    val packages = doc.value?.apps?.mapNotNull { it.packageName }.orEmpty()
    LaunchedEffect(packages, resumes) {
        installed = withContext(Dispatchers.IO) { installedOn(context.packageManager, packages) }
    }
    val data = doc.value ?: return Waiting(doc.failed)
    val address = LocalModel.current.session?.address

    Page(doc.loading, doc::refresh) {
        appsInOrder(data).forEach { app ->
            item(key = "app-${app.name}") { AppCard(app, app.packageName?.let { installed[it] }, address) }
        }
    }
}

@Composable
private fun AppLogo(name: String, size: Dp) {
    val logo = appLogo(name)
    if (logo == null) {
        Box(Modifier.size(size).clip(RoundedCornerShape(size * 0.25f)).background(colors.surface2))
    } else {
        Image(painterResource(logo), null, Modifier.size(size).clip(RoundedCornerShape(size * 0.25f)))
    }
}

@OptIn(ExperimentalLayoutApi::class)
@Composable
internal fun AppCard(app: HubApp, installed: Installed?, address: String?) {
    val p = colors
    val uri = LocalUriHandler.current
    HubCard {
        Row(verticalAlignment = Alignment.CenterVertically) {
            AppLogo(app.name, 44.dp)
            Spacer(Modifier.width(12.dp))
            Column(Modifier.weight(1f)) {
                Text(app.name, style = Type.heading.copy(fontSize = 18.sp, lineHeight = 22.sp), color = p.text)
                Spacer(Modifier.height(4.dp))
                FlowRow(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    app.runs.forEach { Place(it) }
                }
            }
        }
        Facts(app, installed)
        Row(Modifier.fillMaxWidth().padding(top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Visibility(app.isPublic)
            Spacer(Modifier.weight(1f))
            val page = app.web?.let { path -> address?.let { runCatching { URI(it).resolve(path).toString() }.getOrNull() } }
            // Out by the link's own padding, so its words line up with the facts above.
            Row(Modifier.offset(x = 10.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                // openUri throws when nothing on the phone opens links; a tap that does nothing is better.
                if (page != null) CardLink("Open", filled = true) { runCatching { uri.openUri(page) } }
                if (app.repoUrl.isNotEmpty()) CardLink("GitHub") { runCatching { uri.openUri(app.repoUrl) } }
            }
        }
    }
}

@Composable
private fun Place(place: String) {
    val p = colors
    Row(
        Modifier.clip(RoundedCornerShape(50)).background(p.surface2).padding(start = 6.dp, end = 8.dp, top = 2.dp, bottom = 2.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        placeIcon(place)?.let {
            Icon(painterResource(it), null, Modifier.size(12.dp), tint = p.muted)
            Spacer(Modifier.width(4.dp))
        }
        Text(place, style = Type.label, color = p.muted)
    }
}

@Composable
private fun Visibility(isPublic: Boolean) {
    val p = colors
    val shape = RoundedCornerShape(50)
    val base = Modifier.clip(shape)
    Text(
        if (isPublic) "Public repo" else "Private repo",
        (if (isPublic) base.border(1.dp, p.outline, shape) else base.background(p.surface2)).padding(horizontal = 8.dp, vertical = 2.dp),
        style = Type.label,
        color = if (isPublic) p.text else p.muted,
    )
}

@Composable
private fun CardLink(text: String, filled: Boolean = false, onClick: () -> Unit) {
    val p = colors
    Text(
        text,
        Modifier
            .clip(RoundedCornerShape(9.dp))
            .background(if (filled) p.surface2 else Color.Transparent)
            .clickable(onClick = onClick)
            .padding(horizontal = 10.dp, vertical = 6.dp),
        style = Type.label.copy(fontSize = 13.sp),
        color = if (filled) p.text else p.muted,
    )
}

@Composable
private fun Facts(app: HubApp, installed: Installed?) {
    val p = colors
    val rows = factsOf(app, installed)
    val news = newerRelease(app, installed)
    if (rows.isEmpty()) return
    Spacer(Modifier.height(10.dp))
    Box(Modifier.fillMaxWidth().height(1.dp).background(p.line))
    Spacer(Modifier.height(6.dp))
    rows.forEach { FactLine(it) }
    if (news != null && installed != null) {
        Text(
            updateWords(news),
            Modifier.fillMaxWidth().padding(top = 6.dp).clip(RoundedCornerShape(10.dp)).background(p.warnBg)
                .padding(horizontal = 10.dp, vertical = 7.dp),
            style = Type.body,
            color = p.warn,
            fontWeight = FontWeight.Medium,
        )
    }
}

@Composable
private fun FactLine(fact: Fact) {
    val p = colors
    Row(Modifier.fillMaxWidth().padding(vertical = 3.dp), verticalAlignment = Alignment.CenterVertically) {
        Text(fact.label, style = Type.small, color = p.muted)
        Spacer(Modifier.width(12.dp))
        Row(Modifier.weight(1f), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.CenterVertically) {
            fact.dot?.let {
                Dot(it)
                Spacer(Modifier.width(6.dp))
            }
            fact.logo?.takeIf(::hasLogo)?.let {
                Logo(it, 16.dp)
                Spacer(Modifier.width(6.dp))
            }
            Text(fact.value, style = Type.number, color = if (fact.bad) p.bad else p.text, textAlign = TextAlign.End)
        }
    }
}

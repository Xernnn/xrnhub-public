package com.xern.xrnhub.ui

import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.Immutable
import androidx.compose.runtime.ReadOnlyComposable
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.ExperimentalTextApi
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontVariation
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.sp
import com.xern.xrnhub.R

/** The web page's tokens, so the two look like one product in either mode. */
@Immutable
data class Palette(
    val dark: Boolean,
    val bg: Color,
    val surface: Color,
    val surface2: Color,
    val line: Color,
    val outline: Color,
    val text: Color,
    val muted: Color,
    val faint: Color,
    val accent: Color,
    val onAccent: Color,
    val bad: Color,
    val badBg: Color,
    val good: Color,
    val goodBg: Color,
    val warn: Color,
    val warnBg: Color,
    val series: Color,
    val levels: List<Color>,
    /** For pairs that aren't services: in and out, CPU and battery. */
    val charts: List<Color>,
)

private val Light = Palette(
    dark = false,
    bg = Color(0xFFF2F2F4),
    surface = Color(0xFFFFFFFF),
    surface2 = Color(0xFFEBEBEF),
    line = Color(0xFFE3E3E8),
    outline = Color(0xFFC9C9CF),
    text = Color(0xFF1B1B1E),
    muted = Color(0xFF6B6B73),
    faint = Color(0xFFC9C9CF),
    accent = Color(0xFF16A34A),
    onAccent = Color(0xFFFFFFFF),
    bad = Color(0xFFDC2626),
    badBg = Color(0xFFFDE3E3),
    good = Color(0xFF15803D),
    goodBg = Color(0xFFDCFCE7),
    warn = Color(0xFFA16207),
    warnBg = Color(0xFFFEF3C7),
    series = Color(0xFF475569),
    levels = listOf(Color(0xFFEBEBEF), Color(0xFFC9ECD4), Color(0xFF8FD8A7), Color(0xFF45B976), Color(0xFF15803D)),
    // In/Out and Battery/CPU: hues no service's logo uses (web/style.css --pair-1, --pair-2).
    charts = listOf(Color(0xFF0891B2), Color(0xFF9C36B5)),
)

private val Dark = Palette(
    dark = true,
    bg = Color(0xFF1F1F23),
    surface = Color(0xFF2B2B30),
    surface2 = Color(0xFF3A3A40),
    line = Color(0xFF37373C),
    outline = Color(0xFF55555C),
    text = Color(0xFFF4F4F5),
    muted = Color(0xFFAAAAAC),
    faint = Color(0xFF4A4A50),
    accent = Color(0xFF22C55E),
    onAccent = Color(0xFF0B2A16),
    bad = Color(0xFFF04242),
    badBg = Color(0xFF4A1F1F),
    good = Color(0xFF4ADE80),
    goodBg = Color(0xFF143C24),
    warn = Color(0xFFFFD60A),
    warnBg = Color(0xFF423814),
    series = Color(0xFF94A3B8),
    levels = listOf(Color(0xFF3A3A40), Color(0xFF1F4A31), Color(0xFF1F7043), Color(0xFF2AA360), Color(0xFF4ADE80)),
    charts = listOf(Color(0xFF1098AD), Color(0xFFCC5DE8)),
)

val LocalPalette = staticCompositionLocalOf { Light }

val colors: Palette
    @Composable
    @ReadOnlyComposable
    get() = LocalPalette.current

/**
 * MiSans Latin's official variable font, one file for every weight. The static Bold and Heavy draw
 * "@" as "&"; the variable font doesn't. Each weight is the named instance the app's static files
 * were (Regular 330 … Bold 630), so nothing else changes.
 */
@OptIn(ExperimentalTextApi::class)
private fun misans(weight: FontWeight, wght: Int) =
    Font(R.font.misans_vf, weight, variationSettings = FontVariation.Settings(FontVariation.weight(wght)))

val MiSans = FontFamily(
    misans(FontWeight.Normal, 330),
    misans(FontWeight.Medium, 380),
    misans(FontWeight.SemiBold, 450),
    misans(FontWeight.Bold, 520),
    misans(FontWeight.ExtraBold, 630),
)

/** Digits the same width, so a number that changes every few seconds doesn't shake. */
private const val TABULAR = "tnum"

object Type {
    val body = TextStyle(fontFamily = MiSans, fontSize = 14.sp, lineHeight = 20.sp)
    val small = TextStyle(fontFamily = MiSans, fontSize = 12.sp, lineHeight = 16.sp, fontFeatureSettings = TABULAR)
    val tiny = TextStyle(fontFamily = MiSans, fontSize = 11.sp, lineHeight = 14.sp, fontFeatureSettings = TABULAR)
    val label = TextStyle(fontFamily = MiSans, fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Medium)
    val cardTitle = TextStyle(fontFamily = MiSans, fontSize = 14.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold)
    val value = TextStyle(fontFamily = MiSans, fontSize = 22.sp, lineHeight = 28.sp, fontWeight = FontWeight.SemiBold, fontFeatureSettings = TABULAR)
    val hero = TextStyle(fontFamily = MiSans, fontSize = 34.sp, lineHeight = 40.sp, fontWeight = FontWeight.Bold, fontFeatureSettings = TABULAR)
    val heading = TextStyle(fontFamily = MiSans, fontSize = 20.sp, lineHeight = 26.sp, fontWeight = FontWeight.Bold)
    val number = TextStyle(fontFamily = MiSans, fontSize = 13.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold, fontFeatureSettings = TABULAR)
    /** Log lines, paths and addresses: MiSans like the rest, with digits that line up. */
    val code = TextStyle(fontFamily = MiSans, fontSize = 11.sp, lineHeight = 15.sp, fontFeatureSettings = TABULAR)
}

// Every Material style, so a dialog's buttons or a text field's label never fall back to Roboto.
private val materialType = Typography().run {
    copy(
        displayLarge = displayLarge.copy(fontFamily = MiSans),
        displayMedium = displayMedium.copy(fontFamily = MiSans),
        displaySmall = displaySmall.copy(fontFamily = MiSans),
        headlineLarge = headlineLarge.copy(fontFamily = MiSans),
        headlineMedium = headlineMedium.copy(fontFamily = MiSans),
        headlineSmall = headlineSmall.copy(fontFamily = MiSans),
        titleLarge = titleLarge.copy(fontFamily = MiSans),
        titleMedium = titleMedium.copy(fontFamily = MiSans),
        titleSmall = titleSmall.copy(fontFamily = MiSans),
        bodyLarge = bodyLarge.copy(fontFamily = MiSans),
        bodyMedium = bodyMedium.copy(fontFamily = MiSans),
        bodySmall = bodySmall.copy(fontFamily = MiSans),
        labelLarge = labelLarge.copy(fontFamily = MiSans),
        labelMedium = labelMedium.copy(fontFamily = MiSans, fontWeight = FontWeight.Medium),
        labelSmall = labelSmall.copy(fontFamily = MiSans),
    )
}

@Composable
fun HubTheme(content: @Composable () -> Unit) {
    val palette = if (isSystemInDarkTheme()) Dark else Light
    val scheme = (if (palette.dark) darkColorScheme() else lightColorScheme()).copy(
        primary = palette.series,
        onPrimary = palette.surface,
        secondaryContainer = palette.surface2,
        onSecondaryContainer = palette.text,
        background = palette.bg,
        onBackground = palette.text,
        surface = palette.surface,
        onSurface = palette.text,
        surfaceVariant = palette.surface2,
        onSurfaceVariant = palette.muted,
        surfaceContainer = palette.surface,
        surfaceContainerLow = palette.surface,
        surfaceContainerHigh = palette.surface,
        surfaceContainerHighest = palette.surface2,
        outline = palette.outline,
        outlineVariant = palette.line,
        error = palette.bad,
    )
    CompositionLocalProvider(LocalPalette provides palette) {
        MaterialTheme(colorScheme = scheme, typography = materialType, content = content)
    }
}

/** A service's chart colour in light and in dark mode: one hue, a lighter step where dark needs it. */
private class ServiceColor(val light: Color, val dark: Color = light)

private val SYSTEM_COLOR = ServiceColor(Color(0xFF52525B), Color(0xFFA1A1AA))

/**
 * Each service's logo tile colour (web/apps/), the same map as the web page's style.css tokens. In
 * dark mode the four dark tiles step up to just past 3:1 on the card, and xrntube and xrnnote step
 * up with them so each family's pair stays apart; xrnhub steps above the system grey.
 */
private val SERVICE_COLORS = mapOf(
    "calendar" to ServiceColor(Color(0xFF308FFE)),
    "xrnloops" to ServiceColor(Color(0xFF037200), Color(0xFF2C8B27)),
    "xrnrule" to ServiceColor(Color(0xFFE86C05)),
    "xrnwal" to ServiceColor(Color(0xFF7B9C17)),
    "xrninsta" to ServiceColor(Color(0xFF592AC4), Color(0xFF7F5DF6)),
    "xrnfit" to ServiceColor(Color(0xFFA20129), Color(0xFFD54451)),
    "xrnhome" to ServiceColor(Color(0xFF6C6104), Color(0xFF847A29)),
    "xrntube" to ServiceColor(Color(0xFFFF2238), Color(0xFFFF847E)),
    "xrnhub" to ServiceColor(Color(0xFF8B8F9B), Color(0xFFC0C4D1)),
    "xrnnote" to ServiceColor(Color(0xFFB68A04), Color(0xFFCDA02F)),
    "xrnimg" to ServiceColor(Color(0xFF0F889F)),
    "tailscale" to SYSTEM_COLOR,
    "ssh" to SYSTEM_COLOR,
)

/**
 * For red-green colour blindness the tiles of a lightness run together: xrnwal's yellow-green with
 * xrntube's red, xrnnote's yellow and xrnrule's orange, and xrnfit's heart red with xrnloops' and
 * xrnhome's dark greens. Among several lines xrnwal's and xrnfit's are dashed so those still tell
 * apart. Stacks keep them apart by SERVICE_ORDER: other services sit between them.
 */
private val DASHED_SERVICES = setOf("xrnwal", "xrnfit")

/** A service's colour in a chart, its logo's, the same one wherever it appears. */
@Composable
@ReadOnlyComposable
fun colorOf(service: String): Color =
    SERVICE_COLORS[service]?.let { if (colors.dark) it.dark else it.light } ?: colors.series

fun dashedOf(service: String): Boolean = service in DASHED_SERVICES

private val HABITS_LIGHT = listOf(
    0xFFD32F2F, 0xFFE64A19, 0xFFF57C00, 0xFFFF8F00, 0xFFF9A825, 0xFFAFB42B, 0xFF7CB342, 0xFF388E3C,
    0xFF00897B, 0xFF00ACC1, 0xFF039BE5, 0xFF1976D2, 0xFF303F9F, 0xFF5E35B1, 0xFF8E24AA, 0xFFD81B60,
    0xFF5D4037, 0xFF27272A, 0xFF757575, 0xFF9E9E9E,
)
private val HABITS_DARK = listOf(
    0xFFEF9A9A, 0xFFFFAB91, 0xFFFFCC80, 0xFFFFECB3, 0xFFFFF59D, 0xFFE6EE9C, 0xFFC5E1A5, 0xFF69F0AE,
    0xFF80CBC4, 0xFF80DEEA, 0xFF81D4FA, 0xFF64B5F6, 0xFF9FA8DA, 0xFFB39DDB, 0xFFCE93D8, 0xFFF48FB1,
    0xFFBCAAA4, 0xFFF5F5F5, 0xFFE0E0E0, 0xFF9E9E9E,
)
private const val HABIT_FALLBACK = 18

/** xrnloops' habit colours, so a habit looks the same in both apps. */
@Composable
@ReadOnlyComposable
fun habitColor(index: Int?): Color {
    val set = if (colors.dark) HABITS_DARK else HABITS_LIGHT
    return Color(set.getOrElse(index ?: HABIT_FALLBACK) { set[HABIT_FALLBACK] })
}

/** A calendar's colour, as `#rrggbb` or `#rrggbbaa`; calendars keep the colour set for them. */
fun calendarColor(text: String?): Color? {
    val hex = text?.removePrefix("#")?.take(6)?.takeIf { it.length == 6 } ?: return null
    return hex.toLongOrNull(16)?.let { Color(0xFF000000 or it) }
}

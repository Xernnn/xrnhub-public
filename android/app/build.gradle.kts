import java.util.Properties

plugins {
    // Kotlin is built into AGP 9: no kotlin-android plugin.
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.compose)
    alias(libs.plugins.kotlin.serialization)
}

// The server's address stays out of the repo: it comes from local.properties, which git ignores,
// and only fills in the sign-in page.
val machine = Properties().apply {
    rootProject.file("local.properties").takeIf { it.exists() }?.inputStream()?.use(::load)
}

fun filled(name: String): String = (findProperty(name) ?: machine.getProperty(name) ?: "").toString()

android {
    namespace = "com.xern.xrnhub"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.xern.xrnhub"
        minSdk = 26
        targetSdk = 35
        versionCode = 12
        versionName = "0.3.0"
        buildConfigField("String", "DEFAULT_ADDRESS", "\"${filled("hub.address")}\"")
        buildConfigField("String", "DEFAULT_USER", "\"${filled("hub.user")}\"")
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            isShrinkResources = true
            proguardFiles(getDefaultProguardFile("proguard-android-optimize.txt"))
            // The same key as the debug build, so this updates an installed copy and keeps its data.
            signingConfig = signingConfigs.getByName("debug")
        }
    }

    buildFeatures {
        compose = true
        buildConfig = true
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
}

dependencies {
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.lifecycle.runtime.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.activity.compose)
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.compose.ui)
    implementation(libs.androidx.compose.material3)
    implementation(libs.kotlinx.coroutines.android)
    implementation(libs.kotlinx.serialization.json)
    debugImplementation(libs.androidx.compose.ui.tooling)
    testImplementation(libs.junit)
}

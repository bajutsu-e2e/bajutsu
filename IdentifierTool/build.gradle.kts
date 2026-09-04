// A standalone Android library giving bajutsu a stable `resource-id` / `content-desc` (BE-0405). It
// has no dependency on BajutsuAndroid (BE-0233's clipboard/network-capture library) and BajutsuAndroid
// has none on it, so a consumer wanting identifiers alone never links either. Consumed as a Gradle
// module (the showcase includes it by path; see settings.gradle.kts). Plugin versions come from the
// including build's root `plugins {}` block, so this declares no versions of its own.
plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.bajutsu.identifier"
    compileSdk = 35

    defaultConfig {
        minSdk = 26
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    // The Compose helpers are plain extension functions, not @Composable ones, so this needs no
    // Compose compiler plugin. compileOnly keeps a Views-only consumer's build free of Compose
    // entirely, and lets a Compose consumer bring its own version via its own BOM.
    compileOnly("androidx.compose.ui:ui:1.7.3")
}

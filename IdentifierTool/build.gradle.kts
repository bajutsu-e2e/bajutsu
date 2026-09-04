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
        // A Views-only consumer never puts androidx.compose.ui on its classpath (the dependency
        // below is compileOnly), so its R8 pass would otherwise fail on the Compose helpers'
        // "missing classes" — this keep file tells R8 those references are expected to be absent.
        consumerProguardFiles("consumer-rules.pro")
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
    // entirely, and lets a Compose consumer bring its own version via its own BOM. Pinned to what
    // the showcase's own compose-bom:2024.09.02 resolves androidx.compose.ui:ui to — compiling
    // against a newer Compose than a real consumer's runtime classpath would risk a
    // NoSuchMethodError the fast gate cannot catch (there is no Kotlin compile step in it).
    compileOnly("androidx.compose.ui:ui:1.7.2")
}

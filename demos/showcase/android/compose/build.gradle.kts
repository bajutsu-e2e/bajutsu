plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    namespace = "com.bajutsu.showcase.android.compose"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.bajutsu.showcase.android.compose"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    // SPEC §8: the ACCESSIBLE twin. `a11y` defines BuildConfig.ACCESSIBLE (Modifier.aid attaches a
    // testTag surfaced as resource-id); `noax` leaves it false, so the tree carries no ids — the
    // honest "we skipped accessibility" app that `record` must cope with and `doctor` flags Blocked.
    flavorDimensions += "accessibility"
    productFlavors {
        create("a11y") {
            dimension = "accessibility"
            buildConfigField("boolean", "ACCESSIBLE", "true")
            manifestPlaceholders["appLabel"] = "Showcase Compose"
            manifestPlaceholders["deeplinkScheme"] = "showcasecompose"
        }
        create("noax") {
            dimension = "accessibility"
            applicationIdSuffix = ".noax"
            buildConfigField("boolean", "ACCESSIBLE", "false")
            manifestPlaceholders["appLabel"] = "Showcase Compose (no a11y)"
            manifestPlaceholders["deeplinkScheme"] = "showcasecomposenoax"
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
    kotlinOptions {
        jvmTarget = "17"
    }
}

dependencies {
    implementation("androidx.core:core-ktx:1.13.1")
    implementation("androidx.activity:activity-compose:1.9.2")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")

    val composeBom = platform("androidx.compose:compose-bom:2024.09.02")
    implementation(composeBom)
    implementation("androidx.compose.ui:ui")
    implementation("androidx.compose.material3:material3")
    implementation("androidx.compose.material:material-icons-core")
    implementation("androidx.compose.runtime:runtime")
}

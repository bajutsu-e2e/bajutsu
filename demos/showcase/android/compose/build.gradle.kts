plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
    id("org.jetbrains.kotlin.plugin.compose")
}

android {
    // The build namespace must match the Kotlin source package (where BuildConfig/R are generated and
    // where the manifest's relative `.MainActivity` resolves); the installed applicationId keeps the
    // `.android.` segment the showcase.config.yaml `package` targets, so they intentionally differ.
    namespace = "com.bajutsu.showcase.compose"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.bajutsu.showcase.android.compose"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        // The instrumentation runner for the generated UI Automator codegen test (BE-0294), the
        // Android twin of the checked-in XCUITest fixture. Only the a11y flavor carries the ids the
        // test queries by name, so `make -C demos/showcase/android e2e-codegen` runs it there.
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
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
    implementation(project(":bajutsu-android")) // clipboard + network test support (BE-0233 / BE-0283)
    implementation("com.squareup.okhttp3:okhttp:4.12.0") // Net.kt uses OkHttp so BajutsuNet can observe it
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

    // The generated UI Automator codegen test (BE-0294) drives UiDevice / UiObject2 and asserts with
    // JUnit — the same androidx.test + UI Automator stack the resident server uses (BE-0245), plus
    // androidx.test:core for the ApplicationProvider the generated `launch(...)` reads.
    androidTestImplementation("androidx.test:core:1.6.1")
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
}

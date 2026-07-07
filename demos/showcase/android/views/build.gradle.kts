plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    // The build namespace must match the Kotlin source package (where BuildConfig is generated and
    // where the manifest's relative `.MainActivity` etc. resolve); the installed applicationId keeps
    // the `.android.` segment the showcase.config.yaml `package` targets, so they intentionally differ.
    namespace = "com.bajutsu.showcase.views"
    compileSdk = 35

    defaultConfig {
        applicationId = "com.bajutsu.showcase.android.views"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
    }

    // SPEC §8: the ACCESSIBLE twin, same as the compose module. `a11y` assigns every view its
    // ids.xml resource id (surfaced by UI Automator as resource-id); `noax` compiles the helper to a
    // no-op, so the tree carries no ids — the doctor-Blocked / record-ladder demo.
    flavorDimensions += "accessibility"
    productFlavors {
        create("a11y") {
            dimension = "accessibility"
            buildConfigField("boolean", "ACCESSIBLE", "true")
            manifestPlaceholders["appLabel"] = "Showcase Views"
            manifestPlaceholders["deeplinkScheme"] = "showcaseviews"
        }
        create("noax") {
            dimension = "accessibility"
            applicationIdSuffix = ".noax"
            buildConfigField("boolean", "ACCESSIBLE", "false")
            manifestPlaceholders["appLabel"] = "Showcase Views (no a11y)"
            manifestPlaceholders["deeplinkScheme"] = "showcaseviewsnoax"
        }
    }

    buildFeatures {
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
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("androidx.lifecycle:lifecycle-runtime-ktx:2.8.6")
    implementation("org.jetbrains.kotlinx:kotlinx-coroutines-android:1.8.1")
}

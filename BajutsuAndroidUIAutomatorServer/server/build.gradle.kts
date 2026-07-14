// The resident server ships as an androidTest instrumentation: `am instrument -w` keeps a blocking
// @Test alive, and that live UiAutomation session is exactly what answers reads cheaply (no
// per-invocation startup, unlike `uiautomator dump`). The main sourceset is a near-empty host app —
// instrumentation needs a target package, but the server drives whatever app is under test, so this
// app has no UI of its own.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.bajutsu.android.server"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.bajutsu.android.server"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
        testInstrumentationRunner = "androidx.test.runner.AndroidJUnitRunner"
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
    // The whole server is androidx.test + UI Automator over a raw socket — no HTTP or JSON library,
    // so the instrumentation APK stays small and dependency-light.
    androidTestImplementation("androidx.test:runner:1.6.2")
    androidTestImplementation("androidx.test.ext:junit:1.2.1")
    androidTestImplementation("androidx.test.uiautomator:uiautomator:2.3.0")
}

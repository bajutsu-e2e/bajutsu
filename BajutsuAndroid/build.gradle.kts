// The Android peer of BajutsuKit (iOS): a reusable, test/debug-only library that gives bajutsu an
// in-app foothold for capabilities the platform only exposes from the app process. Today it backs
// the clipboard (BE-0233): Android 10+ lets only the foreground app / default IME touch the
// clipboard, so bajutsu drives it through an in-app receiver rather than a shell command. Consumed
// as a Gradle module (the showcase includes it by path; see settings.gradle.kts). Plugin versions
// come from the including build's root `plugins {}` block, so this declares no versions of its own.
plugins {
    id("com.android.library")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.bajutsu.android"
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
    // ContextCompat.registerReceiver picks the right RECEIVER_EXPORTED handling across API levels.
    implementation("androidx.core:core-ktx:1.13.1")
    // BajutsuNet's interceptor exposes OkHttp types (BE-0283), but only apps that already use OkHttp
    // call it, so they bring the runtime dependency — compileOnly keeps the library from pinning a
    // version onto its consumers. okio is OkHttp's transitive I/O layer, used for the request-body copy.
    compileOnly("com.squareup.okhttp3:okhttp:4.12.0")
    compileOnly("com.squareup.okio:okio:3.9.0")
}

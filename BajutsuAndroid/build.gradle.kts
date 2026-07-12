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
}

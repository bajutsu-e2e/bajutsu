plugins {
    id("com.android.application")
    // The Flutter Gradle Plugin must be applied after the Android and Kotlin Gradle plugins.
    id("dev.flutter.flutter-gradle-plugin")
}

android {
    namespace = "com.bajutsu.showcase.showcase_flutter"
    compileSdk = flutter.compileSdkVersion
    ndkVersion = flutter.ndkVersion

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    defaultConfig {
        // The installed applicationId keeps the `.android.` segment the showcase.config.yaml
        // `package` targets, so it intentionally differs from the Kotlin source namespace above.
        applicationId = "com.bajutsu.showcase.android.flutter"
        minSdk = maxOf(flutter.minSdkVersion, 23) // POST_NOTIFICATIONS runtime prompt needs API 33+ at run time
        targetSdk = flutter.targetSdkVersion
        versionCode = flutter.versionCode
        versionName = flutter.versionName
    }

    // SPEC §8: the ACCESSIBLE twin, mirroring the Compose flavors. The Dart `--dart-define=ACCESSIBLE`
    // gates whether `Semantics(identifier:)` is attached; the flavor here differentiates the installed
    // applicationId, label, and deeplink scheme so the a11y and noax builds coexist on one device.
    flavorDimensions += "accessibility"
    productFlavors {
        create("a11y") {
            dimension = "accessibility"
            manifestPlaceholders["appLabel"] = "Showcase Flutter"
            manifestPlaceholders["deeplinkScheme"] = "showcaseflutter"
        }
        create("noax") {
            dimension = "accessibility"
            applicationIdSuffix = ".noax"
            manifestPlaceholders["appLabel"] = "Showcase Flutter (no a11y)"
            manifestPlaceholders["deeplinkScheme"] = "showcaseflutternoax"
        }
    }

    buildTypes {
        release {
            // TODO: Add your own signing config for the release build.
            // Signing with the debug keys for now, so `flutter run --release` works.
            signingConfig = signingConfigs.getByName("debug")
        }
    }
}

kotlin {
    compilerOptions {
        jvmTarget = org.jetbrains.kotlin.gradle.dsl.JvmTarget.JVM_17
    }
}

flutter {
    source = "../.."
}

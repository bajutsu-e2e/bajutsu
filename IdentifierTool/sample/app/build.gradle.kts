// The minimal shape any app adopting IdentifierTool writes: one dependency, no other bajutsu
// library, no gate of its own (see IdentifierTool/README.md for why every function tags
// unconditionally). Views only — demos/showcase/android/compose shows the Compose half.
plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "dev.bajutsu.identifier.sample"
    compileSdk = 35

    defaultConfig {
        applicationId = "dev.bajutsu.identifier.sample"
        minSdk = 26
        targetSdk = 35
        versionCode = 1
        versionName = "1.0"
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
    implementation(project(":identifier-tool"))
}

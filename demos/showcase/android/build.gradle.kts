// Plugin versions declared once for both modules; each module applies the ones it needs.
// The `views` module skips the Compose compiler plugin.
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
    id("org.jetbrains.kotlin.plugin.compose") version "2.0.20" apply false
}

// Plugin versions declared once; the single `:server` module applies the ones it needs. This build
// has no Compose module (the resident server is a headless instrumentation, not a UI app), so unlike
// the showcase build it declares no Compose compiler plugin.
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
}

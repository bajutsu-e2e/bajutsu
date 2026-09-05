// Plugin versions declared once, pinned to the same versions demos/showcase/android's own root
// build.gradle.kts uses — the toolchain IdentifierTool is verified against in CI.
// com.android.library is here for IdentifierTool/build.gradle.kts: it applies that plugin with no
// version of its own, relying on the including build's root `plugins {}` block to supply one.
plugins {
    id("com.android.application") version "8.7.3" apply false
    id("com.android.library") version "8.7.3" apply false
    id("org.jetbrains.kotlin.android") version "2.0.20" apply false
}

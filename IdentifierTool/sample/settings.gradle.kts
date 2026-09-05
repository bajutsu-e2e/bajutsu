// A minimal, standalone Android app demonstrating IdentifierTool with no other bajutsu library
// involved — the shape any third-party app adopting IdentifierTool would use. Its own Gradle root,
// distinct from demos/showcase/android's larger fixture, which shows the Compose half instead
// (demos/showcase/android/compose) alongside BajutsuAndroid and BajutsuZOrder.
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "IdentifierToolSample"

include(":app")

// IdentifierTool itself, included by path exactly as README.md documents — one directory up from
// this sample's own root.
include(":identifier-tool")
project(":identifier-tool").projectDir = file("..")

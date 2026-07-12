// Two sibling modules mirror the iOS pair (ios/swiftui/, ios/uikit/): `compose` is the SwiftUI twin
// (testTag → resource-id via testTagsAsResourceId), `views` the UIKit twin (android:id →
// resource-id). Both build an a11y and a noax flavor — the ACCESSIBLE compile-flag pair. See
// ../SPEC.md and the BE-0007 Android-backend roadmap item.
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

rootProject.name = "BajutsuShowcaseAndroid"

include(":compose", ":views")

// The reusable app-side test-support library (BE-0233), living at the repo root like iOS's
// BajutsuKit. Included by path (not published), so the showcase pair can embed its clipboard
// receiver; any other app would include it the same way (or publish it as an aar).
include(":bajutsu-android")
project(":bajutsu-android").projectDir = file("../../../BajutsuAndroid")

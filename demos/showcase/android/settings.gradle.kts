// Two sibling modules mirror the iOS pair (swiftui/, uikit/): `compose` is the SwiftUI twin
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

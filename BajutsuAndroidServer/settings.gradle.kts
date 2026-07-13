// The resident UI Automator server (BE-0245): a self-contained instrumentation that bajutsu installs
// on the target device to answer hierarchy reads over a local socket, the way Appium's UiAutomator2
// server does. It is app-independent — it drives whatever app is under test — so it lives on its own
// here rather than being included into any app's build (contrast BajutsuAndroid, the app-embedded
// clipboard library the showcase includes by path). See the BE-0245 roadmap item.
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

rootProject.name = "BajutsuAndroidServer"

include(":server")

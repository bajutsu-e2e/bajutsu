# Build targets for the resident UI Automator server (BE-0245). Invoke from anywhere with
# `make -C BajutsuAndroidUIAutomatorServer <target>`.
#
# Not part of `make check`: the Python gate builds no Kotlin, and this needs the Android SDK. Like
# the showcase Android build, it goes through the committed `./gradlew`, so a fresh clone needs no
# system-installed gradle. CI wiring (an e2e lane that installs and exercises the server on an
# emulator) is a later BE-0245 slice; today these targets only build.
.PHONY: build server-apk test-apk wrapper clean

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
GRADLEW := $(HERE)/gradlew

# Both artefacts: the host app APK plus the instrumentation (androidTest) APK that carries the
# resident server. bajutsu installs both, then keeps the server alive with `am instrument -w`.
build: server-apk test-apk

# The near-empty host app — instrumentation needs a target package to attach to.
server-apk:
	cd $(HERE) && $(GRADLEW) :server:assembleDebug

# The instrumentation APK holding ResidentServerTest (the resident server itself).
test-apk:
	cd $(HERE) && $(GRADLEW) :server:assembleDebugAndroidTest

# The wrapper jar IS committed (like any standard Android repo), so a fresh clone builds with no
# extra step. Only run this to bump the pinned Gradle version — it rewrites the tracked
# gradlew/properties and would otherwise dirty the tree.
GRADLE_VERSION := 8.9
wrapper:
	cd $(HERE) && $(GRADLEW) wrapper --gradle-version $(GRADLE_VERSION)

clean:
	cd $(HERE) && $(GRADLEW) clean

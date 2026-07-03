# Build targets for the Android showcase pair — four products from two codebases
# (Compose × Views, each in an accessibility-on / -off flavor). See ../SPEC.md §2.
# Invoke from anywhere with `make -C demos/showcase/android <target>`.
#
# `run` / `doctor` land with the BE-0007 adb backend; until then these targets only build.
.PHONY: build-all wrapper \
	compose-build compose-noax-build views-build views-noax-build clean

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
GRADLEW := $(HERE)/gradlew

# The wrapper jar IS committed (like any standard Android repo), so a fresh clone builds with no
# extra step. Only run this to bump the pinned Gradle version — never as part of a build, since it
# rewrites the tracked gradlew/properties and would dirty the tree. It goes through the committed
# `./gradlew` (not a system-installed gradle), so no extra tool is needed.
GRADLE_VERSION := 8.9
wrapper:
	cd $(HERE) && $(GRADLEW) wrapper --gradle-version $(GRADLE_VERSION)

build-all: compose-build compose-noax-build views-build views-noax-build

# Each build lands at <module>/build/outputs/apk/<flavor>/debug/<module>-<flavor>-debug.apk,
# exactly where showcase.config.yaml's android targets point their appPath.
compose-build:
	cd $(HERE) && $(GRADLEW) :compose:assembleA11yDebug
compose-noax-build:
	cd $(HERE) && $(GRADLEW) :compose:assembleNoaxDebug
views-build:
	cd $(HERE) && $(GRADLEW) :views:assembleA11yDebug
views-noax-build:
	cd $(HERE) && $(GRADLEW) :views:assembleNoaxDebug

clean:
	rm -rf $(HERE)/build $(HERE)/compose/build $(HERE)/views/build $(HERE)/.gradle

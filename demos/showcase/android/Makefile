# Build targets for the Android showcase pair — four products from two codebases
# (Compose × Views, each in an accessibility-on / -off flavor). See ../SPEC.md §2.
# Invoke from anywhere with `make -C demos/showcase/android <target>`.
#
# `run` / `doctor` land with the BE-0007 adb backend; until then these targets only build.
.PHONY: build-all wrapper \
	compose-build compose-noax-build views-build views-noax-build e2e clean

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
GRADLEW := $(HERE)/gradlew
ROOT   := $(abspath $(HERE)/../../..)
CONFIG := $(HERE)/../showcase.config.yaml
# The attached emulator/device serial; `booted` resolves to the first ready one (adb.resolve_serial).
SERIAL ?= booted
# The core id/tap/type/value scenarios BE-0007 confirmed pass on device (2026-07-07): id resolution,
# taps, text entry, and value assertions against the Compose a11y twin (dotted SPEC ids verbatim).
# The deferred slices (gestures, multi-touch, scroll, notices, device control, mocked network,
# visual/golden) stay out until their on-device work lands (BE-0007 follow-ups; BE-0208 units 4-5).
E2E_SCENARIOS := smoke firstlook search components data_driven modals relaunch system

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

# On-device e2e for the Compose a11y twin over the adb backend (BE-0007). Builds the APK — bajutsu's
# launch installs it from the config's appPath — then runs each passing scenario with a clean state
# (the default erase = `pm clear`). Not part of `make check`: it needs a booted AVD, so it is driven
# by .github/workflows/android-e2e.yml (Linux + KVM), the Android twin of the idb and web e2e lanes.
e2e: compose-build
	cd $(ROOT) && for s in $(E2E_SCENARIOS); do \
		echo "=== $$s ==="; \
		uv run bajutsu run --scenario demos/showcase/scenarios/$$s.yaml \
			--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG) || exit 1; \
	done

clean:
	rm -rf $(HERE)/build $(HERE)/compose/build $(HERE)/views/build $(HERE)/.gradle

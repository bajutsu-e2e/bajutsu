# Build targets for the Android showcase pair — four products from two codebases
# (Compose × Views, each in an accessibility-on / -off flavor). See ../SPEC.md §2.
# Invoke from anywhere with `make -C demos/showcase/android <target>`.
#
# `run` / `doctor` land with the BE-0007 adb backend; until then these targets only build.
.PHONY: build-all wrapper \
	compose-build compose-noax-build views-build views-noax-build e2e e2e-golden e2e-visual clean

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))
GRADLEW := $(HERE)/gradlew
ROOT   := $(abspath $(HERE)/../../..)
CONFIG := $(HERE)/../showcase.config.yaml
# The attached emulator/device serial; `booted` resolves to the first ready one (adb.resolve_serial).
SERIAL ?= booted
# The core id/tap/type/value scenarios BE-0007 confirmed pass on device (2026-07-07): id resolution,
# taps, text entry, and value assertions against the Compose a11y twin (dotted SPEC ids verbatim).
# BE-0223 taught the adb driver to drive the native tab bar (a Compose NavigationBarItem resolves the
# shared cross-backend selector `{ label, traits: [button] }`), so the tab-switching scenarios rejoin
# the lane: `search` / `data_driven` (Search), `relaunch` (Log), `system` (Permissions) — all verified
# on device. `navigation` (BE-0208 unit 5) never leaves the launch (Stable) tab: tap a catalog row to
# push Horse Detail, toggle the favorite, then pop back via the cross-backend `back` step (Android
# system key) — detail-screen assertions and back-navigation. The golden element-tree check is a
# separate target (`e2e-golden`, BE-0208 unit 4).
#
# The sheet/cover flows `components` / `modals` rejoin the lane (BE-0208 unit 5): they pass on the
# local hardware-accelerated arm64 emulator but their 5s sheet-open waits risk timing out on the CI
# x86_64 software renderer, which draws a modal more slowly. The shared scenarios can't be retuned
# per backend, so instead this lane raises the wait *ceiling* via BAJUTSU_MIN_WAIT_TIMEOUT (below):
# a condition wait returns the instant the sheet presents, so the larger floor never slows the flow
# — it only gives the software renderer time before the step is failed.
#
# The deep-scroll flows `controls` / `notices` rejoin the lane (BE-0208 unit 5). Both scroll a far
# target into view — `controls` the segmented-control value node below the buttons, `notices` a list
# row that starts well below the fold. They failed before because the default directional swipe
# travelled a fixed coordinate count, which scrolls far less of Android's dense screen (2400px) than
# of iOS's (~900pt); the default is now a screen fraction (bajutsu/orchestrator/actions/handlers/
# gestures.py), so a swipe reaches the same proportion on either backend. Verified on a local arm64
# emulator.
#
# The single-touch gesture flow `gestures` rejoins the lane (BE-0208 unit 5): its long-press already
# landed, but the double-tap did not register through adb's `input tap ; input tap` (each tap spawns
# a fresh `input` JVM, so the inter-tap gap overruns the platform double-tap window even chained in
# one round-trip). The adb driver now drives a double-tap with a raw `sendevent` touch sequence on a
# rooted device (the `e2e` target roots the emulator below), which fires both taps inside the window;
# on a non-rooted device it falls back to `input tap` unchanged. Verified on a local arm64 emulator.
#
# The runtime-permission flow `permission_android` joins the lane (BE-0208 unit 6, exercising
# BE-0210's up-front grant). It is the deterministic Android twin of the idb `permission.yaml`: rather
# than tapping the out-of-process prompt through the vision alert guard, showcase-compose grants
# POST_NOTIFICATIONS up front (`grantPermissions` → `pm grant`), so the RequestPermission contract
# short-circuits to granted with no dialog — no LLM, no fixed sleep on the CI lane. Verified on a
# local arm64 emulator.
#
# The device-control flow `device_android` joins the lane (BE-0208 unit 5): it overrides the GPS
# location (`emu geo fix`) and round-trips the clipboard, then re-asserts the settled screen — the
# deterministic Android twin of the idb `device.yaml`. It runs on the Stable launch tab, needing no
# tab-bar navigation. Both setLocation and clipboard of the device-control family are exercised:
# `cmd clipboard` is a no-op on-device, so the clipboard runs through the in-app receiver the showcase
# embeds from BajutsuAndroid (BE-0233) — the read-back is the strong assertion PR #934 wanted.
# Verified on a local arm64 emulator.
#
# The multi-touch gesture flow `gestures_multitouch` joins the lane (BE-0232): the adb driver now
# drives a pinch / rotate as a raw two-slot `sendevent` sweep on the rooted emulator (two contacts
# moving together across interleaved frames), so the shared scenario that iOS runs on XCUITest runs
# unchanged on Android. Unlike the single-touch `gestures` double-tap there is no `input` fallback —
# a two-finger gesture cannot be approximated — so it requires root (the `e2e` target runs `adb root`)
# and fails loudly otherwise. Verified on a local arm64 emulator.
#
# Still held out: mocked network (a BE-0007 follow-up).
E2E_SCENARIOS := smoke firstlook navigation search data_driven relaunch system components modals \
	controls notices gestures gestures_multitouch permission_android device_android

# Raise the condition-wait ceiling for this lane only: the CI x86_64 software-rendered emulator
# presents sheets/covers slower than the shared scenarios' 5s waits allow. A wait returns as soon as
# it is satisfied, so this is a safe upper bound, not a fixed delay (bajutsu/orchestrator/waits.py).
E2E_WAIT_FLOOR ?= 15

# The second Android UI toolkit (BE-0221): Compose surfaces the dotted SPEC id verbatim, Views maps
# it to underscores (`android:id` allows neither `.` nor `-`), and each scenario's selectors list both
# forms (`id: [stable.refresh, stable_refresh]`) so one file resolves against either tree. Running the
# shared scenarios on Views too turns "the shared scenarios run unchanged on Android" from a claim
# true for one toolkit into a guarantee checked for both, on every push/PR. Views runs the subset
# whose selectors carry both id forms and that is verified on the Views tree; `components` / `modals`
# stay Compose-only until their id forms and modal timing are confirmed on Views (a BE-0221 follow-up).
E2E_VIEWS_SCENARIOS := smoke firstlook navigation search data_driven relaunch system

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

# On-device e2e for both a11y twins over the adb backend (BE-0007 / BE-0221). Builds each APK —
# bajutsu's launch installs it from the config's appPath — then runs each passing scenario with a
# clean state (the default erase = `pm clear`): Compose over the full set, Views over the id-form-
# verified subset. Not part of `make check`: it needs a booted AVD, so it is driven by
# .github/workflows/android-e2e.yml (Linux + KVM), the Android twin of the idb and web e2e lanes.
# `adb root` restarts adbd as root so the `gestures` double-tap can write /dev/input via `sendevent`
# (the google_apis image allows it; the driver falls back to `input tap` when it cannot). `wait-for-
# device` blocks until the restarted adbd reconnects before the first scenario runs. Both act on the
# single attached AVD (this lane, like the local flow, only ever has one) rather than `-s $(SERIAL)`.
e2e: compose-build views-build
	adb root && adb wait-for-device
	cd $(ROOT) && for s in $(E2E_SCENARIOS); do \
		echo "=== showcase-compose / $$s ==="; \
		BAJUTSU_MIN_WAIT_TIMEOUT=$(E2E_WAIT_FLOOR) \
		uv run bajutsu run --scenario demos/showcase/scenarios/$$s.yaml \
			--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG) || exit 1; \
	done
	cd $(ROOT) && for s in $(E2E_VIEWS_SCENARIOS); do \
		echo "=== showcase-views / $$s ==="; \
		BAJUTSU_MIN_WAIT_TIMEOUT=$(E2E_WAIT_FLOOR) \
		uv run bajutsu run --scenario demos/showcase/scenarios/$$s.yaml \
			--target showcase-views --udid $(SERIAL) --backend android --config $(CONFIG) || exit 1; \
	done

# On-device golden element-tree assertion (BE-0006, BE-0208 unit 4) for the Compose a11y twin over
# adb. The Stable catalog's normalized tree is pinned in
# demos/showcase/scenarios/golden/goldens/lists_android.json — a backend-specific baseline (the adb
# tree differs from idb's lists.json and XCUITest's controls.json; each backend renders a distinct
# accessibility tree). The comparison is field-level with a tolerant frame check (bajutsu/golden.py),
# so the baseline recorded on the local arm64 emulator also passes on the CI x86_64 one: identity /
# label / traits are stable across the ABI, only the density-scaled frame differs and it is only
# sanity-checked. The scenario runs on the Stable tab (the launch tab), so it needs no tab-bar
# navigation. The goldens dir resolves by default to `goldens/` beside the scenario, so no --goldens
# flag is needed. Not part of `make check`: it needs a booted AVD, so android-e2e.yml drives it in
# the same emulator session as `e2e`.
e2e-golden: compose-build
	cd $(ROOT) && uv run bajutsu run \
		--scenario demos/showcase/scenarios/golden/golden_android.yaml \
		--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG)

# On-device visual regression (VRT, BE-0208 unit 4 visual dimension) for the Compose a11y twin over
# adb: pixel-compare the Stable catalog against the committed baseline
# demos/showcase/scenarios/visual/baselines_android/stable.png. Unlike the element-tree golden, a
# pixel baseline is host-specific — the CI x86_64 software renderer (swiftshader) and a local arm64
# emulator diverge per pixel — so the baseline is RECORDED ON CI and committed (see that dir's
# README); this target reads it via --baselines. Runs on the Stable (launch) tab, so it needs no
# tab-bar navigation. Not part of `make check`: it needs a booted AVD, so android-e2e.yml drives it
# in the same emulator session as `e2e` / `e2e-golden`. The pixel compare needs Pillow, so this
# pulls in the `visual` extra (the element-tree golden does not) — the adb backend itself has none.
e2e-visual: compose-build
	cd $(ROOT) && uv run --extra visual bajutsu run \
		--scenario demos/showcase/scenarios/visual/visual_android.yaml \
		--baselines demos/showcase/scenarios/visual/baselines_android \
		--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG)

clean:
	rm -rf $(HERE)/build $(HERE)/compose/build $(HERE)/views/build $(HERE)/.gradle

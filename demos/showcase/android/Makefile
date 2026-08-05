# Build targets for the Android showcase pair — four products from two codebases
# (Compose × Views, each in an accessibility-on / -off flavor). See ../SPEC.md §2.
# Invoke from anywhere with `make -C demos/showcase/android <target>`.
#
# `run` / `doctor` land with the BE-0007 adb backend; until then these targets only build.
.PHONY: build-all wrapper \
	compose-build compose-noax-build views-build views-noax-build e2e e2e-network e2e-golden e2e-fallback \
	e2e-visual e2e-conformance e2e-fault-injection e2e-codegen clean

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
# Every on-device target sets that floor, not just the scenario lanes. `e2e-golden`, `e2e-fallback`,
# and `e2e-visual` were left without it, so their first wait — the Stable catalog's `stable.row.1`,
# the same one `smoke` opens with — got the scenario's bare 10s while `e2e` gave it 15s. The app's
# first render after launch outruns 10s on this emulator often enough that both jobs failed on it
# with `step 0 (wait): wait timeout`, on a warm AVD, while the scenario lanes passed the identical
# wait. The floor is a ceiling on a condition wait, so a fast render still returns at once.
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
# The runtime-permission flow `permission` joins the lane (BE-0208 unit 6, exercising BE-0210's
# up-front grant). This is the *same* permission.yaml the iOS lane runs: the grant mechanism is in
# config, not the scenario, so one file serves both. showcase-compose grants POST_NOTIFICATIONS up
# front (`grantPermissions` → `pm grant`), so the RequestPermission contract short-circuits to granted
# with no dialog; the scenario's `systemAlertHandling` guard therefore never fires here — no LLM, no fixed
# sleep on the CI lane (on iOS, where notifications can't be pre-granted, that guard taps "Allow"
# instead). Verified on a local arm64 emulator.
#
# The device-control flow `device` joins the lane (BE-0208 unit 5): it overrides the GPS location
# (`emu geo fix`) and round-trips the clipboard, then re-asserts the settled screen. This is the *same*
# device.yaml the iOS lane runs — `setLocation` and the clipboard are advertised by every device
# backend (clipboard via simctl on iOS, the in-app BajutsuAndroid receiver on Android, BE-0233), so one
# file is preflight-clean on iOS and Android alike (the iOS-only `push` half lives in push.yaml). It
# runs on the Stable launch tab, needing no tab-bar navigation. `cmd clipboard` is a no-op on-device,
# so the clipboard runs through the receiver the showcase embeds — the read-back is the strong
# assertion PR #934 wanted. Verified on a local arm64 emulator.
#
# The multi-touch gesture flow `gestures_multitouch` joins the lane (BE-0232): the adb driver now
# drives a pinch / rotate as a raw two-slot `sendevent` sweep on the rooted emulator (two contacts
# moving together across interleaved frames), so the shared scenario that iOS runs on XCUITest runs
# unchanged on Android. Unlike the single-touch `gestures` double-tap there is no `input` fallback —
# a two-finger gesture cannot be approximated — so it requires root (the `e2e` target runs `adb root`)
# and fails loudly otherwise. Verified on a local arm64 emulator.
#
# The scenario-authoring flows `extract` / `foreach` join the lane (BE-0285): the web slice (#1184)
# proved them on Playwright, and this is their adb twin. `extract` captures the Log counter's live
# value and reuses it (Log tab, exercised by `relaunch` already); `foreach` iterates the five Stable
# rows and opens each detail, so the tree mutates between iterations — the push/pop a frozen fake
# cannot represent (the whole point of BE-0285). Both reuse the Stable/Log instruments the lane
# already drives, need no app change, and carry both id forms so they run on Views too.
#
# The text-editing flow (select/copy/clear/delete, text_editing.yaml) is deliberately absent: the
# Compose / Views trees do not expose an editable field's text as its `value`, so `clear` is a no-op
# and the per-edit checks have nothing to read here. That scenario is iOS-only (see its header); a
# count-only Android twin for select/copy/delete is a possible follow-up.
#
# Observed network capture lands in its own `e2e-network` target below (BE-0283); mocked network
# (deterministic stub responses) stays a follow-up.
E2E_SCENARIOS := smoke firstlook navigation search data_driven relaunch system components modals \
	controls notices gestures gestures_multitouch permission device extract foreach

# Raise the condition-wait ceiling for this lane only: the CI x86_64 software-rendered emulator
# presents sheets/covers slower than the shared scenarios' 5s waits allow. A wait returns as soon as
# it is satisfied, so this is a safe upper bound, not a fixed delay (bajutsu/orchestrator/waits.py).
E2E_WAIT_FLOOR ?= 15

# Quiet by default. Debug here writes a line per read, which measurably lengthened the lane, and the
# investigation it was added for is over: it named the double tap's interval as the cause, and the two
# things worth hearing about — the read-lag barrier spending its budget, and the settle poll falling
# through its deadline — are warnings, so they still print. Raise it for the next timing puzzle:
# `make -C demos/showcase/android e2e E2E_LOG_LEVEL=DEBUG`.
E2E_LOG_LEVEL ?= WARNING
E2E_ENV = BAJUTSU_MIN_WAIT_TIMEOUT=$(E2E_WAIT_FLOOR) BAJUTSU_LOG_LEVEL=$(E2E_LOG_LEVEL)

# The second Android UI toolkit (BE-0221): Compose surfaces the dotted SPEC id verbatim, Views maps
# it to underscores (`android:id` allows neither `.` nor `-`), and each scenario's selectors list both
# forms (`id: [stable.refresh, stable_refresh]`) so one file resolves against either tree. Running the
# shared scenarios on Views too turns "the shared scenarios run unchanged on Android" from a claim
# true for one toolkit into a guarantee checked for both, on every push/PR. Views runs the subset
# whose selectors carry both id forms and that is verified on the Views tree; `components` / `modals`
# stay Compose-only until their id forms and modal timing are confirmed on Views (a BE-0221 follow-up).
E2E_VIEWS_SCENARIOS := smoke firstlook navigation search data_driven relaunch system extract foreach

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
# .github/workflows/android-e2e.yml (Linux + KVM), the Android twin of the iOS and web e2e lanes.
# `adb root` restarts adbd as root so the `gestures` double-tap can write /dev/input via `sendevent`
# (the google_apis image allows it; the driver falls back to `input tap` when it cannot). `wait-for-
# device` blocks until the restarted adbd reconnects before the first scenario runs. Both act on the
# single attached AVD (this lane, like the local flow, only ever has one) rather than `-s $(SERIAL)`.
# Suppress the system's crash / "isn't responding" dialogs for the emulator session.
#
# A cold-created AVD loads its snapshot into a thrashing system, and the Pixel Launcher regularly
# misses its ANR deadline while the first scenario is launching. The dialog that follows takes the
# foreground window, so the run's very first `wait` polls the dialog's tree instead of the app and
# times out — with the app fully drawn behind it. That failure took this lane four times on one PR.
#
# Selecting "Close app" would clear one dialog; `hide_error_dialogs` stops them being posted at all,
# which is the same intent without a coordinate tap on a system surface whose button labels are
# locale-dependent. The app under test is unaffected: the setting governs the system's error UI, not
# whether a process is killed, so a genuine app ANR still ends the run — it just ends it with the
# app's own failure rather than with a dialog the scenario cannot see past.
#
# The broadcast that follows closes one that is already up: the setting only governs dialogs posted
# after it, so a lane that starts with the launcher already wedged would otherwise still open on a
# blocked foreground window. `hide_error_dialogs=1` should hold for the rest of the emulator's
# session once set, but a multi-scenario lane (`e2e`, `run-flutter-android`) calls it again before
# every scenario in its loop rather than trust that once at the top of the job — each call is two
# fast adb round trips, and the alternative is re-diagnosing a mid-loop dialog from a bare timeout.
#
# The tap-based counterpart is showcase.config.yaml's per-target `interrupts`: it covers a
# `bajutsu run` invoked outside any make target, which this setting therefore never reaches. It
# answers the locale half of the objection above — the selector is a resource id, not a button
# label — and still acts on the system surface (a node-level click on the resident channel, an
# `input tap` at the resolved node otherwise), which is acceptable because the *pair* of ids is
# exact: `aerr_wait` selects only the ANR dialog, and within it `aerr_close` names one node, so a
# resolve cannot reach `aerr_report`. Where this setting does reach, suppression stays preferred.
#
# The trailing echo reads the setting straight back rather than trusting the `put` succeeded: a
# lane that fails downstream on an ANR dialog anyway (`adb`'s own failure, a wrong scope, a device
# that dropped the setting) should say so in its own log line, not leave that ambiguity for whoever
# investigates the failure to resolve by re-running and guessing.
ANR_QUIET = adb shell settings put global hide_error_dialogs 1 && adb shell am broadcast -a android.intent.action.CLOSE_SYSTEM_DIALOGS >/dev/null && echo "ANR_QUIET: hide_error_dialogs=$$(adb shell settings get global hide_error_dialogs | tr -d '\r')"

# Draw every touch on screen, so the captured video answers "where did it actually land" directly
# rather than by inference from the log. `show_touches` is a surface-level effect the input system
# draws, adding no window; `pointer_location` adds a secure system overlay with a coordinate strip.
# The pair is the visual half of the diagnostics BAJUTSU_LOG_LEVEL turns on: the log says which frame
# a selector resolved to, and the video says which pixel the finger reached.
#
# On the scenario lanes only. `e2e-golden` / `e2e-fallback` compare an element tree and `e2e-visual`
# compares pixels against a committed baseline, so an overlay there is a false diff waiting to
# happen — those targets turn the pair off explicitly rather than trusting a fresh AVD's defaults,
# which also keeps a local run honest after someone has been debugging by hand.
TOUCH_DEBUG_ON = adb shell settings put system show_touches 1 && adb shell settings put system pointer_location 1
TOUCH_DEBUG_OFF = adb shell settings put system show_touches 0 && adb shell settings put system pointer_location 0

e2e: compose-build views-build
	adb root && adb wait-for-device
	$(TOUCH_DEBUG_ON)
	cd $(ROOT) && for s in $(E2E_SCENARIOS); do \
		echo "=== showcase-compose / $$s ==="; \
		$(ANR_QUIET); \
		$(E2E_ENV) \
		uv run bajutsu run --scenario demos/showcase/scenarios/$$s.yaml \
			--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG) || exit 1; \
	done
	cd $(ROOT) && for s in $(E2E_VIEWS_SCENARIOS); do \
		echo "=== showcase-views / $$s ==="; \
		$(ANR_QUIET); \
		$(E2E_ENV) \
		uv run bajutsu run --scenario demos/showcase/scenarios/$$s.yaml \
			--target showcase-views --udid $(SERIAL) --backend android --config $(CONFIG) || exit 1; \
	done

# On-device network-capture assertion (BE-0283): a `request` assertion observes real emulator traffic
# end to end — the showcase GETs SHOWCASE_API_URL/horses over OkHttp, BajutsuAndroid's interceptor
# reports the exchange to the host collector, and the runner bridges that collector to the emulator
# with `adb reverse` (the transport under test). net_stub.py stands up a throwaway host endpoint the
# emulator reaches at 10.0.2.2 (its host-loopback alias); the scenario points SHOWCASE_API_URL there.
# Backgrounded and trap-killed so the port never leaks past the run. Not part of `make check` (needs a
# booted AVD); android-e2e.yml drives it in its own job. Compose only — one toolkit proves the wire.
# must match SHOWCASE_API_URL in demos/showcase/scenarios/network_android.yaml — not override-safe.
# Keep the comment on its own line: an inline `# …` after a `:=` value leaves the whitespace before
# the `#` in the variable, so the port would carry trailing spaces into the curl URL and net_stub arg.
E2E_NET_STUB_PORT := 8770
e2e-network: compose-build
	adb wait-for-device
	$(ANR_QUIET)
	$(TOUCH_DEBUG_ON)
	cd $(ROOT) && ( python3 demos/showcase/android/net_stub.py $(E2E_NET_STUB_PORT) & \
		stub=$$!; trap "kill $$stub 2>/dev/null" EXIT; \
		ready=0; for i in $$(seq 1 50); do \
		  if curl -sf --max-time 1 -o /dev/null "http://127.0.0.1:$(E2E_NET_STUB_PORT)/"; then ready=1; break; fi; \
		  sleep 0.1; \
		done; \
		if [ "$$ready" -ne 1 ]; then echo "net_stub.py did not answer on port $(E2E_NET_STUB_PORT) within the readiness timeout" >&2; exit 1; fi; \
		$(E2E_ENV) \
		uv run bajutsu run --scenario demos/showcase/scenarios/network_android.yaml \
			--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG) )

# On-device golden element-tree assertion (BE-0006, BE-0208 unit 4) for the Compose a11y twin over
# adb. This runs the *same* golden.yaml the iOS lane runs; only the baseline dir differs. The adb tree
# differs from XCUITest's (each backend renders a distinct accessibility tree), so the
# Compose baseline — of the same name, lists.json — lives in its own dir,
# demos/showcase/scenarios/golden/goldens_android/, selected with `--goldens` (the iOS lane uses the
# default goldens/ beside the scenario). The comparison is field-level with a tolerant frame check
# (bajutsu/evidence/golden.py), so the baseline recorded on the local arm64 emulator also passes on the CI
# x86_64 one: identity / label / traits are stable across the ABI, only the density-scaled frame
# differs and it is only sanity-checked. The scenario runs on the Stable tab (the launch tab), so it
# needs no tab-bar navigation. Not part of `make check`: it needs a booted AVD, so android-e2e.yml
# drives it in its own `golden (adb)` job, which runs this target then `e2e-fallback` in one AVD boot.
e2e-golden: compose-build
	$(ANR_QUIET)
	$(TOUCH_DEBUG_OFF)
	cd $(ROOT) && $(E2E_ENV) uv run bajutsu run \
		--scenario demos/showcase/scenarios/golden/golden.yaml \
		--goldens demos/showcase/scenarios/golden/goldens_android \
		--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG)

# Guard the `uiautomator dump` fallback (BE-0245 unit 5). With the resident server built, reads are
# on the resident channel by default (bajutsu/platform_lifecycle.py), so `e2e` (the smoke job) and
# `e2e-golden` exercise that path; this target pins BAJUTSU_ADB_RESIDENT=0 to run the *same* golden
# through the dump path instead, so the fallback stays exercised on every run and the two paths are
# proven to yield the same element tree. Not part of `make check`: it needs a booted AVD, so
# android-e2e.yml drives it right after `e2e-golden` in the same `golden (adb)` job's AVD session.
e2e-fallback: compose-build
	$(ANR_QUIET)
	$(TOUCH_DEBUG_OFF)
	cd $(ROOT) && BAJUTSU_ADB_RESIDENT=0 $(E2E_ENV) uv run bajutsu run \
		--scenario demos/showcase/scenarios/golden/golden.yaml \
		--goldens demos/showcase/scenarios/golden/goldens_android \
		--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG)

# On-device visual regression (VRT, BE-0208 unit 4 visual dimension) for the Compose a11y twin over
# adb: pixel-compare the Stable catalog against the committed baseline
# demos/showcase/scenarios/visual/baselines_android/stable.png. Unlike the element-tree golden, a
# pixel baseline is host-specific — the CI x86_64 software renderer (swiftshader) and a local arm64
# emulator diverge per pixel — so the baseline is RECORDED ON CI and committed (see that dir's
# README); this target reads it via --baselines. Runs on the Stable (launch) tab, so it needs no
# tab-bar navigation. Not part of `make check`: it needs a booted AVD, so android-e2e.yml drives it
# in its own `visual (adb)` job (a separate AVD boot). The pixel compare needs Pillow, so this
# pulls in the `visual` extra (the element-tree golden does not) — the adb backend itself has none.
e2e-visual: compose-build
	$(ANR_QUIET)
	$(TOUCH_DEBUG_OFF)
	cd $(ROOT) && $(E2E_ENV) uv run --extra visual bajutsu run \
		--scenario demos/showcase/scenarios/visual/visual_android.yaml \
		--baselines demos/showcase/scenarios/visual/baselines_android \
		--target showcase-compose --udid $(SERIAL) --backend android --config $(CONFIG)

# On-device driver conformance (BE-0114 / BE-0270) for the adb backend: run the *same* backend-
# agnostic contract (tests/driver_conformance.py) the XCUITest / Playwright / FakeDriver suites
# run, against the Compose a11y twin. Unlike the other e2e targets this drives pytest, not the run
# CLI, so it needs the dev group (the CI job's `uv sync` keeps it); `launch_driver` installs the APK
# (built here) and boots the app straight into ConformanceScreen. `adb root` matches the `e2e` target:
# the MULTI_TOUCH capability test drives a pinch/rotate, which the driver actuates via a rooted
# `sendevent` sweep (BE-0232) — the google_apis image allows it and `adb root` enables it. `-n0` runs
# serially (the suite reseeds one shared emulator), and BAJUTSU_CONFORMANCE_SERIAL both routes the
# backend and un-skips the module. Not part of `make check`: it needs a booted AVD, so
# android-e2e.yml drives it in its own `conformance (adb)` job.
e2e-conformance: compose-build
	adb root && adb wait-for-device
	$(ANR_QUIET)
	$(TOUCH_DEBUG_ON)
	cd $(ROOT) && BAJUTSU_CONFORMANCE_SERIAL=$(SERIAL) \
		uv run pytest tests/test_driver_conformance_ondevice_android.py -m ondevice -n0 -v

# On-device fault injection (BE-0305) for the adb read path: put the display to sleep so the real read
# source serves a genuinely empty accessibility tree, and check that `CoordinateTreeDriver`'s
# transient-empty retry rides over it — the condition its unit test can only fabricate. Like
# `e2e-conformance` this drives pytest (so it needs the dev group) and runs serially; the display is
# left awake whatever happens. `TOUCH_DEBUG_OFF` because nothing here inspects touch overlays, and no
# `adb root`: the cases only read and tap. Not part of `make check` (it needs a booted AVD), and
# deliberately non-gating in CI — android-e2e.yml drives it in its own `fault-injection (adb)` job,
# outside the `E2E (Android)` aggregate, until it proves stable.
e2e-fault-injection: compose-build
	@[ -n "$(SERIAL)" ] || { echo "SERIAL is empty: the fault-injection module would skip and exit 0"; exit 1; }
	$(ANR_QUIET)
	$(TOUCH_DEBUG_OFF)
	cd $(ROOT) && BAJUTSU_FAULT_INJECTION_SERIAL=$(SERIAL) \
		uv run pytest tests/test_fault_injection_ondevice_android.py -m ondevice -n0 -v

# The codegen output path: a scenario -> native UI Automator (Kotlin) test, compiled and run by
# Gradle's connectedAndroidTest with no bajutsu runtime / adb driver / AI at test time (BE-0294) —
# the Android twin of the iOS `make -C demos/showcase ui-test` target. Re-generates the checked-in
# CodegenAndroidUITest.kt from the scenario first (so a stale check-in cannot mask an emitter or UI
# Automator API drift), then `connectedA11yDebugAndroidTest` builds the app + instrumentation APKs,
# installs both, and runs the generated test against the booted emulator. Compose only, matching the
# XCUITest gate's single-toolkit scope; the a11y flavor carries the ids the test queries by name. Not
# part of `make check`: it needs a booted AVD, so android-e2e.yml drives it in its own
# `uiautomator (codegen)` job (a separate AVD boot).
CODEGEN_SCENARIO := codegen_android
CODEGEN_KT := demos/showcase/android/compose/src/androidTest/java/CodegenAndroidUITest.kt
e2e-codegen:
	cd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/$(CODEGEN_SCENARIO).yaml \
		--emit uiautomator --target showcase-compose --config $(CONFIG) -o $(CODEGEN_KT)
	adb wait-for-device
	$(ANR_QUIET)
	# Unlike the bajutsu-driven jobs (whose scenarios set reinstall: clean, so AndroidEnvironment
	# uninstalls before installing), connectedAndroidTest installs incrementally with no such guard.
	# The CI AVD boot snapshot is cached and shared across jobs (android-e2e.yml), so a stale copy of
	# this package signed with another job's ephemeral debug keystore can already be resident;
	# Gradle's install then fails loudly with INSTALL_FAILED_UPDATE_INCOMPATIBLE instead of replacing
	# it. Uninstalling first is idempotent — a harmless no-op when neither package is present — and
	# guarantees a clean install regardless of what the cached snapshot's disk state carries.
	adb uninstall com.bajutsu.showcase.android.compose || true
	adb uninstall com.bajutsu.showcase.android.compose.test || true
	# On failure the generated test dumps the window hierarchy, a screenshot, and the accessibility
	# window list into the directory Gradle names with `additionalTestOutputDir` and copies back into
	# compose/build/outputs/connected_android_test_additional_output — no `adb pull` here, because
	# `adb` cannot read /sdcard/Android/data/<package> from Android 11 on. android-e2e.yml uploads it.
	cd $(HERE) && $(GRADLEW) :compose:connectedA11yDebugAndroidTest

clean:
	rm -rf $(HERE)/build $(HERE)/compose/build $(HERE)/views/build $(HERE)/.gradle

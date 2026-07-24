# Build / run targets for the showcase suite — four products from two codebases
# (UIKit × SwiftUI, each in an accessibility-on / -off variant). See SPEC.md.
# Invoke from anywhere with `make -C demos/showcase <target>`.
.PHONY: build-all \
	swiftui-gen swiftui-build swiftui-noax-build \
	uikit-gen uikit-build uikit-noax-build runner-build \
	swiftui-archive-device swiftui-ipa-device runner-build-device \
	run-swiftui run-uikit run-swiftui-tabs run-uikit-tabs \
	run-swiftui-noax run-uikit-noax doctor record ui-test ui-test-coverage \
	vrt vrt-approve vrt-swiftui vrt-swiftui-approve vrt-uikit vrt-uikit-approve \
	e2e-visual e2e-visual-approve \
	flutter-build flutter-android-build flutter-android-noax-build \
	run-flutter run-flutter-android clean

ROOT   := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HERE   := $(ROOT)/demos/showcase
CONFIG := $(HERE)/showcase.config.yaml
SWIFTUI := $(HERE)/ios/swiftui
UIKIT   := $(HERE)/ios/uikit
FLUTTER := $(HERE)/flutter
RUNNER  := $(ROOT)/BajutsuKit/Runner

# A built scheme lands at build/dd/Build/Products/Debug-iphonesimulator/<Scheme>.app,
# which is exactly the appPath each iOS targets.<name> entry in showcase.config.yaml points to.
# (The Android targets' APKs are built by demos/showcase/android/Makefile — see SPEC §2.1.)
define build_scheme
	cd $(1) && xcodegen generate
	cd $(1) && xcodebuild -project $(2).xcodeproj -scheme $(3) \
		-destination 'generic/platform=iOS Simulator' -derivedDataPath build/dd build
endef

build-all: swiftui-build swiftui-noax-build uikit-build uikit-noax-build

swiftui-gen:
	cd $(SWIFTUI) && xcodegen generate
swiftui-build:
	$(call build_scheme,$(SWIFTUI),BajutsuShowcaseSwiftUI,BajutsuShowcaseSwiftUI)
swiftui-noax-build:
	$(call build_scheme,$(SWIFTUI),BajutsuShowcaseSwiftUI,BajutsuShowcaseSwiftUINoAx)

uikit-gen:
	cd $(UIKIT) && xcodegen generate
uikit-build:
	$(call build_scheme,$(UIKIT),BajutsuShowcaseUIKit,BajutsuShowcaseUIKit)
uikit-noax-build:
	$(call build_scheme,$(UIKIT),BajutsuShowcaseUIKit,BajutsuShowcaseUIKitNoAx)

# The generic XCUITest runner (BE-0019). One app-agnostic .xctestrun drives any installed app
# by the forwarded BAJUTSU_BUNDLE_ID, so this serves every target's `xcuitest.testRunner`. The
# built name carries the SDK version, so copy it to a stable name in the same dir (its __TESTROOT__
# must stay beside the built products). This is the `xcuitest.build` fallback in showcase.config.yaml.
RUNNER_PRODUCTS := $(RUNNER)/build/dd/Build/Products
runner-build:
	cd $(RUNNER) && xcodegen generate
	cd $(RUNNER) && xcodebuild build-for-testing -project BajutsuRunner.xcodeproj -scheme BajutsuRunner \
		-destination 'generic/platform=iOS Simulator' -derivedDataPath build/dd
	cp $(RUNNER_PRODUCTS)/BajutsuRunner_*.xctestrun $(RUNNER_PRODUCTS)/BajutsuRunner.xctestrun

# ---- Device builds for a device cloud (BE-0238 batch route) ---------------------------------------
# The builds above are unsigned Simulator products; a real device — AWS Device Farm or a locally
# attached phone (xcuitest.deviceType: device) — needs a signed device build instead. Signing is
# parameterized on an Apple Developer team, never hardcoded: pass DEVELOPMENT_TEAM=<10-char Team ID>
# with that team signed into Xcode, so -allowProvisioningUpdates can mint the development profile.
# Device Farm re-signs the uploaded .ipa with its own profile, so a development export is enough.
# The Simulator recipes above are untouched — device signing is turned on only here, via command-line
# build settings, so `make check` and the Simulator lanes stay unsigned. See docs/ios-device-cloud.md
# and demos/showcase/devicefarm/showcase.devicefarm.ios.config.yaml.
SWIFTUI_EXPORT         := $(SWIFTUI)/build/export-device
RUNNER_DD_DEVICE       := $(RUNNER)/build/dd-device
RUNNER_DEVICE_PRODUCTS := $(RUNNER_DD_DEVICE)/Build/Products

define require_team
	@if [ -z "$(DEVELOPMENT_TEAM)" ]; then \
		echo "error: DEVELOPMENT_TEAM=<Apple Team ID> is required for device builds"; \
		echo "  e.g. make -C demos/showcase $(MAKECMDGOALS) DEVELOPMENT_TEAM=ABCDE12345"; \
		exit 1; \
	fi
endef

# The device app archive (signed .xcarchive). `archive` — not `build` — is what exportArchive needs.
swiftui-archive-device:
	$(require_team)
	cd $(SWIFTUI) && xcodegen generate
	cd $(SWIFTUI) && xcodebuild archive -project BajutsuShowcaseSwiftUI.xcodeproj -scheme BajutsuShowcaseSwiftUI \
		-destination 'generic/platform=iOS' -archivePath build/BajutsuShowcaseSwiftUI.xcarchive \
		-derivedDataPath build/dd-device \
		-allowProvisioningUpdates CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=$(DEVELOPMENT_TEAM)

# The device app .ipa (what Device Farm's IOS_APP upload accepts). ExportOptions.plist is a template
# whose __DEVELOPMENT_TEAM__ placeholder is substituted here, so the team stays out of the tree.
swiftui-ipa-device: swiftui-archive-device
	sed 's/__DEVELOPMENT_TEAM__/$(DEVELOPMENT_TEAM)/' $(SWIFTUI)/ExportOptions.plist > $(SWIFTUI)/build/ExportOptions.plist
	cd $(SWIFTUI) && rm -rf build/export-device
	cd $(SWIFTUI) && xcodebuild -exportArchive -archivePath build/BajutsuShowcaseSwiftUI.xcarchive \
		-exportPath build/export-device -exportOptionsPlist build/ExportOptions.plist -allowProvisioningUpdates
	@echo "device .ipa: $(SWIFTUI_EXPORT)/BajutsuShowcaseSwiftUI.ipa"

# The device XCUITest runner (signed .xctestrun). The runner project defaults CODE_SIGNING_ALLOWED=NO
# for the Simulator; a real device requires signing, so override it here only. A separate derivedData
# path keeps the device .xctestrun from colliding with the Simulator one under the glob below. The
# .xctestrun's __TESTROOT__ points beside the built products, so package the whole Products dir.
runner-build-device:
	$(require_team)
	cd $(RUNNER) && xcodegen generate
	cd $(RUNNER) && xcodebuild build-for-testing -project BajutsuRunner.xcodeproj -scheme BajutsuRunner \
		-destination 'generic/platform=iOS' -derivedDataPath build/dd-device \
		-allowProvisioningUpdates CODE_SIGNING_ALLOWED=YES CODE_SIGN_STYLE=Automatic DEVELOPMENT_TEAM=$(DEVELOPMENT_TEAM)
	cp $(RUNNER_DEVICE_PRODUCTS)/BajutsuRunner_*.xctestrun $(RUNNER_DEVICE_PRODUCTS)/BajutsuRunner.xctestrun
	@echo "device .xctestrun: $(RUNNER_DEVICE_PRODUCTS)/BajutsuRunner.xctestrun (package its Products dir alongside)"

# Run the shared id-based scenarios against the a11y apps on a booted Simulator. `--backend ios`
# resolves to XCUITest, the sole iOS backend since idb was retired (BE-0290). `scenarios/tabs.yaml`
# stays tagged `xcuitest` and excluded here — a legacy split from when a coordinate backend could not
# reach the tab bar — and `run-swiftui-tabs`/`run-uikit-tabs` below run it on its own; both paths run
# on XCUITest now. `permission_system_alert.yaml` (BE-0316) is tagged `systemalert` and excluded too:
# these bulk runs discover every scenario file and do not reset per-scenario permission state, so the
# `notDetermined` prompt its handleSystemAlert step needs is not guaranteed here (its own iOS CI job
# installs a fresh app for it). A dedicated tag keeps it out without riding the legacy `xcuitest` tag.
# Prereqs: a booted simulator, `make runner-build`.
SIM ?= $(shell xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)

run-swiftui: swiftui-build runner-build
	cd $(ROOT) && uv run bajutsu run --target showcase-swiftui --udid $(SIM) --backend ios --exclude xcuitest,systemalert --config $(CONFIG)
run-uikit: uikit-build runner-build
	cd $(ROOT) && uv run bajutsu run --target showcase-uikit --udid $(SIM) --backend ios --exclude xcuitest,systemalert --config $(CONFIG)

# scenarios/tabs.yaml on its own, forced onto XCUITest (see the comment above run-swiftui/run-uikit).
run-swiftui-tabs: swiftui-build runner-build
	cd $(ROOT) && uv run bajutsu run --scenario demos/showcase/scenarios/tabs.yaml --target showcase-swiftui \
		--udid $(SIM) --backend xcuitest --config $(CONFIG)
run-uikit-tabs: uikit-build runner-build
	cd $(ROOT) && uv run bajutsu run --scenario demos/showcase/scenarios/tabs.yaml --target showcase-uikit \
		--udid $(SIM) --backend xcuitest --config $(CONFIG)

# The -noax twin of run-swiftui/run-uikit: the same flows from ios/scenarios-noax (config-bound), by
# visible label + on-screen mirror text, run on XCUITest — a coordinate backend collapsed the tab bar and could not
# reach a -noax element by label, and this app-level need is not a per-scenario capability the [ios]
# ladder can detect, so we pin `--backend xcuitest` rather than let it resolve. The UIKit run drops the
# `swiftui`-tagged scenarios (the SHOWCASE_GESTURES pinch/rotate screen and the SwiftUI-recorded
# `generated.yaml` have no UIKit counterpart). NOT part of `make check` — needs a booted Simulator; the
# permission scenario is non-deterministic on iOS (its alert grant is an AI path, like the a11y twin).
run-swiftui-noax: swiftui-noax-build runner-build
	cd $(ROOT) && uv run bajutsu run --target showcase-swiftui-noax --udid $(SIM) --backend xcuitest --config $(CONFIG)
run-uikit-noax: uikit-noax-build runner-build
	cd $(ROOT) && uv run bajutsu run --target showcase-uikit-noax --udid $(SIM) --backend xcuitest --exclude swiftui --config $(CONFIG)

# The accessibility A/B: doctor grades the a11y app Ready and the no-a11y app Blocked.
doctor: swiftui-build swiftui-noax-build
	cd $(ROOT) && uv run bajutsu doctor --target showcase-swiftui      --udid $(SIM) --backend ios --config $(CONFIG)
	cd $(ROOT) && uv run bajutsu doctor --target showcase-swiftui-noax --udid $(SIM) --backend ios --config $(CONFIG)

# AI authoring against the no-a11y app — record falls down the stability ladder (DESIGN §5).
# Needs ANTHROPIC_API_KEY. GOAL overrides the first non-comment line of record/goals.txt.
GOAL ?= $(shell grep -vE '^[[:space:]]*#|^[[:space:]]*$$' $(HERE)/record/goals.txt | head -1)
record: swiftui-noax-build
	cd $(ROOT) && uv run bajutsu record --target showcase-swiftui-noax --udid $(SIM) --backend ios \
		--config $(CONFIG) --goal "$(GOAL)"

# Generate a native XCUITest from the representative codegen scenario and run it via
# xcodebuild (no bajutsu runtime or AI at test time — the codegen output path).
# Generates against the SwiftUI a11y app, whose identifiers the test queries by name.
ui-test:
	cd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/components.yaml --target showcase-swiftui \
		--config $(CONFIG) -o demos/showcase/ios/swiftui/UITests/ComponentsUITests.swift
	cd $(SWIFTUI) && xcodegen generate
	# -retry-tests-on-failure re-runs only a failed test (up to -test-iterations total, stopping
	# on first pass) so a loaded CI Simulator's intermittent "Failed to terminate" between cases
	# self-heals; a genuinely broken test still fails every attempt and trips the gate.
	cd $(SWIFTUI) && xcodebuild test -project BajutsuShowcaseSwiftUI.xcodeproj -scheme UITests \
		-destination 'platform=iOS Simulator,id=$(SIM)' -derivedDataPath build/dd \
		-retry-tests-on-failure -test-iterations 3

# BE-0297: compile-and-run the DSL constructs the required `ui-test` slice (components.yaml) never
# reaches — text-editing, gestures, multi-touch, and the coordinate-swipe / element-drag /
# compound-selector constructs of codegen_extra.yaml. The first three scenarios are already proven
# on-device by `bajutsu run` (the gestures / multi-touch e2e jobs), so only the *emitted* Swift is
# newly under test. Generated files are gitignored (generation-only); this lands as a non-gating CI
# step until the compiled slice proves stable, then folds into the required job (Unit 4). Runs only
# the coverage classes (`-only-testing`, an allow-list keyed on the `Coverage*` output names below,
# robust to any Components rename) — the required `ui-test` above owns the Components class. Each class
# name is the `bajutsu codegen` transform of its `-o` stem (`CoverageExtra` -> `CoverageextraUITests`).
# No `-retry-tests-on-failure` here (unlike `ui-test`): this is the deliberately-unstable slice, so a
# retry would mask the very flakiness this signal exists to surface, and burn 10x-metered minutes doing it.
UITESTS := $(SWIFTUI)/UITests
ui-test-coverage:
	cd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/text_editing.yaml --target showcase-swiftui \
		--config $(CONFIG) -o $(UITESTS)/CoverageTextEditing.swift
	cd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/gestures.yaml --target showcase-swiftui \
		--config $(CONFIG) -o $(UITESTS)/CoverageGestures.swift
	cd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/gestures_multitouch.yaml --target showcase-swiftui \
		--config $(CONFIG) -o $(UITESTS)/CoverageMultitouch.swift
	cd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/codegen_extra.yaml --target showcase-swiftui \
		--config $(CONFIG) -o $(UITESTS)/CoverageExtra.swift
	cd $(SWIFTUI) && xcodegen generate
	cd $(SWIFTUI) && xcodebuild test -project BajutsuShowcaseSwiftUI.xcodeproj -scheme UITests \
		-destination 'platform=iOS Simulator,id=$(SIM)' -derivedDataPath build/dd \
		-only-testing:BajutsuShowcaseSwiftUIUITests/CoveragetexteditingUITests \
		-only-testing:BajutsuShowcaseSwiftUIUITests/CoveragegesturesUITests \
		-only-testing:BajutsuShowcaseSwiftUIUITests/CoveragemultitouchUITests \
		-only-testing:BajutsuShowcaseSwiftUIUITests/CoverageextraUITests

# Visual regression demo — every screen, for BOTH a11y toolkits. The two render the same
# screens but not pixel-identically, so each keeps its own baseline dir (gitignored, regenerate
# locally). The first `vrt` reports missing baselines (exit 1) — that's expected; approve them
# (`make -C demos/showcase vrt-approve`, or the Web UI's Approve button), then re-run `vrt` to
# pixel-match and pass. `-` lets the first failure not abort make.
BASELINES_SWIFTUI = $(HERE)/ios/scenarios/baselines/swiftui
BASELINES_UIKIT   = $(HERE)/ios/scenarios/baselines/uikit

# Both toolkits (run / approve). Use the per-toolkit targets to drive just one.
# Each `-approve` re-runs its own toolkit first, so `approve` (which promotes the *latest*
# run) always sees a run from the matching toolkit — never the other's screenshots.
vrt: vrt-swiftui vrt-uikit
vrt-approve: vrt-swiftui-approve vrt-uikit-approve

vrt-swiftui: swiftui-build runner-build
	-cd $(ROOT) && uv run bajutsu run --scenario demos/showcase/scenarios/visual.yaml --target showcase-swiftui \
		--udid $(SIM) --backend ios --config $(CONFIG) --no-erase --baselines $(BASELINES_SWIFTUI)
vrt-swiftui-approve: vrt-swiftui
	cd $(ROOT) && uv run bajutsu approve --baselines $(BASELINES_SWIFTUI)

vrt-uikit: uikit-build runner-build
	-cd $(ROOT) && uv run bajutsu run --scenario demos/showcase/scenarios/visual.yaml --target showcase-uikit \
		--udid $(SIM) --backend ios --config $(CONFIG) --no-erase --baselines $(BASELINES_UIKIT)
vrt-uikit-approve: vrt-uikit
	cd $(ROOT) && uv run bajutsu approve --baselines $(BASELINES_UIKIT)

# Committed iOS pixel VRT (parity with android/Makefile's e2e-visual + baselines_android). Unlike the
# `vrt` demo above — every screen, XCUITest, local gitignored baselines — this is one screen (the
# Stable catalog on the launch tab, so no tab navigation is needed), compared to a committed baseline
# under scenarios/visual/baselines_ios/. It runs on the path-gated, non-required ios-e2e.yml lane (the
# iOS twin of android-e2e.yml's e2e-visual): a pixel baseline is host-specific (Simulator renderer
# varies by Xcode / device / OS), so drift surfaces on that lane rather than blocking PRs on the
# required gate. `e2e-visual-approve` re-records the baseline from the latest run (do this on the
# lane's Simulator if it drifts). Needs the `visual` extra (Pillow); status bar + Liquid Glass tab bar
# are masked in the scenario.
BASELINES_IOS_VRT = $(HERE)/scenarios/visual/baselines_ios
e2e-visual: swiftui-build
	cd $(ROOT) && uv run --extra visual bajutsu run \
		--scenario demos/showcase/scenarios/visual/visual_ios.yaml \
		--baselines $(BASELINES_IOS_VRT) \
		--target showcase-swiftui --udid $(SIM) --backend ios --config $(CONFIG)
# Run the VRT first so `approve` (which promotes the *latest* run under runs/) always sees this
# scenario's run, never some other target's leftover. The `-` ignores its expected non-zero exit
# when the baseline is missing/drifted — that failure is exactly what we are approving away, the same
# shape as `vrt-swiftui-approve` above. A recursive `$(MAKE)` avoids duplicating the run recipe.
e2e-visual-approve:
	-$(MAKE) --no-print-directory e2e-visual
	cd $(ROOT) && uv run --extra visual bajutsu approve --baselines $(BASELINES_IOS_VRT)

# ---- Flutter (BE-0008) ----------------------------------------------------------------------------
# The Flutter twin (demos/showcase/flutter), one Dart codebase built for both platforms. It adds no
# backend: `Semantics(identifier: …)` surfaces into the OS accessibility tree the native backends
# already read, so the shared scenarios drive it through XCUITest / adb unchanged (docs/drivers.md).
# `--dart-define=ACCESSIBLE` is the a11y/noax switch (the analog of the iOS ACCESSIBLE condition).
# Flutter must be on PATH. NOT part of `make check` — these need a Simulator / emulator.
flutter-build:
	cd $(FLUTTER) && flutter build ios --simulator --debug --dart-define=ACCESSIBLE=true
flutter-android-build:
	cd $(FLUTTER) && flutter build apk --debug --flavor a11y --dart-define=ACCESSIBLE=true
flutter-android-noax-build:
	cd $(FLUTTER) && flutter build apk --debug --flavor noax --dart-define=ACCESSIBLE=false

# The shared id-based scenarios against the Flutter app. iOS resolves to XCUITest; `--exclude`
# drops what a Flutter target does not serve (see docs/drivers.md "Flutter" out-of-scope): `network`
# (Dart HTTP bypasses the native interceptors, so capture / mocks see nothing) and the AI-path
# `systemalert`. Prereqs: a booted Simulator, `make runner-build`; an attached emulator for Android.
run-flutter: flutter-build runner-build
	cd $(ROOT) && uv run bajutsu run --target showcase-flutter --udid $(SIM) --backend xcuitest \
		--exclude network,systemalert --config $(CONFIG)

# The same on-device set the native Compose lane runs (demos/showcase/android/Makefile), minus
# device.yaml — its device-control clipboard round-trips through the in-app BajutsuAndroid receiver
# the plugin-free Flutter app does not link (docs/drivers.md "Flutter" out-of-scope). `adb root`
# enables the `sendevent` path the gestures double-tap and multi-touch use, exactly as the native
# android-e2e lane does; `text_editing` / `push` stay iOS-only in the native suite too.
# BAJUTSU_MIN_WAIT_TIMEOUT=15 mirrors the native Compose lane (demos/showcase/android/Makefile): a
# CI x86_64 software-rendered emulator presents sheets/covers slower than the shared scenarios' 5s
# waits allow, so the modal/component scenarios need a higher wait ceiling. It is a ceiling on a
# condition wait, not a fixed delay, so a fast (hardware-accelerated) emulator still resolves early.
FLUTTER_ANDROID_SCENARIOS := smoke firstlook navigation search data_driven relaunch system \
	components modals controls notices gestures gestures_multitouch permission extract foreach
run-flutter-android: flutter-android-build
	adb root >/dev/null 2>&1 || true
	adb wait-for-device
	cd $(ROOT) && for s in $(FLUTTER_ANDROID_SCENARIOS); do \
		BAJUTSU_MIN_WAIT_TIMEOUT=15 \
		uv run bajutsu run --scenario demos/showcase/scenarios/$$s.yaml \
			--target showcase-flutter-android --backend android --config $(CONFIG) || exit 1; \
	done

clean:
	rm -rf $(SWIFTUI)/build $(UIKIT)/build \
		$(SWIFTUI)/*.xcodeproj $(UIKIT)/*.xcodeproj $(FLUTTER)/build

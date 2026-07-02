# Build / run targets for the showcase suite — four products from two codebases
# (UIKit × SwiftUI, each in an accessibility-on / -off variant). See SPEC.md.
# Invoke from anywhere with `make -C demos/showcase <target>`.
.PHONY: build-all \
	swiftui-gen swiftui-build swiftui-noax-build \
	uikit-gen uikit-build uikit-noax-build runner-build \
	run-swiftui run-uikit doctor record ui-test \
	vrt vrt-approve vrt-swiftui vrt-swiftui-approve vrt-uikit vrt-uikit-approve clean

ROOT   := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HERE   := $(ROOT)/demos/showcase
CONFIG := $(HERE)/showcase.config.yaml
SWIFTUI := $(HERE)/swiftui
UIKIT   := $(HERE)/uikit
RUNNER  := $(ROOT)/BajutsuKit/Runner

# A built scheme lands at build/dd/Build/Products/Debug-iphonesimulator/<Scheme>.app,
# which is exactly the appPath each targets.<name> entry in showcase.config.yaml points to.
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

# Run the shared id-based scenarios against the a11y apps on a booted Simulator (idb).
# Prereqs: a booted simulator, `brew install facebook/fb/idb-companion`, `uv sync --extra idb`.
SIM ?= $(shell xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)

run-swiftui: swiftui-build
	cd $(ROOT) && uv run bajutsu run --target showcase-swiftui --udid $(SIM) --backend idb --config $(CONFIG)
run-uikit: uikit-build
	cd $(ROOT) && uv run bajutsu run --target showcase-uikit --udid $(SIM) --backend idb --config $(CONFIG)

# The accessibility A/B: doctor grades the a11y app Ready and the no-a11y app Blocked.
doctor: swiftui-build swiftui-noax-build
	cd $(ROOT) && uv run bajutsu doctor --target showcase-swiftui      --udid $(SIM) --backend idb --config $(CONFIG)
	cd $(ROOT) && uv run bajutsu doctor --target showcase-swiftui-noax --udid $(SIM) --backend idb --config $(CONFIG)

# AI authoring against the no-a11y app — record falls down the stability ladder (DESIGN §5).
# Needs ANTHROPIC_API_KEY. GOAL overrides the first non-comment line of record/goals.txt.
GOAL ?= $(shell grep -vE '^[[:space:]]*#|^[[:space:]]*$$' $(HERE)/record/goals.txt | head -1)
record: swiftui-noax-build
	cd $(ROOT) && uv run bajutsu record --target showcase-swiftui-noax --udid $(SIM) --backend idb \
		--config $(CONFIG) --goal "$(GOAL)"

# Generate a native XCUITest from the representative codegen scenario and run it via
# xcodebuild (no bajutsu runtime / idb / AI at test time — the codegen output path).
# Generates against the SwiftUI a11y app, whose identifiers the test queries by name.
ui-test:
	cd $(ROOT) && uv run bajutsu codegen demos/showcase/scenarios/components.yaml --target showcase-swiftui \
		--config $(CONFIG) -o demos/showcase/swiftui/UITests/ComponentsUITests.swift
	cd $(SWIFTUI) && xcodegen generate
	# -retry-tests-on-failure re-runs only a failed test (up to -test-iterations total, stopping
	# on first pass) so a loaded CI Simulator's intermittent "Failed to terminate" between cases
	# self-heals; a genuinely broken test still fails every attempt and trips the gate.
	cd $(SWIFTUI) && xcodebuild test -project BajutsuShowcaseSwiftUI.xcodeproj -scheme UITests \
		-destination 'platform=iOS Simulator,id=$(SIM)' -derivedDataPath build/dd \
		-retry-tests-on-failure -test-iterations 3

# Visual regression demo — every screen, for BOTH a11y toolkits. The two render the same
# screens but not pixel-identically, so each keeps its own baseline dir (gitignored, regenerate
# locally). The first `vrt` reports missing baselines (exit 1) — that's expected; approve them
# (`make -C demos/showcase vrt-approve`, or the Web UI's Approve button), then re-run `vrt` to
# pixel-match and pass. `-` lets the first failure not abort make.
BASELINES_SWIFTUI = $(HERE)/scenarios/baselines/swiftui
BASELINES_UIKIT   = $(HERE)/scenarios/baselines/uikit

# Both toolkits (run / approve). Use the per-toolkit targets to drive just one.
# Each `-approve` re-runs its own toolkit first, so `approve` (which promotes the *latest*
# run) always sees a run from the matching toolkit — never the other's screenshots.
vrt: vrt-swiftui vrt-uikit
vrt-approve: vrt-swiftui-approve vrt-uikit-approve

vrt-swiftui: swiftui-build
	-cd $(ROOT) && uv run bajutsu run --scenario demos/showcase/scenarios/visual.yaml --target showcase-swiftui \
		--udid $(SIM) --backend idb --config $(CONFIG) --no-erase --baselines $(BASELINES_SWIFTUI)
vrt-swiftui-approve: vrt-swiftui
	cd $(ROOT) && uv run bajutsu approve --baselines $(BASELINES_SWIFTUI)

vrt-uikit: uikit-build
	-cd $(ROOT) && uv run bajutsu run --scenario demos/showcase/scenarios/visual.yaml --target showcase-uikit \
		--udid $(SIM) --backend idb --config $(CONFIG) --no-erase --baselines $(BASELINES_UIKIT)
vrt-uikit-approve: vrt-uikit
	cd $(ROOT) && uv run bajutsu approve --baselines $(BASELINES_UIKIT)

clean:
	rm -rf $(SWIFTUI)/build $(UIKIT)/build \
		$(SWIFTUI)/*.xcodeproj $(UIKIT)/*.xcodeproj

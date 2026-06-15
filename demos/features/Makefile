# Build / run targets for the feature demo, which exercises the demos/features/app fixture app.
# Invoke from anywhere with `make -C demos/features <target>` (or from inside this folder).
# ROOT resolves to the repo root so the paths match demos/features/demo.config.yaml, the
# purpose-built config for these scenarios (passed explicitly via --config below).
.PHONY: sample-gen sample-build e2e ui-test

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
SAMPLE := $(ROOT)/demos/features/app
CONFIG := $(ROOT)/demos/features/demo.config.yaml

sample-gen:
	cd $(SAMPLE) && xcodegen generate

sample-build: sample-gen
	cd $(SAMPLE) && xcodebuild -project BajutsuSample.xcodeproj -scheme BajutsuSample \
		-destination 'generic/platform=iOS Simulator' -derivedDataPath build/dd build

# End-to-end against a booted Simulator via idb.
# Prereqs: a booted simulator, `brew install facebook/fb/idb-companion`,
# and the idb client (`uv sync --extra idb`).
SIM ?= $(shell xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)
APP = $(SAMPLE)/build/dd/Build/Products/Debug-iphonesimulator/BajutsuSample.app

e2e: sample-build
	xcrun simctl install $(SIM) $(APP)
	cd $(ROOT) && uv run bajutsu run --scenario demos/features/app/scenarios/smoke.yaml --app sample --udid $(SIM) --backend idb --config $(CONFIG) --no-erase
	cd $(ROOT) && uv run bajutsu doctor --app sample --udid $(SIM) --backend idb --config $(CONFIG)

# Generate a native XCUITest from a scenario and run it via xcodebuild (no bajutsu
# runtime / idb / AI at test time) — the codegen output path.
ui-test:
	cd $(ROOT) && uv run bajutsu codegen demos/features/app/scenarios/components.yaml --app sample \
		--config $(CONFIG) -o demos/features/app/BajutsuSampleUITests/ComponentsUITests.swift
	cd $(SAMPLE) && xcodegen generate
	cd $(SAMPLE) && xcodebuild test -project BajutsuSample.xcodeproj -scheme UITests \
		-destination 'platform=iOS Simulator,id=$(SIM)' -derivedDataPath build/dd

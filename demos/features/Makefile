# Build / run targets for the feature demo, which exercises the app/sample fixture app.
# Invoke from anywhere with `make -C demos/features <target>` (or from inside this folder).
# ROOT resolves to the repo root so the paths match demos/features/demo.config.yaml and the
# repo-root bajutsu.config.yaml.
.PHONY: sample-gen sample-build e2e ui-test

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
SAMPLE := $(ROOT)/app/sample

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
	cd $(ROOT) && uv run bajutsu run app/sample/scenarios/smoke.yaml --app sample --udid $(SIM) --backend idb --no-erase
	cd $(ROOT) && uv run bajutsu doctor --app sample --udid $(SIM) --backend idb

# Generate a native XCUITest from a scenario and run it via xcodebuild (no bajutsu
# runtime / idb / AI at test time) — the codegen output path.
ui-test:
	cd $(ROOT) && uv run bajutsu codegen app/sample/scenarios/components.yaml --app sample \
		-o app/sample/BajutsuSampleUITests/ComponentsUITests.swift
	cd $(SAMPLE) && xcodegen generate
	cd $(SAMPLE) && xcodebuild test -project BajutsuSample.xcodeproj -scheme UITests \
		-destination 'platform=iOS Simulator,id=$(SIM)' -derivedDataPath build/dd

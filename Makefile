.PHONY: test lint typecheck check sample-gen sample-build e2e ui-test

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy bajutsu

check: lint typecheck test

sample-gen:
	cd sample && xcodegen generate

sample-build: sample-gen
	cd sample && xcodebuild -project BajutsuSample.xcodeproj -scheme BajutsuSample \
		-destination 'generic/platform=iOS Simulator' -derivedDataPath build/dd build

# End-to-end against a booted Simulator via idb.
# Prereqs: a booted simulator, `brew install facebook/fb/idb-companion`,
# and the idb client (`uv sync --extra idb`).
SIM ?= $(shell xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)
APP = sample/build/dd/Build/Products/Debug-iphonesimulator/BajutsuSample.app

e2e: sample-build
	xcrun simctl install $(SIM) $(APP)
	uv run bajutsu run sample/scenarios/smoke.yaml --app sample --udid $(SIM) --backend idb --no-erase
	uv run bajutsu doctor --app sample --udid $(SIM) --backend idb

# Generate a native XCUITest from a scenario and run it via xcodebuild (no bajutsu
# runtime / idb / AI at test time) — the codegen output path.
ui-test:
	uv run bajutsu codegen sample/scenarios/components.yaml --app sample \
		-o sample/BajutsuSampleUITests/ComponentsUITests.swift
	cd sample && xcodegen generate
	cd sample && xcodebuild test -project BajutsuSample.xcodeproj -scheme UITests \
		-destination 'platform=iOS Simulator,id=$(SIM)' -derivedDataPath build/dd

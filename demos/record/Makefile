# Build target for the record demo, which exercises the app/sample2 fixture app.
# Invoke from anywhere with `make -C demos/record <target>` (or from inside this folder).
# ROOT resolves to the repo root so the build output matches demos/record/demo.config.yaml.
.PHONY: sample2-gen sample2-build

ROOT := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
SAMPLE2 := $(ROOT)/app/sample2

sample2-gen:
	cd $(SAMPLE2) && xcodegen generate

sample2-build: sample2-gen
	cd $(SAMPLE2) && xcodebuild -project BajutsuSample.xcodeproj -scheme BajutsuSample \
		-destination 'generic/platform=iOS Simulator' -derivedDataPath build/dd build

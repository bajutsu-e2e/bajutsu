.PHONY: test lint typecheck check sample-gen sample-build

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy simpilot

check: lint typecheck test

sample-gen:
	cd sample && xcodegen generate

sample-build: sample-gen
	cd sample && xcodebuild -project SimPilotSample.xcodeproj -scheme SimPilotSample \
		-destination 'generic/platform=iOS Simulator' build

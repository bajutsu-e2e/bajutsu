.PHONY: test lint typecheck check sample-gen sample-build

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy simyoke

check: lint typecheck test

sample-gen:
	cd sample && xcodegen generate

sample-build: sample-gen
	cd sample && xcodebuild -project SimyokeSample.xcodeproj -scheme SimyokeSample \
		-destination 'generic/platform=iOS Simulator' build

.PHONY: deps deps-check serve test lint typecheck check

# Install the external tools the idb backend needs (idempotent).
#   - Homebrew tools (idb_companion / xcodegen) from the Brewfile
#   - the idb python client via uv (the `idb` extra)
deps:
	@command -v brew >/dev/null 2>&1 || { echo "Homebrew is required: https://brew.sh"; exit 1; }
	brew bundle --file=Brewfile
	uv sync --extra idb --group dev

# Verify the required tools are on PATH without installing anything.
deps-check:
	@command -v idb_companion >/dev/null 2>&1 && echo "idb_companion: ok" || echo "idb_companion: MISSING (make deps)"
	@command -v xcodegen >/dev/null 2>&1 && echo "xcodegen: ok" || echo "xcodegen: MISSING (make deps)"
	@command -v xcrun >/dev/null 2>&1 && echo "xcrun (Xcode): ok" || echo "xcrun (Xcode): MISSING (install Xcode)"

# Launch the web UI, installing the idb backend's deps on demand (see scripts/serve.sh).
# Pass flags through ARGS, e.g. `make serve ARGS="--port 8766"`.
serve:
	@./scripts/serve.sh $(ARGS)

test:
	uv run pytest -q

lint:
	uv run ruff check .

typecheck:
	uv run mypy bajutsu

check: lint typecheck test

# Sample-app build / E2E targets live with their demos:
#   make -C demos/features sample-gen|sample-build|e2e|ui-test   (demos/features/app)
#   make -C demos/record   sample2-gen|sample2-build             (demos/record/app)

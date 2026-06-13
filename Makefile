.PHONY: setup hooks deps deps-check serve test lint typecheck check

# One-command bootstrap for a fresh clone (cross-platform; the dev gate needs no
# Simulator). Installs the Python toolchain and wires the tracked git hooks.
setup: hooks
	uv sync --group dev

# Wire the tracked pre-push gate into this clone. `core.hooksPath` is a per-clone
# local setting that clone/pull never carry over, so this self-heals existing
# clones too — `check` runs it before every gate, right when it matters. Idempotent.
hooks:
	@[ -d .githooks ] && git config core.hooksPath .githooks && echo "hooks: core.hooksPath -> .githooks" || true

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

check: hooks lint typecheck test

# Sample-app build / E2E targets live with their demos:
#   make -C demos/features sample-gen|sample-build|e2e|ui-test   (demos/features/app)
#   make -C demos/record   sample2-gen|sample2-build             (demos/record/app)

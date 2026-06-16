.PHONY: setup hooks deps deps-check serve test lint format format-check typecheck \
        lock-check lint-sh lint-actions check

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

# Shell scripts the gate lints. pre-push has no .sh suffix, so they're listed explicitly.
SHELL_SCRIPTS := .githooks/pre-push scripts/serve.sh .claude/hooks/session-start.sh demos/record/demo.sh demos/tour/demo.sh

# Run the suite with a coverage floor — a regression that quietly drops coverage fails the gate.
test:
	uv run pytest -q --cov=bajutsu --cov-report=term-missing:skip-covered --cov-fail-under=85

lint:
	uv run ruff check .

# Apply the formatter; `format-check` (in the gate) only verifies, never rewrites.
format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy bajutsu demos

# The committed uv.lock must already satisfy pyproject — a dependency edit that forgets
# to re-lock fails here instead of silently resolving something else in CI.
lock-check:
	uv lock --check

lint-sh:
	uv run shellcheck $(SHELL_SCRIPTS)

# actionlint is a standalone Go binary (not pip/uv installable), so it's the one gate
# check that needs a separate install. CI always installs and runs it; locally we lint
# the workflows if it's present and skip with a notice otherwise, so `check` still runs
# anywhere. Install locally: https://github.com/rhysd/actionlint/blob/main/docs/install.md
lint-actions:
	@command -v actionlint >/dev/null 2>&1 && actionlint -color || echo "lint-actions: actionlint not installed — skipping (CI enforces it)"

# The full gate. CI (.github/workflows/ci.yml) mirrors these steps so "green locally"
# predicts "green in CI". The uv-native checks run identically everywhere; actionlint is
# the lone exception (see lint-actions above).
check: hooks format-check lint lint-sh lint-actions lock-check typecheck test

# Sample-app build / E2E targets live with their demos:
#   make -C demos/features sample-gen|sample-build|e2e|ui-test   (demos/features/app)
#   make -C demos/record   sample2-gen|sample2-build             (demos/record/app)

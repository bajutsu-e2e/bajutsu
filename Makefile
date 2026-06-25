.PHONY: setup hooks deps deps-check serve test lint lint-docstrings format format-check typecheck \
        lock-check lint-sh lint-actions lint-roadmap check roadmap-index roadmap-promote roadmap-id-repair \
        docs docs-serve

# One-command bootstrap for a fresh clone (cross-platform; the dev gate needs no
# Simulator). Installs the Python toolchain and wires the tracked git hooks.
setup: hooks
	uv sync --group dev

# Wire per-clone local git settings that clone/pull never carry over, so this self-heals
# existing clones too — `check` runs it before every gate, right when it matters. Idempotent:
#   - core.hooksPath    -> the tracked pre-push gate
#   - merge.uv-lock     -> regenerate uv.lock from pyproject.toml on conflict (BE-0043)
#   - merge.roadmap-index -> regenerate the roadmap index tables on conflict (BE-0043)
#   - rerere            -> replay a once-resolved conflict automatically (BE-0043)
hooks:
	@[ -d .githooks ] && git config core.hooksPath .githooks && echo "hooks: core.hooksPath -> .githooks" || true
	@git config merge.uv-lock.name "regenerate uv.lock from pyproject.toml" \
	  && git config merge.uv-lock.driver "./scripts/merge-uv-lock.sh %A" \
	  && git config merge.roadmap-index.name "row-merge the roadmap index tables" \
	  && git config merge.roadmap-index.driver "./scripts/merge-roadmap-index.sh %O %A %B" \
	  && git config rerere.enabled true \
	  && echo "hooks: uv.lock + roadmap-index merge drivers + rerere wired"

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
SHELL_SCRIPTS := .githooks/pre-push scripts/serve.sh scripts/merge-uv-lock.sh scripts/merge-roadmap-index.sh scripts/open_pr_be_ids.sh scripts/open_pr_be_map.sh scripts/be_claims.sh .claude/hooks/session-start.sh demos/record/demo.sh demos/tour/demo.sh

# Modules whose public surface has migrated to the Google-style docstring standard (BE-0065),
# enforced by `lint-docstrings`. This list GROWS module-by-module as more migrate; keep it the
# allowlist (not an ignore list) so an unmigrated module never accidentally falls under the gate.
DOCSTRING_PATHS := bajutsu/drivers/base.py bajutsu/assertions.py bajutsu/network.py bajutsu/runner bajutsu/scenario bajutsu/mcp

# Run the suite with a coverage floor — a regression that quietly drops coverage fails the gate.
# The JSON report is a gitignored side artifact CI renders into its job summary (scripts/coverage_summary.py).
test:
	uv run pytest -q --cov=bajutsu --cov-report=term-missing:skip-covered --cov-report=json:coverage.json --cov-fail-under=87

lint:
	uv run ruff check .

# BE-0065 phase 5: enforce the Google-style docstring standard on the migrated public surface
# ($(DOCSTRING_PATHS)). Scoped (not repo-wide) because the migration is phased — unmigrated modules
# keep their prose docstrings until their turn. D102/D105/D107 are excluded by design: D102 would
# force docstrings onto the compact `Driver`/`Collector` Protocol `: ...` stubs, and D105/D107
# (magic methods / __init__) are noise. The google convention is set in pyproject's pydocstyle.
lint-docstrings:
	uv run ruff check --select D --ignore D102,D105,D107 $(DOCSTRING_PATHS)

# Apply the formatter; `format-check` (in the gate) only verifies, never rewrites.
format:
	uv run ruff format .

format-check:
	uv run ruff format --check .

typecheck:
	uv run mypy bajutsu demos scripts

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

# Lint roadmap items: every item-to-item markdown link resolves, and each Author is a handle link
# (BE-0069). Folded into `check` so a broken cross-reference fails the gate, not a reader's click.
# Pass flags through ARGS, e.g. `make lint-roadmap ARGS="--fix"` rewrites broken item links to the
# target's current status folder.
lint-roadmap:
	uv run python scripts/lint_roadmap.py $(ARGS)

# Regenerate the roadmap index tables (README.md / README-ja.md) from each BE item's own
# metadata, so a roadmap PR only touches its own directory (BE-0043). The committed result is
# enforced by tests/test_roadmap_index.py — part of `make test`, so the gate fails on drift.
roadmap-index:
	uv run python scripts/build_roadmap_index.py

# Move shipped items (Status: Implemented) from proposals/ to implemented/ and reindex, so each
# item's directory matches its Status — the mechanical half of the documented ship step. The
# roadmap-promote workflow runs this on a PR; the same invariant is enforced by
# tests/test_promote_roadmap_items.py (part of `make test`), so the gate fails on a mismatch.
roadmap-promote:
	uv run python scripts/promote_roadmap_items.py

# Renumber any item on this branch whose BE id a more authoritative holder already owns — origin/main
# (a merged item wins), or, when nothing is merged, a lower-numbered open PR — picking the next free
# ID. The backstop for the rare collision the refs/be-claims/* reservation does not prevent. The
# roadmap-id-repair workflow runs this across open PRs on a push to main and on a schedule; run it
# locally to fix your own branch before pushing (needs `git fetch origin` first; the open-PR
# tiebreaker only applies in CI, which passes ROADMAP_LOWER_PR_IDS).
roadmap-id-repair:
	uv run python scripts/allocate_roadmap_ids.py --repair

# The full gate. CI (.github/workflows/ci.yml) mirrors these steps so "green locally"
# predicts "green in CI". The uv-native checks run identically everywhere; actionlint is
# the lone exception (see lint-actions above).
check: hooks format-check lint lint-docstrings lint-sh lint-actions lint-roadmap lock-check typecheck test

# Generated API reference (BE-0065). Deliberately NOT in `check`: like on-device E2E, the
# reference build is a separate, heavier path (it pulls the `docs` extra) and must not slow the
# gate. `--strict` fails on a broken reference (e.g. an unresolved symbol). `docs-serve` previews
# it locally with live reload.
docs:
	uv run --extra docs mkdocs build --strict
docs-serve:
	uv run --extra docs mkdocs serve

# Sample-app build / E2E targets live with their demos:
#   make -C demos/features sample-gen|sample-build|e2e|ui-test   (demos/features/app)
#   make -C demos/record   sample2-gen|sample2-build             (demos/record/app)

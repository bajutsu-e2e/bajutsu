.PHONY: setup hooks install deps deps-check serve worktree preflight test lint lint-docstrings lint-imports format format-check typecheck \
        lock-check lint-sh lint-actions lint-js lint-roadmap lint-pr check new-roadmap-item \
        roadmap-status roadmap-dashboard docs docs-serve docs-diagrams

# One-command bootstrap for a fresh clone (cross-platform; the dev gate needs no
# Simulator). Installs the Python toolchain and wires the tracked git hooks.
setup: hooks
	uv sync --group dev

# Wire per-clone local git settings that clone/pull never carry over, so this self-heals
# existing clones too — `check` runs it before every gate, right when it matters. Idempotent:
#   - core.hooksPath    -> the tracked hooks dir (pre-push gate + commit-msg scope check, BE-0069)
#   - merge.uv-lock     -> regenerate uv.lock from pyproject.toml on conflict (BE-0043)
#   - rerere            -> replay a once-resolved conflict automatically (BE-0043)
hooks:
	@[ -d .githooks ] && git config core.hooksPath .githooks && echo "hooks: core.hooksPath -> .githooks" || true
	@git config merge.uv-lock.name "regenerate uv.lock from pyproject.toml" \
	  && git config merge.uv-lock.driver "./scripts/merge-uv-lock.sh %A" \
	  && git config rerere.enabled true \
	  && echo "hooks: uv.lock merge driver + rerere wired"

# Config-aware one-command bootstrap (BE-0164): the base toolchain (`setup`) PLUS exactly the
# backend deps a project's config needs — not "idb unconditionally", not "everything". Meant to run
# right after `git clone`, the same moment `make setup` does. Pass a config or a forced backend
# through ARGS, e.g. `make install ARGS="--config demos/showcase/showcase.config.yaml"`. With no
# config in cwd it installs nothing beyond the base (the dev gate needs no backend).
install: setup
	@./scripts/install.sh $(ARGS)

# Install the external tools the idb backend needs (idempotent). Superseded by `make install`
# (config-aware); kept as the idb-forced shortcut. The `idb` extra + the `idb_companion` formula
# come from the one requirements mapping via the installer (BE-0164), so the Brewfile now holds
# only the sample-app build tool (xcodegen), which is not a bajutsu backend requirement.
deps:
	@./scripts/install.sh --backend idb
	@if command -v brew >/dev/null 2>&1; then \
	  brew bundle --file=Brewfile; \
	else \
	  echo "deps: Homebrew absent — skipping xcodegen (brew bundle); see https://brew.sh"; \
	fi

# Verify the required tools are on PATH without installing anything.
deps-check:
	@command -v idb_companion >/dev/null 2>&1 && echo "idb_companion: ok" || echo "idb_companion: MISSING (make deps)"
	@command -v xcodegen >/dev/null 2>&1 && echo "xcodegen: ok" || echo "xcodegen: MISSING (make deps)"
	@command -v xcrun >/dev/null 2>&1 && echo "xcrun (Xcode): ok" || echo "xcrun (Xcode): MISSING (install Xcode)"

# Launch the web UI, installing the idb backend's deps on demand (see scripts/serve.sh).
# Pass flags through ARGS, e.g. `make serve ARGS="--port 8766"`.
serve:
	@./scripts/serve.sh $(ARGS)

# Create an isolated worktree + branch for a focused session, off the latest origin/main, and
# bootstrap it (the docs/ai-development.md "worktree" recipe as one command, BE-0069 C). The
# `git fetch origin` is baked in so the "branched off a stale origin/main" foot-gun can't happen.
# Branch prefix defaults to `claude`; override for a human, e.g. PREFIX=<user>. Usage:
#   make worktree TOPIC=<topic> [PREFIX=<user>]
worktree:
	@./scripts/worktree.sh "$(TOPIC)"

# Run-it-early pre-push routine: fetch + rebase onto origin/main + run the gate, then print the
# "definition of done" reminder (BE-0069 C). Advisory and human-initiated — the pre-push hook
# already GATES `make check`; this is the do-it-early version, not a second hard gate or a hook.
preflight:
	@./scripts/preflight.sh

# Shell scripts the gate lints. pre-push has no .sh suffix, so they're listed explicitly.
SHELL_SCRIPTS := .githooks/pre-push .githooks/commit-msg scripts/serve.sh scripts/install.sh scripts/worktree.sh scripts/preflight.sh scripts/merge-uv-lock.sh .claude/hooks/session-start.sh demos/tour/demo.sh

# Modules whose public surface has migrated to the Google-style docstring standard (BE-0065),
# enforced by `lint-docstrings`. This list GROWS module-by-module as more migrate; keep it the
# allowlist (not an ignore list) so an unmigrated module never accidentally falls under the gate.
DOCSTRING_PATHS := bajutsu/ai bajutsu/drivers bajutsu/assertions bajutsu/evidence/network.py bajutsu/runner bajutsu/scenario bajutsu/mcp bajutsu/cli bajutsu/doctor.py bajutsu/analysis/audit.py bajutsu/analysis/coverage.py bajutsu/analysis/stats.py bajutsu/trace.py bajutsu/triage.py bajutsu/report bajutsu/evidence/core.py bajutsu/idb_version.py bajutsu/evidence/intervals.py bajutsu/evidence/redaction.py bajutsu/config.py bajutsu/config_source.py bajutsu/codegen/xcuitest.py bajutsu/codegen/common.py bajutsu/codegen/playwright.py bajutsu/backends.py bajutsu/capability_preflight.py bajutsu/requirements.py bajutsu/provision.py bajutsu/crawl/core.py bajutsu/crawl/serialize.py bajutsu/crawl/guide.py bajutsu/crawl/tabs.py bajutsu/agents/protocols.py bajutsu/agents/factory.py bajutsu/agents/claude.py bajutsu/agents/claude_backed.py bajutsu/agents/claude_triage.py bajutsu/agents/alerts.py bajutsu/agents/ai_config.py bajutsu/agents/anthropic_client.py bajutsu/record.py bajutsu/screenshots.py bajutsu/evidence/visual.py bajutsu/web_network.py bajutsu/from_grouping.py

# Run the suite with a coverage floor — a regression that quietly drops coverage fails the gate.
# The JSON report is a gitignored side artifact CI renders into its job summary (scripts/coverage_summary.py).
test:
	uv run pytest -q --cov=bajutsu --cov-report=term-missing:skip-covered --cov-report=json:coverage.json --cov-fail-under=89

lint:
	uv run ruff check .

# BE-0065 phase 5: enforce the Google-style docstring standard on the migrated public surface
# ($(DOCSTRING_PATHS)). Scoped (not repo-wide) because the migration is phased — unmigrated modules
# keep their prose docstrings until their turn. D102/D105/D107 are excluded by design: D102 would
# force docstrings onto the compact `Driver`/`Collector` Protocol `: ...` stubs, and D105/D107
# (magic methods / __init__) are noise. The google convention is set in pyproject's pydocstyle.
lint-docstrings:
	uv run ruff check --select D --ignore D102,D105,D107 $(DOCSTRING_PATHS)

# BE-0112: enforce the core / contract / periphery layer model as a static import contract
# ([tool.importlinter] in pyproject). Fails when a deterministic-core module imports the periphery,
# keeping the verdict/evidence path free of the serve / AI / codegen stacks. Static analysis on the
# import graph — no Simulator, no model, nothing on the run/CI verdict path (prime directives 1 & 3).
lint-imports:
	uv run lint-imports

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

# BE-0129: a proportionate guardrail for the serve Web UI's vanilla JS. Since BE-0247 the section
# files bajutsu/templates/serve.*.mjs (~3.2k lines total, no build step) are native ES modules —
# `.mjs` so `node --check` parses them with the module goal (not the default script goal, under which
# top-level `import`/`export` is a SyntaxError). `node --check` catches syntax errors and runs
# wherever Node is present (including CI runners) — one file at a time, so we loop over the modules.
# There is no combined-script check anymore: each module has its own scope, so BE-0202's cross-file
# duplicate-`const` hazard (only visible once inlined into one scope) no longer exists — a collision
# would now be a per-file duplicate, which the per-file pass already catches. The roadmap dashboard's
# embedded filter script (build_roadmap_dashboard.py `_SCRIPT`) lives inline in a Python string, not
# under templates/, so the glob misses it; we emit it (`--emit-script`) and `node --check` it too
# (as a plain script — it uses no modules), so a typo there fails the gate rather than only surfacing
# in a browser. The uv-driven dashboard emit skips with a notice when uv isn't set up (no non-uv
# fallback — the glob never touched it), so it goes unchecked — CI always has uv, so the gate is
# unaffected. The flat-config eslint (eslint.config.mjs) adds a few structural checks and runs only
# when eslint is already resolvable, so the gate never downloads it. Node absence skips with a notice
# — the same pattern lint-actions uses for actionlint — so `check` runs anywhere.
lint-js:
	@set -e; \
	if ! command -v node >/dev/null 2>&1; then \
		echo "lint-js: node not installed — skipping (CI enforces it)"; \
	else \
		for f in bajutsu/templates/serve.*.mjs; do node --check "$$f"; done; \
		if command -v uv >/dev/null 2>&1; then \
			dir="$$(mktemp -d)"; trap 'rm -rf "$$dir"' EXIT; \
			uv run --no-sync python scripts/build_roadmap_dashboard.py --emit-script > "$$dir/dashboard.js"; \
			node --check "$$dir/dashboard.js"; \
		else \
			echo "lint-js: uv not available — skipping the dashboard check (ran per-file node --check on the modules)"; \
		fi; \
		if npx --no-install eslint --version >/dev/null 2>&1; then \
			npx --no-install eslint 'bajutsu/templates/serve.*.mjs'; \
		else \
			echo "lint-js: eslint not installed — skipping (ran node --check; install eslint for the structural checks)"; \
		fi; \
	fi

# Lint roadmap items: every item-to-item markdown link resolves, and each Author is a handle link
# (BE-0069). Folded into `check` so a broken cross-reference fails the gate, not a reader's click.
# Pass flags through ARGS, e.g. `make lint-roadmap ARGS="--fix"` rewrites broken item links to the
# target item's current path.
lint-roadmap:
	uv run python scripts/lint_roadmap.py $(ARGS)

# Scaffold a new roadmap (BE) item — both language files in the canonical format, with the literal
# BE-XXXX placeholder (CI allocates the real id). The error-prone item-authoring recipe as one
# command (BE-0069). Usage:
#   make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Proposal] [HANDLE=<handle>]
new-roadmap-item:
	uv run python scripts/new_roadmap_item.py --slug "$(SLUG)" --title "$(TITLE)" \
	  $(if $(TOPIC),--topic "$(TOPIC)") $(if $(STATUS),--status "$(STATUS)") $(if $(HANDLE),--handle "$(HANDLE)")

# Check the mechanical PR-metadata conventions on this branch vs origin/main (BE-0069):
# conventional scoped commit subjects, a [BE-NNNN] PR-title prefix on a roadmap change, and a
# behaviour-change-without-test reminder. ADVISORY and deliberately NOT in `check` — it needs
# branch/PR context (the gate runs on any checkout) and most of it is a reminder, not a gate. It
# exits nonzero only on a clear violation (a non-scoped commit; in CI with $PR_TITLE, a roadmap PR
# missing the prefix). Run before pushing; CI can run it with PR_TITLE set to validate the title.
lint-pr:
	uv run python scripts/lint_pr.py

# Filter roadmap (BE) items by Status into one small table — ID / Item / Topic / Path — so an AI
# session surveys just the rows it needs (e.g. every Proposal) without paging through the dashboard's
# rendered HTML or opening each item file to check its `Status` (BE-0162). Pure and offline: reads
# roadmaps/ metadata only. The `roadmap-filter` skill wraps this.
#   make roadmap-status STATUS="Proposal"   # or "In progress" / "Implemented" / "Proposal (deferred)"
roadmap-status:
	uv run python scripts/roadmap_query.py --status "$(STATUS)"

# The full gate. CI (.github/workflows/ci.yml) mirrors these steps so "green locally"
# predicts "green in CI". The uv-native checks run identically everywhere; actionlint is
# the lone exception (see lint-actions above).
check: hooks format-check lint lint-docstrings lint-imports lint-sh lint-actions lint-js lint-roadmap lock-check typecheck test

# Generated API reference (BE-0065). Deliberately NOT in `check`: like on-device E2E, the
# reference build is a separate, heavier path (it pulls the `docs` extra) and must not slow the
# gate. `--strict` fails on a broken reference (e.g. an unresolved symbol). `docs-serve` previews
# it locally with live reload.
# Regenerate the roadmap dashboard page from live BE metadata (BE-XXXX). A docs build artifact
# (gitignored), so every `docs` / `docs-serve` regenerates it first — the page can never drift from
# the committed roadmap. Needs only stdlib, so it runs without the docs extra.
roadmap-dashboard:
	uv run python scripts/build_roadmap_dashboard.py

docs: roadmap-dashboard
	uv run --extra docs mkdocs build --strict
docs-serve: roadmap-dashboard
	uv run --extra docs mkdocs serve

# Re-render every ```mermaid diagram in docs/ to its checked-in SVG (scripts/render_diagrams.py).
# Manual and opt-in — NOT part of `docs` or `check` — because it shells out to Node
# (`npx @mermaid-js/mermaid-cli`), a dependency this Python/uv-native repo otherwise has none of,
# and needs a one-time `npx puppeteer browsers install chrome-headless-shell`. Run it after editing
# a mermaid fence; the rendered SVGs are committed, so a plain `make docs` never needs Node.
docs-diagrams:
	uv run python scripts/render_diagrams.py

# Showcase build / on-device targets live with the fixture (demos/showcase/, the single iOS app):
#   make -C demos/showcase swiftui-build|uikit-build|run-swiftui|doctor|record|ui-test|vrt

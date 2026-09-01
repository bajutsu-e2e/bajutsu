.PHONY: setup hooks git-guard-install install deps deps-check serve worktree preflight test lint lint-docstrings lint-imports format format-check typecheck typecheck-tests \
        lock-check lint-sh lint-actions lint-js lint-roadmap lint-pr lint-secrets skills lint-skills \
        lint-coverage-floors coverage-floors \
        check new-roadmap-item \
        roadmap-status roadmap-dashboard docs docs-serve docs-diagrams runner-bundle

# One-command bootstrap for a fresh clone (cross-platform; the dev gate needs no
# Simulator). Installs the Python toolchain, wires the tracked git hooks, and best-effort
# installs the personal `git push --no-verify` guard (git-guard-install below) — so a fresh
# worktree is protected from the moment development starts, not from a separate step a session
# can forget to run under time pressure. `|| true`: this edits the caller's shell rc, a file
# `setup` doesn't otherwise touch, so a failure here (unwritable rc, unrecognized $SHELL) must
# never block the toolchain install that follows it.
setup: hooks
	uv sync --group dev
	@./scripts/install-no-verify-guard.sh || true

# Wire per-clone local git settings that clone/pull never carry over, so this self-heals
# existing clones too — `check` runs it before every gate, right when it matters. Idempotent:
#   - core.hooksPath    -> the tracked hooks dir (pre-push gate + commit-msg scope check, BE-0069;
#                          pre-commit/prepare-commit-msg/commit-msg secret scan, via .gitleaks.toml
#                          — a tracked file, so no local git-config registration is needed here)
#   - merge.uv-lock     -> regenerate uv.lock from pyproject.toml on conflict (BE-0043)
#   - merge.apm-generated -> regenerate apm.lock.yaml and .claude/skills/ from .apm/skills/ (BE-0390)
#   - rerere            -> replay a once-resolved conflict automatically (BE-0043)
# It also refuses to proceed when a per-worktree setting has been written to the shared config
# (issue #1803) — first, before anything else reads or writes this repository, because that
# misconfiguration silently redirects every git command in every worktree.
hooks:
	@./scripts/check_worktree_config.sh
	@[ -d .githooks ] && git config core.hooksPath .githooks && echo "hooks: core.hooksPath -> .githooks" || true
	@git config merge.uv-lock.name "regenerate uv.lock from pyproject.toml" \
	  && git config merge.uv-lock.driver "./scripts/merge-uv-lock.sh %A" \
	  && git config merge.apm-generated.name "regenerate APM's generated output from .apm/skills/" \
	  && git config merge.apm-generated.driver "./scripts/merge-apm-generated.sh %A %P" \
	  && git config rerere.enabled true \
	  && echo "hooks: uv.lock + apm generated-output merge drivers + rerere wired"

# Personal safeguard against `git push --no-verify`. `setup` above already runs this
# automatically (best-effort) on a fresh checkout; this target exists to (re)run it standalone —
# on a clone set up before this existed, after deleting the installed block, or with a
# non-default rc file via BAJUTSU_GUARD_RC_FILE. It edits the caller's shell rc, a file `hooks`
# never touches, because `--no-verify` skips every git hook unconditionally and git refuses to
# let a config alias override an existing subcommand name — a personal `git()` shell function is
# the only thing left that can see the flag before git acts on it. Scoped to repos carrying
# .githooks/no-verify-guard-marker, so it never fires outside this one. See scripts/install-no-
# verify-guard.sh and docs/ai-development.md#never-push-red for the full reasoning and limits.
git-guard-install:
	@./scripts/install-no-verify-guard.sh

# Config-aware one-command bootstrap (BE-0164): the base toolchain (`setup`) PLUS exactly the
# backend deps a project's config needs — not "every backend unconditionally", not "everything".
# Meant to run right after `git clone`, the same moment `make setup` does. Pass a config or a forced
# backend through ARGS, e.g. `make install ARGS="--config demos/showcase/showcase.config.yaml"`. With
# no config in cwd it installs nothing beyond the base (the dev gate needs no backend).
install: setup
	@./scripts/install.sh $(ARGS)

# Install the external tools an on-device iOS run needs (idempotent). Superseded by `make install`
# (config-aware); kept as the iOS-forced shortcut. The iOS backend (XCUITest) needs only Xcode's
# `xcodebuild` (a system tool, no pip extra); the Brewfile holds the sample-app build tool
# (xcodegen), which is not a bajutsu backend requirement.
deps:
	@./scripts/install.sh --backend ios
	@if command -v brew >/dev/null 2>&1; then \
	  brew bundle --file=Brewfile; \
	else \
	  echo "deps: Homebrew absent — skipping xcodegen (brew bundle); see https://brew.sh"; \
	fi

# Verify the required tools are on PATH without installing anything.
deps-check:
	@command -v xcodebuild >/dev/null 2>&1 && echo "xcodebuild (Xcode): ok" || echo "xcodebuild (Xcode): MISSING (install Xcode)"
	@command -v xcodegen >/dev/null 2>&1 && echo "xcodegen: ok" || echo "xcodegen: MISSING (make deps)"
	@command -v xcrun >/dev/null 2>&1 && echo "xcrun (Xcode): ok" || echo "xcrun (Xcode): MISSING (install Xcode)"

# Launch the web UI, installing the configured backend's deps on demand (see scripts/serve.sh). On
# macOS it also stages the bundled XCUITest Simulator runner (BE-0292) when a source checkout ships
# none, so XCUITest works out of the box; set BAJUTSU_SKIP_RUNNER_BUNDLE=1 to skip that.
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

# Shell scripts the gate lints. pre-push/pre-commit/prepare-commit-msg have no .sh suffix, so
# they're listed explicitly.
SHELL_SCRIPTS := .githooks/pre-push .githooks/commit-msg .githooks/pre-commit .githooks/prepare-commit-msg scripts/serve.sh scripts/install.sh scripts/worktree.sh scripts/preflight.sh scripts/worktree_cleanup.sh scripts/check_worktree_config.sh scripts/merge-uv-lock.sh scripts/merge-apm-generated.sh scripts/install-no-verify-guard.sh scripts/xcuitest-runner-hash.sh scripts/collect_android_diagnostics.sh scripts/android_pool_e2e.sh .claude/hooks/session-start.sh demos/tour/demo.sh

# Modules whose public surface has migrated to the Google-style docstring standard (BE-0065),
# enforced by `lint-docstrings`. This list GROWS module-by-module as more migrate; keep it the
# allowlist (not an ignore list) so an unmigrated module never accidentally falls under the gate.
DOCSTRING_PATHS := bajutsu/ai bajutsu/drivers bajutsu/assertions bajutsu/evidence/network.py bajutsu/runner bajutsu/scenario bajutsu/mcp bajutsu/cli bajutsu/common/doctor.py bajutsu/analysis/audit.py bajutsu/analysis/coverage.py bajutsu/analysis/stats.py bajutsu/trace.py bajutsu/triage/heuristic.py bajutsu/report bajutsu/evidence/core.py bajutsu/evidence/intervals.py bajutsu/evidence/redaction.py bajutsu/config bajutsu/config_source.py bajutsu/codegen/xcuitest.py bajutsu/codegen/common.py bajutsu/codegen/playwright.py bajutsu/backends.py bajutsu/capability_preflight.py bajutsu/requirements.py bajutsu/provision.py bajutsu/crawl/core.py bajutsu/crawl/serialize.py bajutsu/crawl/guide.py bajutsu/crawl/tabs.py bajutsu/agents/protocols.py bajutsu/agents/factory.py bajutsu/agents/claude.py bajutsu/agents/claude_backed.py bajutsu/agents/claude_triage.py bajutsu/agents/alerts.py bajutsu/agents/ai_config.py bajutsu/agents/anthropic_client.py bajutsu/record/loop.py bajutsu/common/screenshots.py bajutsu/evidence/visual.py bajutsu/web_network.py bajutsu/from_grouping.py

# Run the suite with a coverage floor — a regression that quietly drops coverage fails the gate.
# The floor itself is `fail_under` in pyproject.toml's [tool.coverage.report], not a flag here
# (BE-0385): pytest-cov adopts that key when `--cov-fail-under` is absent, so the gate, the drift
# advisory (`lint-pr`), and CI's job summary all read one declarative source.
# The JSON report is a gitignored side artifact two later steps read — `lint-coverage-floors` below,
# and CI's job summary (scripts/coverage_summary.py).
test:
	uv run pytest -q --cov=bajutsu --cov-report=term-missing:skip-covered --cov-report=json:coverage.json

# The whole-tree roadmap date test (`roadmap_dates` marker) is excluded from `test`/the gate: it
# git-logs every roadmap item twice, so its runtime grows with the tree and once dominated the whole
# suite. The date logic is covered fast by the mocked `test_git_dates_*` cases that DO run on every
# gate; this end-to-end pass can only regress when scripts/build_roadmap_index.py changes, so run it
# by hand after editing that loader. NOT part of `check` by design (same reasoning as `docs`).
test-roadmap-dates:
	uv run pytest tests/test_roadmap_index.py -m roadmap_dates -n0

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

# BE-0388: `tests/` runs under the same strict mypy as the rest, with one setting relaxed for the
# pattern a pytest suite conventionally uses — a bare `def test_x():` carries no return annotation,
# because pytest never inspects one. It is a second invocation rather than a
# `[[tool.mypy.overrides]]` block because `tests/` has no `__init__.py` (its helper modules are
# imported by bare name, the way pytest's prepend import mode puts `tests/` on `sys.path`), so mypy
# names every module under it by basename and no per-module pattern can select them: mypy requires
# `*` to be a whole component, so neither `tests.*` nor `test_*` matches. Basename naming has a
# second consequence: no two files under `tests/` may share a name, because a collision aborts the
# whole run with `Duplicate module named …` and reports no errors at all.
#
# `--cache-dir` keeps the two runs off each other's cache. `--allow-untyped-defs` is per-module, and
# mypy stores the options in every module's cache metadata, so a shared directory would make each
# run abandon the other's metadata ("options differ") and re-analyse from source — over all of
# `bajutsu/` too, which `tests` imports. Nesting the second cache under `.mypy_cache/` keeps
# `.gitignore` as is.
typecheck:
	uv run mypy bajutsu demos scripts
	uv run mypy --cache-dir=.mypy_cache/tests --allow-untyped-defs tests

# BE-0388: the same strict mypy, over `tests/`, with the two settings a pytest suite conventionally
# needs relaxed. A bare `def test_x():` carries no return annotation because pytest never inspects
# one, and `--no-warn-unused-ignores` holds only while the existing `# type: ignore` comments are
# swept directory by directory. It is a second invocation rather than a `[[tool.mypy.overrides]]`
# block because `tests/` has no `__init__.py` — its helper modules are imported by bare name, the way
# pytest's prepend import mode puts `tests/` on `sys.path` — so mypy names every module under it by
# basename, and a per-module pattern cannot select them (mypy requires `*` to be a whole component,
# so neither `tests.*` nor `test_*` matches). Basename naming has a second consequence: no two files
# under `tests/` may share a name, because a collision aborts the whole run with `Duplicate module
# named …` and reports no errors at all.
#
# `--cache-dir` keeps the two runs off each other's cache. Both relaxed settings are per-module, and
# mypy stores the options in every module's cache metadata, so a shared directory makes each run
# abandon the other's metadata ("options differ") and re-analyse from source — over all of `bajutsu/`
# too, which `tests` imports. Nesting the second cache under `.mypy_cache/` keeps `.gitignore` as is.
#
# Not in `check` yet: it joins `typecheck` once every directory is clean.
typecheck-tests:
	uv run mypy --cache-dir=.mypy_cache/tests --allow-untyped-defs --no-warn-unused-ignores tests

# The committed uv.lock must already satisfy pyproject — a dependency edit that forgets
# to re-lock fails here instead of silently resolving something else in CI.
lock-check:
	uv lock --check

lint-sh:
	uv run shellcheck $(SHELL_SCRIPTS)

# actionlint is a standalone Go binary (not pip/uv installable), so it needs a separate install —
# as does gitleaks (lint-secrets). CI always installs and runs it; locally we lint
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

# Check docs/architecture.md's hand-written module table against the bajutsu/ package: no row may
# name a module that does not exist, and no subpackage or top-level module may go unmentioned.
# The role prose in each row is not generated — only the row *set* is compared — so the map cannot
# quietly fall behind the tree the way it already had when this check landed (only 15 of the 43
# top-level modules were named; the 27 non-dunder gaps are grandfathered, and a new module fails
# until its row lands).
lint-module-map:
	uv run python scripts/lint_module_map.py

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
	uv run python scripts/coverage_drift.py

# Re-scan every tracked file for a committed secret with gitleaks: defense-in-depth alongside the
# pre-commit hook, which a `--no-verify` commit or a clone that skipped `make setup` never runs.
# Config is the tracked .gitleaks.toml — no per-clone registration step needed. Skips with a
# notice, like `lint-actions`/`lint-js`, when gitleaks isn't on PATH (CI always installs it, a
# pinned release of https://github.com/gitleaks/gitleaks) — an if/else, not `cmd && ... || echo
# ...`: the latter would also print (and mask a real failure behind) the "not installed" notice
# whenever `gitleaks dir` itself found a match and exited non-zero.
lint-secrets:
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks dir . --no-banner --redact; \
	else \
		echo "lint-secrets: gitleaks not installed — skipping (CI enforces it); see docs/ai-development.md"; \
	fi

# Deploy the agent skills: APM reads apm.yml, copies each .apm/skills/<name>/ source tree to
# .claude/skills/<name>/, and records a SHA-256 per deployed file in apm.lock.yaml (BE-0390).
# Both the lockfile and the deployed tree are committed, so a fresh clone has a working skill set
# without running this; run it after editing a source skill, then commit what it rewrote. A rename
# needs no extra step: `apm install` prunes the deployment it no longer owns.
# --no-policy for the same reason lint-skills passes it, and so the deploy and the audit that
# replays it run under one configuration: org-policy discovery would otherwise reach api.github.com,
# which only warns when it fails but leaves this offline-resolvable install needing the network.
skills:
	uv run apm install --no-policy

# Fail when a deployed skill file no longer matches its source. `apm audit --ci` replays the
# install and compares against the lockfile's hashes, so it catches drift in both directions: a
# deployed file edited by hand, and a source edit whose `make skills` was forgotten. Unlike
# lint-actions and lint-secrets there is no skip branch: apm-cli is a `dev` dependency
# (pyproject.toml), so uv resolves the pinned version on any clone and this step cannot pass by not
# running. The audit goes through scripts/audit_skills.py, which first mirrors the paths APM reads
# into a scratch tree holding only git-visible files: the `claude` target's governed prefix is
# `.claude/` whole, which would otherwise pull every concurrent session's `.claude/worktrees/`
# checkout — and its vendored `.venv` and `node_modules` — into the content scan, reddening the
# local gate over files CI never sees (issue #1775).
lint-skills:
	uv run python scripts/audit_skills.py

# Filter roadmap (BE) items by Status into one small table — ID / Item / Topic / Path — so an AI
# session surveys just the rows it needs (e.g. every Proposal) without paging through the dashboard's
# rendered HTML or opening each item file to check its `Status` (BE-0162). Pure and offline: reads
# roadmaps/ metadata only. The `roadmap-filter` skill wraps this.
#   make roadmap-status STATUS="Proposal"
#   STATUS is one of: Proposal / In progress / Implemented / Deferred / Rejected
roadmap-status:
	uv run python scripts/roadmap_query.py --status "$(STATUS)"

# Find roadmap (BE) items by keyword, topic, or id — the same small table as `roadmap-status`, for
# the question "is there already an item about X". Answers it from each item's title/Topic/
# Introduction excerpt instead of a grep over ~127k lines of item prose (BE-0162). Composes with a
# status, and refuses an unfiltered scan. Pure and offline, like `roadmap-status`.
#   make roadmap-find ARGS="--grep scroll"
#   make roadmap-find ARGS="--status Implemented --topic driver"
#   make roadmap-find ARGS="--id BE-0349"
roadmap-find:
	uv run python scripts/roadmap_query.py $(ARGS)

# Map the repository for a session that does not yet know where something lives: one line per
# docs/ page, or per bajutsu/ package and top-level module, or per heading of one file with its
# line span. Printed, never committed — a committed index drifts from the tree it describes, and a
# session that trusts a stale index searches in the wrong place. Needs only the standard library,
# so `python3 scripts/repo_map.py --docs` runs on any 3.11+ interpreter, with no virtualenv built.
#   make repo-map ARGS="--docs"
#   make repo-map ARGS="--code --grep driver"
#   make repo-map ARGS="--headings docs/cli.md"
repo-map:
	uv run python scripts/repo_map.py $(ARGS)

# BE-0385: fail when a source file's branch coverage drops below the floor recorded for it in
# coverage-floors.json. The global `fail_under` is one number over the whole package, so a file can
# fall from 65% to 40% while the total stays above the floor and the rest of the tree absorbs the
# loss; a per-file floor catches that. Check-only — it never writes the snapshot, mirroring the
# `format` / `format-check` split so the gate can't quietly move the bar it enforces. A rise never
# fails: blocking a PR for improving coverage would punish what the ratchet exists to encourage.
# `test` is a prerequisite, not just an earlier line in `check`, so the coverage.json this reads is
# the one this invocation produced — and since make builds a phony target once per run, `make check`
# still runs the suite exactly once.
lint-coverage-floors: test
	uv run python scripts/coverage_floors.py

# The deliberate counterpart to the check above: rewrite coverage-floors.json to what the suite just
# measured, then commit it. Normally run once coverage has risen; it is also the escape hatch for a
# drop a human decides to accept, so it prints rises and drops separately. Deliberately NOT in
# `check` — a gate that rewrote its own bar would ratchet in both directions.
coverage-floors: test
	uv run python scripts/coverage_floors.py --write

# The full gate. CI (.github/workflows/ci.yml) mirrors these steps so "green locally"
# predicts "green in CI". The uv-native checks run identically everywhere; actionlint and gitleaks
# are the exceptions — CI installs each one, and the step skips with a notice when it is absent
# (see lint-actions / lint-secrets above).
check: hooks format-check lint lint-docstrings lint-imports lint-sh lint-actions lint-js lint-roadmap lint-skills lint-module-map lint-secrets lock-check typecheck test lint-coverage-floors

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

# Build the generic XCUITest Simulator runner into the package (BE-0292), so a wheel built after
# this ships it and `xcuitest.testRunner` becomes optional. macOS + Xcode only, run at release
# time — NOT part of `check` (the deterministic gate runs anywhere and never touches the runner).
# The whole products directory is copied so the `.xctestrun`'s `__TESTROOT__` still resolves beside
# its test bundles. The output is gitignored and force-included via pyproject `artifacts`. A
# build-info.json records the Xcode / Simulator SDK the runner was built against, so `doctor` can warn
# when the host toolchain differs from it (BE-0292) rather than surfacing an opaque xcodebuild error;
# it also records a content hash of the runner's own sources (scripts/xcuitest-runner-hash.sh), so
# `scripts/serve.sh` can tell a stale bundle from a current one without re-running xcodebuild.
runner-bundle:
	$(MAKE) -C demos/showcase runner-build
	rm -rf bajutsu/_xcuitest_runner
	mkdir -p bajutsu/_xcuitest_runner
	cp -R BajutsuKit/Runner/build/dd/Build/Products/. bajutsu/_xcuitest_runner/
	printf '{"xcode": "%s", "sdk": "%s", "sourceHash": "%s"}\n' \
		"$$(xcodebuild -version | awk 'NR==1 {print $$2}')" \
		"$$(xcodebuild -version -sdk iphonesimulator SDKVersion 2>/dev/null | tr -d '[:space:]')" \
		"$$(scripts/xcuitest-runner-hash.sh)" \
		> bajutsu/_xcuitest_runner/build-info.json

# Showcase build / on-device targets live with the fixture (demos/showcase/, the single iOS app):
#   make -C demos/showcase swiftui-build|uikit-build|run-swiftui|doctor|record|ui-test|vrt

**English** · [日本語](BE-0257-layer-package-topology-ja.md)

# BE-0257 — Package the enforced architecture layers as directories

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0257](BE-0257-layer-package-topology.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0257") |
| Implementing PR | [#1045](https://github.com/bajutsu-e2e/bajutsu/pull/1045), [#1052](https://github.com/bajutsu-e2e/bajutsu/pull/1052) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md), [BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt.md), [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/` holds roughly 71 top-level modules in a flat namespace, but the codebase already has a
gate-enforced layering — deterministic core / contract / periphery — from
[BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md). Layer
membership is invisible in the directory tree: it exists only as hand-maintained module lists in
`pyproject.toml`'s `[tool.importlinter]` contracts and as prose in `docs/architecture.md`. This
proposal groups the modules that already behave as a unit into packages, so a module's layer (and
its cluster) is legible from its path, and the import-linter contracts can name one package instead
of enumerating members by hand.

## Motivation

A module's architecture layer should be visible from where it lives. Today it isn't: a reader (and
the linter config) has to carry the layer map in their head, cross-referencing
`docs/architecture.md` against a flat file listing. `pyproject.toml:216-316` spells out the
consequence directly — the three `[tool.importlinter.contracts]` blocks enumerate close to 40
individual module names by hand, purely because the layers have no package boundary to name
instead.

Several tight clusters already behave as a package in every way except the directory structure:

- **codegen** — `codegen.py` (401 lines) plus `codegen_common.py` (99), `codegen_emit.py` (55),
  `codegen_playwright.py` (521), and `codegen_uiautomator.py` (419): one generator with per-backend
  emitters, 1,495 lines total, already named with a shared `codegen_` prefix.
- **crawl** — `crawl.py` (1,256 lines) plus `crawl_flows.py`, `crawl_guide.py`, `crawl_report.py`,
  `crawl_repro.py`, and `crawl_tabs.py`: the largest module in the periphery, already split by
  `crawl_` prefix into flow/guide/report/repro/tab concerns.
- **AI / agent periphery** — `agent.py`, `agents.py`, `claude_agent.py`, `claude_enrich_agent.py`,
  `claude_triage.py`, `anthropic_client.py`, `ai_availability.py`, `enrich.py`, and `alerts.py`: nine
  modules that are all periphery-layer AI/agent surface per the BE-0112 contract, presently
  scattered across the flat namespace with no shared prefix to signal the grouping.

Three name collisions exist across layers, distinguishable today only by knowing which import path
resolves where: `bajutsu/mailbox.py` vs. `bajutsu/runner/mailbox.py`, `bajutsu/object_store.py` vs.
`bajutsu/serve/server/object_store.py`, and `bajutsu/handoff.py` vs. `bajutsu/cli/handoff.py`.
Packaging the top-level modules resolves each pair into a distinct, self-documenting path.

There is also one real import cycle: `bajutsu/config_source.py` defines `GitHubAccessError`
(`config_source.py:178`) and imports `bajutsu.github_app` lazily, inside a function body
(`config_source.py:274`), specifically to avoid a cycle with `github_app.py`, which imports
`GitHubAccessError` from `config_source` at module level (`github_app.py:24`). The deferred import
is a workaround for a layering problem, not a fix for it — moving the shared error type into a
`github/` package resolves the cycle at its root instead of routing around it.

## Detailed design

The work is MECE by cluster. Each stage lands as its own follow-up PR and is independently
verifiable via `make lint-imports` (the BE-0112 gate) — no stage depends on another landing first.
Every stage preserves public import paths through `__init__.py` re-exports, following the pattern
`bajutsu/report/__init__.py` already established (BE-0043): callers keep importing
`bajutsu.codegen.foo` etc. unchanged; only the internal module layout moves. All stages are
behavior-preserving — no runtime logic changes, only module location and import statements.

1. **`bajutsu/codegen/` package** — `codegen/__init__.py` (re-exporting the current public API),
   `codegen/common.py` (was `codegen_common.py`), `codegen/emit.py` (was `codegen_emit.py`),
   `codegen/playwright.py` (was `codegen_playwright.py`), `codegen/uiautomator.py` (was
   `codegen_uiautomator.py`).
2. **`bajutsu/crawl/` package** — `crawl/__init__.py` plus `crawl/flows.py`, `crawl/guide.py`,
   `crawl/report.py`, `crawl/repro.py`, `crawl/tabs.py`. Also extract the roughly 150-line
   serialization group — `action_to_dict`, `action_from_dict`, `screenmap_from_dict`,
   `screenmap_dict` (currently `crawl.py:1106-1218`) — into `crawl/serialize.py`, since it is a
   self-contained concern (dict (de)serialization for `Action`/`ScreenMap`) distinct from the crawl
   loop itself. Note: the crawl coordinator was already pulled out into its own class by
   [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md), so
   that extraction is out of scope here — this stage is purely a file-layout move of the remaining
   modules.
3. **`bajutsu/github/` package** — `github/__init__.py`, `github/actions.py` (was `github.py`, the
   `bajutsu run` Actions-annotation integration), and `github/app.py` (was `github_app.py`, the
   GitHub App installation-token path). Moving `GitHubAccessError` into `github/__init__.py` (or a
   small `github/errors.py`) and having `config_source.py` import it from there — rather than
   `github_app` importing it *from* `config_source` — breaks the cycle directly: the error type no
   longer lives in the module that needs to import the App path lazily to avoid it.
4. **`bajutsu/agents/` periphery package** — `agents/__init__.py` plus `agents/base.py` (was
   `agent.py`), `agents/registry.py` (was `agents.py`), `agents/claude.py` (was `claude_agent.py`),
   `agents/claude_enrich.py` (was `claude_enrich_agent.py`), `agents/claude_triage.py` (was
   `claude_triage.py`), `agents/anthropic_client.py` (was `anthropic_client.py`),
   `agents/availability.py` (was `ai_availability.py`), `agents/enrich.py` (was `enrich.py`), and
   `agents/alerts.py` (was `alerts.py`). This is the largest single cluster (nine modules) and the
   one where the BE-0112 forbidden-module list is longest today.
5. **`bajutsu/evidence/` and `bajutsu/analysis/` packages** — split the remaining evidence-adjacent
   flat modules into two packages by role: `evidence/` holds today's `bajutsu/evidence.py` moved to
   `evidence/core.py` and re-exported via `evidence/__init__.py`, plus `evidence/intervals.py`,
   `evidence/network.py`, `evidence/visual.py`, `evidence/golden.py`, and `evidence/redaction.py`
   (evidence capture and redaction, all deterministic-core per BE-0112); `analysis/` holds
   `analysis/__init__.py` plus `analysis/coverage.py`, `analysis/audit.py`, and `analysis/stats.py`
   (post-run analysis, reporting-adjacent). Keeping the two separate rather than one large package
   matches the BE-0112 layer split — `evidence` is core, `coverage`/`audit`/`stats` are consumers of
   a run's output rather than part of deriving the verdict.
6. **`bajutsu/analytics/` package** — `analytics/__init__.py` plus `analytics/usage.py` (was
   `usage.py`), `analytics/ledger.py` (was `usage_ledger.py`), and `analytics/stats.py` (was
   `usage_stats.py`): the token/cost accounting pipeline, a self-contained trio with a shared
   `usage_` prefix today.

Each stage that removes a module-list contract member lets the matching `[[tool.importlinter.contracts]]`
entry in `pyproject.toml` (`pyproject.toml:216-316`) name one package (e.g. `bajutsu.agents`) instead
of enumerating each of its former top-level modules — shrinking the hand-maintained lists as the
packaging lands, stage by stage.

## Alternatives considered

- **Keep the flat layout and rely on the import-linter contracts alone.** Rejected: the contracts
  are verbose (close to 40 modules enumerated by hand across three blocks) precisely because the
  layout has no package boundary to name instead. Packaging shrinks the contracts as a direct
  consequence, rather than asking every future contributor to keep a mental map of which flat module
  belongs to which layer.
- **Do the whole regrouping in one PR.** Rejected: the six clusters are independent of one another,
  and each is sizable enough (the crawl cluster alone is over 2,200 lines) that one combined PR would
  be difficult to review and to revert in isolation if a single stage needed rework. Staging keeps
  each move small, independently gate-verifiable, and independently revertable.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `bajutsu/codegen/` package (`__init__`/`common`/`emit`/`playwright`/`uiautomator`).
- [x] `bajutsu/crawl/` package (`__init__`/`core`/`flows`/`guide`/`report`/`repro`/`tabs`/`serialize`).
- [x] `bajutsu/github/` package (`__init__`/`actions`/`app`/`errors`), resolving the `config_source`
  ↔ `github_app` cycle.
- [ ] `bajutsu/agents/` periphery package (nine modules).
- [ ] `bajutsu/evidence/` and `bajutsu/analysis/` packages.
- [ ] `bajutsu/analytics/` package (`usage`/`ledger`/`stats`).

**Log**

- 2026-07-14 ([#1045](https://github.com/bajutsu-e2e/bajutsu/pull/1045)): stage 1 — moved the flat
  `codegen*.py` modules into a `bajutsu/codegen/` package (`xcuitest`/`common`/`emit`/`playwright`/
  `uiautomator` + a re-export `__init__`), collapsed the three codegen import-linter entries to a
  single `bajutsu.codegen`, and retargeted the `Makefile`/`e2e_changes.py`/e2e-workflow path filters
  and the bilingual docs at the package.
- 2026-07-14 ([#1052](https://github.com/bajutsu-e2e/bajutsu/pull/1052)): stage 2 — moved the flat
  `crawl*.py` modules into a `bajutsu/crawl/` package (`core` engine + `flows`/`guide`/`report`/
  `repro`/`tabs` + a re-export `__init__`), and extracted the `Action`/`ScreenMap` (de)serialization
  group into `crawl/serialize.py`. Retargeted the `bajutsu.crawl_guide` import-linter entry to
  `bajutsu.crawl.guide`, the `Makefile`/`e2e_changes.py`/e2e-workflow path filters at
  `crawl/core.py` + `crawl/serialize.py` + `crawl/__init__.py` (the engine and the re-export the
  on-device run imports — the periphery siblings stay excluded), and the bilingual docs at the
  package.

## References

- `pyproject.toml:216-316` — the three `[tool.importlinter.contracts]` blocks (BE-0112) whose
  `source_modules` / `forbidden_modules` enumerate close to 40 individual module names by hand.
- `docs/architecture.md` — the prose description of the core / contract / periphery layers that a
  packaged layout would make visible in the directory tree instead.
- `bajutsu/codegen.py`, `bajutsu/codegen_common.py`, `bajutsu/codegen_emit.py`,
  `bajutsu/codegen_playwright.py`, `bajutsu/codegen_uiautomator.py` — the codegen cluster (1,495
  lines combined).
- `bajutsu/crawl.py:1106-1218` — the `action_to_dict` / `action_from_dict` / `screenmap_from_dict` /
  `screenmap_dict` serialization group proposed for `crawl/serialize.py`.
- `bajutsu/config_source.py:178` (`GitHubAccessError` definition), `bajutsu/config_source.py:274`
  (deferred `from bajutsu.github_app import installation_token`), `bajutsu/github_app.py:24`
  (`from bajutsu.config_source import GitHubAccessError`) — the real `config_source` ↔ `github_app`
  2-cycle broken today by a deferred import.
- `bajutsu/mailbox.py` vs. `bajutsu/runner/mailbox.py`, `bajutsu/object_store.py` vs.
  `bajutsu/serve/server/object_store.py`, `bajutsu/handoff.py` vs. `bajutsu/cli/handoff.py` — the
  three cross-layer name collisions a packaged layout disambiguates by directory.
- `bajutsu/report/__init__.py` — the existing re-export pattern (BE-0043) this proposal's staged
  moves follow to keep public import paths stable.
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md) — the
  layer model and import-linter gate this proposal makes visible in the directory tree.
- [BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt.md) — prior top-level module
  naming cleanup that this proposal continues at the package level.
- [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md) — the
  crawl coordinator was already extracted into its own class; this proposal's crawl-package stage
  covers only the remaining file layout, not that extraction.
- A sibling proposal cleaning up AI/agent module naming and taxonomy is under discussion separately
  and is not referenced here by ID.

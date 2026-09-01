**English** · [日本語](BE-XXXX-bajutsu-feature-package-topology-ja.md)

# BE-XXXX — Regroup bajutsu/ into feature packages over a shared common/

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-bajutsu-feature-package-topology.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
| Related | [BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology.md), [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md), [BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/` holds 313 Python files across 77,303 lines. Three organizing principles sit side by
side in it today. Some directories are already named after the feature they implement (`crawl/`,
`serve/`, `mcp/`, `codegen/`). Others are role packages that
[BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology.md) carved out of the
deterministic core (`drivers/`, `evidence/`, `orchestrator/`, `runner/`, `assertions/`, `config/`,
`scenario/`, `report/`, `platform_lifecycle/`). The remaining 44 modules sit flat directly under
`bajutsu/`, with no package at all. Among that flat remainder are the entire `run`, `record`, and
`triage` features. Each is just one or a few files, with no directory naming it as a feature the way
`crawl/` and `serve/` already are. This proposal finishes what BE-0257 started. Every module
`bajutsu/` holds moves into one of nine feature directories (`run/`, `crawl/`, `record/`, `triage/`,
`serve/`, `mcp/`, `codegen/`, `analysis/`), or into a new `common/` package that names the shared
deterministic-core and AI-periphery infrastructure every feature draws on for the first time. Every
stage is a pure move plus an import rewrite; no stage changes runtime behavior.

## Motivation

A module's feature should be visible from where it lives. That is the same claim BE-0257 made about
architecture layer, applied one axis over. BE-0257 packaged six tight clusters: `codegen`, `crawl`,
`github`, `agents`, `evidence`/`analysis`, and `analytics`. As a direct consequence, it shrank the
`[tool.importlinter.contracts]` blocks in `pyproject.toml:300-419` that used to enumerate each
cluster's modules by hand. The clusters it left behind are exactly the ones this proposal takes on.
One set is the deterministic-core role packages BE-0257 named but did not relocate: `drivers/`,
`evidence/`, `orchestrator/`, `runner/`, `assertions/`, `config/`, `scenario/`, `report/`, and
`platform_lifecycle/`. The other is a gap BE-0257's own scope left open: the three commands a user
runs most, `run`, `record`, and `triage`, none of which has ever had a feature directory of its own.
`record`'s loop lives in a flat `record.py` plus `record_capture.py`. `triage`'s self-heal engine
lives in a flat `triage.py`. `run`'s provisioning, lease, and plan-building logic — the single
largest command module in the tree — lives in `cli/commands/run.py`, in no `run`-named package at
all. A contributor who wants to change how `bajutsu run` provisions a device has to already know the
code lives inside `cli/commands/`, a directory named for the command-line interface (CLI)'s plumbing
rather than for the feature.

BE-0257 also named, without resolving, three name collisions across layers: `bajutsu/mailbox.py`
versus `bajutsu/runner/mailbox.py`, `bajutsu/object_store.py` versus
`bajutsu/serve/server/object_store.py`, and `bajutsu/handoff.py` versus `bajutsu/cli/handoff.py`.
Each pair resolves today only if the reader already knows which import path each file answers to.
None of the three was in scope for BE-0257's six clusters, so all three stand unresolved still. A
fourth pair exists for the same reason: `bajutsu/totp.py` sits alongside
`bajutsu/orchestrator/actions/handlers/totp.py`. Moving every flat module into a named package
resolves each pair into a distinct, self-documenting path — the same fix BE-0257 applied to the
collisions inside its own six clusters.

Finally, `bajutsu/common/` does not exist as a name today. The deterministic core and the
AI-periphery infrastructure that feeds it — `agents/`, `ai/`, `analytics/`, `github/`, `cloud/` —
together span some fifteen top-level role packages, plus the bulk of the 44 flat files: a shared
foundation with no directory that says so. Once this proposal lands, a reader can tell a feature
command from the shared base it stands on by path alone. Everything a feature imports but does not
own lives under `bajutsu/common/`; `bajutsu/` itself holds only the nine feature directories, plus
`common/` and a thin `cli/` package for the handful of commands (`doctor`, `lint`, `schema`) that
belong to no single feature.

## Detailed design

The work is MECE by cluster, following the precedent BE-0257 set: each stage lands as its own
follow-up PR and is independently verifiable via `make lint-imports` and `make check` — no stage
depends on another landing first, because each stage rewrites every import to its cluster's modules
across the whole repository in the same PR that moves them. Every stage preserves public import
paths through `__init__.py` re-exports where one already exists, following the pattern
`bajutsu/report/__init__.py` established (BE-0043). All nine stages are pure file moves plus import
rewrites — no runtime logic changes. A tenth, code-free stage closes out the prose and diagrams that
no gate enforces.

Each of the nine moving stages touches the same five configuration surfaces, and a stage's PR is not
done until all five are updated for the modules it moves:

1. **`pyproject.toml`'s `[tool.importlinter]` contracts** (`pyproject.toml:300-419`) — retarget every
   enumerated module the stage moves to its new dotted path; a stage that empties a cluster's list
   collapses it to the new package name, the same shrinkage BE-0257 produced.
2. **`pyproject.toml`'s `[tool.ruff.lint.per-file-ignores]`** (`pyproject.toml:206`) — three exact
   paths (`bajutsu/_yaml.py`, `bajutsu/backends.py`, `bajutsu/runner/launch_server.py`) that move in
   stages 7 and 8.
3. **`Makefile`'s `DOCSTRING_PATHS`** (`Makefile:105`) — the list `make lint-docstrings` reads; every
   moved module that already carries Google-style docstrings keeps its entry, retargeted to the new
   path.
4. **`docs/architecture.md`'s "Module list and roles" table** — `scripts/lint_module_map.py`
   (`make lint-module-map`) fails a PR that moves a module without updating the matching row.
5. **`scripts/e2e_changes.py`'s `_PERIPHERY_EXCLUSIONS`** — the exact-path table that decides whether
   a change should trigger the on-device E2E workflows; `tests/test_e2e_changes.py` exercises it, so
   a stage that moves a listed path updates the table in the same PR.

`coverage-floors.json` needs no hand edit in any stage: running `make test && make coverage-floors`
after a move regenerates it, and the only expected diff is each moved file's entry gaining a new key
at the same coverage number it had before — any other drop is a real regression to chase down, not a
side effect of the rename.

1. **`common/analytics/`, `common/cloud/`, `common/github/`**
   (`claude/reorg-common-analytics-cloud-github`) — move today's `analytics/`, `cloud/`, and
   `github/` packages under `common/` unchanged, rewriting only their import paths. The smallest,
   most loosely coupled cluster, chosen to prove out the mechanical move-and-rewrite recipe at low
   risk before the larger stages rely on it.
2. **`common/ai/`, `common/agents/`** (`claude/reorg-common-ai-agents`) — move `ai/` and `agents/`
   under `common/`. The next-smallest cluster, independent of stage 1's.
3. **`common/evidence/`, `common/report/`, `analysis/` confirmed**
   (`claude/reorg-common-evidence-report-analysis`) — move `evidence/` and `report/` (plus the
   single file `from_grouping.py`, a grouping helper for `report/rows.py`) under `common/`, and
   confirm `analysis/` (already carved out by BE-0257) as a standalone feature directory rather than
   a `common/` subpackage, since it consumes a run's output rather than participating in deriving the
   verdict — the same core/consumer split BE-0257 drew between `evidence/` and `analysis/`. Mirrors
   BE-0257's stage 5 grouping.
4. **`common/assertions/`, `common/scenario/`**
   (`claude/reorg-common-assertions-scenario`) — move `assertions/` and `scenario/` (plus
   `interp.py`, the `${ns.key}` expansion helper `scenario/` depends on) under `common/` together, as
   the scenario-schema contract layer.
5. **`common/config/`, `common/capability/`, `common/provisioning/`**
   (`claude/reorg-common-config-capability-provisioning`) — move `config/` (plus the adjacent
   `config_source.py`, kept a sibling rather than nested inside `config/` since it plays a different
   role — sourcing a project's config binding rather than defining the schema) under `common/`;
   collect `preflight.py`, `capability_preflight.py`, and `capabilities.py` into a new
   `common/capability/` package; and collect `requirements.py` and `provision.py` into a new
   `common/provisioning/` package. `provision.py` is not a CLI command but a direct entry point
   (`scripts/install.sh` invokes it as `python -m bajutsu.provision`), so this stage also updates
   `scripts/install.sh` and the module's own `prog=` string to the new `python -m
   bajutsu.common.provisioning.provision` path.
6. **`common/drivers/`** (`claude/reorg-common-drivers`) — move `drivers/` under `common/`, along
   with the five driver-adjacent flat files `elements.py`, `dom.py`, `web_network.py`, `webview.py`,
   and `zorder.py`. Deferred to sixth rather than run earlier because `drivers/` has the widest
   fan-in of any package in the tree, so this stage waits until the move-and-rewrite recipe is
   proven on five smaller clusters first.
7. **`common/orchestrator/`, `common/runner/`**
   (`claude/reorg-common-orchestrator-runner`) — move `orchestrator/` and `runner/` under `common/`,
   along with the flat files `cancellation.py`, `backends.py`, `mailbox.py`, and `totp.py`, resolving
   the `bajutsu/mailbox.py` / `bajutsu/runner/mailbox.py` and `bajutsu/totp.py` /
   `bajutsu/orchestrator/actions/handlers/totp.py` collisions this item's Motivation names. The
   largest and most central cluster, scheduled last among the `common/` stages so every module it
   depends on already has its final path.
8. **`run/`, `record/`, `triage/`, and the remaining `common/` files**
   (`claude/reorg-run-record-triage`) — move `notify.py` to `run/notify.py` (the Slack notification
   `run` sends on completion, used by no other feature); move `record.py` to `record/loop.py` (the
   observe-propose-execute-emit loop) and `record_capture.py` to `record/capture.py`; move
   `triage.py` to `triage/heuristic.py` (the M4 self-heal engine). Collect the remaining
   `common/run_meta/` (`run_files.py`, `run_id.py`, `run_root.py`, `artifact_perms.py`,
   `object_store.py`), `common/devices/` (`device_os.py`, `device_id.py`, `device_errors.py`), and
   `common/backend_cli/` (`adb.py`, `adb_resident.py`, `simctl.py`) packages, plus the residual
   single files with no cluster of their own (`doctor.py`, `lint.py`, `screenshots.py`,
   `handoff.py`, `deprecations.py`, `diagnostics.py`, `stall_diagnostics.py`, `_yaml.py`) directly
   under `common/`. `doctor.py` and `lint.py` stay flat under `common/` rather than moving into a
   feature directory: both are named directly in the deterministic-core import-linter contract and
   are read broadly from `analysis/`, `mcp/`, and `common/runner/`, so only a thin Typer wrapper
   moves into `cli/commands/`. This stage resolves the `bajutsu/handoff.py` /
   `bajutsu/cli/handoff.py` collision this item's Motivation names, and disambiguates
   `common/backend_cli/adb.py` (the `adb`/`simctl` shell wrapper) from `common/drivers/adb.py` (the
   `Driver` implementation stage 6 moved) by directory rather than by memorizing which import path
   resolves where. Scheduled after every `common/` stage above, since it depends on every module's
   destination package already being final.
9. **CLI feature co-location** (`claude/reorg-cli-feature-colocation`) — move each single-command
   feature's `cli/commands/<feature>.py` into that feature's own `cli.py` (`run`, `crawl`, `record`,
   `triage`, `mcp`, `codegen`); move `cli/commands/{serve,worker,approve}.py` into
   `serve/cli/{serve,worker,approve}.py`, kept as separate files rather than merged into one, since a
   merged file would be harder to navigate and would grow without bound as `serve` gains
   subcommands; move `cli/commands/{audit,coverage,impact,stats,flakiness,export,trace}.py` into
   `analysis/cli/{audit,coverage,impact,stats,flakiness,export,trace}.py`, kept separate for the same
   reason; move `serve/flakiness.py` to `analysis/flakiness.py` and `trace.py` to `analysis/trace.py`
   (both already documented in `docs/architecture.md` as the same "read-only, never gates CI" family
   as `audit`/`coverage`/`impact`/`stats`); and move `dotenv.py` to `cli/dotenv.py`, since its one
   caller is `cli/__init__.py` — a flat file used from a single place stays adjacent to that caller
   rather than moving into `common/`, the same rule this stage applies throughout. Scheduled last
   among the moving stages because every module's destination package is already fixed by stages 1
   through 8, so this stage's import rewrite is a single mechanical pass with no destination left to
   decide.
10. **Documentation sweep** (`claude/reorg-docs-sweep`) — no file moves. Update
    `docs/architecture.md`'s "Dependencies (layers)" prose and diagram, the path comments in
    `.github/workflows/{android,ios}-e2e.yml` (not `paths:` triggers, so they gate nothing and can
    wait until every path is final), and any other stale path mentioned in prose. A `grep` across the
    tree for a leftover pre-move path closes the stage.

Because each stage rewrites every import to its own modules across the whole repository, no stage
depends on another landing first — the sequence above orders stages by risk and by how settled their
destination paths are, not by a real dependency. Two independent clusters (for example stages 1 and
2, or 3 through 5) can be built in parallel on separate branches, but only one merges to `main` at a
time: every stage touches the same five configuration surfaces above, so a second branch rebases onto
the first stage's merge before it can land — the conflicts should be the mechanical kind (an added
line next to an added line), but each still needs a look, not an assumption.

A reader can confirm the whole item landed by listing `bajutsu/`'s top level once stage 10 merges:
it holds exactly `run/`, `crawl/`, `record/`, `triage/`, `serve/`, `mcp/`, `codegen/`, `analysis/`,
`common/`, and a thin `cli/` for the commands that belong to no single feature — no flat `.py` module
beside `__init__.py` and `__main__.py`.

## Alternatives considered

- **Move everything in one PR.** Rejected for the same reason BE-0257 rejected it: the nine clusters
  are independent of one another, several are large on their own (`common/orchestrator/` plus
  `common/runner/` alone spans several thousand lines), and a single combined PR would be difficult
  to review and to revert in isolation if one cluster needed rework.
- **Leave `run`, `record`, and `triage` flat and package only the deterministic-core role
  directories.** Rejected: it would finish BE-0257's original scope but leave the inconsistency this
  item's Motivation opens with — `crawl/` and `serve/` legible as features, `run/` and `record/` not
  — unaddressed, for the three commands a user runs most often.
- **Skip `common/` and leave the deterministic-core packages directly under `bajutsu/`.** Rejected:
  without a name for the shared layer, a reader still cannot tell, from the top-level listing alone,
  which directories are a feature a user invokes and which are infrastructure every feature shares —
  the same illegibility BE-0257's Motivation opened with, one level up.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `bajutsu/common/analytics/`, `common/cloud/`, `common/github/`.
- [ ] `bajutsu/common/ai/`, `common/agents/`.
- [ ] `bajutsu/common/evidence/`, `common/report/`; `bajutsu/analysis/` confirmed as a feature
  directory.
- [ ] `bajutsu/common/assertions/`, `common/scenario/`.
- [ ] `bajutsu/common/config/`, `common/capability/`, `common/provisioning/`.
- [ ] `bajutsu/common/drivers/`.
- [ ] `bajutsu/common/orchestrator/`, `common/runner/`.
- [ ] `bajutsu/run/`, `record/`, `triage/`, and the remaining `common/run_meta/` / `devices/` /
  `backend_cli/` packages and residual single files.
- [ ] CLI feature co-location (`<feature>/cli.py`, `serve/cli/`, `analysis/cli/`, `cli/dotenv.py`).
- [ ] Documentation sweep (`docs/architecture.md` dependency diagram, workflow path comments, a
  final repo-wide grep for a leftover pre-move path).

## References

- [BE-0257](../BE-0257-layer-package-topology/BE-0257-layer-package-topology.md) — the precedent this
  proposal continues: six flat clusters packaged by architecture layer, using the same staged,
  MECE, `make lint-imports`-verifiable approach this item applies to the clusters BE-0257 left flat.
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md) — the
  layer model and import-linter gate both this proposal and BE-0257 make visible in the directory
  tree.
- [BE-0135](../BE-0135-module-naming-debt/BE-0135-module-naming-debt.md) — the earlier top-level
  module naming cleanup BE-0257 continued at the package level, and this proposal continues at the
  feature level.
- `pyproject.toml:300-419` — the `[tool.importlinter.contracts]` blocks each stage retargets.
- `Makefile:105` — the `DOCSTRING_PATHS` list `make lint-docstrings` reads.
- `scripts/e2e_changes.py` — the `_PERIPHERY_EXCLUSIONS` table stage 8 and stage 9 update, exercised
  by `tests/test_e2e_changes.py`.
- `docs/architecture.md` — the "Module list and roles" table `scripts/lint_module_map.py` checks
  against the tree, and the "Dependencies (layers)" prose stage 10 realigns.
- `bajutsu/mailbox.py`, `bajutsu/runner/mailbox.py`, `bajutsu/handoff.py`, `bajutsu/cli/handoff.py`,
  `bajutsu/totp.py`, `bajutsu/orchestrator/actions/handlers/totp.py` — the name collisions this
  proposal resolves by directory, left unaddressed by BE-0257's narrower scope.

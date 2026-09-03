**English** · [日本語](BE-XXXX-split-multi-class-modules-ja.md)

# BE-XXXX — Split bajutsu's multi-class modules into one class per file

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-split-multi-class-modules.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

85 files under `bajutsu/` define more than one top-level class in the same module. Together they
hold 446 classes. This item splits each of those 85 files into a package directory. Each class
moves to its own module inside that directory. The package's `__init__.py` re-exports every public
name, so every existing import path keeps resolving unchanged. A matching test file splits the same
way when one exists. The change is a pure physical reorganization: no class gains, loses, or
changes a responsibility, and no test asserts anything new.

## Motivation

Two files show the scale of the problem. `bajutsu/common/scenario/models/actions.py` packs 40
classes into 645 lines. `bajutsu/common/drivers/base.py` packs 23 classes into 1044 lines. Most of
the other 83 files follow the same pattern at a smaller scale. A reader who wants one class cannot
reach it from an editor's file tree. The tree indexes by filename, and the target class shares its
filename with dozens of others. The reader instead opens the file and searches inside it, or greps
for a line number first.

An AI agent editing one class pays that cost a second way. Reaching the target class still means
loading the whole file. An edit to one class carries the unrelated other 39 classes along in
context. A file grows as new classes get added to it, so this cost compounds: the next class added
to `actions.py` makes the file larger for every reader and every edit after it, not merely for the
one adding it.

Splitting to one class per file removes both costs at once. A filename search or a file-tree click
reaches a class directly. An edit to one class loads that class's file alone. A reader can check
that this landed without reading anything else. `LongPress` sits somewhere inside `actions.py`
today. After the split, the file tree leads straight to `actions/long_press.py`, with no
intermediate search.

## Detailed design

### Split rule

The split replaces each of the 85 files that define two or more top-level classes with a package
directory of the same name. Five rules apply.

1. **One file per class.** `foo.py` becomes `foo/`. The package's `__init__.py` carries the
   original module's docstring, then imports and re-exports every public class in the original
   declaration order (and the original `__all__`, if the file had one). Each class moves to
   `snake_case(ClassName).py`. A private class such as `_Model` moves to `_model.py`, keeping its
   leading underscore in the filename. The rule applies the same way to every kind of `class`: a
   Pydantic model, a `TypedDict`, a `Protocol`, an `Exception`, or a plain `dataclass`.
2. **Top-level functions stay together, in one file.** This item's scope is classes, not
   functions. A file's top-level functions move as a group into `_functions.py` inside the new
   package. They do not scatter into `__init__.py`, and they do not split one function per file.
   62 of the 85 files define at least one top-level function alongside their classes.
   `bajutsu/common/backend_cli/adb.py` alone defines 66. Without this rule, those functions would
   crowd back into `__init__.py` and undo the size reduction for the files carrying the most code.
   `__init__.py` re-exports a public function from `_functions.py` the same way it re-exports a
   class.
3. **Explicit intra-package imports.** A class that subclasses or references another class from the
   same original file now imports it explicitly, with a relative import
   (`from ._model import _Model`). The two classes no longer share a module scope.
4. **Shared module-level code moves to `__init__.py`.** Some code depends on more than one class, or
   belongs to none of them individually. One example is
   [`assertions.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/scenario/models/assertions.py)'s
   `_ASSERTION_KINDS`: a constant the module derives from every assertion class's fields, computed
   after the module declares every assertion class. Code like this stays in `__init__.py` rather
   than attaching to one class's file.
5. **A new circular import gets the existing lazy-import treatment.**
   `bajutsu/common/config/schema.py`'s `Config` model already imports `bajutsu.common.config.resolve`
   inside a method body, not at module load, to avoid a cycle. The split resolves any circular
   import it newly introduces the same way.

### Test-side split

Take a test file with a matching source file — for example `tests/scenario/test_models_actions.py`
for `bajutsu/common/scenario/models/actions.py`. It splits into one test module per class, the same
way the source does. The resulting test directory gets an empty `__init__.py`. `pyproject.toml`'s
`[tool.pytest.ini_options]` sets no `--import-mode`, so pytest's default `prepend` mode needs every
test module's filename to be unique across the whole suite. The empty `__init__.py` turns the new
directory into a package and heads off that constraint. Eight class names repeat across the 85
files. `DeviceError` and `Env` both name a class in `bajutsu/common/backend_cli/adb.py` and in
`bajutsu/common/backend_cli/simctl.py`, for one. Without the package boundary, two of their split
test files would collide on the same `test_device_error.py` name. A source file with no dedicated
unit-test file — covered by an integration test alone — gets no new test file. The scope here is
splitting what exists, not adding coverage that does not.

### Three existing mechanisms keyed on today's file paths

`coverage-floors.json` records a per-file coverage floor keyed by path
([BE-0385](../BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet.md)).
A path the split creates has no floor recorded yet. `scripts/coverage_floors.py` treats that gap as
informational rather than an error, so it stays harmless while the split is in progress. Running
`make coverage-floors` once, after the last batch lands, regenerates the whole snapshot with the
new paths.

The `Makefile`'s `DOCSTRING_PATHS` variable lists the modules `lint-docstrings` checks under the
BE-0065 Google-style docstring migration. 26 of today's 85 multi-class files appear in it by their
exact `.py` path. `bajutsu/common/drivers/base.py` and `bajutsu/analysis/audit.py` are two
examples. Another 15 files are already covered by a directory entry instead of a file entry.
Splitting a file listed by its exact path breaks `lint-docstrings`, unless that entry gets updated
too: the entry would otherwise name a file that no longer exists. A directory entry needs no
change. `bajutsu/common/scenario` already covers `models/actions.py` and its siblings, since
`ruff` walks the whole directory on its own.

[`docs/architecture.md`](../../docs/architecture.md)'s module-list table names 15 of the 85 files
by their exact `.py` path too:

- every file under `bajutsu/common/drivers/`
- `config_source.py`, `doctor.py`, `handoff.py`
- `provisioning/provision.py`, `provisioning/requirements.py`
- `run/notify.py`

`scripts/lint_module_map.py` runs as `make lint-module-map`, part of `make check`. It fails when a
table entry names a path absent from the tree, so splitting one of these files without renaming
its table entry breaks the gate. The same check also descends one level into `bajutsu/common/`,
looking for an undocumented subpackage. `config_source.py`, `doctor.py`, and `handoff.py` sit
directly under `common/`. Splitting one of them turns its new package directory into an
undocumented subpackage, unless the table entry moves to the new directory path in the same
commit. Renaming the entry's path cell satisfies both checks at once.

### Work breakdown

The 85 files group into 14 batches along their existing directory boundaries. Each batch is one
commit on a single PR.

| # | Batch | Files |
|---|---|---|
| 1 | `bajutsu/analysis/` | `audit.py`, `coverage.py`, `flakiness.py`, `impact.py`, `stats.py` |
| 2 | Single-file modules | `bajutsu/cli/handoff.py`, `bajutsu/codegen/common.py`, `bajutsu/run/notify.py`, `bajutsu/triage/heuristic.py` |
| 3 | `bajutsu/common/agents/` + `bajutsu/common/ai/` | `agents/alerts.py`, `agents/claude.py`, `agents/claude_triage.py`, `agents/protocols.py`, `ai/base.py` |
| 4 | `bajutsu/common/analytics/` + `bajutsu/common/assertions/` | `analytics/ledger.py`, `analytics/stats.py`, `analytics/usage.py`, `assertions/evaluate.py`, `assertions/visual.py` |
| 5 | `bajutsu/common/backend_cli/` + `bajutsu/common/cloud/` | `backend_cli/adb.py`, `backend_cli/adb_resident.py`, `backend_cli/simctl.py`, `cloud/devicefarm.py` |
| 6 | `bajutsu/common/config/` and neighbors | `config/effective.py`, `config/schema.py`, `config_source.py`, `doctor.py` |
| 7 | `bajutsu/common/drivers/` | `actuation.py`, `adb.py`, `base.py`, `fake.py`, `playwright.py`, `webview.py`, `xcuitest.py`, `xcuitest_live.py`, `zorder.py` |
| 8 | `bajutsu/common/evidence/` + `bajutsu/common/handoff.py` | `evidence/core.py`, `evidence/golden.py`, `evidence/intervals.py`, `evidence/network.py`, `handoff.py` |
| 9 | `bajutsu/common/orchestrator/` + `bajutsu/common/platform_lifecycle/` | `orchestrator/loop.py`, `orchestrator/types.py`, `orchestrator/waits.py`, `platform_lifecycle/environments/android.py`, `platform_lifecycle/environments/xcuitest.py`, `platform_lifecycle/protocols.py` |
| 10 | `bajutsu/common/provisioning/` + `run_meta/` + `runner/` | `provisioning/provision.py`, `provisioning/requirements.py`, `run_meta/object_store.py`, `runner/device_provider.py`, `runner/recovery.py` |
| 11 | `bajutsu/common/scenario/` | `models/actions.py`, `models/assertions.py`, `models/evidence.py`, `models/mocks.py`, `models/scenario.py`, `models/steps.py`, `system_alerts.py` |
| 12 | `bajutsu/crawl/` | `core.py`, `guide.py`, `report.py`, `tabs.py` |
| 13 | `bajutsu/serve/` (top level) | `artifacts.py`, `baselines.py`, `batch_provider.py`, `executor.py`, `logbus.py`, `oplog.py`, `provider_store.py`, `routes.py`, `scenarios.py`, `secrets.py`, `sessions.py`, `state.py`, `themes.py`, `uploads.py` |
| 14 | `bajutsu/serve/operations/` + `bajutsu/serve/server/` | `operations/coverage.py`, `server/db.py`, `server/executor.py`, `server/logbus.py`, `server/models.py`, `server/oauth.py`, `server/scenarios.py`, `server/sessions.py` |

Each batch's commit updates the `DOCSTRING_PATHS` entries and the `docs/architecture.md` table rows
for the files it touches, wherever that batch contains a file listed there by its `.py` path.
`make coverage-floors` and the final `make check` each run once, after every batch lands.

## Alternatives considered

| Option | Why not |
|---|---|
| Split each directory batch into its own small PR, following this repo's usual "one topic, one branch" preference | A single long-lived PR touching nearly every package is real friction. It competes for a rebase against any other branch editing the same files. `git`'s rename detection does not track a plain file becoming many, either. Splitting into 14 PRs does not remove this friction: a concurrent PR editing `bajutsu/common/drivers/` still conflicts with batch 7's commit, whether that commit sits in this PR or its own. 14 PRs would only add 14 review-and-merge round trips for files with no dependency on each other. One PR with per-batch commits keeps a single round trip, still lets a reviewer read one directory's diff at a time, and stays open only as long as the batches take to land. |
| Exclude the files where several classes are deliberately grouped as one concept's variants — `actions.py`, `assertions.py`, `bajutsu/common/config/schema.py`. A comment in `assertions.py` (line 309) says a new variant belongs in one place. | These are the files the Motivation section names as the worst case: 40 classes and 645 lines for `actions.py` alone. Excluding them would leave the file-tree and context-loading cost this item exists to remove standing in the files where it bites hardest. |
| Group related classes into a few files per module, instead of one file per class | This does not reach the goal a filename search or a file-tree click depends on: a class still shares a filename with its group. It also swaps one clear rule — one class, one file — for a second judgment call: how to draw each group's boundary. The saving is modest too, since the 446-class total shrinks only a little when a group still holds two or three classes each. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Batch 1 — split `bajutsu/analysis/` (5 files).
- [ ] Batch 2 — split the four single-file modules: `cli/handoff.py`, `codegen/common.py`,
      `run/notify.py`, `triage/heuristic.py`.
- [ ] Batch 3 — split `bajutsu/common/agents/` and `bajutsu/common/ai/` (5 files).
- [ ] Batch 4 — split `bajutsu/common/analytics/` and `bajutsu/common/assertions/` (5 files).
- [ ] Batch 5 — split `bajutsu/common/backend_cli/` and `bajutsu/common/cloud/` (4 files).
- [ ] Batch 6 — split `bajutsu/common/config/`, `config_source.py`, and `doctor.py` (4 files).
- [ ] Batch 7 — split `bajutsu/common/drivers/` (9 files).
- [ ] Batch 8 — split `bajutsu/common/evidence/` and `bajutsu/common/handoff.py` (5 files).
- [ ] Batch 9 — split `bajutsu/common/orchestrator/` and `bajutsu/common/platform_lifecycle/`
      (6 files).
- [ ] Batch 10 — split `bajutsu/common/provisioning/`, `run_meta/`, and `runner/` (5 files).
- [ ] Batch 11 — split `bajutsu/common/scenario/` (7 files).
- [ ] Batch 12 — split `bajutsu/crawl/` (4 files).
- [ ] Batch 13 — split `bajutsu/serve/` top level (14 files).
- [ ] Batch 14 — split `bajutsu/serve/operations/` and `bajutsu/serve/server/` (8 files).
- [ ] Regenerate `coverage-floors.json` with `make coverage-floors`. Confirm the diff touches
      nothing but file paths.
- [ ] Confirm `make check` passes with every batch landed.

## References

- [BE-0385 — Coverage floor continuous ratchet](../BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet.md) — defines `coverage-floors.json`, the per-file mechanism this item's final step regenerates.
- [BE-0065 — Docstring standard / API reference](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md) — defines the `DOCSTRING_PATHS` migration this item's batches must keep pointing at real paths.
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict) — the roadmap-item format this proposal follows.

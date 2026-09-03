**English** · [日本語](BE-0411-split-multi-class-modules-ja.md)

# BE-0411 — Split bajutsu's multi-class modules into one class per file

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0411](BE-0411-split-multi-class-modules.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0411") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

85 files under `bajutsu/` define more than one top-level class in the same module. Together they
hold 446 classes. This item splits each of those 85 files into a package directory. Each class
moves to its own module inside that directory. The package's `__init__.py` re-exports every public
name, so every existing import path keeps resolving unchanged. A matching test file splits the same
way when one exists. The change is a physical reorganization: no class gains, loses, or changes a
responsibility, and no test asserts anything new. The one piece of new prose it does require is a
one-line module docstring per new file, where an existing lint rule demands one (Detailed design,
*Three existing mechanisms*).

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
today. After the split, opening it resolves straight to `actions/long_press.py` from the file
tree. It needs no intermediate search.

## Detailed design

### Split rule

The split replaces each of the 85 files that define two or more top-level classes with a package
directory of the same name. Five rules apply.

1. **One file per class.** `foo.py` becomes `foo/`. The package's `__init__.py` carries the
   original module's docstring, then imports and re-exports every public class in the original
   declaration order, declaring every re-exported name in an `__all__`. The `__all__` is written
   whether or not the original file had one: `F` is in ruff's `select` (`pyproject.toml`) with no
   `__init__.py` per-file-ignore, so an import that exists only to re-export raises `F401` and
   fails `make lint` otherwise. Each class moves to `snake_case(ClassName).py`. A private class
   such as `_Model` moves to `_model.py`, keeping its leading underscore in the filename. The rule
   applies the same way to every kind of `class`: a Pydantic model, a `TypedDict`, a `Protocol`, an
   `Exception`, or a plain `dataclass`.
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
4. **Module-level code with no single owner moves to `__init__.py`; code with one owner stays in
   that owner's file.** Code genuinely used by more than one split-off class has nowhere else to
   go. One example is
   [`serve/server/models.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/serve/server/models.py)'s
   `_JSON`, a SQLAlchemy column-type variant that six different ORM model classes pass to
   `mapped_column`.

   Code tied to exactly one class or function stays in that owner's own file, even at module
   level. Moving it breaks the reference in one case, or, when a moved function rebinds it with
   `global`, stops updating it without an error: `global` cannot reach a name in another module.
   [`assertions.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/scenario/models/assertions.py)'s
   `_ASSERTION_KINDS` is the first shape: `Assertion.model_fields` derives it and
   `Assertion._one_kind` is its sole reader, so it stays with `Assertion`. `drivers/playwright.py`'s
   `_PW_ERRORS` is the second: `_playwright_error_types` rebinds it with `global` to memoize an
   optional import, so the memo stays wherever rule 2 sends that function — `_functions.py`.
5. **A new circular import gets the existing lazy-import treatment.**
   `bajutsu/common/config/schema.py`'s `Config` model already imports `bajutsu.common.config.resolve`
   inside a method body, not at module load, to avoid a cycle. The split resolves any circular
   import it newly introduces the same way.

### Implementation: a parsing script, not manual edits

A Python script applies rules 1–3 and 5 mechanically, run once per batch against that batch's file
list — not an editor, human or AI, splitting each file by hand. An AI agent reading and rewriting
446 classes one at a time would spend tokens roughly in proportion to that count; a script pays a
fixed authoring cost once and then runs at no further token cost per batch. It parses each target
file with
[`libcst`](https://github.com/Instagram/LibCST) rather than the standard-library `ast`, since `ast`
drops comments, and many classes in these files carry a leading comment explaining a design choice
that the split must not lose. The script extracts each top-level `class` or `def` node together
with its leading comment block and writes it, byte-for-byte, to the file rule 1 or rule 2 names. It
scans each extracted class's body for references to sibling top-level names and emits the relative
import rule 3 needs. It writes `__init__.py` from the same parse: the original module docstring,
one import per class or function, and the `__all__` list. `libcst` becomes a dev-only dependency
for this one script, not a runtime one.

The script does not decide rule 4's two-case split — whether a piece of module-level code has a
single owner or none — since that call needs the same judgment this design applied to
`_ASSERTION_KINDS` and `_PW_ERRORS`. That judgment, and authoring each new module's `D100`
docstring, are the two things each batch still does by hand, working from the rules and examples
above.

The script earns trust the same way any other piece of this codebase does: with its own test
suite (`tests/test_split_modules.py` or similar), covering each rule above against small fixture
modules — a class with a leading comment, a class referencing a sibling, a file with `__all__` and
one without, a function `global`-rebinding a module-level name. That suite passes before the
script runs against a single real file. Running it against real files, batch by batch, is what
the batch entries in *Progress* below track.

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

A test that patches by module path gets re-pointed in the same commit as its source file.
`tests/` calls `monkeypatch.setattr("bajutsu.<module>.<name>", ...)` in about a hundred places.
Patching a name that `__init__.py` merely re-exports rebinds the re-export, not the binding the
caller resolves. That break stays quiet in some cases and raises in others.

`bajutsu.run.notify._RETRY_DELAY` (four call sites, batch 2) is a quiet case. `_deliver` moves to
`_functions.py` with its own binding under rule 2. The patch stops applying, and the retry test
sleeps for real instead of exercising the patched value.
`bajutsu.common.drivers.playwright._PW_ERRORS` and `bajutsu.common.drivers.xcuitest.XcuitestDriver`
(batch 7) fail the same quiet way.

`bajutsu.common.drivers.base.time.sleep` and `.time.monotonic` (ten call sites, batch 7) raise
`AttributeError` instead, once `base/__init__.py` no longer imports `time` itself.

### Four existing mechanisms keyed on today's file paths

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
change to the entry itself.

A directory entry carries a consequence the file entries do not, though. `lint-docstrings` selects
the whole `D` family minus `D102`/`D105`/`D107`, which leaves `D100` (missing docstring in a public
module) active. It applies to every module the split creates under any of the 41 covered files —
both the 26 renamed from a file entry and the 15 already covered by a directory entry.
`bajutsu/common/scenario` already covers `models/actions.py`, so each of its 40 new per-class
modules needs its own module docstring written, not merely carried over. Across all 41 covered
files, that totals 275 new module docstrings to author. That is real prose, not a mechanical
carry-over, and a cost the Introduction's framing does not fully capture on its own.
`bajutsu/common/scenario` and `bajutsu/common/drivers/` (batches 11 and 7) hold the largest share
of it. `actions.py` and `base.py` are the two files with the most classes to begin with.

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

`pyproject.toml`'s `[tool.importlinter]` contracts name `bajutsu.common.drivers.base` as a
`source_modules` or `forbidden_modules` entry in three contracts. Two more modules this split
touches join it there: `bajutsu.common.drivers.actuation` and `bajutsu.common.doctor`. These
contracts guard the deterministic core's independence from the periphery.

Those entries need no change. import-linter resolves a named module to itself plus every
descendant, so a plain module turning into a package stays covered — the same subtree semantics
the artifact-sink contract's own comment in `pyproject.toml` already relies on.

One entry names an exact edge rather than a subtree, and that one does break. The contract that
keeps the scenario schema and `Driver` Protocol a portable inner layer has an `ignore_imports`
entry covering a single edge: `bajutsu.common.drivers.base -> bajutsu.common.evidence.network`.
That edge is the `Driver` Protocol's reference to `Collector`, guarded by `TYPE_CHECKING`, made for
its `network_collector` signature. `Driver` moves to its own file under batch 7. The real edge then
becomes `bajutsu.common.drivers.base.driver -> bajutsu.common.evidence.network`, or whatever module
`Driver` lands in. The recorded string no longer matches it. `unmatched_ignore_imports_alerting`
defaults to `error`, so `make lint-imports` fails on the stale entry. Batch 7's commit updates it
to the new module path.

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
for the files it touches, wherever that batch contains a file listed there by its `.py` path, and
writes the module docstring `D100` requires into every new module under a `DOCSTRING_PATHS`-covered
file. Batch 7's commit also updates the `ignore_imports` entry in `pyproject.toml`.
`make coverage-floors` and the final `make check` each run once, after every batch lands.

## Alternatives considered

| Option | Why not |
|---|---|
| Split each directory batch into its own small PR, following this repo's usual "one topic, one branch" preference | A single long-lived PR touching nearly every package is real friction. It competes for a rebase against any other branch editing the same files. `git`'s rename detection does not track a plain file becoming many, either. Splitting into 14 PRs does not remove this friction: a concurrent PR editing `bajutsu/common/drivers/` still conflicts with batch 7's commit, whether that commit sits in this PR or its own. 14 PRs would only add 14 review-and-merge round trips for files with no dependency on each other. One PR with per-batch commits keeps a single round trip, still lets a reviewer read one directory's diff at a time, and stays open only as long as the batches take to land. |
| Exclude the files where several classes are deliberately grouped as one concept's variants — `actions.py`, `assertions.py`, `bajutsu/common/config/schema.py`. A comment in `assertions.py` (line 309) says a new variant belongs in one place. | These are the files the Motivation section names as the worst case: 40 classes and 645 lines for `actions.py` alone. Excluding them would leave the file-tree and context-loading cost this item exists to remove standing in the files where it bites hardest. |
| Group related classes into a few files per module, instead of one file per class | This does not reach the goal a filename search or a file-tree click depends on: a class still shares a filename with its group. It also swaps one clear rule — one class, one file — for a second judgment call: how to draw each group's boundary. The saving is modest too, since the 446-class total shrinks only a little when a group still holds two or three classes each. |
| Split each file by hand — a human or an AI agent, editing one file at a time | 446 classes across 85 files is 446 chances to drop a leading comment, mis-copy a class body, or forget an intra-package import. A script applies rules 1–3 and 5 the same way every time and cannot introduce that class of error; the batches still need human judgment for rule 4's ownership calls and for authoring `D100` docstrings, but that judgment shrinks to a handful of cases per batch instead of 446. An AI agent editing 446 classes one at a time also spends tokens roughly in proportion to that count, where a script pays a fixed authoring cost once. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Write the `libcst`-based splitting script and its test suite. Confirm the suite passes
      before running the script against any real file.
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

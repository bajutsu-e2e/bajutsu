**English** · [日本語](BE-0411-split-multi-class-modules-ja.md)

# BE-0411 — Split bajutsu's multi-class modules into one class per file

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0411](BE-0411-split-multi-class-modules.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0411") |
| Implementing PR | [#1940](https://github.com/bajutsu-e2e/bajutsu/pull/1940) (the split), [#1946](https://github.com/bajutsu-e2e/bajutsu/pull/1946) (test-side split closed) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

86 files under `bajutsu/` define more than one top-level class in the same module. Together they hold 459 classes. This item splits each of those 86 files into a package directory. Each class moves to its own module inside that directory. The package's `__init__.py` re-exports every public name. Every existing import path then keeps resolving unchanged. A matching test file splits the same way when one exists.

The change is a physical reorganization. No class gains, loses, or changes a responsibility, and no test asserts anything new. The one piece of new prose it requires is a one-line module docstring per new file. An existing lint rule demands the docstring (Detailed design, *Three existing mechanisms*).

## Motivation

Two files show the scale of the problem. `bajutsu/common/scenario/models/actions.py` packs 40 classes into 645 lines. `bajutsu/common/drivers/base.py` packs 23 classes into 1044 lines. Most of the other 84 files follow the same pattern at a smaller scale.

A reader who wants one class cannot reach it from an editor's file tree. The tree indexes by filename, and the target class shares its filename with dozens of others. The reader instead opens the file and searches inside it, or greps for a line number first.

An AI agent editing one class pays that cost a second way. Reaching the target class still means loading the whole file. An edit to one class carries the other 39 unrelated classes along in context. A file grows as new classes get added to it, so this cost compounds. The next class added to `actions.py` makes the file larger for every later reader and every later edit.

Splitting to one class per file removes both costs at once. A filename search or a file-tree click reaches a class directly. An edit to one class loads that class's file alone.

A reader can check that this landed without reading anything else. `LongPress` sits somewhere inside `actions.py` today. After the split, opening it resolves straight to `actions/long_press.py` from the file tree, with no intermediate search.

## Detailed design

### Split rule

The split replaces each of the 86 files that define two or more top-level classes with a package directory of the same name. Six rules apply.

1. **One file per class.** `foo.py` becomes `foo/`. The package's `__init__.py` carries the original module's docstring. It then imports and re-exports every public class in the original declaration order, and it declares every re-exported name in an `__all__`. The `__all__` is written even when the original file had none. `F` is in ruff's `select` (`pyproject.toml`) with no `__init__.py` per-file-ignore. Without the `__all__`, an import that exists to re-export alone raises `F401` and fails `make lint`.

   Each class moves to `snake_case(ClassName).py`. A private class such as `_Model` moves to `_model.py`, keeping its leading underscore in the filename. The rule applies the same way to every kind of `class`: a Pydantic model, a `TypedDict`, a `Protocol`, an `Exception`, or a plain `dataclass`.
2. **Top-level functions stay together, in one file.** This item's scope is classes, not functions. A file's top-level functions move as a group into `_functions.py` inside the new package. They do not scatter into `__init__.py`, and they do not split one function per file.

   62 of the 86 files define at least one top-level function alongside their classes. `bajutsu/common/backend_cli/adb.py` alone defines 66. Without this rule, those functions would crowd back into `__init__.py`. That would undo the size reduction for the files carrying the most code. `__init__.py` re-exports a public function from `_functions.py` the same way it re-exports a class.
3. **Explicit intra-package imports.** A class that subclasses or references another class from the same original file now imports that class explicitly. The import is relative (`from ._model import _Model`). The two classes no longer share a module scope.
4. **Module-level code with no single owner moves to `__init__.py`; code with one owner stays in that owner's file.** Code that more than one split-off class uses has nowhere else to go. One example is [`serve/server/models.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/serve/server/models.py)'s `_JSON`, a SQLAlchemy column-type variant that six different ORM model classes pass to `mapped_column`.

   Code tied to exactly one class or function stays in that owner's own file, even at module level. Moving the code breaks the reference in one case. In the other case, a moved function rebinds the name with `global`. The name then stops updating without an error, because `global` cannot reach a name in another module.

   [`assertions.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/bajutsu/common/scenario/models/assertions.py)'s `_ASSERTION_KINDS` is the first shape. `Assertion.model_fields` derives it, and `Assertion._one_kind` is its sole reader. It stays with `Assertion`. `drivers/playwright.py`'s `_PW_ERRORS` is the second shape. `_playwright_error_types` rebinds it with `global` to memoize an optional import. The memo stays wherever rule 2 sends that function, which is `_functions.py`.
5. **A new circular import gets the existing lazy-import treatment.** The `Config` model in `bajutsu/common/config/schema.py` already imports `bajutsu.common.config.resolve` inside a method body, not at module load, to avoid a cycle. The split resolves any new circular import the same way.
6. **An entry-point guard moves to `__main__.py`.** `python -m <package>` runs `__main__.py`, never `__init__.py`. A guard left in the package body loses its entry point, and nothing raises. `bajutsu/common/provisioning/provision.py` is the one file of the 86 with an `if __name__ == "__main__":` block. Both `scripts/install.sh` and the web-e2e workflow invoke it that way.

### Implementation: a parsing script, not manual edits

A Python script applies rules 1–3 and 5–6 mechanically. It runs once per batch against that batch's file list. An editor, human or AI, does not split each file by hand.

An AI agent that read and rewrote 459 classes one at a time would spend tokens roughly in proportion to that count. A script pays a fixed authoring cost once, then runs at no further token cost per batch.

The script parses each target file with [`libcst`](https://github.com/Instagram/LibCST) rather than the standard-library `ast`. The `ast` module drops comments. Many classes in these files carry a leading comment that explains a design choice, and the split must not lose it.

The script then works in four steps:

1. It extracts each top-level `class` or `def` node together with its leading comment block.
2. It writes the node, byte-for-byte, to the file that rule 1 or rule 2 names.
3. It scans each extracted class's body for references to sibling top-level names, and emits the relative import that rule 3 needs.
4. It writes `__init__.py` from the same parse: the original module docstring, one import per class or function, and the `__all__` list.

`libcst` becomes a dev-only dependency for this one script, not a runtime one.

The script does not decide rule 4's two-case split, which asks whether a piece of module-level code has a single owner or none. That call needs the same judgment that this design applied to `_ASSERTION_KINDS` and `_PW_ERRORS`. Each batch still does two things by hand, working from the rules and examples above: that judgment, and authoring each new module's `D100` docstring.

The script earns trust the same way any other piece of this codebase does: with its own test suite (`tests/test_split_modules.py` or similar). The suite covers each rule above against small fixture modules:

- a class with a leading comment
- a class referencing a sibling
- a file with `__all__` and one without
- a function `global`-rebinding a module-level name

That suite passes before the script runs against a single real file. Running the script against real files, batch by batch, is what the batch entries in *Progress* below track.

### Test-side split

Take a test file with a matching source file. One example is `tests/scenario/test_models_actions.py` for `bajutsu/common/scenario/models/actions.py`. The test file splits into one test module per class, the same way the source does. The resulting test directory gets an empty `__init__.py`.

The empty `__init__.py` heads off a constraint. `pyproject.toml`'s `[tool.pytest.ini_options]` sets no `--import-mode`, so pytest's default `prepend` mode needs every test module's filename to be unique across the whole suite. The empty `__init__.py` turns the new directory into a package.

Eight class names repeat across the 85 files. `DeviceError` and `Env` are one case. Each names a class in both `bajutsu/common/backend_cli/adb.py` and `bajutsu/common/backend_cli/simctl.py`. Without the package boundary, two of their split test files would collide on the same `test_device_error.py` name.

A source file with no dedicated unit-test file gets no new test file. An integration test alone covers such a file. The scope here is splitting what exists, not adding coverage that does not exist.

A test that patches by module path gets re-pointed in the same commit as its source file. `tests/` calls `monkeypatch.setattr("bajutsu.<module>.<name>", ...)` in about a hundred places. Patching a name that `__init__.py` merely re-exports rebinds the re-export. The caller resolves a different binding. That break stays quiet in some cases and raises in others.

Quiet cases:

- `bajutsu.run.notify._RETRY_DELAY` (four call sites, batch 2). `_deliver` moves to `_functions.py` with its own binding under rule 2. The patch stops applying. The retry test then sleeps for real instead of exercising the patched value.
- `bajutsu.common.drivers.playwright._PW_ERRORS` and `bajutsu.common.drivers.xcuitest.XcuitestDriver` (batch 7). They fail the same quiet way.

A raising case:

- `bajutsu.common.drivers.base.time.sleep` and `.time.monotonic` (ten call sites, batch 7). They raise `AttributeError` once `base/__init__.py` no longer imports `time` itself.

### Five existing mechanisms keyed on today's file paths

**Coverage floors.** `coverage-floors.json` records a per-file coverage floor keyed by path ([BE-0385](../BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet.md)). A path that the split creates has no floor recorded yet. `scripts/coverage_floors.py` treats that gap as informational, not as an error. The gap stays harmless while the split is in progress. Running `make coverage-floors` once, after the last batch lands, regenerates the whole snapshot with the new paths.

**Docstring lint.** The `Makefile`'s `DOCSTRING_PATHS` variable lists the modules that `lint-docstrings` checks under the BE-0065 Google-style docstring migration. 26 of today's 85 multi-class files appear in it by their exact `.py` path. `bajutsu/common/drivers/base.py` and `bajutsu/analysis/audit.py` are two examples. Another 15 files are already covered by a directory entry instead of a file entry.

Splitting a file listed by its exact path stops `lint-docstrings` from checking the file. The drop is silent, not loud, because `ruff` exits 0 on a path that is not there. A stale entry stops enforcing docstrings on that surface and says nothing. The implementation adds an existence guard to the `lint-docstrings` recipe. The guard turns that silent drop into a hard stop. A directory entry needs no change to the entry itself.

A directory entry carries a consequence that the file entries do not. `lint-docstrings` selects the whole `D` family minus `D102`/`D105`/`D107`, which leaves `D100` (missing docstring in a public module) active. `D100` applies to every module the split creates under any of the 41 covered files. That count covers both the 26 renamed from a file entry and the 15 already covered by a directory entry.

`bajutsu/common/scenario` already covers `models/actions.py`. Each of its 40 new per-class modules therefore needs its own module docstring written, not merely carried over. Across all 41 covered files, that totals 275 new module docstrings to author. That is real prose, not a mechanical carry-over. The Introduction's framing does not fully capture the cost on its own. `bajutsu/common/scenario` and `bajutsu/common/drivers/` (batches 11 and 7) hold the largest share of it. `actions.py` and `base.py` are the two files with the most classes to begin with.

**The module-list table.** [`docs/architecture.md`](../../docs/architecture.md)'s module-list table names 15 of the 85 files by their exact `.py` path too:

- every file under `bajutsu/common/drivers/`
- `config_source.py`, `doctor.py`, `handoff.py`
- `provisioning/provision.py`, `provisioning/requirements.py`
- `run/notify.py`

`scripts/lint_module_map.py` runs as `make lint-module-map`, part of `make check`. It fails when a table entry names a path absent from the tree. Splitting one of these files without renaming its table entry therefore breaks the gate.

The same check also descends one level into `bajutsu/common/`, looking for an undocumented subpackage. `config_source.py`, `doctor.py`, and `handoff.py` sit directly under `common/`. Splitting one of them turns its new package directory into an undocumented subpackage. The table entry must move to the new directory path in the same commit. Renaming the entry's path cell satisfies both checks at once.

**The on-device lane selector.** [`scripts/e2e_changes.py`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/e2e_changes.py) decides which on-device CI lanes a change triggers. It names about thirty modules by their exact `.py` path. The names appear in three places: the per-lane patterns, the shared run filter, and the periphery exclusions.

Splitting one module without updating its entry reproduces what the script's own comment records from [BE-0252](../BE-0252-config-package-split/BE-0252-config-package-split.md). Every file under the new package stops matching. The lane's `changes` job then reports nothing relevant. The required aggregator goes green without running a thing. The script's own tests catch a stale plain path but not a stale regular expression, so each batch checks both.

**Import-linter contracts.** `pyproject.toml`'s `[tool.importlinter]` contracts name `bajutsu.common.drivers.base` as a `source_modules` or `forbidden_modules` entry in three contracts. Two more modules that this split touches join it there: `bajutsu.common.drivers.actuation` and `bajutsu.common.doctor`. These contracts guard the deterministic core's independence from the periphery.

Those entries need no change. import-linter resolves a named module to itself plus every descendant. A plain module that turns into a package stays covered. The artifact-sink contract's own comment in `pyproject.toml` already relies on the same subtree semantics.

One entry names an exact edge, not a subtree, and that entry does break. The contract that keeps the scenario schema and `Driver` Protocol a portable inner layer has an `ignore_imports` entry covering a single edge: `bajutsu.common.drivers.base -> bajutsu.common.evidence.network`. That edge is a `TYPE_CHECKING`-guarded reference to `Collector`. It serves the `network_collector` signature, which belongs to `EvidenceProvider`, not to `Driver`.

Batch 7 gives that Protocol its own file, so the real edge becomes `bajutsu.common.drivers.base.evidence_provider -> bajutsu.common.evidence.network`. The recorded string no longer matches it. `unmatched_ignore_imports_alerting` defaults to `error`. `make lint-imports` therefore fails on the stale entry, and batch 7's commit updates the entry to the new module path.

### Work breakdown

The 86 files group into 14 batches along their existing directory boundaries. Each batch is one commit on a single PR.

| # | Batch | Files |
|---|---|---|
| 1 | `bajutsu/analysis/` | `audit.py`, `coverage.py`, `flakiness.py`, `impact.py`, `stats.py` |
| 2 | Single-file modules | `bajutsu/cli/handoff.py`, `bajutsu/codegen/common.py`, `bajutsu/run/notify.py`, `bajutsu/triage/heuristic.py`, `bajutsu/common/devices/errors.py` |
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

Each batch's commit does two things:

- It updates the `DOCSTRING_PATHS` entries and the `docs/architecture.md` table rows for the files it touches, wherever the batch contains a file listed there by its `.py` path.
- It writes the module docstring that `D100` requires into every new module under a `DOCSTRING_PATHS`-covered file.

Batch 7's commit also updates the `ignore_imports` entry in `pyproject.toml`. `make coverage-floors` and the final `make check` each run once, after every batch lands.

## Alternatives considered

| Option | Why not |
|---|---|
| Split each directory batch into its own small PR, following this repo's usual "one topic, one branch" preference | A single long-lived PR touching nearly every package is real friction. It competes for a rebase against any other branch editing the same files. `git`'s rename detection does not track a plain file becoming many, either. Splitting into 14 PRs does not remove this friction: a concurrent PR editing `bajutsu/common/drivers/` still conflicts with batch 7's commit, whether that commit sits in this PR or its own. 14 PRs would only add 14 review-and-merge round trips for files with no dependency on each other. One PR with per-batch commits keeps a single round trip, still lets a reviewer read one directory's diff at a time, and stays open only as long as the batches take to land. |
| Exclude the files where several classes are deliberately grouped as one concept's variants — `actions.py`, `assertions.py`, `bajutsu/common/config/schema.py`. A comment in `assertions.py` (line 309) says a new variant belongs in one place. | These are the files the Motivation section names as the worst case: 40 classes and 645 lines for `actions.py` alone. Excluding them would leave the file-tree and context-loading cost this item exists to remove standing in the files where it bites hardest. |
| Group related classes into a few files per module, instead of one file per class | This does not reach the goal a filename search or a file-tree click depends on: a class still shares a filename with its group. It also swaps one clear rule — one class, one file — for a second judgment call: how to draw each group's boundary. The saving is modest too, since the 446-class total shrinks only a little when a group still holds two or three classes each. |
| Split each file by hand — a human or an AI agent, editing one file at a time | 459 classes across 86 files is 459 chances to drop a leading comment, mis-copy a class body, or forget an intra-package import. A script applies rules 1–3 and 5–6 the same way every time and cannot introduce that class of error; the batches still need human judgment for rule 4's ownership calls and for authoring `D100` docstrings, but that judgment shrinks to a handful of cases per batch instead of 459. An AI agent editing 459 classes one at a time also spends tokens roughly in proportion to that count, where a script pays a fixed authoring cost once. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Write the `libcst`-based splitting script and its test suite. Confirm the suite passes
      before running the script against any real file.
- [x] Batch 1 — split `bajutsu/analysis/` (5 files).
- [x] Batch 2 — split the five single-file modules: `cli/handoff.py`, `codegen/common.py`,
      `run/notify.py`, `triage/heuristic.py`, `common/devices/errors.py`.
- [x] Batch 3 — split `bajutsu/common/agents/` and `bajutsu/common/ai/` (5 files).
- [x] Batch 4 — split `bajutsu/common/analytics/` and `bajutsu/common/assertions/` (5 files).
- [x] Batch 5 — split `bajutsu/common/backend_cli/` and `bajutsu/common/cloud/` (4 files).
- [x] Batch 6 — split `bajutsu/common/config/`, `config_source.py`, and `doctor.py` (4 files).
- [x] Batch 7 — split `bajutsu/common/drivers/` (9 files).
- [x] Batch 8 — split `bajutsu/common/evidence/` and `bajutsu/common/handoff.py` (5 files).
- [x] Batch 9 — split `bajutsu/common/orchestrator/` and `bajutsu/common/platform_lifecycle/`
      (6 files).
- [x] Batch 10 — split `bajutsu/common/provisioning/`, `run_meta/`, and `runner/` (5 files).
- [x] Batch 11 — split `bajutsu/common/scenario/` (7 files).
- [x] Batch 12 — split `bajutsu/crawl/` (4 files).
- [x] Batch 13 — split `bajutsu/serve/` top level (14 files).
- [x] Batch 14 — split `bajutsu/serve/operations/` and `bajutsu/serve/server/` (8 files).
- [x] Regenerate `coverage-floors.json` with `make coverage-floors`. Confirm the diff touches
      nothing but file paths.
- [x] Confirm `make check` passes with every batch landed.
- [x] ~~Split each test file that mirrors a split source file, one test module per class.~~
  - Measured against the finished split, then decided against. 57 test files mirror a split
    package by name, and they hold 1,771 cases. 61 percent of those cases name none of that
    package's classes. 30 percent name one class. 10 percent name two or more. Of the 327
    classes in those packages, 96 turn up by name in a case in their mirror file. In 41 of the
    57 files, under half the cases resolve to a single class. A per-class split would thus leave
    six cases in ten with no owner. It would produce an empty test module for seven classes in
    ten. The cause is the one rule 2 already names on the source side. These files group
    their cases around a module's functions. The split keeps those functions together in
    `_functions.py`. Other cases group around end-to-end behavior that runs through a public
    entry point. Splitting per class would re-partition the suite along a boundary its cases do
    not follow. It would not mirror the source, which is what this unit asked for.
  - The navigability cost this item exists to remove is real on the test side too.
    `tests/test_adb.py` runs to 2,730 lines and `tests/test_xcuitest.py` to 2,395. The boundary
    that would divide them is subject matter, not class. Drawing it needs a design of its own.
    That design must settle where each subject ends and whether a size ceiling enforces the
    result. It must also keep the unique filenames pytest's `prepend` import mode requires.
    That is a roadmap item of its own rather than a unit of this one, and none exists yet.

Log:

- [#1940](https://github.com/bajutsu-e2e/bajutsu/pull/1940) — every batch, in one pass.
  `scripts/split_modules.py` and its suite land first. The 14 batches then
  follow in order. No module under `bajutsu/` now defines more than one
  top-level class. The whole existing test suite passes unaltered
  throughout. That is what shows the reorganization carries no behavior
  with it.

  Running the script against real code found eight defects in it. Each one got a regression test
  before the batch it blocked. Three rounds of self-review over the finished diff found eleven
  more, listed after these.

  - Tuple-unpacking assignment targets were invisible, so nothing imported the names they bind.
  - Python Enhancement Proposal 695 (PEP 695) type-parameter bounds went unread.
  - A quoted forward reference written outside an annotation went unread too.
  - A class-body field sharing a sibling's name read as a reference to that sibling.
  - A name an inner `import` binds counted as a read.
  - Module-level code derived from its own owner came out above that owner.
  - Reads merged per declaration, not per target file. A module's last function
    was then the sole one counted as an owner.

  Two rules changed shape under real code. Deferring an annotation-only
  sibling under `TYPE_CHECKING` breaks Pydantic. Pydantic resolves
  annotations at run time. A sibling import is now a runtime import by
  default, and a cycle is what defers it. Rule 4's no-single-owner case
  splits in two. Code that binds nothing runs at the end of
  `__init__.py`, where this item's design puts it. Code that binds a name
  goes to a `_shared.py` instead, because `__init__.py` re-exports the
  modules that read that name back.

  Rule 4 also counts a module-level statement as an owner of another.
  `oplog.py`'s `_CONTEXT_KEYS` and the context variables beside it had
  landed in two files that then imported each other at module load.
  Fifteen new cycles took rule 5's in-method import by hand, each on its
  single edge.

  Forty-two `monkeypatch` targets across the suite now name the module that owns the binding. Most
  were the quiet kind this item's design predicts. A retry test would have slept for real. A
  cache-hit test asserting a call is *not* made would have passed vacuously. So would every
  rejection test that lowers an upload cap. One cost more than correctness. A
  cold-startup ceiling the patch no longer reached left
  `test_spawn_cold_discards_a_never_ready_runner` waiting out its real 120
  seconds on every run of the gate. It now takes a quarter of a second.

  Three rounds of self-review found eleven further defects of one kind. The script
  produced a wrong-but-plausible package and reported success.

  - It wrote an unbreakable cycle to disk under a note.
  - It read a `Literal["Beta"]` string as a forward reference.
  - It brought a sibling back as a module-level import that a method already
    imported for itself. That restored a cycle rule 5 had broken by hand.
  - It re-exported a `global`-rebound name by value. The package attribute then
    froze at import while the real binding moved on, which is what had made one
    usage-ledger assertion unconditionally true.
  - It pruned a method's local import file-wide. The module-level statement beside
    it then lost the header import it still read.
  - It read every subscript as annotation context. A runtime read inside one was
    then free to move under `TYPE_CHECKING`, where the name resolves to nothing.

  Each of the eleven is a refusal or a fix now. The tidy-up step, the delete step,
  and the batch loop each report a fault rather than an exit code of zero. The
  suite grew from 41 tests to 94, at 92 percent branch coverage of the script. It
  now writes three representative plans into real packages, imports them, and
  lints them. That is the script's actual contract, which every other assertion
  had stood in for.

  `make check` is green, and total coverage measures 94.71 percent across
  the suite. The test-side split is the one part of the design left
  undone. These test files group their cases by function, not by source class.
  Rule 2 already keeps a module's functions together.
  Splitting them per class would re-partition the suite rather than
  mirror the source.

  A twelfth defect surfaced in CI alone. `.gitignore` carries an unanchored `uploads/` pattern.
  It exists for the bundle directories `serve` extracts at run time.
  `bajutsu/serve/uploads.py` became a directory and walked straight into it.
  `git add` then skipped all six generated files in silence.
  Every local check still passed, because the working tree held the package.
  CI cloned a branch that had lost the module with nothing in its place.
  The pattern now re-includes that one source path by name.
  The script also refuses a package path Git already ignores, before it writes anything.
  A collision between a module's name and an ignore pattern hides from every local check.
  The refusal has to come from the tool that creates the directory.

- [#1946](https://github.com/bajutsu-e2e/bajutsu/pull/1946) — closes the item's last open unit.
  The test-side split ships no code under this item. Measuring the finished split answered the
  question the design left open. 57 test files mirror a split package by name, and they hold
  1,771 cases. 61 percent of them name none of that package's classes. Of those packages' 327
  classes, 96 turn up by name in a case in their mirror file. The suite groups its cases around
  a module's functions and around end-to-end behavior. Rule 2 preserves the first of those
  groupings on the source side, so the split did not create it. A per-class split would have
  invented an owner for six cases in ten. It would have left an empty module for seven classes
  in ten. Every other unit having landed, the item is `Implemented`.

## References

- [BE-0385 — Coverage floor continuous ratchet](../BE-0385-coverage-floor-continuous-ratchet/BE-0385-coverage-floor-continuous-ratchet.md) — defines `coverage-floors.json`, the per-file mechanism this item's final step regenerates.
- [BE-0065 — Docstring standard / API reference](../BE-0065-docstring-standard-api-reference/BE-0065-docstring-standard-api-reference.md) — defines the `DOCSTRING_PATHS` migration this item's batches must keep pointing at real paths.
- [`docs/ai-development.md`](../../docs/ai-development.md#roadmap-items-be-ids-strict) — the roadmap-item format this proposal follows.

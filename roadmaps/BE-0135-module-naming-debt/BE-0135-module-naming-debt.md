**English** · [日本語](BE-0135-module-naming-debt-ja.md)

# BE-0135 — Resolve top-level module naming debt

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0135](BE-0135-module-naming-debt.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0135") |
| Implementing PR | [#640](https://github.com/bajutsu-e2e/bajutsu/pull/640) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md) |
<!-- /BE-METADATA -->

## Introduction

Six top-level modules in `bajutsu/` carry names that overlap with each other or promise a different job
than the module does. None of this is a functional bug, because every module does what its docstring
says. Still, a new contributor who guesses a module's contents from its name will guess wrong more often
than not. This proposal renames the modules to intent-revealing names.

## Motivation

Four modules all claim some piece of "environment" or "config", with non-intersecting meanings:

- `bajutsu/env.py` (338 lines) is a `simctl` command-builder wrapper: "Command builders are pure and
  unit-tested. Execution goes through an injectable runner" (`env.py:1-5`). It has nothing to do with
  "the environment" in any general sense. It is specifically the iOS Simulator control surface.
- `bajutsu/environment.py` (686 lines) is "Per-platform app lifecycle behind one Protocol" (BE-0009
  Phase 0). It holds the `Environment` Protocol that brings an app to a fresh, launched state per
  platform (`environment.py:1-11`). The `type: ignore` lifecycle escape hatches concentrate here, so the
  module is also a likely touchpoint for a planned follow-up item that brings backend lifecycle into the
  type system (TBD).
- `bajutsu/dotenv.py` (55 lines) is a "Minimal .env loader: read KEY=VALUE lines into the environment"
  (`dotenv.py:1`). It loads a `.env` file's secrets into `os.environ`.
- `bajutsu/config_source.py` (243 lines) is documented as "Acquire a config (and its scenario tree) from a Git
  source" (`config_source.py:1`, BE-0063). It fetches and materializes a config from `github:owner/repo`
  or `git+https://...` specs.

A contributor who asks "where does environment-variable handling live" or "where is the app launch
sequence" has four plausibly named files to check. No name rules out three of them. The sharpest case is
`env.py` versus `environment.py`. A two-letter difference in the filename maps to two unrelated concerns
(simctl commands versus cross-platform lifecycle). An editor can easily pick the wrong file through
autocomplete or fuzzy-find.

Two further modules promise a job that belongs to a different module:

- `bajutsu/capture.py` (180 lines) is not about screen or network capture, despite the name. It is
  `record`'s action capture: "proxy-actuation capture of tap / type / swipe" (`capture.py:1`). It
  resolves a stable selector from a hit-tested point and emits scenario steps. A reader would also reach
  for "capture" to describe screenshot capture, video capture, or network capture. All three exist
  elsewhere in the codebase under different names, so the name does not say which capture this module
  means.
- `bajutsu/provenance.py` (20 lines) is the smallest and narrowest of the six: "Display grouping for the
  `from:` provenance field" (`provenance.py:1`, BE-0044). It is purely how the timeline and report
  collapse consecutive identical `from:` values into one labeled group.

The word "provenance" is used far more broadly elsewhere in the codebase:

- the run manifest's `provenance.scenarioHash` (`audit.py:351,368,374,383,393`)
- the Git config source's `source_provenance()` (`config_source.py:127-128`)
- the idb version stamp (`idb_version.py:7,31,95`)

All three are "provenance" in the ordinary sense of the word, and this 20-line module has nothing to do
with any of them. A reader looking for any of those has equal reason to open `provenance.py` first, and
finds only the `from:` display-grouping helper.

This is onboarding-load debt (**small** severity, per the codebase-analysis report), not a functional
bug. Every module is internally correct and well documented at the top. The cost is paid repeatedly, in
small increments, by every contributor who opens a module to learn it is not the one they wanted.

## Detailed design

The renames are independent of each other (MECE by module). They can land as separate commits or one
combined PR. Each rename is a pure `git mv` plus an import-path update, with no behavior change:

1. **`bajutsu/env.py` → `bajutsu/simctl.py`.** The new name says exactly what the module wraps, the
   `simctl` CLI, and removes the two-letter collision with `environment.py`.
2. **`bajutsu/environment.py` → `bajutsu/lifecycle.py`** (or `platform_lifecycle.py` if `lifecycle.py`
   reads as too generic once the module list is seen together). The new name says the module's actual
   job: the per-platform `Environment` Protocol and its implementations. The choice must be coordinated
   with a planned follow-up item that brings backend lifecycle into the type system (TBD). If that item
   introduces a `Lifecycle` Protocol inside this module, the module name and the Protocol name collide,
   and `bajutsu.lifecycle.Lifecycle` reads oddly. Whichever of the two items lands first should pick a
   name that leaves room for the other. For example, the Protocol becomes `BackendLifecycle` if the
   module claims `lifecycle.py`. The module becomes `platform_lifecycle.py` if the Protocol claims
   `Lifecycle`.
3. **`bajutsu/dotenv.py`**: keep as is. "dotenv" is an established, specific term (the `.env` file
   convention) and does not collide with the other three. Renaming it would trade a precise,
   widely recognized name for a less standard one.
4. **`bajutsu/config_source.py`**: keep as is. "Config source" names the job precisely (acquiring a
   config from a source). It does not collide with `env.py` or `environment.py` once those are renamed.
   The only overlap was ambient ("config" appearing in the name), not a mismatch with the description.
5. **`bajutsu/capture.py` → `bajutsu/record_capture.py`** (or `action_capture.py`). The new name names
   the specific kind of capture the module does, recording actions during `record`. That separates it
   from the screenshot, network, and video capture elsewhere in the codebase.
6. **`bajutsu/provenance.py` → `bajutsu/from_grouping.py`** (or `provenance_display.py`). The new name
   says the module's actual narrow job, grouping consecutive `from:` values for display. The old name
   used the broad term "provenance", which several other, unrelated parts of the codebase also
   legitimately use.

Steps 3 and 4 conclude "no rename". They are included for completeness, because MECE requires covering
all six modules named in the motivation. They also document that the two modules were considered and
found not to need a rename, so a future reader does not re-raise the same question.

## Alternatives considered

- **Rename only the sharpest collision (`env.py`/`environment.py`) and leave the rest.** This fixes the
  single easiest mistake, picking the wrong file via fuzzy-find. It leaves `capture.py` and
  `provenance.py` promising the wrong job, which is the harder-to-notice half of the debt. A contributor
  searching for "provenance" has no signal that `provenance.py` is the wrong file until they open it.
- **Add a module-level docstring index (for example, a table in `docs/`) instead of renaming.** This is
  cheaper to ship, but it is a second source of truth that can drift from the code. It also does not
  help the contributor who greps or fuzzy-finds by filename instead of reading documentation first.
  According to the report, that is the actual failure mode.
- **Do nothing.** This is legitimately the lowest-risk option, since nothing is broken. But the report
  frames the cost as recurring (paid by every new contributor). Deferring indefinitely keeps paying that
  cost instead of paying the one-time rename cost.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Rename `bajutsu/env.py` → `bajutsu/simctl.py`.
- [x] Rename `bajutsu/environment.py` → `bajutsu/platform_lifecycle.py` — chose `platform_lifecycle.py` over the bare `lifecycle.py` to leave the `Lifecycle` Protocol name free for the sibling [BE-0141](../BE-0141-backend-lifecycle-protocol/BE-0141-backend-lifecycle-protocol.md) (still a proposal, so this item lands first).
- [x] Confirm `bajutsu/dotenv.py` and `bajutsu/config_source.py` need no rename (documented, no action).
- [x] Rename `bajutsu/capture.py` → `bajutsu/record_capture.py`.
- [x] Rename `bajutsu/provenance.py` → `bajutsu/from_grouping.py`.

Log:

- All four renames landed together as a pure `git mv` plus an import-path update, with no behavior change.
- The mirror test files were renamed to match: `test_env.py` → `test_simctl.py`, `test_capture.py` → `test_record_capture.py`, `test_provenance.py` → `test_from_grouping.py`, and `test_environment_seam.py` → `test_platform_lifecycle_seam.py`.
- The `simctl` module is aliased as `_simctl` in the three `serve` files whose injectable runner parameter is itself named `simctl`.
- The documented references in `docs/` (both languages), `DESIGN.md`, and the one live link in [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) were updated in step.

## References

- `bajutsu/simctl.py:1-5` (was `bajutsu/env.py`) — module docstring: the `simctl` command-builder wrapper.
- `bajutsu/platform_lifecycle.py:1-11` (was `bajutsu/environment.py`) — module docstring: the per-platform `Environment` Protocol.
- `bajutsu/dotenv.py:1-7` — module docstring: the `.env` loader.
- `bajutsu/config_source.py:1-11` — module docstring: Git config acquisition (BE-0063).
- `bajutsu/record_capture.py:1-5` (was `bajutsu/capture.py`) — module docstring: record-time action capture (BE-0012).
- `bajutsu/from_grouping.py:1-7` (was `bajutsu/provenance.py`) — module docstring: `from:` display grouping (BE-0044).
- `bajutsu/audit.py:351,368,374,383,393`, `bajutsu/config_source.py:127-128`,
  `bajutsu/idb_version.py:7,31,95` — other, unrelated uses of "provenance" elsewhere in the
  codebase that `provenance.py`'s name collides with.
- Related roadmap items: [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)
  (Git config source — names `config_source.py`), [BE-0044](../BE-0044-scenario-provenance/BE-0044-scenario-provenance.md)
  (scenario provenance — names `provenance.py`).
- Originates from the 2026-07-02 codebase-analysis report (design).

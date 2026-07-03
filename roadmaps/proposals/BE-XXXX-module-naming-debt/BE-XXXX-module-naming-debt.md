**English** · [日本語](BE-XXXX-module-naming-debt-ja.md)

# BE-XXXX — Resolve environment and config module naming debt

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-module-naming-debt.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Development infrastructure (contributor workflow) |
| Related | [BE-0063](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0044](../../implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance.md) |
<!-- /BE-METADATA -->

## Introduction

Six top-level modules in `bajutsu/` carry names that either overlap with each other or promise a
different job than the one the module actually does. None of this is a functional bug — every
module does what its docstring says — but a new contributor guessing a module's contents from its
name will guess wrong more often than not. This proposal renames the modules to intent-revealing
names.

## Motivation

Four modules all claim some piece of "environment" or "config", with non-intersecting meanings:

- `bajutsu/env.py` (338 lines) — a `simctl` command-builder wrapper: "Command builders are pure and
  unit-tested. Execution goes through an injectable runner" (`env.py:1-5`). Nothing about it is
  about "the environment" in any general sense; it is specifically the iOS Simulator control
  surface.
- `bajutsu/environment.py` (686 lines) — "Per-platform app lifecycle behind one Protocol" (BE-0009
  Phase 0): the `Environment` Protocol that brings an app to a fresh, launched state per platform
  (`environment.py:1-11`). This module is also a likely touchpoint for a planned follow-up item to
  bring backend lifecycle into the type system (TBD), since it is where the `type: ignore` lifecycle
  escape hatches concentrate.
- `bajutsu/dotenv.py` (55 lines) — "Minimal .env loader: read KEY=VALUE lines into the environment"
  (`dotenv.py:1`): loading a `.env` file's secrets into `os.environ`.
- `bajutsu/config_source.py` (243 lines) — "Acquire a config (and its scenario tree) from a Git
  source" (`config_source.py:1`, BE-0063): fetching and materializing a config from
  `github:owner/repo` or `git+https://...` specs.

A contributor who hears "where does environment-variable handling live" or "where is the app
launch sequence" has four plausibly-named files to check and no name-based way to rule three of
them out. `env.py` versus `environment.py` is the sharpest case: the two-letter difference in the
filename maps to two unrelated concerns (simctl commands vs. cross-platform lifecycle), which is an
easy source of an editor picking the wrong file via autocomplete or fuzzy-find.

Two further modules promise a job that belongs to a different module:

- `bajutsu/capture.py` (180 lines) — despite the name, this is not about screen/network capture; it
  is `record`'s action-capture: "proxy-actuation capture of tap / type / swipe" (`capture.py:1`),
  resolving a stable selector from a hit-tested point and emitting scenario steps. "Capture" is
  also the generic word a reader would reach for to describe screenshot capture, video capture, or
  network capture — all of which exist elsewhere in the codebase under different names — so the
  name doesn't disambiguate which capture this module means.
- `bajutsu/provenance.py` (20 lines) — the smallest and narrowest of the six: "Display grouping for
  the `from:` provenance field" (`provenance.py:1`, BE-0044) — purely how the timeline/report
  collapses consecutive identical `from:` values into one labeled group. But "provenance" is used
  far more broadly elsewhere in the codebase: the run manifest's `provenance.scenarioHash`
  (`audit.py:351,368,374,383,393`), the Git config source's `source_provenance()`
  (`config_source.py:127-128`), and the idb version stamp (`idb_version.py:7,31,95`) are all also
  "provenance" in the ordinary sense of the word, none of which this 20-line module has anything to
  do with. A reader looking for any of those has equal reason to open `provenance.py` first and
  find only the `from:` display-grouping helper.

This is onboarding-load debt (**small** severity, per the codebase-analysis report), not a
functional bug: every module is internally correct and well-documented at the top. The cost is
paid repeatedly, in small increments, by every contributor who has to open a module to learn it
isn't the one they wanted.

## Detailed design

The renames are independent of each other (MECE by module) and can land as separate commits or one
combined PR; each rename is a pure `git mv` + import-path update with no behavior change:

1. **`bajutsu/env.py` → `bajutsu/simctl.py`.** Names the module for exactly what it wraps — the
   `simctl` CLI — and removes the two-letter collision with `environment.py`.
2. **`bajutsu/environment.py` → `bajutsu/lifecycle.py`** (or `platform_lifecycle.py` if
   `lifecycle.py` reads as too generic once the module list is seen together). Names the module for
   its actual job, the per-platform `Environment` Protocol and its implementations. This name
   choice must be coordinated with a planned follow-up item to bring backend lifecycle into the type
   system (TBD): if that item introduces a `Lifecycle` Protocol inside this module, the module name
   and the Protocol name collide (`bajutsu.lifecycle.Lifecycle` reads oddly) — whichever of the two
   items lands first should pick a name that leaves room for the other (e.g. the Protocol becomes
   `BackendLifecycle` if the module claims `lifecycle.py`, or the module becomes
   `platform_lifecycle.py` if the Protocol claims `Lifecycle`).
3. **`bajutsu/dotenv.py`** — keep as is. "dotenv" is an established, specific term (the `.env` file
   convention) and does not collide with the other three; renaming it would trade a precise,
   widely-recognized name for a less standard one.
4. **`bajutsu/config_source.py`** — keep as is. "config source" precisely names its job (acquiring
   a config from a source) and doesn't collide with `env.py`/`environment.py` once those are
   renamed; the only overlap was ambient ("config" appearing in the name), not a description
   mismatch.
5. **`bajutsu/capture.py` → `bajutsu/record_capture.py`** (or `action_capture.py`). Disambiguates
   from screenshot/network/video capture elsewhere in the codebase by naming the specific kind of
   capture this module does — recording actions during `record`.
6. **`bajutsu/provenance.py` → `bajutsu/from_grouping.py`** (or `provenance_display.py`). Names the
   module for its actual narrow job — grouping consecutive `from:` values for display — rather than
   the broad term "provenance" that several other, unrelated parts of the codebase also legitimately
   use.

Steps 3 and 4 are included for completeness (MECE requires covering all six modules named in the
motivation) even though they conclude "no rename" — this documents that they were considered and
found not to need one, so a future reader doesn't re-raise the same question.

## Alternatives considered

- **Rename only the sharpest collision (`env.py`/`environment.py`) and leave the rest.** Fixes the
  single easiest mistake (picking the wrong file via fuzzy-find) but leaves `capture.py` and
  `provenance.py` promising the wrong job, which is the harder-to-notice half of the debt — a
  contributor searching for "provenance" has no signal that `provenance.py` is the wrong file until
  they open it.
- **Add a module-level docstring index (e.g. a table in `docs/`) instead of renaming.** Cheaper to
  ship, but it's a second source of truth that can drift from the code, and it doesn't help the
  contributor who greps or fuzzy-finds by filename rather than reading documentation first — which,
  per the report, is the actual failure mode being described.
- **Do nothing.** This is legitimately the lowest-risk option since nothing is broken, but the
  report explicitly frames the cost as recurring (paid by every new contributor), so deferring
  indefinitely keeps paying it rather than paying the one-time rename cost.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Rename `bajutsu/env.py` → `bajutsu/simctl.py`.
- [ ] Rename `bajutsu/environment.py` → `bajutsu/lifecycle.py` (name coordinated with the sibling `Lifecycle` Protocol item).
- [ ] Confirm `bajutsu/dotenv.py` and `bajutsu/config_source.py` need no rename (documented, no action).
- [ ] Rename `bajutsu/capture.py` → `bajutsu/record_capture.py`.
- [ ] Rename `bajutsu/provenance.py` → `bajutsu/from_grouping.py`.

No PR has landed yet.

## References

- `bajutsu/env.py:1-5` — module docstring: the `simctl` command-builder wrapper.
- `bajutsu/environment.py:1-11` — module docstring: the per-platform `Environment` Protocol.
- `bajutsu/dotenv.py:1-7` — module docstring: the `.env` loader.
- `bajutsu/config_source.py:1-11` — module docstring: Git config acquisition (BE-0063).
- `bajutsu/capture.py:1-5` — module docstring: record-time action capture (BE-0012).
- `bajutsu/provenance.py:1-7` — module docstring: `from:` display grouping (BE-0044).
- `bajutsu/audit.py:351,368,374,383,393`, `bajutsu/config_source.py:127-128`,
  `bajutsu/idb_version.py:7,31,95` — other, unrelated uses of "provenance" elsewhere in the
  codebase that `provenance.py`'s name collides with.
- Related roadmap items: [BE-0063](../../implemented/BE-0063-git-config-source/BE-0063-git-config-source.md)
  (Git config source — names `config_source.py`), [BE-0044](../../implemented/BE-0044-scenario-provenance/BE-0044-scenario-provenance.md)
  (scenario provenance — names `provenance.py`).
- Originates from the 2026-07-02 codebase-analysis report (design).

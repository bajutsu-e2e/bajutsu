**English** · [日本語](BE-0206-serve-state-module-split-ja.md)

# BE-0206 — Split serve job state from job execution

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0206](BE-0206-serve-state-module-split.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0206") |
| Implementing PR | [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/serve/jobs.py` (797 lines) holds two distinct concerns. The first is the **serve state
container** (`ServeState`, `Job`, `StoreBundle`, `CaptureSession`), which most of the serve package
reads. The second is the **job execution engine** (`run_job`, `cancel_job`, spawning, device boot,
app build), which only three modules touch. This item splits the state half into
`bajutsu/serve/state.py`. In the same cohesion pass, it extracts the CLI command builders from
`serve/helpers.py` into `bajutsu/serve/commands.py`. Both moves preserve behavior.

## Motivation

The module's own docstring admits the double role ("Job lifecycle: **state**, spawning,
cancellation, device boot, and app build"). The two halves relate in one direction: the execution
functions read `ServeState` fields and mutate `Job`, while nothing in the state half calls the
execution half. About 28 modules import from `serve/jobs.py` (`handler.py`, `authz.py`, every
`operations/*.py`, `server/app.py`, …). The overwhelming majority import only `ServeState`
(sometimes `Job`, `_scenarios_dir_for`, `_DEFAULT_ORG`). Just `serve/executor.py`,
`serve/server/worker_job.py`, and `serve/__init__.py` touch `run_job`/`cancel_job`.

Keeping state and execution in one module also forces two documented lazy-import workarounds:
`serve/executor.py:30` ("local import breaks the jobs↔executor cycle") and the lazy
`operations.config` import inside `ServeState._env_var_for_secret`. The cycle exists because
importing the execution engine drags the state along today. With the state in its own module,
`executor` can import the state at top level and keep only `run_job` lazy.

`serve/helpers.py` (675 lines) has the same problem at smaller scale. Its command builders
(`run_command`, `record_command`, `crawl_command`, `triage_command`, plus `_int`, ~240 lines)
reference nothing else in the file. Their only dependency is `serve/_cli_flags.flag_args`, and they
have exactly three consumers (`operations/dispatch.py`, `operations/triage.py`,
`serve/__init__.py`). The builders form a self-contained unit inside a module whose docstring
describes query and validation helpers, not command building.

## Detailed design

Both moves stay inside `bajutsu.serve`, so the import-boundary contracts (BE-0112) are
unaffected. The migration uses the re-export-facade pattern that BE-0127 shipped. The old module
keeps re-exports while importers migrate, and the shim is dropped afterward.

1. Create `bajutsu/serve/state.py` holding `Job`, `StoreBundle`, `CaptureSession`, `ServeState`,
   `_scenarios_dir_for`, and `_DEFAULT_ORG`. This is a pure move with no signature changes.
2. `serve/jobs.py` keeps the execution functions (`run_job`, `cancel_job`, `send_response`,
   `_spawn_env`, `_boot_devices`, `_build_app`, `_persist_run`, …) and imports `state`
   in one direction, so the dependency cannot cycle. It re-exports the moved names during the migration.
3. Migrate the ~28 importers to `serve.state` and drop the re-export shim.
4. Lift the lazy imports the split obsoletes: `serve/executor.py` imports the state module at
   top level; revisit `_env_var_for_secret`'s lazy `operations.config` import.
5. Extract `run_command`, `record_command`, `crawl_command`, `triage_command`, and `_int` from
   `serve/helpers.py` into `bajutsu/serve/commands.py`; update the three importers.
6. Update `docs/architecture.md` (and `docs/ja/architecture.md`) where the serve module list
   names the touched modules.

## Alternatives considered

- **Leave as is.** The fan-in keeps every state-only consumer nominally coupled to the process
  and Simulator machinery, and the two lazy-import workarounds stay load-bearing. The file keeps
  growing on both axes, because hosted-mode state fields and execution features land
  independently.
- **A broader serve re-layering.** BE-0127 already split the transport-shared operation bodies;
  re-opening the whole package layout for this one seam is churn beyond what the evidence
  supports.
- **Move `ServeState` into `serve/__init__.py`.** The composition root (`_build_server_state`)
  already lives there. Adding the class itself would inflate the package façade rather than give
  the state module a name.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `serve/state.py` created with the state container (pure move)
- [x] `serve/jobs.py` reduced to the execution engine
- [x] Importers migrated (production + tests); no re-export shim left behind
- [x] Lazy-import workarounds lifted where the split obsoletes them
- [x] `serve/commands.py` extracted from `serve/helpers.py`; importers updated
- [x] Module-list docs updated — `docs/architecture.md` (en/ja) names no touched serve submodule,
  so the `serve/__init__.py` docstring's submodule list was the one to update

Log:

- [#825](https://github.com/bajutsu-e2e/bajutsu/pull/825) — Split the serve state container into `serve/state.py` and the CLI command
  builders into `serve/commands.py`, both pure moves; `serve/jobs.py` keeps the run/cancel/build
  execution engine and imports `state` in one direction. `executor.py`'s type imports repoint to
  `state` (the lazy `run_job` import stays, as does `ServeState._env_var_for_secret`'s lazy
  `operations.config` import — both would still cycle). All ~28 production importers and the serve
  tests migrated with no shim left behind. `make check` green (3214 passed).

## References

- [`bajutsu/serve/jobs.py`](../../bajutsu/serve/jobs.py) · [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)
- [BE-0127](../BE-0127-split-serve-operations-module/BE-0127-split-serve-operations-module.md) — the serve operations split whose facade pattern this reuses
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md) — the import-boundary contracts the moves must respect

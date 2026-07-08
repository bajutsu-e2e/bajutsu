**English** · [日本語](BE-0206-serve-state-module-split-ja.md)

# BE-0206 — Split serve job state from job execution

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0206](BE-0206-serve-state-module-split.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0206") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/serve/jobs.py` (797 lines) holds two genuinely distinct concerns: the **serve state
container** (`ServeState`, `Job`, `StoreBundle`, `CaptureSession`) that most of the serve package
reads, and the **job execution engine** (`run_job`, `cancel_job`, spawning, device boot, app
build) that only three modules touch. This item splits the state half into `bajutsu/serve/state.py`
and, in the same cohesion pass, extracts the CLI command builders out of `serve/helpers.py`
into `bajutsu/serve/commands.py`. Both moves are behavior-preserving.

## Motivation

The module's own docstring admits the double role ("Job lifecycle: **state**, spawning,
cancellation, device boot, and app build"). The two halves have a one-directional relationship:
the execution functions read `ServeState` fields and mutate `Job`, while nothing in the state half
calls the execution half. Of the ~28 modules importing from `serve/jobs.py` (`handler.py`,
`authz.py`, every `operations/*.py`, `server/app.py`, …), the overwhelming majority import only
`ServeState` (sometimes `Job`, `_scenarios_dir_for`, `_DEFAULT_ORG`); only `serve/executor.py`,
`serve/server/worker_job.py`, and `serve/__init__.py` touch `run_job`/`cancel_job`.

Keeping state and execution in one module also forces two documented lazy-import workarounds:
`serve/executor.py:30` ("local import breaks the jobs↔executor cycle") and the lazy
`operations.config` import inside `ServeState._env_var_for_secret`. The cycle exists because
importing the execution engine drags the state along today; with the state in its own module,
`executor` can import it at top level and keep only `run_job` lazy.

`serve/helpers.py` (675 lines) has the same shape of problem at smaller scale: its command
builders (`run_command`, `record_command`, `crawl_command`, `triage_command`, plus `_int`,
~240 lines) reference nothing else in the file — their only dependency is
`serve/_cli_flags.flag_args` — and have exactly three consumers (`operations/dispatch.py`,
`operations/triage.py`, `serve/__init__.py`). They are a self-contained unit living in a
module whose docstring describes query and validation helpers, not command building.

## Detailed design

Both moves stay inside `bajutsu.serve`, so the import-boundary contracts (BE-0112) are
unaffected. The migration uses the re-export-facade pattern BE-0127 shipped: the old module
keeps re-exports while importers migrate, then the shim is dropped.

1. Create `bajutsu/serve/state.py` holding `Job`, `StoreBundle`, `CaptureSession`, `ServeState`,
   `_scenarios_dir_for`, and `_DEFAULT_ORG` — a pure move, no signature changes.
2. `serve/jobs.py` keeps the execution functions (`run_job`, `cancel_job`, `send_response`,
   `_spawn_env`, `_boot_devices`, `_build_app`, `_persist_run`, …) and imports `state`
   (single direction, no cycle). It re-exports the moved names during the migration.
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
  growing on both axes, since hosted-mode state fields and execution features land independently.
- **A broader serve re-layering.** BE-0127 already split the transport-shared operation bodies;
  re-opening the whole package layout for this one seam is churn beyond the evidence.
- **Move `ServeState` into `serve/__init__.py`.** The composition root (`_build_server_state`)
  already lives there; adding the class itself would inflate the package façade rather than name
  the state module.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `serve/state.py` created with the state container (pure move)
- [ ] `serve/jobs.py` reduced to the execution engine (+ temporary re-exports)
- [ ] Importers migrated; re-export shim dropped
- [ ] Lazy-import workarounds lifted where the split obsoletes them
- [ ] `serve/commands.py` extracted from `serve/helpers.py`; importers updated
- [ ] `docs/architecture.md` (en/ja) updated

## References

- [`bajutsu/serve/jobs.py`](../../bajutsu/serve/jobs.py) · [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)
- [BE-0127](../BE-0127-split-serve-operations-module/BE-0127-split-serve-operations-module.md) — the serve operations split whose facade pattern this reuses
- [BE-0112](../BE-0112-layer-boundary-enforcement/BE-0112-layer-boundary-enforcement.md) — the import-boundary contracts the moves must respect

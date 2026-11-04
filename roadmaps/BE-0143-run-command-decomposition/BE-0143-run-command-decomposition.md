**English** · [日本語](BE-0143-run-command-decomposition-ja.md)

# BE-0143 — Decompose the run command god-function

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0143](BE-0143-run-command-decomposition.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0143") |
| Implementing PR | [#624](https://github.com/bajutsu-e2e/bajutsu/pull/624) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`run`, the command that decides pass/fail for every scenario, is implemented as one 418-line
function. This item extracts its option declarations and orchestration steps into cohesive helpers
behind the same CLI surface. The most load-bearing command of the deterministic core then becomes
readable and independently testable, and its behavior does not change.

## Motivation

`bajutsu/cli/commands/run.py:186` opens a single function, `run`. The function does not return until
`bajutsu/cli/commands/run.py:603`, and `register` starts at line 604. That makes 418 lines in one
function body, split into two parts:

- **About 118 lines (186–303)** are `typer.Option` declarations for the command's large flag
  surface.
- **The remaining ~300 lines (304–601)** are the orchestration. The function runs these steps in
  order:
  1. Resolve the effective config, and build the app if needed.
  2. Resolve `--headed`, `--browser`, and `--browsers`.
  3. Load scenarios, and filter them by `--tag` and `--exclude`.
  4. Select the actuator, and validate the `--browsers` matrix against it.
  5. Resolve device lanes.
  6. Construct the alert-guard factory.
  7. Resolve the baselines, schemas, and goldens directories.
  8. Emit the start webhook.
  9. Bring up the launch server.
  10. Dispatch either the single-engine path or the cross-browser-matrix path.
  11. Emit the verdict, the end-of-run webhook, the optional `--zip` artifact, and the AI usage
      summary.

A function of this size has three problems:

- Readers struggle to follow it start to finish.
- Tests cannot target anything finer than "run the whole command".
- Changes carry risk. A single edit anywhere in the body can touch state built up over the
  preceding 300 lines.

This is a size-M effort. The seams between the stages already show in the code's own comments, for
example "Validate the backend before touching the Simulator CLIs" and "Mocks ride the network
channel". The extraction mostly names and moves code. It does not redesign it.

## Detailed design

The refactor preserves behavior. The CLI surface of `run` does not change: flags, defaults, exit
codes, and stdout and stderr output stay as they are. The coverage added by the cli-command-coverage
item should land first, so that this decomposition has a regression net to verify against. The work
breaks down into two independent axes:

- **Option-declaration grouping.** The ~118 lines of `typer.Option(...)` declarations
  (`bajutsu/cli/commands/run.py:186`–`303`) fall into clusters that their ordering already shows:
  - target and scenario selection
  - backend and device selection
  - browser and engine selection
  - baseline, schema, and golden directory overrides
  - reporting and output flags (`--progress`, `--zip`, `--runs-dir`)

  The declarations do not need to become separate functions, because `typer.Option` calls must stay
  in the signature. The signature itself can be organized with clearer section comments. It can
  also be grouped via a small number of `dataclass`-based option bundles that `run` re-expands.
  Either approach must keep `typer`'s introspection working without duplicating the flag surface.
  The sibling serve-cli-flag-mirror-drift item covers the mirror-image problem: `serve` currently
  duplicates this same surface by hand.
- **Orchestration-step extraction.** The ~300-line body decomposes into helpers alongside the file's
  existing `_resolve_*`, `_scenario_files`, and `_expand_file` helpers. Each helper takes and
  returns plain data, so it stays unit-testable without a Simulator:
  - config and build resolution (the `_load_effective_with_source` call through `build_if_missing`)
  - browser and engine resolution (`--headed`, `_resolve_browser`, `_parse_browsers`), which is
    already partially extracted
  - scenario loading and `--tag` / `--exclude` filtering, including the `--erase` override
  - backend and actuator selection, plus `--browsers`-matrix validation on a non-web backend
  - device-lane resolution and the `--dismiss-alerts` override
  - alert-guard factory construction (the `_guard_for` closure and its credential-gap checks)
  - baselines, schemas, and goldens directory resolution (already extracted; wire it into the new
    steps)
  - the single-engine versus cross-browser-matrix dispatch (the `run_pass` / `device_pool` /
    `run_and_report` / `run_matrix_and_report` branch)
  - post-run reporting (verdict echo, end-of-run webhook, `--zip` packaging, AI usage summary)

  `run` itself becomes a thin sequence of calls to these helpers. The new fast tests from the
  cli-command-coverage item can test each helper independently.

## Alternatives considered

- **Leave `run` as one function and rely on comments to delineate its stages.** Rejected. The
  function already carries explanatory comments at each stage boundary (quoted above). The comments
  show that the seams are known but not yet expressed in code. Comments do not make the stages
  independently testable or safe to change in isolation.
- **Introduce a `Runner` class that holds the orchestration as methods on shared state.** Rejected
  for this pass. The class would still leave one large surface, now spread across methods with
  implicit shared state via `self`. The plain-data helpers that this item favors are easier to
  unit-test and to reason about in isolation. The class deserves reconsideration if the extracted
  helpers turn out to need more shared context than fits cleanly as function parameters.
- **Do the extraction and the coverage work in one PR.** Rejected. Refactoring without an existing
  test net would make it hard to tell whether the extraction preserved behavior. Sequencing the
  cli-command-coverage item first gives this refactor a safety net to verify against.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Group `run`'s ~118 lines of option declarations into clearer clusters (or option bundles)
      without changing the flag surface
- [x] Extract config/build resolution into a helper
- [x] Extract browser/engine resolution into a helper (building on the existing
      `_resolve_browser`/`_parse_browsers`)
- [x] Extract scenario loading + `--tag`/`--exclude` filtering + `--erase` override into a helper
- [x] Extract backend/actuator selection + `--browsers`-matrix validation into a helper
- [x] Extract device-lane resolution + `--dismiss-alerts` override into a helper
- [x] Extract the alert-guard factory construction into a helper
- [x] Extract the single-engine vs. cross-browser-matrix dispatch into a helper
- [x] Extract post-run reporting (verdict, webhook, `--zip`, usage summary) into a helper

- 2026-07-04: Decomposed `run` into plain-data helpers (`_resolve_config_and_engines`,
  `_resolve_secrets`, `_load_scenarios`, `_filter_scenarios`, `_select_actuator`,
  `_apply_dismiss_alerts`, `_alert_guard_factory`, `_apply_mocks`, `_resolve_evidence_dirs`), a
  frozen `_RunPlan` record, and `_dispatch`/`_finish` (with `_write_zip`/`_upload_evidence`); grouped
  the option signature with section comments (bundles were rejected because serve introspects `run`'s
  exact `typer` option metadata — BE-0134). Behavior-preserving; added unit tests for the new pure
  helpers alongside BE-0142's coverage.

## References

- `bajutsu/cli/commands/run.py:186`–`603` (the `run` function; `register` starts at `:604`)
- `bajutsu/cli/commands/run.py:49`, `:68`, `:87`, `:98`, `:108`, `:118`, `:148` (already-extracted
  helpers this item builds alongside)
- Sequenced after the cli-command-coverage item, which should land first to give this refactor a
  regression net
- Related: BE-0043 (conflict-resistant file flow / auto-registry)
- See also the serve-cli-flag-mirror-drift item, the mirror-image problem on the `serve` side of
  this same flag surface
- Originates from the 2026-07-02 codebase-analysis report (technical debt).

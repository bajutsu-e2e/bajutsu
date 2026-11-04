**English** · [日本語](BE-0142-cli-command-coverage-ja.md)

# BE-0142 — Cover the CLI command layer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0142](BE-0142-cli-command-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0142") |
| Implementing PR | [#618](https://github.com/bajutsu-e2e/bajutsu/pull/618) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

The CLI command layer is every user's entry point into Bajutsu. Yet its three commands carry some
of the lowest coverage in the codebase. This item adds fast, Simulator-free unit tests for
`doctor`, `record`, and `run`. Regressions in the layer that users actually touch are then caught
before they ship.

## Motivation

Coverage for the command layer trails the rest of the codebase:

- `bajutsu/cli/commands/doctor.py` sits at 42.7%.
- `bajutsu/cli/commands/record.py` sits at 56.4%.
- `bajutsu/cli/commands/run.py` sits at 66.1%.

All three modules hold logic beyond argument parsing:

- **`doctor.py`** resolves actuator readiness and walks the current screen. The helpers are
  `_claude_readiness`, `_current_screen`, and `check_scenarios`, at
  `bajutsu/cli/commands/doctor.py:135`, `:150`, and `:20`.
- **`record.py`** builds output paths, in `_record_out_path` at
  `bajutsu/cli/commands/record.py:31`.
- **`run.py`** resolves lanes, baselines, schemas, goldens, and scenario files, and parses the
  `--browsers` matrix. The helpers are `_resolve_lanes`, `_resolve_baselines_dir`,
  `_resolve_schemas_dir`, `_resolve_goldens_dir`, `_scenario_files`, and `_parse_browsers`, at
  `bajutsu/cli/commands/run.py:68`, `:87`, `:98`, `:108`, `:118`, and `:49`.

None of this needs a Simulator. The logic is path resolution, option parsing, and branching over
an `Effective` config. The existing fast (non-E2E) suite already targets that kind of logic
elsewhere.

Leaving the layer under-tested has a cost. Every user's invocation passes through this layer, and
it is also the layer least protected against regressions.

This is a size-M effort. It covers three modules. Each has several already-isolated helper
functions to target directly, plus the thin `typer` entry points around them.

## Detailed design

The work is additive. Production behavior does not change, so the tests become the safety net for
later work. The run-command decomposition is the main example, and it should follow once this item
lands. The work breaks down by module:

- **`doctor.py`**: unit-test three helpers against a fake actuator and driver:
  - `check_scenarios` (scenario directory validation)
  - `_claude_readiness` (the readiness string for each `Effective` shape)
  - `_current_screen` (the element-tree walk)

  Then test the branches of the `doctor` command body through `CliRunner`: missing config, missing
  scenarios directory, and unavailable actuator.
- **`record.py`**: unit-test `_record_out_path` across its naming and collision branches. Test the
  `record` command's option handling (target resolution and output path selection) with a fake
  driver, so no Simulator is required.
- **`run.py`**: unit-test the helpers already isolated from the command body, independently of the
  `run` function itself. These are `_parse_browsers`, `_resolve_lanes`, `_resolve_baselines_dir`,
  `_resolve_schemas_dir`, `_resolve_goldens_dir`, `_scenario_files`, and `_expand_file`. Then add
  `CliRunner`-level tests for the option-parsing surface (target, scenario, backend, tag, and
  exclude resolution). These tests use the `fake` backend, so the dispatch logic of the command
  body runs without a real actuator.
- **All three modules**: the new tests use the existing `fake` driver and backend fixtures that the
  suite already uses elsewhere. The additions stay consistent with the project's fast-test
  conventions and need no new test infrastructure.

## Alternatives considered

- **Leave coverage as is and rely on E2E (Simulator) tests to catch regressions.** Rejected. The
  on-device suite is a separate, heavier path (`make -C demos/features e2e`). It is not part of the
  fast gate and does not run on every change. Command-layer bugs would surface late, if at all,
  for contributors who cannot run the Simulator-backed suite (for example, on Linux).
  written just to hit a number. A follow-up item to ratchet the coverage floor (TBD) should be
  sequenced after this work, once real coverage gains are banked.
- **Rewrite the command layer to be thinner before testing it.** The rewrite would extract all
  logic out of `cli/commands/`. Rejected as the immediate step: reshaping the surface first,
  without a test net, risks silently changing behavior. Coverage lands first. Any later
  restructuring then has a regression net to lean on (see the run-command decomposition item for
  `run.py` specifically).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit-test `doctor.py`'s helpers (`check_scenarios`, `_claude_readiness`, `_current_screen`)
      and the `doctor` command's branches
- [x] Unit-test `record.py`'s `_record_out_path` and the `record` command's option handling
- [x] Unit-test `run.py`'s isolated helpers (`_parse_browsers`, `_resolve_lanes`,
      `_resolve_baselines_dir`, `_resolve_schemas_dir`, `_resolve_goldens_dir`,
      `_scenario_files`, `_expand_file`)
- [x] Add `CliRunner`-level tests for the `run` command's option-parsing and dispatch surface
      against the `fake` backend

- 2026-07-03: Added Simulator-free tests across the three command modules. `run.py`'s directory
  helpers (`_resolve_baselines_dir` / `_resolve_schemas_dir` / `_resolve_goldens_dir`) and
  `_expand_file`'s setup/component/data error branches get direct unit tests; `doctor.py`'s
  `check_scenarios` / `_claude_readiness` / `_current_screen` and the `record`/`run` command bodies
  get `CliRunner` tests driven by the `fake` backend (device/AI boundaries stubbed only). The
  `run` matrix-execution branch (`run_matrix_and_report`) stays out of scope — a non-web backend
  can't reach it and it is exercised at the pipeline layer (`tests/runner/`). Verified with
  `make check`. PR: [#618](https://github.com/bajutsu-e2e/bajutsu/pull/618).

## References

- `bajutsu/cli/commands/doctor.py:20`, `:135`, `:150` (helpers), 42.7% coverage
- `bajutsu/cli/commands/record.py:31` (`_record_out_path`), 56.4% coverage
- `bajutsu/cli/commands/run.py:49`, `:68`, `:87`, `:98`, `:108`, `:118` (helpers), 66.1% coverage
- Related: [BE-0067](../BE-0067-code-quality-gate-hardening/BE-0067-code-quality-gate-hardening.md) (code quality gate hardening), [BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) (E2E coverage map)
- Sequenced before a follow-up coverage-floor-ratchet item (TBD), which should follow once this work lands
- Originates from the 2026-07-02 codebase-analysis report (technical debt).

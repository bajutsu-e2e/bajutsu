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

The CLI command layer is every user's entry point into Bajutsu, yet its three commands carry
some of the lowest coverage in the codebase. This item adds fast, Simulator-free unit tests for
`doctor`, `record`, and `run` so regressions in the layer users actually touch are caught before
they ship.

## Motivation

Coverage for the command layer trails the rest of the codebase: `bajutsu/cli/commands/doctor.py`
sits at 42.7%, `bajutsu/cli/commands/record.py` at 56.4%, and `bajutsu/cli/commands/run.py` at
66.1%. All three modules hold logic beyond argument parsing — `doctor.py` resolves actuator
readiness and walks the current screen (`_claude_readiness`, `_current_screen`, `check_scenarios`
at `bajutsu/cli/commands/doctor.py:135`, `:150`, `:20`); `record.py` builds output paths
(`_record_out_path` at `bajutsu/cli/commands/record.py:31`); `run.py` resolves lanes, baselines,
schemas, goldens, and scenario files, and parses the `--browsers` matrix (`_resolve_lanes`,
`_resolve_baselines_dir`, `_resolve_schemas_dir`, `_resolve_goldens_dir`, `_scenario_files`,
`_parse_browsers` at `bajutsu/cli/commands/run.py:68`, `:87`, `:98`, `:108`, `:118`, `:49`). None
of this needs a Simulator: it is path resolution, option parsing, and branching over an
`Effective` config, so it is exactly the kind of logic the existing fast (non-E2E) suite already
targets elsewhere. Leaving it under-tested means the layer every user's invocation passes through
is also the layer least protected against regressions. This is a size-M effort: three modules,
each with several already-isolated helper functions to target directly plus the thin `typer`
entry points around them.

## Detailed design

The refactor is additive only — no production behavior changes, so the tests act as the safety
net for later work (in particular the run-command decomposition, which should follow once this
lands). Work breaks down by module:

- **`doctor.py`**: unit-test `check_scenarios` (scenario directory validation), `_claude_readiness`
  (readiness string for each `Effective` shape), and `_current_screen` (element-tree walk) against
  a fake actuator/driver, plus the `doctor` command body's branches (missing config, missing
  scenarios dir, actuator unavailable) via `CliRunner`.
- **`record.py`**: unit-test `_record_out_path` across its naming/collision branches, and the
  `record` command's option handling (target resolution, output path selection) with a fake driver
  so no Simulator is required.
- **`run.py`**: unit-test the helpers already isolated from the command body —
  `_parse_browsers`, `_resolve_lanes`, `_resolve_baselines_dir`, `_resolve_schemas_dir`,
  `_resolve_goldens_dir`, `_scenario_files`, `_expand_file` — independently of the `run` function
  itself, then add `CliRunner`-level tests for the option-parsing surface (target/scenario/backend/
  tag/exclude resolution) using the `fake` backend so the command body's dispatch logic is
  exercised without a real actuator.
- Each module's new tests use the existing `fake` driver/backend fixtures already used elsewhere
  in the suite, keeping the additions consistent with the project's fast-test conventions and
  requiring no new test infrastructure.

## Alternatives considered

- **Leave coverage as is and rely on E2E (Simulator) tests to catch regressions.** Rejected: the
  on-device suite is a separate, heavier path (`make -C demos/features e2e`) that is not part of
  the fast gate and does not run on every change; command-layer bugs would surface late, if at
  all, for contributors who cannot run the Simulator-backed suite (e.g. on Linux).
  written just to hit a number. A follow-up item to ratchet the coverage floor (TBD) should be
  sequenced after this work, once real coverage gains are banked.
- **Rewrite the command layer to be thinner (extract all logic out of `cli/commands/`) before
  testing it.** Rejected as the immediate step: reshaping the surface first without a test net
  risks silently changing behavior. Coverage lands first so any subsequent restructuring (see the
  run-command decomposition item for `run.py` specifically) has a regression net to lean on.

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

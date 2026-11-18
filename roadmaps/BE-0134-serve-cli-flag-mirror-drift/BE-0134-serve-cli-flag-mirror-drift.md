**English** · [日本語](BE-0134-serve-cli-flag-mirror-drift-ja.md)

# BE-0134 — Eliminate serve-to-CLI flag-mirror drift

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0134](BE-0134-serve-cli-flag-mirror-drift.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0134") |
| Implementing PR | [#621](https://github.com/bajutsu-e2e/bajutsu/pull/621) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/serve/helpers.py` builds the CLI argv for `serve`'s launch requests by hand. It re-lists each
`typer` flag that `run.py` and `record.py` already declare. This second, manual definition of the flag
surface drifts from the CLI whenever a flag is added or changed. This item derives the serve flag
surface from the single `typer` source of truth instead.

## Motivation

`run_command`, at `bajutsu/serve/helpers.py:246`, builds the `python -m bajutsu run ...` argv. It
appends `cmd += [...]` for each flag it knows about: `--backend`, `--udid`, `--workers`,
`--erase`/`--no-erase`, `--dismiss-alerts`/`--no-dismiss-alerts`, `--headed`/`--no-headed`,
`--baselines`, `--runs-dir`, `--upload-exec`. This hand-maintained list is a second definition of
`run`'s flag surface, and it has already drifted.

The `run` typer command in `bajutsu/cli/commands/run.py` also declares `--browser`, `--browsers`,
`--tag`, `--exclude`, `--schemas`, `--goldens`, `--network`, `--log-predicate`, `--log-subsystem`,
`--alert-instruction`, `--zip`, `--config-offline`, and `--require-pinned-config`. `run_command`
cannot pass any of them through. A `serve`-launched run cannot reach these options, whatever
the web UI's request body contains. `record_command` (`bajutsu/serve/helpers.py:308`) repeats the same
pattern for `record`'s flag surface.

Nothing ties these two argv builders to the `typer.Option` declarations they re-derive. Every future
flag added to `run` or `record` needs a contributor to remember a second edit in `helpers.py`. The step
is easy to miss: the CLI itself works without it, and the gap surfaces only when someone tries to drive
that flag from `serve`.

The effort is size M. The fix touches two functions, plus however `run` and `record` expose their
option metadata for introspection.

## Detailed design

The refactor preserves behavior for every flag that `run_command` and `record_command` already emit:
existing inputs produce the same argv. It newly closes the gap for flags that `serve` cannot pass
through today. The design has two parts:

- **A single source of truth for the flag surface.** `run_command` and `record_command` stop appending
  `cmd += [...]` per flag. They build their argv from the option metadata that `typer` already holds
  for the `run` and `record` commands. Two mechanisms are candidates:
  - Iterate the command's `click.Command.params` (a list of `click.Parameter`) through the `click`
    object underneath `typer`.
  - Have `run` and `record` accept a shared, declarative option bundle that both the `typer` command and
    `run_command`/`record_command` consume. The run-command-decomposition item's option-grouping work
    is a base for this mechanism, if it lands first.

  Whichever mechanism is chosen, adding a flag to the CLI command must make it available to `serve`
  with no second edit.
  - The same metadata lets `run_command` and `record_command` validate at import or test time, rather
    than silently, that every flag they can emit still exists on the CLI command. The check catches a
    *removed* CLI flag as well as a newly *added* one.
- **Backfill the currently-missing flags.** Once the mirroring mechanism exists, wire through the flags
  that `run_command` cannot pass today: `--browser`, `--browsers`, `--tag`, `--exclude`, `--schemas`,
  `--goldens`, `--network`, `--log-predicate`, `--log-subsystem`, `--alert-instruction`, `--zip`,
  `--config-offline`, `--require-pinned-config`. Each flag accepts the matching value from the `serve`
  request body. This is additive to `run_command`'s signature and to the request-body parsing that
  calls it. `bajutsu/serve/operations.py:659` (`_register_and_dispatch`) and `:687` (`start_run`) build
  the request body that `run_command` consumes.

## Alternatives considered

- **Keep the two argv builders as hand-maintained lists and add a test that fails when they drift from
  the CLI.** Rejected as the primary fix, though worth keeping as a belt-and-suspenders check. A test
  catches drift after the fact. It does not remove the need for a contributor to remember the second
  edit. Deriving the surface from one source removes the duplication itself, not merely the risk of
  forgetting to update it.
- **Have `serve` shell out to `run`/`record` with every option as free-form key-value pairs taken
  directly from the web UI's request body, skipping the CLI's own validation.** Rejected. Bypassing
  `typer`'s option parsing (choices, defaults, mutually exclusive flag pairs like
  `--erase`/`--no-erase`) would let `serve` send combinations that the CLI itself would reject. The
  validation would move into `serve`'s request handling and be duplicated there, not removed.
- **Route `serve`'s launch requests through the same Python functions that `run`/`record` call
  internally, instead of shelling out to `python -m bajutsu run`.** Considered as a longer-term
  direction, because it would remove the argv-building step entirely. Rejected for this item's scope.
  It changes `serve`'s process-isolation model (each launch currently runs as its own subprocess) by
  more than the flag mirror requires. The broader serve-scope-boundary discussion is the better place
  to evaluate it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Derive `run_command`'s argv from `run`'s `typer` option metadata instead of a hand-maintained
      list
- [x] Derive `record_command`'s argv from `record`'s `typer` option metadata instead of a
      hand-maintained list (and `crawl_command` too, for consistency — it shared the same pattern)
- [x] Add a check (test or import-time assertion) that a flag known to `run_command`/
      `record_command` still exists on the corresponding CLI command
- [x] Backfill the currently-missing `run` flags (`--browser`, `--browsers`, `--tag`, `--exclude`,
      `--schemas`, `--goldens`, `--network`, `--log-predicate`, `--log-subsystem`,
      `--alert-instruction`, `--zip`, `--config-offline`, `--require-pinned-config`) through
      `run_command` and the `serve` request body that feeds it

- The flag surface now derives from a single source of truth. `bajutsu/serve/_cli_flags.py` introspects
  each command's `typer`/`click` options, and `flag_args` renders every flag from them. As a result,
  `run_command`, `record_command`, and `crawl_command` in `bajutsu/serve/helpers.py` no longer
  hand-list spellings or on/off forms.
- `flag_args` raises on a name that is not an option on the command. A completeness test
  (`tests/serve/test_cli_flag_mirror.py`) requires every CLI flag to be classified. A renamed or
  removed flag, or a newly added one left unreachable, fails the gate.
- The `run` flags above are backfilled. `start_run` (`bajutsu/serve/operations/dispatch.py`) wires the
  client-safe ones from the request body. It deliberately leaves `--schemas` and `--goldens` (host
  directory paths), `--config-offline`, and `--require-pinned-config` config-driven, so a serve client
  cannot supply an arbitrary host path (BE-0051).

## References

- `bajutsu/serve/helpers.py:246` (`run_command`), `:308` (`record_command`)
- `bajutsu/cli/commands/run.py:186` (the `run` typer command whose flags `run_command` mirrors)
- `bajutsu/serve/operations.py:659` (`_register_and_dispatch`), `:687` (`start_run`) — the callers
  that build the request body `run_command` consumes
- Related: BE-0069 (executable contributor guardrails), BE-0043 (conflict-resistant file flow /
  auto-registry)
- See also the run-command-decomposition item's option-grouping work, which this item can build on
- Originates from the 2026-07-02 codebase-analysis report (technical debt).

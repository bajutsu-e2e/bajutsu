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

`bajutsu/serve/helpers.py` builds the CLI argv for `serve`'s launch requests by hand, re-listing
each `typer` flag `run.py` and `record.py` already declare. This second, manual definition of the
flag surface drifts from the CLI as flags are added or changed. This item derives the serve flag
surface from the single `typer` source of truth instead.

## Motivation

`run_command`, at `bajutsu/serve/helpers.py:246`, builds the `python -m bajutsu run ...` argv by
appending `cmd += [...]` for each flag it knows about: `--backend`, `--udid`, `--workers`,
`--erase`/`--no-erase`, `--dismiss-alerts`/`--no-dismiss-alerts`, `--headed`/`--no-headed`,
`--baselines`, `--runs-dir`, `--upload-exec`. This is a hand-maintained second definition of
`run`'s flag surface, and it has already drifted: `bajutsu/cli/commands/run.py`'s `run` typer
command additionally declares `--browser`, `--browsers`, `--tag`, `--exclude`, `--schemas`,
`--goldens`, `--network`, `--log-predicate`, `--log-subsystem`, `--alert-instruction`, `--zip`,
`--config-offline`, and `--require-pinned-config` — none of which `run_command` knows how to pass
through, so a `serve`-launched run cannot reach these options no matter what the web UI's request
body contains. `record_command` (`bajutsu/serve/helpers.py:308`) repeats the same pattern for
`record`'s flag surface. Because nothing ties these two argv builders to the `typer.Option`
declarations they're re-deriving, every future flag added to `run`/`record` requires a
contributor to remember to also update `helpers.py` — an easy step to miss, since the CLI itself
works fine without it and the gap only surfaces when someone tries to drive that flag from
`serve`. This is a size-M effort: the fix touches two functions plus however `run`/`record`
expose their option metadata for introspection.

## Detailed design

The refactor is behavior-preserving for every flag `run_command`/`record_command` already emit —
the same argv is produced for existing inputs — but newly closes the gap for flags `serve`
currently cannot pass through. The design has two parts:

- **A single source of truth for the flag surface.** Rather than a manual `cmd += [...]` per
  flag, `run_command` and `record_command` build their argv from the same option metadata `typer`
  already holds for the `run`/`record` commands (e.g. by iterating the command's `click.Command.params`
  (a list of `click.Parameter`) via `typer`'s underlying `click` object, or by having `run`/`record` accept a shared,
  declarative option-bundle — see the run-command-decomposition item's option-grouping work,
  which this item can build on if it lands first — that both the `typer` command and
  `run_command`/`record_command` consume). Whichever mechanism is chosen, adding a flag to the
  CLI command must make it available to `serve` with no second edit.
  - This also lets `run_command`/`record_command` validate at import/test time (rather than
    silently) that every flag they know how to emit still exists on the CLI command, catching a
    *removed* CLI flag as well as a newly *added* one.
- **Backfill the currently-missing flags.** Once the mirroring mechanism exists, wire through the
  flags identified above that `run_command` cannot currently pass (`--browser`, `--browsers`,
  `--tag`, `--exclude`, `--schemas`, `--goldens`, `--network`, `--log-predicate`,
  `--log-subsystem`, `--alert-instruction`, `--zip`, `--config-offline`,
  `--require-pinned-config`), each accepting the corresponding value from the `serve` request body
  it's paired with (this is additive to `run_command`'s signature and to whatever request-body
  parsing calls it — see `bajutsu/serve/operations.py:659` (`_register_and_dispatch`) and
  `:687` (`start_run`), which build the request body `run_command` consumes).

## Alternatives considered

- **Leave the two argv builders as hand-maintained lists and add a test that fails when they drift
  from the CLI.** Rejected as the primary fix, though worth keeping as a belt-and-suspenders
  check: a test catches drift after the fact, but does not remove the need for a contributor to
  remember the second edit in the first place. Deriving the surface from one source removes the
  duplication itself, not just the risk of forgetting to update it.
- **Have `serve` shell out to `run`/`record` supplying every option as free-form key-value pairs
  taken directly from the web UI's request body, skipping the CLI's own validation.** Rejected:
  bypassing `typer`'s option parsing (choices, defaults, mutually-exclusive flag pairs like
  `--erase`/`--no-erase`) would let `serve` send combinations the CLI itself would reject, moving
  validation into `serve`'s request handling and duplicating it there instead of removing it.
- **Route `serve`'s launch requests through the same Python functions `run`/`record` call
  internally, instead of shelling out to `python -m bajutsu run`.** Considered as a longer-term
  direction (it would remove the argv-building step entirely), but rejected for this item's scope:
  it is a larger change to `serve`'s process-isolation model (each launch currently runs as its
  own subprocess) than fixing the flag mirror requires, and is better evaluated as part of the
  broader serve-scope-boundary discussion.

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

- The flag surface now derives from a single source of truth: `bajutsu/serve/_cli_flags.py`
  introspects each command's `typer`/`click` options and `flag_args` renders every flag from them,
  so `run_command` / `record_command` / `crawl_command` in `bajutsu/serve/helpers.py` no longer
  hand-list spellings or on/off forms. `flag_args` raises on a name that isn't an option on the
  command, and a completeness test (`tests/serve/test_cli_flag_mirror.py`) requires every CLI flag
  to be classified — so a renamed/removed flag, or a newly-added one left unreachable, fails the
  gate. The `run` flags above are backfilled; `start_run` (`bajutsu/serve/operations/dispatch.py`)
  wires the client-safe ones from the request body, deliberately leaving `--schemas` / `--goldens`
  (host directory paths) and `--config-offline` / `--require-pinned-config` config-driven so a serve
  client can't supply an arbitrary host path (BE-0051).

## References

- `bajutsu/serve/helpers.py:246` (`run_command`), `:308` (`record_command`)
- `bajutsu/cli/commands/run.py:186` (the `run` typer command whose flags `run_command` mirrors)
- `bajutsu/serve/operations.py:659` (`_register_and_dispatch`), `:687` (`start_run`) — the callers
  that build the request body `run_command` consumes
- Related: BE-0069 (executable contributor guardrails), BE-0043 (conflict-resistant file flow /
  auto-registry)
- See also the run-command-decomposition item's option-grouping work, which this item can build on
- Originates from the 2026-07-02 codebase-analysis report (technical debt).

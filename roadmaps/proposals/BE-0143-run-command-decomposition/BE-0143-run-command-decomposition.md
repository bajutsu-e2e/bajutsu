**English** · [日本語](BE-0143-run-command-decomposition-ja.md)

# BE-0143 — Decompose the run command god-function

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0143](BE-0143-run-command-decomposition.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0143") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`run`, the command that decides pass/fail for every scenario, is implemented as one 418-line
function. This item extracts its option declarations and orchestration steps into cohesive
helpers behind the same CLI surface, so the deterministic core's most load-bearing command
becomes readable and independently testable without changing its behavior.

## Motivation

`bajutsu/cli/commands/run.py:186` opens a single function, `run`, that does not return until
`bajutsu/cli/commands/run.py:603` (`register` starts at line 604) — 418 lines in one function
body. Of those, roughly 118 lines (186–303) are `typer.Option` declarations for the command's
large flag surface, and the remaining ~300 lines (304–601) are the orchestration: resolving the
effective config and building the app if needed, resolving `--headed`/`--browser`/`--browsers`,
loading and filtering scenarios by `--tag`/`--exclude`, selecting the actuator and validating the
`--browsers` matrix against it, resolving device lanes, constructing the alert-guard factory,
resolving the baselines/schemas/goldens directories, emitting the start webhook, bringing up the
launch server, dispatching either the single-engine or cross-browser-matrix execution path, and
finally emitting the verdict, the end-of-run webhook, the optional `--zip` artifact, and the AI
usage summary. A function this size is hard to read start to finish, hard to test at anything
finer than "run the whole command," and risky to change — a single edit anywhere in the body can
touch state built up over the preceding 300 lines. This is a size-M effort: the seams between
these stages are already implicit in the code's own comments (e.g. "Validate the backend before
touching the Simulator CLIs", "Mocks ride the network channel"), so extraction is mostly a matter
of naming and moving, not redesigning.

## Detailed design

The refactor is behavior-preserving: `run`'s CLI surface (flags, defaults, exit codes, stdout/
stderr output) does not change, and the coverage added by the cli-command-coverage item should
land first so this decomposition has a regression net to verify against. The work breaks down
into two independent axes:

- **Option-declaration grouping.** The ~118 lines of `typer.Option(...)` declarations
  (`bajutsu/cli/commands/run.py:186`–`303`) group into related clusters already visible in their
  ordering — target/scenario selection, backend/device selection, browser/engine selection,
  baseline/schema/golden directory overrides, and reporting/output flags (`--progress`, `--zip`,
  `--runs-dir`). These do not need to become separate functions (`typer.Option` calls must stay
  in the signature), but the signature itself can be organized with clearer section comments, or
  grouped via a small number of `dataclass`-based option bundles that `run` re-expands, whichever
  keeps `typer`'s introspection working without duplicating the flag surface (see the sibling
  serve-cli-flag-mirror-drift item, which is the mirror-image problem: `serve` currently hand-
  duplicates this same surface).
- **Orchestration-step extraction.** The ~300-line body decomposes into helpers alongside the
  file's existing `_resolve_*`/`_scenario_files`/`_expand_file` helpers, each taking and
  returning plain data so they stay unit-testable without a Simulator:
  - config/build resolution (`_load_effective_with_source` call through `build_if_missing`)
  - browser/engine resolution (`--headed`, `_resolve_browser`, `_parse_browsers`) — already
    partially extracted
  - scenario loading and `--tag`/`--exclude` filtering, including the `--erase` override
  - backend/actuator selection and `--browsers`-matrix-on-non-web validation
  - device-lane resolution and the `--dismiss-alerts` override
  - alert-guard factory construction (the `_guard_for` closure and its credential-gap checks)
  - baselines/schemas/goldens directory resolution (already extracted; wire into the new steps)
  - the single-engine vs. cross-browser-matrix dispatch (the `run_pass`/`device_pool`/
    `run_and_report`/`run_matrix_and_report` branch)
  - post-run reporting (verdict echo, end-of-run webhook, `--zip` packaging, AI usage summary)

  `run` itself becomes a thin sequence of calls to these helpers, each independently testable by
  the cli-command-coverage item's new fast tests.

## Alternatives considered

- **Leave `run` as one function and rely on comments to delineate its stages.** Rejected: the
  function already carries explanatory comments at each stage boundary (as quoted above), which
  shows the seams are known but not yet expressed in code — comments do not make the stages
  independently testable or safe to change in isolation.
- **Introduce a `Runner` class that holds the orchestration as methods on shared state.** Rejected
  for this pass: it would still leave one large surface (now spread across methods with implicit
  shared state via `self`) rather than the plain-data helpers this item favors, which are easier
  to unit-test and reason about in isolation. Worth reconsidering only if the extracted helpers
  turn out to need more shared context than fits cleanly as function parameters.
- **Do the extraction and the coverage work in one PR.** Rejected: refactoring without an existing
  test net first would make it hard to tell whether the extraction preserved behavior. Sequencing
  the cli-command-coverage item first gives this refactor a safety net to verify against.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Group `run`'s ~118 lines of option declarations into clearer clusters (or option bundles)
      without changing the flag surface
- [ ] Extract config/build resolution into a helper
- [ ] Extract browser/engine resolution into a helper (building on the existing
      `_resolve_browser`/`_parse_browsers`)
- [ ] Extract scenario loading + `--tag`/`--exclude` filtering + `--erase` override into a helper
- [ ] Extract backend/actuator selection + `--browsers`-matrix validation into a helper
- [ ] Extract device-lane resolution + `--dismiss-alerts` override into a helper
- [ ] Extract the alert-guard factory construction into a helper
- [ ] Extract the single-engine vs. cross-browser-matrix dispatch into a helper
- [ ] Extract post-run reporting (verdict, webhook, `--zip`, usage summary) into a helper

No PR has landed yet.

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

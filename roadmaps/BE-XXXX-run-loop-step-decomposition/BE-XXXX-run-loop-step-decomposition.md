**English** · [日本語](BE-XXXX-run-loop-step-decomposition-ja.md)

# BE-XXXX — Decompose the run-path step loop and per-scenario runner

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-run-loop-step-decomposition.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
| Related | [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md), [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md) |
<!-- /BE-METADATA -->

## Introduction

Two of the hottest functions on the deterministic `run` path — the step loop
(`bajutsu/orchestrator/loop.py:_run_steps`) and the per-scenario runner
(`bajutsu/runner/pipeline.py:run_one`) — carry their state as closures over mutable lists and
outer-scope variables rather than as explicit parameters. This item is a behavior-preserving
refactor that lifts that hidden state into named helpers with plain-data signatures, so the code
that decides pass/fail for every step becomes readable start to finish and unit-testable without a
Simulator. It is the run-path sibling of the decomposition [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md)
did for the `run` CLI command, targeting the two functions that item did not touch.

## Motivation

Two functions concentrate the run loop's control flow and are hard to read, test, and change:

- **`bajutsu/orchestrator/loop.py:_run_steps` — a 150-line function (304–454) that drives every
  step.** It threads its per-step state through mutable single-element lists captured by a nested
  recursive closure: `step_counter = [0]` (the shared step index, incremented inside `exec_steps`)
  and `_active_driver: list[base.Driver] = [driver]` (the currently-active driver, swapped in and
  out when a web step hands control to a second driver and then restores it). The recursion
  (`exec_steps` calls itself for `for_each` / nested step groups) reads `_active_driver[0]` at entry
  and mutates it mid-body, so a step group's behavior depends on state built up by preceding
  iterations. Reasoning about — or safely editing — any one branch requires holding the whole
  closure in your head, and the `[0]`-boxed variables exist only to smuggle mutation past Python's
  closure rules. This is exactly the shape that makes a change risky on the path where correctness
  matters most.

- **`bajutsu/runner/pipeline.py:run_one` — the per-scenario runner (≈140–229), nested inside
  `run_all` (74–229).** It closes over a broad swath of the outer scope — `eff`, `lease`, `mailbox`,
  `redactor`, `caps`, `run_dir`, `bindings`, `clock`, `progress`, `total`, the baselines/schemas
  directories, `golden_context`, and the alert-guard handlers, among others.
  Because it is a closure, it cannot be unit-tested without reconstructing all of `run_all`'s setup,
  and `run_all` runs it two ways — sequentially and through a `ThreadPoolExecutor`
  (`pool.map(lambda pair: run_one(*pair), …)`), so the implicit captured state is also shared across
  worker threads. The threading is correct today, but the closure obscures exactly which state each
  worker touches.

Neither function can change behavior here — they sit on the deterministic Tier-2 gate — so the win
is purely structural: make the load-bearing code legible and testable. This is a size-M effort
whose seams already exist: `_run_steps`'s web-step driver swap and step-index bookkeeping are
self-contained, and `run_one`'s captured variables are a ready-made parameter list. The
cli-command-coverage precedent ([BE-0142](../BE-0142-cli-command-coverage/BE-0142-cli-command-coverage.md))
and the crawl-coordinator extraction ([BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md))
show the pattern: land the regression net first, then move-and-name without redesigning.

## Detailed design

The refactor is **behavior-preserving**: no change to step semantics, driver lifecycle, evidence
capture, verdicts, threading, or any observable output. Prime directive 1 holds — no LLM is added
to the `run`/CI path; this only reshapes deterministic code. The regression net is the existing
`tests/` suite plus any gaps closed first (below). The work is MECE across four independent units:

- **Regression-net check (do first).** Confirm `_run_steps` and `run_one` are covered by fast,
  Simulator-free tests at the branch level (step index progression, `for_each`/nested groups, the
  web-step driver swap-and-restore, sequential vs. `ThreadPoolExecutor` execution). Add the missing
  branch tests **before** any extraction, so the move is verified against a green baseline.

- **Lift `_run_steps`'s step-index state out of the `[0]`-box.** Replace the `step_counter = [0]`
  closure smuggle with an explicit counter carried as a parameter/return (or a small cohesive
  helper object), so `exec_steps` no longer reads and mutates a boxed list. Behavior identical; the
  index just becomes visible in the signature.

- **Extract the web-step driver swap from `_run_steps`.** Move the `_active_driver` swap-and-restore
  (a web step handing control to `web_driver` and restoring `prev_driver` afterward) into a named
  helper — e.g. a small context manager or an `_exec_web_step(step, driver, …) -> (driver, result)`
  — that takes and returns the active driver explicitly instead of mutating `_active_driver[0]`. This
  removes the second `[0]`-boxed variable and isolates the one branch with non-trivial lifecycle.

- **Promote `run_one` to a top-level function with an explicit signature.** Turn the closure into a
  standalone function (or a small callable dataclass/`_ScenarioRunner`) that takes its captured
  values as explicit parameters, and have both `run_all` call sites (the sequential comprehension and the
  `ThreadPoolExecutor.map`) pass them in. The threading model is unchanged; the shared state simply
  becomes an explicit parameter list, and `run_one` becomes unit-testable in isolation.

Each unit is independently landable in its own small PR, and each keeps the suite green on its own.

## Alternatives considered

- **Leave both functions as-is and rely on comments.** Rejected for the same reason
  [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md) rejected it
  for `run`: the functions already carry stage comments, yet the `[0]`-boxed closure state and the
  broad free-variable capture are precisely what comments cannot make safe to edit. The risk lives in the
  hidden mutable state, not in missing prose.

- **Fold this into BE-0143.** Rejected: BE-0143 is scoped to `cli/commands/run.py`'s `run`
  function and has shipped ([#624](https://github.com/bajutsu-e2e/bajutsu/pull/624)). These are
  different files and different functions; tracking them separately keeps each PR small and its
  regression net focused.

- **Redesign the step loop (e.g. an explicit state machine or an iterator protocol).** Rejected as
  out of scope: that is a behavior-affecting redesign on the deterministic gate, exactly what this
  item avoids. This item only makes the current design legible; a redesign, if ever wanted, is a
  separate proposal with its own risk budget.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Regression-net check: fast, Simulator-free branch tests for `_run_steps` and `run_one` land first.
- [ ] Lift `_run_steps`'s step-index state out of the `[0]`-box into an explicit counter.
- [ ] Extract the web-step driver swap into a named helper that passes the driver explicitly.
- [ ] Promote `run_one` to a top-level function with its captured values as explicit parameters.

## References

- [BE-0143 — Decompose the run command god-function](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md) (the sibling decomposition on the `run` CLI command)
- [BE-0092 — Extract the crawl coordinator](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md) (precedent: behavior-preserving extraction of a run-path coordinator)
- [BE-0142 — CLI command coverage](../BE-0142-cli-command-coverage/BE-0142-cli-command-coverage.md) (the "land the regression net first" precedent)
- `bajutsu/orchestrator/loop.py:304` (`_run_steps`), `bajutsu/runner/pipeline.py:140` (`run_one`)

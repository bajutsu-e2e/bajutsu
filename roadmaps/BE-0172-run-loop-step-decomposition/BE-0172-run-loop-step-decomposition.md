**English** · [日本語](BE-0172-run-loop-step-decomposition-ja.md)

# BE-0172 — Decompose the run-path step loop and per-scenario runner

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0172](BE-0172-run-loop-step-decomposition.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0172") |
| Implementing PR | [#693](https://github.com/bajutsu-e2e/bajutsu/pull/693), [#694](https://github.com/bajutsu-e2e/bajutsu/pull/694) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md), [BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md) |
<!-- /BE-METADATA -->

## Introduction

Two of the hottest functions on the deterministic `run` path carry their state as closures over mutable lists and outer-scope variables. They are the step loop (`bajutsu/orchestrator/loop.py:_run_steps`) and the per-scenario runner (`bajutsu/runner/pipeline.py:run_one`). Neither takes that state as explicit parameters.

This item is a behavior-preserving refactor. It lifts the hidden state into named helpers with plain-data signatures. The code that decides pass/fail for every step then reads start to finish, and a unit test can exercise it without a Simulator.

The item is the run-path sibling of the decomposition that [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md) did for the `run` CLI command. It targets the two functions that BE-0143 did not touch.

## Motivation

Two functions concentrate the run loop's control flow, and both are hard to read, test, and change:

- **`bajutsu/orchestrator/loop.py:_run_steps` — a 150-line function (304–454) that drives every step.** It threads its per-step state through mutable single-element lists, which a nested recursive closure captures. `step_counter = [0]` is the shared step index, incremented inside `exec_steps`. `_active_driver: list[base.Driver] = [driver]` is the currently active driver. A web step swaps it out when the step hands control to a second driver, then restores it. The recursion (`exec_steps` calls itself for `for_each` and nested step groups) reads `_active_driver[0]` at entry and mutates it mid-body. A step group's behavior therefore depends on state built up by preceding iterations. To reason about any one branch, or to edit it safely, a reader must hold the whole closure in mind. The `[0]`-boxed variables exist to smuggle mutation past Python's closure rules. This shape makes a change risky on the path where correctness matters most.

- **`bajutsu/runner/pipeline.py:run_one` — the per-scenario runner (≈140–229), nested inside `run_all` (74–229).** It closes over a broad swath of the outer scope: `eff`, `lease`, `mailbox`, `redactor`, `caps`, `run_dir`, `bindings`, `clock`, `progress`, `total`, the baselines and schemas directories, `golden_context`, and the alert-guard handlers, among others. Because `run_one` is a closure, a test cannot run it without reconstructing all of `run_all`'s setup. `run_all` also runs it two ways, sequentially and through a `ThreadPoolExecutor` (`pool.map(lambda pair: run_one(*pair), …)`), so the worker threads share the implicit captured state. The threading is correct today, but the closure obscures which state each worker touches.

Neither function can change behavior here, because both sit on the deterministic Tier-2 gate. The win is structural: the load-bearing code becomes legible and testable. This is a size-M effort whose seams already exist:

- `_run_steps`'s web-step driver swap and step-index bookkeeping are self-contained.
- `run_one`'s captured variables are a ready-made parameter list.

Two precedents show the pattern: the cli-command-coverage work ([BE-0142](../BE-0142-cli-command-coverage/BE-0142-cli-command-coverage.md)) and the crawl-coordinator extraction ([BE-0092](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md)). Both landed the regression net first, then moved and named code without redesigning it.

## Detailed design

The refactor is **behavior-preserving**. It changes no step semantics, driver lifecycle, evidence capture, verdict, threading, or observable output. Prime directive 1 holds: no LLM is added to the `run`/CI path, because the refactor only reshapes deterministic code. The regression net is the existing `tests/` suite, plus any gaps closed first (below). The work is MECE across four independent units:

- **Regression-net check (do first).** Confirm that fast, Simulator-free tests cover `_run_steps` and `run_one` at the branch level: step index progression, `for_each` and nested groups, the web-step driver swap-and-restore, and sequential versus `ThreadPoolExecutor` execution. Add the missing branch tests **before** any extraction, so the move is verified against a green baseline.

- **Lift `_run_steps`'s step-index state out of the `[0]`-box.** Replace the `step_counter = [0]` closure smuggle with an explicit counter, carried as a parameter and return value (or held in a small cohesive helper object). `exec_steps` then no longer reads and mutates a boxed list. Behavior is identical, and the index becomes visible in the signature.

- **Extract the web-step driver swap from `_run_steps`.** Move the `_active_driver` swap-and-restore into a named helper. In that swap, a web step hands control to `web_driver` and restores `prev_driver` afterward. The helper could be a small context manager or an `_exec_web_step(step, driver, …) -> (driver, result)` function. It takes and returns the active driver explicitly instead of mutating `_active_driver[0]`. This removes the second `[0]`-boxed variable and isolates the one branch with a non-trivial lifecycle.

- **Promote `run_one` to a top-level function with an explicit signature.** Turn the closure into a standalone function (or a small callable dataclass such as `_ScenarioRunner`) that takes its captured values as explicit parameters. Both `run_all` call sites, the sequential comprehension and the `ThreadPoolExecutor.map`, pass the values in. The threading model is unchanged. The shared state becomes an explicit parameter list, and `run_one` becomes unit-testable in isolation.

Each unit lands independently in its own small PR, and each keeps the suite green on its own.

## Alternatives considered

- **Leave both functions as-is and rely on comments.** Rejected, for the reason that [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md) gave for `run`. The functions already carry stage comments. Comments cannot make the `[0]`-boxed closure state and the broad free-variable capture safe to edit. The risk lives in the hidden mutable state, not in missing prose.

- **Fold this into BE-0143.** Rejected. BE-0143 is scoped to the `run` function in `cli/commands/run.py` and has shipped ([#624](https://github.com/bajutsu-e2e/bajutsu/pull/624)). The functions here live in different files. Tracking them separately keeps each PR small and its regression net focused.

- **Redesign the step loop (for example, an explicit state machine or an iterator protocol).** Rejected as out of scope. Such a redesign affects behavior on the deterministic gate, which is exactly what this item avoids. This item makes the current design legible and goes no further. A redesign, if ever wanted, is a separate proposal with its own risk budget.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Regression-net check: fast, Simulator-free branch tests for `_run_steps` and `run_one` land first.
- [x] Lift `_run_steps`'s step-index state out of the `[0]`-box into an explicit counter.
- [x] Extract the web-step driver swap into a named helper that passes the driver explicitly.
- [x] Promote `run_one` out of the closure into a top-level `_ScenarioRunner` (frozen dataclass, `run_one` as a method) with its captured values as explicit fields.

## References

- [BE-0143 — Decompose the run command god-function](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md) (the sibling decomposition on the `run` CLI command)
- [BE-0092 — Extract the crawl coordinator](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md) (precedent: behavior-preserving extraction of a run-path coordinator)
- [BE-0142 — CLI command coverage](../BE-0142-cli-command-coverage/BE-0142-cli-command-coverage.md) (the "land the regression net first" precedent)
- `bajutsu/orchestrator/loop.py:304` (`_run_steps`), `bajutsu/runner/pipeline.py:140` (`run_one`)

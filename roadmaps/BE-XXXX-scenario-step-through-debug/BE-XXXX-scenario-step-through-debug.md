**English** · [日本語](BE-XXXX-scenario-step-through-debug-ja.md)

# BE-XXXX — Step through a scenario at a prompt, with breakpoints and quit

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-scenario-step-through-debug.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

`bajutsu run` executes a scenario from its first step to its last. This item adds a debugging mode to
it: `--step` stops before each step at a `step>` prompt, `--break` stops at a chosen step, and
`--break-on-fail` stops on the step that fails. While the run is stopped, the operator reads the live app
with the commands of [`repl`](../../docs/cli.md#repl), then lets the run continue or ends it with `quit`.
The pause is deterministic bookkeeping over the run's own step indices and names, and the verdict still
comes only from the scenario's assertions.

## Motivation

A failing scenario tells an author which step failed and shows that step's evidence, and nothing
more. To see why the selector missed, the author must rerun the whole scenario while watching the
device, or open the report and compare screenshots. `bajutsu repl` reads the element tree and acts on an
id, but it starts from a fresh launch and knows nothing of the scenario, so reaching step 14 means
retyping steps 1 to 13 by hand. The `serve` Author view picks a selector from a live screenshot, yet it
never runs the scenario up to the step in question. No command today runs a scenario and stops partway.

The result of this item is observable in one session: an author runs
`bajutsu run --scenario checkout.yaml --break-on-fail`, the run stops on the failing step with its screen
still up, and `tree` at the prompt prints the element table that step's selector was matched against,
with no second launch. A run that uses only `next`, `continue`, and read commands is judged by the same
assertions as the same scenario without the flags.

## Detailed design

### Behavior

1. **Flags.** `run` gains three flags. `--step` stops before every step, starting with the first.
   `--break <name-or-index>` is repeatable. It matches a step's authored `name`, or the decimal index the
   run assigns across nested steps, which is the `N` in a failure reason such as `step 3 (tap): …`.
   `--break-on-fail` stops once on the failed step. Any of the three opens the prompt.
2. **Prompt.** The prompt `step> ` is written to stderr, so stdout stays the `PASS|FAIL` line. Before a
   step it prints `⏸ before step N [name]: <label>`. After a failure it prints `✘ step N failed: <reason>`.
3. **Commands.** `next` (`n`, or an empty line) runs the step and stops again before the next one.
   `continue` (`c`) runs on to the next breakpoint. `quit` (`q`, `exit`, or end of input) ends the run.
   Ctrl-C ends it too, because the terminal also delivers it to the run's recorder and browser.
   `help` lists them. Every other line goes to the `repl` command set, bound to the step's live driver.
4. **Quit.** `quit` raises `RunCancelled`, the exception cooperative cancellation already uses
   ([BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md)). The scenario is
   reported as `cancelled`, its evidence is finalized by the existing unwind, and the run exits 1.
   At a failure pause, `quit` returns instead, so the scenario keeps the reason it failed with.
5. **Verdict.** A command that acts on the app (`tap`, `type`, `scroll`, `back`, `step <yaml>`) makes the
   run hand-driven. The first such command is recorded in `RunResult.interactive` and in `result.json`.
   A scenario whose steps all passed then fails with `interactive: <command>`. A scenario that already
   failed keeps its own reason. Reads (`tree`, `find`, `screenshot`) and stepping leave the verdict alone.
   The command is recorded before it runs, because an actuation that raised may still have moved the app.
6. **Refusals.** Before any device is leased, `run` exits 2 when stdin or stderr is not a terminal, when
   the run has more than one scenario or more than one `--browsers` engine, or when `--workers` exceeds 1.
   A prompt that nobody can see would hang a CI job, and a suite would leave every scenario but one waiting.
7. **Typos.** After the run, a breakpoint that no step matched is named on stderr.

### Where it hooks in

| Piece | Location | Role |
|---|---|---|
| `StepGate`, `StepPause` | `bajutsu/common/orchestrator/types/step_gate.py` | The protocol the loop calls: `before_step`, `after_failure`, and `manual_action` |
| `_LoopConfig.step_gate` | `bajutsu/common/orchestrator/loop/_loop_config.py` | Carries the gate next to `cancelled` and `progress` |
| `_StepRunner.exec_steps` | `bajutsu/common/orchestrator/loop/_step_runner.py` | Calls the gate right after the cancellation check and routing, before `_run_one` |
| `StepLoopState.failure_paused` | `bajutsu/common/orchestrator/loop/step_loop_state.py` | Stops once per failure, not once per enclosing `if` / `forEach` / `web` level |
| `run_scenario` | `bajutsu/common/orchestrator/loop/_functions.py` | Stamps `interactive` and the failure on the result |
| `PromptStepGate` | `bajutsu/run/step_gate.py` | The prompt: breakpoints, commands, and a `ReplSession` per driver |
| `_build_step_gate` | `bajutsu/run/cli.py` | Parses the flags and applies the refusals above |

The gate sits at the step boundary because that is where cancellation is already checked
([BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md)): the step has not
acted, so nothing is half-actuated and the screen is settled. It fires for the scenario's own steps, never for a
`before` or `after` hook phase or for an `interrupts` rule's recovery steps. Recovery runs from
inside a step already under way, so a pause there would spend that step's timeout. Because the pause happens before `_run_one` starts its clock,
a step's recorded duration and every condition wait's timeout are unaffected by how long the operator
stays at the prompt. The scenario's video keeps recording through the pause. An app that reacts to elapsed time can
still behave differently after a long pause, which is why the gate drops the cached `before` tree
and screenshot whenever it held the loop.

The protocol lives in `common/orchestrator` and the prompt in `run`, because the import contract keeps the
deterministic core free of `bajutsu.run` and `bajutsu.repl`. The core calls a gate it knows only as a
protocol, which is how `cancelled` and `progress` already reach it.

### Not doing

- A `serve` UI for stepping. The pause protocol is shared, so a later item can add buttons; this item
  ships the terminal first.
- Retrying a step, rewinding, or editing the scenario at the prompt. A retry would repeat a step's side
  effects and break the counters and the report.
- Stepping through a suite, a `--browsers` matrix, or a worker pool.
- Any model call. The prompt and the stop rules read no model.

## Alternatives considered

| Option | What it is | Why not |
|---|---|---|
| Extend `repl` with a `load <scenario>` command | Run steps from inside the shell | `repl` launches the app itself and has none of `run`'s lease, evidence sink, retries, or alert guard, so it would grow a second runner |
| A `--from-step N` flag | Skip to step N and run on | Steps depend on earlier ones (login, extracted `vars.*`), so a skip lands on a screen the scenario never reaches |
| Stop only on failure | The smallest change | It cannot show the steps leading up to the failure, which is most of what debugging needs |
| Pause between every step only | No breakpoints | A 30-step scenario then needs 30 `next` presses to reach step 28 |
| Judge a hand-driven run normally | Smallest code | A step the operator redid by hand would leave a green result that the scenario cannot reproduce |
| Do not allow actuation at the prompt | Keep the run pure | Trying a fix, such as tapping the control the selector missed, is the main reason to stop; the operator would have to leave and rerun |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `StepGate`, `StepPause`, and `RunResult.interactive`, and hook the gate into the step loop.
- [x] Add the `PromptStepGate` prompt and the `--step` / `--break` / `--break-on-fail` flags with their refusals.
- [x] Record `interactive` in `result.json` and fail a hand-driven run.
- [x] Add tests, and document the flags in `docs/cli.md` and `docs/architecture.md` in both languages.

## References

- [`docs/cli.md`](../../docs/cli.md#stepping-through-a-scenario): the user-facing reference.
- [BE-0423](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md): the `repl` command set this prompt reuses.
- [BE-0370](../BE-0370-graceful-run-cancel/BE-0370-graceful-run-cancel.md): the cancellation path `quit` rides.

**English** · [日本語](BE-0451-explicit-sleep-step-ja.md)

# BE-0451 — An explicit `sleep` step for waits that no condition can observe

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0451](BE-0451-explicit-sleep-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0451") |
| Implementing PR | [#2120](https://github.com/bajutsu-e2e/bajutsu/pull/2120) |
| Topic | Scenario authoring features |
<!-- /BE-METADATA -->

## Introduction

This item adds a `sleep` step: `sleep: { seconds: 2, reason: "the server rate-limits retries for two seconds" }`. The step pauses the scenario for a fixed number of seconds and does nothing else. It is the one sanctioned exception to "condition waits only" ([prime directive 2](../../CLAUDE.md#prime-directives-do-not-violate)), and it is built to stay visible: the schema requires a `reason` and caps `seconds`, the report shows every fixed pause, and the determinism audit ([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)) lists each one as a finding.

The step is for a wait that no condition on the screen, the accessibility tree, or the network log can observe. A scenario author has no way to express such a wait today, so the author either invents a `wait` with an unrelated condition or leaves the scenario flaky.

## Motivation

Waiting in a scenario is a condition wait: the `wait` step polls `query()` until a selector appears, disappears, or the screen settles, and it requires a `timeout` ([`docs/scenarios.md`](../../docs/scenarios.md), [BE-0118](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md)). The run loop's wait never sleeps for a fixed duration. This is the right default, and the item keeps it.

Some waits have no observable condition, though. A server throttles a retry for a fixed interval and shows nothing until the interval ends. An animation outlives the point where the tree stops changing, so `until: settled` returns early. A scenario that records a demo video needs a screen to stay visible for a moment, and no element encodes "long enough to read". In each case the author works around the missing step. The workaround is either a `wait` on an unrelated element, which hides the real reason in a condition that happens to hold late enough, or a retry loop that burns device time. Both look deterministic and are not, because the delay the author needed was never written down.

The observable difference is threefold:

- A scenario that must pause states the duration and the reason in one line, and the loader rejects a `sleep` that has no reason or exceeds the cap.
- Every fixed pause appears in `report.html` with its reason, and `bajutsu audit` reports it as a `fixed-sleep` finding, so a reviewer can count the fixed waits of a suite and challenge each.
- The suite's `wait` steps keep meaning "a condition, with a timeout", because the unobservable waits no longer borrow that step.

## Detailed design

### The step

`sleep` is a new action beside `wait`, with a model in `bajutsu/common/scenario/models/actions/sleep.py` and a field on `Step` ([`step.py`](../../bajutsu/common/scenario/models/steps/step.py)), so the one-action rule and `STEP_ACTIONS` pick it up without a second registration.

| Field | Type | Meaning |
|---|---|---|
| `seconds` | number, required | The pause length. Greater than zero and at most 30. Strict, so a boolean or a quoted string is refused rather than coerced. |
| `reason` | string, required | Why no condition can express this wait. Not empty after trimming whitespace. |

The cap of 30 seconds is a constant, `MAX_SLEEP_SECONDS`, in the model module. A longer fixed pause is almost certainly a missing condition, so the loader rejects it with a message that points at `wait`. The model takes no `timeout` and no selector, since a sleep has no condition.

### Execution

The run loop executes `sleep` beside `wait`, not through a driver handler, because the step touches no device. The loop calls the injected `Clock.sleep` ([`clock.py`](../../bajutsu/common/orchestrator/types/clock.py)) in slices of a quarter second, so a test with a fake clock advances time without waiting, and a cancelled run leaves the pause within one slice. The step succeeds once the pause ends. A `record` or `enrich` replay honors the pause too, since the delay it stands in for is just as real there. It issues no `query()`, sends no input, and does not apply `BAJUTSU_MIN_WAIT_TIMEOUT`, because that floor raises condition-wait ceilings and a fixed pause has no ceiling. It is available on every backend, including the fake driver, and needs no capability token.

The step skips the interrupt check that precedes an acting step, as `wait` does, since a pause acts on nothing. The end-of-step system-alert guard fires on a failed step alone, and a pause never fails short of cancellation. A prompt that appears during the pause is handled by the next step that queries the tree.

### Keeping the exception visible

Three surfaces make the exception countable.

- **Report.** The step's progress label and its row in `report.html` read `sleep 2s — <reason>`. The row is built from the step's own definition, which already carries `seconds`, so `StepOutcome` gains no field and a tool can still sum the fixed time of a run.
- **Determinism audit.** [`audit/_functions.py`](../../bajutsu/analysis/audit/_functions.py) gains a `fixed-sleep` finding next to `loose-wait`, one per `sleep` step, carrying the reason. The audit finds a `sleep` by walking the whole scenario model, so a pause in `before`, `after`, `interrupts`, or a `web` or `app` block is listed too. A scenario with fixed pauses totalling more than ten seconds lands in the audit's moderate tier, the same tier a loose wait reaches.
- **Authoring by AI.** `record` and `crawl` never offer `sleep` to the model. The author of a scenario may add one by hand, and the model may not choose a fixed pause as a way around a flaky step. The recording agent's tool list is written by hand rather than derived from `STEP_ACTIONS`, so it lacks `sleep` already; a test pins that absence. A `triage --ai` fix proposal is a third path by which a model could add a pause, so the laxer-change guard warns on any fix that adds a `sleep`. Authoring through the MCP server is outside these guards: a client there can submit a `sleep` like any other step, and the audit finding is what surfaces it.

### Codegen

The three emitters translate the pause, so a generated test matches the scenario.

| Emitter | Output |
|---|---|
| `xcuitest` | `Thread.sleep(forTimeInterval: <seconds>)` with the reason as a comment |
| `uiautomator` | `Thread.sleep(<milliseconds>)` with the reason as a comment |
| `playwright` | The Playwright page's own fixed-wait call, with the reason as a comment |

The Playwright emitter writes TypeScript, so its call is `await page.waitForTimeout(<milliseconds>)`. Each emitter keeps the reason, so a generated test stays as reviewable as the scenario.

### Serve Author editor

The Web UI's Author editor ([`serve.author.mjs`](../../bajutsu/templates/serve.author.mjs)) edits scenario YAML as text. It validates every edit through `/api/lint`, which runs the scenario loader, and grades it through `/api/audit`, so the cap, the non-empty rule, and the `fixed-sleep` finding reach the editor without code of their own. The editor's step list labels a `sleep` as `sleep 2s — <reason>`.

### Prime-directive compliance

- **AI never judges.** A fixed pause has no verdict. The pass or fail of the scenario still comes from the assertions, and no model call is involved.
- **Determinism first.** The pause is deterministic in duration and recorded. It is a deliberate, named exception: the cap, the mandatory reason, the report line, and the audit finding keep it from becoming a silent habit. The wording of the principle in [`CLAUDE.md`](../../CLAUDE.md), `README.md`, `DESIGN.md`, and the `docs/` pages that restate it gains the exception, so the documents and the schema do not disagree.
- **App-agnostic.** The step takes no selector and no target-specific value, so nothing about one application enters the tool.

### Work breakdown (MECE)

| # | Work | Files | Done when | Depends on |
|---|---|---|---|---|
| 1 | The `Sleep` model, the `Step` field, and the schema rules (positive, capped, non-empty reason) | `models/actions/sleep.py`, `models/steps/step.py` | A scenario with a valid `sleep` loads; zero, negative, over-cap, and missing-reason cases are rejected; `bajutsu schema` lists it | none |
| 2 | Run-loop execution through `Clock`, sliced for cancellation, and the `record` replay | `orchestrator/loop/_functions.py`, `orchestrator/loop/_step_runner.py`, `record/loop.py` | A fake-clock test advances time by `seconds`; a cancelled run leaves early | 1 |
| 3 | Report row and progress label | `common/report`, `orchestrator/actions/_registry.py` | The report shows `sleep Ns — reason`; the run total is derivable | 2 |
| 4 | The `fixed-sleep` audit finding, the ten-second tier rule, and the triage laxer warning | `analysis/audit/_functions.py`, `triage/heuristic/_functions.py` | Tests report one finding per `sleep` wherever it sits, the tier change, and the warning on a fix that adds a `sleep` | 1 |
| 5 | Keep `sleep` off the `record` and `crawl` AI surfaces | `tests/test_sleep_step.py` | A test shows the model's tool list lacks `sleep` | 1 |
| 6 | The three codegen emitters and their coverage entries | `bajutsu/codegen/*.py` | Each emitter has a golden-output test with the reason comment | 1 |
| 7 | The serve Author editor's step label | `templates/serve.author.mjs` | `make lint-js` passes; `/api/lint` rejects a reason-less draft | 1 |
| 8 | Documentation in both languages, including the reworded principle | `docs/scenarios.md`, `docs/concepts.md`, `docs/cli.md`, `docs/index.md`, `docs/vision.md`, `docs/run-loop.md`, `docs/dsl-grammar.md`, `docs/codegen.md`, `DESIGN.md`, `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, the PR and issue templates, the skills that restate the directive, and the Japanese mirrors | `make check` passes and no statement of the principle omits the exception | 1–7 |

### Decisions taken during implementation

- The alert guard needs no special case: its end-of-step pass runs on a failed step alone, and the interrupt check before an act is skipped for `sleep` as for `wait`.
- The audit grades a scenario `Moderate` once its fixed pauses total more than ten seconds. A single short `sleep` stays a finding without changing the grade, so a justified pause does not hide a real selector problem behind a lower grade.
- `StepOutcome` gains no field, because the step's definition already carries `seconds`.

## Alternatives considered

| Option | Summary | Why not |
|---|---|---|
| `wait` with `until: { duration: N }` | Extend `wait` with a fixed-duration form. | `wait` means "a condition, bounded by `timeout`", and every consumer (audit, report, codegen) relies on that. A duration form turns the one step that never sleeps into one that does. |
| `sleep: 2` with no reason | A bare number. | A review cannot tell a justified pause from a flaky-step workaround, and the audit has no explanation to show. The one extra line is the price of the exception. |
| Allow only when `config` opts in | Reject `sleep` unless the project sets `allowFixedSleep: true`. | The guard is real, but it adds a setting for a step that already carries a reason, a cap, and an audit finding. We can adopt it later if the audit shows misuse. |
| Allow without any trace | A plain pause, no reason, no finding. | The exception would be invisible, and the principle it bends would erode without anyone noticing. |
| No step; use `until: request` and `settled` | Express every wait as a condition. | Network and settle conditions already cover many waits, and the item does not replace them. A server throttle or a deliberate on-screen dwell has no such condition, so some waits stay unexpressible. |

## Progress

> Keep this section current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one entry per unit of work), and the log records what changed and when, oldest
> first, each with a link to its PR.

- [x] The `Sleep` model and schema rules
- [x] Run-loop execution through `Clock`
- [x] Report row and progress label
- [x] The `fixed-sleep` audit finding
- [x] Exclusion from the `record` and `crawl` AI surfaces
- [x] The three codegen emitters
- [x] The serve Author editor's step label
- [x] Documentation and Japanese mirrors

Log:

- [#2120](https://github.com/bajutsu-e2e/bajutsu/pull/2120): proposal and implementation landed together: the model, run-loop execution, report row, audit finding, codegen, Author editor label, tests, and documentation.

## References

- [BE-0118](../BE-0118-wait-for-contract-unification/BE-0118-wait-for-contract-unification.md): the condition-wait contract this item makes one exception to.
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md): the determinism audit that reports each fixed pause.

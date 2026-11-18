**English** · [日本語](BE-0392-scenario-before-after-hooks-ja.md)

# BE-0392 — Independent before/after lifecycle hooks for scenarios

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0392](BE-0392-scenario-before-after-hooks.md) |
| Author | [@akira-matsuda](https://github.com/akira-matsuda) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0392") |
| Implementing PR | [#1762](https://github.com/bajutsu-e2e/bajutsu/pull/1762) |
| Topic | Scenario authoring features |
| Related | [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) |
<!-- /BE-METADATA -->

## Introduction

Two new top-level scenario fields, `before` and `after`, let an author declare setup and teardown
processing. The runner tracks that processing separately from the scenario's own `steps` list
instead of splicing it into the list.

- `before` is an ordered list of steps that runs first, in its own report section. If it fails, the
  scenario aborts before `steps` runs.
- `after` is a list of rules. Each rule pairs an outcome (`always`, `success`, or `error`) with the
  steps to run for that outcome.

With `after`, a scenario can release a leased test record after every run. It can delete data it
created only when the run passed, and capture extra diagnostics only when the run failed. Today
these three concerns share the same scenario file with no way to keep them apart.

Both fields reuse the existing deterministic step and assertion domain-specific language (DSL). A
`before`/`after` hook is thus exactly as machine-checkable as the scenario's own steps.

## Motivation

Bajutsu already has a "before" mechanism, but not an independent one. `preconditions.setup` names a
reusable prelude scenario file. `apply_setups`
([`bajutsu/scenario/expand.py:153`](../../bajutsu/scenario/expand.py)) prepends the steps of that
prelude directly onto the scenario's own `steps` list before the run starts.

The prelude's steps then run indistinguishable from the scenario's own. The report lists them in the
same numbered sequence, and a prelude failure surfaces as an ordinary step failure. Nothing marks
the failure as coming from setup rather than from the scenario itself. An author who wants setup
processing as a visibly separate phase cannot ask for it today. With such a phase, a reviewer
reading the report could tell setup from the scenario under test at a glance.

The gap on the other side is a missing mechanism, not partial coverage: Bajutsu has no "after" hook
at all. The only place to put cleanup today is at the tail of `steps`, and that placement fails the
one case cleanup exists for. The step loop breaks on the first failure
([run-loop](../../docs/run-loop.md), step 9). Trailing cleanup steps therefore run only when every
preceding step already passed, which is exactly the run that needed cleanup least.

Consider a scenario that signs up a test user and then hits a broken button three steps later. The
scenario leaves the user behind, because the step that would have deleted the user was never
reached. Nothing in the scenario file ran the deletion.

A workaround outside the scenario file, such as a CI-level script or a separately scheduled cleanup
job, has two limits:

- The workaround cannot see the run's own `vars.*` bindings. It cannot address the record that an
  `http` step's `saveBody` captured earlier in the same run.
- The workaround cannot tell a scenario that needs diagnostics collected only on failure from one
  that needs its test data deleted only on success.

The runner already treats "after the scenario's own steps, and aware of the run's outcome" as a
distinct phase elsewhere. This item generalizes that shape rather than inventing a new one:

- The scenario-level `expect` block runs strictly after `steps` finishes. It can flip an
  all-passing step sequence to a failing run (`run_scenario`,
  [`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)).
- `capturePolicy`'s `Trigger.result: Literal["error"]`
  ([`bajutsu/scenario/models/evidence.py:22`](../../bajutsu/scenario/models/evidence.py)) already
  fires a rule when a step fails. It is a per-step trigger evaluated throughout the run, not a
  scenario-wide verdict, but it uses the same "error" word for the same idea.

What is missing is a phase symmetric with these two. Its action is an arbitrary list of steps, not
a fixed assertion check or an evidence capture, and it is available before the scenario runs as
well as after.

Once this ships, an author can point to two concrete, checkable differences from today:

- A scenario's report shows a "Before" and an "After" section distinct from its numbered `steps`.
  Today the steps of a `setup` prelude are invisible as such, merged into the same list.
- A scenario whose steps fail partway through still runs its declared `error` and `always` cleanup.
  Today the identical trailing steps would never execute, because the loop already broke before
  reaching them.

## Detailed design

### The `before` and `after` fields

```yaml
# scenario.yaml
scenario:
  before:
    # the seed endpoint returns the new user's bare id as its response body
    - http: { method: POST, url: "${vars.apiBase}/users", saveBody: userId }
  steps:
    - tap: { id: login.button }
    - type: { id: login.username, text: "${vars.userId}" }
    # ...
  after:
    - on: always
      steps:
        - tap: { id: session.logout }
    - on: success
      steps:
        - http: { method: DELETE, url: "${vars.apiBase}/users/${vars.userId}" }
    - on: error
      steps:
        - http: { method: POST, url: "${vars.diagnostics}/report", body: { userId: "${vars.userId}" } }
```

The example's `before` step relies on `http`'s existing `saveBody`, which stores a response's whole
body text as `${vars.<name>}`. The step captures the seed endpoint's bare id directly only because that
endpoint returns nothing else. Extracting one field out of a larger JSON response is a separate,
unrelated gap, and this item does not propose closing it.

`before` is a plain `list[Step]`: an ordered prelude with no branching, since there is nothing yet
to branch on. `after` is a list of `AfterRule` entries. Each entry pairs an `on` outcome (`always`,
`success`, or `error`) with the `steps` to run for it. More than one entry may share the same `on`
value, and such entries compose in declaration order. `capturePolicy` already lets more than one
rule fire on the same trigger in the same way.

The two outcome words `success` and `error` extend the word that `capturePolicy`'s
`Trigger.result: Literal["error"]` already established for a failed outcome. There the word names a
single step's outcome, and here it names the whole scenario's. Reusing the word avoids a second word
for the same idea. `always` is the one addition, for cleanup that does not depend on the outcome at
all.

Both fields also exist at the target-config level (`TargetConfig.before` and `TargetConfig.after`).
There they act as an app-wide default that a scenario's own `before`/`after` extends. This is the
config-then-scenario layering that `interrupts` already established
([`bajutsu/config/schema.py:403`](../../bajutsu/config/schema.py)).

- `before` merges config-then-scenario, matching `interrupts`: the app-wide prelude runs first, then
  the scenario's own addition.
- `after` merges in the opposite order, scenario-then-config. A scenario's own teardown (for
  example, deleting the specific record it created) runs before the app-wide one (for example,
  logging out).

The `after` order mirrors how a resource's own release runs before the outer resource that contains
it. Most fixture-based test frameworks give setup/teardown pairs the same last-acquired-first-released
order. Pytest's fixture teardown is prior art here, not a mechanism this item depends on.

### Runner integration

`run_scenario` ([`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)) gains two
phases around its existing `steps`/`expect` sequence. Both phases run through the recursive
`_ExecSteps` closure, which `if`, `forEach`, and `_InterruptGuard`'s recovery steps already share
([`bajutsu/orchestrator/loop.py:635`](../../bajutsu/orchestrator/loop.py)). A `before`/`after` step
is thus exactly as capable as any other step. It also shares the run's `live_bindings` (`vars.*`)
the way those existing users do.

1. **Before `_run_steps` runs**, execute the effective `before` list. A failure there sets
   `failure = "before: " + reason` and skips `steps` and `expect` entirely.

   `before` is a precondition for the scenario, not a step within it. The runner already refuses to
   start a scenario when the target cannot satisfy its `preconditions`: a failed launch step raises
   a `simctl.DeviceError` from `launch_driver`
   ([`bajutsu/runner/launch.py:27`](../../bajutsu/runner/launch.py)) before `run_scenario` is ever
   called.

   `apply_setups` prepends a `setup` prelude onto `steps` at expand time
   ([`bajutsu/scenario/expand.py:176`](../../bajutsu/scenario/expand.py)). A `before` phase running
   ahead of `_run_steps` thus runs ahead of that prelude too. `before` seeds state that the prelude
   and the scenario both then use, and a `before` step must not depend on a screen the prelude
   reaches.

   `TargetConfig.before` layers under the same rule. It runs before the app-wide `TargetConfig.setup`
   prelude ([`bajutsu/config/schema.py:356`](../../bajutsu/config/schema.py)) and does not replace
   the prelude. The two stay distinct because `before` alone is its own report phase.
2. **`steps` and `expect` run unchanged.** This item does not touch how their own verdict is
   computed. A `before` failure counts as an `error` outcome of step 3 below, the same as an
   ordinary `steps`/`expect` failure would. Cleanup still matters for whatever partial state a
   `before` list left behind before it failed.
3. **Once a verdict exists**, run the effective `after` list in declaration order. A verdict exists
   in three cases:
   - `steps`/`expect` finished normally.
   - `before` failed and skipped them.
   - The run was cancelled (`RunCancelled`, caught where `run_scenario` already sets
     `failure = CANCELLED_FAILURE`,
     [`bajutsu/orchestrator/loop.py:607`](../../bajutsu/orchestrator/loop.py)).

   The phase skips any entry whose `on` matches neither `always` nor that verdict. It runs entries
   interleaved rather than grouped by `on`, so the scenario-then-config order above holds across the
   whole phase and not within one `on` group alone. A cancelled run dispatches as `error`,
   consistent with `docs/run-loop.md`'s framing of a cancelled run as an ordinary failed one.

   An `after` entry's own failure updates `failure` only when the run was passing up to that point
   (`failure = "after: " + reason`), mirroring how `expect` can already flip a passing `steps`
   sequence. When the run had already failed for any reason above, the entry's failure is appended to
   the existing `failure` string instead of replacing it. The reason a reader sees first is then
   still the original cause, not a symptom of cleanup that ran because of it.
4. **The `after` phase runs under its own deadline on a cancelled run.** The phase cannot reuse the
   `cancelled` source. `CancelSource` is a latched `threading.Event.is_set`
   ([`bajutsu/cancellation.py:147`](../../bajutsu/cancellation.py)), and a second `SIGTERM` inside
   the grace window is a deliberate no-op. On the very path that dispatches `after` as `error`, the
   first cancellation check inside `after` would raise `RunCancelled` again, and no cleanup step
   would run at all.

   We bound the phase by time instead. On a cancelled run, `after` stops reading `cancelled` and
   runs under its own deadline carved out of `grace_seconds()`
   ([`bajutsu/cancellation.py:76`](../../bajutsu/cancellation.py)). It abandons the remaining entries
   once that deadline passes. Cleanup thus gets a bounded chance to run, without pushing the
   shutdown tail past the window `serve` waits before an unconditional kill.

   On an uncancelled run, `after` reads `cancelled` at each boundary the way any step does. Either
   way, the phase is reached on every path out of `steps`/`expect`, including the path `RunCancelled`
   already takes. `after` thus runs before the existing
   `finally: artifacts = sink.finish_scenario_intervals(...)`, which already finalizes
   unconditionally for the same reason.

### Report

`RunResult` ([`bajutsu/orchestrator/types.py:171`](../../bajutsu/orchestrator/types.py)) gains
`before_outcomes: list[StepOutcome]` and `after_outcomes: list[StepOutcome]`. They sit alongside
`steps` and `expect_results` rather than folded into either, the same separation `expect_results`
already gets today.

The report's Steps tab renders a "Before" block and an "After" block distinct from the scenario's
numbered steps. A reviewer can then tell setup and teardown apart from the scenario under test at a
glance. This closes the gap named in the Motivation: today's `setup` prelude leaves no such marker.

### Codegen

`interrupts` (BE-0314) has no native equivalent and falls back to a labeled comment everywhere.
`before` and the `always` entries of `after` differ: they map onto a construct that every codegen
target already exposes.

- Playwright: `test.beforeEach` and `afterEach`.
- XCTest: `setUpWithError` and `tearDownWithError`.
- Espresso/JUnit: `@Before` and `@After`.

`success`/`error` branching needs each framework's own way to read the test's outcome inside its
teardown hook: Playwright's `testInfo.status`, XCTest's `testRun?.hasSucceeded`, and JUnit's
`TestWatcher`. A codegen target that supports this emits the branch. A target that does not yet
falls back to the labeled `// TODO` comment that
[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) and BE-0314
already use elsewhere.

### Work breakdown (MECE)

1. **Schema.** `before: list[Step]` and `after: list[AfterRule]` (`AfterRule = { on: Literal["always",
   "success", "error"], steps: list[Step] }`) on both `Scenario`
   ([`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)) and
   `TargetConfig` ([`bajutsu/config/schema.py`](../../bajutsu/config/schema.py)). Both default to
   empty, so an unset field prunes from a dump, matching `interrupts`.
2. **Merge helpers.** Config-then-scenario for `before`, scenario-then-config for `after`, resolved
   once at the same point `interrupts`' config/scenario merge already resolves.
3. **Runner integration.** The before-phase gate, the after-phase's outcome-aware dispatch, and the
   failure-reason composition described above, in `run_scenario`.
4. **Report.** `before_outcomes` and `after_outcomes` on `RunResult`, and the report renderer's
   distinct "Before" and "After" sections. Two exports read `RunResult.steps` directly and would
   otherwise drop a failure that occurred in `before` or `after` alone: `_details`, the JUnit
   `<failure>` body ([`bajutsu/report/manifest.py`](../../bajutsu/report/manifest.py)), and the CTRF
   step list ([`bajutsu/report/ctrf.py`](../../bajutsu/report/ctrf.py)). `asdict` carries the new
   fields into `manifest.json` unchanged.
5. **Codegen.** `beforeEach`/`afterEach` (or the per-backend equivalent) emission for `always`
   entries, outcome-aware emission for `success`/`error` where the backend supports it, and the TODO
   fallback elsewhere.
6. **Docs and fixture.** Document `before`/`after` in [`docs/scenarios.md`](../../docs/scenarios.md)
   and its Japanese mirror. A comparison table sits alongside `preconditions.setup` (an in-`steps`
   prelude) and `capturePolicy` (evidence capture keyed to the same `error` outcome), explaining when
   to reach for each. Add a showcase fixture whose scenario creates a record in `before`, then
   deletes the record in an `after` `success` rule.
7. **Tests.** Schema parse and validation for both levels, both merge orders, `vars.*` sharing into
   and out of both phases, and the new `RunResult` fields. Verdict computation across the full
   outcome matrix:
   - A failing `before` skips `steps`/`expect` and dispatches `after` as `error`.
   - `always` runs regardless of the outcome.
   - An `error` rule's own failure is appended rather than replacing the original `failure`.
   - A `success` rule's failure becomes the sole `failure` on an otherwise-passing run.
   - A cancelled run dispatches `after` as `error` and still runs its cleanup entries, because the
     latched `cancelled` source is not read on that path. The run abandons the remaining entries once
     the phase's own deadline passes.

   A `success`-rule failure on an otherwise-passing run must also name that rule in the JUnit failure
   text and the CTRF record, not in the HTML report alone.

### Prime directives preserved

- **AI never judges.** `before`/`after` steps are the existing deterministic Step DSL. The outcome an
  `after` rule branches on (`success`/`error`) is the scenario's own machine-checked verdict, never
  a model call. This item adds no new AI surface.
- **Determinism first.** No fixed `sleep`: `before`/`after` steps use the same condition-wait
  primitives every other step already does. The outcome dispatch is a plain comparison against the
  already-computed `failure` value.
- **App-agnostic.** `before`/`after` are config and scenario data. The runner and report gain one
  generic mechanism, not per-app code.

## Alternatives considered

- **Add a symmetric `preconditions.teardown` field, spliced into `steps` the same way `setup`
  already is.** Rejected. Splicing keeps the exact problem this item exists to fix. A spliced
  teardown step still cannot run after an earlier step failed and broke the loop, and it still
  shows up in the report as an ordinary step rather than its own phase. The point is to stop
  merging setup and teardown into `steps`.
- **Extend `capturePolicy`'s `Trigger` with a `result: "success"` value, and let a rule's action be
  arbitrary steps instead of only evidence capture.** Rejected. `Trigger` also matches `action` and
  `event`, keyed to opportunistic checks made throughout the step loop, not to the scenario's single
  final verdict. Overloading `Trigger` would let an author write a `before`/`after`-shaped rule that
  fires mid-run by accident. It would also conflate two features that are triggered at fundamentally
  different times for different reasons.
- **Three fixed keys (`always`, `onSuccess`, `onFailure`) instead of a list of `{on, steps}`
  entries.** Rejected in favor of the list shape. Every other outcome-branching field this schema
  already has (`capturePolicy`, `interrupts`, `systemAlertHandling.rules`) is a list of entries
  carrying their own condition, not a fixed-shape object. A list composes where three fixed keys
  cannot: a second `error` entry can serve a distinct concern.
- **Give `after` hooks their own retry or timeout policy, independent of ordinary step semantics.**
  Deferred, not rejected: nothing here prevents adding the policy later. The gap named in the
  Motivation is that after-cleanup cannot run at all once an earlier step has failed. The gap is not
  that cleanup needs different resilience once it can run. Closing the first gap does not require
  solving the second at the same time.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — `before: list[Step]` / `after: list[AfterRule]` schema on `Scenario` and
      `TargetConfig`.
- [x] Unit 2 — config-then-scenario merge for `before`, scenario-then-config merge for `after`.
- [x] Unit 3 — runner integration in `run_scenario` (before-phase gate, after-phase outcome
      dispatch, failure-reason composition).
- [x] Unit 4 — `before_outcomes` / `after_outcomes` on `RunResult`; report renderer sections; the
      JUnit `_details` body and the CTRF step list.
- [x] Unit 5 — codegen mapping (`beforeEach`/`afterEach` and outcome-aware emission where
      supported; TODO fallback elsewhere).
- [x] Unit 6 — docs (scenarios.md + ja) with a comparison table, and a showcase fixture.
- [x] Unit 7 — tests (schema, both merge orders, verdict-computation matrix, `vars.*` sharing,
      report fields).

Two details of the shipped implementation differ from the Detailed design above, both decided while
building it:

- **`RunResult` carries a third new field, `after_verdict`.** The report renders the After block by
  lining each outcome up with the rule that declared it, and the rules a run dispatched cannot be
  recovered from `failure` once a cleanup step's own reason has been folded into it. Recording the
  verdict the phase dispatched on is what makes that alignment exact.
- **No codegen target needed the `// TODO` fallback.** Every target expresses the phase natively.
  Playwright and UI Automator wrap the test body in `try` / `catch` / `finally` rather than in a
  `beforeEach`/`afterEach` pair, because a framework hook is registered per describe block or per
  class while `before`/`after` are per scenario, and both targets' assertions throw, so the `catch`
  observes the verdict directly. XCUITest registers one `addTeardownBlock` and reads
  `testRun?.hasSucceeded`, since `XCTAssert` records a failure rather than throwing.

## References

- [`bajutsu/scenario/expand.py:153`](../../bajutsu/scenario/expand.py) — `apply_setups`, the
  existing before-only mechanism this item complements, whose steps splice directly into `steps`
  rather than running as their own phase.
- [`bajutsu/runner/launch.py:27`](../../bajutsu/runner/launch.py) — `launch_driver`, where an
  unsatisfiable `preconditions` value already fails a scenario before `run_scenario` runs at all —
  the precedent `before`'s own gating follows.
- [`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py) — `run_scenario`, the
  exact seam the new before/after phases slot into.
- [`bajutsu/orchestrator/loop.py:635`](../../bajutsu/orchestrator/loop.py) — `_ExecSteps`, the
  recursive step-execution closure `if`/`forEach`/`interrupts` already share, and that `before`/
  `after` reuse rather than duplicate.
- [`bajutsu/cancellation.py:147`](../../bajutsu/cancellation.py) — `CancelSource` as a latched
  `threading.Event.is_set`, and the `grace_seconds()` budget above it (60s worst-case driver call
  plus a 30s shutdown tail) that leaves no room for an unbounded teardown phase — together the
  reason the `after` phase is bounded by its own deadline rather than by the cancel latch.
- [`bajutsu/scenario/models/evidence.py:22`](../../bajutsu/scenario/models/evidence.py) —
  `Trigger.result: Literal["error"]`, the existing `capturePolicy` outcome word this item's `after`
  rules extend with `success` rather than replace.
- [`bajutsu/orchestrator/types.py:171`](../../bajutsu/orchestrator/types.py) — `RunResult`, where
  `expect_results` already sits beside `steps` rather than inside it — the precedent
  `before_outcomes`/`after_outcomes` follow.
- [BE-0030 — Parameterized shared steps](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md) —
  the existing `setup` prelude and `use` component-reuse mechanism this item's `before` field
  complements rather than replaces.
- [BE-0033 — Scenario variables + light control flow](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md) —
  the `vars.*`-sharing precedent `before`/`after` steps follow.
- [BE-0314 — Deterministic interrupt handlers for unpredictable interstitial screens](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) —
  the nearest sibling shape: a config-then-scenario-merged, deterministic-condition-gated list of
  steps, and the codegen TODO-fallback convention this item's codegen unit reuses for the one case
  it cannot map natively.

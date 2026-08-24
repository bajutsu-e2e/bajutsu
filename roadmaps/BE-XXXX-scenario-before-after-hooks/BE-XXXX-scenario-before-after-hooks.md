**English** · [日本語](BE-XXXX-scenario-before-after-hooks-ja.md)

# BE-XXXX — Independent before/after lifecycle hooks for scenarios

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-scenario-before-after-hooks.md) |
| Author | [@akira-matsuda](https://github.com/akira-matsuda) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0030](../BE-0030-parameterized-shared-steps/BE-0030-parameterized-shared-steps.md), [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) |
<!-- /BE-METADATA -->

## Introduction

Two new top-level scenario fields, `before` and `after`, let an author declare setup and teardown
processing that the runner tracks separately from the scenario's own `steps` list, rather than
splicing it into that list. `before` is an ordered list of steps that runs first, on its own
report section, and aborts the scenario before `steps` runs if it fails. `after` is a list of
rules, each pairing an outcome (`always`, `success`, or `error`) with the steps to run for it, so a
scenario can release a leased test record after every run, delete data it created only when the run
passed, and capture extra diagnostics only when it failed — three concerns that share the same
scenario file today with no way to keep them apart. Both fields reuse the existing deterministic
step and assertion domain-specific language (DSL), so a `before`/`after` hook is exactly as
machine-checkable as the scenario's own steps.

## Motivation

Bajutsu already has a "before" mechanism, but not an independent one. `preconditions.setup` names a
reusable prelude scenario file, and `apply_setups`
([`bajutsu/scenario/expand.py:153`](../../bajutsu/scenario/expand.py)) prepends that prelude's
steps directly onto the scenario's own `steps` list before the run starts. The prelude's steps then
run indistinguishable from the scenario's own: the report lists them in the same numbered sequence,
and a prelude failure surfaces as an ordinary step failure with no marker showing it came from
setup rather than the scenario itself. An author who wants setup processing that is visibly its own
phase — so a reviewer reading the report can tell setup from the scenario under test at a glance —
has no way to ask for that today.

The gap on the other side is not partial coverage but a missing mechanism: Bajutsu has no "after"
hook at all. The only place to put cleanup today is at the tail of `steps`, and that placement
fails the one case cleanup exists for. The step loop breaks on the first failure
([run-loop](../../docs/run-loop.md), step 9), so trailing cleanup steps run only when every
preceding step already passed — exactly the run that needed cleanup least. A scenario that signs
up a test user, then hits a broken button three steps later, leaves that user behind: nothing in
the scenario file ever ran the deletion, because the step that would have run it was never reached.
A workaround outside the scenario file — a CI-level script, a separately scheduled cleanup job —
cannot see the run's own `vars.*` bindings, so it cannot address the specific record a `http`
step's `saveBody` captured earlier in that same run, and it cannot distinguish a scenario that needs
extra diagnostics collected only on failure from one that needs its test data deleted only on
success.

The runner already treats "after the scenario's own steps, and aware of the run's outcome" as a
distinct phase elsewhere, which is the shape this item generalizes rather than invents. The
scenario-level `expect` block runs strictly after `steps` finishes, and can still flip an
all-passing step sequence to a failing run (`run_scenario`,
[`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)); `capturePolicy`'s
`Trigger.result: Literal["error"]` ([`bajutsu/scenario/models/evidence.py:22`](../../bajutsu/scenario/models/evidence.py))
already fires a rule specifically when a step fails, keyed to that same "error" outcome word — a
per-step trigger evaluated throughout the run, not a scenario-wide verdict, but the same word for
the same idea. What
is missing is a phase, symmetric with these two, whose action is an arbitrary list of steps rather
than either a fixed assertion check or an evidence capture, and that is available before the
scenario runs as well as after it.

Once this ships, an author can point to two concrete, checkable differences from today. First, a
scenario's report shows a "Before" and an "After" section distinct from its numbered `steps` —
where today a `setup` prelude's steps are invisible as such, merged into the same list. Second, a
scenario whose steps fail partway through still runs its declared `error`/`always` cleanup — where
today the identical trailing steps would simply never execute, because the loop already broke
before reaching them.

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
body text as `${vars.<name>}` — it captures the seed endpoint's bare id directly only because that
endpoint is written to return nothing else; extracting one field out of a larger JSON response is a
separate, unrelated gap this item does not propose closing.

`before` is a plain `list[Step]` — an ordered prelude with no branching, since there is nothing yet
to branch on. `after` is a list of `AfterRule` entries, each an `on` outcome (`always`, `success`,
or `error`) paired with the `steps` to run for it; more than one entry may share the same `on`
value, composing in declaration order, the same way `capturePolicy` already lets more than one rule
fire on the same trigger. `on`'s two outcome words, `success` and `error`, extend the word
`capturePolicy`'s `Trigger.result: Literal["error"]` already established for a failed outcome —
there a single step's, here the whole scenario's — rather than inventing a second word for the same
idea; `always` is the one addition, for cleanup that does not depend on the outcome at all.

Both fields also exist at the target-config level (`TargetConfig.before` /
`TargetConfig.after`), as an app-wide default a scenario's own `before`/`after` extends — the same
config-then-scenario layering `interrupts` already established
([`bajutsu/config/schema.py:403`](../../bajutsu/config/schema.py)).
`before` merges config-then-scenario (the app-wide prelude runs first, then this scenario's own
addition), matching `interrupts`. `after` merges in the opposite order, scenario-then-config: a
scenario's own teardown (for example, deleting the specific record it created) runs before the
app-wide one (for example, logging out), mirroring how a resource's own release runs before the
outer resource that contains it — the same
last-acquired-first-released order most fixture-based test frameworks already give setup/teardown
pairs (pytest's fixture teardown, cited here as prior art rather than as a mechanism this item
depends on).

### Runner integration

`run_scenario` ([`bajutsu/orchestrator/loop.py:478`](../../bajutsu/orchestrator/loop.py)) gains two
phases around its existing `steps`/`expect` sequence, both driven by the same recursive `_ExecSteps`
closure `if`, `forEach`, and `_InterruptGuard`'s recovery steps already share
([`bajutsu/orchestrator/loop.py:635`](../../bajutsu/orchestrator/loop.py)), so a `before`/`after`
step is exactly as capable as any other step, and shares the run's `live_bindings` (`vars.*`) the
same way those existing users do:

1. **Before `_run_steps` runs**, execute the effective `before` list. A failure there sets
   `failure = "before: " + reason` and skips `steps` and `expect` entirely — `before` is a
   precondition for the scenario, not a step within it, the same way the runner already refuses to
   start a scenario at all when its `preconditions` are ones the target cannot satisfy (a failed
   launch step raises a `simctl.DeviceError` from `launch_driver`,
   [`bajutsu/runner/launch.py:27`](../../bajutsu/runner/launch.py), before `run_scenario` is ever
   called).
2. **`steps` and `expect` run unchanged.** This item does not touch how their own verdict is
   computed. A `before` failure counts as an `error` outcome of step 3 below, the
   same as an ordinary `steps`/`expect` failure would — cleanup still matters for whatever partial
   state a `before` list left behind before it failed.
3. **Once a verdict exists** — `steps`/`expect` finished normally, `before` failed and skipped them,
   or the run was cancelled (`RunCancelled`, caught where `run_scenario` already sets
   `failure = CANCELLED_FAILURE`, [`bajutsu/orchestrator/loop.py:607`](../../bajutsu/orchestrator/loop.py)) —
   run the effective `after` list's `always` entries in declaration order, then whichever of
   `success` / `error` matches that verdict (a cancelled run dispatches as `error`, consistent with
   `docs/run-loop.md`'s framing of a cancelled run as an ordinary failed one). An `after` entry's own
   failure updates `failure` only when the run was passing up to that point (`failure = "after: " +
   reason`, mirroring how `expect` can already flip a passing `steps` sequence); when the run had
   already failed for any reason above, an `after` entry's failure is appended to the existing
   `failure` string instead of replacing it, so the reason a reader sees first is still the original
   cause, not a symptom of cleanup that ran because of it.
4. The `after` phase reuses the same `cancelled` source `steps` already reads at every boundary and
   wait-poll tick, so a fresh cancellation asserted while `after` itself is running cuts it short the
   same way it would a step — cleanup does not get to run unbounded past a second cancel request.
   Both phases are reached on every path out of `steps`/`expect`, including the one `RunCancelled`
   already takes, so `after` runs before the existing
   `finally: artifacts = sink.finish_scenario_intervals(...)`, which already finalizes
   unconditionally for the same reason.

### Report

`RunResult` ([`bajutsu/orchestrator/types.py:171`](../../bajutsu/orchestrator/types.py)) gains
`before_outcomes: list[StepOutcome]` and `after_outcomes: list[StepOutcome]`, alongside `steps` and
`expect_results` rather than folded into either — the same separation `expect_results` already gets
today. The report's Steps tab renders a "Before" block and an "After" block distinct from the
scenario's numbered steps, so a reviewer can tell setup and teardown apart from the scenario under
test at a glance, closing the gap this item's Motivation names: today's `setup` prelude leaves no
such marker.

### Codegen

Unlike `interrupts` (BE-0314), which has no native equivalent and falls back to a labeled comment
everywhere, `before` and `after`'s `always` entries map onto a construct every codegen target
already exposes: Playwright's `test.beforeEach`/`afterEach`, XCTest's
`setUpWithError`/`tearDownWithError`, and Espresso/JUnit's `@Before`/`@After`. `success`/`error`
branching needs each framework's own way to read the test's outcome inside its teardown hook
(Playwright's `testInfo.status`, XCTest's `testRun?.hasSucceeded`, JUnit's `TestWatcher`); emit that
branch for a codegen target that supports it, and fall back to the labeled `// TODO` comment
[BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) and BE-0314
already use elsewhere for a target that does not yet.

### Work breakdown (MECE)

1. **Schema.** `before: list[Step]` and `after: list[AfterRule]` (`AfterRule = { on: Literal["always",
   "success", "error"], steps: list[Step] }`) on both `Scenario`
   ([`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)) and
   `TargetConfig` ([`bajutsu/config/schema.py`](../../bajutsu/config/schema.py)); both default to
   empty so an unset field prunes from a dump, matching `interrupts`.
2. **Merge helpers.** Config-then-scenario for `before`, scenario-then-config for `after`, resolved
   once at the same point `interrupts`' config/scenario merge already resolves.
3. **Runner integration.** The before-phase gate, the after-phase's outcome-aware dispatch, and the
   failure-reason composition described above, in `run_scenario`.
4. **Report.** `before_outcomes` / `after_outcomes` on `RunResult`; the report renderer's distinct
   "Before" / "After" sections.
5. **Codegen.** `beforeEach`/`afterEach` (or the per-backend equivalent) emission for `always`
   entries; outcome-aware emission for `success`/`error` where the backend supports it; the TODO
   fallback elsewhere.
6. **Docs and fixture.** Document `before`/`after` in
   [`docs/scenarios.md`](../../docs/scenarios.md) and its Japanese mirror, with a comparison table
   alongside `preconditions.setup` (an in-`steps` prelude) and `capturePolicy` (evidence capture
   keyed to the same `error` outcome) explaining when to reach for each. Add a showcase fixture
   whose scenario creates a record in `before`, then deletes it in an `after` `success` rule.
7. **Tests.** Schema parse/validate for both levels; both merge orders; `vars.*` sharing into and
   out of both phases; the new `RunResult` fields. Verdict computation across the full outcome
   matrix: a failing `before` skips `steps`/`expect` and dispatches `after` as `error`; `always`
   runs regardless of the outcome; an `error` rule's own failure is appended rather than replacing
   the original `failure`; a `success` rule's failure becomes the sole `failure` on an
   otherwise-passing run; a cancelled run dispatches `after` as `error` and a fresh cancellation
   during `after` itself cuts the phase short the same way it would an ordinary step.

### Prime directives preserved

- **AI never judges.** `before`/`after` steps are the existing deterministic Step DSL; the outcome
  an `after` rule branches on (`success`/`error`) is the scenario's own machine-checked verdict,
  never a model call. This item adds no new AI surface.
- **Determinism first.** No fixed `sleep`: `before`/`after` steps use the same condition-wait
  primitives every other step already does, and the outcome dispatch is a plain comparison against
  the already-computed `failure` value.
- **App-agnostic.** `before`/`after` are config/scenario data; the runner and report gain one
  generic mechanism, not per-app code.

## Alternatives considered

- **Add a symmetric `preconditions.teardown` field, spliced into `steps` the same way `setup`
  already is.** Rejected: splicing keeps the exact problem this item exists to fix — a spliced
  teardown step still cannot run after an earlier step failed and broke the loop, and it still
  shows up in the report as an ordinary step rather than its own phase. The whole point is to stop
  merging setup/teardown into `steps`.
- **Extend `capturePolicy`'s `Trigger` with a `result: "success"` value, and let a rule's action be
  arbitrary steps instead of only evidence capture.** Rejected: `Trigger` also matches `action` and
  `event`, keyed to opportunistic checks made throughout the step loop, not to the scenario's single
  final verdict; overloading it would let an author write a `before`/`after`-shaped rule that fires
  mid-run by accident, and conflates two features that are triggered at fundamentally different
  times for different reasons.
- **Three fixed keys (`always`, `onSuccess`, `onFailure`) instead of a list of `{on, steps}`
  entries.** Rejected in favor of the list shape: every other outcome-branching field this schema
  already has — `capturePolicy`, `interrupts`, `systemAlertHandling.rules` — is a list of entries
  carrying their own condition, not a fixed-shape object, and a list composes (a second `error`
  entry for a distinct concern) where three fixed keys cannot.
- **Give `after` hooks their own retry or timeout policy, independent of ordinary step semantics.**
  Deferred, not rejected: nothing here prevents adding it later. The gap named in this item's
  Motivation is that after-cleanup cannot run at all once an earlier step has failed, not that it
  needs different resilience once it can — closing the first gap does not require solving the
  second at the same time.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `before: list[Step]` / `after: list[AfterRule]` schema on `Scenario` and
      `TargetConfig`.
- [ ] Unit 2 — config-then-scenario merge for `before`, scenario-then-config merge for `after`.
- [ ] Unit 3 — runner integration in `run_scenario` (before-phase gate, after-phase outcome
      dispatch, failure-reason composition).
- [ ] Unit 4 — `before_outcomes` / `after_outcomes` on `RunResult`; report renderer sections.
- [ ] Unit 5 — codegen mapping (`beforeEach`/`afterEach` and outcome-aware emission where
      supported; TODO fallback elsewhere).
- [ ] Unit 6 — docs (scenarios.md + ja) with a comparison table, and a showcase fixture.
- [ ] Unit 7 — tests (schema, both merge orders, verdict-computation matrix, `vars.*` sharing,
      report fields).

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

**English** · [日本語](BE-0314-scenario-interrupt-handlers-ja.md)

# BE-0314 — Deterministic interrupt handlers for unpredictable interstitial screens

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0314](BE-0314-scenario-interrupt-handlers.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0314") |
| Implementing PR | [#1316](https://github.com/bajutsu-e2e/bajutsu/pull/1316), [#1489](https://github.com/bajutsu-e2e/bajutsu/pull/1489) |
| Topic | Scenario authoring features |
| Related | [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a declarative `interrupts` field. The field names a screen that an app can show at an
unpredictable point in a run, together with the steps that clear the screen. Examples are an
onboarding step, a tutorial overlay, and a permission prompt that the accessibility tree can see.

The field is set at the config level, as an app-wide default, and extended per scenario.

The runner checks the `condition` of each `interrupts` entry opportunistically. The check uses the
deterministic assertion domain-specific language (DSL) that already backs `if`
([BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)).
The check runs against the element tree that the runner has already fetched for the step in progress,
wherever in the step sequence the interstitial appears. An author no longer has to predict the single
spot in the scenario to check.

## Motivation

Bajutsu already lets a scenario branch on the current screen. `if`
([BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md),
`_run_if`, [`bajutsu/orchestrator/loop.py:472`](../../bajutsu/orchestrator/loop.py)) evaluates a
`condition` against a single `driver.query()` and runs `then` or `else`.

That single check fits well when the author knows exactly which step precedes the screen in question.
It is the wrong tool when the appearance of the screen is not tied to any one step. A permission
dialog, an app-specific onboarding screen, or a promotional overlay can each surface a few steps
earlier or later than expected, or not at all. Account state, network timing, or an A/B cohort decides.

A single `if` after a chosen step catches the screen only when the screen appears exactly there. Every
other timing slips through unhandled. The rest of the scenario then fails against a screen that it was
not written to expect.

The gap is concrete, not hypothetical.
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) added a
deterministic, pre-launch `permissions` field that grants or revokes an OS permission before the first
request of the app. BE-0276 documents one permission that its own mechanism cannot reach: iOS
notification authorization has no `simctl privacy` TCC (Transparency, Consent, and Control) service.
BE-0276 falls back to `dismissAlerts`, the vision-based alert guard
([`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py)), as the only path left for that prompt.

That guard is deliberately the one AI call left in `run` (prime directive 1: AI investigates and
never judges pass/fail). It fires reactively, after a step has already failed or a wait already looks
blocked.

A scenario may want a deterministic, machine-checkable way to handle a screen that the tree can
actually see, as opposed to an out-of-process system alert that only vision can locate. Today such a
scenario has nowhere to put that handling, because the timing of the screen is not tied to a step.

Two existing mechanisms already establish the right shape for a fix, applied to a different layer:

- `dismissAlerts` is both a config-level default and a scenario-level override (`dismiss_alerts` in
  [`bajutsu/config/schema.py:368`](../../bajutsu/config/schema.py) and in
  [`bajutsu/scenario/models/scenario.py:104`](../../bajutsu/scenario/models/scenario.py)). An
  app-wide expectation ("this app always shows X") composes with a per-scenario addition.
- [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
  showed that a screen-collapse check can ride on the already-fetched tree of a poll at zero extra
  query cost. BE-0269 also showed that a bounded, capped intervention can resume the interrupted wait
  and need not abandon it.

XCUITest reaches independently for the same pattern outside this codebase. `addUIInterruptionMonitor`
registers a handler that the test framework consults whenever a screen interaction is blocked,
regardless of which line of the test triggered it.

This proposal brings that shape to the tree that the drivers of Bajutsu already see: registration at
the config or scenario level, and checking that runs continuously and not at one scripted point. The
check itself stays deterministic (the assertion DSL). The proposal adds no second AI surface.

## Detailed design

### The `interrupts` field

```yaml
# config.yaml — an app-wide default: this app's onboarding/tutorial screen
targets:
  myapp:
    interrupts:
      - condition: { exists: { id: onboarding.skip } }
        steps:
          - tap: { id: onboarding.skip }
```

```yaml
# scenario.yaml — this scenario's own addition, merged with the config-level list
scenario:
  interrupts:
    - condition: { exists: { id: att.dialog } }        # App Tracking Transparency prompt
      steps:
        - tap: { id: att.allow }
  steps:
    - tap: { id: login.button }
    - wait: { for: { id: home.title }, timeout: 10 }
```

Each entry has two parts:

- A `condition`, in the same assertion DSL that `if` already uses: `exists`, `value`, and the rest of
  the [Assertion DSL](../../docs/scenarios.md#assertion-dsl).
- A `steps` list to run when the condition matches.

`interrupts` at the config level is an app-wide default. The `interrupts` list of a scenario is
appended to the default, with config entries first. The layering mirrors how `dismissAlerts` already
layers a config-level default under a scenario-level value. The `steps` of an entry share the
`vars.*` bindings of the enclosing scenario, as the `then` and `else` branches of `if` do today.

### Work breakdown (MECE)

1. **Scenario and config schema.** Add `interrupts: list[Interrupt]` to both `Config`
   ([`bajutsu/config/schema.py`](../../bajutsu/config/schema.py)) and `Scenario`
   ([`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)). Validate each
   entry as `{ condition: Assertion, steps: list[Step] }`. The entry reuses the existing `Assertion`
   and `Step` models that `If` at
   [`bajutsu/scenario/models/steps.py:71`](../../bajutsu/scenario/models/steps.py) already validates
   against.

   At load time, the effective list for a run is the entries of the config followed by the entries of
   the scenario. The precedence is the same config-then-scenario precedence that the `_enabled` helper
   of `_alert_guard_factory` already applies to `dismiss_alerts`
   ([`bajutsu/cli/commands/run.py:339`](../../bajutsu/cli/commands/run.py)).
2. **Opportunistic check, reusing already-fetched trees.** Thread the effective `interrupts` list into
   `_run_steps` ([`bajutsu/orchestrator/loop.py:513`](../../bajutsu/orchestrator/loop.py)). The loop
   evaluates the `condition` of each entry against a tree that the loop already holds. Two sources
   qualify: the `before` / `after` query already taken for a `screenChanged`-policy step, and the poll
   result already fetched inside `_wait` ([`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py))
   on every tick.

   A step kind that takes no such tree today, such as a bare `tap` or `type` with no capture policy,
   takes one extra `query()` immediately before the act. `if` already pays the same cost for its own
   single check. Only scenarios that declare at least one `interrupts` entry pay the cost.
3. **On a match, run the entry's `steps`, then resume.** Reuse the `_ExecSteps` callable that `if` and
   `forEach` already close over ([`bajutsu/orchestrator/loop.py:452`](../../bajutsu/orchestrator/loop.py))
   to run the `steps` of the matched entry. After the call returns, resume the interrupted step
   exactly where it left off:
   - A `wait` keeps polling toward its original `deadline`. This is the "resume, don't restart the
     timeout" contract that BE-0269 established for the alert guard.
   - An act step re-attempts its single action once.

   The `steps` of the interrupt usually dismiss the screen, so the `condition` turns from true back
   to false. A step in that common case proceeds normally on resume.
4. **Cap re-entrancy.** An interrupt whose own `steps` do not clear its `condition` must not turn the
   run into an infinite loop. Examples are a broken selector and a screen that re-renders identically.
   Cap consecutive fires of the *same* entry within one step's resolution at a small fixed number.
   Once the cap is hit, fall back to running the original step to its ordinary outcome: pass, fail, or
   timeout. The cap mirrors the `_GUARD_MAX_ATTEMPTS` / `_GUARD_COOLDOWN` shape of BE-0269. A mis-set
   condition then fails the step cleanly and does not hang the run.
5. **codegen.** No native XCUITest, Espresso, or Playwright construct maps onto "check this condition
   opportunistically throughout the whole test". Emit a labeled `// TODO` that names the field and
   each configured `condition`. The output is consistent with how
   [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) and BE-0276
   already handle a field with no app-level equivalent.
6. **Docs and fixture.** Document `interrupts` in [`docs/scenarios.md`](../../docs/scenarios.md) and
   its Japanese mirror, alongside `dismissAlerts` and `permissions`. Add a comparison table of when to
   reach for each mechanism:
   - `if`, for a known spot.
   - `interrupts`, for an unpredictable spot visible in the tree.
   - `dismissAlerts`, for an out-of-process screen handled by AI vision.
   - `permissions`, to avoid the prompt outright.

   Add a showcase fixture. Its config declares an onboarding-screen interrupt. Its scenario exercises
   the screen appearing mid-flow.
7. **Tests.** Cover these cases:
   - Schema parse and validate for both levels.
   - Config-then-scenario ordering.
   - A fake driver whose tree flips a condition true partway through the poll loop of a `wait`.
   - A fake driver whose tree flips a condition true partway through a plain `tap`.
   - The re-entrancy cap firing and falling back cleanly.
   - `vars.*` sharing between the enclosing scenario and the `steps` of an entry.

### Prime directives preserved

- **AI never judges.** `condition` is the existing assertion DSL: a machine-checkable predicate and
  never a model call. This item adds no new AI surface. It gives scenarios a deterministic path for
  the cases that would otherwise fall to the vision `dismissAlerts` guard.
- **Determinism first.** No fixed `sleep` appears. The check rides on ticks that the loop or a `wait`
  already performs. A capped re-entrancy count keeps a mis-set entry from hanging a run, and the run
  fails instead.
- **App-agnostic.** The `interrupts` list itself is config or scenario data. The runner and drivers
  gain one generic mechanism and no per-app code.

## Alternatives considered

- **Extend `if` with a `timeout` so it polls before branching.** This was the starting point of the
  discussion, and it does turn `if` into a wait-then-branch. The author still has to place the `if`
  at one chosen point in the step sequence. The extension therefore does not solve the actual problem:
  an interstitial whose timing relative to the step sequence cannot be predicted has no single correct
  point to place it. Rejected as the primary shape for that reason. `if` keeps its current, simpler
  single-check semantics for the cases where a chosen spot is genuinely known.
- **A new local `switch`/race step over several candidate conditions, still placed at one point in
  `steps`.** The step comes closer than a bare `if`. It helps when an author does know a specific point
  where one of several screens might appear. But it has the same placement problem as the `if`
  extension, because it still asks the author to guess the right point. Deferred. Nothing here would
  prevent adding the step later as a local complement to the config-level and scenario-level
  `interrupts` mechanism that this item proposes.
- **Extend `dismissAlerts` to cover in-tree screens too.** Rejected. `dismissAlerts` is deliberately
  the one AI-vision path in `run`, reserved for screens that the accessibility tree cannot see at all.
  An in-tree screen already has a deterministic signal available: a `condition` against `query()`.
  Routing the screen through the vision guard anyway would add cost and non-determinism, which this
  item removes the need for.
- **A silent per-app allowlist of "expected extra screens" with no explicit recovery steps.**
  Rejected. Silently ignoring a matched screen, instead of running an author-specified `steps` list,
  gives up the ability to dismiss the screen. It also produces no evidence trail. When the allowlist
  masked something that the author did not anticipate, nothing would explain what happened.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — scenario and config schema (`interrupts: list[Interrupt]`), config-then-scenario
      resolution order.
- [x] Unit 2 — opportunistic condition check reusing already-fetched trees in `_run_steps` and
      `_wait`.
- [x] Unit 3 — on a match, run the entry's `steps` via the existing `_ExecSteps` machinery, then
      resume the interrupted step (wait: same deadline; act: one re-attempt).
- [x] Unit 4 — re-entrancy cap per entry, with a clean fallback to the step's ordinary outcome.
- [x] Unit 5 — codegen labeled TODO for the field.
- [x] Unit 6 — docs (scenarios.md + ja) with an `if` / `interrupts` / `dismissAlerts` / `permissions`
      comparison, and a showcase fixture.
- [x] Unit 7 — tests (schema, ordering, wait- and act-step matches, re-entrancy cap, `vars.*`
      sharing).

**Log**

- [#1316](https://github.com/bajutsu-e2e/bajutsu/pull/1316) — implemented all seven units: the
  `Interrupt` schema on `Scenario` / `TargetConfig` threaded through `RunDefaults` / `resolve`
  (config-then-scenario order); `_InterruptGuard` in `bajutsu/orchestrator/loop.py` (per-step
  re-entrancy cap, conditions interpolated once, recovery via the existing `exec_steps` seam, a
  `running_recovery` flag suppressing re-entrant checks) plus the `on_interrupt_poll` hook on
  `_wait` / `_wait_settled`; a codegen labeled `// TODO` per entry; docs (en + ja) with the
  `if` / `interrupts` / `dismissAlerts` / `permissions` comparison and a showcase fixture. Covered
  by `tests/orchestrator/test_interrupts.py`.
- [#1489](https://github.com/bajutsu-e2e/bajutsu/pull/1489) — put `interrupts.yaml` on a real device:
  added it to the Android (adb) lane's `E2E_SCENARIOS` and `E2E_VIEWS_SCENARIOS`, so the check path
  runs against a live Compose *and* Views tree on every `smoke (adb)` run, not just the fast-suite fake.

## References

- [`bajutsu/orchestrator/loop.py:472`](../../bajutsu/orchestrator/loop.py) — `_run_if`, the existing
  single-check conditional this item complements rather than replaces.
- [`bajutsu/scenario/models/steps.py:71`](../../bajutsu/scenario/models/steps.py) — `If`, the
  `condition`/`then`/`else` model whose `condition` shape this item's `interrupts` entries reuse.
- [`bajutsu/config/schema.py:368`](../../bajutsu/config/schema.py) and
  [`bajutsu/scenario/models/scenario.py:104`](../../bajutsu/scenario/models/scenario.py) — the
  existing config-level-default-plus-scenario-level-override shape (`dismiss_alerts`) this item's
  `interrupts` field mirrors.
- [`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py) — `SystemAlertGuard`, the vision-based, reactive
  guard for out-of-process system alerts that `interrupts` deliberately does not replace.
- [BE-0033 — Scenario variables + light control flow](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md) —
  introduced `if` / `forEach` and the assertion-DSL-as-condition pattern this item builds on.
- [BE-0269 — Speed up the system-alert guard's intervention during wait steps](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md) —
  the zero-extra-query, debounced, capped mid-wait intervention shape this item's Units 2–4 adapt
  from a heuristic (collapsed tree) to an explicit `condition`.
- [BE-0276 — Declarative per-scenario permission state (simctl privacy / pm grant)](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) —
  the pre-launch, deterministic complement to `dismissAlerts`; its documented gap (iOS notification
  authorization has no `simctl privacy` path) is the concrete case motivating this item.
- Apple's [`XCTestCase.addUIInterruptionMonitor(withDescription:handler:)`](https://developer.apple.com/documentation/xctest/xctestcase/adduiinterruptionmonitor(withdescription:handler:))
  — external precedent for a continuously-checked, registration-based interruption handler, cited as
  prior art for this shape rather than as a mechanism this item depends on.
- [BE-0310 — Use iOS accessibility screen-change notifications to make the readiness and settle waits more accurate](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md) —
  a related but distinct improvement to *how precisely* Bajutsu detects that a screen transition has
  finished; this item is about recognizing and handling a specific interstitial screen wherever it
  appears, not about the fidelity of the underlying transition signal.

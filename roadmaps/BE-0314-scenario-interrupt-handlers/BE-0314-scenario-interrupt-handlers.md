**English** · [日本語](BE-0314-scenario-interrupt-handlers-ja.md)

# BE-0314 — Deterministic interrupt handlers for unpredictable interstitial screens

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0314](BE-0314-scenario-interrupt-handlers.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0314") |
| Implementing PR | _pending_ |
| Topic | Scenario authoring features |
| Related | [BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md), [BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md), [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md) |
<!-- /BE-METADATA -->

## Introduction

A declarative `interrupts` field — set at the config level (an app-wide default) and extended per
scenario — that names a screen an app can show at an unpredictable point in a run (an onboarding
step, a tutorial overlay, a permission prompt the accessibility tree can see) together with the
steps that clear it. The runner checks each `interrupts` entry's `condition` opportunistically,
using the same deterministic assertion domain-specific language (DSL) that already backs `if`
([BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md)),
against the element tree it has already fetched for the step in progress, wherever in the step
sequence the interstitial happens to appear, so an author no longer has to predict which single
spot in the scenario to check.

## Motivation

Bajutsu already lets a scenario branch on the current screen: `if` ([BE-0033](../BE-0033-scenario-variables-control-flow/BE-0033-scenario-variables-control-flow.md),
`_run_if`, [`bajutsu/orchestrator/loop.py:472`](../../bajutsu/orchestrator/loop.py)) evaluates a
`condition` against a single `driver.query()` and runs `then` or `else`. That single check is a
good fit when the author knows exactly which step precedes the screen in question. It is the wrong
tool when the screen's appearance is not tied to any one step at all — a permission dialog, an
app-specific onboarding screen, or a promotional overlay can each surface a few steps earlier or
later than expected, or not at all, depending on account state, network timing, or an A/B cohort.
Placing a single `if` after a chosen step only catches the screen when it happens to appear exactly
there; every other timing slips through unhandled and the rest of the scenario fails against a
screen it was not written to expect.

The gap is concrete, not hypothetical. [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)
added a deterministic, pre-launch `permissions` field that grants or revokes an OS permission
before the app's first request, but it documents one permission its own mechanism cannot reach:
iOS notification authorization has no `simctl privacy` TCC (Transparency, Consent, and Control)
service, so BE-0276 falls back to `dismissAlerts` — the vision-based alert guard
([`bajutsu/agents/alerts.py`](../../bajutsu/agents/alerts.py)) — as the only path left for that one prompt. That
guard is deliberately the one AI call left in `run` (prime directive 1: AI investigates, it never
judges pass/fail), and it fires only reactively, after a step has already failed or a wait already
looks blocked. A scenario that wants a deterministic, machine-checkable way to handle a screen the
tree can actually see — as opposed to an out-of-process system alert only vision can locate — has
nowhere to put that handling today, precisely because the screen's timing is not tied to a step.

Two existing mechanisms already establish the right shape for a fix, applied to a different layer.
`dismissAlerts` itself is both a config-level default and a scenario-level override
(`dismiss_alerts` in [`bajutsu/config/schema.py:368`](../../bajutsu/config/schema.py) and in
[`bajutsu/scenario/models/scenario.py:104`](../../bajutsu/scenario/models/scenario.py)), so an
app-wide expectation ("this app always shows X") composes with a per-scenario addition. And
[BE-0269](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
already showed that a screen-collapse check can ride on a poll's already-fetched tree at zero extra
query cost, and that a bounded, capped intervention can resume the interrupted wait rather than
abandon it. XCUITest itself independently reaches for the same pattern outside this codebase:
`addUIInterruptionMonitor` registers a handler that the test framework consults whenever a screen
interaction is blocked, regardless of which line of the test triggered it. This proposal brings
that same shape — config/scenario-level registration, checked continuously rather than at one
scripted point — to the tree Bajutsu's own drivers already see, keeping the check itself
deterministic (the assertion DSL) rather than adding a second AI surface.

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

Each entry is a `condition` (the same assertion DSL `if` already uses — `exists`, `value`, and the
rest of the [Assertion DSL](../../docs/scenarios.md#assertion-dsl)) and a `steps` list to run when
the condition matches. `interrupts` at the config level is an app-wide default; a scenario's own
`interrupts` list is appended to it (config entries first), mirroring how `dismissAlerts` already
layers a config-level default under a scenario-level value. An entry's `steps` share the enclosing
scenario's `vars.*` bindings, the same as `if`'s `then`/`else` do today.

### Work breakdown (MECE)

1. **Scenario and config schema.** Add `interrupts: list[Interrupt]` to both `Config`
   ([`bajutsu/config/schema.py`](../../bajutsu/config/schema.py)) and `Scenario`
   ([`bajutsu/scenario/models/scenario.py`](../../bajutsu/scenario/models/scenario.py)), each entry
   validated as `{ condition: Assertion, steps: list[Step] }` (reusing the existing `Assertion` and
   `Step` models `If` at [`bajutsu/scenario/models/steps.py:71`](../../bajutsu/scenario/models/steps.py)
   already validates against). At load time, the effective list for a run is the config's entries
   followed by the scenario's own, the same config-then-scenario precedence `_alert_guard_factory`'s
   `_enabled` helper already applies to `dismiss_alerts`
   ([`bajutsu/cli/commands/run.py:339`](../../bajutsu/cli/commands/run.py)).
2. **Opportunistic check, reusing already-fetched trees.** Thread the effective `interrupts` list
   into `_run_steps` ([`bajutsu/orchestrator/loop.py:513`](../../bajutsu/orchestrator/loop.py)) so
   each entry's `condition` is evaluated against a tree the loop already holds: the `before` /
   `after` query already taken for a `screenChanged`-policy step, or the poll result already fetched
   inside `_wait` ([`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)) on every
   tick. A step whose kind takes no such tree today (a bare `tap`/`type` with no capture policy)
   takes one extra `query()` immediately before the act — the same cost `if` already pays for its
   own single check, paid only for scenarios that declare at least one `interrupts` entry.
3. **On a match, run the entry's `steps`, then resume.** Reuse the same `_ExecSteps` callable `if`
   and `forEach` already close over ([`bajutsu/orchestrator/loop.py:452`](../../bajutsu/orchestrator/loop.py))
   to run the matched entry's `steps`. After it returns, resume the interrupted step exactly where it
   left off: a `wait` keeps polling toward its original `deadline` (the same "resume, don't restart
   the timeout" contract BE-0269 established for the alert guard); an act step re-attempts its single
   action once. A step whose own action changed the entry's `condition` from true back to false
   (the common case — the interrupt's `steps` dismissed the screen) simply proceeds normally on
   resume.
4. **Cap re-entrancy.** An interrupt whose own `steps` do not actually clear its `condition` (a
   broken selector, a screen that re-renders identically) must not turn the run into an infinite
   loop. Cap consecutive fires of the *same* entry within one step's resolution at a small fixed
   number, and fall back to running the original step to its ordinary outcome (pass, fail, or
   timeout) once the cap is hit — mirroring BE-0269's `_GUARD_MAX_ATTEMPTS` /
   `_GUARD_COOLDOWN` shape, so a mis-set condition fails the step cleanly instead of hanging the run.
5. **codegen.** No native XCUITest / Espresso / Playwright construct maps onto "check this condition
   opportunistically throughout the whole test." Emit a labeled `// TODO` naming the field and each
   configured `condition`, consistent with how [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)
   and BE-0276 already handle a field with no app-level equivalent.
6. **Docs and fixture.** Document `interrupts` in [`docs/scenarios.md`](../../docs/scenarios.md) and
   its Japanese mirror, alongside `dismissAlerts` and `permissions`, with a comparison table of when
   to reach for `if` (a known spot), `interrupts` (an unpredictable spot, in-tree), `dismissAlerts`
   (out-of-process, AI-vision), or `permissions` (avoid the prompt outright). Add a showcase fixture
   whose config declares an onboarding-screen interrupt and a scenario that exercises it appearing
   mid-flow.
7. **Tests.** Schema parse/validate for both levels; config-then-scenario ordering; a fake driver
   whose tree flips a condition true partway through a `wait`'s poll loop and partway through a plain
   `tap`; the re-entrancy cap firing and falling back cleanly; `vars.*` sharing between the enclosing
   scenario and an entry's `steps`.

### Prime directives preserved

- **AI never judges.** `condition` is the existing assertion DSL — a machine-checkable predicate,
  never a model call. This item adds no new AI surface; it gives scenarios a deterministic path for
  exactly the cases that would otherwise fall to the vision `dismissAlerts` guard.
- **Determinism first.** No fixed `sleep`: the check rides on ticks the loop or a `wait` already
  performs, and a capped re-entrancy count keeps a mis-set entry from hanging a run instead of
  failing it.
- **App-agnostic.** The `interrupts` list itself is config/scenario data; the runner and drivers gain
  one generic mechanism, not per-app code.

## Alternatives considered

- **Extend `if` with a `timeout` so it polls before branching.** This was the discussion's starting
  point, and it does turn `if` into a wait-then-branch. It still requires the author to place that
  `if` at one chosen point in the step sequence, so it does not solve the actual problem: an
  interstitial whose timing relative to the step sequence cannot be predicted has no single correct
  point to place it. Rejected as the primary shape for that reason; `if` keeps its current, simpler
  single-check semantics for the cases where a chosen spot is genuinely known.
- **A new local `switch`/race step over several candidate conditions, still placed at one point in
  `steps`.** Closer than a bare `if`, and useful when an author does know a specific point where one
  of several screens might appear — but it has the same placement problem as the `if` extension: it
  still asks the author to guess the right point. Deferred; nothing here would prevent adding it
  later as a local complement to the config/scenario-level `interrupts` mechanism this item proposes.
- **Extend `dismissAlerts` to cover in-tree screens too.** Rejected: `dismissAlerts` is deliberately
  the one AI-vision path in `run`, reserved for screens the accessibility tree cannot see at all. An
  in-tree screen already has a deterministic signal (`condition` against `query()`) available, and
  routing it through the vision guard anyway would add cost and non-determinism this item removes
  the need for.
- **A silent per-app allowlist of "expected extra screens" with no explicit recovery steps.**
  Rejected: silently ignoring a matched screen (rather than running an author-specified `steps` list)
  gives up the ability to actually dismiss it, and produces no evidence trail explaining what
  happened when the allowlist masked something the author did not anticipate.

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

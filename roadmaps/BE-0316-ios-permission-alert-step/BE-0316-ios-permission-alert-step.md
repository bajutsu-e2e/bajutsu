**English** · [日本語](BE-0316-ios-permission-alert-step-ja.md)

# BE-0316 — Explicit mid-flow step for iOS permission-prompt alerts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0316](BE-0316-ios-permission-alert-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0316") |
| Implementing PR | [#1324](https://github.com/bajutsu-e2e/bajutsu/pull/1324) |
| Topic | Scenario authoring features |
| Related | [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md), [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds `handleSystemAlert`, a new scenario step that taps an iOS permission-prompt button
deterministically. The step resolves the button by a native accessibility query against the prompt
itself. A vision model never judges a screenshot. The step runs at the exact point in a scenario
where the author expects the prompt to appear.

iOS presents a permission request (location, camera, contacts, and the rest) as a SpringBoard-level
alert. SpringBoard is the iOS home-screen process, and the process also owns system-wide UI chrome,
including these prompts. The alert therefore lives outside the process of the app under test and
outside that app's accessibility tree.

A scenario has two existing ways to handle such a prompt today. Neither fits the case this item
targets: a scenario that triggers the permission request itself and then acts on the prompt,
deterministically, at that specific step. `handleSystemAlert` closes that gap.

## Motivation

Bajutsu already handles a permission prompt in two ways, and each earns its place in a different
situation.

- **The vision alert guard.** The guard (`dismissAlerts`, `bajutsu/agents/alerts.py`) exists for the
  prompt that a scenario cannot see coming. On a blocked step, the guard takes a screenshot, asks
  Claude where to tap, and clears the prompt. The recovery is reactive and best-effort. The recovery
  needs a Claude API key. A vision call is a judgment, not a lookup, so the call is not guaranteed to
  land on the same coordinates from one run to the next.
- **`permissions`.** The field
  ([BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)) exists for
  the permission that a scenario knows about ahead of time. The field grants or revokes the
  permission through `simctl privacy` before the app process starts, so the prompt never appears.

Between the two sits a case that neither handles well. A scenario wants to test the request flow
itself: tap the button that triggers the permission request, then grant or deny the prompt that
follows. The scenario wants the test to be deterministic and to make no AI call.

Pre-launch `permissions` cannot cover this case, because the request fires only once the app makes
it, mid-scenario. The vision guard, `dismissAlerts: { instruction: "tap Allow" }`, is the one tool
left for that moment.

BE-0276 itself pointed at mid-flow permission work as a future direction. The step that BE-0276
deferred is a different mechanism, though. The deferred step is an imperative `setPermissions` step.
The step would set or revoke Transparency, Consent, and Control (TCC) state mid-scenario through
`simctl privacy`, to revoke a permission and re-exercise a denied path. The step never taps a prompt.

`handleSystemAlert` answers the same mid-flow moment from the other side. The step does not set
permission state programmatically. The step taps the visible SpringBoard prompt that the request
raises.

The author already knows which prompt appears and which button the scenario needs at that point.
The reasoning that BE-0276 used to move a known, pre-launch permission off the vision guard
therefore applies again. A known, mid-flow prompt has no reason to route through an AI call. A native
accessibility query resolves the same button deterministically, at less cost, and with a
machine-checkable result. The step either taps one unambiguous button or fails clearly. The step
never guesses a coordinate that could miss a moved button silently (prime directive 1: AI never
decides pass/fail; prime directive 2: determinism first).

## Detailed design

Proposal altitude. The work is MECE along the units below.

A scenario that tests the request-and-grant flow reads like this. The scenario fires the OS
permission request, then taps the SpringBoard prompt's button, deterministically and with no vision
model:

```yaml
- name: grant the notification prompt mid-flow
  steps:
    - tap: { id: perm.requestNotif }                              # fires the OS permission request
    - handleSystemAlert: { sel: { label: "Allow" }, timeout: 5 }  # tap the prompt's button by label
    - wait: { for: { id: perm.notif.authorized }, timeout: 5 }    # request granted, app state updates
```

To dismiss the prompt rather than accept it, the same step targets the dismissive button
(`handleSystemAlert: { sel: { label: "Don't Allow" }, timeout: 5 }`).

- **Scenario schema.** A new step action, `handleSystemAlert: { sel: <Selector>, timeout: <sec> }`,
  follows the `sel:`-wrapped shape that `longPress` and `pinch` already use. The `sel` field accepts
  the label-based `Selector` fields (`label`, `labelMatches`, `index`) and rejects `id`, `idMatches`,
  `traits`, `value`, and `within` at parse time. A SpringBoard alert button carries no app-assigned
  accessibility identifier, trait set, or value. The button carries its visible text, so the rejected
  fields could never match one. The `timeout` field is required, as it is for `wait`. A condition
  wait needs an explicit bound. The shared scenarios' timeout floor applies to the step the same way
  it applies to every other wait in the runner.
- **iOS (XCUITest) runner support.** `BajutsuKit/Runner/Sources/RunnerUITest.swift` already builds an
  `XCUIApplication(bundleIdentifier:)` handle for the app under test. A `handleSystemAlert` step adds
  a second, on-demand handle for `com.apple.springboard`, which is SpringBoard's own bundle
  identifier. The runner queries that handle when this step runs. Every other selector and query path
  stays scoped to the app under test, exactly as today. The step resolves the button by label within
  SpringBoard's alert element. The step taps the button through the same native accessibility tap that
  XCUITest already uses for an in-app element. The step uses no screenshot and no vision model.
- **Fail-fast on zero or multiple matches.** The step follows the rule that an ordinary selector
  already follows ([selectors](../../docs/selectors.md)), applied to the alert's button instead of an
  app element:
  - No alert within `timeout` fails the step with a clear message.
  - More than one button matching the label fails as ambiguous, **unless** `index` selects the nth of
    them.

  The `index` field is a last resort for an alert, as for any element. A label normally resolves a
  prompt button uniquely.
- **Capability token and preflight.** Advertise a capability that names this step, following the
  per-operation preflight pattern
  ([BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md)).
  Only the iOS (XCUITest)
  [backend](../../docs/glossary.md#driver-backend-actuator-platform) declares the capability.

  The Android (adb) backend already dumps whatever window is topmost, including a system permission
  dialog. An ordinary `tap` step therefore reaches the dialog there today. The web (Playwright)
  backend has no OS-level permission prompt at all. A scenario that names `handleSystemAlert` against
  either backend fails preflight before any device work. The failure names the step individually and
  never waits for runtime.
- **codegen.** iOS XCUITest codegen emits the native idiom directly. The generated test waits for
  `XCUIApplication(bundleIdentifier: "com.apple.springboard").buttons["Allow"]` to exist, bounded by
  the step's own `timeout`, then taps the button. A hand-written XCUITest test already clears a
  SpringBoard alert this way. The generated wait carries the step's required `timeout` instead of
  dropping the value.

  Android and web codegen emit a labeled `// TODO`. The precedent is
  [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) and BE-0276,
  for a field that neither backend's native test framework can express. The `permissions` field
  differs: bajutsu itself applies that field before launch on every backend. `handleSystemAlert` is a
  mid-flow step, and only the iOS (XCUITest) framework can express the native idiom for it. This
  difference explains the split.
- **Docs and fixture.** Document the step in [`docs/scenarios.md`](../../docs/scenarios.md), in the
  Japanese mirror of that page, and in the DSL grammar.

  `docs/scenarios.md` already has a "dismissAlerts (the system-alert guard)" section. The
  documentation of the new step must therefore contrast the two by role, so that a reader does not
  conflate them:
  - `dismissAlerts` is the reactive, AI-driven *guard*. The guard fires only when a step is already
    blocked.
  - `handleSystemAlert` is the deterministic, native *step*. The author places the step where the
    prompt is expected.

  Both act on the same kind of OS-level alert through opposite mechanisms. Stating the contrast of
  reactive guard against deterministic step keeps a reader from reaching for the wrong one.

  Add the fixture as its **own** scenario file
  (`demos/showcase/scenarios/permission_system_alert.yaml`). Do not add a new scenario inside the
  existing `permission.yaml`. The notification scenario in `permission.yaml` is tagged `ai`, so the
  iOS smoke lane can `--exclude ai` it. Android's `smoke (adb)` job runs every scenario in that file
  **unchanged**. A new `handleSystemAlert` scenario in the same file would fail Android's required job
  at preflight, because only the iOS (XCUITest) backend declares the capability.

  A separate file sidesteps the failure, and the boundary then splits by mechanism instead of by one
  tag. Android's `smoke (adb)` job runs a **fixed scenario list** from
  `demos/showcase/android/Makefile`. A file that the list never names is never picked up there. The
  file separation, not any tag, keeps the fixture off the Android lane.

  The fixture must also stay out of the local bulk `run-swiftui` and `run-uikit` iOS runs. Those runs
  discover every scenario file and do not reset per-scenario permission state. The runs therefore do
  not guarantee the `notDetermined` prompt that the step needs. Give the fixture a **dedicated**
  exclusion tag (for example `systemalert`) and add the tag to the `--exclude` of those two targets.
  Do not reuse the `xcuitest` tag of `tabs.yaml`.

  The `xcuitest` tag is a legacy carve-out from the time when `[ios]` could resolve to the retired
  idb backend (per the `tabs.yaml` header and `demos/showcase/Makefile`). The tag is also a documented
  removal candidate. Riding the tag would silently un-exclude this fixture on the day the tag is
  retired. A purpose-named tag keeps the fixture's exclusion independent of the legacy split's
  removal.
- **CI wiring.** A tag alone never adds a file to a CI job. Every `ios-e2e.yml` job that runs a
  scenario names its file explicitly. CI has no directory scan and no tag-based inclusion anywhere.
  Add an explicit `scenarios: demos/showcase/scenarios/permission_system_alert.yaml` step to the
  `xcuitest (multi-touch)` job. That job already runs `permission.yaml` through its own explicit step.
  The Simulator, app, and runner build for the new scenario are already paid for there. The new
  scenario costs one more `bajutsu run` instead of a new job.
- **Tests.** The tests cover four areas:
  - Schema parse and validation: accept the label-based fields, and reject `id`, `idMatches`,
    `traits`, `value`, and `within` with a clear message.
  - Preflight over a backend that advertises a subset: iOS passes, and Android and web fail fast,
    each named.
  - The XCUITest runner's SpringBoard resolution against a fake button list that covers zero, one,
    and multiple matches.
  - A codegen snippet.

## Alternatives considered

- **Extend `dismissAlerts` to fire on demand, mid-step.** Rejected. The reactive design of
  `dismissAlerts` fires only once a step is already blocked. A passing scenario never calls the model
  at all ([`docs/scenarios.md`](../../docs/scenarios.md)), and that invariant is part of the value.
  Firing at an author-chosen point would take one of two costs. One cost is to fake a blocked step
  that triggers the guard. The other cost is to break the "never calls the model on a passing
  scenario" guarantee for every scenario that uses the guard. A dedicated step keeps that guarantee
  intact for `dismissAlerts`. The step also gives `handleSystemAlert` its own, simpler contract:
  deterministic, and unconditional at the point where the step appears in `steps`.
- **Add a `system: true` scope to the general `Selector`, so `tap`, `wait`, and `assert` reach
  SpringBoard directly.** Rejected for this item. Every step and assertion resolves through one
  shared `Selector`-resolution path. Threading a SpringBoard-versus-app distinction through all of
  them would serve a need that arises at one specific point in a scenario. The change would widen far
  past what the need justifies. A dedicated `handleSystemAlert` action keeps the change contained to
  one new step implementation. The action puts no risk on the selector-resolution path that every
  other step relies on.
- **Cover every SpringBoard-level alert (a save-password prompt, a paste-permission prompt, a crash
  sheet), not just a permission prompt.** Deferred. A permission prompt is the concrete, common case
  that a scenario author hits when testing a request flow. The underlying SpringBoard query mechanism
  does not distinguish an alert's origin, so nothing here forecloses widening `handleSystemAlert` to
  any SpringBoard alert later. Scoping the first version to permission prompts keeps this proposal
  small enough to review and land as one unit.
- **Cache the vision guard's coordinates from one run and replay them.** Rejected. The approach still
  needs a screenshot-and-model round trip at least once. The approach still needs a Claude API key. A
  cached coordinate is not guaranteed to stay correct, because a different device rotation, locale,
  or iOS version can move the alert's button. A native accessibility query resolves the button's live
  position every time, with no such assumption to invalidate.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Scenario schema — `handleSystemAlert` step, label-based `Selector` subset, required `timeout`.
- [x] iOS (XCUITest) runner support — on-demand SpringBoard `XCUIApplication` handle, native tap.
- [x] Fail-fast on zero or multiple matching alert buttons.
- [x] Capability token + preflight (iOS-only advertisement).
- [x] codegen — native XCUITest idiom on iOS; labeled `// TODO` on Android and web.
- [x] Docs (scenarios.md + ja, DSL grammar) and a new, iOS-only showcase fixture file
      (`permission_system_alert.yaml`), never added to the shared `permission.yaml` that Android's
      `smoke (adb)` job runs unchanged; a dedicated exclusion tag (e.g. `systemalert`) keeps it out
      of the local bulk `run-swiftui`/`run-uikit` runs without riding the legacy `xcuitest` tag.
- [x] CI wiring — an explicit `scenarios:` step for the new fixture in the `xcuitest (multi-touch)`
      job (`ios-e2e.yml`); a tag alone adds nothing to a CI job.
- [x] Tests — schema, preflight, SpringBoard resolution (zero/one/many matches), codegen snippet.

**Design note (resolution stays Python-side).** The runner exposes two endpoints:
`POST /systemAlert/query` snapshots the SpringBoard alert's buttons, each with a handle, and
`POST /systemAlert/tap` taps one button by handle. The Python driver polls the query to the step's
`timeout`, then runs the label-based selector through the shared `resolve_unique`. Resolution
therefore uses the same zero, ambiguous, and index discipline that every selector follows. The fast
Python gate (no Simulator) covers resolution against a fake button list. The `xcuitest (multi-touch)`
CI job exercises the on-device Swift path.

## References

- [BE-0276: Declarative per-scenario permission state](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md).
  The pre-launch, deterministic complement to the reactive guard. BE-0276 deferred an imperative
  mid-flow state-setting step (`simctl privacy`), a different mechanism from this item's prompt
  tapping.
- [BE-0128: Preflight-gate device-control steps](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md).
  The per-operation preflight pattern that this item follows.
- [BE-0026: Shrink unsupported syntax](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md).
  The labeled-`// TODO` codegen precedent for a step that a backend's native framework cannot
  express.
- `bajutsu/agents/alerts.py` and `docs/scenarios.md` (`dismissAlerts` section): the existing reactive
  vision guard that this item complements.
- `BajutsuKit/Runner/Sources/RunnerUITest.swift`: the existing `XCUIApplication(bundleIdentifier:)`
  construction that this item extends with a second, SpringBoard-scoped handle.
- Sibling proposal `rename-dismiss-alerts-to-alert-handling` (`alertHandling`): the proposal to
  rename the reactive `dismissAlerts` guard, which this item stands beside and refers to throughout.
  A reader adopting `dismissAlerts` here should know that the name is being deprecated in favor of
  `alertHandling`. The docs of the two proposals should contrast the reactive guard against the
  explicit step in one place.

**English** · [日本語](BE-XXXX-ios-permission-alert-step-ja.md)

# BE-XXXX — Explicit mid-flow step for iOS permission-prompt alerts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-permission-alert-step.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md), [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md), [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds `permissionAlert`, a new scenario step that taps an iOS permission-prompt button
deterministically — by a native accessibility query against the prompt itself, never a screenshot
judged by a vision model — at the exact point in a scenario where the author expects the prompt to
appear. iOS presents a permission request (location, camera, contacts, and the rest) as a
SpringBoard-level alert: SpringBoard is the iOS home-screen process that also owns system-wide UI
chrome, including these prompts, so the alert lives outside the app under test's own process and
its accessibility tree. A scenario has two existing ways to reach such a prompt today, and neither
fits the case this item targets: a scenario that wants to trigger the permission request itself and
then act on the prompt, deterministically, at that specific step. `permissionAlert` closes that gap.

## Motivation

Bajutsu already handles a permission prompt two ways, and each earns its place for a different
situation. The vision alert guard (`dismissAlerts`, `bajutsu/agents/alerts.py`) exists for the
prompt a scenario cannot see coming: on a blocked step, it takes a screenshot, asks Claude where to
tap, and clears the prompt — a reactive, best-effort recovery that needs a Claude API key and is not
guaranteed to land on the same coordinates from one run to the next, since a vision call is a
judgment, not a lookup. `permissions`
([BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md)) exists for
the permission a scenario knows about ahead of time: it grants or revokes the permission through
`simctl privacy` before the app process starts, so the prompt never appears at all.

Between those two sits a case neither handles well: a scenario that wants to test the request flow
itself — tap the button that triggers the permission request, then grant or deny the prompt that
follows — deterministically, with no AI call. Pre-launch `permissions` cannot cover this case,
because the request only fires once the app makes it, mid-scenario; the only tool left for that
moment is the vision guard, `dismissAlerts: { instruction: "tap Allow" }`. BE-0276 already named
this gap when it proposed `permissions`, calling a mid-flow step "a possible future extension, not
part of this item." `permissionAlert` is that extension. The author already knows which prompt appears
and which button it needs at that point in the scenario, so the same reasoning BE-0276 used to move
a known, pre-launch permission off the vision guard applies again here: a known, mid-flow prompt has
no reason to route through an AI call, when a native accessibility query resolves the same button
deterministically, at less cost, and with a machine-checkable result — a step that either taps one
unambiguous button or fails clearly, never a coordinate guess that can miss a moved button silently
(prime directive 1: AI never decides pass/fail; prime directive 2: determinism first).

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Scenario schema.** A new step action, `permissionAlert: { sel: <Selector>, timeout: <sec> }`,
  following the `sel:`-wrapped shape `longPress` and `pinch` already use. `sel` accepts only the
  label-based `Selector` fields — `label`, `labelMatches`, `index` — and rejects `id`, `idMatches`,
  `traits`, and `within` at parse time: a SpringBoard alert button carries no app-assigned
  accessibility identifier, only its visible text, so those fields could never match one. `timeout`
  is required, exactly as it is for `wait`: a condition wait needs an explicit bound, and the shared
  scenarios' timeout floor applies to it the same way it applies to every other wait in the runner.
- **iOS (XCUITest) runner support.** `BajutsuKit/Runner/Sources/RunnerUITest.swift` already builds
  an `XCUIApplication(bundleIdentifier:)` handle for the app under test; a `permissionAlert` step adds a
  second, on-demand handle for `com.apple.springboard` — SpringBoard's own bundle identifier —
  queried only when this step runs, so every other selector and query path stays scoped to the app
  under test exactly as it is today. The step resolves the button by label within SpringBoard's
  alert element and taps it through the same native accessibility tap XCUITest already uses
  elsewhere for an in-app element — no screenshot, no vision model.
- **Fail-fast on zero or multiple matches.** No alert within `timeout` fails the step with a clear
  message; more than one button matching the label fails as ambiguous — the same rule an ordinary
  selector already follows ([selectors](../../docs/selectors.md)), applied here to the alert's
  button instead of an app element.
- **Capability token + preflight.** Advertise a capability naming this step, following the
  per-operation preflight pattern
  ([BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md)),
  and have only the iOS (XCUITest) backend declare it. The Android (adb) backend already dumps
  whatever window is topmost, including a system permission dialog, so an ordinary `tap` step
  already reaches it there today; the web (Playwright) backend has no OS-level permission prompt at
  all. A scenario naming `permissionAlert` against either backend fails preflight before any device
  work, named individually, never at runtime.
- **codegen.** iOS XCUITest codegen emits the native idiom directly: wait for
  `XCUIApplication(bundleIdentifier: "com.apple.springboard").buttons["Allow"]` to exist, bounded by
  the step's own `timeout`, then tap it — exactly how a hand-written XCUITest test already clears a
  SpringBoard alert, and it carries the step's required `timeout` into the generated wait rather than
  dropping it. Android and web codegen emit a labeled `// TODO`,
  consistent with [BE-0026](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md)
  and BE-0276's precedent for a field neither backend's native test framework can express. Unlike
  `permissions`, where bajutsu itself applies the field before launch on every backend, `permissionAlert`
  is a mid-flow step whose native idiom only the iOS (XCUITest) framework can express — hence the
  split.
- **Docs + fixture.** Document the step in [`docs/scenarios.md`](../../docs/scenarios.md) and its
  Japanese mirror, and the DSL grammar. Add the fixture as its **own** scenario file
  (`demos/showcase/scenarios/permission_alert.yaml`), not a new scenario inside the existing
  `permission.yaml`: `permission.yaml`'s notification scenario is already tagged `ai` so the iOS
  smoke lane can `--exclude ai` it, while Android's `smoke (adb)` job runs every scenario in that
  file **unchanged**, with no exclusion wired at all — a new `permissionAlert` scenario dropped into the
  same file would fail Android's required job at preflight, since only the iOS (XCUITest) backend
  declares the capability. A separate file sidesteps that: the new scenario requests notification
  authorization mid-flow and taps "Allow" through `permissionAlert`, tagged `xcuitest` the same way
  `demos/showcase/scenarios/tabs.yaml` tags an iOS-only scenario, so the local bulk
  `run-swiftui`/`run-uikit` targets' existing `--exclude xcuitest` skips it exactly as it already
  skips `tabs.yaml`.
- **CI wiring.** A tag alone never adds a file to a CI job — every `ios-e2e.yml` job that runs a
  scenario names its file explicitly, with no directory scan and no tag-based inclusion anywhere in
  CI. Add an explicit `scenarios: demos/showcase/scenarios/permission_alert.yaml` step to the
  `xcuitest (multi-touch)` job, the same job that already runs `permission.yaml` through its own
  explicit step — this new scenario's Simulator, app, and runner build are already paid for there, so
  it costs one more `bajutsu run` rather than a new job.
- **Tests.** Schema parse/validate (accept the label-based fields, reject `id`/`idMatches`/
  `traits`/`within` with a clear message); preflight over a subset-advertising backend (iOS passes,
  Android and web named and failed fast); the XCUITest runner's SpringBoard resolution against a
  fake button list covering zero, one, and multiple matches; a codegen snippet.

## Alternatives considered

- **Extend `dismissAlerts` to fire on demand, mid-step.** Rejected. `dismissAlerts`'s reactive
  design fires only once a step is already blocked, and a passing scenario never calls the model at
  all ([`docs/scenarios.md`](../../docs/scenarios.md)) — that invariant is part of its value. Making
  it fire at an author-chosen point would mean either faking a blocked step to trigger it, or
  breaking the "never calls the model on a passing scenario" guarantee for every scenario that uses
  it. A dedicated step keeps that guarantee intact for `dismissAlerts` and gives `permissionAlert` its
  own, simpler contract: deterministic, and unconditional at the point it appears in `steps`.
- **Add a `system: true` scope to the general `Selector`, so `tap` / `wait` / `assert` reach
  SpringBoard directly.** Rejected for this item. Every step and assertion resolves through one
  shared `Selector`-resolution path; threading a SpringBoard-versus-app distinction through all of
  them, for a need that arises at one specific point in a scenario, widens the change's surface far
  past what the need justifies. A dedicated `permissionAlert` action keeps the change contained to one
  new step implementation, with no risk to the selector-resolution path every other step already
  relies on.
- **Cover every SpringBoard-level alert (a save-password prompt, a paste-permission prompt, a crash
  sheet), not only a permission prompt.** Deferred. A permission prompt is the concrete, common case
  a scenario author hits when testing a request flow, and the underlying SpringBoard query mechanism
  does not distinguish an alert's origin, so nothing here forecloses widening `permissionAlert` to any
  SpringBoard alert later. Scoping the first version to permission prompts keeps this proposal small
  enough to review and land as one unit.
- **Cache the vision guard's coordinates from one run and replay them.** Rejected. It still needs a
  screenshot-and-model round trip at least once, still needs a Claude API key, and a cached
  coordinate is not guaranteed to stay correct: a different device rotation, locale, or iOS version
  can move the alert's button. A native accessibility query resolves the button's live position
  every time, with no such assumption to invalidate.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Scenario schema — `permissionAlert` step, label-based `Selector` subset, required `timeout`.
- [ ] iOS (XCUITest) runner support — on-demand SpringBoard `XCUIApplication` handle, native tap.
- [ ] Fail-fast on zero or multiple matching alert buttons.
- [ ] Capability token + preflight (iOS-only advertisement).
- [ ] codegen — native XCUITest idiom on iOS; labeled `// TODO` on Android and web.
- [ ] Docs (scenarios.md + ja, DSL grammar) and a new, iOS-only showcase fixture file
      (`permission_alert.yaml`, tagged `xcuitest`), never added to the shared
      `permission.yaml` Android's `smoke (adb)` job runs unchanged.
- [ ] CI wiring — an explicit `scenarios:` step for the new fixture in the `xcuitest (multi-touch)`
      job (`ios-e2e.yml`); a tag alone adds nothing to a CI job.
- [ ] Tests — schema, preflight, SpringBoard resolution (zero/one/many matches), codegen snippet.

## References

- [BE-0276 — Declarative per-scenario permission state](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) —
  names the mid-flow step this item builds as a future extension, and the pre-launch complement it
  builds alongside.
- [BE-0128 — Preflight-gate device-control steps](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md) —
  the per-operation preflight pattern this item follows.
- [BE-0026 — Shrink unsupported syntax](../BE-0026-shrink-unsupported-syntax/BE-0026-shrink-unsupported-syntax.md) —
  the labeled-`// TODO` codegen precedent for a step a backend's native framework cannot express.
- `bajutsu/agents/alerts.py`, `docs/scenarios.md` (`dismissAlerts` section) — the existing reactive
  vision guard this item complements.
- `BajutsuKit/Runner/Sources/RunnerUITest.swift` — the existing `XCUIApplication(bundleIdentifier:)`
  construction this item extends with a second, SpringBoard-scoped handle.

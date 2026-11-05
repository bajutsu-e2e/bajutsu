**English** · [日本語](BE-0430-ios-cross-app-ui-control-feasibility-ja.md)

# BE-0430 — Launch a named app and drive its UI from a scenario (iOS)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0430](BE-0430-ios-cross-app-ui-control-feasibility.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0430") |
| Implementing PR | [#2021](https://github.com/bajutsu-e2e/bajutsu/pull/2021) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This item lets a [scenario](../../docs/glossary.md#scenario-authoring) launch an app by bundle id that
the test target never started. The scenario then drives that app's own UI, the same way it already
drives the test target. Driving means reading the accessibility tree, tapping, typing, and asserting.
The scenario is written in the YAML (YAML Ain't Markup Language) scenario language.

A new step, sketched below as `app: { bundleId, steps }`, makes this possible. The step follows the
shape that [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) already
established for entering a WebView's DOM context. It switches the driver's query and actuation target
to the named app for the nested `steps`. When the block ends, it switches back to whichever app was
active before the block.

This feature is iOS-only. It rests on the `XCUIApplication(bundleIdentifier:)` capability of the
XCUITest backend. The web and Android backends do not have that capability.

Before this design was written, a throwaway XCTest spike confirmed the two facts it depends on:

- `XCUIApplication(bundleIdentifier:).activate()` reliably foregrounds an app that the test target
  never launched, with no cooperating action in the test target. The spike measured Safari, Maps, and
  Contacts on Simulator. Each reached `.runningForeground` and returned a non-empty accessibility tree.
- The resident runner's design of one long-lived test method survives repeated handoffs between
  unrelated apps. The test process does not go down.

[`docs/specs/ios-cross-app-ui-control-feasibility.md`](../../docs/specs/ios-cross-app-ui-control-feasibility.md)
records both facts and the measurements behind them. This item builds on them and does not restate
them.

## Motivation

The XCUITest backend is built around one handle for the app under test:
`XcuitestElementProvider.app`
([`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:35`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)).
Two exceptions exist today:

- SpringBoard, for a system alert.
- `com.apple.SafariViewService`, the process that draws `SFSafariViewController`
  ([BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)).

Both are lazily built companion handles. Their trees are **merged into the test target's own reply**
once `.state == .runningForeground` says they are up.

That shape fits a companion that draws alongside the test target's own screen, such as a permission
prompt or an in-app browser sheet. It does not fit a scenario that means to drive an unrelated app as
the primary target for a while. Three examples show the gap:

- OAuth handed off to Safari.app itself, not the in-app `SFSafariViewController`.
- A share-sheet target such as Mail.app.
- A Settings.app round trip that flips a permission the test target cannot flip on itself.

None of those scenarios can be written today. A scenario author has no way to name a second app at
all.

The spike closed two questions. The first question was whether `activate()` alone brings an
uncooperative app to the foreground. The second was whether the resident runner's one-test-method
design survives repeated cross-app handoffs. Both held on every app tried. Together they show that
the pattern SpringBoard and SafariViewService use generalizes to an app the test target does not own.

The spike does not answer the remaining design questions alone. Three remain:

- how a scenario names the app
- how the driver's query and actuation target switch to the app and back
- what a scenario author can and cannot do inside that block

These questions are this item's detailed design.

## Detailed design

### The scenario-facing step

```
{ app: { bundleId: string, steps: list(<Step>) } }
```

`bundleId` names the app to drive. It is a plain string, not a value drawn from a fixed set, so the
step stays app-agnostic per prime directive 3. Any installed app can be named, not only an app listed
in config.

`steps` nests the existing `Step` grammar unchanged. Inside the block, `tap`, `type`, `assert`, and
the rest resolve selectors and actuate against the named app's own tree. Outside the block, the same
steps act on the test target.

On entry, the driver activates the named app. It then waits, within a bound, for
`.state == .runningForeground`. The return of `.activate()` is not by itself the readiness signal:
the spike's own measurements needed the same poll before the first read. A bundle id that never
reaches the foreground within the bound fails the step by name (`ElementNotFound`). The driver never
reads an empty or not-yet-foreground tree.

That guarantee holds for an *installed* app that is slow to foreground, for example on a cold launch
or behind a permission prompt. It does not hold for a bundle id that is not installed at all. On real
hardware, such a bundle id left the runner unresponsive instead of reaching the bounded poll, while a
different app was genuinely foreground (see this item's own Log). The step requires `bundleId` to name an
already-installed app. The rule is not a mere recommendation.

On exit, the driver activates back to whichever app was active before the block. Exit happens when
the block's last step completes or when a step inside it fails. This mirrors the enter and leave
contract of [`web: { within, steps }`](../../docs/dsl-grammar.md) (BE-0037) rather than inventing a
second contract.

Nesting an `app:` block inside another follows the same stack discipline that the spike exercised by
hand (Safari, then Maps, then back to Safari). Each block's exit returns to its immediate parent, not
to the test target unconditionally. A scenario can therefore visit a second app from inside the first
without losing its way back.

"Unchanged" does not mean that every step composes safely with `app:`. This item deliberately adds no
new guarding for either of the two gaps below.

- **Device-lifecycle steps.** `relaunch`, `foreground`, `background`, `clearKeychain`, and
  `overrideStatusBar` act on the test target through its own simctl-bound bundle id. They never go
  through the `app:` stack. A step of this kind inside an `app:` block affects the test
  target, whichever app is on top. A scenario author who wants one of these mid-block should
  expect it to reach the test target, not the app that `app:` last entered.
- **Nested `web:` blocks.** The two directions behave differently:
  - `app:` inside `web:` fails cleanly. `WebContextDriver` does not implement `enter_app` and
    `leave_app`, so it raises `UnsupportedAction`, the same way it rejects every action outside its
    narrowed surface.
  - `web:` inside `app:` does not fail. It opens a `WebContextDriver` bound to the test target's own
    `BAJUTSU_WEBVIEW_PORT` bridge, whichever app is on the `app:` stack. Preflight does not catch this
    mistake today. The gap pre-dates this item and is acknowledged: the capability walk that this item's own
    preflight requirement uses recurses into neither `web:` nor `app:` steps.

### Unit 1 — Feasibility spike (complete)

The spike has already run. The Introduction and
[`docs/specs/ios-cross-app-ui-control-feasibility.md`](../../docs/specs/ios-cross-app-ui-control-feasibility.md)
give the design and the measurements.

Nothing from the spike ships. Its throwaway file was deleted. `BajutsuRunner.xcodeproj` is an
XcodeGen-generated, gitignored artifact
([`BajutsuKit/Runner/project.yml:36-37`](../../BajutsuKit/Runner/project.yml)). `xcodegen generate`
regenerated it back to its pre-spike state. The durable output of this unit is the answer on which the
rest of this item's design rests.

### Unit 2 — A query/actuation target stack in `XcuitestElementProvider`

`XcuitestElementProvider.app` is a single fixed property. It becomes the top of a stack of
`XCUIApplication` handles, seeded with the test target's own handle. `query()`, `tap`, and every other
actuation resolve against the top of the stack, unchanged in every other respect.

Entering an `app:` block pushes a new `XCUIApplication(bundleIdentifier:)` handle and calls
`.activate()`. Leaving the block pops the handle and calls `.activate()` on the handle beneath.

The existing SpringBoard alert-button and notification-banner reads stay untouched. Both address a
separate `springboard` handle directly, never through `app`. A system alert can still
interrupt a scenario, whichever app an `app:` block targets.

The SafariViewService merge inside `queryElements()` is different. It already reads `app.snapshot()`,
the same `app` that this unit redefines as the current top of the stack. Once `app:` shifts that top,
the merge follows it naturally: a foreign app's own `SFSafariViewController` presentation attaches to
that app's tree, not to the test target's. This falls out of the stack change itself, so it needs no
separate code path.

Resolved during implementation: a real-device check confirmed `enterApp` and `leaveApp` against real
Safari. The check was a temporary, since-removed spike, under the same discipline as Unit 1.
`queryElements()` returned Safari's own tree while Safari was entered: 146 elements, including its
`TabBarItemTitle` address field. After leaving, it returned the host app's own tree with an unchanged
element count.

`tap`, `type`, and gesture actuation *inside* a foreign app's tree are exercised only through
`FakeDriver` in the fast suite. They are proven at the dispatch and nesting level, not against a real
foreign app's live hit-testing. Proving each gesture for real needs a target app with a known, stable
control to act on, and Safari's own onboarding surface does not reliably offer one. The item leaves
this as a real, open gap for whichever concrete scenario first needs a gesture inside an `app:` block,
rather than assuming it away.

### Unit 3 — Capability gating

`app:` is XCUITest-only, so it needs its own preflight token: a new per-operation capability alongside
`handleSystemAlert` and `setPickerValue`
([docs/architecture.md](../../docs/architecture.md#implementation-status)). BE-0212 split the old
coarse `deviceControl` family into per-operation tokens rather than an all-or-nothing gate, and this
token follows that split.

A scenario that uses `app:` against the web or Android backend fails preflight by name, before any
device work. `setPickerValue` behaves the same way today.

### Unit 4 — Scenario model and driver interface

The Python scenario model (`bajutsu/common/scenario/models/`) gains the `App` step type (`bundleId:
str`, `steps: list[Step]`). Validation matches the `within` and `steps` pair of `Web`.

The `Driver` interface gains an enter and leave pair. The XCUITest backend implements the pair against
the stack of unit 2. Every other backend simply raises "unsupported". The preflight
token of unit 3 already short-circuits that path before a scenario reaches it. The pair is a
compile-time surface, not a new runtime branch for those backends to maintain.

The step runner reuses `active_driver` unchanged for the block. It does not construct a second driver
instance the way `web:` does. `app:` needs the full native actuation surface (tap, type, gestures,
picker wheels), and reusing the same object provides it for free.

One piece of shared run-loop state does need resetting around the block, on both sides: `prev_after`,
the previous step's screenshot-reuse cache. The block reads a different app's tree. A native step
right after the block must not reuse a screenshot that the block's own last step took. `_handle_web`
already resets the cache around its own context switch for the same reason (BE-0234 Unit 2).

### Unit 5 — Showcase fixture and scenario

A showcase scenario exercises the step end to end against a real system app:

1. From the showcase app, an `app:` block activates Safari, and the scenario asserts an element inside
   Safari.
2. After the block ends, the scenario asserts that a showcase element is readable again.

This is the same round trip that the spike measured by hand, now driven through the scenario DSL.

The scenario is *not* the same fixture-app pattern as
[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)'s
`scenarios/browser.yaml`. `browser.yaml` asserts against a page that this repository serves itself,
so it is hermetic by construction. A scenario that asserts inside Safari.app depends on Apple's own
UI. That UI changes across iOS releases and carries onboarding and network state that the repository
does not control. This is exactly the kind of dependency that prime directive 2 exists to keep out of
a regression gate.

The scenario is deliberately **not** wired into the automated matrix of `ios-e2e.yml`. It
runs through its own opt-in `make -C demos/showcase e2e-cross-app` lane. `e2e-browser` is likewise its
own lane rather than folded into the bulk `run-swiftui` and `run-uikit` targets. Both bulk lanes
exclude the scenario's tag for the same reason.

### Unit 6 — Documentation

`docs/dsl-grammar.md` (the `Action` grammar), `docs/drivers.md` (the XCUITest section), and
`docs/scenarios.md` (a cookbook entry) gain the new step, in both languages.

### Prime-directive compliance

- **AI out of the gate.** The step is ordinary deterministic actuation: activate an app, resolve a
  selector, act. No model sits on the decision path.
- **Determinism first.** Selector resolution inside an `app:` block follows the same fail-fast rule on
  ambiguity as everywhere else. The item introduces no new waiting primitive, because `.state` polling
  is a condition wait like any other, not a fixed sleep.
- **App-agnostic.** `bundleId` is a plain scenario-level string, not a config value naming one partner
  app. The tool stays unaware of which specific apps a given target chooses to drive.

## Alternatives considered

- **Merge a named app's tree into the test target's reply, the way SpringBoard and SafariViewService
  are handled today.** This fits a companion drawn *alongside* the test target's own screen. It does
  not fit a scenario that means to drive an unrelated app as the primary target for a while. Once two
  trees are merged into one reply, a scenario author also has no way to say which tree a given
  selector should resolve against. The block-scoped `app:` step avoids that problem by construction,
  because only one tree is ever the resolution target at a time.
- **Give the test target (showcase) a button that opens Safari.app, and drive the handoff from there,
  instead of naming the app directly in the step.** The step needs to work for any named bundle id.
  That includes a bundle id the test target itself never triggers a transition to, such as a partner
  app reached only by a deep link outside this repository's control. Tying entry to an in-app trigger
  would make the feature only as general as the triggers the scenario authors happen to have on hand.
- **Probe through `bajutsu repl` (BE-0423's interactive shell) instead of a scenario step.** The REPL
  is an authoring aid for inspecting one app's tree interactively. It is not a vehicle for expressing
  a reusable, checked-in scenario. The REPL does not substitute for a step that scenario files can
  carry, that CI can run, and that reruns deterministically.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — feasibility spike (Safari/Maps/Contacts, cross-app `activate()` and resident-runner
  survival confirmed; see `docs/specs/ios-cross-app-ui-control-feasibility.md`)
- [x] Unit 2 — query/actuation target stack in `XcuitestElementProvider`
- [x] Unit 3 — `app:`'s preflight capability token
- [x] Unit 4 — scenario model (`App` step type) and `Driver` interface enter/leave pair
- [x] Unit 5 — showcase fixture and scenario exercising `app:` end to end
- [x] Unit 6 — documentation (`dsl-grammar.md`, `drivers.md`, `scenarios.md`, both languages)

Log:

- 2026-09-18 — Unit 1 landed as a throwaway spike, not shipped. `CrossAppFeasibilitySpike.swift`
  activated `com.apple.mobilesafari`, `com.apple.Maps`, and `com.apple.MobileAddressBook` in
  sequence on a dedicated iPhone 17 Pro Simulator (iOS 26.5, Xcode 26.6): all three reached
  `.runningForeground` with a non-empty tree (136/50/127 elements respectively), and activating back
  to a previous app also returned a non-empty tree (134/50 elements) with the resident test process
  never going down across the sequence (11.288s total, 0 failures). Findings recorded in
  `docs/specs/ios-cross-app-ui-control-feasibility.md`; the spike file and its
  `project.pbxproj` wiring were removed afterward.
- 2026-09-18 — Units 2–6 implemented and verified. Swift: `XcuitestElementProvider.appStack` plus
  `enterApp`/`leaveApp` (bounded `.runningForeground` poll, matching the spike's own bound), two new
  resident-runner routes (`POST /app/enter`, `POST /app/leave`) on the generated OpenAPI surface,
  with a host-only `APIHandler` test (no legacy `Router` twin — `app:` is new to the generated path
  only). Python: `Driver.enter_app`/`leave_app` implemented for real by `XcuitestDriver` and as
  `UnsupportedAction` on every other backend; `Capability.APP_CONTEXT`, declared by `XcuitestDriver`
  and `FakeDriver`; the `App` scenario model; `_handle_app` in the step runner, reusing
  `active_driver` unchanged (not a new driver instance, unlike `web:`'s `WebContextDriver`) with
  `leave_app()` in a `finally` so a failing nested step still restores the foreground app. A
  fast-suite regression test drives the exact nested sequence the spike measured by hand (enter A,
  enter B, leave, leave) and confirms it restores the *immediate* parent, not the test target
  unconditionally. A second real-device check (temporary, since removed) confirmed the Swift stack
  against live Safari: `queryElements()` returned Safari's own 146-element tree while entered, and
  the host app's own tree, unchanged, once left. `make check` green throughout
  (8053 passed, coverage unaffected).
- 2026-09-18 — Units 5 and 6 (originally sketched together) landed as: a showcase scenario
  (`demos/showcase/scenarios/app.yaml`) driving real Safari.app end to end, using
  `TabBarItemTitle` — Safari's own address-field identifier, read off the same real-device check
  above — and a new `make -C demos/showcase e2e-cross-app` lane (no fixture server needed, unlike
  `e2e-browser`: Safari serves its own start page). Run for real against a dedicated Simulator on
  both the SwiftUI and UIKit showcase targets: both passed (`manifest.json` `"ok": true` for each;
  the `app` step itself completed in ~6s). Documentation added in both languages: `dsl-grammar.md`
  (the `App` production and its reference-graph edges), `drivers.md` (the `enter_app`/`leave_app`
  bullet), `scenarios.md` (the `app` cookbook entry), and `architecture.md` (a new "DSL cross-app
  control" subsection under Implementation status).
- 2026-09-18 — A self-review pass against `.github/claude-review-prompt.md` (`propose-and-build`
  Phase B) found seven issues, all fixed. A `BE-0430` placeholder had leaked into roughly 40
  non-roadmap files (code comments, docstrings, tests, docs) in violation of the id-confinement
  invariant; removed everywhere outside this item's own directory, with the two dead roadmap links
  in `scenarios.md` replaced by a reference to the durable spec doc. `demos/showcase/Makefile`'s
  three bulk lanes (`run-swiftui`, `run-uikit`, `run-flutter`) were missing `app` from their
  `--exclude` lists despite `app.yaml`'s own comment claiming the exclusion; added. `AppActivationTests.swift`
  used `XCTSkip` on an unexpected output shape, which would hide a real regression as a skip rather
  than a failure; changed to `XCTFail`. The new `APP_CONTEXT` preflight requirement had no dedicated
  test; three added, mirroring `handleSystemAlert`'s. `_handle_app`'s `finally: leave_app()` could
  mask the exception already propagating out of the block — `RunCancelled` included — with whatever
  `leave_app()` itself raised; now logs the leave failure and re-raises the original, mirroring
  `capability_suspended`'s BE-0365 precedent, with a new regression test proving a cancellation
  still wins over a simultaneous leave failure. `XcuitestElementProvider.enterApp`'s `.notForeground`
  path did not re-activate the app already on the stack, leaving the device's foreground state
  unspecified after a failed activation; now re-activates it, matching `leaveApp`'s own restore
  guarantee. The Japanese `architecture.md` bullet lacked the English side's own "DSL cross-app
  control" heading; added. Re-verified afterward: `swift test` (229 passed), `make check` (8065
  passed, green), and `make -C demos/showcase e2e-cross-app` (both the SwiftUI and UIKit targets
  passed) on a fresh dedicated Simulator.
- 2026-09-18 — CI's on-device `conformance (xcuitest)` job crashed the resident runner on
  `test_app_context_capability_matches_behavior`, and a neighboring job on the same runner
  reported "CoreSimulator may be wedged" shortly after. Reproduced twice, standalone, against a
  dedicated Simulator: `enter_app` with a fabricated bundle id (as the test used) left `POST
  /app/enter` unanswered — the runner never responded at all, not even with a `not-foreground`
  reply — while the showcase app was genuinely foreground. `XCUIApplication.activate()` for a
  bundle id that is not installed, called while a different app is legitimately running, does not
  reliably return on this Xcode/Simulator combination; catching an `NSException` around the call
  (confirmed harmless in isolation, no prior foreground app) does not help a call that never
  returns at all. `enterApp`'s bounded `.runningForeground` poll only starts once `.activate()`
  itself returns, so it cannot bound this. Fixed by changing the shared on-device conformance test
  to exercise `enter_app`/`leave_app` against `com.apple.mobilesafari` — a bundle id every iOS
  Simulator carries — instead of a fabricated one; the "bad bundle id" failure semantics stay
  covered by the existing fast-suite test against `XcuitestDriver`'s fake transport, which never
  touches real XCUITest. Corrected every "wrong or uninstalled bundle id" claim this item's own
  code, docs, and tests had made about `not-foreground` to instead read "installed but slow to
  foreground", and documented in `enter_app`'s own contract and the `app` cookbook entry that
  `bundleId` must name an already-installed app — an uninstalled one is not guaranteed to fail
  cleanly. Re-verified: the corrected on-device test passes against a fresh dedicated Simulator
  (`enter_app`/`leave_app` against Safari, round-tripping back to the showcase app), and `make
  check` stays green.
- 2026-09-18 — Strengthened `demos/showcase/scenarios/app.yaml` to drive the showcase app for
  real between two separate handoffs to Safari, rather than only asserting its tab bar before and
  after a single one: the scenario now pushes a catalog row's detail screen, hands off to Safari
  and back, asserts the pushed screen is still there, pops it, switches to the Permissions tab,
  then hands off to Safari a second time and asserts the Permissions tab is still the one showing.
  An earlier version typed into the Search tab's text field between the two handoffs instead of
  pushing a detail screen; dropped after a real run showed the on-screen keyboard it raised covers
  the tab bar, and neither `submit: true` nor this app's own `SearchView` resigns it, so the
  follow-up tab switch could not resolve a hittable target. Re-verified: both showcase-swiftui and
  showcase-uikit pass against a fresh dedicated Simulator.

## References

- [`docs/specs/ios-cross-app-ui-control-feasibility.md`](../../docs/specs/ios-cross-app-ui-control-feasibility.md) —
  the feasibility spike's design and measured results (Unit 1).
- `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift` — the single-test-target handle Unit 2
  turns into a stack, and the existing SpringBoard/SafariViewService companion handles Unit 2 leaves
  untouched.
- `BajutsuKit/Runner/Sources/RunnerUITest.swift` — the resident runner's one-long-lived-test-method
  design the spike exercised across repeated foreground handoffs.
- [BE-0037 — WebView / hybrid support](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) —
  the `web: { within, steps }` enter/leave contract this item's `app:` step follows.
- [BE-0212 — Split the coarse deviceControl capability into per-operation tokens](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md) —
  the precedent Unit 3's new preflight token follows.
- [BE-0396 — Read SFSafariViewController's element tree from the process that draws it](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md) —
  the companion-handle pattern this item's Motivation explains the limits of, and the reason Unit
  5's own showcase scenario stays out of `ios-e2e.yml`'s automated matrix (see Unit 5).
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the
  resident runner that owns `XcuitestElementProvider`.
- [BE-0423 — Interactive REPL for element-tree inspection and id-based actuation](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md) —
  the alternative considered and set aside above.

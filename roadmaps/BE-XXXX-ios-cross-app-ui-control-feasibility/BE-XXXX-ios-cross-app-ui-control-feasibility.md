**English** · [日本語](BE-XXXX-ios-cross-app-ui-control-feasibility-ja.md)

# BE-XXXX — Launch a named app and drive its UI from a scenario (iOS)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-cross-app-ui-control-feasibility.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2021](https://github.com/bajutsu-e2e/bajutsu/pull/2021) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This item lets a scenario, written in the YAML scenario language, launch an app by bundle id that
the test target never started, and drive that app's own UI — read its accessibility tree, tap and
type into it, assert against it — the same way a scenario already drives the test target. A new
step, sketched below as `app: { bundleId, steps }` after the shape [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md)
already established for entering a WebView's DOM context, switches the driver's query and actuation
target to the named app for its nested `steps`, then switches back to the test target once the
block ends. This is iOS-only: it rests on the XCUITest backend's `XCUIApplication(bundleIdentifier:)`
capability, which the web and Android backends do not have.

Before writing this design, a throwaway XCTest spike confirmed the two facts the design depends on.
`XCUIApplication(bundleIdentifier:).activate()` reliably foregrounds an app the test target never
launched, with no cooperating action in the test target — measured against Safari, Maps, and
Contacts on Simulator, each reaching `.runningForeground` and returning a non-empty accessibility
tree. And the resident runner's one-long-lived-test-method design survives repeated handoffs between
unrelated apps without the test process going down. Both facts, and the measurements behind them,
are recorded in
[`docs/specs/ios-cross-app-ui-control-feasibility.md`](../../docs/specs/ios-cross-app-ui-control-feasibility.md);
this item does not restate them, only builds on them.

## Motivation

The XCUITest backend is built around one handle for the app under test
(`XcuitestElementProvider.app`,
[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift:35`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)).
Two exceptions exist today: SpringBoard (for a system alert) and `com.apple.SafariViewService` (for
the process that draws `SFSafariViewController`,
[BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)).
Both are lazily built companion handles whose trees are **merged into the test target's own reply**
once `.state == .runningForeground` says they are up. That shape fits a companion that draws
alongside the test target's own screen — a permission prompt, an in-app browser sheet — but it does
not fit a scenario that means to drive an unrelated app as the primary target for a while: OAuth
handed off to Safari.app itself (not the in-app `SFSafariViewController`), a share-sheet target like
Mail.app, or a Settings.app round trip to flip a permission the test target cannot flip on itself.
None of those scenarios can be written today; a scenario author has no way to name a second app at
all.

The spike closed the two questions that stood between "the pattern SpringBoard/SafariViewService use
already exists" and "this generalizes to an app the test target does not own": whether `activate()`
alone brings an uncooperative app to the foreground, and whether the resident runner's one-test-
method design survives repeated cross-app handoffs. Both held on every app tried. What is left is a
design question the spike does not answer on its own: how a scenario names the app, how the driver's
query/actuation target switches to it and back, and what a scenario author can and cannot do inside
that block. That is this item's detailed design.

## Detailed design

### The scenario-facing step

```
{ app: { bundleId: string, steps: list(<Step>) } }
```

`bundleId` names the app to drive — a plain string, not a value drawn from a fixed set, so the step
stays app-agnostic per prime directive 3 (any installed app, not a config-listed one, can be named).
`steps` nests the existing `Step` grammar unchanged: `tap`, `type`, `assert`, and the rest resolve
selectors and actuate against the named app's own tree while inside the block, exactly as they do
against the test target outside it. On entry, the driver activates the named app; on exit — the
block's last step completing, or a step inside it failing — it activates back to whichever app was
active before the block, mirroring [`web: { within, steps }`](../../docs/dsl-grammar.md)'s own
enter/leave contract (BE-0037) rather than inventing a second one. Nesting an `app:` block inside
another follows the same stack discipline the spike exercised by hand (Safari → Maps → back to
Safari): each block's exit returns to its immediate parent, not to the test target unconditionally,
so a scenario can visit a second app from inside the first without losing its way back.

### Unit 1 — Feasibility spike (complete)

Already run; see the Introduction and
[`docs/specs/ios-cross-app-ui-control-feasibility.md`](../../docs/specs/ios-cross-app-ui-control-feasibility.md)
for the design and the measurements. Nothing from the spike ships — its throwaway file was deleted
and `BajutsuRunner.xcodeproj` (an XcodeGen-generated, gitignored artifact,
[`BajutsuKit/Runner/project.yml:36-37`](../../BajutsuKit/Runner/project.yml)) was regenerated back to
its pre-spike state with `xcodegen generate`. This unit's only durable output is the answer the rest
of this item's design rests on.

### Unit 2 — A query/actuation target stack in `XcuitestElementProvider`

`XcuitestElementProvider.app` is a single fixed property today. It becomes the top of a stack of
`XCUIApplication` handles, seeded with the test target's own handle. `query()`, `tap`, and every
other actuation resolve against the stack's top, unchanged in every other respect (the existing
SpringBoard/SafariViewService merge logic is untouched — it still reads relative to the test
target's own entry, not whatever the current top is, since a system alert can interrupt a scenario
regardless of which app an `app:` block currently targets). Entering an `app:` block pushes a new
`XCUIApplication(bundleIdentifier:)` handle and calls `.activate()`; leaving it pops and
`.activate()`s the handle beneath.

Resolved during implementation: a real-device check (a temporary, since-removed spike, same
discipline as Unit 1) confirmed `enterApp`/`leaveApp` against real Safari — `queryElements()`
returned Safari's own tree (146 elements, including its `TabBarItemTitle` address field) while
entered, and the host app's own tree (unchanged element count) once left. `tap`/`type`/gesture
actuation *inside* a foreign app's tree is exercised only through `FakeDriver` in the fast suite —
proven at the dispatch/nesting level, not against a real foreign app's live hit-testing — since
proving each gesture for real needs a target app with a known, stable control to act on, which
Safari's own onboarding surface does not reliably offer. Left as a real, open gap for whichever
concrete scenario first needs a gesture inside an `app:` block, rather than assumed away.

### Unit 3 — Capability gating

`app:` is XCUITest-only, so it needs its own preflight token — a new per-operation capability
alongside `handleSystemAlert` and `setPickerValue`
([docs/architecture.md](../../docs/architecture.md#implementation-status)), following BE-0212's
split of the old coarse `deviceControl` family into per-operation tokens rather than an all-or-
nothing gate. A scenario using `app:` against the web or Android backend fails preflight by name,
before any device work, the same way `setPickerValue` does today.

### Unit 4 — Scenario model and driver interface

The Python scenario model (`bajutsu/common/scenario/models/`) gains the `App` step type
(`bundleId: str`, `steps: list[Step]`), validated the same way `Web`'s `within`/`steps` pair is. The
`Driver` interface gains the enter/leave pair the XCUITest backend implements against unit 2's stack;
every other backend's implementation simply raises "unsupported" — a path unit 3's preflight token
already short-circuits before a scenario reaches it — so this is a compile-time surface, not a new
runtime branch for those backends to maintain.

### Unit 5 — Showcase fixture and scenario

A showcase scenario exercises the step end to end against a real system app — the same fixture-app
pattern [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)'s
`scenarios/browser.yaml` uses. Candidate flow: from the showcase app, assert an element inside
Safari (or another system app) after an `app:` block activates it, then assert a showcase element is
readable again once the block ends — the same round trip the spike measured by hand, now driven
through the scenario DSL and part of the repository's own CI-covered regression net rather than a
one-off manual run.

### Unit 6 — Documentation

`docs/dsl-grammar.md` (the `Action` grammar), `docs/drivers.md` (the XCUITest section), and
`docs/scenarios.md` (a cookbook entry) gain the new step, in both languages.

### Prime-directive compliance

- **AI out of the gate.** The step is ordinary deterministic actuation — activate an app, resolve a
  selector, act — with no model on the decision path.
- **Determinism first.** Selector resolution inside an `app:` block follows the same fail-fast-on-
  ambiguity rule as everywhere else; no new waiting primitive is introduced, since `.state` polling
  is a condition wait like any other, not a fixed sleep.
- **App-agnostic.** `bundleId` is a plain scenario-level string, not a config value naming one
  partner app, so the tool stays unaware of which specific apps any given target chooses to drive.

## Alternatives considered

- **Merge a named app's tree into the test target's reply, the way SpringBoard and SafariViewService
  are handled today.** Fits a companion drawn *alongside* the test target's own screen, not a
  scenario that means to drive an unrelated app as the primary target for a while. A scenario author
  would also have no way to say which tree a given selector should resolve against once two trees
  are merged into one reply, which the block-scoped `app:` step avoids by construction — only one
  tree is ever the resolution target at a time.
- **Give the test target (showcase) a button that opens Safari.app, and drive the handoff from
  there, instead of naming the app directly in the step.** The step needs to work for any named
  bundle id, including ones the test target itself never triggers a transition to (a partner app
  reached only by a deep link outside this repository's control, for instance); tying entry to an
  in-app trigger would make the feature only as general as whatever triggers scenario authors happen
  to have on hand.
- **Probe through `bajutsu repl` (BE-0423's interactive shell) instead of a scenario step.** The REPL
  is an authoring aid for inspecting one app's tree interactively, not a vehicle for expressing a
  reusable, checked-in scenario; the two are complementary; the REPL does not substitute for a
  step scenario files can carry, run in CI, and rerun deterministically.

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
  Phase B) found seven issues, all fixed. A `BE-XXXX` placeholder had leaked into roughly 40
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
  the companion-handle pattern this item's Motivation explains the limits of, and the showcase
  fixture pattern Unit 5 follows.
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the
  resident runner that owns `XcuitestElementProvider`.
- [BE-0423 — Interactive REPL for element-tree inspection and id-based actuation](../BE-0423-cli-repl-inspect-actuate/BE-0423-cli-repl-inspect-actuate.md) —
  the alternative considered and set aside above.

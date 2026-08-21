**English** · [日本語](BE-XXXX-ios-native-tipkit-guard-ja.md)

# BE-XXXX — iOS native TipKit tip guard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ios-native-tipkit-guard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md), [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md) |
<!-- /BE-METADATA -->

## Introduction

Apple's TipKit (iOS 17+) renders a popover-style tip anchored to a UI element to guide a user
through a feature, and it does so on a schedule the app itself does not fully control — first-launch
state, an eligibility rule, or an event trigger, not a fixed step in the flow. When the anchor
happens to be the same control a scenario needs to tap, the tip's own view sits in front of it —
and, confirmed on a real Simulator, TipKit's presentation hides the covered content from the
accessibility tree entirely rather than merely occluding it, so the blocked tap can fail as
`ElementNotFound`, not only `ElementNotTappable`. This proposal adds a deterministic, opt-in guard
on the iOS XCUITest backend that recognizes a blocking TipKit tip and dismisses it — no screenshot and
no model call, and, because the tip already surfaces inside the app's own accessibility tree, no Swift
runner change either. The contribution is recognizing this specific case — a same-process,
framework-owned overlay whose structure no app can customize — and clearing it without asking every
app that uses TipKit to hand-author the same recovery.

## Motivation

[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) already lets
a scenario declare `interrupts`: a `condition` checked opportunistically against the tree a wait has
already fetched, paired with recovery `steps`, for a screen that can appear at an unpredictable point
— an onboarding step, a tutorial overlay, a permission prompt the accessibility tree can see. A
TipKit tip has exactly that unpredictable-timing shape, so on its face `interrupts` looks like the
right tool.

It is not, for one concrete reason: `interrupts`' `condition` is an assertion an author writes against
the app's own accessibility tree, and that requires a selector the author can name. A custom
onboarding screen is the app team's own view, so it carries whatever identifier the team chooses to
give it — the case `interrupts` was built for. A TipKit tip is not the app's view. Its title and
message differ per `Tip` instance across an app, and the built-in close control TipKit adds by
default carries no accessibility identifier the app sets — the app only supplies the tip's content,
not its container or dismiss control. An author has no stable, app-authored selector to put in a
`condition` at all, so `interrupts` cannot express "dismiss whichever TipKit tip is showing" without
the app team first reverse-engineering TipKit's internal view structure well enough to select it — and
redoing that for every app that adopts TipKit, the same gap
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) hit for the
notification prompt — a surface no per-app config can reach.

[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md) already
keeps a blocked tap from silently landing on the wrong element: `isHittable` reads false when a tip
covers the target, so the driver raises `ElementNotTappable` instead of mistapping the tip. That is
the correct failure to raise when the obstruction is unexpected — but when the obstruction is a known,
recoverable TipKit tip, failing outright wastes the run. `ElementNotTappable`'s own recovery, a bounded
scroll, does not apply here: scrolling does not dismiss a popover anchored to an on-screen control.

The pattern to reuse is BE-0315/BE-0316's, not BE-0314's: a TipKit tip is owned by an OS framework,
so its structure is the same across every app that adopts it — the property that let BE-0315 give the
XCUITest backend a built-in, deterministic guard behind a capability token instead of asking each app
to configure one. Web already has the analogous case, in miniature: Playwright's native
`page.on('dialog')` (`bajutsu/drivers/playwright.py`) auto-dismisses a browser-owned JavaScript
`alert`/`confirm`/`prompt`, because the browser engine, not the page, owns that overlay — and it
lives in the *driver*, which is where knowledge of one backend's overlays belongs. This proposal
keeps that layering: the TipKit-specific identifier stays inside the XCUITest driver, and the
orchestrator sees only a backend-agnostic "was a blocking tip dismissed?" call.

What an on-device spike (unit 1 below) does change is how much machinery that driver method needs.
BE-0315's SpringBoard alert lives in a second, out-of-process `XCUIApplication`, so reaching it
needed new Swift runner routes on top of the capability token. A TipKit tip is not out-of-process:
it renders inside the app's own view hierarchy and already surfaces in the very `elements` snapshot
every wait poll and every tap resolution fetches. So the driver can implement the whole guard in
Python, on the query and tap primitives it already has — **no Swift or BajutsuKit change at all**,
which is what makes this a small item rather than a runner-protocol change.

## Detailed design

The work divides into a feasibility spike (confirmed on-device before this proposal was written up),
the driver-side dismiss method behind a capability token, the tap-time recovery hook, the wait-loop
gate, opt-in configuration, and on-device verification.

1. **On-device feasibility spike — confirmed.** A real Simulator run captured a live TipKit tip's
   accessibility tree (`elements.json` for the step, from the showcase app's Stable tab) and found
   three stable, locale-independent identifiers: `PopoverDismissRegion` (label "dismiss popup", a
   full-screen tap-outside-to-dismiss scrim — its frame matches the whole screen, confirming the
   `UIPopoverPresentationController` lead), `TipView` (the tip's own container), and `xmark.circle.fill`
   (the close button, `traits: [button]`, label "Close"). The close button's identifier is an SF Symbol
   name rather than a localized string, so it would have satisfied the same locale-independence
   [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
   raised for SpringBoard's alert-button strings — but it is rejected as the dismiss target anyway: an
   SF Symbol name is a plausible accessibility identifier for an unrelated, app-authored button
   elsewhere in the same app, so matching on it risks `AmbiguousSelector` in some app, unlike
   `PopoverDismissRegion`, a TipKit-internal name no app-authored view would coincidentally reuse.
   `PopoverDismissRegion` is therefore the sole signal and the sole dismiss target this design uses.
   The same run also surfaced a fact the original design missed: while the tip is up, an ordinary
   toolbar button behind it (`stable.refresh`) does not merely fail `isHittable` — it disappears from
   the tree outright (0 of 188 polls over 10 seconds found it), because TipKit's presentation, like a
   modal, marks the covered content accessibility-hidden rather than merely stacking a view in front
   of it. A tap on that target therefore fails as `ElementNotFound`, not `ElementNotTappable` — unit 3
   below accounts for both. Finally, a plain, already-existing `tap: { id: "PopoverDismissRegion" }`
   step, with no new code at all, dismissed the tip and made `stable.refresh` queryable again
   immediately — the empirical basis for the simplification the rest of this design rests on: no new
   `Driver` method and no new `Capability` token, because `PopoverDismissRegion` is an ordinary node in
   the tree every wait poll and every tap resolution already fetches, and dismissing it is an ordinary
   `driver.tap()` every scenario already has access to. What the spike removes is the Swift runner work
   BE-0315 needed, not the driver layer itself — see unit 2 and the Alternatives entry on where the
   identifier lives.

2. **A driver-side dismiss method behind a capability token.** Add one backend-agnostic `Driver`
   method — `dismiss_blocking_tip() -> bool`, returning whether a tip was found and dismissed — behind
   a new top-level `Capability.HANDLE_TIPKIT_TIP`, advertised only by the XCUITest backend, the same
   shape and reasoning `HANDLE_SYSTEM_ALERT` and `PICKER_WHEEL` already document in
   `bajutsu/drivers/base.py`. The `PopoverDismissRegion` identifier lives **only** inside
   `bajutsu/drivers/xcuitest.py`, never in the orchestrator: TipKit is one backend's overlay, so
   knowledge of it belongs at the layer that already owns backend specifics, exactly as Playwright's
   `_on_dialog` does for browser dialogs. The orchestrator sees a boolean, not an identifier, so the
   deterministic core stays backend-agnostic (prime directive 3). The method resolves the region to
   exactly one match, returning `False` on no match and failing loudly on several rather than guessing
   (the `resolve_unique` / `AmbiguousSelector` contract, prime directive 2), and reports a fact without
   deciding pass/fail, so it stays clear of prime directive 1. Unit 1's finding is what keeps this unit
   small: it needs no Swift runner route, only the driver's existing query and tap primitives.

3. **Extend the tap-time recovery path, gated on the tip's confirmed presence.**
   [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md)'s
   `_tap_with_recovery` (`bajutsu/orchestrator/actions/handlers/gestures.py`) already catches a tap's
   `ElementNotTappable` and retries after a bounded scroll — but unit 1 showed a tip-covered target can
   also fail as `ElementNotFound`, entirely outside that catch, since the target vanishes from the tree
   rather than merely losing `isHittable`. Extend the recovery to catch both exceptions and, before
   running any existing recovery, call unit 2's `dismiss_blocking_tip()`. Like the wait-loop gate in
   unit 4, this hook runs only when the backend advertises `HANDLE_TIPKIT_TIP` and `tipKitHandling`
   (unit 5) is enabled, so a scenario asserting on a tip keeps today's behavior on both paths. Only when
   the call reports a tip actually dismissed does the hook retry the
   tap once: an `ElementNotFound` with no tip present is left to raise unchanged, since retrying on a
   generic "not found" with no confirmed cause would quietly mask a genuine selector bug — the same
   fail-loud discipline prime directive 2 already holds `resolve_unique` to. When a tip is confirmed and
   the retry still fails, `ElementNotTappable`'s existing bounded-scroll recovery still runs as today's
   fallback; scrolling never runs first, since a popover anchored to the target sits in front of it
   regardless of scroll position, and running it first would burn `_TAP_RECOVERY_MAX_SCROLLS`'s bounded
   budget on an obstruction it cannot clear.

4. **A wait-loop gate.** Following `_AlertGuardGate`'s shape (`bajutsu/orchestrator/waits.py`) but
   considerably smaller, a new gate calls unit 2's `dismiss_blocking_tip()` on the wait's own poll
   cadence — no independent wall-clock interval, since unit 1 established there is nothing
   out-of-process to rate-limit the way BE-0315's cross-process SpringBoard query needed. Dismissing
   proactively while a scenario is still waiting keeps most runs from ever reaching unit 3's tap-time
   path at all; unit 3 remains the backstop for a tip that is already up the moment a tap is attempted
   with no preceding wait. The gate only activates when the backend advertises `HANDLE_TIPKIT_TIP` and
   the opt-in setting below is enabled; everywhere else, today's behavior is unchanged.

5. **Opt-in configuration, off by default.** A new `tipKitHandling` setting (boolean), resolved through
   the same flag > scenario > target > default precedence
   [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) already
   established for `systemAlertHandling`, with a matching `--tipkit-handling` /
   `--no-tipkit-handling` CLI flag. Unlike `systemAlertHandling`, which defaults on, this defaults
   **off**: a TipKit tip is sometimes the subject a scenario is written to verify (an onboarding-flow
   assertion checking the tip's own text or its dismiss behavior), and turning the guard on by default
   would silently break that scenario by dismissing the very tip it asserts on. A scenario not asking
   for the guard keeps seeing the tip exactly as it does today.

6. **Verify on device and wire into the showcase.** A `.popoverTip()` fixture anchored to the "Refresh"
   button in the showcase SwiftUI demo app's Stable tab (the fixture the feasibility spike in unit 1
   already used), plus two scenarios that enable `tipKitHandling`: one exercising the wait-loop gate
   clearing a tip that appears mid-wait, and one tapping a target a tip already covers with no preceding
   wait, to exercise unit 3's recovery path directly. A dismiss against a real TipKit tip cannot be
   proven by the off-Simulator gate, so this unit must run against a booted Simulator; the
   backend-agnostic wiring (units 3 and 4) is covered by off-device tests against a stub driver.

## Alternatives considered

- **Express this through BE-0314's `interrupts` instead of a built-in guard.** Rejected: TipKit's
  close control carries no app-authored identifier, so no `condition`/selector an author writes can
  match it in an app-agnostic way — every app adopting TipKit would need to reverse-engineer the same
  internal structure this proposal determines once, in the driver.
- **Match `PopoverDismissRegion` directly in the orchestrator, skipping the driver method and the
  capability token.** The on-device spike (unit 1) showed this would work — the tip is already in the
  tree the orchestrator polls, so a bare identifier check in `waits.py` and `gestures.py` needs no new
  driver surface at all. Rejected anyway: it would put knowledge of one backend's overlay into the
  backend-agnostic core, which is the boundary prime directive 3 draws, and the precedent this proposal
  leans on puts that knowledge in the driver (Playwright's `_on_dialog` lives in
  `bajutsu/drivers/playwright.py`, not the orchestrator). One boolean-returning method is a small price
  for keeping an iOS-only identifier out of files every backend runs through. The spike's real dividend
  is narrower and still collected: no *Swift runner* route is needed, unlike BE-0315.
- **Dismiss via the close button's identifier (`xmark.circle.fill`) instead of `PopoverDismissRegion`.**
  Rejected: confirmed on-device that both work equally well as a dismiss target, but an SF Symbol name
  is a plausible accessibility identifier an unrelated, app-authored button could coincidentally reuse,
  risking `AmbiguousSelector` in some app. `PopoverDismissRegion` is a TipKit-internal name no
  app-authored view would collide with.
- **Default the guard on, mirroring `systemAlertHandling`.** Rejected: an OS permission prompt is never
  itself the subject of an assertion, but a TipKit tip sometimes is (an onboarding scenario). Defaulting
  off preserves today's behavior for every scenario not asking for the guard.
- **Dismiss by a fixed coordinate offset from the anchor element.** Rejected for the same reason BE-0315
  rejected it for SpringBoard buttons: a fixed offset breaks across device sizes and Dynamic Type; a
  resolved, named element is the stable primitive, and unit 1 confirmed one exists.
- **Design a cross-platform "framework-owned overlay" capability abstraction now, ahead of a concrete
  second target.** Rejected: no OS- or Jetpack-standardized Android equivalent exists with TipKit's key
  property (one consistent accessibility-tree shape across every app that uses it) — `TooltipCompat` and
  Compose Material3's `PlainTooltip`/`RichTooltip` are per-app-instantiated widgets, already reachable
  through BE-0314's `interrupts` like any other bespoke onboarding view. `HANDLE_SYSTEM_ALERT`'s
  precedent already shows the capability-token model generalizes without a redesign should a future
  platform-owned overlay turn out to need one.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — on-device feasibility spike, confirmed: `PopoverDismissRegion` is the sole signal and
      dismiss target; a tip-covered target can fail as `ElementNotFound`, not only `ElementNotTappable`.
- [ ] Unit 2 — `Driver.dismiss_blocking_tip()` behind `Capability.HANDLE_TIPKIT_TIP`, with the TipKit
      identifier confined to `bajutsu/drivers/xcuitest.py`.
- [ ] Unit 3 — tap-time recovery hook catching both exceptions, tried before the bounded-scroll
      recovery.
- [ ] Unit 4 — wait-loop gate calling the driver method on the wait's own poll cadence.
- [ ] Unit 5 — opt-in `tipKitHandling` setting (default off) with `--tipkit-handling` /
      `--no-tipkit-handling` and BE-0177 precedence.
- [ ] Unit 6 — on-device verification and showcase fixtures/scenarios for both recovery paths.

## References

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — the `Driver` interface and the
  `Capability` tokens (`HANDLE_SYSTEM_ALERT`, `PICKER_WHEEL`) this proposal's `HANDLE_TIPKIT_TIP`
  follows the shape of.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — the XCUITest driver that gains
  `dismiss_blocking_tip()`, and the one file the `PopoverDismissRegion` identifier appears in.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`, the
  mid-wait gate shape this proposal's smaller TipKit gate (unit 4) follows.
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)
  — `_tap_with_recovery` and `_TAP_RECOVERY_MAX_SCROLLS`, BE-0349's tap-time recovery path this
  proposal's unit 3 extends with a dismiss attempt tried ahead of the bounded scroll.
- [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — `_on_dialog`, the web
  backend's existing precedent for a framework-owned overlay handled in the driver rather than through
  scenario config or the orchestrator.
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) — the
  flag > scenario > target > default precedence `tipKitHandling` follows.
- [BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) — the
  permission pre-set whose notification gap is the precedent for an OS-owned surface no per-app
  config can reach.
- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) — the
  `interrupts` mechanism this proposal's Motivation explains is the wrong tool for a framework-owned
  overlay, and stays the right tool for a bespoke onboarding screen.
- [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md),
  [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) — the native,
  deterministic SpringBoard alert guard this proposal follows, whose Swift runner route turned out
  unnecessary for an in-tree overlay.
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md) — the locale-determinism
  concern the feasibility spike (unit 1) confirmed does not apply to any of the three identifiers found.
- [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md) — the
  `isHittable` check and `ElementNotTappable` error a blocking tip already triggers, without a way to
  recover from it.
- [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md)
  — a precedent for an on-device spike settling whether a plausible-looking signal is safe to rely on.

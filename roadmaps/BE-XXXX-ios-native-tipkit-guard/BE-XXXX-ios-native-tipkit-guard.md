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
happens to be the same control a scenario needs to tap, the tip's own view sits in front of it. This
proposal adds a deterministic, native, opt-in guard on the iOS XCUITest backend that recognizes a
blocking TipKit tip and dismisses it through its own close control — no screenshot, no model call —
mirroring the shape [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
and [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) already
established for SpringBoard system alerts. The contribution is recognizing this specific case —
a same-process, framework-owned overlay whose structure no app can customize — and clearing it
without asking every app that uses TipKit to hand-author the same recovery.

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

The pattern to reuse is BE-0315/BE-0316's, not BE-0314's: a TipKit tip is owned by an OS framework, so
its structure is the same across every app that adopts it, the same property that let BE-0315 give the
XCUITest driver a built-in, native SpringBoard-alert guard instead of asking each app to configure one.
Web has the same shape already, in miniature — Playwright's native `page.on('dialog')`
(`bajutsu/drivers/playwright.py`) auto-dismisses a browser-owned JavaScript `alert`/`confirm`/`prompt`
the same way, because the browser engine, not the page, owns that overlay. A TipKit tip is the iOS
in-process counterpart: framework-owned, not app-owned, and so a fit for a driver capability rather
than scenario config.

## Detailed design

The work divides into a feasibility spike, the native signal and dismiss action, the tap-time
recovery hook, the wait-loop gate, opt-in configuration, and on-device verification.

1. **Feasibility spike: find a locale- and app-independent signal that identifies a TipKit tip.**
   Before committing to an implementation, determine on a real Simulator whether TipKit's popover
   container and default close control expose a structural signal the XCUITest accessibility tree can
   query reliably — independent of the tip's own title/message text and of the device's locale (the
   same locale-determinism concern [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
   raised for SpringBoard's alert-button strings). An early look at a live tip's accessibility tree
   already turned up a promising lead: while a tip is showing, the tree exposes a region identified as
   `PopoverDismissRegion` — a name consistent with the tap-outside-to-dismiss scrim a
   `UIPopoverPresentationController`-backed popover typically installs. If this identifier holds up as
   stable and locale-independent, it plausibly serves both roles the spike needs at once — its mere
   presence is the presence signal, and tapping it is the dismiss action a user tapping outside the tip
   already triggers, which would let unit 3 resolve a single element rather than hunt for a separate
   close-button selector. This is a lead for the spike to confirm on-device, not yet a verified
   mechanism. Other candidate signals to test alongside it: the element hierarchy TipKit assembles
   around the popover (a container shape distinct from ordinary app content), and the close control's
   own accessibility traits (a button most apps do not otherwise place at a tip's anchor). If no
   reliable signal survives an on-device test — the same outcome
   [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md)'s
   spike hit — this proposal stops at this unit and is deferred rather than shipping a heuristic that
   only works by accident.

2. **A native presence query, exposed through the `Driver` interface.** Add a backend-agnostic method
   reporting whether a TipKit tip is currently blocking the screen and, when one is, enough to resolve
   its dismiss target deterministically (`PopoverDismissRegion` or the close control, whichever the
   spike confirms). Gate it behind a new top-level capability token, `Capability.HANDLE_TIPKIT_TIP`,
   advertised only by the iOS XCUITest backend — TipKit is an iOS-only framework, so Android and the
   web backend never advertise it, the same reasoning `HANDLE_SYSTEM_ALERT` already documents in
   `bajutsu/drivers/base.py`. This signal reports a fact and never decides pass/fail, so it stays clear
   of prime directive 1.

3. **A deterministic dismiss action.** Dismiss the current tip by tapping its resolved dismiss target —
   the `PopoverDismissRegion` scrim if unit 1 confirms it, else the close control — to exactly one match,
   failing loudly on zero or multiple rather than guessing (the `resolve_unique` / `AmbiguousSelector`
   contract, prime directive 2) — the same discipline BE-0316's `handle_system_alert` already applies to
   a SpringBoard button.

4. **Extend the tap-time recovery path, alongside a wait-loop gate.** [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md)'s
   `_tap_with_recovery` (`bajutsu/orchestrator/actions/handlers/gestures.py`) already catches a tap's
   `ElementNotTappable` and retries after a bounded scroll — but a tip is not scroll-clearable, so a
   tip already blocking the target the moment a scenario taps it, with no preceding wait, would still
   fail the step even with the guard installed only as a wait-loop gate (unit 5 below). Add a native
   dismiss attempt to `_tap_with_recovery`, gated by `HANDLE_TIPKIT_TIP` and `tipKitHandling`: on
   `ElementNotTappable`, check the presence query once before the scroll recovery runs; if a tip is
   present, dismiss it and retry the tap immediately, falling through to the existing bounded-scroll
   recovery only if the target is still not tappable afterward. Trying the dismiss first, ahead of
   scrolling, matches the obstruction — a popover anchored to the target sits in front of it regardless
   of scroll position, so scrolling first would burn `_TAP_RECOVERY_MAX_SCROLLS`'s bounded budget
   without ever addressing the actual cover.

5. **A wait-loop gate, gated by both the capability and an opt-in setting.** Following
   `_AlertGuardGate`'s shape (`bajutsu/orchestrator/waits.py`), a new gate polls the native presence
   query on its own wall-clock interval — decoupled from the wait's own poll cadence, for the same
   single-main-thread runner-load reason BE-0315 documents — and dismisses the tip the moment a poll
   finds one. No debounce or cooldown is needed: a native presence query reports a fact, not a
   collapsed-tree proxy's correlation. Dismissing proactively while a scenario is still waiting keeps
   most runs from ever reaching unit 4's tap-time path at all; unit 4 remains the backstop for a tip
   that is already up the moment a tap is attempted with no preceding wait. The gate only activates
   when the backend advertises `HANDLE_TIPKIT_TIP` and the opt-in setting below is enabled; everywhere
   else, today's behavior is unchanged.

6. **Opt-in configuration, off by default.** A new `tipKitHandling` setting (boolean), resolved through
   the same flag > scenario > target > default precedence
   [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) already
   established for `systemAlertHandling`, with a matching `--tipkit-handling` /
   `--no-tipkit-handling` CLI flag. Unlike `systemAlertHandling`, which defaults on, this defaults
   **off**: a TipKit tip is sometimes the subject a scenario is written to verify (an onboarding-flow
   assertion checking the tip's own text or its dismiss behavior), and turning the guard on by default
   would silently break that scenario by dismissing the very tip it asserts on. A scenario not asking
   for the guard keeps seeing the tip exactly as it does today.

7. **Verify on device and wire into the showcase.** Add a `.popoverTip()` fixture anchored to a button
   in the showcase SwiftUI demo app, and two scenarios that enable `tipKitHandling`: one exercising the
   wait-loop gate clearing a tip that appears mid-wait, and one tapping a target a tip already covers
   with no preceding wait, to exercise unit 4's recovery path directly. A native dismiss against a real
   TipKit tip cannot be proven by the off-Simulator gate, so this unit must run against a booted
   Simulator; the backend-agnostic wiring (units 3 through 5) is covered by off-device tests that stub
   the driver capability.

## Alternatives considered

- **Express this through BE-0314's `interrupts` instead of a driver capability.** Rejected: TipKit's
  close control carries no app-authored identifier, so no `condition`/selector an author writes can
  match it in an app-agnostic way — every app adopting TipKit would need to reverse-engineer the same
  internal structure this proposal determines once, in the driver.
- **Default the guard on, mirroring `systemAlertHandling`.** Rejected: an OS permission prompt is never
  itself the subject of an assertion, but a TipKit tip sometimes is (an onboarding scenario). Defaulting
  off preserves today's behavior for every scenario not asking for the guard.
- **Dismiss by a fixed coordinate offset from the anchor element.** Rejected for the same reason BE-0315
  rejected it for SpringBoard buttons: a fixed offset breaks across device sizes and Dynamic Type; a
  resolved close-control element is the stable primitive once the feasibility spike identifies one.
- **Design a cross-platform "framework-owned overlay" capability abstraction now, ahead of a concrete
  second target.** Rejected: no OS- or Jetpack-standardized Android equivalent exists with TipKit's key
  property (one consistent accessibility-tree shape across every app that uses it) — `TooltipCompat` and
  Compose Material3's `PlainTooltip`/`RichTooltip` are per-app-instantiated widgets, already reachable
  through BE-0314's `interrupts` like any other bespoke onboarding view. `HANDLE_SYSTEM_ALERT`'s
  precedent already shows the capability-token model generalizes without a redesign, so a future
  platform-owned overlay can add its own token when one actually appears.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — on-device feasibility spike for a locale- and app-independent TipKit tip signal,
      starting from the `PopoverDismissRegion` lead.
- [ ] Unit 2 — native presence query behind a new `Capability.HANDLE_TIPKIT_TIP` token.
- [ ] Unit 3 — deterministic dismiss-by-resolved-selector action.
- [ ] Unit 4 — tap-time recovery hook in `_tap_with_recovery`, tried before the bounded-scroll
      recovery.
- [ ] Unit 5 — wait-loop gate polling on an independent interval.
- [ ] Unit 6 — opt-in `tipKitHandling` setting (default off) with `--tipkit-handling` /
      `--no-tipkit-handling` and BE-0177 precedence.
- [ ] Unit 7 — on-device verification and showcase fixtures/scenarios for both recovery paths.

## References

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — the `Driver` interface and
  `Capability` tokens (`HANDLE_SYSTEM_ALERT`, `PICKER_WHEEL`) this proposal's
  `HANDLE_TIPKIT_TIP` follows the shape of.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`, the
  mid-wait gate shape this proposal's TipKit gate reuses.
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)
  — `_tap_with_recovery` and `_TAP_RECOVERY_MAX_SCROLLS`, BE-0349's tap-time recovery path this
  proposal's unit 4 extends with a native dismiss attempt tried ahead of the bounded scroll.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — the Python XCUITest driver
  that would gain the presence and dismiss methods.
- [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — `_on_dialog`, the web
  backend's existing precedent for a framework-owned overlay handled natively rather than through
  scenario config.
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
  deterministic SpringBoard alert guard this proposal mirrors for an in-process overlay.
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md) — the locale-determinism
  concern the feasibility spike must account for.
- [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md) — the
  `isHittable` check and `ElementNotTappable` error a blocking tip already triggers, without a way to
  recover from it.
- [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md)
  — a precedent for an on-device spike disproving a plausible-looking signal and the item deferring
  rather than shipping a heuristic.

**English** · [日本語](BE-0389-ios-native-tipkit-guard-ja.md)

# BE-0389 — iOS native TipKit tip guard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0389](BE-0389-ios-native-tipkit-guard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0389") |
| Implementing PR | [#1682](https://github.com/bajutsu-e2e/bajutsu/pull/1682) |
| Topic | Platform support |
| Related | [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md), [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md), [BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md), [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md), [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md) |
<!-- /BE-METADATA -->

## Introduction

Apple's TipKit (iOS 17+) renders a popover-style tip anchored to a UI element to guide a user
through a feature. The app itself does not fully control the schedule: first-launch state, an
eligibility rule, or an event trigger decides when a tip appears, not a fixed step in the flow.

When the anchor happens to be the same control a scenario needs to tap, the tip's own view sits in
front of it. On a real Simulator we confirmed that TipKit's presentation hides the covered content
from the accessibility tree entirely, rather than merely occluding it. The blocked tap can
fail as `ElementNotFound`, not only `ElementNotTappable`.

This proposal adds a deterministic, opt-in guard on the iOS XCUITest backend. The guard recognizes a
blocking TipKit tip and dismisses it. It needs no screenshot and no model call. Because the tip
already surfaces inside the app's own accessibility tree, it needs no Swift runner change either.

The contribution is recognizing this specific case: a same-process, framework-owned overlay whose
structure no app can customize. The guard clears that overlay without asking every app that uses
TipKit to hand-author the same recovery.

## Motivation

[BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) already lets
a scenario declare `interrupts`: a `condition` checked opportunistically against the tree a wait has
already fetched, paired with recovery `steps`. It targets a screen that can appear at an
unpredictable point, such as an onboarding step, a tutorial overlay, or a permission prompt that the
accessibility tree can see. A TipKit tip has exactly that unpredictable-timing shape, so on its face
`interrupts` looks like the right tool.

That looks right but is not, for one concrete reason. The `condition` of `interrupts` is an assertion an
author writes against the app's own accessibility tree, and that requires a selector the author can
name. A custom onboarding screen is the app team's own view, so it carries whatever identifier the
team chooses to give it. That is the case `interrupts` was built for.

A TipKit tip is not the app's view. Its title and message differ per `Tip` instance across an app.
The built-in close control that TipKit adds by default carries no accessibility identifier the app
sets, because the app supplies only the tip's content, not its container or dismiss control. An author
has no stable, app-authored selector to put in a `condition` at all.

`interrupts` cannot express "dismiss whichever TipKit tip is showing" unless the app team
first reverse-engineers TipKit's internal view structure well enough to select it. The team would
have to redo that work for every app that adopts TipKit. This is the same gap that
[BE-0276](../BE-0276-scenario-permission-state/BE-0276-scenario-permission-state.md) hit for the
notification prompt, a surface no per-app config can reach.

[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md) already
keeps a blocked tap from landing quietly on the wrong element. `isHittable` reads false when a tip
covers the target, so the driver raises `ElementNotTappable` instead of mistapping the tip. That is
the correct failure to raise when the obstruction is unexpected. When the obstruction is a known,
recoverable TipKit tip, though, failing outright wastes the run. `ElementNotTappable`'s own recovery,
a bounded scroll, does not apply here, because scrolling does not dismiss a popover anchored to an
on-screen control.

The pattern to reuse is BE-0315/BE-0316's, not BE-0314's. An OS framework owns a TipKit tip, so its
structure is the same across every app that adopts it. That property let BE-0315 give the XCUITest
backend a built-in, deterministic guard behind a capability token, instead of asking each app to
configure one.

Web already has the analogous case in miniature. Playwright's native `page.on('dialog')`
(`bajutsu/drivers/playwright.py`) auto-dismisses a browser-owned JavaScript
`alert`/`confirm`/`prompt`, because the browser engine, not the page, owns that overlay. That
handler lives in the *driver*, which is where knowledge of one backend's overlays belongs. This
proposal keeps that layering. The TipKit-specific identifier stays inside the XCUITest driver, and
the orchestrator sees a backend-agnostic call that answers "was a blocking tip dismissed?".

The on-device spike (unit 1 below) changes how much machinery that driver method needs. BE-0315's
SpringBoard alert lives in a second, out-of-process `XCUIApplication`, so reaching it needed new
Swift runner routes on top of the capability token. A TipKit tip is not out-of-process. It renders
inside the app's own view hierarchy and already surfaces in the same `elements` snapshot that every
wait poll and every tap resolution fetches. The driver can implement the whole guard in
Python, on the query and tap primitives it already has: **no Swift or BajutsuKit change at all**.
That is what makes this a small item rather than a runner-protocol change.

## Detailed design

The work divides into six units:

- a feasibility spike (confirmed on-device before this proposal was written up);
- the driver-side dismiss method behind a capability token;
- the post-failure retry;
- the mid-wait gate;
- opt-in configuration;
- on-device verification.

1. **On-device feasibility spike — confirmed.** A real Simulator run captured a live TipKit tip's
   accessibility tree (`elements.json` for the step, from the showcase app's Stable tab). The tree
   held three stable, locale-independent identifiers:
   - `PopoverDismissRegion` (label "dismiss popup"): a full-screen tap-outside-to-dismiss scrim.
     Its frame matches the whole screen, which confirms the `UIPopoverPresentationController` lead.
   - `TipView`: the tip's own container.
   - `xmark.circle.fill`: the close button (`traits: [button]`, label "Close").

   The close button's identifier is an SF Symbol name rather than a localized string. It would have
   satisfied the same locale-independence that
   [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
   raised for SpringBoard's alert-button strings. We reject it as the dismiss target anyway, because
   an SF Symbol name is a plausible accessibility identifier for an unrelated, app-authored button
   elsewhere in the same app. Matching on it risks `AmbiguousSelector`. `PopoverDismissRegion` is the
   sole signal and the sole dismiss target this design uses.

   The spike did **not** settle one limit: whether that name is TipKit-exclusive. Both the name and
   the full-screen frame point at `UIPopoverPresentationController`. A genuinely popover-presented
   view of the app's own (a SwiftUI `.popover` forced past its compact sheet adaptation) plausibly
   installs the same scrim. An attempt to reproduce that on-device could not get such a popover to
   present, so the question remains unverified rather than disproved.

   The opt-in default bounds the consequence. The guard runs only on a scenario that asked for it, and
   only after a step already failed. An author whose app presents its own popovers, and who wants them
   untouched, leaves `iosTipKitHandling` off. Narrowing the match (pairing the region with
   `TipView`'s presence) is the follow-up if a real collision turns up.

   The same run surfaced a fact the original design missed. While the tip is up, an ordinary toolbar
   button behind it (`stable.refresh`) does not merely fail `isHittable`. It disappears from the tree
   outright (0 of 188 polls over 10 seconds found it). TipKit's presentation, like a modal, marks the
   covered content accessibility-hidden. It does not merely stack a view in front of it.
   A tap on that target fails as `ElementNotFound`, not `ElementNotTappable`. Unit 3 below
   accounts for both.

   A plain, already-existing `tap: { id: "PopoverDismissRegion" }` step, with no new code at
   all, dismissed the tip and made `stable.refresh` queryable again at once. That is the empirical
   basis for the simplification the rest of this design rests on: **no Swift runner route**.
   `PopoverDismissRegion` is an ordinary node in the tree that every wait poll and every tap
   resolution already fetches, reachable by the driver's existing tap. The spike removes the runner
   work BE-0315 needed, not the driver layer itself. Unit 2 keeps the method and the capability token,
   and the Alternatives entry below explains why the identifier stays there.

2. **A driver-side dismiss method behind a capability token.** Add one backend-agnostic `Driver`
   method, `dismiss_blocking_tip() -> bool`, which returns whether a tip was found and dismissed.
   The method sits behind a new top-level `Capability.HANDLE_TIPKIT_TIP`, which the XCUITest backend
   alone advertises. The shape and reasoning match `HANDLE_SYSTEM_ALERT` and `PICKER_WHEEL`, which
   `bajutsu/drivers/base.py` already documents.

   The `PopoverDismissRegion` identifier lives **only** inside `bajutsu/drivers/xcuitest.py`, never in
   the orchestrator. TipKit is one backend's overlay, so knowledge of it belongs at the layer that
   already owns backend specifics, as Playwright's `_on_dialog` does for browser dialogs. The
   orchestrator sees a boolean, not an identifier, so the deterministic core stays backend-agnostic
   (prime directive 3).

   The method resolves the region to exactly one match. It returns `False` on no match. On several matches
   it fails loudly rather than guessing, which is the `resolve_unique` / `AmbiguousSelector` contract
   (prime directive 2). The method reports a fact without deciding pass/fail, so it stays clear of
   prime directive 1.

   Unit 1's finding keeps this unit small. The unit needs no Swift runner route, merely the driver's
   existing query and tap primitives.

3. **A post-failure dismiss-and-retry, in the step loop, checked in sequence with the alert guard.**
   Unit 1 showed that a tip-covered target does not merely lose `isHittable`. It leaves the tree, so
   the step it blocked fails as `ElementNotFound` as readily as `ElementNotTappable`.

   That rules out
   [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md)'s
   `_tap_with_recovery` (`bajutsu/orchestrator/actions/handlers/gestures.py`) as the place for the
   recovery. It catches the latter error alone. A step handler's signature also carries no
   per-scenario run-behavior setting. Gating the recovery there would mean changing `ActionHandler`
   and every handler and caller.

   Put the recovery in the step loop instead (`bajutsu/orchestrator/loop.py`), beside the alert
   guard's existing end-of-step "dismiss, then one more shot" branch. That layer already sees every
   step failure whatever its exception, and already holds the scenario. Both guards then end up in
   the one place that owns the config.

   On a failed step, call unit 2's `dismiss_blocking_tip()`. The step gets one retry if, and only if,
   the call reports a tip actually dismissed. A failure with no tip present is left to fail
   unchanged. Retrying a generic "not found" with no confirmed cause would quietly mask a genuine
   selector bug, the same fail-loud discipline that prime directive 2 holds `resolve_unique` to.
   Because the dismiss is what gates the retry, a passing run never pays a query for this at all.

   The two end-of-step guards are checked in sequence rather than as one `elif` ladder. A tip and a
   system alert can both be up. A tip dismissed first must not consume the failure and leave the
   alert, the case the alert guard exists for, unhandled. Each guard still runs at most once per
   step.

4. **A mid-wait gate, riding the poll callback BE-0314 already installs.** A wait blocked behind a tip
   should not use up its whole timeout before unit 3 rescues it. The dismiss also runs while
   the wait polls. The gate composes onto the `on_interrupt_poll` callback that BE-0314's `interrupts`
   already threads through every wait. The gate is not a second object beside `_AlertGuardGate`.

   That callback receives each poll's already-fetched tree, so the dismiss costs no query of its own
   and needs no independent wall-clock interval. Unit 1 established that nothing out-of-process needs
   rate-limiting here, the way BE-0315's cross-process SpringBoard query did.

   The callback's contract forces one detail: a successful dismiss must report `False`, since `True` on
   that callback means "a recovery failed, end the wait". Reporting `True` would abort a wait that the
   dismiss freed to succeed.

   The gate activates only when the backend advertises `HANDLE_TIPKIT_TIP` and the opt-in setting below is
   enabled. It composes transparently with a scenario that also declares `interrupts` and with one
   that does not.

5. **Opt-in configuration, off by default.** A new `iosTipKitHandling` setting (boolean) resolves
   through the same flag > scenario > target > default precedence that
   [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) already
   established for `systemAlertHandling`. A matching `--ios-tipkit-handling` /
   `--no-ios-tipkit-handling` CLI flag accompanies it.

   Unlike `systemAlertHandling`, which defaults on, this setting defaults **off**. A TipKit tip is
   sometimes the subject a scenario is written to verify, for example an onboarding-flow assertion
   that checks the tip's own text or its dismiss behavior. Turning the guard on by default would
   silently break that scenario by dismissing the same tip it asserts on. A scenario that does not ask
   for the guard keeps seeing the tip as it does today.

   The `ios` prefix is deliberate, and it is the one place where this proposal's naming departs from
   `systemAlertHandling`'s. That key needs no platform prefix, because the idea it names (an OS
   prompt the app's own tree cannot reach) has some form on every platform, so each backend can honor
   it in its own terms. TipKit is one vendor's framework. No Android or web counterpart has its
   defining property (one tree shape across every app that adopts it, per the Alternatives entry
   below), so the key is inert anywhere else. Naming that in the key spares an author who discovers
   it from a silent no-op. It also leaves room for a differently-shaped Android answer later, without
   renaming an unprefixed key that had quietly meant "iOS only" all along.

6. **Verify on device and wire into the showcase.** Add a `.popoverTip()` fixture anchored to the
   "Refresh" button in the showcase SwiftUI demo app's Stable tab (the fixture the feasibility spike
   in unit 1 already used). Add two scenarios that enable `iosTipKitHandling`:
   - one exercises the wait-loop gate clearing a tip that appears mid-wait;
   - one taps a target a tip already covers, with no preceding wait, to exercise unit 3's recovery
     path directly.

   The off-Simulator gate cannot prove a dismiss against a real TipKit tip, so this unit must run
   against a booted Simulator. Off-device tests against a stub driver cover the backend-agnostic
   wiring (units 3 and 4).

## Alternatives considered

- **Express this through BE-0314's `interrupts` instead of a built-in guard.** Rejected. TipKit's
  close control carries no app-authored identifier, so no `condition`/selector an author writes can
  match it in an app-agnostic way. Every app adopting TipKit would need to reverse-engineer the same
  internal structure this proposal determines once, in the driver.
- **Match `PopoverDismissRegion` directly in the orchestrator, skipping the driver method and the
  capability token.** The on-device spike (unit 1) showed this would work. The tip is already in the
  tree the orchestrator polls. A bare identifier check in `waits.py` and `gestures.py` needs no new
  driver surface at all.

  Rejected anyway. The check would put knowledge of one backend's overlay into the backend-agnostic
  core, which is the boundary prime directive 3 draws. The precedent this proposal leans on also
  puts that knowledge in the driver. Playwright's `_on_dialog` lives in
  `bajutsu/drivers/playwright.py`, not the orchestrator.

  One boolean-returning method is a small price for keeping an iOS-only identifier out of files
  every backend runs through. The spike's real dividend is narrower and still collected: no *Swift
  runner* route is needed, unlike BE-0315.
- **Dismiss via the close button's identifier (`xmark.circle.fill`) instead of `PopoverDismissRegion`.**
  Rejected. We confirmed on-device that both work as well as a dismiss target. But an SF Symbol
  name is a plausible accessibility identifier that an unrelated, app-authored button could reuse
  by coincidence, which risks `AmbiguousSelector` in some app. `PopoverDismissRegion` is a
  TipKit-internal name that no app-authored view would collide with.
- **Default the guard on, mirroring `systemAlertHandling`.** Rejected. An OS permission prompt is
  never itself the subject of an assertion, but a TipKit tip sometimes is (an onboarding scenario).
  Defaulting off preserves today's behavior for every scenario not asking for the guard.
- **Dismiss by a fixed coordinate offset from the anchor element.** Rejected for the same reason
  BE-0315 rejected it for SpringBoard buttons. A fixed offset breaks across device sizes and Dynamic
  Type, while a resolved, named element is the stable primitive, and unit 1 confirmed one exists.
- **Design a cross-platform "framework-owned overlay" capability abstraction now, ahead of a concrete
  second target.** Rejected. No OS- or Jetpack-standardized Android equivalent exists with TipKit's
  key property (one consistent accessibility-tree shape across every app that uses it).
  `TooltipCompat` and Compose Material3's `PlainTooltip`/`RichTooltip` are per-app-instantiated
  widgets, already reachable through BE-0314's `interrupts` like any other bespoke onboarding view.
  `HANDLE_SYSTEM_ALERT`'s precedent already shows the capability-token model generalizes without a
  redesign, should a future platform-owned overlay turn out to need one.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — on-device feasibility spike, confirmed: `PopoverDismissRegion` is the sole signal and
      dismiss target; a tip-covered target can fail as `ElementNotFound`, not only `ElementNotTappable`.
- [x] Unit 2 — `Driver.dismiss_blocking_tip()` behind `Capability.HANDLE_TIPKIT_TIP`, with the TipKit
      identifier confined to `bajutsu/drivers/xcuitest.py`.
- [x] Unit 3 — post-failure dismiss-and-retry in the step loop, beside the alert guard's own branch.
- [x] Unit 4 — mid-wait dismiss composed onto BE-0314's `on_interrupt_poll` hook.
- [x] Unit 5 — opt-in `iosTipKitHandling` setting (default off) with `--ios-tipkit-handling` /
      `--no-ios-tipkit-handling` and BE-0177 precedence.
- [x] Unit 6 — on-device verification and showcase fixtures/scenarios for both recovery paths.

Verified on a booted Simulator with `demos/showcase/scenarios/tipkit.yaml`: all three scenarios pass
with the guard on, and forcing it off with `--no-ios-tipkit-handling` fails exactly the two
guard-dependent ones — the tap scenario with `no match: {'id': 'stable.refresh'}` (the target absent
from the tree, unit 1's `ElementNotFound` finding) and the wait scenario on its full 15s timeout.
Each guard path is therefore load-bearing, not incidentally passing.

## References

- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — the `Driver` interface and the
  `Capability` tokens (`HANDLE_SYSTEM_ALERT`, `PICKER_WHEEL`) this proposal's `HANDLE_TIPKIT_TIP`
  follows the shape of.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — the XCUITest driver that gains
  `dismiss_blocking_tip()`, and the one file the `PopoverDismissRegion` identifier appears in.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — `_AlertGuardGate`, the
  mid-wait guard whose shape unit 4 was modelled on before it reused BE-0314's poll hook instead, and
  the `on_interrupt_poll` contract that makes a successful dismiss report `False`.
- [`bajutsu/orchestrator/loop.py`](../../bajutsu/orchestrator/loop.py) — the step loop that gains
  both guards (units 3 and 4), beside the alert guard's existing end-of-step dismiss-and-retry.
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py)
  — `_tap_with_recovery`, BE-0349's tap-time recovery, which unit 3 explains is the wrong hook: it
  catches only `ElementNotTappable`, and a tip-covered target leaves the tree instead.
- [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py) — `_on_dialog`, the web
  backend's existing precedent for a framework-owned overlay handled in the driver rather than through
  scenario config or the orchestrator.
- [BE-0177](../BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md) — the
  flag > scenario > target > default precedence `iosTipKitHandling` follows.
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

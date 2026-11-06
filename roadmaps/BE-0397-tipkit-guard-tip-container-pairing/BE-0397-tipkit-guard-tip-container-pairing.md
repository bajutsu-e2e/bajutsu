**English** · [日本語](BE-0397-tipkit-guard-tip-container-pairing-ja.md)

# BE-0397 — Require the tip container before the TipKit guard dismisses a popover

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0397](BE-0397-tipkit-guard-tip-container-pairing.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0397") |
| Implementing PR | [#1750](https://github.com/bajutsu-e2e/bajutsu/pull/1750) |
| Topic | Platform support |
| Related | [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md), [BE-0389](../BE-0389-ios-native-tipkit-guard/BE-0389-ios-native-tipkit-guard.md) |
<!-- /BE-METADATA -->

## Introduction

Apple's TipKit renders a popover-style tip that can cover the control a scenario needs to reach. [BE-0389](../BE-0389-ios-native-tipkit-guard/BE-0389-ios-native-tipkit-guard.md) added an opt-in guard on the iOS XCUITest backend that recognizes such a tip and dismisses it.

The guard recognizes one thing: a node whose accessibility identifier is `PopoverDismissRegion`. That node is the full-screen tap-outside-to-dismiss scrim that a popover presentation installs. BE-0389 could not settle whether the identifier belongs to TipKit alone. It named the narrowing that this proposal makes as the follow-up to run if a collision ever turned up.

A collision has turned up, and the case is not the exotic one BE-0389 imagined. A plain SwiftUI `confirmationDialog`, the ordinary way an app asks a user to confirm a deletion, installs the same scrim under the same identifier and the same full-screen frame.

This proposal pairs the scrim with `TipView`, the tip's own container. The guard then recognizes a TipKit tip by two nodes instead of one and leaves every other popover alone.

## Motivation

The guard dismisses by tapping the scrim, and a scrim tap closes whatever the scrim belongs to. When the scrim belongs to an app's own confirmation dialog instead of a tip, the guard closes a dialog that the scenario was about to act on. The next step then fails to find the button it wanted. The run reports a missing element, not the dismissal that removed it. The stated reason names neither the guard nor `PopoverDismissRegion`, because the orchestrator sees merely a boolean from the driver.

Measurement, not inference, establishes the collision. A Simulator run against the showcase app captured the accessibility tree in three states, and the trees separate cleanly:

| Screen state | `PopoverDismissRegion` | `TipView` |
|---|---|---|
| TipKit tip, SwiftUI `.popoverTip` | present, label "dismiss popup", frame `[0, 0, 402, 874]` | **present** |
| TipKit tip, UIKit `TipUIPopoverViewController` | present, same label, same full-screen frame | **present** |
| SwiftUI `confirmationDialog` | present, same label, same full-screen frame | absent |
| Native long-press edit menu | absent | absent |

Two facts in the table matter beyond the collision itself. First, the scrim that a confirmation dialog installs matches the tip's scrim exactly in label and frame. No property of the scrim node distinguishes the two cases, so only a second node can.

Second, the native long-press edit menu (Paste / Select / Select All / AutoFill) installs no such scrim at all. Its nodes carry neither an identifier nor a label. An edit menu never triggers the guard, so it never needed to be told apart from a tip.

The collision reaches beyond the guard, because the same identifier is the natural thing to write into an [`interrupts`](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) entry by hand. An author who writes `condition: { exists: { id: PopoverDismissRegion } }` to clear tips gets an entry that fires on the app's own confirmation dialogs too.

The scenario DSL offers no way to narrow such an entry:

- `Assertion` permits exactly one kind per condition, with no conjunction to add "and the tip container is present".
- A scenario cannot disable one entry inherited from its target's config.

Documenting `TipView` as the condition to key on costs no code and removes this trap for hand-written entries. This proposal carries the documentation change alongside the driver change.

Once this change lands, a reader can tell it worked from a single on-device observation. A scenario opts into `iosTipKitHandling`, opens a confirmation dialog, and asserts on the dialog's own button. The button is still there. Before the change, the guard dismisses the dialog and the assertion fails on a missing element.

## Detailed design

The work divides into four parts: the driver-side narrowing, an on-device fixture that pins it, the unit tests, and the documentation correction.

1. **Pair the scrim with the tip container in the driver.** `dismiss_blocking_tip()` in [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) treats `PopoverDismissRegion` as enough evidence that a tip is up. It will instead require both `PopoverDismissRegion` and `TipView` to be present. When only the scrim is present, it reports `False` with no tap and no error.

   What the driver taps does not change. The scrim stays the dismiss target, as BE-0389 established, and the container is a detection signal alone. BE-0389 asserted this without measuring it, so this item measures it. Tapping `TipView` leaves the tip up: the tree stays at 29 elements, with container and scrim both still present. Tapping the close button clears the tip, and the tree returns to 118 elements. The container is usable for recognizing a tip and unusable for closing one.

   Both the caller's-tree short-circuit and the re-check against the freshly queried tree apply the pair. A dialog costs no more queries than a tip-free screen already does.

   `resolve_unique` keeps guarding the scrim alone. Multiple scrims remain a shape TipKit should never produce, so they still fail loudly instead of picking one (prime directive 2). The container is tested for presence alone, because an app may legitimately show more than one tip container in a tree, and the guard does not need a count.

   The obvious way this narrowing could go wrong is by making the guard miss a real tip, and the measurement closes that off. `TipView` is what SwiftUI's `.popoverTip` produces. A container named purely in SwiftUI's own view code would leave every UIKit-hosted tip unrecognized, so the case worth checking separately is a UIKit app presenting a tip through `TipUIPopoverViewController`.

   Measured on-device, the UIKit-hosted tip carries `TipView` too, alongside the same `PopoverDismissRegion` scrim and the same `xmark.circle.fill` close button (the table above records it). The container is TipKit's own, not SwiftUI's wrapper around it, so the pair holds across both hosts.

   The same measurement settles a question an author might reasonably ask: can an app name its own tips and skip the internal identifier? It cannot, on the side where the name would matter most.

   - A UIKit app *can* set `accessibilityIdentifier` on the popover controller's view. That name reaches the tree as a node of its own, wrapping `TipView` instead of replacing it.
   - SwiftUI grants no such handle. `.accessibilityIdentifier` applied to a `.popoverTip` decorates the anchor, and the tip's own presentation belongs to the framework. The identifier never appears in the tip's tree at all.

   An app-supplied name is available just where it is not needed. That is why the guard keys on TipKit's own container.

2. **An on-device fixture for the case that must be left alone.** The showcase app has no real confirmation dialog today. Its Log tab presents an action sheet built from a `ZStack` overlay. That choice was deliberate: a SwiftUI `confirmationDialog` renders duplicate accessibility elements for its buttons, so it cannot serve as a fixture for a single-match tap. Detection needs no single-match tap, so a `confirmationDialog` serves this fixture even though it cannot serve that one.

   Add a `confirmationDialog` to [`demos/showcase/ios/swiftui/Sources/LogView.swift`](../../demos/showcase/ios/swiftui/Sources/LogView.swift) behind its own button. Add a scenario to [`demos/showcase/scenarios/tipkit.yaml`](../../demos/showcase/scenarios/tipkit.yaml) that opts into the guard, opens the dialog, and asserts that one of the dialog's own buttons is still present.

   The existing tipkit scenarios already cover the other direction: a tip that must be dismissed, and a tip that must be left alone when the scenario does not opt in. The third of them already waits on `TipView` by name. That wait is the on-device evidence that the container this design keys on is reliably in the tree.

3. **Unit tests for both halves of the pair.** The TipKit-internal identifiers live in the XCUITest driver alone, so [`tests/test_xcuitest.py`](../../tests/test_xcuitest.py) is where the pair is pinned:

   - A tree holding both nodes still dismisses.
   - A tree holding only the scrim does not dismiss and taps nothing.
   - The caller's-tree short-circuit rules a scrim-only tree out without querying.

   The fake driver in [`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py) seeds the identifiers that a test put in its tree, instead of hardcoding iOS names. It grows a second seeded identifier for the container. The orchestrator tests in [`tests/orchestrator/test_tipkit_guard.py`](../../tests/orchestrator/test_tipkit_guard.py) seed both.

4. **Correct the documentation, and name the condition to key on.** [`docs/scenarios.md`](../../docs/scenarios.md) states that a TipKit tip is "the framework-owned popover an app cannot give a selector, so no `interrupts` entry can name it". The claim is wrong in a way that matters here. A tip's nodes carry stable identifiers, an `interrupts` entry can name them, and an entry keyed on the scrim alone is the trap this proposal documents.

   Replace the claim with what is true. The guard exists so that a scenario need not hand-author the recovery, and an author who does write one keys it on `TipView`, not on `PopoverDismissRegion`. Mirror the correction in `docs/ja/scenarios.md`. Update [`docs/architecture.md`](../../docs/architecture.md) and its Japanese mirror too, because their one-line description of the guard names the scrim as the whole signal.

## Alternatives considered

- **Leave the guard as it is, since it is opt-in.** Rejected. Opt-in bounds who is exposed, not what happens to them. Opting in to clear tips is not agreement to have the app's own confirmation dialogs closed, and the resulting failure names a missing element, not the dismissal that caused it. BE-0389 accepted the risk because it could not reproduce the collision. The measurement above removes that basis.

- **Key on the close button (`xmark.circle.fill`) instead of the container.** Rejected, for the reason BE-0389 already gives. The identifier is an SF Symbol name, which an unrelated app-authored button could plausibly reuse, turning the guard's own lookup into an `AmbiguousSelector`. `TipView` is a TipKit-internal container name with no such second life.

- **Exclude the collision instead of identifying the tip, by skipping a scrim whose tree also holds a node with the `sheet` trait.** Rejected. A denial list has to enumerate every popover an app might present. The confirmation dialog measured here is one case, not the boundary of the problem, and the next collision would need another entry. Requiring the tip's own container identifies what the guard is for, so an unenumerated popover is left alone by default.

- **Drop the guard instead of narrowing it, since a hand-written `interrupts` entry does the same job.** Out of scope here, and worth stating plainly because the case is stronger than it looks.

  BE-0389's own spike records that a plain `tap: { id: "PopoverDismissRegion" }` step, with no new code at all, dismissed the tip. An `interrupts` entry is evaluated on every act step's pre-action read, not only after a step has already failed. It clears the tip *before* the act instead of paying one failure first, at the cost of one extra query per bare act.

  What the guard still buys is a single home for TipKit's internal names. When Apple renames a node, two lines in one driver cover every scenario, where hand-written entries would each need editing. This item's measurement cuts both ways on that trade: the same central place that would absorb a rename is where the wrong dismiss came from. Retiring the guard in favor of documented `interrupts` entries is therefore a coherent position.

  It is a different decision from this one, though. It reverses what BE-0389 settled, while this item fixes a defect in the guard that exists today.

- **Add conjunction to `Interrupt.condition` so a hand-written entry can require two signals.** Rejected for this item, and left as its own question. Conjunction would help an author writing an `interrupts` entry, but it would not touch the driver-side guard, which evaluates no scenario condition. It also widens the assertion DSL, whose one-kind-per-assertion rule is load-bearing well beyond this case. Documenting `TipView` as the condition to key on solves the hand-written case here without that change.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — require `PopoverDismissRegion` and `TipView` together in `dismiss_blocking_tip()`.
- [x] Unit 2 — a `confirmationDialog` fixture in the showcase app and the tipkit scenario that pins
      it as left alone.
- [x] Unit 3 — unit tests for both halves of the pair, in the driver, the fake, and the orchestrator
      guard.
- [x] Unit 4 — correct the guard's description in `docs/scenarios.md` and `docs/architecture.md`,
      both languages, and name `TipView` as the condition a hand-written `interrupts` entry uses.

Verified on a booted Simulator with `demos/showcase/scenarios/tipkit.yaml`: all four scenarios pass
with the pair required, and reverting the driver to the scrim alone fails the new one — its manifest
records `tap PopoverDismissRegion [0, 0, 402, 874]` inside the settle step, and the assertion that
follows reports `expected present but was absent`. The new scenario is therefore load-bearing rather
than incidentally passing.

Two shapes of that scenario do not work, and both reasons are worth recording. Waiting on the
dialog's own button (`wait: { for: { label: "Remove note" } }`) passes even with the unfixed driver,
because the mid-wait dismiss hook runs only while a wait is still blocked, and a wait for a button
already on screen succeeds on its first tick without ever asking the guard; the scenario settles
instead, which keeps the tree polled while the dialog is up. Reaching the dialog's own opener without
scrolling first also works by accident: the button resolves at the very bottom of the screen, behind
the floating tab bar, and the tap lands only because the driver recovers with a scroll of its own —
the manifest recorded a failed tap at `y = 828.7`, a scroll, then a retry at `y = 174.3`. An explicit
`scroll` puts the tap on the first attempt at `y = 171`, so the fixture no longer depends on where the
fold falls.

## References

- [BE-0389](../BE-0389-ios-native-tipkit-guard/BE-0389-ios-native-tipkit-guard.md) — the guard this
  narrows, and the source of the follow-up this item carries out.
- [BE-0314](../BE-0314-scenario-interrupt-handlers/BE-0314-scenario-interrupt-handlers.md) — the
  `interrupts` handlers a hand-written tip condition is expressed in.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — the only place in
  `bajutsu/` the TipKit identifiers appear.
- [`demos/showcase/scenarios/tipkit.yaml`](../../demos/showcase/scenarios/tipkit.yaml) — the
  on-device scenarios for the guard.

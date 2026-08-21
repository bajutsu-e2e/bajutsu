**English** · [日本語](BE-XXXX-vision-alert-guard-native-actuation-ja.md)

# BE-XXXX — Actuate the vision alert guard's decision through the native SpringBoard tap

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-vision-alert-guard-native-actuation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

On iOS the vision alert guard cannot clear a SpringBoard prompt, whatever it decides. The guard
(`bajutsu/agents/alerts.py`) ends in `driver.tap_point`, an app-scoped XCUITest coordinate tap, and a
SpringBoard prompt belongs to another process — the very reason
[BE-0316](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md) queries and taps
a second `XCUIApplication`. Measured behavior is worse than a tap that misses: performing *any*
app-scoped interaction while a prompt is up makes XCTest resolve the prompt with its own default
button, which for a permission request is the *granting* one. This item routes the guard's decision
through BE-0316's native SpringBoard tap, keeping vision for identifying the button and giving the
actuation to the path that provably works.

## Motivation

Three measurements on the showcase SwiftUI app (iPhone 17, iOS 26.5) separate the hypotheses. With
the notification prompt up and nothing touched, it stayed up across fifteen polls and the app kept
reporting `notDetermined` — so nothing dismisses it on its own. A coordinate tap at `(5, 5)`, nowhere
near either button, cleared it and left the app reporting `authorized`. A coordinate tap aimed at the
exact centre of `Don’t Allow`, `(127, 518)`, did the same: `authorized`. Meanwhile a coordinate tap on
the app's *own* dialog worked normally, landing on the button under it. The conclusion the three
readings force is that the coordinate never decides anything while a SpringBoard prompt is up: the
interaction itself resolves the prompt, and it resolves it by the prompt's default.

So the guard's advertised behavior and its actual behavior are opposites. Its documented default is
"the dismissive, least-destructive button" — `Not Now`, `Don’t Allow`, `Cancel` — and what a run
actually gets is the granting default: a permission the scenario never asked for, silently granted
mid-run. The run's report meanwhile records the button the *model* chose, so the evidence names a
`Don’t Allow` that was never pressed. A test tool that reports the opposite of what happened is worse
than one that fails, which is what makes this more than a missing feature.

The vision path is not dead code that no longer matters.
[BE-0315](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
made the native path primary and kept vision as the fallback for exactly the cases the native path
cannot handle: a backend without the capability, a blocking surface that is not an enumerable
`springboard.alerts` alert, or an alert carrying no button the policy names. That third case is a
genuine iOS SpringBoard alert reached through the vision path — precisely where the coordinate tap
cannot work. The fallback is therefore live, and on iOS it is inert.

The vision *judgment*, by contrast, is sound and measured to be so.
[BE-0308](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md)
showed a real model answering the notification prompt within half a point of `Don’t Allow`'s centre,
picking the bottom of the location prompt's three stacked choices, and correctly reporting no prompt
at all on the app's own delete confirmation rather than reaching for its red `Delete`. What needs
replacing is the actuation beneath a decision that is already right, not the decision.

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **Give the guard a native actuation route where the backend has one.** The locator already returns
  a `label` beside its coordinates (`AlertDecision.label`), and BE-0316's `handle_system_alert`
  already taps a SpringBoard button by label under `resolve_unique`'s zero / ambiguous / unique
  discipline. When the backend advertises `HANDLE_SYSTEM_ALERT` and a prompt is enumerable, the guard
  should pass that label to the native tap instead of tapping a coordinate. Vision then answers
  "which button", and the deterministic path answers "press it" — which also removes a blind
  coordinate tap in favour of a selector that fails loudly when it matches nothing or several things
  (prime directive 2).
- **Keep the coordinate tap for the surfaces it genuinely serves, and say which those are.** The
  measurement above found a coordinate tap working normally on the app's own dialog, so the
  coordinate path remains correct for a blocking surface that is *not* an out-of-process prompt — an
  in-app overlay, and the web backend's own dialogs. The unit is to draw that boundary explicitly
  rather than leave one path silently covering both.
- **Fail loudly when neither route can act.** Today an unclearable prompt produces a tap, an
  `AlertEvent` naming a button that was never pressed, and a run that continues. Whatever the
  boundary above decides, the case where the guard cannot actuate must be reported as such rather
  than recorded as a dismissal — the guard may still decline to crash the run (it is best-effort by
  design), but it must not claim an action it did not perform.
- **An on-device check that a vision decision really clears the prompt.** The fast suite cannot see
  this defect at all: every existing test hands the guard a `FakeDriver`, whose `tap_point` records a
  coordinate rather than driving XCTest, so the whole class of failure is invisible off-device. A
  scenario that reaches the vision path against a real SpringBoard prompt and asserts the resulting
  authorization state — `denied`, not `authorized` — is what would have caught this, and is what
  keeps it caught.

Nothing here puts a model on the `run` verdict. The guard stays a Tier 1 live-AI operation whose
output is an action, never a pass/fail judgment, and the change moves that action onto a
deterministic primitive rather than adding a model anywhere new.

## Alternatives considered

- **Fix the coordinate tap so it reaches SpringBoard.** The tap is issued as an offset from the app's
  own origin (`app.coordinate(withNormalizedOffset:).withOffset(...)` in the runner), and the
  measurement shows the interaction is intercepted before the coordinate matters, so there is no
  offset that makes it land. Reaching SpringBoard means addressing SpringBoard, which is what
  BE-0316's second `XCUIApplication` already does.
- **Drop the vision fallback on iOS entirely, leaving only BE-0315's native path.** This is the
  honest floor if the unit above finds no native route, and it should be named as the fallback
  position rather than the plan: it would remove the safety net for an alert whose button the
  deterministic policy does not name, which is the case the vision path exists for. Reusing the
  native tap keeps that net instead of cutting it.
- **Have the guard tap by label through the app-scoped tree rather than SpringBoard's.** The app's
  accessibility query cannot see a SpringBoard prompt at all — the premise `agents/alerts.py`'s own
  module docstring opens with — so there is no element to resolve a label against on that side.
- **Widen BE-0276's `permissions` pre-grant so no prompt ever appears.** BE-0315 already rejected
  this for the prompts at issue: notification authorization is not a Transparency, Consent, and
  Control (TCC) service, and neither App Tracking Transparency nor the cross-process paste consent
  has a `simctl` toggle, so a reactive path for them is unavoidable.
- **Treat XCTest's own prompt resolution as the feature rather than the bug.** It does clear the
  prompt, so a reading exists in which nothing is broken. It presses the prompt's default, though,
  which is the granting choice for a permission request and the opposite of the guard's documented
  least-destructive default — so accepting it would mean a run silently granting permissions the
  scenario never asked for, and a report naming a button that was never pressed.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Route the guard's decision through BE-0316's native label tap where the backend has one.
- [ ] Draw the boundary for the surfaces a coordinate tap still serves.
- [ ] Report an unactuatable prompt as such, rather than recording a dismissal that did not happen.
- [ ] Add an on-device check that a vision decision really clears a real SpringBoard prompt.

## References

- [BE-0316 — Explicit mid-flow step for iOS permission-prompt alerts](../BE-0316-ios-permission-alert-step/BE-0316-ios-permission-alert-step.md)
  — its `/systemAlert/query` + `/systemAlert/tap` on a second `XCUIApplication` is the working
  actuation this item reuses
- [BE-0315 — Make the reactive alert guard deterministic and native, reusing BE-0316's SpringBoard path](../BE-0315-ios-native-system-alert-handling/BE-0315-ios-native-system-alert-handling.md)
  — it made the native path primary and defined the three cases that still fall through to vision
- [BE-0308 — Real-model verification of the system-alert guard](../BE-0308-alerts-guard-real-model-verification/BE-0308-alerts-guard-real-model-verification.md)
  — its capture measured this defect, and separately measured the vision decision to be accurate
- [BE-0269 — Speed up the system-alert guard's intervention during wait steps](../BE-0269-ios-alert-guard-early-wait-intervention/BE-0269-ios-alert-guard-early-wait-intervention.md)
  — the gate the guard fires from, unchanged by this item
- The sibling proposal *Map a normalized coordinate through the viewport rather than the element
  tree's extent* (`normalized-coordinate-viewport-mapping`), which fixes the other defect BE-0308
  measured: the guard scales a normalized answer by the element tree's extent rather than the
  screen's. That mapping is wrong independently of this item, and correcting it alone would not make
  the iOS vision guard work, since the coordinate it produces still cannot reach a SpringBoard
  prompt. Neither item depends on the other landing first.
- `bajutsu/agents/alerts.py` (`SystemAlertGuard.dismiss`), `bajutsu/drivers/base.py`
  (`handle_system_alert`, `system_alert_labels`, `Capability.HANDLE_SYSTEM_ALERT`),
  `bajutsu/drivers/xcuitest.py`, `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`
  (`tapPoint` / `coordinate`), `bajutsu/orchestrator/waits.py` (`_AlertGuardGate`)

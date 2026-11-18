**English** · [日本語](BE-0373-xcuitest-refused-tap-descendant-redirect-ja.md)

# BE-0373 — Offer the one reachable named descendant when a container refuses a tap

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0373](BE-0373-xcuitest-refused-tap-descendant-redirect.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0373") |
| Implementing PR | [#1571](https://github.com/bajutsu-e2e/bajutsu/pull/1571) |
| Topic | Platform support |
| Related | [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md), [BE-0285](../BE-0285-scenario-feature-real-backend-coverage/BE-0285-scenario-feature-real-backend-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

iOS can report an accessibility element **inflated over the control it wraps**. The element refuses a
tap, yet the control inside it is perfectly reachable. This item teaches the XCUITest backend to
notice that shape.

Suppose a uniquely resolved target is refused. The
[driver](../../docs/glossary.md#driver-backend-actuator-platform) then probes the **named
descendants** of the target. A named descendant is an element that comes after the target in document
order, lies inside its frame, and carries an identifier.

- If **exactly one** named descendant is reachable, the driver taps it and records why.
- If none or several are reachable, the driver fails and **names the candidates**. It does not choose
  between them. The tap has no single meaning in that case, and picking one would be the guess that
  prime directive 2 forbids.

The record carries the reason on a new `Actuation.substitution` field. Neither the report nor the
`trace` timeline lets a redirected tap read like an ordinary one.

## Motivation

`tap: { id: log.count }` on the Log tab of the showcase fails on an iOS 18.6 Simulator against the
SwiftUI build:

```
still not tappable after a bounded scroll attempt: element resolved but not hittable: {'id': ['log.count', 'log_count']}
```

The element is on screen at `[16, 268, 358, 44]`, and it resolved uniquely. What XCTest refuses is a
SwiftUI `Stepper` whose accessibility element is inflated to the whole form row. The frame equals the
frame of the enclosing cell. The two buttons inside it answer `isHittable` with `ok`. We measured all
three on the device at once:

| element | traits | frame | `POST /isHittable` |
|---|---|---|---|
| `log.count` | `other` | `[16, 268, 358, 44]` | `not-hittable` |
| `log.count-Decrement` | `button` | `[264, 274, 46.5, 32]` | `ok` |
| `log.count-Increment` | `button` | `[311.5, 274, 46.5, 32]` | `ok` |

The only recovery today is the bounded scroll that
[BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md) added
above the driver, and it cannot help. The center of the target is already in the viewport. Every
scroll step re-asks the same question and gets the same answer until the budget runs out.

The failure a reader sees names the container and says "not hittable" about an element the reader can
see. It says nothing about the two controls inside it that would have worked.

**The shape is not rare, and it is not the quirk of one OS.** XCUITest synthesizes `-Increment` and
`-Decrement` from the identifier of a stepper on every iOS version we measured. The inflated frame is
a difference of *toolkit*, not of version. The `UIStepper` of the UIKit build reports its own 94×32
rect and is hittable. The stepper of the SwiftUI build spans the row and is not. Any container that an
app labels can present the same way. Examples are a `Stepper`, a labelled row wrapping a single
switch, and a card whose only control is a button.

Leaving the defect costs twice.

- A scenario author reads a refusal about an element that is plainly there. The message gives no way
  to learn that `log.count-Increment` exists.
- The bounded scroll spends its whole budget first. The failure thus arrives slowly on a metered
  on-device lane.

## Detailed design

The fix sits in `tap` on the XCUITest driver ([`xcuitest.py`](../../bajutsu/drivers/xcuitest.py)). It
catches the `ElementNotTappable` that `_actuate` raises for a `not-hittable` reply. The candidate
arithmetic is a pure function in [`base.py`](../../bajutsu/drivers/base.py), beside the shared driver
helpers.

**Which elements may be offered.** `redirect_candidates(elements, target)` is the mirror image of
`topmost_at_point`. It scans the same after-`target` slice and keeps exactly what that function
throws away. Three conditions apply. Each one rules out a way the offer could be wrong:

- **After `target` in document order.** `Element` carries no parent pointer. Geometry alone cannot
  tell a descendant from an ancestor, or from an unrelated overlay that encloses the same frame. A
  pre-order walk always emits an ancestor before its descendants. No frame check can make that
  discrimination. `topmost_at_point` already spells out the same reasoning for the opposite case.
- **Inside the frame of `target`**, edge-inclusive. An equal frame counts. One control registered
  twice at one place is as legitimate an offer as a smaller child. It still has to satisfy the third
  condition.
- **Carrying an identifier.** The offer is then always an element that the author could have named
  directly. That keeps a redirect from becoming a rewrite the author cannot predict. It answers the
  first of the three objections that
  [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md)
  raised against driver-side absorption and that still bear here. The condition also lets a refusal
  print the candidates it declined to choose between.

`MAX_REDIRECT_CANDIDATES` bounds the group at four. Above that, a container is a layout region, not a
control with one actuatable child. Each probe is a round trip. The driver refuses without spending
any.

**When the tap moves.** With exactly one reachable candidate, no choice exists, so the tap goes there.
With none or several, a choice does exist, so the driver re-raises. The message then names every
candidate. That is the actionable half of this item.

A real `Stepper` has two reachable children. `tap: { id: log.count }` therefore has no single
meaning. Telling the author which two ids to choose between is worth more than any pick the driver
could make. The re-raise chains the original refusal, so the cause is preserved.

The redirect is scoped to `tap`. A long-press or a two-finger gesture redirected to a child is a
different intent. It is not the same intent reaching its target.

**The child is actuated by its own id.** `_actuate` re-resolves from the selector it was handed on a
stale retry (BE-0289). Passing the selector of the container would silently undo the redirect on that
retry.

**Why this is not the implicit rewrite BE-0221 rejected.** That item refused a driver-side `.`↔`_` id
transform for four reasons.

- The fourth reason does not transfer. A driver transform would not reach the assertion, `wait`, and
  `forEach` paths, which resolve against `query()` output *outside* the driver. This item touches
  actuation alone, and an assertion about the container still evaluates against the container.
- The other three reasons are answered by construction:
  - The redirect is not implicit, because the record carries an explicit token.
  - It cannot conflate distinct ids, because it never invents a name. It only actuates an element
    already in the tree, inside the own frame of the target, whose identifier resolves uniquely.
  - It is predictable. The offer must be named. Where more than one candidate exists, the driver
    refuses instead of choosing.

The supporting precedent is `_collapse_identical_duplicates`. It absorbs an XCUITest artifact in the
shared core, with its own stated guard against over-absorption.

**Making the substitution visible.** `_actuate` records before the transport answers. A redirect
therefore already leaves a refused-then-accepted pair. But a reader would have to diff that pair
against the selector of the step to tell a deliberate redirect from a stale re-resolution.

`Actuation` gains an optional `substitution`, drawn from a fixed `SUBSTITUTIONS` tuple. The manifest
moves to `schemaVersion` 7. The token is not a new `via` value. `CHANNELS` answers *how the gesture
reached its target*, and a redirected tap still travels by `handle`. What changed is *which element*.

A fixed token also keeps rule 3 of the record true: never a string that a scenario authored. The
field is thus safe in an unredacted `manifest.json`. Both readers show it. The report shows a badge
beside `via`. The `trace` timeline shows `↷<token>`.

**The fixture on which the defect was found is corrected too, separately from the driver.**
`log.count` names a different thing per platform. On Android it is the increment control itself. On
iOS it is the container of the Stepper.

`extract.yaml` now lists `log.count-Increment` and pins `traits: [button]`. On iOS the trait drops the
container (`other`) and leaves exactly the increment. On Android the one button that the id names is
already a `button`.

That is a scenario saying what it means. It is also the BE-0221-consistent resolution for a selector
with two possible meanings. The redirect of the driver serves the container that an author has no
better way to address. It is not a licence to leave an ambiguous selector in place.

**Tests.** The candidate arithmetic is pure, and the tests cover it from the measured iOS 18.6 tree.
Eight elements sit inside the container after it, and only the two that the platform named survive.
The tests also cover the ancestor, the outside-the-frame, the equal-frame, and the
not-in-the-list-by-identity cases.

The five outcomes of the driver are pinned against a fake transport built from the same tree:

1. one reachable child
2. two reachable children (the real Stepper, asserting that both ids appear in the message)
3. none
4. no named descendant at all
5. a container crowded past the cap, which must spend no probe

The evidence field is covered in four ways: a round trip, an older manifest loading as `None`, a
malformed value degrading without dropping the record, and the token reaching both the rendered
report and the trace timeline.

## Alternatives considered

**Fix it in the fixture alone.** Retargeting `extract.yaml` at `log.count-Increment` makes the
showcase pass and needs no driver change. This item does that too, because a scenario should say what
it means. But it leaves the general case untouched. An app with a labelled container wrapping a
single control would still refuse a tap on the only element the author can name. The message would
say nothing about the child.

**Let the orchestrator recover.** `_tap_with_recovery` already catches `ElementNotTappable`. But it
can re-address a target only by *selector*. It would have to synthesize a selector from geometry,
which reintroduces the ambiguity the driver already holds handles for. The driver has the
per-snapshot handle map and the only native hittability oracle. The orchestrator has neither.

**Pick a child when several are reachable.** Taking the first, or the leading one, would have made
the Stepper tap of the showcase "work" with no scenario change. It would also have made
`tap: { id: log.count }` mean "increment" on iOS. On a build that happened to emit the buttons in
the other order, it would mean "decrement". Nothing in the scenario would say which. That is the
author-unpredictability that BE-0221 named, and a determinism violation in substance if not in form.

**Classify `.stepper` in the `typeName` of the runner.** The container falls through to the `other`
trait because `typeName` does not name `XCUIElementType.stepper`. Classifying it looks like the
version-agnostic fix. Measurement disproves it. The trait token would change, but the frame and
`isHittable` would not, so the tap stays refused. The classification would still be worth doing for
the `other`-trait tie-drop in `resolve_unique`, which is a different problem.

**Probe the hittability of every element up front.** This would make the refused case a special case
of nothing. But it would cost one native call per element on every query, and most of those elements
never take part in a refusal. Scoping the probe to the own descendants of a refused tap keeps the
cost proportional to how often this shape occurs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Measure the shape on a device before designing to it: probe `log.count` and both of its
      synthesized children with `POST /isHittable` on an iOS 18.6 Simulator.
- [x] `Actuation.substitution` end to end: the `SUBSTITUTIONS` tuple, `schemaVersion` 7, the degrade
      of the loader, the report badge, and the `trace` segment.
- [x] `base.redirect_candidates` and `MAX_REDIRECT_CANDIDATES`, pure and covered from the measured
      tree.
- [x] The redirect of the driver in `tap` of `xcuitest.py`, with all five outcomes pinned.
- [x] `extract.yaml` names the increment. `docs/selectors.md`, `docs/drivers.md`, `docs/evidence.md`,
      and `docs/reporting.md`, with their `docs/ja/` mirrors, describe the redirect and the new field.

Log:

- The measurement that shaped the item came from an iOS 18.6 Simulator against the SwiftUI showcase.
  `log.count` answers `not-hittable`. **Both** `log.count-Increment` and `log.count-Decrement` answer
  `ok`.
  - Two reachable children is the "several" case. The redirect deliberately does not fire for the
    very control that prompted it. The actionable outcome there is the message naming both ids, and
    the scenario naming the one it meant.
  - A second measurement found the inflated frame to be a difference of toolkit, not of version. The
    `UIStepper` of the UIKit build reports its own 94×32 rect and is hittable. Its center falls in the
    1pt divider between the two halves.
- Verified on device after the change: `extract.yaml` passes on `showcase-swiftui` and
  `showcase-uikit` × iOS 18.6 and 26.5. The SwiftUI/18.6 combination failed before. The manifest
  shows the three taps landing on `log.count-Increment` and records no substitution. The scenario now
  names what it means, so the redirect never has to fire.

## References

- [BE-0349 — Verify tappability before acting, with a bounded scroll safety net](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md):
  introduced `isHittable`, `TapResult.notHittable`, and the bounded scroll this item follows when
  that scroll cannot help, because the target is already centred.
- [BE-0221 — Android scenario portability guarantee](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md):
  rejected a driver-side implicit id rewrite for four reasons; three of them bear here and are
  answered by the guard, and its "say it in the scenario" resolution is what `extract.yaml` adopts.
- [BE-0285 — extract real-backend coverage](../BE-0285-scenario-feature-real-backend-coverage/BE-0285-scenario-feature-real-backend-coverage.md):
  owns `extract.yaml`, the scenario whose counter tap surfaced this and whose selector this item
  corrects.
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py): `redirect_candidates`,
  `MAX_REDIRECT_CANDIDATES`, and `topmost_at_point`, whose after-target scan this one mirrors.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py): `tap` and
  `_tap_sole_reachable_descendant`.
- [`bajutsu/drivers/actuation.py`](../../bajutsu/drivers/actuation.py): `SUBSTITUTIONS` and the
  record's new field.

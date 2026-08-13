**English** · [日本語](BE-XXXX-xcuitest-refused-tap-descendant-redirect-ja.md)

# BE-XXXX — Offer the one reachable named descendant when a container refuses a tap

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-refused-tap-descendant-redirect.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1571](https://github.com/bajutsu-e2e/bajutsu/pull/1571) |
| Topic | Platform support |
| Related | [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md), [BE-0285](../BE-0285-scenario-feature-real-backend-coverage/BE-0285-scenario-feature-real-backend-coverage.md) |
<!-- /BE-METADATA -->

## Introduction

iOS can report an accessibility element **inflated over the control it wraps**, refuse a tap on it,
and still expose the control inside it as perfectly reachable. This item teaches the XCUITest
backend to notice that shape: when a uniquely-resolved target is refused, the driver probes the
target's **named descendants** — the elements emitted after it, inside its frame, carrying an
identifier — and where **exactly one** is reachable it taps that one, recording why. Where none or
several are, it fails and **names the candidates** rather than choosing between them, because there
the tap has no single meaning and picking one would be the guess prime directive 2 forbids. The
record carries the reason on a new `Actuation.substitution` field, so neither the report nor the
`trace` timeline lets a redirected tap read like an ordinary one.

## Motivation

`tap: { id: log.count }` on the showcase's Log tab fails on an iOS 18.6 Simulator against the
SwiftUI build:

```
still not tappable after a bounded scroll attempt: element resolved but not hittable: {'id': ['log.count', 'log_count']}
```

The element is on screen, at `[16, 268, 358, 44]`, and it resolved uniquely. What XCTest refuses is
a SwiftUI `Stepper` whose accessibility element is inflated to the whole form row — the same frame
as the enclosing cell — while the two buttons inside it answer `isHittable` with `ok`. Measured on
the device, all three at once:

| element | traits | frame | `POST /isHittable` |
|---|---|---|---|
| `log.count` | `other` | `[16, 268, 358, 44]` | `not-hittable` |
| `log.count-Decrement` | `button` | `[264, 274, 46.5, 32]` | `ok` |
| `log.count-Increment` | `button` | `[311.5, 274, 46.5, 32]` | `ok` |

The bounded scroll [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md)
added above the driver is the only recovery today, and it cannot help: the target's centre is
already in the viewport, so every scroll step re-asks the same question and gets the same answer
until the budget runs out. The failure a reader sees names the container and says "not hittable"
about an element they can see, and says nothing about the two controls inside it that would have
worked.

**The shape is not rare and not one OS's quirk.** XCUITest synthesizes `-Increment` / `-Decrement`
from a stepper's own identifier on every iOS version measured, and the inflated frame is a *toolkit*
difference rather than a version one: the UIKit build's `UIStepper` reports its own 94×32 rect and
is hittable, where the SwiftUI build's spans the row and is not. Any container an app labels — a
`Stepper`, a labelled row wrapping a single switch, a card whose only control is a button — can
present the same way.

Leaving it costs twice. A scenario author reads a refusal about an element that is plainly there and
has no way, from the message, to learn that `log.count-Increment` exists. And the bounded scroll
spends its whole budget first, so the failure arrives slowly on a metered on-device lane.

## Detailed design

The fix sits in `tap` on the XCUITest driver
([`xcuitest.py`](../../bajutsu/drivers/xcuitest.py)), catching the `ElementNotTappable` that
`_actuate` raises for a `not-hittable` reply. The candidate arithmetic is a pure function in
[`base.py`](../../bajutsu/drivers/base.py), beside the shared driver helpers.

**Which elements may be offered.** `redirect_candidates(elements, target)` is the mirror image of
`topmost_at_point`, scanning the same after-`target` slice and keeping exactly what that function
throws away. Three conditions, each ruling out a way the offer could be wrong:

- **After `target` in document order.** `Element` carries no parent pointer, so geometry alone
  cannot tell a descendant from an ancestor or from an unrelated overlay enclosing the same frame. A
  pre-order walk always emits an ancestor before its descendants, which is the discrimination no
  frame check can make — the reasoning `topmost_at_point` already spells out for the opposite case.
- **Inside `target`'s frame**, edge-inclusive. An equal frame counts: one control registered twice at
  one place is as legitimate an offer as a smaller child, and it still has to satisfy the third.
- **Carrying an identifier.** The offer is then always an element the author could have named
  directly. That is what keeps a redirect from being a rewrite the author cannot predict — the first
  of the three objections [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md)
  raised against driver-side absorption that still bear here — and it is what lets a refusal print
  the candidates it declined to choose between.

`MAX_REDIRECT_CANDIDATES` bounds the group at four. Above that, a container is a layout region
rather than a control with one actuatable child, and each probe is a round trip; the driver refuses
without spending any.

**When the tap moves.** Exactly one reachable candidate and no choice exists, so the tap goes there.
None or several and a choice does exist, so the driver re-raises. The message then names every
candidate, which is the actionable half of this item: a real `Stepper` has two reachable children,
so `tap: { id: log.count }` genuinely has no single meaning, and telling the author which two ids to
choose between is worth more than any pick the driver could make. The re-raise chains the original
refusal, so the cause is preserved.

Scoped to `tap`. A long-press or a two-finger gesture redirected to a child is a different intent,
not the same intent reaching its target.

**The child is actuated by its own id.** `_actuate` re-resolves from the selector it was handed on a
stale retry (BE-0289), so passing the container's selector would silently undo the redirect on that
retry.

**Why this is not the implicit rewrite BE-0221 rejected.** That item refused a driver-side `.`↔`_`
id transform for four reasons. The fourth — a driver transform would not reach the assertion /
`wait` / `forEach` paths, which resolve against `query()` output *outside* the driver — does not
transfer: this touches actuation only, and an assertion about the container still evaluates against
the container. The other three are answered by construction. It is not implicit, because the record
carries an explicit token. It cannot conflate distinct ids, because it never invents a name: it only
ever actuates an element already in the tree, inside the target's own frame, whose identifier
resolves uniquely. And it is predictable, because the offer must be named and, where more than one
is, the driver refuses instead of choosing. The supporting precedent is `_collapse_identical_duplicates`,
an XCUITest artifact absorbed in the shared core with its own stated guard against over-absorption.

**Making the substitution visible.** `_actuate` records before the transport answers, so a redirect
already leaves a refused-then-accepted pair — but a reader would have to diff that against the
step's selector to tell a deliberate redirect from a stale re-resolution. `Actuation` therefore
gains an optional `substitution`, drawn from a fixed `SUBSTITUTIONS` tuple, and the manifest moves
to `schemaVersion` 7. It is not a new `via` value: `CHANNELS` answers *how the gesture reached its
target*, and a redirected tap still travels by `handle` — what changed is *which element*. A fixed
token also keeps the record's rule 3 (never a string a scenario authored) true, so the field is safe
in an unredacted `manifest.json`. Both readers show it: the report as a badge beside `via`, the
`trace` timeline as `↷<token>`.

**The fixture the defect was found on is corrected too, separately from the driver.** `log.count`
names a different thing per platform: on Android it is the increment control itself, on iOS the
Stepper's container. `extract.yaml` now lists `log.count-Increment` and pins `traits: [button]`,
which drops the iOS container (`other`) and leaves exactly the increment, while on Android the one
button the id names is already a `button`. That is a scenario saying what it means, and it is the
BE-0221-consistent resolution for a selector with two possible meanings — the driver's redirect is
for the container an author has no better way to address, not a licence to leave an ambiguous
selector in place.

**Tests.** The candidate arithmetic is pure and covered from the measured iOS 18.6 tree: eight
elements sit inside the container after it, and only the two the platform named survive, plus the
ancestor, outside-the-frame, equal-frame, and not-in-the-list-by-identity cases. The driver's five
outcomes are pinned against a fake transport built from the same tree: one reachable child, two (the
real Stepper, asserting both ids appear in the message), none, no named descendant at all, and a
container crowded past the cap — which must spend no probe. The evidence field is covered by a
round trip, an older manifest loading as `None`, a malformed value degrading without dropping the
record, and the token reaching both the rendered report and the trace timeline.

## Alternatives considered

**Fix it in the fixture alone.** Retargeting `extract.yaml` at `log.count-Increment` makes the
showcase pass and needs no driver change — and this item does that too, because a scenario should
say what it means. It leaves the general case untouched, though: an app with a labelled container
wrapping a single control would still refuse a tap on the only element the author can name, with a
message that says nothing about the child.

**Let the orchestrator recover.** `_tap_with_recovery` already catches `ElementNotTappable`. It can
only re-address a target by *selector*, though, and would have to synthesize one from geometry,
reintroducing the ambiguity the driver already holds handles for. The driver has the per-snapshot
handle map and the only native hittability oracle; the orchestrator has neither.

**Pick a child when several are reachable.** Taking the first, or the leading one, would have made
the showcase's Stepper tap "work" with no scenario change. It would also have made
`tap: { id: log.count }` mean "increment" on iOS and "decrement" on a build that happened to emit
them in the other order, with nothing in the scenario saying which — the author-unpredictability
BE-0221 named, and a determinism violation in substance if not in form.

**Classify `.stepper` in the runner's `typeName`.** The container falls through to the `other` trait
because `typeName` does not name `XCUIElementType.stepper`, and classifying it looks like the
version-agnostic fix. Measurement disproves it: the trait token would change while the frame and
`isHittable` would not, so the tap stays refused. It would still be worth doing for
`resolve_unique`'s `other`-trait tie-drop, which is a different problem.

**Probe every element's hittability up front.** Making the refused case a special case of nothing
would cost one native call per element on every query, most of which never take part in a refusal.
Scoping the probe to a refused tap's own descendants keeps the cost proportional to how often this
shape actually occurs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Measure the shape on a device before designing to it: probe `log.count` and both of its
      synthesized children with `POST /isHittable` on an iOS 18.6 Simulator.
- [x] `Actuation.substitution` end to end — the `SUBSTITUTIONS` tuple, `schemaVersion` 7, the loader's
      degrade, the report badge, and the `trace` segment.
- [x] `base.redirect_candidates` and `MAX_REDIRECT_CANDIDATES`, pure and covered from the measured
      tree.
- [x] The driver's redirect in `xcuitest.py`'s `tap`, with all five outcomes pinned.
- [x] `extract.yaml` names the increment; `docs/selectors.md`, `docs/drivers.md`, `docs/evidence.md`,
      `docs/reporting.md` and their `docs/ja/` mirrors describe the redirect and the new field.

Log:

- The measurement that shaped the item, on an iOS 18.6 Simulator against the SwiftUI showcase:
  `log.count` answers `not-hittable`, and **both** `log.count-Increment` and `log.count-Decrement`
  answer `ok`. Two reachable children is the "several" case, so the redirect deliberately does not
  fire for the very control that prompted it — the actionable outcome there is the message naming
  both ids, and the scenario naming the one it meant. A second measurement found the inflated frame
  to be a toolkit difference rather than a version one: the UIKit build's `UIStepper` reports its own
  94×32 rect and is hittable, with its centre falling in the 1pt divider between the two halves.
- Verified on device after the change: `extract.yaml` passes on `showcase-swiftui` and
  `showcase-uikit` × iOS 18.6 and 26.5 — the SwiftUI/18.6 combination failed before — with the
  manifest showing the three taps landing on `log.count-Increment` and no substitution recorded,
  since the scenario now names what it means and the redirect never has to fire.

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

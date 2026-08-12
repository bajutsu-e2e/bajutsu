**English** · [日本語](BE-XXXX-xcuitest-duplicate-identity-resolution-ja.md)

# BE-XXXX — Resolve a content-identical duplicate node pair to one live element instead of failing the lookup

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-duplicate-identity-resolution.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1567](https://github.com/bajutsu-e2e/bajutsu/pull/1567) |
| Topic | Platform support |
| Related | [BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets a **content-identical duplicate node pair** — two entries the iOS accessibility tree
reports with the same identifier, label, traits, value, and frame, a known artifact of a standard
`UIAlertController` button — resolve to one live element, instead of failing every actuation on it
with `element vanished (stale handle)`. The runner that drives the app on the iOS backend
(XCUITest, Apple's own UI testing framework) turns a recorded element reference back into something
it can act on in two steps: it replays the element's recorded position in the tree, and, when that
replay no longer matches, falls back to a flat query for the recorded identity. That fallback,
`uniquelyIdentifiedElement` in
[`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift),
demands **exactly one** candidate and returns nothing on two — so a duplicate pair defeats the very
recovery meant to save it, and both members of the pair become unreachable. We propose to treat a
candidate group whose members are identical to *each other* down to the value and the frame as the
one physical control it really is, and resolve to a member of that group rather than give up.

## Motivation

On iOS 26 a `tap` on either button of a native alert fails, whether the scenario addresses the
button by `id` or by `label` and `traits`:

```
step 8 (tap): element vanished (stale handle): {'id': 'log.alert.ok'}
```

The same scenario passes on iOS 18. A spike against the showcase fixture measured the difference
directly: on iOS 26.5 the accessibility tree reports the alert's "OK" button as **two** entries with
the same identifier, label, traits, and frame, where iOS 18.6 reports one. Probing the runner's
`/isHittable` endpoint on each entry in turn, with the alert on screen, returned `stale` for all
four of the alert's button entries, while the alert container and its message text — ordinary,
non-duplicated nodes in the same subtree — returned `ok`. A raw coordinate tap at the button's own
frame centre then dismissed the alert and set the app's result mirror to `ok`.

Those three observations together locate the defect precisely. The pair is one genuinely tappable
control, since tapping its centre works. The channel and the handle bookkeeping are sound, since
neighbouring elements answer normally. What fails is resolution: neither member of the pair can be
turned back into an element the runner can act on at all.

Both steps of that resolution fail, and the second fails *because of* the duplication.
`liveElement(for:)` first replays the recorded position path — a root-relative index path recorded
over the snapshot — and accepts the element it lands on only if the identity still matches. A live
alert hierarchy carries system-owned wrapper nodes the snapshot walk did not, so replaying an index
path through it lands elsewhere — the recovery case the fallback's own code comment names, citing
the iOS Save Password sheet as the screen whose live wrapper nodes a snapshot's child indices cannot
be replayed through. That fallback then collects every live element matching the
recorded identifier and label and hands them to `uniqueMatchingIndex` in
[`PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift), whose documented
contract is to return the index of the **sole** candidate and `nil` otherwise: "Zero matches and
multiple matches both return nil: neither case identifies one element safely." A duplicate pair is
two matches, so the fallback returns nothing, `liveElement` returns nothing, and the runner reports
`stale`. On iOS 18 the same fallback runs and succeeds, because there is one candidate to be sole.

The `nil`-on-multiple rule is right for the case it was written for. Two elements that share an
identifier but denote different controls cannot be told apart by identity, and picking one would be
the guess that prime directive 2 forbids. A duplicate *pair*, though, is not that case: its members
agree on frame as well, so they describe one control at one place on screen, and choosing between
them is not a choice about which control to act on. Today the runner cannot express that
distinction, so it treats a redundant registration as an ambiguity and refuses a control that was
never ambiguous.

[BE-0357](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md)
set out to fix the same alert failure one layer up, by dropping whichever member of a duplicate
group is not hittable so only one candidate ever reaches the handle store. Its own text made that
plan conditional on a premise it had not yet measured — that exactly one member reports itself
hittable — and required a spike to confirm the premise before any code was written. The spike
above is that spike, and it disproved the premise: zero members report `ok`, not one, because
`isHittable` resolves through the same `liveElement` lookup this item repairs and therefore cannot
see either member either. Under its own specification BE-0357 would leave such a group untouched,
so it is a no-op for the failure it was written to remove. Its `Progress` records that outcome, and
its `Status` moves to `Proposal (deferred)` in the same change that lands this item. Repairing
resolution first is also what would give BE-0357 a signal to work with at all.

Leaving the defect unfixed costs what this failure class has cost before: a native alert is
ordinary in a real application, and a required on-device gate that fails on a button which never
left the screen erodes trust in the gate and burns metered macOS-runner minutes on a step that did
nothing wrong. It also costs coverage the suite has today — the showcase's own `alert.yaml` cannot
run on any iOS 26 device until resolution works.

## Detailed design

The fix belongs in the flat-query fallback, the one place that already holds every live candidate
for a recorded identity and can therefore compare the candidates with one another.

**Treat a candidate group that agrees on value and frame as one control.** `uniquelyIdentifiedElement` in
[`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)
keeps collecting candidates exactly as it does today, and the new function preserves
`uniqueMatchingIndex`'s answer for zero matches and for a sole match, so every screen that resolves
today resolves unchanged. The new rule takes effect only when **more than one** candidate matches:
if every matching candidate reports the same value and the same frame as every other, the group is a
redundant registration of one control, and the fallback resolves to a member of it rather than
returning nothing. A group whose members disagree on either stays unresolved, exactly as today,
because two controls reporting different content are a genuine ambiguity that a selector must fail
on rather than guess at.

Value and frame are the discriminators precisely because
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
excluded them from `attributesMatch`, and the two uses do not conflict. `attributesMatch` compares
what was **recorded** against what is **live now**, across a gap in which a settling screen
legitimately moves an element — BE-0287 measured a 49-point shift of an unchanged field being read
as stale — and a slider or text field legitimately changes value. The comparison this item adds is
between candidates read from **one live query** and weighed only **against one another**, where the
recorded side never enters and no gap of that kind opens.

Those candidates are nonetheless not sampled at one instant, which decides how exactly frames must
agree. `uniquelyIdentifiedElement` builds its candidate attributes with `candidates.map(...)`, and
each candidate's frame is its own XCUITest attribute fetch, so a screen still settling under the very
animation BE-0287 measured can report one control's two registrations a fraction of a point apart.
Frames therefore agree **within a point** rather than exactly, which is far below the distance
separating two controls that genuinely stand at two places on screen, and so costs the
loud-failure branch nothing.

**Key on the same fields the host does.** `_collapse_identical_duplicates` in
[`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) already collapses this artifact in an
`/elements` reply, keying on identifier, label, traits, value, and frame. This item's rule is the
runner-side twin, reached when a *recorded* handle is re-resolved at actuation time over a candidate
set no `/elements` reply gated, so it keys on the same fields and each docstring points at the other.
Keying on less would let the runner guess where the host fails loudly: two registrations of a
value-bearing control that disagree on value are an ambiguity on the host side, and must stay one on
this side too. `RecordedAttributes` therefore gains the `value` the flattened `ElementSnapshot`
already reports, used by the group rule alone and still left out of `attributesMatch`.

**Which member to resolve to is a question the implementation must answer with a measurement, not
an assumption** — the mistake this item exists to correct. Two candidate rules are open: return the
first matching member, which is the cheapest and adds no native call; or probe each member with
`isHittable(backingElement:)` and prefer a hittable one, falling back to the first when the probe
distinguishes nothing. The first rule is correct if both members can be actuated, and the second is
needed if one of them cannot. Nothing measured so far settles the question, because the members
the spike probed were reached through the position path rather than through a flat query, and
`allElementsBoundByIndex` — the flat query's own accessor — binds each candidate by index into a
live query rather than by a recorded tree position. So the first unit of work below is a spike that
taps a duplicate alert button through each member of the flat-query group in turn and reports which
of them actuate. Should the spike find that neither member actuates, the premise of this design
fails in the way BE-0357's did, and the design needs revisiting rather than implementing.

**Scope.** `uniquelyIdentifiedElement` is reached from `liveElement(for:)`, which every actuation
and the hittability probe already share, so `tap`, `gesture`, and `isHittable` all gain the repair
from one change and none of them needs its own. The `/elements` reply is untouched: this item
changes how a recorded reference resolves back to a live element, not what the tree reports, so a
duplicate pair still appears as two entries and a `count` assertion over that identity still reports
two. Whether the reply should also collapse such a pair is the question BE-0357 asks, and this item
deliberately leaves it there rather than answering it in passing. The handle store
([BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md))
is likewise untouched, since both members keep their own handles and each now resolves.

**Where the rule lives.** `uniqueMatchingIndex` is a pure function in the device-agnostic
`BajutsuRunner` library, called from one place in the XCUITest-specific provider. Adding a second
pure function beside it — one that reports the index to use for a candidate list, given the
group-agrees-on-frame rule — keeps the decision off-device-testable and leaves the existing function
and its three tests untouched. The provider now calls the new function in place of
`uniqueMatchingIndex`, whose zero- and sole-match behaviour it subsumes; the stricter function stays
beside it for any caller that wants uniqueness alone.

**Tests.** The rule is pure list logic, so `PositionPathTests.swift` covers it with no Simulator:
two candidates identical including value and frame resolve to one; two candidates matching on
identity but differing in frame, and two differing only in value, stay unresolved; two whose frames
differ by a fraction of a point still resolve to one; one candidate resolves as it does today; zero
candidates stay unresolved. On-device coverage is the showcase's existing `alert.yaml`, which
reproduces the failure today and must pass on an iOS 26 Simulator afterwards while continuing to
pass on iOS 18.

## Alternatives considered

**Drop the non-hittable member from the `/elements` reply (BE-0357).** Filtering the reply so only
one candidate ever reaches the handle store needs a signal that distinguishes the members, and the
spike in *Motivation* shows there is none to be had: `isHittable` reports `stale` for both, because
it resolves through the very lookup that is broken. Even after this item lands, both members would
resolve and both would report `ok`, which is BE-0357's own leave-untouched case. The two items do
not compose for the alert; BE-0357 stands or falls on a duplicate pair whose members a live probe
can actually tell apart.

**Make the position path replay survive the alert hierarchy.** Repairing the first resolution step
would keep the flat query from ever being reached here. A snapshot's child indices and a live
hierarchy's wrapper nodes disagree in a way that cannot be reconciled in general, though — the
reason the flat-query fallback exists beside the replay rather than the replay being fixed — so this
path fights a battle already judged lost, and would leave every other screen that reaches the
fallback with a duplicate still failing.

**Fall back to a coordinate tap when resolution fails.** Tapping the recorded frame centre works,
as the spike showed. It also turns a genuinely vanished element into a blind tap at whatever now
occupies its former position, which is the stability-ladder descent Bajutsu's design exists to
avoid, and it would hide the `stale` outcome the retry in
[BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md)
depends on.

**Relax `uniqueMatchingIndex` itself to return the first of several matches.** Changing the
existing function rather than adding one beside it would silently widen every caller's contract to
"guess when ambiguous", including the strict-uniqueness use its own docstring promises. The group
rule needs agreement on value and frame, neither of which that function ever compares; folding a
rule sensitive to both into an identity check blind to both would leave one function answering two
different questions.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Spike (gates the rest): on an iOS 26 Simulator with a `UIAlertController` presented, actuate
      the duplicate button through each member of the flat-query candidate group in turn and record
      which members actuate. One member actuating settles the selection rule as prefer-hittable;
      both actuating settles it as take-the-first; neither actuating disproves this design's premise
      and needs the design revisited rather than implemented. **Ran as an experiment rather than the
      per-member probe**, because the probe cannot be run — see the log below.
- [x] Add the group-resolution function beside `uniqueMatchingIndex` in `PositionPath.swift`,
      applying the selection rule the spike settled, and call it from `uniquelyIdentifiedElement` in
      `XcuitestElementProvider.swift` in place of `uniqueMatchingIndex`, whose zero- and one-match
      behaviour it preserves. Leave `uniqueMatchingIndex`, `attributesMatch`, the `/elements` reply,
      and `SnapshotStore` unchanged.
- [x] Key the group rule on the fields `_collapse_identical_duplicates` keys on: add `value` to
      `RecordedAttributes`, require it alongside frame in the group check, and cross-reference the
      two definitions from each other's docstring so a later change to one is visible from the other.
      Keep `value` out of `attributesMatch`.
- [x] Off-device tests in `PositionPathTests.swift`: two candidates identical in value and frame
      resolve to one; two candidates matching on identity but differing in frame, and two differing
      only in value, stay unresolved; two whose frames differ by a fraction of a point still resolve
      to one; one candidate and zero candidates behave as they do today.
- [x] On-device verification: `demos/showcase/scenarios/alert.yaml` passes against
      `showcase-swiftui` and `showcase-uikit` on an iOS 26 Simulator, and still passes on iOS 18.
- [x] Record the outcome in BE-0357: its `Progress` carries the disproved premise and the spike
      evidence, and its `Status` moves to `Proposal (deferred)`. Update
      `attributesMatch`'s neighbouring documentation if it now reads as though identity alone ever
      decides a flat-query match.

Log:

- The per-member probe this unit specifies cannot be run, in either direction: before the fix
  neither member of the pair resolves at all, which is the defect itself, and after it either
  member's flat-query fallback resolves to the group's first element, so actuating "through the
  second member" taps the element the first one resolves to. The spike therefore ran as an
  experiment: the take-the-first rule went in, and `alert.yaml` ran against both showcase fixtures
  on an iOS 26.5 Simulator. Both passed. That establishes the first member actuates and
  take-the-first suffices; a prefer-hittable rule could not have distinguished the members anyway,
  so `resolvableMatchingIndex` needs no native hittability call and the group rule stays a pure
  function on the off-device gate. The same scenario still passes on iOS 18.6, where the pair never
  appears and the sole-match path is unchanged.
- Review on [#1567](https://github.com/bajutsu-e2e/bajutsu/pull/1567) corrected two premises the
  first implementation rested on. Frame equality was exact, justified by the candidates being read
  "at one instant" — but each candidate's frame is its own attribute fetch, so a settling screen can
  report one control's two registrations a fraction of a point apart and drop the pair back into the
  failure this item removes; frames now agree within a point. The rule also keyed on frame alone
  while the host's `_collapse_identical_duplicates` keys on `value` as well, so a value-bearing
  control registered twice with disagreeing values would have resolved here and raised
  `AmbiguousSelector` there; `RecordedAttributes` gained `value` and the group rule now requires it.

## References

- [BE-0357 — Drop XCUITest's non-hittable duplicate accessibility node when exactly one is tappable](../BE-0357-xcuitest-duplicate-node-hittable-tiebreak/BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md):
  attacks the same alert failure by filtering the `/elements` reply; its premise that exactly one
  member of a duplicate pair reports itself hittable is what this item's spike disproved, and this
  item moves it to `Proposal (deferred)`.
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md):
  excluded frame from `attributesMatch` (Unit 5), the recorded-against-live comparison this item's
  candidate-against-candidate comparison does not contradict.
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py): `_collapse_identical_duplicates` — the
  host-side twin of this item's rule, keying on the same fields over an `/elements` reply.
- [BE-0312 — Derive XCUITest actuation handles from element identity so an unchanged screen keeps its handles valid](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md):
  the handle scheme this item leaves unchanged; both members of a duplicate pair keep their own
  handles, and after this item both resolve.
- [BE-0289 — Make the XCUITest channel re-resolve a stale actuation handle before failing](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md):
  the retry that cannot recover this failure, because every attempt re-derives the same unresolvable
  candidate group.
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift):
  `liveElement(for:)` and `uniquelyIdentifiedElement` — the two-step resolution this item changes at
  its second step.
- [`BajutsuKit/Sources/BajutsuRunner/PositionPath.swift`](../../BajutsuKit/Sources/BajutsuRunner/PositionPath.swift):
  `uniqueMatchingIndex` and `attributesMatch` — the pure identity logic the new group rule sits
  beside rather than replaces.
- [`demos/showcase/scenarios/alert.yaml`](../../demos/showcase/scenarios/alert.yaml): the scenario
  that reproduces the failure on iOS 26 and passes on iOS 18.

**English** · [日本語](BE-0357-xcuitest-duplicate-node-hittable-tiebreak-ja.md)

# BE-0357 — Drop XCUITest's non-hittable duplicate accessibility node when exactly one is tappable

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0357](BE-0357-xcuitest-duplicate-node-hittable-tiebreak.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Deferred** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0357") |
| Topic | Platform support |
| Related | [BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

A content-identical duplicate accessibility node pair is two on-screen elements.
XCUITest reports them with the same identifier, label, traits, value, and frame. It is a known
`UIAlertController` artifact. This item resolves such a pair down to the single element that XCUITest can actually hit-test.
It drops the other element from the `/elements` reply of the runner. Today
both stay in the tree, and the query order of the runner binds a handle to one of them arbitrarily.

`resolve_unique` in [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) already collapses such a pair to one representative.
A tap then does not fail outright with `AmbiguousSelector`. The
collapse helper that it calls, `_collapse_identical_duplicates`, names the residual cost in its own
docstring: "which of the two a run actually taps swaps between runs, stale-handle-failing whichever
twin it didn't."

This item closes that residual cost at the source. Suppose exactly one twin of a duplicate pair
reports itself hittable via
[`ElementProviding.isHittable`](../../BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift). That
is the native XCUITest signal that the backend already uses to tell a covered element apart from a
real actuation failure, for an element whose frame center is on screen (off-screen is deliberately
left to `scroll`, not this signal). The runner then drops the other twin before either reaches the
handle assignment of
[`SnapshotStore`](../../BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift) (BE-0312). Only one
candidate is left to resolve or to bind a handle to.

`SnapshotStore` itself is unchanged. The fix is confined to the query step that hands it its input,
and to the `/elements` query for the in-app tree. The SpringBoard system-alert query that BE-0316
added stays untouched (see *Detailed design*).

## Motivation

A `tap` by `id` on a button inside a `UIAlertController` can fail with
`element vanished (stale handle)`, even though the evidence snapshot from the immediately preceding
step shows the button on screen, unmoved, with the identifier that the selector names. The snapshot registers the accessibility node of the button twice.
The two entries have identical `identifier`, `label`, `traits`, `value`, and `frame`. Several other mirrored `"other"`-trait duplicates sit in the
same subtree. The docstring of `resolve_unique` already names this double registration.
It calls it "a known XCUITest duplicate registration for a standard `UIAlertController` button."

The collapse that the docstring describes keeps a selector from failing loudly on a pair that no
scenario author can tell apart. It does not decide which of the two physical nodes the collapsed
match binds to. `resolve_unique` keeps the first element that `find_all` returns, in the order of
the `/elements` reply of the runner. `SnapshotStore.refreshSnapshot` in
[`SnapshotStore.swift`](../../BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift) assigns the bare,
unsuffixed handle to that same first-listed occurrence.

Neither side has a reason to prefer one physical node over the other. The order comes from however
the accessibility-tree walk of XCUITest happened to list the pair on that query. The collapse's own
docstring reports that order as unstable: "swaps between runs."

A `UIAlertController` button pair does not swap positions mid-run once it settles. Every one of the three re-resolution attempts of BE-0289 within a single step therefore re-derives the same first-listed twin.
Each attempt repeats the same outcome.

- Suppose that twin is the one that the native tap of XCUITest can resolve and hit-test.
The step then passes every time it runs against that duplicate order.
- If it is the other twin, the same query reports it with an identical identifier, label, traits,
  value, and frame. But the event synthesis of XCUITest cannot resolve it, so every attempt raises
  "No matches found" natively. The `onMainCatching` function of
  [`Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift) reports that as `stale` (the
  second of the two paths that the `_actuate` comment in
  [`xcuitest.py`](../../bajutsu/drivers/xcuitest.py) names for that reply). The step fails with
  `element vanished (stale handle)` on a button that never left the screen.

The result is a scenario that is flaky across runs and deterministic within one. The outcome of the tap step hinges on an ordering that the tree walk of XCUITest decides.
Neither the scenario nor the retry has any lever over that ordering.

This is the second flake class that BE-0312 named and deferred rather than fixed. The "Detailed
design" section of BE-0312 states the assumption directly: "The occurrence-index tiebreak for
identity-identical elements assumes the query order of identical elements is stable across
snapshots; a screen that reorders visually identical elements between snapshots could still reassign
their handles, but such a screen is rare and its elements are indistinguishable to a selector
anyway, so closing that gap is deferred until a real scenario needs it."

A `UIAlertController` confirmation dialog reachable from ordinary in-app navigation is that real
scenario. Its buttons double-register on every presentation, and a scenario author has every reason
to `tap` one. Once that screen is in a suite, the failure that this item fixes is no rare edge case.

Leaving it unfixed carries the cost that BE-0289 and BE-0312 both measure for this failure class. A
required on-device gate that fails at random on an element that was never missing erodes trust in
the gate. Every re-run also burns metered macOS-runner minutes on investigating a step that did
nothing wrong.

## Detailed design

The fix stays confined to the element-query step of the runner. That layer holds what neither the
Python driver nor `SnapshotStore` has: a live native handle to each physical duplicate, and the
existing capability to ask XCUITest whether that specific node is hit-testable right now.

**Drop each non-hittable duplicate from a group, but only when doing so leaves exactly one
candidate.** `handleElements` in
[`Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift) calls `queryElements()` from
inside a `caughtOnMain` closure. That is the main-thread context that
[`ElementProviding.isHittable(backingElement:)`](../../BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift)
itself needs, since it is a native XCUITest call. The filter belongs inside that same closure,
before the result reaches `elementsResponse`.

This item is scoped to `handleElements` and `/elements` alone. It does not cover
`handleSystemAlertQuery` or the SpringBoard `/systemAlert/query` that BE-0316 added.

- `isHittable` resolves a backing reference through the in-app position-path walk (`liveElement(for:)`
  in [`XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)).
- It returns `.notFound` immediately for anything that is not that kind of reference. That includes
  the ordinal-addressed `SystemAlertButtonBacking` that `querySystemAlertButtons()` produces. The
  design of BE-0316 uses that form because the out-of-process alert has no snapshot position path to
  record.
- Probing a SpringBoard duplicate would therefore always land in the "no member reports `.ok`" case below.
It would never filter anything, and it would waste one call per member.
- The double-registration artifact that *Motivation* describes is itself a property of the in-app
  `UIAlertController` snapshot walk, not of the system-alert path. This item leaves no case
  unhandled there.

The filter groups the just-returned elements by the identity that `_collapse_identical_duplicates` already uses.
The five fields `identifier`, `label`, `traits`, `value`, and `frame` must all be equal. It compares `traits`
as a set, matching the Python key `tuple(sorted(set(el["traits"])))`. A pair reported with the same
traits in a different order still groups. A plain `[String] ==`, or the array hash of
`SnapshotStore`, would not group such a pair.

A group of size one needs no probing and costs nothing extra. For a group of two or more, the filter
calls `isHittable(backingElement:)` on each member, still on the main thread. This is the same
native check that the `/isHittable` endpoint already uses. The filter catches a raise from each call
individually, the way the `/isHittable` endpoint already treats a raising resolution as `.stale`
rather than letting it propagate (`onMainCatching` in
[`Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)).

The discriminator between the two twins is `el.isHittable`. That is the boolean of XCUITest for
whether a synthesized event can currently cover a resolved, on-screen element.
`isHittable(backingElement:)` returns:

- `.ok` when `el.isHittable` is `true`;
- `.notHittable` when it is `false`;
- `.ok` without even asking, when the element's frame center is off screen. This matches the guard
  of `tap`, since off-screen is a `scroll` question rather than a hittability one.

A raise is not the mechanism that this item expects to distinguish the twins. Both resolve through
the same position-path lookup, and neither is expected to fail resolution. The per-member catch is
defensive. It keeps a raise (from either twin, for any reason) from falling through to the own
`caughtOnMain([])` fallback of `handleElements`, which would report an empty screen instead of the
rest of the tree. A member whose probe raises counts as not `.ok`.

When exactly one member reports `.ok`, the filter drops every other member of the group from the
list before the list reaches `elementsResponse`. The survivor is now the only candidate of its
group, so no tiebreak, occurrence-index or otherwise, is needed to place *it*.

Elements outside any duplicate group keep their original relative order. Duplicate groups where
dropping does not apply (the two cases below) keep it too. The filter removes members from the original list in place.
It does not reassemble the list from a keyed grouping, whose iteration order Swift does not guarantee. This changes nothing for the overwhelming majority of the elements
of a query. The filter is scoped to the rare groups that would otherwise collide on the five-field
key of `_collapse_identical_duplicates`.

Neither `SnapshotStore` nor `elementsResponse` needs a change for the case that this item targets. A
solitary survivor resolves Python-side without `_collapse_identical_duplicates` ever having two
candidates to collapse.

Some selectors reach elements through `find_all` directly rather than through `resolve_unique`:
`forEach`, `exists`, and `count` against a selector broader than the identity of the duplicate, and
the `within`-container resolution of a `scroll` step. They see the same shrink in candidates that
the callers of `resolve_unique` see. This item does not re-derive any of them separately, since each
already reads whatever `/elements` returns.

That shrink is conditional on a live probe, though.

- While the pair is still covered, `count` and `exists` against a duplicate identity report two members.
No member reports `.ok`, so the group is left untouched.
- Once the pair settles, they report one, because exactly one member reports `.ok`.

The on-device tests of this item pin both values for the same screen. An assertion polled across
that transition can change without the identity of the tree changing. Unlike a tap, it has no
`stale` reply for the retry of BE-0289 to absorb. *Progress* records this alongside the deferred
limitation below.

Whether the filter can narrow a group at all also depends on the twins sharing a frame by
construction (one of the five identity fields). Both take the same on-screen-or-not branch of
`isHittable`. A duplicate pair that is off screen, below the fold of a scroll view for example,
reports `.ok` for both twins. It lands in the "more than one member reports `.ok`" case and stays
unfiltered.

That is not a regression, since query order already decides that case as it does today. It does mean that the practical effect of the filter is confined to one kind of pair.
The pair must be on screen, with exactly one twin actually coverable. That is the shape of the `UIAlertController` case that *Motivation* describes.
It is not guaranteed for every content-identical pair that a scenario might encounter.

One case remains that this item does not close. The own occurrence-index tiebreak of `SnapshotStore` keys on a different, coarser identity than the five-field group above.
The coarser key holds `identifier`, `label`, and `traits`, and nothing else. It deliberately excludes `value` and `frame` (the reasoning of BE-0312 for a screen
that is settling). A five-field duplicate match trivially implies a match on that coarser,
three-field key. A duplicate pair's two members therefore already collide with *each other* on the hash of `SnapshotStore`.
No third element is needed for the collision to exist.

That matters across a drop decision that flips between queries. Suppose a query leaves the group untouched (either of the two cases below).
Suppose it hands the bare, unsuffixed handle to the twin that this item will drop at a later query. A caller holds onto that handle. The query that drops the twin leaves the survivor alone in the group.
The survivor lands on that same bare handle, because the coarser hash is the same and now has one occupant. `SnapshotStore.lookup` returns `.found` for that handle against
the survivor, not `.stale`. A handle minted for one physical node silently resolves to a different one.
It never passes through the `.stale` state that the retry of BE-0289 depends on to notice.

Whether a twin is dropped depends on a live probe result, and that result can itself change between
two queries of an unchanged screen. The `isHittable` outcome during a settling transition is not
guaranteed stable. This can therefore happen without either physical node ever moving or changing
identity. The same coarser-hash collision, and the same occurrence-index churn, extends to a third element.
It applies to any unrelated element that shares the three coarser fields with the survivor. This item does
not attempt to close either case. *Progress* records both as a known, deferred limitation rather
than a silent gap.

Two cases the filter must leave untouched, so that it never regresses an existing outcome:

- **No member of the group reports `.ok`.** The filter drops nothing. A filter with no survivor to keep would either empty the group outright or pick one non-hittable member arbitrarily.
Either choice invents a distinction that the probe did not find. The group stays as it is today.
The eventual tap still fails through the paths that already exist and already report a precise cause.
  - `.notHittable` for a resolvable, on-screen node that is currently covered. Today it surfaces as
    `ElementNotTappable`. This case cannot arise from a member being off screen, since `isHittable`
    reports that as `.ok`, not `.notHittable` (see *Detailed design*).
  - A native resolution failure for a node that XCUITest cannot act on at all. Today it surfaces as
    `stale`, which the retry of BE-0289 can still legitimately absorb if the underlying condition
    clears.

  Neither this item nor `SnapshotStore` changes what either outcome means.
- **More than one member reports `.ok`.** Two genuinely distinct, independently tappable controls
  happen to share identity. This is not the `UIAlertController` double registration that this item
  targets, where only one twin is ever really actionable. Dropping either one on this signal alone
  would silently remove a control that a scenario might need to reach by `index`. The probe cannot
  tell "an accidental duplicate" apart from "two real, independently tappable controls that happen
  to render identically." The group stays exactly as it is today: query order still decides which
  twin gets the bare, unsuffixed handle, and which of the two tappable twins that is can still vary
  from query to query, as before this item. A selector this ambiguous already reads as intentional.
It matches an app that renders two indistinguishable, independently tappable controls.
It does not read as the artifact that this item closes. That instability is a pre-existing property of such a selector.
This item neither introduces it as a regression nor claims to close it as a gap.

**Cost.** The added native call runs only for elements that already collide on content.
`_collapse_identical_duplicates` frames those as a known, narrow XCUITest artifact in its own
docstring, not the common case. An ordinary query with no duplicate group pays nothing extra. The
call itself is the same one that `/isHittable` already performs on the main thread today. It is invoked from inside the same `caughtOnMain` closure that `handleElements` already holds open for `queryElements()`.
It adds no new thread hop.

**An unverified premise, checked first.** The whole filter is a no-op in one case.
The case is a real `UIAlertController` double registration that does not resolve to exactly one twin reporting `.ok`. *Detailed design* asserts that it does, from the `el.isHittable` distinction.
Nothing in this document has confirmed that against a real device.

Before the Router and test-infrastructure work below, a spike checks the premise directly:

1. Present a `UIAlertController` on a real Simulator.
2. Capture both twins' `backingElement` references from `queryElements()`.
3. Call `isHittable(backingElement:)` on each.

If exactly one reports `.ok`, the rest of this design is worth building. If both do, or both do not,
the premise of *Detailed design* is wrong and the filter as specified cannot narrow this case at
all. That outcome would need a different design, and would not be a defect in the implementation of
the filter.

**Tests.** The grouping and the drop-or-leave decision are Router logic, not a native behavior, so
they are coverable off-device. Unlike Unit 3 of BE-0312, they are not covered in
`SnapshotStoreTests.swift`, because `SnapshotStore` itself is untouched. They are covered in
`RouterTests.swift`, driving `GET /elements` end to end against `FakeElementProvider`.

Today the `isHittableResult` of that fake is a single `TapResult` shared by every call. It becomes a per-backing-element result.
The result is a small dictionary keyed by `ObjectIdentifier` of the own backing reference of the fake, not by content. The two fake elements in the tests below are content-identical by construction.
A content-keyed dictionary could not hold two different results for them. Three
tests then pin the branch logic deterministically, with no Simulator:

- Two fake elements with identical content and opposite `isHittable` results. The test asserts that
  the reply keeps only the hittable one.
- Two fake elements that both report `.ok`. The test asserts that the reply still contains both,
  unchanged.
- Two that both report something other than `.ok`. The test asserts that the reply still contains
  both, unchanged.

A fourth test drives the exception-catching requirement above directly. It needs a second, new
per-backing-element raise-injection property alongside the `TapResult` dictionary. `isHittable` has
no such property today; only `tap` and `queryElements` do, via `tapRaises` and `queryRaises`. And
`TapResult` itself has no raise case to express "raises" as a return value. With that property, a
fake configured to raise for one element and return `.ok` for the other still drops only the raising
one.

On-device coverage is new rather than a precedent's continuation.
`Router.swift` and `ElementProviding.swift` have no existing on-device or off-device split to follow. It runs once the
spike above has shown that the premise holds. It confirms that a genuine `UIAlertController` double
registration reproduces the same way in a full scenario run:

- A `tap` by `id` lands on the surviving twin.
- A `count` assertion on that identity reports one element afterward, rather than two.

The regression that this item exists to prevent is a tap on the wrong twin. The count assertion pins
that the survivor is truly the sole candidate, not merely reordered.

## Alternatives considered

**Reorder the group instead of dropping the non-hittable twin.** An earlier version of this design
moved the hittable twin to the front of the group and left both in the tree. That matches the shape
of the existing occurrence-index tiebreak of BE-0312, rather than changing it. Reordering fixes the
failure that *Motivation* describes, because the survivor at position zero always gets the bare,
unsuffixed handle. But it does so only for the case where exactly one twin is hittable. When both twins are hittable, a reorder-only design still has to fall back to query order.
Query order decides which twin lands at the front, so that case stays as non-deterministic as today. Dropping the non-hittable
twin in the one-hittable case removes the need to order the survivor against its twin at all, since
no second occurrence of that five-field identity remains to order, by reordering or by anything
else.

Reordering also has to keep the non-hittable twin around forever as a suffixed-handle duplicate.
Nobody can ever tap it, and a `count` or `index` selector still sees it. Dropping it makes `count`
report the number of controls that a scenario can actually act on. That is arguably the more correct
reading of "how many buttons are here" for an artifact that was never a real second control. The trade-off is the one that *Detailed design* names.
Dropping changes what `/elements` reports for that identity.
Reordering would have left the reported shape untouched.

**Fix the tiebreak in `SnapshotStore` itself.** `SnapshotStore.refreshSnapshot` only ever sees
`ElementSnapshot` values, each already reduced to identity, value, frame, and an opaque
`backingElement` reference. Nothing in that signature carries a live hittability outcome.
Computing one requires the same native, main-thread `isHittable` call that this item already proposes. Moving the filter inside `SnapshotStore` would have to give the store that native
capability directly. That would couple a class that BE-0312 kept deliberately device-agnostic and
off-device-testable to a live XCUITest call. It is worse on both counts than filtering the list one
layer up, before it reaches the store.

**Fix it Python-side, in `resolve_unique` or `_collapse_identical_duplicates`.** The Python driver
never receives more than one candidate's content, because the two duplicates collapse to a single
logical match before Python compares them. It also has no channel to ask the runner "which of these
is hittable" without a second round trip per duplicate group, on every query, from the side of the
channel that does not hold the native reference at all.

Python could drop one candidate once the runner has told it which is hittable.
That would still leave the *other* twin's handle live and reachable by `index` on the runner side. The drop has to happen
where the element disappears from the reply, which is the runner, not a client that sees only what
the runner already sent it.

This reasoning is specific to a backend with a persistent native runner process to ask.
`resolve_unique` and `_collapse_identical_duplicates` are shared by every backend. A backend with no
such process (adb, Playwright, the WebView bridge, the Appium-driven live route) has no runner layer
to move this fix into at all, so the same rejection does not transfer to a duplicate reported by one
of them.

**Retry more, or add a scroll/dismiss-and-reopen recovery above the driver.** The retry of BE-0289
already re-resolves and re-actuates on a `stale` reply, and *Motivation* shows why it cannot recover
this failure: the duplicate order is stable within a run, so every attempt re-derives the same twin
and repeats the same outcome. Widening the retry budget spends more time reproducing an outcome that
is not going to change. A scroll or dismiss-and-reopen recovery above the driver would treat the
layout of a `UIAlertController` as broken when it is not. The dialog was never the problem.

**Compute `isHittable` for every element on every query, not only duplicate groups.** Probing the
hittability of every element up front would make the duplicate case a special case of nothing. But it
costs one native call per element on every `/elements` reply, and most of those elements are never
involved in a duplicate collision. Scoping the probe to elements that already collide on content
keeps the added cost proportional to how often the artifact that this item targets actually occurs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Spike (gates the rest): on a real Simulator, present a `UIAlertController` double-registration,
      capture both twins' `backingElement` references from `queryElements()`, and call
      `isHittable(backingElement:)` on each directly. Confirm exactly one reports `.ok` before building
      anything below; if the premise doesn't hold, this design needs to change rather than proceed.
      **Ran, and the premise did not hold** — see the log entry below. Everything under it is on hold.
- [ ] Group `queryElements()` results by the shared identity (`identifier`, `label`, `traits`,
      `value`, `frame`) inside `handleElements`'s `caughtOnMain` closure in `Router.swift`. Probe each
      member of a group of two or more with `isHittable(backingElement:)`, catching a raise per member
      defensively (not `.ok` for that member, not a fallthrough to the closure's own `[]` fallback).
      When exactly one member reports `.ok`, drop every other member before the list reaches
      `elementsResponse`; leave a group with no `.ok` member, or more than one, exactly as it is today.
      `handleSystemAlertQuery` / `/systemAlert/query` is explicitly out of scope (see *Detailed
      design*).
- [ ] Off-device tests in `RouterTests.swift`: give `FakeElementProvider` a per-backing-element (keyed
      by `ObjectIdentifier`, not by content) `isHittable` result and a separate per-backing-element
      raise-injection property; pin the three branches (drop the non-`.ok` twin when exactly one is
      `.ok`, keep both when zero are, keep both when two or more are) and the per-member
      exception-catching requirement (one raising fake element, one `.ok`, only the raising one is
      dropped).
- [ ] On-device test: a `UIAlertController` with a genuinely covered duplicate button, tapped by `id`,
      lands on the surviving hittable twin (not the covered one); a `count` assertion on that identity
      reports one element afterward; a both-covered pair still surfaces `ElementNotTappable` with
      `count` still reporting two; a both-hittable pair still resolves by today's query order with
      `count` still reporting two.
- [ ] Documentation: note in the `handleElements` comment (and `SnapshotStore`'s class comment, if it
      still implies every content-identical group reaches the store) that a group with exactly one
      hittable member is resolved to that member before the store ever sees it, and that
      `handleSystemAlertQuery` is deliberately untouched. Record the deferred limitation *Detailed
      design* names — a five-field duplicate pair's own two members, or a pair plus a third unrelated
      element, that also collide on `SnapshotStore`'s coarser three-field identity can see a handle
      silently resolve to a different physical node, or an occurrence index shift, between queries.
      Update the conditional language in `_collapse_identical_duplicates`'s docstring
      (`bajutsu/drivers/base.py`), which this item's own Introduction quotes as the residual cost being
      removed, and `DESIGN.md` / `docs/architecture.md` if either describes this behavior (BE-0113).

Log:

- The spike above ran against a `UIAlertController` presented by the showcase UIKit fixture on an
  iOS 26.5 Simulator, and **disproved this item's premise**: `isHittable(backingElement:)` reported
  `stale` — neither `.ok` nor `.notHittable` — for **every** member of both duplicate pairs, while
  ordinary non-duplicated nodes in the same alert subtree reported `.ok`. Zero members report `.ok`
  rather than exactly one, so *Detailed design*'s filter lands in its own "no member of the group
  reports `.ok`" branch and drops nothing: as specified, this item is a no-op for the very failure
  *Motivation* describes. The cause sits one layer below the one this item addresses. `isHittable`
  resolves its argument through `liveElement(for:)`, whose flat-query recovery calls
  `uniqueMatchingIndex` and returns nothing on more than one identity match, so the duplicate pair
  defeats that recovery, neither member resolves to a live element at all, and no hittability signal
  exists for this item to filter on. The `xcuitest-duplicate-identity-resolution` item repairs that
  resolution, and measures on the same fixture that a member of the pair actuates once it resolves —
  which puts the repaired screen in this item's *other* leave-untouched branch, "more than one member
  reports `.ok`". This item therefore stays deferred until a duplicate pair turns up whose members a
  live probe can genuinely tell apart.

## References

- [BE-0312 — Derive XCUITest actuation handles from element identity so an unchanged screen keeps its handles valid](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md):
  introduced the occurrence-index tiebreak this item's target case no longer needs to place the
  survivor against its dropped twin, since only one candidate of that five-field identity remains; the
  tiebreak still runs unchanged for the two cases this item leaves untouched, and can still reassign a
  handle across queries when a dropped twin's survivor, or a dropped twin plus an unrelated element,
  collides with something else on the tiebreak's own coarser three-field identity (the known limitation
  *Detailed design* names). Its own "Detailed design" section names the query-order-instability gap
  this item closes for that target case.
- [BE-0289 — Make the XCUITest channel re-resolve a stale actuation handle before failing](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md):
  the retry *Motivation* shows cannot recover this failure, because the duplicate order it re-derives
  is stable within a run; the same retry cannot notice the known limitation above either, since a
  silently reassigned handle never produces the `stale` reply the retry watches for.
- [BE-0349 — Verify tappability before acting, with a bounded scroll safety net](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md):
  introduced `ElementProviding.isHittable`/`TapResult.notHittable` and the `/isHittable` endpoint this
  item reuses twice, for the related but distinct problem of a uniquely-resolved element that is
  covered rather than one of several content-identical candidates; its own "Alternatives considered"
  rejected a uniform per-element schema field for a related reason this item's Python-side alternative
  does not credit.
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md):
  the determinism stance this item stays consistent with — remove the cause of a flake rather than
  absorb an outcome.
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py): `_collapse_identical_duplicates` and
  `resolve_unique` — the Python-side collapse whose own docstring names the residual cost this item
  removes for its target case, and whose docstring Progress commits to updating so it no longer reads
  as unconditional.
- [`BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift`](../../BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift):
  `refreshSnapshot` — unchanged by this item; its occurrence-index tiebreak no longer needs to place a
  dropped twin's survivor against that twin, keeps running unchanged for the two cases this item leaves
  untouched, and can still resolve a handle to a different physical node than the one it was minted for
  across a query where the drop decision flips (the known limitation *Detailed design* names).
- [`BajutsuKit/Sources/BajutsuRunner/Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift):
  `handleElements` — the handler this item changes; `elementsResponse` and `handleSystemAlertQuery`
  stay unchanged.
- [`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift`](../../BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift):
  `isHittable(backingElement:)` — the existing native signal this item reuses.
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift):
  `isHittable`, `liveElement(for:)`, `centerIsOnScreen` — the concrete implementation behind
  `ElementProviding.isHittable`, whose `PositionPathBacking`-only guard is why this item cannot reach
  `SystemAlertButtonBacking` and why an off-screen member reports `.ok` rather than `.notHittable`.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py): `_actuate` — whose comment names
  the two paths that produce a `stale` reply, the second of which this item's *Motivation* traces to
  the wrong twin.

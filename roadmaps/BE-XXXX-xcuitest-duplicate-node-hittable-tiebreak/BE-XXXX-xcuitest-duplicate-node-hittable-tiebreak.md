**English** · [日本語](BE-XXXX-xcuitest-duplicate-node-hittable-tiebreak-ja.md)

# BE-XXXX — Drop XCUITest's non-hittable duplicate accessibility node when exactly one is tappable

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-duplicate-node-hittable-tiebreak.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0349](../BE-0349-tap-target-hittability-check/BE-0349-tap-target-hittability-check.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) |
<!-- /BE-METADATA -->

## Introduction

This item resolves a content-identical duplicate accessibility node pair down to the single element
XCUITest can actually hit-test, dropping the other from the runner's `/elements` reply — instead of
leaving both in the tree for the runner's query order to bind a handle to arbitrarily. A
content-identical duplicate accessibility node pair is two on-screen elements XCUITest reports with the
same identifier, label, traits, value, and frame — a known `UIAlertController` artifact.
`resolve_unique` in [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) already collapses such a
pair to one representative so a tap does not fail outright with `AmbiguousSelector`, but the collapse
helper it calls, `_collapse_identical_duplicates`, names the residual cost in its own docstring: "which
of the two a run actually taps swaps between runs, stale-handle-failing whichever twin it didn't." This
item closes that residual cost at the source: when exactly one twin in
a duplicate pair reports itself hittable via
[`ElementProviding.isHittable`](../../BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift) — the
same native XCUITest signal the backend already uses to tell "covered or off-screen" apart from a real
actuation failure — the runner drops the other twin before either ever reaches
[`SnapshotStore`](../../BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift)'s handle assignment
(BE-0312), so only one candidate is ever left to resolve or to bind a handle to. `SnapshotStore` itself
is unchanged: the fix is confined to the query step that hands it its input.

## Motivation

A `tap` by `id` on a button inside a `UIAlertController` can fail with
`element vanished (stale handle)` even though the evidence snapshot from the immediately preceding
step shows the button on screen, unmoved, with the exact identifier the selector names. The button's
own accessibility node is registered twice in that snapshot: two entries with identical `identifier`,
`label`, `traits`, `value`, and `frame`, alongside several other mirrored `"other"`-trait duplicates in
the same
subtree — the double registration `resolve_unique`'s docstring already names as "a known XCUITest
duplicate registration for a standard `UIAlertController` button."

The collapse that docstring describes keeps a selector from failing loudly on a pair no scenario author
can tell apart, but it does not decide which of the two physical nodes the collapsed match binds to.
`resolve_unique` keeps the first element `find_all` returns, in the order the runner's `/elements`
reply listed them, and `SnapshotStore.refreshSnapshot` in
[`SnapshotStore.swift`](../../BajutsuKit/Sources/BajutsuRunner/SnapshotStore.swift) assigns the bare,
unsuffixed handle to that same first-listed occurrence. Neither side has a reason to prefer one
physical node over the other: the order comes from however XCUITest's own accessibility-tree walk
happened to list the pair on that query, which the collapse's own docstring reports as unstable —
"swaps between runs." A `UIAlertController` button pair does not swap positions mid-run once it
settles, so every one of BE-0289's three re-resolution attempts within a single step re-derives the
same first-listed twin and repeats the same outcome: if that twin is the one XCUITest's native tap can
resolve and hit-test, the step passes every time it runs against that duplicate order; if it is the
other twin — one that the same query reports with an identical identifier, label, traits, value, and
frame, but that XCUITest's own event synthesis cannot resolve — every attempt raises "No matches found"
natively, [`Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)'s `onMainCatching`
reports it as `stale` (the second of the two paths
[`xcuitest.py`](../../bajutsu/drivers/xcuitest.py)'s `_actuate` comment names for that reply), and the
step fails with `element vanished (stale handle)` on a button that never left the screen.

The result is a scenario that is flaky across runs and deterministic within one: the tap step's
outcome hinges on an ordering XCUITest's own tree walk decides, and neither the scenario nor the retry
has any lever over that ordering. This is the second flake class BE-0312 named and deferred rather
than fixed. BE-0312's own "Detailed design" section states the assumption directly: "The
occurrence-index tiebreak for identity-identical elements assumes the query order of identical
elements is stable across snapshots; a screen that reorders visually identical elements between
snapshots could still reassign their handles, but such a screen is rare and its elements are
indistinguishable to a selector anyway, so closing that gap is deferred until a real scenario needs
it." A `UIAlertController` confirmation dialog reachable from ordinary in-app navigation is exactly
that real scenario: its buttons double-register on every presentation, a scenario author has every
reason to `tap` one, and the failure this item fixes is not a rare edge case once that screen is in a
suite. Leaving it unfixed carries the same cost BE-0289 and BE-0312 both measure for this failure
class: a required on-device gate that fails at random on an element that was never missing erodes
trust in the gate, and every re-run burns metered macOS-runner minutes investigating a step that did
nothing wrong.

## Detailed design

The fix stays confined to the runner's element-query step, the layer that already holds the one piece
of information the Python driver and `SnapshotStore` both lack: a live native handle to each physical
duplicate, and the existing capability to ask XCUITest whether that specific node is actually
hit-testable right now.

**Drop each non-hittable duplicate from a group, but only when doing so leaves exactly one
candidate.**
[`Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)'s `handleElements` and
`handleSystemAlertQuery` each call `queryElements()` / `querySystemAlertButtons()` from inside a
`caughtOnMain` closure — the main-thread context
[`ElementProviding.isHittable(backingElement:)`](../../BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift)
itself needs, since it is a native XCUITest call. The filter belongs inside that same closure, before
the result reaches `elementsResponse` — the handler shared by `/elements` and the SpringBoard
`/systemAlert/query` (BE-0316), which stays unchanged. Group the just-returned elements by the same
identity `_collapse_identical_duplicates` already uses: `identifier`, `label`, `traits`, `value`, and
`frame` all equal. A group of size one needs no probing and costs nothing extra. For a group of two or
more, call `isHittable(backingElement:)` — the same native check the `/isHittable` endpoint already
uses to distinguish "covered right now" from a genuine actuation failure — on each member, still on
the main thread, and catch a raise from that call individually, the same way the `/isHittable` endpoint
already treats a raising resolution as `.stale` rather than letting it propagate
([`Router.swift`](../../BajutsuKit/Sources/BajutsuRunner/Router.swift)'s `onMainCatching`). A member
whose probe raises counts as not `.ok` for that member only: the phantom twin this item exists to drop
is exactly a node whose native resolution can raise, so catching per member, not per query, keeps one
raising probe from falling through to `handleElements`'s (or, for the SpringBoard alert path,
`handleSystemAlertQuery`'s) own `caughtOnMain([])` fallback and reporting an empty screen instead of the
rest of the tree. When exactly one member reports `.ok`, drop every
other member of the group from the list before it reaches `elementsResponse`: the survivor is now the
group's only candidate, so no tiebreak — occurrence-index or otherwise — is needed to place *it*.
Elements outside any duplicate group, and duplicate groups where dropping does not apply (the two cases
below), keep their original relative order — the filter removes members from the original list in
place rather than reassembling it from a keyed grouping, whose iteration order Swift does not
guarantee — so this changes nothing for the overwhelming majority of a query's elements: the filter is
scoped to the rare groups that would otherwise collide on `_collapse_identical_duplicates`'s five-field
key. Neither `SnapshotStore` nor `elementsResponse` needs a change for the case this item targets: a
solitary survivor resolves Python-side without `_collapse_identical_duplicates` ever having two
candidates to collapse. Selectors that reach elements through `find_all` directly rather than through
`resolve_unique` — `forEach`, `exists`, `count` against a selector broader than the duplicate's own
identity, and a `scroll` step's `within`-container resolution — see the same shrink in candidates
`resolve_unique`'s callers do; none of them is re-derived separately here, since each already reads
whatever `/elements` returns.

One case this item does not close: `SnapshotStore`'s own occurrence-index tiebreak keys on a
different, coarser identity than the five-field group above — `identifier`, `label`, and `traits`
only, deliberately excluding `value` and `frame` (BE-0312's own reasoning for a screen that is
settling). A five-field duplicate match trivially implies a match on that coarser, three-field key, so
a duplicate pair's own two members already collide with *each other* on `SnapshotStore`'s hash — no
third element is needed for the collision to exist. That matters across a drop decision that flips
between queries: if a query where the group is left untouched (either of the two cases below) hands
the bare, unsuffixed handle to the twin this item will go on to drop at a later query, and a caller
holds onto that handle, the query that drops the twin leaves the survivor alone in the group, which
then lands on that same bare handle — the same coarser hash, now with only one occupant.
`SnapshotStore.lookup` returns `.found` for that handle against the survivor, not `.stale`: a handle
minted for one physical node silently resolves to a different one, without ever passing through the
`.stale` state BE-0289's retry depends on to notice. Because whether a twin is dropped depends on a
live probe result that can itself change between two queries of an unchanged screen — the isHittable
outcome during a settling transition is not guaranteed stable — this can happen without either physical
node ever actually moving or changing identity. The same coarser-hash collision, and the same
occurrence-index churn, extends to any unrelated third element that happens to share only the three
coarser fields with the survivor. This item does not attempt to close either case; Progress records
both as a known, deferred limitation rather than a silent gap.

Two cases the filter must leave untouched, so it never regresses an existing outcome:

- **No member of the group reports `.ok`.** Drop nothing; a filter with no survivor to keep would
  either empty the group outright or have to pick one non-hittable member arbitrarily, and either
  choice invents a distinction the probe did not actually find. Keep the group exactly as it is today,
  so the eventual tap still fails through the same paths that already exist and already report a
  precise cause: `.notHittable` for a resolvable-but-covered or off-screen node (surfaced today as
  `ElementNotTappable`), or a native resolution failure for a node XCUITest cannot act on at all
  (surfaced today as `stale`, which BE-0289's retry can still legitimately absorb if the underlying
  condition clears). Neither this item nor `SnapshotStore` changes what either outcome means.
- **More than one member reports `.ok`.** This means two genuinely distinct, independently tappable
  controls happen to share identity — not the `UIAlertController` double-registration this item
  targets, where only one twin is ever really actionable. Dropping either one on this signal alone
  would silently remove a control a scenario might genuinely need to reach by `index`, on the strength
  of a probe that cannot tell "an accidental duplicate" apart from "two real, independently tappable
  controls that happen to render identically." Leave the group exactly as it is today instead: query
  order still decides which twin gets the bare, unsuffixed handle, and which of the two tappable twins
  that is can still vary from query to query, exactly as before this item. A selector this ambiguous
  already reads as intentional (an app that genuinely renders two indistinguishable, independently
  tappable controls) rather than as the artifact this item closes, so that instability is a
  pre-existing property of such a selector, not a regression this item introduces or a gap it claims
  to close.

**Cost.** The added native call runs only for elements that already collide on content, which
`_collapse_identical_duplicates`'s own docstring frames as a known, narrow XCUITest artifact rather
than the common case — an ordinary query with no duplicate group pays nothing extra. The call itself
is the same one `/isHittable` already performs on the main thread today, invoked here from inside the
same `caughtOnMain` closure `handleElements` and `handleSystemAlertQuery` already hold open for
`queryElements()` / `querySystemAlertButtons()`, so it adds no new thread hop.

**Tests.** The grouping and drop-or-leave decision is Router logic, not a native behavior, so it is
coverable off-device — unlike BE-0312's Unit 3, not in `SnapshotStoreTests.swift` (`SnapshotStore`
itself is untouched), but in `RouterTests.swift` driving `GET /elements` and `POST /systemAlert/query`
end to end against `FakeElementProvider`. That fake's `isHittableResult` is a single `TapResult` shared
by every call today; giving it a per-backing-element result (a small dictionary keyed by
`ObjectIdentifier` of the fake's own backing reference, not by content — the two fake elements in the
test below are content-identical by construction, so a content-keyed dictionary could not hold two
different results for them) lets one test present two fake elements with identical content and
opposite `isHittable` results and assert the reply keeps only the hittable one, a second test present
two fake elements that both report `.ok` and assert the reply still contains both, unchanged, and a
third present two that both report something other than `.ok` and assert the reply still contains both,
unchanged. These three pin the branch logic deterministically, with no Simulator. A fourth test drives
the exception-catching requirement above directly, and needs a second, new per-backing-element
raise-injection property alongside the `TapResult` dictionary — `isHittable` has no such property
today, only `tap`/`queryElements` do, via `tapRaises`/`queryRaises`, and `TapResult` itself has no
raise case to express "raises" as a return value: with that property, a fake configured to raise for
one element and return `.ok` for the other still drops only the raising one. On-device coverage —
`Router.swift`/`ElementProviding.swift` have no existing on-device/off-device split to follow, so this
one is new rather than a precedent's continuation — is reserved for what only a real device can
confirm: that a genuine `UIAlertController` double-registration reproduces with exactly one twin
`isHittable`, that a `tap` by `id` lands on it, and that a `count`
assertion on that identity reports one element afterward rather than two — the regression this item
exists to prevent is a tap on the wrong twin, and the count assertion pins that the survivor is truly
the sole candidate, not merely reordered.

## Alternatives considered

**Reorder the group instead of dropping the non-hittable twin.** An earlier version of this design
moved the hittable twin to the front of the group and left both in the tree, matching the shape of
BE-0312's existing occurrence-index tiebreak rather than changing it. Reordering fixes the exact
failure *Motivation* describes — the survivor at position zero always gets the bare, unsuffixed handle
— but only for the case where exactly one twin is hittable. When two twins are both hittable, a
reorder-only design still has to fall back to query order to decide which one lands at the front, so
it leaves that case exactly as non-deterministic as today; dropping the non-hittable twin in the
one-hittable case removes the need to order the survivor against its twin at all, since there is no
longer a second occurrence of that five-field identity to order — by reordering or by anything else.
Reordering also has to keep the non-hittable twin around forever as a
suffixed-handle duplicate that can never be tapped, which a `count` or `index` selector still sees.
Dropping it instead makes `count` report the number of controls a scenario can actually act on, which
is arguably the more correct reading of "how many buttons are here" for an artifact that was never a
real second control in the first place. The trade-off is the one *Detailed design* names: dropping
changes what `/elements` reports for that identity, where reordering would have left the reported
shape untouched.

**Fix the tiebreak in `SnapshotStore` itself.** `SnapshotStore.refreshSnapshot` only ever sees
`ElementSnapshot` values, each already reduced to identity, value, frame, and an opaque
`backingElement` reference; nothing in that signature carries a live hittability outcome, and
computing one requires the same native, main-thread `isHittable` call this item already proposes.
Moving the filter inside `SnapshotStore` would need to give it that native capability directly,
coupling a class BE-0312 kept deliberately device-agnostic and off-device-testable to a live XCUITest
call — worse on both counts than filtering the list one layer up, before it reaches the store.

**Fix it Python-side, in `resolve_unique` or `_collapse_identical_duplicates`.** The Python driver
never receives more than one candidate's content — the two duplicates collapse to a single logical
match before Python ever compares them — and it has no channel to ask the runner "which of these is
hittable" without a second round trip per duplicate group, on every query, from the side of the
channel that does not hold the native reference at all. Dropping one candidate Python-side, once the
runner has told it which is hittable, would still leave the *other* twin's handle live and reachable by
`index` on the runner side — the drop has to happen where the element genuinely disappears from the
reply, which is the runner, not a client that only sees what the runner already sent it. This reasoning
is specific to a backend with a persistent native runner process to ask; `resolve_unique` and
`_collapse_identical_duplicates` are shared by every backend, and a backend with no such process (adb,
Playwright, the WebView bridge, the Appium-driven live route) has no runner layer to move this fix into
at all, so the same rejection does not transfer to a duplicate reported by one of them.

**Retry more, or add a scroll/dismiss-and-reopen recovery above the driver.** BE-0289's retry already
re-resolves and re-actuates on a `stale` reply, and *Motivation* shows why it cannot recover this
failure: the duplicate order is stable within a run, so every attempt re-derives the same twin and
repeats the same outcome. Widening the retry budget spends more time reproducing an outcome that is
not going to change, and a scroll or dismiss-and-reopen recovery above the driver would treat a
`UIAlertController`'s own layout as broken when it is not — the dialog was never the problem.

**Compute `isHittable` for every element on every query, not only duplicate groups.** Probing every
element's hittability up front would make the duplicate case a special case of nothing, but at the
cost of one native call per element on every `/elements` and `/systemAlert/query` reply, most of which
are never involved in a duplicate collision. Scoping the probe to elements that already collide on
content keeps the added cost proportional to how often the artifact this item targets actually occurs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Group `queryElements()` / `querySystemAlertButtons()` results by the shared identity
      (`identifier`, `label`, `traits`, `value`, `frame`) inside `handleElements`'s and
      `handleSystemAlertQuery`'s `caughtOnMain` closures in `Router.swift`. Probe each member of a
      group of two or more with `isHittable(backingElement:)`, catching a raise per member (not `.ok`
      for that member, not a fallthrough to the closure's own `[]` fallback). When exactly one member
      reports `.ok`, drop every other member before the list reaches `elementsResponse`; leave a group
      with no `.ok` member, or more than one, exactly as it is today.
- [ ] Off-device tests in `RouterTests.swift`, covering both `GET /elements` and
      `POST /systemAlert/query`: give `FakeElementProvider` a per-backing-element (keyed by
      `ObjectIdentifier`, not by content) `isHittable` result and a separate per-backing-element
      raise-injection property; pin the three branches (drop the non-`.ok` twin when exactly one is
      `.ok`, keep both when zero are, keep both when two or more are) and the per-member
      exception-catching requirement (one raising fake element, one `.ok`, only the raising one is
      dropped).
- [ ] On-device test: a `UIAlertController` with a genuinely covered duplicate button, tapped by `id`,
      lands on the surviving hittable twin (not the covered one); a `count` assertion on that identity
      reports one element afterward; a both-covered pair still surfaces `ElementNotTappable` with
      `count` still reporting two; a both-hittable pair still resolves by today's query order with
      `count` still reporting two.
- [ ] Documentation: note in the `handleElements` / `handleSystemAlertQuery` comments (and
      `SnapshotStore`'s class comment, if it still implies every content-identical group reaches the
      store) that a group with exactly one hittable member is resolved to that member before the store
      ever sees it, and record the deferred limitation *Detailed design* names — a five-field duplicate
      pair's own two members, or a pair plus a third unrelated element, that also collide on
      `SnapshotStore`'s coarser three-field identity can see a handle silently resolve to a different
      physical node, or an occurrence index shift, between queries. Update the conditional language in
      `_collapse_identical_duplicates`'s docstring (`bajutsu/drivers/base.py`), which this item's own
      Introduction quotes as the residual cost being removed, and `DESIGN.md` / `docs/architecture.md`
      if either describes this behavior (BE-0113).

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
  `handleElements` and `handleSystemAlertQuery` — the handlers this item changes;
  `elementsResponse` stays unchanged.
- [`BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift`](../../BajutsuKit/Sources/BajutsuRunner/ElementProviding.swift):
  `isHittable(backingElement:)` — the existing native signal this item reuses.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py): `_actuate` — whose comment names
  the two paths that produce a `stale` reply, the second of which this item's *Motivation* traces to
  the wrong twin.

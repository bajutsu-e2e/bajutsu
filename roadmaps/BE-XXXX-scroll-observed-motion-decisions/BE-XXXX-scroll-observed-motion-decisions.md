**English** · [日本語](BE-XXXX-scroll-observed-motion-decisions-ja.md)

# BE-XXXX — Decide from observed element motion whether `scroll`'s region stopped or overshot

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-scroll-observed-motion-decisions.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md), [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md), [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) |
<!-- /BE-METADATA -->

## Introduction

The `scroll` action brings an off-screen element into view. It performs one bounded scroll gesture,
re-queries the user interface element tree, and repeats until the named target resolves with its frame
center inside the viewport. The action was added in
[BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md).

This item fixes two defects in that loop. Both make `scroll` fail on a target it could have reached.

The first defect is in how the loop decides that the content has ended. It compares the elements it
queried before a gesture with the elements it queried after. When nothing differs, it reports the end
of the content. On Android, a row taller than the screen reports the same position no matter how far
the content scrolls, so nothing differs even while the list is moving.

The second defect is in the size of one scroll step. A step is meant to advance 0.6 of the screen, and
the code assumes the content advances at most as far as the step asks for. If the gesture leaves any
momentum, the content advances further. Once it advances more than a full screen in one step, the
target can pass between two queries without ever being looked at.

The fix changes what the loop is allowed to conclude from two consecutive trees. The loop reports the
end of the content only when an element it has already seen move is still there and has stopped
moving. It treats two trees with no element in common as a step that may have skipped the target, and
it shrinks the gesture and looks back rather than reporting the target missing. A driver conformance
case then requires every backend to leave some overlap between consecutive steps, so a gesture that
flings fails the test suite rather than skipping targets during a run.

## Motivation

### How the loop decides the content has ended today

The loop builds a list of the region's elements, each with its identifier, its label, and its frame.
The region is the whole tree, or the elements inside the container named by `within`. After each
gesture the loop builds the same list again and compares the two. A gesture that moves content changes
some frame. So when nothing changes, the code concludes the region has reached its end and the target
is not in it, and it fails immediately. That immediate failure is useful: a mistyped selector fails at
once instead of spending the whole `maxScrolls` budget. The comparison uses trees the loop has already
queried, so it costs no extra `query()`
([BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)).

### Defect 1: a clipped frame hides the motion

Android's UI Automator reports an element's bounds clipped to the part that is visible. An element
taller than the screen therefore reports the same frame while the content scrolls behind it, because
the visible part is the whole screen both before and after.

We observed this on the Android showcase, on the conformance scroll screen. That screen has a row 1400
density-independent pixels tall, `conformance.scroll.tall`. Taking a `screencap` checksum before and
after a step over that row shows the pixels moved. The element list was byte-identical across the same
step. The loop cannot tell that case apart from a list that has stopped, and it treats it as the end of
the content.

The tall row is not the target that breaks. Its own clipped frame keeps a center on screen, so `scroll`
stops on it, and the conformance case that reveals a target taller than the viewport passes. The
targets that break are the ones further down, behind a screen-sized element the loop has to scroll
past. While that element is the only thing in the tree, every step looks like the end of the content. A
list below a full-width image or a tall embedded map has this shape. The failure text,
`the region stopped changing (end of content)`, points at the data rather than at the loop.

### Defect 2: a step can carry the target past the viewport

Each step asks for a travel of 0.6 of the viewport. The comment on that constant says the remaining 40
percent of overlap stops a target near the fold from slipping between two consecutive queries. That
holds only if the content moves no further than the gesture asks. A point just below the fold needs
more than a full viewport of travel to end up above the fold, and a 0.6 request cannot produce that on
its own. Momentum can: the content keeps going after the gesture ends. Once one step moves the content
more than a viewport, the target can pass between two queries unseen.

We saw this while trying a different Android gesture. A chained `input motionevent` pan, whose contacts
are close enough together to fling, produced a step that went from rows 8 through 18 straight to a tree
holding only `conformance.scroll.tall`. It skipped `conformance.scroll.row.19`. The loop then reported
the end of the content for a row that exists.

That pan was reverted, so no shipped backend is known to fling today. This half of the item is a latent
defect, not a failing lane. What remains is that the loop's correctness depends on a per-backend
property that nothing measures: any change to a scroll gesture, and any screen whose own scrolling
carries momentum past the gesture, re-opens the defect without warning.

### Why the two defects are one item

The obvious fix for each defect makes the other worse.

- Confirming the end of the content by taking a second step doubles that step's travel, which feeds
  defect 2.
- Shrinking the step to avoid overshoot moves fewer pixels per query, which makes a clipped frame even
  more likely to look unchanged, which feeds defect 1.

Both fixes also read the same thing: what two consecutive trees have in common. Deciding them
separately would mean building the same comparison twice, under two designs that pull in opposite
directions.

### Why a new item rather than reopening BE-0326

[BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) is `Implemented`. Every unit in
its breakdown is complete and its delivering pull request is recorded. This item changes what the loop
may conclude from a pair of trees, which is a new decision rather than an unfinished unit of the
original action. Moving a finished item back to `In progress` would also make it unclear which change
delivered which behavior.

## Detailed design

### Terms

- **Region** — the elements the loop compares across a step. Without `within`, the region is the whole
  tree. With `within`, it is the elements inside that container.
- **Region bounds** — the rectangle the region is clipped to. Without `within`, that is the viewport.
  With `within`, it is the container's frame.
- **Unclipped** — an element whose frame, along the scroll axis, has both edges strictly inside the
  region bounds. A backend that clips reports a clipped edge at the bounds, so only an unclipped
  element reports a position that belongs to the content.
- **Mover** — an element the loop has already seen change position during this `scroll` call. The loop
  records the identifier of every element whose frame it has observed moving, and keeps that record for
  the length of one `scroll` call.

### Deciding that a step moved the content

A step moved the content when either of these holds:

- some element unclipped in both trees changed its frame along the scroll axis, or
- the set of element identities in the region changed, meaning rows entered or left the tree.

A changed label alone does not count. A relative clock label that ticks, or a spinner that only changes
its text, is not scrolling. Today's comparison treats any difference as motion, including such a
change, which is why the loop can keep scrolling a list that has stopped.

One case survives this test: a node whose own frame animates, such as an indeterminate progress bar,
still reads as motion. That is the same weakness this item gives as its reason for rejecting
screenshot comparison, so the frame test does not escape it entirely. What differs is the
consequence. Such a region is never reported as ended, so it costs `maxScrolls` on an absent target,
and it never fails a target the loop can reach.

Every element seen moving by this test is added to the mover set.

### Deciding that the content has ended

The loop reports the end of the content only when the step did not move the content and one of these
holds:

- a mover is present in both trees, is unclipped in both, and did not move, or
- the region contains no clipped element at all.

The first condition is the sound one. An element that has already moved during this call is anchored to
the content, not to the screen, so its standing still means the content stands still. The second
condition keeps today's fast failure for ordinary lists: when nothing in the region is clipped, nothing
can hide motion, so an unchanged region really has stopped. A list of normal rows meets that condition
on the first step, so a mistyped selector still fails at once.

The mover condition is what a position-fixed element would otherwise break. A sticky header sits
unclipped inside the region and never moves, by design. Treating it as evidence would report the end of
the content while the list scrolls behind it. Requiring a mover excludes it, because a sticky header
never enters the mover set.

When the step did not move the content and neither condition holds, the loop cannot tell. It takes
another step. If it spends `maxScrolls` that way, it fails with a message saying it could not observe
whether the region moved, rather than saying the list ended. That distinction matters to an author
reading the failure.

The cost is that a target genuinely absent from such a region is now found absent when the budget runs
out, not on the first unchanged step. We accept it: the alternative is today's behavior of failing a
reachable target, and a spent budget is still a deterministic failure. *Alternatives considered*
records the backend signal that would restore the fast failure, and why this item defers it.

### Deciding that a step may have skipped the target

Two consecutive trees that share no unclipped element mean the content advanced at least a full
viewport. Nothing that was in view survived the step. That is exactly the condition under which the
target can pass unseen, and it is directly visible in the two trees the loop already holds.

On such a step the loop does two things. It halves the step fraction, with a floor at `swipe`'s 0.125
default, so later steps observe the content more finely. It then takes one step in the opposite
direction to query the span that passed. Every gesture counts against `maxScrolls`, and a reversing
step may not itself trigger another reversal, so the recovery cannot oscillate without bound. When a
step still shares nothing at the floor fraction, the loop fails with a message naming the overshoot,
rather than reporting the target absent.

Measuring the distance the content travelled would not work here. The distance can only be measured
from elements common to both trees, and an overshooting step leaves none. The absence of a shared
element is the signal.

### The conformance check

Each backend's `scroll` is documented as non-inertial, and nothing verifies it. The driver conformance
suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) is where
this repository turns a driver contract into a checked property, and it already drives the `scroll`
loop against `FakeDriver`, Playwright, XCUITest, and adb.

The new case takes one step on the conformance scroll screen and asserts that the trees before and
after share at least one unclipped element. That is the non-inertial contract stated in terms the tree
can show: consecutive viewports overlap. A backend that flings shares nothing and fails the case. The
check is what keeps a flinging gesture out of the shipped backends; the in-loop recovery above covers a
screen whose own scrolling carries momentum past a gesture the suite found acceptable.

### Relation to the pending read-lag fix

A separate fix for a third defect in the same loop is in flight on branch
`claude/adb-scroll-stale-read-fix`. It is unmerged and has no pull request.

That fix handles a backend whose tree lags a gesture it has already applied. On the continuous
integration Android emulator, the tree queried right after a scroll returns the pre-scroll snapshot,
and the loop turns that late tree into an end-of-content failure. The fix adds a `ReadLagProvider`
protocol through which a backend declares how long its reads may lag. adb declares four seconds; every
other backend declares nothing and keeps failing immediately. The loop then re-queries within that
budget until the region's element list changes.

Whichever change lands second has to reconcile the two, because the pending fix waits on the comparison
this item replaces. A region with no unclipped element never changes that list, so the re-query would
spend the whole four-second budget on every step and then report no change. Under this item that step
is one the loop cannot judge, but it now costs four seconds each time. The reconciliation is to wait on
the motion test above instead, and to skip the wait when the region has no unclipped element, since
waiting cannot produce a signal the tree is unable to show. The work breakdown carries that as its own
unit.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **Region bounds and the unclipped test.**

   Add to [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py)
   a helper for the region bounds (the viewport, or the `within` container's frame) and a predicate for
   an unclipped element along the scroll axis. Write both as pure functions over the element lists the
   loop already holds, so the fast gate can test them without a device.

2. **The motion test and the mover set.**

   Replace the "any difference means motion" comparison with the two-part test above: a frame change on
   an element unclipped in both trees, or a change in the region's set of element identities. Record
   every element seen moving in a mover set that lives for one `scroll` call.

3. **The end-of-content decision.**

   Report the end of the content only when the step did not move the content and either a mover stood
   still or the region has no clipped element. Otherwise take another step, and on spending
   `maxScrolls` that way, fail with a message that says the loop could not observe whether the region
   moved.

4. **Skip detection and recovery.**

   When two consecutive trees share no unclipped element, halve the step fraction with a floor at
   0.125 and take one reversing step to query the span that passed. Count every gesture against
   `maxScrolls`, forbid a reversing step from triggering another reversal, and fail at the floor with a
   message naming the overshoot. Correct the comment on the step fraction so it states the assumption
   it rests on.

5. **The conformance overlap check.**

   Add a case to [`tests/driver_conformance.py`](../../tests/driver_conformance.py) asserting that one
   step on the conformance scroll screen leaves at least one unclipped element shared between the trees
   before and after, on every backend the suite drives.

6. **Reconciliation with the read-lag re-query.**

   Change the re-query's wait condition from "the element list differs" to the motion test from unit 2,
   and skip the wait when the region has no unclipped element. This unit belongs to whichever of this
   item and the `claude/adb-scroll-stale-read-fix` branch lands second.

7. **Tests.**

   Over `FakeDriver` in [`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py), cover: an ordinary
   stopped list failing at once through the no-clipped-element condition; a region whose only element is
   clipped scrolling to `maxScrolls` and failing with the distinct message; a sticky header that stands
   still not being read as the end of the content; a step sharing no element shrinking the fraction and
   revealing the target through the reversing step; and a step still sharing nothing at the floor
   failing with the overshoot message. Cover the motion test directly, including a label-only change
   that must not count as motion.

8. **Docs.**

   Document in [`docs/scenarios.md`](../../docs/scenarios.md) and its Japanese mirror what `scroll`
   reports when it cannot observe the region's motion, so an author reading the failure does not read it
   as a claim that the list ended. Record the overlap requirement in the driver conformance section of
   [`docs/architecture.md`](../../docs/architecture.md) and its Japanese mirror, since it becomes part
   of the `Driver` contract.

### Prime directives preserved

- **AI never judges.**

  Every decision is arithmetic over frames the backend reported: an edge-inside-bounds test, a frame
  comparison, and a set membership check. No model call enters the `run` path.

- **Determinism first.**

  The loop keeps its bounded condition-wait shape and adds no fixed `sleep`. Each change replaces an
  assumption with an observation: the end of the content now needs an element the loop watched move, and
  the step-overlap assumption becomes a conformance requirement. Every outcome fails within
  `maxScrolls` and names the condition it hit.

- **App-agnostic.**

  The tests, the mover set, and the recovery live in the shared handler and read only the elements and
  viewport the `Driver` interface returns. No per-app or per-backend branch appears, and the conformance
  check holds every backend to the same contract.

## Alternatives considered

- **Extend BE-0326 instead of adding an item.**

  Rejected. [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) is `Implemented`, with
  every unit ticked and its delivering pull request recorded. Moving a finished item back to
  `In progress` would obscure the scope it delivered, and the design here is a new decision about what
  the loop may conclude rather than an unfinished part of the action. The two items link to each other.

- **Split the two defects into separate items.**

  Rejected. Each defect's obvious fix makes the other worse, as the Motivation shows, and both read what
  two consecutive trees have in common. Separate items would build the same comparison twice under
  designs that pull against each other.

- **Require several consecutive unchanged steps before reporting the end of the content.**

  Rejected, and already rejected once on evidence while root-causing the Android scroll failures. A
  confirming step scrolls the region again, doubling that step's travel, which feeds defect 2. It is
  also unsound for the case at hand: a region whose only element is clipped reports no change on every
  step, however many the loop takes.

- **Measure how far the content travelled and compare it against the viewport.**

  Rejected. The distance can only be measured from elements present and unclipped in both trees, and a
  step that overshoots by more than a viewport leaves none. The measurement would return nothing in
  exactly the case it was meant to detect. A conformance check built on it would pass on a flinging
  backend for the same reason. The absence of a shared element carries the signal instead, which is why
  this item uses that.

- **Ask the backend whether the region can scroll further.**

  Deferred, and the most direct fix available. Android's accessibility node exposes its available scroll
  actions, so the resident read channel could report whether more content exists in a direction. A web
  backend can compare a scroll offset against the scroll extent. Such a signal would end the loop
  authoritatively and restore the fast failure this item gives up when the region has no unclipped
  element. It is deferred because the signal is not available everywhere: the `uiautomator dump`
  fallback path reports only whether an element is scrollable, not whether it can scroll further, and
  XCUITest exposes nothing equivalent. Adding it means a new opt-in `Driver` protocol plus work in the
  resident runner, while the tests above need neither.

- **Fail immediately when the loop cannot tell whether the region moved.**

  Rejected. Reporting "cannot tell" at once would be honest, but it would fail a target the loop reaches
  by continuing, which is the defect this item exists to fix. Spending the budget and naming the
  distinction in the failure keeps the diagnosis without giving up the reachable case.

- **Compare screenshots instead of element frames.**

  Rejected. A pixel checksum is what proved the region was moving during the investigation, so it does
  find the motion a clipped frame hides. As a per-step mechanism it costs a capture and transfer on
  every backend, and it reports motion for a blinking cursor, a spinner, or a clock, which would turn a
  stopped region into a loop that runs to `maxScrolls`. The frames the loop already holds carry the same
  information wherever an element is unclipped.

- **Shrink the step fraction instead of detecting a skip.**

  Rejected as the primary shape. A smaller step lowers the chance that travel exceeds a viewport without
  bounding it, because the excess comes from momentum the fraction does not control. It also costs
  reach: the same `maxScrolls` budget covers less content, and each step moves fewer pixels, so a
  clipped frame is more likely to look unchanged. Detecting the skip bounds the failure instead of
  making it rarer, and the conformance check keeps a flinging gesture out of the shipped backends.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — region bounds and the unclipped test
- [ ] Unit 2 — the motion test and the mover set
- [ ] Unit 3 — the end-of-content decision
- [ ] Unit 4 — skip detection, step shrinking, and the reversing step
- [ ] Unit 5 — conformance overlap check
- [ ] Unit 6 — reconciliation with the read-lag re-query
- [ ] Unit 7 — tests over `FakeDriver`
- [ ] Unit 8 — docs for the new failure and the overlap requirement

## References

- [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py) — the loop, its element comparison, and the step fraction this item changes
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — the `Driver` interface, `ViewportProvider`, and the frame helpers the tests use
- [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) — the Android backend whose clipped frames and read lag surfaced both defects
- [`tests/driver_conformance.py`](../../tests/driver_conformance.py) — the shared scroll cases the overlap check joins
- [`docs/scenarios.md`](../../docs/scenarios.md) — the `scroll` action's authoring documentation
- [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) — the `scroll` action this item repairs
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the suite that turns a driver contract into a checked property
- [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md) — queried-tree reuse, which the new tests keep
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) — the Android resident read channel whose reads race a gesture's accessibility update
- [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) — the adb scroll-into-view recovery that `scroll` generalizes

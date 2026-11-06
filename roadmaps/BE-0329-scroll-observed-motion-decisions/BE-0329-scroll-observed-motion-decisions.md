**English** · [日本語](BE-0329-scroll-observed-motion-decisions-ja.md)

# BE-0329 — Decide from observed element motion whether `scroll`'s region stopped or overshot

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0329](BE-0329-scroll-observed-motion-decisions.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0329") |
| Implementing PR | [#1460](https://github.com/bajutsu-e2e/bajutsu/pull/1460) (decisions, recovery, and the rendered-screen confirmation), [#1787](https://github.com/bajutsu-e2e/bajutsu/pull/1787) (the conformance overlap check and the driver-contract note) |
| Topic | Scenario authoring features |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md), [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md), [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) |
<!-- /BE-METADATA -->

## Introduction

The `scroll` action brings an off-screen element into view. The action performs one bounded scroll gesture and re-queries the user interface element tree. It repeats until the named target resolves with its frame center inside the viewport. [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) added the action.

This item fixes two defects in that loop. Both defects make `scroll` fail on a target that the action could have reached.

The first defect is in how the loop decides that the content has ended. The loop compares the elements that it queried before a gesture with the elements that it queried after. When nothing differs, the loop reports the end of the content. On Android, a row taller than the screen reports the same position no matter how far the content scrolls. Nothing differs even while the list is moving.

The second defect is in the size of one scroll step. A step is meant to advance 0.6 of the screen. The code assumes that the content advances at most as far as the step asks for. If the gesture leaves any momentum, the content advances further. Once the content advances more than a full screen in one step, the target can pass between two queries without ever being looked at.

The fix changes what the loop may conclude from two consecutive trees. The loop reports the end of the content in one of two cases.

- An element that the loop has already seen move is still there and has stopped moving.
- Every element in the region is clipped, so the tree can show nothing. In that case, the screen as drawn must also stop changing.

A step after which nothing that had been in view is on screen at all may have skipped the target. The loop treats such a step as a possible skip. The loop shrinks the gesture and looks back, rather than reporting the target missing. A driver conformance case then requires every backend to leave some overlap between consecutive steps. A gesture that flings therefore fails the test suite, instead of skipping targets during a run.

## Motivation

### How the loop decides the content has ended today

The loop builds a list of the elements in the region, each with its identifier, its label, and its frame. The region is the whole tree, or the elements inside the container named by `within`. After each gesture, the loop builds the same list again and compares the two lists. A gesture that moves content changes some frame. When nothing changes, the code concludes that the region has reached its end and that the target is not in it. The loop then fails immediately.

The immediate failure is useful. A mistyped selector fails at once, instead of spending the whole `maxScrolls` budget. The comparison uses trees that the loop has already queried, so the comparison costs no extra `query()` ([BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)).

### Defect 1: a clipped frame hides the motion

Android's UI Automator reports the bounds of an element clipped to the part that is visible. An element taller than the screen reports the same frame while the content scrolls behind the element. The visible part is the whole screen both before and after.

We observed this on the Android showcase, on the conformance scroll screen. That screen has a row 1400 density-independent pixels tall, `conformance.scroll.tall`. We took a `screencap` checksum before and after a step over that row. The checksums show that the pixels moved. The element list was byte-identical across the same step. The loop cannot tell that case apart from a list that has stopped. The loop treats the case as the end of the content.

The tall row is not the target that breaks. The clipped frame of the row keeps a center on screen, so `scroll` stops on the row. The conformance case that reveals a target taller than the viewport passes. The targets that break are the ones further down, behind a screen-sized element that the loop has to scroll past. While that element is the only thing in the tree, every step looks like the end of the content. A list below a full-width image or a tall embedded map has this shape. The failure text, `the region stopped changing (end of content)`, points at the data rather than at the loop.

### Defect 2: a step can carry the target past the viewport

Each step asks for a travel of 0.6 of the viewport. The comment on that constant says that the remaining 40 percent of overlap stops a target near the fold from slipping between two consecutive queries. That claim holds when the content moves no further than the gesture asks. A point just below the fold needs more than a full viewport of travel to end up above the fold. A 0.6 request cannot produce that travel on its own. Momentum can, because the content keeps going after the gesture ends. Once one step moves the content more than a viewport, the target can pass between two queries unseen.

We saw this while trying a different Android gesture. A chained `input motionevent` pan has contacts close enough together to fling. The pan produced a step that went from rows 8 through 18 straight to a tree holding `conformance.scroll.tall` alone. The step skipped `conformance.scroll.row.19`. The loop then reported the end of the content for a row that exists.

That pan was reverted, so no shipped backend is known to fling today. This half of the item is a latent defect, not a failing lane. A problem remains: the correctness of the loop depends on a per-backend property that nothing measures. Any change to a scroll gesture re-opens the defect without warning. So does any screen whose own scrolling carries momentum past the gesture.

### Why the two defects are one item

The obvious fix for each defect makes the other defect worse.

- Confirming the end of the content by taking a second step doubles the travel of that step, which feeds defect 2.
- Shrinking the step to avoid overshoot moves fewer pixels per query. A clipped frame then looks even more likely to be unchanged, which feeds defect 1.

Both fixes also read the same thing: what two consecutive trees have in common. Deciding the two defects separately would mean building the same comparison twice, under two designs that pull in opposite directions.

### Why a new item rather than reopening BE-0326

[BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) is `Implemented`. Every unit in the work breakdown of BE-0326 is complete, and its delivering pull request is recorded. This item changes what the loop may conclude from a pair of trees. That change is a new decision, not an unfinished unit of the original action. Moving a finished item back to `In progress` would also make it unclear which change delivered which behavior.

## Detailed design

### Terms

- **Region** — the elements that the loop compares across a step. Without `within`, the region is the whole tree. With `within`, the region is the elements inside that container.
- **Region bounds** — the rectangle that the region is clipped to. Without `within`, the bounds are the viewport. With `within`, the bounds are the frame of the container.
- **Unclipped** — an element whose frame, along the scroll axis, has both edges strictly inside the region bounds. A backend that clips reports a clipped edge at the bounds. An unclipped element alone reports a position that belongs to the content.
- **Cut off** — an element that the region bounds cross. The true extent of the element is unknown, and the element may reach far past the bound. A cut-off element differs from an element lying wholly outside the bounds. The outside element is simply out of view. On a tree that keeps such elements, the outside element still reports a position belonging to the content.
- **Mover** — an element that the loop has already seen change position during this `scroll` call. The loop records the identifier of every element whose frame the loop has observed moving. The loop keeps that record for the length of one `scroll` call.

### Deciding that a step moved the content

A step moved the content when any of the following holds.

- Some element that the bounds cut off in neither tree changed its frame along the scroll axis.
- The set of element identities in the region changed, meaning rows entered or left the tree.
- The number of elements in view changed, meaning one element travelled as far as a region bound and took its position out of the comparison.

A changed label alone does not count. A relative clock label that ticks is not scrolling. A spinner that changes its text is not scrolling either. Today's comparison treats any difference as motion, including such a change. For that reason, the loop can keep scrolling a list that has stopped.

One case survives this test: a node whose own frame animates, such as an indeterminate progress bar, still reads as motion. The same weakness keeps a per-step screenshot comparison out of this design (*Alternatives considered*), so the frame test does not escape the weakness entirely. The consequence differs. Such a region is never reported as ended. The region costs `maxScrolls` on an absent target, and the region never fails a target that the loop can reach.

The loop adds every element seen moving by this test to the mover set.

### Deciding that the content has ended

The loop reports the end of the content when the step did not move the content and one of the following holds.

- A mover is present in both trees, is unclipped in both, and did not move. In addition, no element cut off by the region bounds could be a scroll container that the mover does not belong to.
- The region bounds cut nothing off at all.
- The screen as drawn did not change across the step either.

The first condition is the sound one. An element that has already moved during this call is anchored to the content, not to the screen. When the element stands still, the content stands still — of *that* content. The qualification covers a mover outside the scrolling region, which would otherwise break the condition.

- A collapsing app bar shifts once on the first scroll and then pins. The app bar becomes a mover that stands still forever while the list scrolls behind it.
- A large title contracting on the first scroll does the same.

A cut-off element speaks against a mover only when the cut-off element is larger along the scroll axis and does not contain the mover. Only something larger could hold content of its own that the mover sits outside of. The list that a row belongs to contains that row. The list that an app bar sits above does not contain the app bar. The same rule leaves the own neighbour of a mover alone. The row that the screen's edge cut off is no larger than the mover, so the row can hold nothing.

A position-fixed element is excluded more simply. A sticky header never moves, so the header never enters the mover set at all.

The second condition keeps today's fast failure, and keeps it where the tree earns it. When the bounds cut nothing off, nothing can hide motion, so an unchanged region really has stopped. A tree of plain rows meets the condition on the first step. A tree that reports a window or a root view spanning the screen does not meet the condition, because that element is cut off by definition. On the backends that report such an element, the condition never fires. The mover or the screen below carries the fast failure instead.

When the step did not move the content and none of the three conditions holds, the loop cannot tell. The loop takes another step. If the loop spends `maxScrolls` that way, the loop fails with a message saying that the loop could not observe whether the region moved. The message does not say that the list ended. That distinction matters to an author reading the failure.

### Confirming from the screen as drawn what the tree cannot show

The first two conditions both read the element tree. A region whose every element is clipped defeats both conditions at once. Nothing in the region reports a position that belongs to the content, so the loop has no element to watch move and none to find standing still. The pixels carry the answer there. A `screencap` checksum proved that the Android region was moving while its element list stayed byte-identical. The same checksum, compared across a step, decides the case that the tree cannot.

The loop captures a checksum on a step that no read could judge. A scroll whose steps the element tests settle pays for no capture at all. The loop waits for two consecutive captures to agree before the loop trusts one. That wait keeps a screen still being drawn from reading as a screen that moved. The loop bounds the wait. A screen whose own animation never settles yields no checksum, and the step stays unjudged.

Two consecutive unjudged steps whose settled checksums agree are the end of the content. The fast failure is restored for the very region that defeated the tree, at the price of one extra step. A backend that cannot capture a screen at all keeps the slower outcome below.

What no capture can settle remains. An indeterminate progress bar keeps the checksum moving, and a backend without a screenshot has no checksum to compare. There, a target that is genuinely absent is found absent when the budget runs out, not on the first unchanged step. We accept that outcome. The alternative is today's behavior of failing a reachable target, and a spent budget is still a deterministic failure. *Alternatives considered* records the backend signal that would end the loop authoritatively, and why this item defers the signal.

### Deciding that a step may have skipped the target

Suppose nothing that was in view before the step is on screen at all after the step. Then the content advanced at least a full viewport. That is exactly the condition under which the target can pass unseen. The condition is directly visible in the two trees that the loop already holds.

*On screen at all* is what makes the inference sound, and partly counts. An element that the step pushed half off the edge is still evidence that the two viewports overlapped, so nothing between them went unqueried. Reading the fully visible elements alone would fire on every step of a screen that shows one element at a time. A carousel card, or a row nearly as tall as the viewport, is replaced in that set by its neighbour after far less than a viewport of travel. The loop would halve the step and look back on an ordinary list.

The inference also needs each of the two trees to have had something in view. A tree with nothing in view describes a viewport covered by an element larger than the viewport. That is the case above where the tree shows nothing, rather than evidence of travel. The ordinary step that scrolls *into* such an element ends that way while advancing a fraction of a viewport. So does the step that scrolls back out. The price is a fling that lands inside an element taller than the viewport. The loop treats that fling as a step that the loop cannot judge. The conformance check below keeps such a gesture out of the shipped backends.

On such a step, the loop does two things.

- The loop halves the step fraction, with a floor at the 0.125 default of `swipe`. Later steps then observe the content more finely.
- The loop takes one step in the opposite direction to query the span that passed.

Every gesture counts against `maxScrolls`. A reversing step may not itself trigger another reversal, so the recovery cannot oscillate without bound. When a step still shares nothing at the floor fraction, the loop fails with a message naming the overshoot, rather than reporting the target absent.

Measuring the distance that the content travelled would not work here. The distance can be measured from elements common to both trees alone, and an overshooting step leaves none. The absence of a shared element is the signal.

### The conformance check

Each backend's `scroll` is documented as non-inertial, and nothing verifies the claim. The driver conformance suite ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) is where this repository turns a driver contract into a checked property. The suite already drives the `scroll` loop against `FakeDriver`, Playwright, XCUITest, and adb.

The new case takes one step on the conformance scroll screen and asserts that the trees before and after share at least one unclipped element. That assertion states the non-inertial contract in terms that the tree can show: consecutive viewports overlap. A backend that flings shares nothing and fails the case. The check keeps a flinging gesture out of the shipped backends. The in-loop recovery above covers a screen whose own scrolling carries momentum past a gesture that the suite found acceptable.

### Relation to the read-lag fix

A separate fix for a third defect in the same loop has since merged. This item is the change that reconciles the two.

That fix handles a backend whose tree lags a gesture that the backend has already applied. On the continuous integration Android emulator, the tree queried right after a scroll returns the pre-scroll snapshot. The loop turns that late tree into an end-of-content failure. The fix adds a `ReadLagProvider` protocol through which a backend declares how long its reads may lag. adb declares four seconds. Every other backend declares nothing and keeps failing immediately. The loop then re-queries within that budget until the element list of the region changes.

This item lands second, which obliges this item to reconcile the two. The merged fix waits on the comparison that this item replaces. A region with no unclipped element never changes that list. The re-query would spend the whole four-second budget on every step and then report no change. Under this item, that step is one that the loop cannot judge, but the step now costs four seconds each time. The reconciliation has two parts. The re-query waits on the motion test above instead. The wait is skipped when the region has no unclipped element, since waiting cannot produce a signal that the tree is unable to show. The work breakdown carries the reconciliation as its own unit.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **Region bounds and the unclipped test.**

   Add two helpers to [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py). The first returns the region bounds (the viewport, or the frame of the `within` container). The second is a predicate for an unclipped element along the scroll axis. Write both as pure functions over the element lists that the loop already holds, so the fast gate can test them without a device.

2. **The motion test and the mover set.**

   Replace the "any difference means motion" comparison with the two-part test above. The first part is a frame change on an element unclipped in both trees. The second part is a change in the set of element identities of the region. Record every element seen moving in a mover set that lives for one `scroll` call.

3. **The end-of-content decision.**

   Report the end of the content when the step did not move the content and either a mover stood still or the region has no clipped element. Otherwise take another step. On spending `maxScrolls` that way, fail with a message that says the loop could not observe whether the region moved.

4. **The rendered-screen confirmation.**

   On a step that neither tree test could judge, capture the screen. Wait for two consecutive captures to agree before trusting one. End the region when two such steps in a row agree. Bound that wait. Treat a backend with no screenshot and a screen that never settles alike as still unjudged. Keep the capture off every step that the tree tests already settle. The checksum is arithmetic over bytes, so no model enters the `run` path.

5. **Skip detection and recovery.**

   Suppose two consecutive trees each report something in view, and nothing that was in view before is on screen at all after. Halve the step fraction with a floor at 0.125 and take one reversing step to query the span that passed. Count every gesture against `maxScrolls`. Forbid a reversing step from triggering another reversal. Fail at the floor with a message naming the overshoot. Correct the comment on the step fraction so that the comment states the assumption it rests on.

6. **The conformance overlap check.**

   Add a case to [`tests/driver_conformance.py`](../../tests/driver_conformance.py). The case asserts that one step on the conformance scroll screen leaves at least one unclipped element shared between the trees before and after, on every backend that the suite drives.

7. **Reconciliation with the read-lag re-query.**

   Change the wait condition of the re-query from "the element list differs" to the motion test from unit 2. Skip the wait when the region holds elements but reports a position for none of them. A region that reports *nothing* keeps waiting. A read that describes an empty screen is the shape that a wedged accessibility bridge or a mid-transition dump takes, and the read must not be turned into a verdict. The read-lag fix merged first, so this unit belongs to this item.

8. **Tests.**

   Over `FakeDriver` in [`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py), cover the following cases.

   - An ordinary stopped list fails at once through the no-clipped-element condition.
   - A region whose sole element is clipped scrolls to `maxScrolls` and fails with the distinct message.
   - The same region ends at once when its rendered screen stops changing, and is never called ended while that screen keeps changing.
   - A sticky header that stands still is not read as the end of the content.
   - A step sharing no element shrinks the fraction and reveals the target through the reversing step.
   - A step that still shares nothing at the floor fails with the overshoot message.
   - A step into an element taller than the viewport is not read as an overshoot.

   Cover the region tests directly, including a label-only change that must not count as motion.

9. **Docs.**

   Document in [`docs/scenarios.md`](../../docs/scenarios.md) and its Japanese mirror what `scroll` reports when the action cannot observe the motion of the region. An author reading the failure then does not read it as a claim that the list ended. Record the overlap requirement in the driver conformance section of [`docs/architecture.md`](../../docs/architecture.md) and its Japanese mirror, since the requirement becomes part of the `Driver` contract.

### Prime directives preserved

- **AI never judges.**

  Every decision is arithmetic over what the backend reported. The decisions are an edge-inside-bounds test, a frame comparison, and a set membership check. Where the frames cannot decide, a byte checksum of the captured screen is compared for equality. Nothing interprets an image, and no model call enters the `run` path.

- **Determinism first.**

  The loop keeps its bounded condition-wait shape and adds no fixed `sleep`. Each change replaces an assumption with an observation. The end of the content now needs an element that the loop watched move. The step-overlap assumption becomes a conformance requirement. Every outcome fails within `maxScrolls` and names the condition it hit.

- **App-agnostic.**

  The tests, the mover set, and the recovery live in the shared handler. They read the elements and the viewport that the `Driver` interface returns, and nothing else. No per-app or per-backend branch appears. The conformance check holds every backend to the same contract.

## Alternatives considered

- **Extend BE-0326 instead of adding an item.**

  Rejected. [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) is `Implemented`, with every unit ticked and its delivering pull request recorded. Moving a finished item back to `In progress` would obscure the scope that the item delivered. The design here is a new decision about what the loop may conclude, not an unfinished part of the action. The two items link to each other.

- **Split the two defects into separate items.**

  Rejected. The obvious fix for each defect makes the other defect worse, as the Motivation shows. Both fixes also read what two consecutive trees have in common. Separate items would build the same comparison twice under designs that pull against each other.

- **Require several consecutive unchanged steps before reporting the end of the content.**

  Rejected, and already rejected once on evidence while root-causing the Android scroll failures. A confirming step scrolls the region again, which doubles the travel of that step and feeds defect 2. The approach is also unsound for the case at hand. A region whose sole element is clipped reports no change on every step, however many steps the loop takes.

- **Measure how far the content travelled and compare it against the viewport.**

  Rejected. The distance can be measured from elements present and unclipped in both trees alone. A step that overshoots by more than a viewport leaves none. The measurement would return nothing in exactly the case that the measurement was meant to detect. A conformance check built on the measurement would pass on a flinging backend for the same reason. This item uses the absence of a shared element as the signal instead.

- **Ask the backend whether the region can scroll further.**

  Deferred, and the most direct fix available. Android's accessibility node exposes its available scroll actions, so the resident read channel could report whether more content exists in a direction. A web backend can compare a scroll offset against the scroll extent. Such a signal would end the loop authoritatively. The signal would also restore the fast failure that this item gives up when the region has no unclipped element.

  The item defers the signal because the signal is not available everywhere. The `uiautomator dump` fallback path reports whether an element is scrollable, and does not report whether the element can scroll further. XCUITest exposes nothing equivalent. Adding the signal means a new opt-in `Driver` protocol plus work in the resident runner, while the tests above need neither.

- **Fail immediately when the loop cannot tell whether the region moved.**

  Rejected. Reporting "cannot tell" at once would be honest. But the report would fail a target that the loop reaches by continuing, which is the defect this item exists to fix. Spending the budget and naming the distinction in the failure keeps the diagnosis without giving up the reachable case.

- **Compare screenshots instead of element frames, on every step.**

  Rejected as the per-step mechanism, and adopted in the narrow case that the tree cannot judge (*Confirming from the screen as drawn what the tree cannot show*). A pixel checksum proved during the investigation that the region was moving. The checksum does find the motion that a clipped frame hides. Two costs rule out the per-step use.

  - Paying for a capture and a transfer on every step of every backend buys nothing wherever an unclipped element already reports the position of the content.
  - A checksum reports motion for a blinking cursor, a spinner, or a clock. A stopped region would then turn into a loop that runs to `maxScrolls`.

  Confined to the step that no read could judge, the capture falls on the steps that would otherwise spend the whole budget. A screen that never settles is declined rather than read as motion.

- **Shrink the step fraction instead of detecting a skip.**

  Rejected as the primary shape. A smaller step lowers the chance that travel exceeds a viewport, without bounding the travel. The excess comes from momentum, which the fraction does not control. A smaller step also costs reach. The same `maxScrolls` budget covers less content, and each step moves fewer pixels, so a clipped frame is more likely to look unchanged. Detecting the skip bounds the failure instead of making the failure rarer. The conformance check keeps a flinging gesture out of the shipped backends.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — region bounds and the unclipped test
- [x] Unit 2 — the motion test and the mover set
- [x] Unit 3 — the end-of-content decision
- [x] Unit 4 — the rendered-screen confirmation
- [x] Unit 5 — skip detection, step shrinking, and the reversing step
- [x] Unit 6 — conformance overlap check
- [x] Unit 7 — reconciliation with the read-lag re-query
- [x] Unit 8 — tests over `FakeDriver`
- [x] Unit 9 — docs for the new failure and the overlap requirement

Log:

- [#1460](https://github.com/bajutsu-e2e/bajutsu/pull/1460) — replaced the whole-frame comparison with
  the region projection and its three decisions, added the rendered-screen confirmation for a region the
  tree cannot judge, added skip detection with the shrink and the look-back step, reconciled the
  read-lag re-query with the motion test, and documented the new failures in `docs/scenarios.md` and
  `docs/architecture.md` with their Japanese mirrors. Review of the first pass found three cases where
  the item's literal rule broke a case the item protects — pinned chrome as a mover, an empty read as
  evidence, and a one-element-at-a-time screen read as an overshoot — and the design records the
  qualification each one needed. The conformance overlap check (unit 6) and its driver-contract note are
  deferred.
- [#1787](https://github.com/bajutsu-e2e/bajutsu/pull/1787) — added the conformance overlap check (unit 6) and recorded the requirement in the
  driver conformance section of `docs/architecture.md` and its Japanese mirror (unit 9), which
  completes the item. The check takes one step on the conformance scroll screen and asserts the reads
  before and after share at least one element the region bounds do not clip, so a backend whose
  gesture flings fails the suite rather than skipping targets during a run.

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

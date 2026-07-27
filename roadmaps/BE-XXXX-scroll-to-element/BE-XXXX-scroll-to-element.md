**English** · [日本語](BE-XXXX-scroll-to-element-ja.md)

# BE-XXXX — The `scroll` action: scroll until an element appears

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-scroll-to-element.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity.md), [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md) |
<!-- /BE-METADATA -->

## Introduction

A new scenario action, `scroll`, brings an off-screen element into view.
It scrolls a region in one direction and re-queries the tree after each step.
It stops when the named target is on-screen.
It fails when it hits a bound.
Each step is non-inertial: it advances a fixed screen-relative distance and leaves no momentum.
One step never flings the target past the viewport.
The action works the same on iOS (XCUITest), Android (adb), and web (Playwright).
All three sit behind one `Driver` interface.
A scenario that scrolls to a below-the-fold control reads and runs the same on each backend.

## Motivation

Authors need to reach off-screen elements on tall screens.
Examples include a logout button at the bottom of Settings.
They also include a late feed row or a submit button under a tall form.
Bajutsu has no direct way to express that need today.
The portable idiom is a hand-tuned chain of `swipe` steps plus a `wait`.
The showcase fixture shows how fragile that chain is.
See [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml).
The author must anchor each `swipe` on a row that stays visible.
The author must also step through the list with a one-swipe margin.
The comments explain why: fling distance varies by device and render speed.
A CI emulator scrolls less per swipe than a hardware device.
An extra swipe covers that gap without over-scrolling on a faster device.
The author is compensating for scroll momentum by hand.

That momentum causes the fragility.
A non-inertial scroll is a determinism need, not a convenience.
A fling gesture imparts velocity.
Travel after lift depends on scroll physics and frame rate.
The same gesture carries a fast device farther than a slow one.
A target in that overshoot can land below the fold on one device.
It can land above the fold on another.
A fixed swipe count that works locally can fail in CI.
It can also scroll the target past the top before a `wait` sees it.
A non-inertial scroll removes that variable.
Each step advances a bounded distance and then stops.
The re-query after every step catches the target in that viewport.
Prime directive 2 already forbids fixed `sleep` for the same reason.
We replace an unpredictable duration with a checked condition.
Here we apply that idea to scroll distance.

The gap is also a portability asymmetry.
The adb backend already scrolls toward an off-screen action target.
It re-queries under a retry bound.
See [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md).
The code lives in `_scroll_into_view` in [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py).
[`docs/drivers.md`](../../docs/drivers.md) marks that recovery as adb exclusive.
That path is a robustness net, not the portable idiom.
XCUITest and Playwright still fail a `tap` when the target starts off-screen.
The same scenario can pass on Android after retries.
It can fail on iOS and web.
The documented portable answer stays the hand-tuned `swipe` chain.
An explicit cross-backend `scroll` action closes the asymmetry.
Authors write one deterministic construct and read it the same everywhere.

## Detailed design

### The `scroll` action

```yaml
# Bring an off-screen row into view, then tap it.
- scroll:
    to: { id: notice.row.20 }   # the element to reveal (the loop's stop condition)
    direction: down             # up | down | left | right (default: down)
- tap: { id: notice.row.20 }
```

```yaml
# Scroll a specific scrollable region and cap the attempts.
- scroll:
    to: { label: "Log out", traits: [button] }
    direction: down
    within: { id: settings.list }   # the scrollable container to gesture inside
    maxScrolls: 25                   # bound (default 15); fail if still missing
```

The action carries a target selector and a direction.
It also accepts two optional refinements.

- **`to`** (required): the [selector](../../docs/selectors.md) to reveal.
  This is the loop's stop condition.
  The condition is stricter than mere existence.
  The action returns when `to` resolves and its frame sits inside the viewport.
  Existence alone is not enough.
  A backend can keep an off-screen element in the tree.
  The web backend's `query()` returns a Document Object Model (`DOM`) node that is off-screen.
  A native lazy list drops an off-screen row from the tree.
  A `wait: { for: … }` predicate checks existence alone.
  That wait would treat a scrolled-away web element as found and never scroll.
  Requiring the frame inside the viewport makes the reveal real on every backend.
  Every queried element already carries its frame.
  Unit 3 supplies the viewport bounds for that comparison.

- **`direction`** (default `down`): which way to scroll through the content.
  This is the direction the viewport advances.
  That direction is the inverse of the finger gesture.
  `down` scrolls farther down the content to reveal below-the-fold items.
  The driver realizes it as an upward finger swipe.
  The content slides up as the viewport moves down.
  `up`, `left`, and `right` cover the other axes.
  Because `direction` names scroll direction, not the finger, it reads opposite to `swipe`.
  `swipe` uses finger direction.
  The direction is explicit, so the action never guesses which way a list should move.

- **`within`** (optional): the scrollable container for the gesture.
  Each scroll anchors on that container.
  End-of-content uses that container's subtree.
  When omitted, the action scrolls the whole screen.
  `within` reaches a target inside a nested scroll view.
  It avoids moving an outer surface that also scrolls.

- **`maxScrolls`** (optional, default 15): the scroll-step bound before the action fails.
  The bound keeps a missing target from scrolling forever.
  The action fails in a deterministic way when it reaches the bound.

### Non-inertial scrolling across backends

Each step must leave no momentum.
The three backends meet that bar with different low-level `API`s.
All sit behind `Driver.scroll` in [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py).

- **web (Playwright)** is already non-inertial.
  `scroll` wheels a desktop context.
  It uses a single-finger touch drag on a mobile context.
  See [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity.md).
  Code lives in [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py).
  A wheel delta scrolls by that delta.
  A synthesized touch drag carries no fling.
  Driving it one bounded step at a time is enough.

- **Android (adb)** scrolls with `input swipe`.
  A finite duration sets the gesture speed.
  See [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py).
  A short duration flings.
  A longer duration over the same distance is a slow drag.
  The list follows without momentum.
  The action uses a duration long enough that content stops when the gesture ends.

- **iOS (XCUITest)** scrolls with a real drag through the resident runner.
  See [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py).
  A quick flick imparts momentum.
  A drag that pauses at its end before lift releases near zero velocity.
  The scroll view settles where the drag left it.

The build may add an argument on `Driver.scroll` or a companion method.
The contract the action needs stays the same: advance a bounded distance and stop.
Leave no momentum carry.
The [driver conformance suite](../../docs/architecture.md#driver-conformance-suite-be-0114) pins that contract.
Unit 6 below covers that suite.

### End-of-content detection

A missing target should fail faster than `maxScrolls` when the region has ended.
Examples include a typo in `to` or a row a data change removed.
After each scroll the action compares the region's element subtree.
When the scenario omits `within`, it compares the whole tree.
When a scroll no longer changes the subtree, content has bottomed out.
The target is not there, so the action fails at once.
It does not repeat identical scrolls up to the bound.
adb bounds its scroll-into-view with a fixed retry count today.
That count is `_SCROLL_RETRIES` in [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py).
`maxScrolls` is the portable analog of that bound.
Tree-diff fail-fast on a bottomed-out region is new to this action.
That check does not generalize an existing adb signal.
The comparison reuses the tree the loop already queried for the stop check.
End-of-content detection adds no extra `query()`.
See [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md).

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **Scenario schema.**

   Add a `Scroll` model to [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py).
   Fields: `to: Selector`.
   Fields: `direction: Literal["up","down","left","right"] = "down"`.
   Fields: `within: Selector | None`.
   Fields: `max_scrolls: int = Field(default=15, alias="maxScrolls")`.
   Use a snake_case attribute with a camelCase alias.
   Match `save_body` and `battery_level` in the same module.
   Wire the model into the `Step` aggregator in [`bajutsu/scenario/models/steps.py`](../../bajutsu/scenario/models/steps.py).
   Require `max_scrolls > 0`.
   Place the model beside `Swipe` and `Drag`.
   Those models already declare the same direction literals.

2. **Orchestrator handler.**

   Add `_do_scroll` to [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py).
   Use a bounded scroll-and-re-query loop.
   Check that `to` resolves and that its frame sits in the viewport.
   If not, perform one non-inertial scroll step and re-query.
   Take step endpoints from the existing `_scroll_gesture` helper.
   Anchor on the `within` container's center or the screen center.
   Translate the action's content `direction` into the finger gesture `_scroll_gesture` expects.
   A `down` reveal is a finger swipe toward the top of the screen.
   That matches [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml).
   That fixture writes `swipe … direction: up` by hand.
   Return on the first tree where `to` is on-screen.
   Fail when the loop spends `maxScrolls` or detects end-of-content.
   Use no fixed `sleep`.
   The loop is a condition wait.
   It matches the structure of the `for` branch in [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py).

3. **Non-inertial driver `API` and viewport bounds.**

   Give `Driver.scroll` (or a companion method) the bounded-step, no-momentum contract.
   Realize that contract per backend.
   Playwright needs no change; it already meets the contract.
   adb uses a slow-duration `input swipe`.
   XCUITest uses a settle-before-release drag.
   The in-viewport stop condition also needs true viewport dimensions.
   Those are not uniform from the tree alone.
   On a native backend the queried tree holds on-screen elements.
   `screen_size_from_elements` in [`bajutsu/elements.py`](../../bajutsu/elements.py) approximates the viewport.
   The web backend's tree includes off-screen `DOM` nodes.
   Its content extent overshoots the viewport.
   Playwright must expose the real viewport for the check.
   Use `window.innerWidth` and `window.innerHeight`.
   Do not rely on the content extent.
   `FakeDriver` in [`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py) gains a minimal scrollable-viewport model.
   That model makes the loop testable without a device.

4. **End-of-content detection.**

   In the handler, compare the region's subtree between consecutive scrolls.
   Fail as soon as a scroll no longer changes it.
   Reuse the already-queried tree.
   Add no extra `query()`.

5. **codegen.**

   Unlike `interrupts`, `scroll` maps onto native constructs on two of three targets.
   Playwright's locator auto-scrolls into view before acting.
   UI Automator has `UiScrollable.scrollIntoView`.
   XCUITest has no single robust native match.
   Emit a labeled `TODO` there.
   Wire each target in [`bajutsu/codegen/`](../../bajutsu/codegen/).
   Follow the shared scenario walk in [BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md).

6. **Driver conformance.**

   Add a scroll-into-view case to [`tests/driver_conformance.py`](../../tests/driver_conformance.py).
   Use a screen with an off-screen target that a scroll reveals.
   Assert that `scroll` reveals it.
   Assert that a target absent from an exhausted region fails.
   This proves the non-inertial, cross-backend contract.
   It holds on FakeDriver, Playwright, XCUITest, and adb.
   See [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md).
   A shared spec closes the BE-0210 asymmetry.

7. **Docs and fixture.**

   Document `scroll` in [`docs/scenarios.md`](../../docs/scenarios.md) and its ja mirror.
   Place it next to `swipe` and `drag`.
   Guide when to use `scroll` (reveal a target).
   Guide when to use `swipe` (a fixed gesture).
   Guide when to use `drag` (move a grabbed handle).
   Update the adb exclusive scroll-into-view note in [`docs/drivers.md`](../../docs/drivers.md).
   Point that note at the portable action.
   Call out that `scroll`'s `direction` is scroll direction.
   Call out that `swipe`'s `direction` is finger direction.
   Rewrite the manual `swipe` chain in [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml).
   Use a single `scroll` step so the headline fixture shows the action end to end.

8. **Tests.**

   Cover schema parse and checks (defaults, `max_scrolls > 0`).
   Cover the handler loop over `FakeDriver`.
   Cases: target found after N scrolls.
   Cases: target never found, fail at `maxScrolls`.
   Cases: end-of-content fails at once.
   Cases: a `within` container scrolls rather than the whole screen.

### Prime directives preserved

- **AI never judges.**

  The stop condition is a selector resolving against `query()`.
  That stop condition is a machine-checkable predicate, not a model call.
  The action adds no AI surface.

- **Determinism first.**

  The action uses no fixed `sleep`.
  The action is a bounded condition wait.
  The non-inertial scroll removes device-dependent overshoot.
  That overshoot makes the current `swipe`-chain idiom flaky.
  An exhausted bound or a bottomed-out region fails.
  The run does not stall.

- **App-agnostic.**

  `scroll` is one generic action over the `Driver` interface.
  No per-app code appears.
  No per-backend divergence appears in what a scenario means.

## Alternatives considered

- **Extend `wait` with a scroll behavior (`wait: { for: X, scroll: down }`).**

  Rejected.
  A `wait` is pure observation today.
  It never actuates.
  The run loop relies on that.
  `_wait` returns the last queried tree for the caller to reuse as the step's `after` snapshot.
  It does so because nothing actuates in a wait.
  See [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py).
  See [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md).
  Folding a gesture into a wait would break that invariant.
  It would blur the line between observing and acting.
  A separate action keeps `wait` as observation alone.
  It makes the actuation explicit in the scenario.

- **Make scroll-into-view implicit on every action.**

  That would extend BE-0210 to all backends.
  Rejected as the primary shape.
  Implicit auto-scroll hides intent.
  The scenario does not say it scrolled.
  The runner must guess a direction.
  That non-determinism is why [`docs/drivers.md`](../../docs/drivers.md) keeps the adb path a robustness net.
  An explicit action states the direction and the target.
  The scenario is self-describing and deterministic.
  Widening the implicit adb net later can still layer on as a safety net.
  That path is not a substitute for an explicit action.

- **Reuse `swipe` with a `to` or `until` selector.**

  Rejected.
  `swipe` already carries two forms: a directional scroll and a coordinate drag.
  A validator keeps those forms from mixing.
  A third loop-until-visible form would overload one verb with three behaviors.
  A distinct `scroll` verb reads with more clarity at the call site.
  Each verb keeps a single contract.

- **Match `swipe` finger direction for `scroll.direction`.**

  Considered, to keep one literal token meaning the same thing across gesture verbs.
  Rejected as the default.
  An author who reaches for `scroll` thinks in scroll terms.
  That author wants "scroll down" to mean farther down the list.
  A finger-direction `up` to reveal lower content is the more surprising reading.
  `scroll.direction` names the scroll direction.
  Unit 7's docs call out the contrast with `swipe`.
  The inversion is explicit rather than a silent pitfall.
  A different field name that avoids the shared-literal clash stays open for the build.
  Use that if the docs contrast proves insufficient.

- **A per-step travel `amount` knob (as `swipe` has).**

  Deferred.
  The first slice does not include it.
  The action's default screen-relative step aims for non-inertial reliability.
  Exposing `amount` invites a large step that reintroduces overshoot.
  If a real need for tuning the step appears, add it later.
  The action's shape can stay the same.

## Progress

> Keep this current as work proceeds.
> The checklist mirrors the `MECE` work breakdown in *Detailed design*.
> The log records what changed and when (oldest first), linking the PRs.

- [ ] Unit 1 — `Scroll` scenario schema wired into `Step`
- [ ] Unit 2 — `_do_scroll` bounded scroll-and-re-query handler
- [ ] Unit 3 — non-inertial `Driver.scroll` plus `FakeDriver` viewport model
- [ ] Unit 4 — end-of-content detection reusing the queried tree
- [ ] Unit 5 — codegen (Playwright, UI Automator, XCUITest `TODO`)
- [ ] Unit 6 — driver conformance case (reveal; fail on exhausted region)
- [ ] Unit 7 — docs and the notices.yaml rewrite
- [ ] Unit 8 — tests (schema, FakeDriver loop, `within`, end-of-content)

## References

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) — `Swipe` / `Drag` beside the new `Scroll` model
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) — `_do_swipe` / `_scroll_gesture` endpoint math
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — condition-wait loop; `wait` stays observation
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `Driver.scroll` for the non-inertial contract
- [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) — hand-tuned `swipe` chain that motivates this action
- [`docs/drivers.md`](../../docs/drivers.md) — scroll-into-view marked adb exclusive
- [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) — adb `_scroll_into_view` this item generalizes
- [BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity.md) — web non-inertial scroll
- [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md) — queried-tree reuse for end-of-content
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — shared contract for cross-backend behavior

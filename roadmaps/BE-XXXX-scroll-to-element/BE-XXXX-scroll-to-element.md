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

A new scenario action, `scroll`, brings an off-screen element into view deterministically: it
scrolls a scrollable region in one direction, re-querying the element tree after each scroll, and
stops the moment the named target is on-screen — or fails loudly once a bound is reached. Every scroll
step is **non-inertial**: it advances a fixed, screen-relative distance and imparts no momentum, so
one step never flings the target past the viewport. The action works the same across the iOS
(XCUITest), Android (adb), and web (Playwright) backends, behind the one `Driver` interface, so a
scenario that scrolls to a below-the-fold control reads and runs identically on all three.

## Motivation

Reaching an off-screen element in a long vertical screen is a first-class need — a logout button at
the bottom of a settings list, the twentieth row of a feed, a submit button below a tall form — yet
Bajutsu has no direct way to express it. The portable idiom today is a hand-tuned chain of `swipe`
steps followed by a `wait`, and the showcase's own fixture records how fragile that chain is. In
[`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) the author
must anchor each `swipe` on a row that stays visible, step through the list with a one-swipe margin,
and add an extra swipe "so the target is still reached where the list's fling settles shorter — a
software-rendered CI emulator scrolls less per swipe than a hardware-accelerated device, and the
extra step covers that gap without over-scrolling the target off the top on a faster one." The
author is hand-compensating for scroll momentum that varies by device and by rendering speed.

That momentum is the root of the fragility, which makes "scroll without inertia" a determinism
requirement, not a convenience. A fling gesture imparts velocity, and the distance a fling travels
after the finger lifts depends on the platform's scroll physics and the device's frame rate — the
same gesture carries a fast device further than a slow one. A target caught in that overshoot lands
below the fold on one device and above it on another, so a fixed swipe count that works locally
fails in CI, or scrolls the target past the top before a `wait` can see it. A non-inertial scroll
removes the variable: each step advances a bounded, screen-relative distance and then stops, so the
re-query after every step reliably catches the target in the viewport the step left it in. This is
the same reasoning prime directive 2 already applies to fixed `sleep` — replace an unpredictable
duration with a checked condition — applied to scroll distance.

The gap is also a portability asymmetry today, not merely a missing convenience. The adb backend
already scrolls toward an off-screen action target internally and re-queries, bounded by a retry
count ([BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md),
`_scroll_into_view` in [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)), but
[`docs/drivers.md`](../../docs/drivers.md) marks that recovery **adb-only** and deliberately a
robustness net, not the portable idiom: XCUITest and Playwright still fail a `tap` fast when the
target starts off-screen. So the *same* scenario can pass on Android after a few swipes yet fail on
iOS and web, and the documented portable answer stays the hand-tuned `swipe` chain. An explicit,
cross-backend `scroll` action closes the asymmetry with one deterministic construct an author writes
once and reads the same on every backend.

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
# Scroll a specific scrollable region, not the whole screen, and cap the attempts.
- scroll:
    to: { label: "Log out", traits: [button] }
    direction: down
    within: { id: settings.list }   # the scrollable container to gesture inside
    maxScrolls: 25                   # bound (default 15); a target never found fails at the bound
```

The action carries a target selector and a direction, plus two optional refinements:

- **`to`** (required): the [selector](../../docs/selectors.md) the action reveals. It is the loop's
  stop condition, and the condition is stricter than mere existence: the action returns the instant
  `to` both resolves *and* has a frame inside the viewport. Existence alone is not enough, because a
  backend can keep an off-screen element in the tree: the web backend's `query()` returns a DOM node
  that is merely scrolled out of view, whereas a native lazy list drops an off-screen row from the
  tree entirely. So a `wait: { for: … }` predicate (existence alone) would report a scrolled-away web
  element as already found and never scroll. Requiring the frame to lie within the viewport makes the
  reveal real and identical across backends. Every queried element already carries its frame;
  supplying the viewport bounds the frame is compared against is a small per-backend concern, handled
  in Unit 3.
- **`direction`** (default `down`): which way to scroll through the content — the direction the
  viewport advances, which is the inverse of the finger gesture. `down` scrolls further down through
  the content to reveal what starts below the fold, and is realized as an upward finger swipe (the
  content slides up as the viewport moves down); `up`, `left`, and `right` cover the other axes.
  Because `direction` names the scroll direction and not the finger, it reads the opposite way round
  from `swipe`, whose `direction` is the finger's. The direction is explicit rather than inferred, so
  the action never guesses which way an ambiguous list should move (prime directive 2).
- **`within`** (optional): the scrollable container the gesture is performed inside. Each scroll is
  anchored on that container, and end-of-content is judged from that container's subtree. Omitted,
  the action scrolls the whole screen. `within` is what lets a scenario reach a target inside a
  nested scroll view — an inner list on a screen that also scrolls as a whole — rather than moving
  the outer surface.
- **`maxScrolls`** (optional, default 15): the maximum number of scroll steps before the action
  fails. It bounds the loop so a target that never appears fails deterministically rather than
  scrolling forever.

### Non-inertial scrolling across backends

The action's per-step scroll must impart no momentum, and the three backends reach that guarantee by
different primitives, all behind the existing `Driver.scroll`
([`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)):

- **web (Playwright)** is already non-inertial. `scroll` wheels a desktop context and performs a
  single-finger touch drag on a mobile context ([BE-0227](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity.md),
  [`bajutsu/drivers/playwright.py`](../../bajutsu/drivers/playwright.py)); a wheel delta scrolls
  exactly its magnitude and a synthesized touch drag carries no fling. No change is needed beyond
  driving it a bounded step at a time.
- **Android (adb)** scrolls with `input swipe`, whose finite duration parameter sets the gesture's
  speed ([`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)). A short duration flings; a longer
  duration over the same distance is a slow drag that the list follows without momentum. The action
  drives adb's scroll with a duration long enough that the content stops when the gesture ends.
- **iOS (XCUITest)** scrolls with a real drag through the resident runner
  ([`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py)). A quick flick imparts
  momentum; a drag that pauses briefly at its end point before lifting releases with near-zero
  velocity, so the scroll view settles where the drag left it.

Whether the non-inertial guarantee is expressed as a new argument on `Driver.scroll` (a velocity or
"settle" flag) or as a distinct `Driver` method is an implementation choice for the build; the
contract the action depends on is "advance a bounded distance and stop, with no momentum carry." The
[driver conformance suite](../../docs/architecture.md#driver-conformance-suite-be-0114) is where that
contract is pinned identically for every backend (Unit 6 below).

### End-of-content detection

A target that is genuinely absent — a typo in `to`, a row that a data change removed — should fail
faster than `maxScrolls` steps when the region has already reached its end. After each scroll the
action compares the region's element subtree (the whole tree when `within` is omitted) against the
previous one; when a scroll no longer changes it, the content has bottomed out and the target is not
there, so the action fails at once rather than repeating identical scrolls to the bound. adb bounds
its own scroll-into-view by a fixed retry count today (`_SCROLL_RETRIES` in
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py)), which `maxScrolls` is the portable analog
of; the tree-diff early-fail on a bottomed-out region is new to this action, not a generalization of
an existing adb signal. The comparison reuses the tree the loop already queried for its
stop-condition check, so end-of-content detection adds no extra `query()`
([BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)).

### Work breakdown (MECE)

1. **Scenario schema.** Add a `Scroll` model to [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py)
   (`to: Selector`, `direction: Literal["up","down","left","right"] = "down"`, `within: Selector | None`,
   `max_scrolls: int = Field(default=15, alias="maxScrolls")` — snake_case attribute with a camelCase
   alias, matching `save_body` / `battery_level` in the same module) and wire it into the `Step` aggregator in
   [`bajutsu/scenario/models/steps.py`](../../bajutsu/scenario/models/steps.py). Validate `maxScrolls > 0`.
   The model sits beside the existing `Swipe` and `Drag` models, which already declare the same
   `up`/`down`/`left`/`right` literal.
2. **Orchestrator handler.** Add `_do_scroll` to
   [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py):
   a bounded scroll-and-re-query loop that checks whether `to` resolves in the current tree *and* has
   a frame within the viewport, and if not, performs one non-inertial scroll step and re-queries. The
   step's endpoints come from the existing `_scroll_gesture` helper, anchored on the `within`
   container's center or the screen center; the handler translates the action's content-`direction`
   into the finger gesture `_scroll_gesture` expects — a `down` reveal is a finger swipe toward the
   top of the screen, the same mapping [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml)
   writes by hand as `swipe … direction: up`. It returns on the first tree where `to` is on-screen,
   and fails when `maxScrolls` is exhausted or end-of-content is detected. No fixed `sleep` — the loop
   is a condition wait, structurally the same as
   [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py)'s `for` branch.
3. **Non-inertial driver primitive and viewport bounds.** Give `Driver.scroll` (or a companion
   method) the "bounded step, no momentum" guarantee described above, implemented per backend: no-op
   for Playwright (already satisfied), a slow-duration `input swipe` for adb, and a
   settle-before-release drag for XCUITest. The in-viewport stop condition also needs the true
   viewport dimensions, which are not uniformly derivable from the tree: on a native backend the
   queried tree holds only on-screen elements, so `screen_size_from_elements`
   ([`bajutsu/elements.py`](../../bajutsu/elements.py)) already approximates the viewport, but the web
   backend's tree includes off-screen DOM nodes, so its content extent overshoots the viewport — the
   Playwright backend must expose its real viewport (`window.innerWidth` / `innerHeight`) for the
   check rather than rely on that content extent. `FakeDriver`
   ([`bajutsu/drivers/fake.py`](../../bajutsu/drivers/fake.py)) gains a minimal scrollable-viewport
   model so the loop is testable without a device.
4. **End-of-content detection.** In the handler, compare the region's subtree between consecutive
   scrolls and fail early when a scroll no longer changes it, reusing the already-queried tree
   (no extra `query()`).
5. **codegen.** Unlike `interrupts`, `scroll` maps onto native constructs on two of three targets:
   Playwright's locator auto-scrolls into view before acting, and UI Automator has
   `UiScrollable.scrollIntoView`. XCUITest has no single robust equivalent, so emit a labeled
   `// TODO` there. Wire each target in [`bajutsu/codegen/`](../../bajutsu/codegen/) accordingly,
   following the shared scenario walk ([BE-0083](../BE-0083-codegen-emitter-unification/BE-0083-codegen-emitter-unification.md)).
6. **Driver conformance.** Add a scroll-into-view case to
   [`tests/driver_conformance.py`](../../tests/driver_conformance.py): a screen with an off-screen
   target that only a scroll reveals, asserting that `scroll` reveals it and that a target absent from
   an exhausted region fails. This is what proves the non-inertial, cross-backend contract holds
   identically on FakeDriver, Playwright, XCUITest, and adb ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)),
   closing the BE-0210 asymmetry with a shared spec rather than a per-backend one.
7. **Docs and fixture.** Document `scroll` in [`docs/scenarios.md`](../../docs/scenarios.md) and its
   Japanese mirror, next to `swipe` and `drag`, with guidance on when to reach for `scroll` (reveal a
   target) versus `swipe` (a fixed gesture) versus `drag` (move a grabbed handle). Update the
   adb-only scroll-into-view note in [`docs/drivers.md`](../../docs/drivers.md) (and its mirror) to
   point at the portable action. Call out explicitly that `scroll`'s `direction` is the scroll
   direction while `swipe`'s is the finger direction, so an author who knows one verb is not tripped
   by the other. Rewrite the manual `swipe` chain in
   [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) as a single
   `scroll` step, so the headline fixture demonstrates the action end-to-end.
8. **Tests.** Schema parse/validate (defaults, `maxScrolls > 0`); the handler loop over `FakeDriver`
   (target found after N scrolls, target never found failing at `maxScrolls`, end-of-content failing
   early, a `within` container scrolled rather than the whole screen).

### Prime directives preserved

- **AI never judges.** The stop condition is a selector resolving against `query()` — a
  machine-checkable predicate, never a model call. The action adds no AI surface.
- **Determinism first.** No fixed `sleep`: the action is a bounded condition wait, and the
  non-inertial scroll removes the device-dependent overshoot that makes the current `swipe`-chain
  idiom flaky. An exhausted bound or a bottomed-out region fails loudly rather than hanging.
- **App-agnostic.** `scroll` is one generic action over the `Driver` interface; no per-app code, and
  no per-backend divergence in what a scenario means.

## Alternatives considered

- **Extend `wait` with a scroll behavior (`wait: { for: X, scroll: down }`).** Rejected. A `wait`
  is pure observation today: it never actuates, and the run loop relies on that — `_wait` returns the
  last queried tree for the caller to reuse as the step's `after` snapshot precisely "since nothing
  actuates in a wait" ([`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py),
  [BE-0259](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md)). Folding
  a gesture into a wait would break that invariant and blur a clean line between observing and acting.
  A separate action keeps `wait` observation-only and makes the actuation explicit in the scenario.
- **Make scroll-into-view implicit on every action, extending BE-0210 to all backends.** Rejected as
  the primary shape. Implicit auto-scroll hides intent (the scenario does not say it scrolled), must
  guess a direction, and its non-determinism is exactly what
  [`docs/drivers.md`](../../docs/drivers.md) keeps it a robustness net rather than the portable idiom
  for. An explicit action states the direction and the target, so the scenario is self-describing and
  deterministic. Widening the implicit adb net to other backends could still be layered on later as a
  separate safety net; it is not a substitute for an explicit action.
- **Reuse `swipe` with a `to`/`until` selector instead of a new verb.** Rejected. `swipe` already
  carries two forms (a directional scroll and a coordinate drag) with a validator that keeps them
  from mixing; adding a third, loop-until-visible form would overload one verb with three distinct
  behaviors. A distinct `scroll` verb reads more clearly at the call site and keeps each verb's
  contract single.
- **Match `swipe`'s finger-direction convention for `scroll.direction`.** Considered, to keep one
  literal token meaning the same thing across gesture verbs. Rejected as the default: an author reaching
  for `scroll` thinks "scroll down to find the element further down the list," so a finger-direction
  `up` to reveal lower content is the more surprising reading. `scroll.direction` therefore names the
  scroll direction (the intuitive one), and Unit 7's docs call out the contrast with `swipe` so the
  inversion is explicit rather than a silent trap. The alternative — a different field name that
  sidesteps the shared-literal-opposite-meaning clash entirely — stays open for the build if the docs
  contrast proves insufficient.
- **A per-step travel `amount` knob (as `swipe` has).** Deferred, not included in the first slice.
  The action's default screen-relative step is deliberately chosen to be non-inertial and reliable;
  exposing `amount` invites a caller to dial a large step that reintroduces overshoot. If a real need
  for tuning the step appears, it can be added later without changing the action's shape.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `Scroll` scenario schema (`to` / `direction` / `within` / `maxScrolls`) wired into `Step`.
- [ ] Unit 2 — `_do_scroll` bounded scroll-and-re-query handler (condition wait, no fixed sleep).
- [ ] Unit 3 — non-inertial `Driver.scroll` guarantee per backend + `FakeDriver` viewport model.
- [ ] Unit 4 — end-of-content detection reusing the already-queried tree.
- [ ] Unit 5 — codegen (Playwright auto-scroll, UI Automator `scrollIntoView`, XCUITest TODO).
- [ ] Unit 6 — driver conformance case (reveal a target; fail on an exhausted region).
- [ ] Unit 7 — docs (scenarios.md + ja, drivers.md note update) and the notices.yaml rewrite.
- [ ] Unit 8 — tests (schema, handler loop over `FakeDriver`, `within`, end-of-content).

## References

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) — the `Swipe` /
  `Drag` models beside which the new `Scroll` model lives, and the source of the directional
  vocabulary it reuses.
- [`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py) —
  `_do_swipe` / `_scroll_gesture`, the endpoint math the `scroll` handler reuses.
- [`bajutsu/orchestrator/waits.py`](../../bajutsu/orchestrator/waits.py) — the condition-wait loop
  (`for` branch) the `scroll` handler mirrors structurally, and whose observation-only contract this
  item keeps intact by not folding scrolling into `wait`.
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — the `Driver.scroll` primitive that
  gains the non-inertial, bounded-step guarantee.
- [`demos/showcase/scenarios/notices.yaml`](../../demos/showcase/scenarios/notices.yaml) — the
  hand-tuned `swipe` chain, and its comment documenting fling-overshoot fragility, that motivates
  this action and becomes its headline fixture.
- [`docs/drivers.md`](../../docs/drivers.md) — the note marking scroll-into-view an adb-only
  robustness net, which this action makes portable.
- [BE-0210 — Android on-device actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) —
  the adb-only `_scroll_into_view` recovery this item generalizes into an explicit, cross-backend action.
- [BE-0227 — Web swipe / scroll fidelity](../BE-0227-web-swipe-scroll-fidelity/BE-0227-web-swipe-scroll-fidelity.md) —
  the web backend's already-non-inertial scroll primitive (wheel / touch drag) this action builds on.
- [BE-0259 — Reuse the assertion query snapshot](../BE-0259-assert-query-snapshot-reuse/BE-0259-assert-query-snapshot-reuse.md) —
  the already-queried-tree reuse that lets end-of-content detection add no extra `query()`.
- [BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) —
  the shared contract where this action's non-inertial, cross-backend behavior is pinned identically
  for every backend.

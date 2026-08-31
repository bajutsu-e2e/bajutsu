**English** · [日本語](BE-0400-scroll-step-amount-ja.md)

# BE-0400 — Make a scroll step travel the distance it asks for and let authors choose it

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0400](BE-0400-scroll-step-amount.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0400") |
| Implementing PR | [#1824](https://github.com/bajutsu-e2e/bajutsu/pull/1824) |
| Topic | Scenario authoring features |
| Related | [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md), [BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md) |
<!-- /BE-METADATA -->

## Introduction

A `scroll` step does not travel the distance it asks for. On the device we measured, a step
requesting 0.05 of the viewport moved the content 6.15 times that far, and no step moved less than
about 269 points however little it requested. This item makes the realized travel match the
request, and then exposes that distance to scenario authors as a new optional `amount` field on
the `scroll` action, matching the field `swipe` and `drag` already carry.

The two halves are one item because neither works alone. Correcting the travel without exposing it
leaves authors with a single fixed step they still cannot tune. Exposing `amount` without
correcting the travel hands authors a field whose smaller values the gesture cannot deliver.

## Motivation

### What a scroll step does today

[BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) added
`scroll: { to: <selector> }`: a bounded loop that scrolls one step, re-queries the element tree,
and stops once the target [selector](../../docs/glossary.md#scenario-authoring) resolves with its
frame's center inside the viewport. Each step asks to advance a fixed fraction of the viewport —
`_STEP_FRACTION = 0.6` in
[`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py).
That constant is the only step size the action has; no field on the `scroll` scenario step reaches
it.

BE-0326 states the contract that fraction is meant to honor: each step "advances a fixed
screen-relative distance and leaves no momentum", so that "one step never flings the target past
the viewport."

### The measurement

We measured realized travel against requested travel on an iPhone 17 Pro running iOS 26.5, on the
driver conformance scrollable screen, viewport 402 by 874 points. Each row is ten repeats with
the list reset to the top before each, and the same five fractions were run again as `swipe` for
the comparison below, for a hundred measurements in all. Travel was read from the element tree: the driver call
returns only after the gesture has settled, so the read after it describes a screen at rest, and
rows carry unique identifiers, so a row present in both reads gives its own displacement directly.

| Requested fraction | Requested (pt) | Realized mean (pt) | sd (pt) | Ratio | Excess (pt) |
|---|---|---|---|---|---|
| 0.05 | 43.7 | 268.9 | 0.3 | 6.15× | +225.2 |
| 0.125 | 109.2 | 279.4 | 5.1 | 2.56× | +170.1 |
| 0.30 | 262.2 | 416.7 | 11.3 | 1.59× | +154.5 |
| 0.60 (the default) | 524.4 | 657.4 | 0.9 | 1.25× | +133.0 |
| 0.80 | 699.2 | 825.4 | 0.2 | 1.18× | +126.2 |

The figures are exact rather than estimated. Within every one of the hundred measurements, the
displacements of the individual rows agreed to 0.0 points, so the list translates rigidly and a
single row's displacement is the region's travel. Row snapping is ruled out: the row pitch is
exactly 98.0 points, and none of the realized distances is a multiple of it.

### What the numbers say

**The error is additive, and it leaves a floor.** The excess shrinks from +225 to +126 points as
the request grows from 44 to 699 points, but it never approaches zero. The clearest statement of
the defect is the floor it implies: **no step travels less than roughly 269 points, however little
it asks for.** On the measured viewport that floor is about 0.31 of a screen.

**The error is a systematic bias, not run-to-run scatter.** The standard deviation is between 0.2
and 11.3 points, at most 2.7 percent of the realized travel, against a bias an order of magnitude
larger. That distinction decides what kind of problem this is: a reproducible bias is correctable,
where scatter would only be bounded. It also narrows what a reader should expect this item to
deliver — it removes a consistent overshoot, and does not claim to make an inherently noisy gesture
quiet.

**Two consequences follow, and both matter more than the raw error.**

The first is that BE-0326's contract holds only at the default. A 0.6 step realizes 657 points
against an 874-point viewport, so it stays inside one screen and the target cannot be flung past.
A 0.125 step realizes 279 points against a request of 109, and the shortfall in overlap is exactly
what the contract exists to prevent.

The second concerns the recovery in
[BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md).
When a step is found to have skipped the target, the loop halves the fraction toward a floor of
0.125, on the reasoning that a smaller fraction observes the content more finely. The halving does
still shrink realized travel, from 657 points to about 279. What it cannot do is reach the
distance its floor names: 0.125 and 0.05 both realize about 270 to 280 points, so **the smallest
step the loop can actually take is around 0.32 of the viewport, not the 0.125 the code
documents.** The recovery's final clamp from 0.15 to the 0.125 floor therefore buys almost
nothing. The halvings above it still shrink real distance.

### Why `amount` alone would not fix this

An author who knows a screen needs a finer step has no way to say so today, and that gap is what
first motivated this item. But a field that only requests a distance inherits the defect above.
The values an author would reach for are the small ones, and the small ones are exactly where the
gesture is least faithful. At 0.125 and at 0.05 — a request differing by a factor of two and a
half — the content moves the same 270 to 280 points, so across that range the field would be
inert. Shipping `amount` on today's gesture would hand authors a knob that reads as precise and
does nothing at the settings they most want.

### The outcome to check

Once this ships, a step requesting 0.125 of the viewport should travel close to the 109 points it
asks for on the measured device, rather than the 279 it travels today. Cutting a request from
0.125 to 0.05 should shrink the distance travelled in proportion, where today it changes nothing.
The driver conformance suite should fail a backend whose realized travel departs from the request
by more than a stated tolerance, so the property stays checked rather than measured once.

### What we have not established

The measurement covers one host, one device, one operating-system version, and one application —
a SwiftUI lazy list. We measured the effect and not its cause. The 0.3-second settle hold the
iOS runner applies before lifting (`scrollSettleDuration` in
[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift))
is not responsible: `scroll` and `swipe` agree within noise at every fraction, and a separate
measurement found content already at rest more than 190 milliseconds before the driver call
returns, for both gestures alike. What that hold does is worth stating precisely, so unit 1
measures the right thing: it is not waiting out deceleration, which cannot begin until the lift,
because the scroll view tracks the finger while the touch is down. It holds the finger still so
that the release velocity UIKit estimates from the touch's last moments comes out near zero.
Whether 0.3 seconds is always long enough for that estimate to fall to zero is the open question,
and it is not the one the numbers above answer. A plausible remaining explanation for the
overshoot is that
`withVelocity: .default` traverses every drag at one fixed speed, so the finger lifts at a similar
velocity however short the drag, leaving a similar fling each time — but nothing has tested that,
and unit 1 below exists to settle it before any correction is designed.

## Detailed design

### The `amount` field

```yaml
- scroll:
    to: { id: log.row.42 }
    amount: 0.2   # each step covers 0.2 of the viewport, instead of the default 0.6
```

`amount` is optional and sets the fraction of the viewport one step travels, replacing
`_STEP_FRACTION` as the loop's starting step. The range is `0 < amount ≤ 1` — the same range and
the same unit `swipe` and `drag` already validate for their own `amount` field. Omitted, the loop
keeps its current default of 0.6.

`amount` sets only where the loop starts. BE-0329's recovery is unchanged: on a detected
overshoot, the loop still halves the current step toward the 0.125 floor, wherever it started. An
`amount` at or below that floor leaves the recovery no room to shrink further, so the first
overshoot the loop detects fails the call outright, naming the overshoot. An author who sets
`amount` that low is choosing a step small enough that the loop should not need to recover.

The range deliberately does not stop at the 0.6 default.
[BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md)'s own *Alternatives
considered* deferred this knob over the risk that a large step reintroduces overshoot. BE-0329
closed that gap for every step size rather than only the default: the loop detects a step that
shares nothing with the read before it and reacts, whatever fraction produced that step.

That detection also sets a practical ceiling the range itself does not, and unit 8 must document
it. `_overshot` fires when nothing that was in view before a step is on screen after it, so a step
travelling a full viewport satisfies it by construction — on a *faithful* backend as much as on a
flinging one. The ceiling therefore tightens once unit 2 lands. Today's 0.8 request overshoots to
825 points, leaving 49 points of the 874-point viewport showing content from before the step —
against a row pitch of 98 points, so whether an element that was *wholly* in view survives that
step depends on where a row boundary happens to fall. A corrected 1.0 would land exactly one
viewport on and trip the detector on its first step, costing two gestures against `maxScrolls` —
the overshooting step itself and the halved reversing look-back after it, since the halving is an
assignment and spends nothing — for half a viewport of net progress, where two default steps give
1.2. An author wanting one large step should stay far enough below 1.0 that consecutive reads
still share an element, and should learn that from the documentation rather than from spent
budget.

### Making the realized travel match the request

The correction belongs behind `Driver.scroll`, so the handler keeps asking for a fraction and each
backend delivers it. Which correction is right depends on unit 1's finding, and the candidates
differ in how much they respect determinism:

- **Tune the gesture's parameters.** If the fixed traversal velocity is the cause, varying it with
  the drag length — or holding differently before lift — may remove the excess at its source. This
  is the preferred shape: it corrects the gesture rather than compensating for it.
- **Apply a per-backend calibration.** A measured relationship between request and realized travel
  could be inverted, so the driver asks for whatever produces the wanted distance. This risks
  encoding one device's numbers into the tool, which unit 5's conformance check would then police
  across backends.
- **Close the loop.** The handler could measure a step's realized travel from the trees it already
  reads and adjust the next request. This stays deterministic — it reads frames, not a model — but
  it changes step sizes mid-scroll, which interacts with BE-0329's halving and would need that
  interaction spelled out.

Whichever lands, no fixed `sleep` may enter the path, and no per-application branch may appear.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **Establish why a step overshoots.**

   Vary the runner's gesture parameters independently — traversal velocity, the hold before lift,
   the drag's length — and measure realized travel for each. The goal is to name the mechanism, not
   to guess it: the correction in unit 2 follows from what this finds. Confirm or refute the
   fixed-traversal-velocity hypothesis stated in *Motivation*. Record the numbers in this item's
   *Progress* log so a later reader can see what the correction rests on.

2. **Correct the travel behind `Driver.scroll`.**

   Implement the correction unit 1 points to, in the iOS runner and the driver, keeping the
   handler's request in viewport fractions. Measure the other backends the same way and correct
   them if they miss too — the conformance check in unit 5 holds all of them to one contract, so
   this unit must not fix iOS alone and leave another backend failing.

3. **The `amount` scenario schema.**

   Add `amount: float | None = None` to `Scroll` in
   [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py). `Scroll` has
   no validator today; `max_scrolls` is constrained inline with `Field(gt=0)`. Constrain `amount`
   the same way, with `Field(gt=0.0, le=1.0)`, or add a validator mirroring the `0 < amount ≤ 1`
   check `Swipe` and `Drag` already run.

   codegen emits nothing new for the field. Both scroll emitters delegate to a native scroll into
   view — `scrollIntoViewIfNeeded` in [`bajutsu/codegen/playwright.py`](../../bajutsu/codegen/playwright.py)
   and `UiScrollable.scrollIntoView` in [`bajutsu/codegen/uiautomator.py`](../../bajutsu/codegen/uiautomator.py)
   — and each does its own stepping, so neither leaves `amount` a faithful mapping, exactly as
   neither maps `within` today. Add `amount` to the Playwright emitter's comment enumerating the
   fields its native scroll subsumes, so the record of that decision does not go stale.

4. **Handler wiring.**

   Thread `s.amount` from `_do_scroll` through to `scroll_to_target` in
   [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py):
   the loop's `fraction = _STEP_FRACTION` initializer becomes
   `fraction = amount if amount is not None else _STEP_FRACTION`. The recovery path that shrinks
   `fraction` toward `_MIN_STEP_FRACTION` reads the same variable, unchanged. One further line does
   need changing: the overshoot message at the floor names `_MIN_STEP_FRACTION` directly, and an
   `amount` below that floor never shrinks before hitting it, so the message must name the
   `fraction` the failing step actually took.

5. **A conformance check on realized travel.**

   Add a case to [`tests/driver_conformance.py`](../../tests/driver_conformance.py) that requests a
   step, measures the region's displacement from the trees before and after, and asserts it falls
   within a stated tolerance of the request. State the tolerance and the reasoning for it in the
   test, as BE-0329's overlap check does for its own property. This is what keeps the correction
   from decaying, and what holds every backend to the same contract
   ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)).

6. **Guard the settle property that already holds.**

   An on-device test for the neighbouring property — that content is at rest by the time the
   driver call returns — was written while measuring for this item and verified negatively (with
   the driver's request made asynchronous, it fails on the intended assertion rather than on a
   vacuity guard). Land it, and decide whether the `conformance (xcuitest)` job should invoke it:
   that job names its test file explicitly, and it is a required check, so wiring in a new file is
   a deliberate choice rather than an oversight.

7. **Tests.**

   Over `FakeDriver`: a call with a small `amount` reaches a target the default step would
   overshoot, with the recovery never triggered; a call omitting `amount` behaves exactly as it
   does today; a call whose `amount` sits at or below the floor fails on its first detected
   overshoot, naming the step it took. Schema tests cover the new field's range, mirroring the
   existing `swipe` and `drag` amount tests.

8. **Docs.**

   Document `amount` in [`docs/scenarios.md`](../../docs/scenarios.md) and its Japanese mirror,
   beside `direction`, `within`, and `maxScrolls`. State its default and its unit, and
   cross-reference `swipe` and `drag` for the shared convention. Add it to the `Scroll ::=`
   production in [`docs/dsl-grammar.md`](../../docs/dsl-grammar.md) and its Japanese mirror too:
   that production enumerates the action's fields, so a reader taking it as the field list would
   otherwise conclude `scroll` has no `amount`. Document the practical ceiling from *The `amount`
   field* alongside the range. Record the realized-travel requirement in the driver conformance
   section of [`docs/architecture.md`](../../docs/architecture.md) and its Japanese mirror, since
   it becomes part of the `Driver` contract.

### Prime directives preserved

- **AI never judges.**

  Every part of this is arithmetic over what the backend reported: a displacement read from two
  element trees, a fraction supplied by the scenario author, a tolerance compared numerically. No
  model call enters the path.

- **Determinism first.**

  The item removes a source of non-determinism rather than adding one. A step that overshoots by a
  device-dependent amount is precisely the unpredictability prime directive 2 exists to exclude,
  and correcting it makes the gesture's effect follow from the request. No fixed `sleep` is
  introduced, and the loop keeps its bounded condition-wait shape. Should the closed-loop candidate
  be chosen, its adjustment reads frames the loop already has.

- **App-agnostic.**

  The correction lives behind `Driver.scroll` and `amount` reaches every backend through the one
  `scroll_to_target` loop. No per-application branch appears, and the conformance check holds every
  backend to the same realized-travel contract.

## Alternatives considered

- **Ship `amount` and leave the travel uncorrected.**

  Rejected, and this is the shape the item originally took. The measurement above is what ruled it
  out: a request of 0.05 and a request of 0.125 move the content the same distance, so the field's
  small values — the ones an author reaching for it would want — would do nothing. A knob that
  reads as precise and is inert where it matters is worse than no knob, because a scenario can be
  written against it and appear correct.

- **Correct the travel and do not expose `amount`.**

  Rejected. Correcting the gesture would make BE-0329's halving reach the distances it names, which
  is worth having on its own. But it leaves the original gap untouched: an author who knows a
  screen needs a finer step still cannot request one, and must wait for the loop to overshoot and
  recover, spending `maxScrolls` budget to arrive where they could have started.

- **Let `amount` also set the recovery floor.**

  Rejected. The floor exists so a step too small to show any motion fails loudly instead of
  shrinking toward zero. Tying it to `amount` would let an author who requests a very small step
  lower the floor beneath it, defeating what the floor is for. The floor stays fixed regardless of
  `amount`.

- **Express `amount` as an absolute distance in points or pixels.**

  Rejected for the reason `swipe` and `drag` already reject it: an absolute distance means
  something different on a phone, a tablet, and a desktop browser window, so a scenario tuned on
  one device would overshoot or undershoot on another. A viewport fraction reads the same on every
  screen size, keeping the action portable across targets.

- **Cap `amount` below 1 to avoid the overshoot risk BE-0326 deferred over.**

  Rejected, though not for the reason it first appears. BE-0329's detection and recovery does
  bound the risk for every step, not only the default one. What it cannot do is deliver a step near
  a full viewport: such a step trips `_overshot` by construction, so the recovery answers the
  request by halving and reversing rather than granting it. The range keeps its 1.0 top anyway,
  because a fixed cap is the wrong instrument — where the ceiling sits depends on the screen's
  content, which the schema cannot know. *The `amount` field* records the ceiling, and unit 8
  carries it into the documentation, so the constraint reaches an author as guidance rather than as
  a number guessed on their behalf.

- **Lower `_STEP_FRACTION` instead of adding a field.**

  Rejected, and the measurement limits how far it could go anyway. A smaller global default slows
  every `scroll` call that does not need one; 0.6 is the value
  [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) chose for the ordinary case
  and the one
  [BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md)
  kept, having rejected a smaller step fraction as its own primary shape; and the realized travel
  stops responding to the constant once it is lowered past about 0.125.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — establish the mechanism behind the overshoot
- [x] Unit 2 — correct realized travel behind `Driver.scroll`, on every backend that misses
- [x] Unit 3 — `amount` field on the `Scroll` scenario schema
- [x] Unit 4 — handler wiring, including the overshoot message naming the step actually taken
- [x] Unit 5 — conformance check on realized travel against the request
- [x] Unit 6 — land the settle test and decide its CI wiring
- [x] Unit 7 — tests over `FakeDriver` and the schema
- [x] Unit 8 — docs (`docs/scenarios.md`, `docs/dsl-grammar.md`, `docs/architecture.md`, both mirrors)

### Log

- Measured realized travel against requested travel on an iPhone 17 Pro (iOS 26.5), conformance
  scrollable screen, viewport 402×874 pt, ten repeats per fraction. Realized travel overshoots at
  every fraction, additively rather than proportionally, leaving a floor near 269 pt; run-to-run
  scatter stays under 2.7 percent, so the error is a systematic bias. `scroll` and `swipe` agree
  within noise, so the 0.3-second settle hold is not the cause. The figures are in *Motivation*.
- A separate measurement ruled out a suspected residual drift after the gesture: sampling the
  rendered screen from before the gesture to 2.5 seconds after the driver returned found content at
  rest more than 190 ms *before* the return, in twelve of twelve gesture measurements, with a
  zero-pixel negative control. What keeps changing after the return is the scroll indicator at the
  screen's right edge, which fades about 900 ms later. The on-device test in unit 6 came out of
  that work.
- Established the mechanism (unit 1), varying the gesture's three parameters independently on the
  same device and screen. The traversal velocity is the whole of the cause. Neither of the other two
  parameters is: the hold before lift changed nothing at 0.0, 0.3, or 1.0 seconds, and the initial
  press changed nothing at 0.1 or 0.5 seconds. Dropping the press to 0.0 did change something, and
  in the wrong direction: it widened the fixed shortfall below from 10.0 points to between 11.4 and
  13.8, which is why the corrected gesture keeps the 0.1 it has always used. Realized travel rises
  monotonically with the
  velocity — `XCUIGestureVelocity` 500 reproduces today's `.default`, 1000 reaches 5.4 times a 0.125
  request, 2000 reaches 10.4 times — and vanishes below it: at 200 and at 100, every step from 17 to
  525 points came back exactly 10.0 points short of its request, with a standard deviation of 0.0.
  *Motivation*'s fixed-traversal-velocity hypothesis is confirmed, and the 0.3-second hold is not
  merely innocent of the overshoot but inert, which is why `scroll` and `swipe` agreed.
- Found that the speed which fails first is a short drag's, not a long one's (unit 1). At 400 a 0.6
  step was exact while a 0.05 step flung five times its request; at 300 a 0.05 step was exact while
  a 17-point drag flung nine times and a 26-point drag flung on some repeats and not others. Small
  steps are what `amount` and the halving recovery exist to ask for, so the corrected gesture is set
  at 200 rather than just under the largest speed a full-size step survives.
- Corrected the gesture (unit 2): the iOS runner now traverses a scroll drag at 200 points per
  second and spends no hold. A 0.125 step travels 99.3 points against a request of 109.2, where it
  travelled 279 before, and a 0.05 step travels 33.7 against 43.7, where it travelled 269. The
  ~269-point floor is gone, and halving a request now halves the distance travelled. The residual
  10.0-point shortfall is the pan recognizer's slop, left uncorrected so the driver conformance
  suite's tolerance names it instead of the gesture carrying one device's constant.
- Ran the new realized-travel conformance case against the backends this host can drive (unit 2). It
  passes against `FakeDriver` and against Playwright with no change to either. This host has no
  emulator, so adb was left to the `conformance (adb)` job, which inherits the case like every other
  contract case.
- That job then caught adb too, which is what unit 2 exists to prevent being missed: a 0.6 step asked
  for 1440 device pixels and travelled 1153, twenty percent short — an undershoot, where iOS
  overshot. The cause is the mirror image of the iOS one. adb panned over a fixed 600 ms whatever the
  distance, so the speed rose with the request until the view stopped tracking the whole path, while
  iOS held the speed fixed and let a fast traversal fling. adb now holds its *speed* fixed instead,
  at 550 device pixels per second with a 600 ms floor — the iOS runner's 200 points per second
  carried across at the two screens' physical scale — so every step size stays honest on both.
- The corrected adb pan then failed the case a second way, and the cause was in the check rather
  than the gesture: a 0.125 step asked for 300 device pixels and travelled 213. The allowance's fixed
  part was an absolute distance in the driver's own units, which differ per backend — points on iOS,
  raw pixels on Android. The pan slop it covers is 10.0 points against a 874-point viewport on iOS
  and about 87 pixels against a 2400-pixel one on Android, so no single absolute number fits both.
  Stating it as a fraction of the viewport fits both for the reason `amount` is a fraction, and still
  rejects the defect by a wide margin, since the uncorrected iOS gesture missed a 44-point request by
  225 points. Every step size is now measured before any is judged, so a failing run reports all
  three rather than stopping at the first — a run on a device the reader does not have would
  otherwise take one cycle per step size to show the error's shape.
- Settled unit 6's wiring question by placing the settle test in
  `tests/test_driver_conformance_ondevice.py` rather than a file of its own: the
  `conformance (xcuitest)` job then runs it with no wiring change to a required check and no second
  cold lease on a metered runner. It sits outside `DriverConformanceContract` because Android
  reaches the same guarantee through a different mechanism (BE-0332's marked read), so stating it as
  a shared contract would assert one backend's timing of another's.

## References

- [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py) —
  `scroll_to_target`'s step-fraction loop, `_STEP_FRACTION`, and the overshoot recovery
- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) — `Swipe`'s and
  `Drag`'s existing `amount` field, and the `Scroll` model this item extends
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift) —
  the iOS scroll gesture and the fixed traversal velocity that keeps it non-inertial (this item
  replaced `withVelocity: .default` and the settle hold with it)
- [`tests/driver_conformance.py`](../../tests/driver_conformance.py) — the shared scroll cases the
  realized-travel check joins
- [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) — the `scroll` action, its
  fixed-distance contract, and the *Alternatives considered* that deferred an `amount` knob
- [BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md) —
  the overshoot detection and the halving recovery whose floor this item makes reachable
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the suite
  that turns a driver contract into a checked property
- [`docs/scenarios.md`](../../docs/scenarios.md) — the `scroll` action's authoring documentation

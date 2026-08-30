**English** · [日本語](BE-XXXX-scroll-step-amount-ja.md)

# BE-XXXX — Add an author-tunable step amount to the scroll action

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-scroll-step-amount.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Scenario authoring features |
| Related | [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md), [BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md) |
<!-- /BE-METADATA -->

## Introduction

The `scroll` action gains an optional `amount` field: the fraction of the viewport each scroll
step travels. `swipe` and `drag` already expose the same field for their own gestures. An author
who knows in advance that a screen scrolls fast, or holds a target easy to skip past, can now
request a smaller step directly, rather than depend only on the scroll loop's own recovery for a
step that has already skipped a target. Omitting `amount` keeps today's behavior: the loop still
starts at its own default step and still shrinks that step once it detects a skip.

## Motivation

[BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) added
`scroll: { to: <selector> }`: a bounded, non-inertial loop that advances a fixed step of 0.6 of
the [viewport](../../docs/scenarios.md), re-queries the element tree, and stops once the target
[selector](../../docs/glossary.md#scenario-authoring) resolves with its frame's center inside the
viewport. The step size is a module constant, `_STEP_FRACTION` in
[`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py).
No field on the `scroll` scenario step reaches it, so every call takes the same step, whatever the
screen it scrolls.

[BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md)
found that a large step can carry the target past the viewport between two queries, so a scenario
failed to reach a target the loop reported missing. Its fix is reactive: the loop compares
consecutive reads, and once it decides a step shared nothing with the read before it, the loop
halves the step, down to a floor of 0.125 (`swipe`'s own default distance), and takes one step
backward to look at the span it skipped. That recovery needs a skip to happen first. Each recovery
step also spends one of the call's `maxScrolls` budget, so a screen that needs several halvings
before its step is small enough leaves less budget for the steps that follow. A step already at
the floor that still shares nothing with the read before it fails the whole call, naming the
overshoot rather than reaching the target.

Whether every backend's step is fully non-inertial in practice is itself not settled. On iOS,
the resident runner holds the touch stationary for a fixed 0.3 seconds before lifting
(`scrollSettleDuration` in
[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)),
rather than waiting on a condition that confirms the scroll view has actually stopped. Whether
that fixed hold always outlasts `UIScrollView`'s own deceleration is a question about on-device
physics that reading the source cannot answer, and no measurement has confirmed it either way. The
driver conformance suite's overlap check
([BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md))
would not catch a small amount of drift on its own: it asks only whether a step left some element
shared between two reads, not whether the region stopped exactly where the step left it. A smaller
`amount` narrows what a drift like that could carry past, whatever its cause, the same way it
narrows what a detected overshoot recovers from.

An author who already knows a screen needs a smaller step, because its rows are dense or because
it holds controls that must stay hittable at rest, has no way to say so today. The only route to a
smaller step is the reactive halving above, and that route opens only after the loop has already
overshot at 0.6. This item adds a direct route instead: `amount`, an optional field on the
`scroll` scenario step, sets the step the loop starts at, in place of `_STEP_FRACTION`. The
reactive recovery keeps working unchanged; it may still halve further from wherever `amount`
starts.

Once this ships, a test can demonstrate the field directly: a `FakeDriver` scroll built so the
default 0.6 step shares nothing between two reads still reaches its target once `amount` is set
small enough that no read skips it, and BE-0329's reversing recovery never triggers. No shipped
backend is known to fling under ordinary use — BE-0329 records that finding — so `amount` is not
documented here as the fix for a scenario failing today. The concrete, present-day case for it is
the unsettled iOS question above: a screen an author suspects of residual drift can be given a
step small enough that the drift, whatever its size, has less viewport left to carry the target
through.

## Detailed design

### The `amount` field

```yaml
- scroll:
    to: { id: log.row.42 }
    amount: 0.2   # each step covers 0.2 of the viewport, instead of the default 0.6
```

`amount` is optional. `0 < amount ≤ 1`, the same range `swipe` and `drag` already validate for
their own `amount` field, and the same unit: a fraction of the viewport along the scroll axis.
Omitted, the loop keeps its current default of 0.6.

The range does not stop at 0.6, so `amount` can also request a larger step than the loop takes
today. [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md)'s own *Alternatives
considered* deferred exactly this knob over that risk, worried that a large step reintroduces
overshoot.
[BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md)
closes that gap for any step size, not only the default one: the loop already detects a step that
shares nothing with the read before it and reacts, whatever fraction produced that step. An
`amount` near 1 is caught and shrunk by the same recovery a large step from `_STEP_FRACTION` would
trigger, rather than a silent skip.

`amount` sets only the loop's starting step.
[BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md)'s
recovery is unchanged: on a detected overshoot, the loop still halves the current step toward a
floor of 0.125, regardless of where it started. An `amount` at or below that floor leaves the
recovery no room to shrink further, so the first overshoot the loop detects fails the call
outright, naming the overshoot, rather than reaching the target through a smaller step. An author
who sets `amount` that low is choosing a step small enough that the loop should never need to
recover in the first place; *Alternatives considered* records why the floor itself stays fixed
rather than tracking `amount`.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **Scenario schema.**

   Add `amount: float | None = None` to `Scroll` in
   [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py). `Scroll` has
   no validator today; `max_scrolls` is constrained inline with `Field(gt=0)`. Constrain `amount`
   the same way, with `Field(gt=0.0, le=1.0)`, or add a small validator mirroring the
   `0 < amount ≤ 1` check `Swipe` and `Drag` already run for their own `amount` field.

2. **Handler wiring.**

   Thread `s.amount` from `_do_scroll` through to `scroll_to_target` in
   [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py):
   the loop's `fraction = _STEP_FRACTION` initializer becomes
   `fraction = amount if amount is not None else _STEP_FRACTION`. The recovery path that shrinks
   `fraction` toward `_MIN_STEP_FRACTION` reads the same variable it already does, unchanged. One
   line does need to change: the overshoot message at the floor names `_MIN_STEP_FRACTION`
   directly, and an `amount` below that floor never shrinks before hitting it, so the message must
   name the actual `fraction` the failing step took, not the constant.

3. **Tests.**

   Over `FakeDriver`: a call with `amount` set below the default reaches a target that only the
   default step would overshoot, with the recovery path never triggered. A call omitting `amount`
   behaves exactly as it does today (a regression guard). A call whose `amount` sits at or below
   the floor fails on its first detected overshoot, naming the overshoot, rather than shrinking
   further. Schema tests cover the new field's range validation, mirroring the existing
   `swipe`/`drag` amount tests.

4. **Docs.**

   Document `amount` in [`docs/scenarios.md`](../../docs/scenarios.md) and its Japanese mirror,
   beside `direction`, `within`, and `maxScrolls`. State the default (0.6) and the unit (a
   fraction of the viewport), and cross-reference `swipe`'s and `drag`'s own `amount` for the
   shared convention.

### Prime directives preserved

- **AI never judges.**

  `amount` is a number a scenario author supplies; the loop reads it the same way it reads
  `max_scrolls` today. No model call enters the field's path.

- **Determinism first.**

  The field changes only the size of a step already bounded by `maxScrolls` and already
  re-queried after each step. It adds no fixed `sleep` and no new failure mode: an author-supplied
  `amount` at or below the recovery floor hits the same overshoot failure the default step already
  hits at that floor, naming whatever step actually ran rather than the default's.

- **App-agnostic.**

  `amount` reaches every backend through the one `scroll_to_target` loop; no backend branches on
  its value.

## Alternatives considered

- **Let `amount` also set the recovery floor.**

  Rejected. The floor exists so that a step too small to show any motion at all fails loudly
  instead of shrinking toward zero forever. Tying the floor to `amount` would let an author who
  requests a very small step also lower the floor below it, defeating the purpose the floor
  serves. The floor stays fixed at `swipe`'s own default distance regardless of `amount`; an
  author who requests a step at or below it accepts the same fail-fast overshoot outcome the loop
  already gives at that floor today.

- **Lower `_STEP_FRACTION` itself instead of adding a field.**

  Rejected. A smaller global default slows every `scroll` call that does not need one, and
  [BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md)
  already chose 0.6 for the ordinary case: large enough to leave real overlap between two reads
  without inviting the reactive recovery on an unremarkable screen. This item is additive — it
  changes no scenario that omits `amount`.

- **Express `amount` as an absolute distance (points or pixels) rather than a viewport fraction.**

  Rejected for the same reason `swipe` and `drag` already reject it: an absolute distance means
  something different on a small phone screen than on a tablet or a desktop browser window, so a
  scenario tuned on one device could overshoot or undershoot on another. A viewport fraction reads
  the same on every screen size, keeping the action portable across targets (prime directive 3).

- **Cap `amount` below 1 (for example, at the 0.6 default) to avoid reintroducing the overshoot risk
  BE-0326 deferred over.**

  Rejected.
  [BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md)'s
  detection-and-recovery loop already bounds that risk for every step, not only the default one: a
  step that shares nothing with the read before it triggers the same halving and reversing
  recovery regardless of the `amount` that produced it. A cap would block a legitimate use — an
  author whose screen scrolls unusually slowly, who wants one large step — while adding no
  protection the recovery does not already give.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — `amount` field on the `Scroll` scenario schema
- [ ] Unit 2 — handler wiring into `scroll_to_target`'s starting step
- [ ] Unit 3 — tests (schema range, `FakeDriver` reach-without-recovery, floor fail-fast, no-`amount` regression)
- [ ] Unit 4 — docs (`docs/scenarios.md` + ja mirror)

## References

- [`bajutsu/scenario/models/actions.py`](../../bajutsu/scenario/models/actions.py) — `Swipe`'s and
  `Drag`'s existing `amount` field, and the `Scroll` model this item extends
- [`bajutsu/orchestrator/actions/handlers/scroll.py`](../../bajutsu/orchestrator/actions/handlers/scroll.py) —
  `scroll_to_target`'s step-fraction loop and its overshoot recovery
- [`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift) —
  the resident runner's fixed-duration hold before lift, cited in *Motivation* as an open question
  about XCUITest's non-inertial guarantee
- [BE-0326](../BE-0326-scroll-to-element/BE-0326-scroll-to-element.md) — the `scroll` action this
  item extends; its *Alternatives considered* deferred a per-step `amount` knob
- [BE-0329](../BE-0329-scroll-observed-motion-decisions/BE-0329-scroll-observed-motion-decisions.md) —
  the overshoot detection and reactive shrink-and-reverse recovery this item complements
- [`docs/scenarios.md`](../../docs/scenarios.md) — the `scroll` action's authoring documentation

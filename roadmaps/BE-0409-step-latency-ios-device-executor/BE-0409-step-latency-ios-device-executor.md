**English** · [日本語](BE-0409-step-latency-ios-device-executor-ja.md)

# BE-0409 — iOS on-device step executor inside the XCTest runner

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0409](BE-0409-step-latency-ios-device-executor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0409") |
| Topic | Platform support |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md), [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md), [BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md), [BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0408](../BE-0408-step-latency-device-executor-protocol/BE-0408-step-latency-device-executor-protocol.md) defines a device-side step-execution protocol. The protocol fixes what moves from the host to the device, what stays on the host, and the selector-semantics contract that both platforms share. It is the route to Bajutsu's 250–500 millisecond per-step target, a target that host-side driver tuning alone cannot reach.

This item is the iOS half of that protocol's implementation. It adds a step executor that runs inside the XCTest runner process, on top of the existing `APIHandler` HTTP server. The executor resolves selectors and evaluates conditions natively. It no longer relays every poll back to the host over HTTP.

## Motivation

Today, iOS actuation and reading both go through `XcuitestElementProvider`. Every host-issued `query` or `tap` is a full HTTP round trip into the runner. A `wait for` step polls this way every 50 milliseconds. `POST /tap` alone resolves roughly eleven element attributes serially before it synthesizes the tap.

Running the same resolution and condition evaluation inside the runner process removes the round trip that each poll and each attribute read pays. The runner already has the accessibility tree in hand, so no intermediate read needs to return to the host.

Once the executor is built, check it against this estimate for a tap step:

- about 35 milliseconds for a single `app.snapshot()`
- 100–200 milliseconds for a coordinate-based tap
- 35–70 milliseconds for the settle judgment
- around 200–350 milliseconds in total, with the screenshot moved off the critical path

A later reader can confirm the estimate the way [BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md) took its driver-internal-tuning measurements. Trace a real tap step against this executor, then compare the result to today's iOS baseline of 0.95–1.07 seconds.

## Detailed design

**Implementation order.** This item is the third of four related items, and the order is strict:

1. the driver-internal-tuning item ([BE-0407](../BE-0407-step-latency-driver-internal-tuning/BE-0407-step-latency-driver-internal-tuning.md))
2. the device-side protocol (BE-0408)
3. this item
4. the Android executor ([BE-0410](../BE-0410-step-latency-android-device-executor/BE-0410-step-latency-android-device-executor.md))

**Work on this item must not begin until BE-0408 is complete.** This item implements the protocol and the selector-semantics contract of BE-0408. Starting against a design that is still changing would force rework each time the design changed.

BE-0410 likewise must not begin until this item is complete. Sequencing the two platform executors lets the selector-semantics port happen once, in this item, before it is repeated for the other platform. The port moves `resolve_unique` to Swift here and to Kotlin there. A gap that this port surfaces then does not need to be rediscovered independently by both platforms at once.

### Why the runner process, not BajutsuKit

The executor runs inside the XCTest runner process, not the in-app SDK (BajutsuKit), for three reasons:

- BajutsuKit has no event injection, no keyboard input, no frame-bearing accessibility tree, and no screenshot capture. Its `BajutsuTouch` component is an observation-only swizzle, and it does not synthesize input.
- The app process cannot reach SpringBoard's permission dialogs, the system keyboard, or `SFSafariViewController` content. [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md) already had to cross the same boundary to read Safari content.
- A process boundary lets the executor observe the app crashing. An in-app executor dies with the app it is testing.

### The executor

The executor is an addition to the existing `APIHandler` HTTP server. It adds a `POST /scenario` endpoint that takes a step sequence (per BE-0408's stage 4). The executor does four things natively:

1. **Resolve selectors from a single `app.snapshot()`.** The host's `resolve_unique` selector logic (`bajutsu/common/drivers/base.py`) is ported to Swift and runs inside the runner. It works against the same snapshot, not a fresh per-attribute read.
2. **Tap by coordinate at the resolved element's frame center.** The executor re-reads the element's attributes when staleness is suspected, and at no other time. BE-0407 takes the same posture for the same technique, and this item generalizes it to every tap the executor performs. The coordinate-tap approach itself is not new: [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md) already takes it for Safari content. This unit needs review before landing, for the reason BE-0407 flags. A coordinate tap trades one kind of correctness risk (a moved element) for latency, so the staleness fallback has to be right. See the Alternatives considered section below.
3. **Evaluate condition waits with a `snapshot()` loop at a 30–40 millisecond cadence**, entirely inside the runner. Today, BajutsuKit's screen-transition signal ([BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)) reaches the Python-side collector alone. Extending it to reach the runner directly lets `settled` use the signal as its condition, in place of a tree-diff poll.
4. **Return the screenshot and the element tree asynchronously**, alongside the step's result, over chunked transfer, one step at a time. Evidence capture stays off the critical path, which the step's own completion is measured against.

### A tap-synthesis floor, and the option to remove it

XCUITest waits for the app to reach quiescence around each synthesized event. That wait may remain a 100–200 millisecond floor even after the changes above. In that case, the private-API route that WebDriverAgent already takes is an option: disable `XCUIApplicationProcess`'s `waitForQuiescenceIncludingAnimationsIdle:`.

This change lives inside the test bundle, so it does not touch the application under test. It does depend on an Apple private API, which an Xcode update could change or remove without notice. This item accepts that risk if the floor turns out to matter in practice. It is not a change to make preemptively.

## Alternatives considered

- **Keep resolving element attributes per-read instead of moving to a single-snapshot resolution inside the runner.** Rejected. This is exactly the redundant-read pattern that BE-0407 already targets at the host level. A new executor that reproduced the same pattern on-device would forfeit most of this item's own motivation.
- **Route the executor's selector resolution through the host instead of porting it to Swift.** Rejected. Routing back to the host for selector resolution reintroduces the exact round trip this item exists to remove. It would leave no latency win over today's driver.
- **Disable XCUITest quiescence waiting from the start, rather than treating it as a fallback.** Rejected as a starting point. It depends on an undocumented private API. The measured impact of the wait is also unknown until the rest of this item's design is built and traced. Keeping it as a fallback, gated on the measured floor, avoids taking on that fragility before it is shown to be worth it.
- **Always re-verify identity after a coordinate tap, rather than when staleness is suspected.** Considered, not rejected outright. Always re-verifying is the safer default, and the place to start. It reintroduces a second attribute-read round trip after every tap, which gives back part of what unit 2 saves. A staleness heuristic keeps the saving on the common path. One example skips re-verification when nothing actuated the device between resolution and injection, matching what the executor already knows about its own timing. Which heuristic is safe enough to use by default needs review before this unit lands. The Progress checklist below tracks it as an open question, not a decided design.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Sequence status: blocked on BE-0408's completion** (see *Implementation
order* in *Detailed design*). Do not start the checklist below before then.

- [ ] Port `resolve_unique` selector semantics to Swift, verified against the driver conformance suite
  ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) once the
  protocol item's fixture extension lands.
- [ ] Add `POST /scenario` to `APIHandler`, accepting a step sequence per the protocol item's stage 4.
- [ ] Decide and document the staleness heuristic that gates identity re-verification after a
  coordinate tap (see Alternatives considered), before implementing the tap itself.
- [ ] Implement coordinate-based tap against the resolved frame center, with that heuristic wired in.
- [ ] Extend the BE-0310 screen-transition signal to reach the runner process directly, and use it as
  the `settled` condition.
- [ ] Move screenshot and tree capture off the critical path, returned asynchronously with the step
  result.
- [ ] Trace a real tap step against this executor and record the resulting per-step wall-clock here,
  compared against the 200–350 millisecond estimate above.
- [ ] If the tap-synthesis floor remains material after the above, evaluate disabling
  `waitForQuiescenceIncludingAnimationsIdle:` as a follow-up.
- [x] Once the `roadmap-id` workflow allocates the four ids on `main`, backfill a reciprocal
  `Related` link with BE-0407, BE-0408, and BE-0410 (see the same box on BE-0407).

## References

[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0310 — iOS accessibility screen-change readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md),
[BE-0396 — iOS SFSafariViewController tree](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md),
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py),
[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)

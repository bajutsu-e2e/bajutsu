**English** · [日本語](BE-XXXX-step-latency-ios-device-executor-ja.md)

# BE-XXXX — iOS on-device step executor inside the XCTest runner

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-step-latency-ios-device-executor.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md), [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md) |
<!-- /BE-METADATA -->

## Introduction

A companion proposal defines a device-side step-execution protocol — what moves from the host to the
device, what stays on the host, and the selector-semantics contract both platforms share — as the
route to Bajutsu's 250–500 millisecond per-step target, past what host-side driver tuning alone can
reach. This item is the iOS half of that protocol's implementation: a step executor that runs inside
the XCTest runner process, on top of the existing `APIHandler` HTTP server, resolving selectors and
evaluating conditions natively instead of relaying every poll back to the host over HTTP.

## Motivation

Today, iOS actuation and reading both go through `XcuitestElementProvider`, and every host-issued
`query` or `tap` is a full HTTP round trip into the runner. A `wait for` step polls this way every
50 milliseconds; `POST /tap` alone resolves roughly eleven element attributes serially before
synthesizing the tap. Running the same resolution and condition evaluation inside the runner process
— rather than shipping every intermediate read back to the host — removes the round trip each poll
and each attribute read pays, since the runner already has the accessibility tree in hand.

The estimate this item should be checked against once built: a tap step at roughly 35 milliseconds
for a single `app.snapshot()`, 100–200 milliseconds for a coordinate-based tap, and 35–70
milliseconds for the settle judgment — around 200–350 milliseconds total, with the screenshot moved
off the critical path. A later reader can confirm this the same way the driver-internal-tuning
companion item's measurements were taken: trace a real tap step against this executor and compare the
result to today's iOS baseline of 0.95–1.07 seconds.

## Detailed design

**Implementation order.** This item is the third of four related items in a strict order: the
driver-internal-tuning item, the device-side protocol item, then this item, then the Android executor
item. **Work on this item must not begin until the device-side protocol item is complete** — this
item implements that item's protocol and selector-semantics contract, and starting against a
still-changing design would need rework whenever that design changed under it. Once this item ships,
the Android executor item must not begin until this item is complete either: sequencing the two
platform executors lets the selector-semantics port (`resolve_unique` to Swift here, to Kotlin there)
happen once, in this item, before it is repeated for the other platform, so a gap this item's port
surfaces does not have to be independently rediscovered by both at once.

### Why the runner process, not BajutsuKit

The executor runs inside the XCTest runner process, not the in-app SDK (BajutsuKit), for three
reasons:

- BajutsuKit has none of event injection, keyboard input, a frame-bearing accessibility tree, or
  screenshot capture. Its `BajutsuTouch` component is an observation-only swizzle; it does not
  synthesize input.
- SpringBoard's permission dialogs, the system keyboard, and `SFSafariViewController` content are
  unreachable from inside the app process — the same boundary
  [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)
  already had to cross to read Safari content.
- Only a process boundary lets the executor observe the app crashing. An in-app executor dies with the
  app it is testing.

### The executor

Built as an addition to the existing `APIHandler` HTTP server, adding a `POST /scenario` endpoint that
takes a step sequence (per the companion protocol item's stage 4). The executor does four things
natively:

1. **Resolve selectors from a single `app.snapshot()`.** The host's `resolve_unique` selector logic
   (`bajutsu/common/drivers/base.py`) is ported to Swift and run inside the runner, against the same
   snapshot instead of a fresh per-attribute read.
2. **Tap by coordinate at the resolved element's frame center**, re-reading its attributes only when
   staleness is suspected — the same posture the companion driver-internal-tuning item takes for the
   same technique, generalized here to every tap this executor performs. The coordinate-tap approach
   itself is not new:
   [BE-0396](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md)
   already takes it for Safari content. Needs review before landing, for the same reason the companion
   item flags it: a coordinate tap trades one kind of correctness risk (a moved element) for latency,
   so the staleness fallback has to be right — see the Alternatives considered section below.
3. **Evaluate condition waits with a `snapshot()` loop at a 30–40 millisecond cadence**, entirely
   inside the runner. BajutsuKit's screen-transition signal
   ([BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md))
   today reaches only the Python-side collector; extending it to reach the runner directly lets
   `settled` use the signal as its condition rather than a tree-diff poll.
4. **Return the screenshot and the element tree asynchronously**, alongside the step's result, over
   chunked transfer, one step at a time — evidence capture stays off the critical path the step's own
   completion is measured against.

### A tap-synthesis floor, and the option to remove it

XCUITest waits for the app to reach quiescence around each synthesized event. If that wait remains a
100–200 millisecond floor even after the changes above, the private-API route WebDriverAgent already
takes is an option: disabling `XCUIApplicationProcess`'s
`waitForQuiescenceIncludingAnimationsIdle:`. This is a change inside the test bundle, so it does not
touch the application under test, but it depends on an Apple private API that an Xcode update could
change or remove without notice — a risk this item accepts only if the floor turns out to matter in
practice, not a change made preemptively.

## Alternatives considered

- **Keep resolving element attributes per-read instead of moving to a single-snapshot resolution
  inside the runner.** Rejected: this is exactly the redundant-read pattern the companion
  driver-internal-tuning item already targets at the host level; building a new executor that
  reproduces the same pattern on-device would forfeit most of this item's own motivation.
- **Route the executor's selector resolution through the host instead of porting it to Swift.**
  Rejected: routing back to the host for selector resolution reintroduces the exact round trip this
  item exists to remove, leaving no latency win over today's driver.
- **Disable XCUITest quiescence waiting from the start, rather than treating it as a fallback.**
  Rejected as a starting point: it depends on an undocumented private API, and the measured impact of
  the wait is not yet known until the rest of this item's design is built and traced. Keeping it as a
  fallback, gated on the measured floor, avoids taking on that fragility before it is shown to be
  worth it.
- **Always re-verify identity after a coordinate tap, rather than only when staleness is
  suspected.** Considered, not rejected outright — this is the safer default and the one to start
  from. Re-verifying unconditionally reintroduces a second attribute-read round trip after every tap,
  giving back part of what unit 2 saves; a staleness heuristic (e.g. skip re-verification when nothing
  actuated the device between resolution and injection, matching what the executor already knows about
  its own timing) keeps the saving on the common path. Which heuristic is safe enough to use by default
  needs review before this unit lands — the Progress checklist below tracks it as an open question, not
  a decided design.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Sequence status: blocked on the device-side protocol item's completion** (see *Implementation
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
- [ ] Once the `roadmap-id` workflow allocates the four ids on `main`, backfill a reciprocal
  `Related` link with the driver-internal-tuning, device-side protocol, and Android executor items
  (see the same box on the driver-internal-tuning item).

## References

[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[BE-0310 — iOS accessibility screen-change readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md),
[BE-0396 — iOS SFSafariViewController tree](../BE-0396-ios-sfsafariviewcontroller-tree/BE-0396-ios-sfsafariviewcontroller-tree.md),
[`bajutsu/common/drivers/base.py`](../../bajutsu/common/drivers/base.py),
[`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`](../../BajutsuKit/Runner/Sources/XcuitestElementProvider.swift)

**English** · [日本語](BE-XXXX-adb-device-side-actuation-ja.md)

# BE-XXXX — Resolve and actuate on the device so an Android gesture never aims at a stale coordinate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-adb-device-side-actuation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1455](https://github.com/bajutsu-e2e/bajutsu/pull/1455) (Unit 1, the directional-gesture anchor) |
| Topic | Driver & backend architecture |
| Related | [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md), [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md), [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md), [BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) |
<!-- /BE-METADATA -->

## Introduction

The Android backend of Bajutsu drives a gesture in three steps: the host reads the accessibility
tree, the host computes the target's pixel center, and the host injects a touch at that coordinate.
Nothing between the second step and the third proves the screen still matches the tree the
coordinate came from. Six roadmap items have made the read fresher — the resident reader, a settle
poll bounded by wall-clock time, an actuation-anchored barrier, a device-clock read mark — and the
`gestures` scenario still fails intermittently on the `smoke (adb)` continuous-integration (CI)
job. Making a read fresher cannot close a gap that opens after the read.

This item removes the gap rather than shortening it. The device resolves the target and injects the
gesture in one on-device operation, so the bounds a touch lands on are the bounds the device holds
at the moment it injects. The host keeps every decision the deterministic core owns: which element
a selector means, and whether that selector is ambiguous. The two other backends already work this
way. The Android backend is the outlier, and this item ends that.

The item also closes, first and separately, one concrete hole the barrier work left open: a
directional `swipe` resolves its anchor from an unsettled read that bypasses the barrier entirely.

## Motivation

A failing run names the defect more precisely than a description of the mechanism can. Run
[30746759314](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30746759314) failed the
[`gestures`](../../demos/showcase/scenarios/gestures.yaml) scenario on `smoke (adb)`:

```
expect: expected equals='pressed' but actual='idle'; expected equals='1' but actual='0'
```

Both gestures the scenario performs produced no result. The scenario long-presses one target, whose
accessibility value then mirrors `pressed`, and double-taps another, whose value mirrors `1`. Both
values stayed at the state the scenario had already asserted before the gestures ran.

The arithmetic of the wait rules out a stale read as the explanation. The Android lane sets
`BAJUTSU_MIN_WAIT_TIMEOUT=15`, and the trailing `expect` block is a condition wait
(`_poll_asserts`, `bajutsu/orchestrator/loop.py`) that re-reads the tree until the assertions pass
or that budget elapses. A tree fifteen seconds late would be a far larger lag than any measurement
in this repository has recorded. The two gestures did not miss their moment in the tree; the two
gestures never happened, because the touch landed somewhere that was not the target.

The run's own warnings agree. Twice inside the scenario the driver logged the read-lag barrier
expiring:

```
read lag: the last gesture did not change the projection within 4.0s — either the tree never
published it, or it moved no frame … Resolving from the current screen
```

That message is the barrier's designed concession. On expiry the driver proceeds with a coordinate
it could not confirm, because the alternative — failing every gesture that legitimately moves no
frame — would fail correct scenarios. A budget that must concede on expiry cannot be the guarantee.

One gap in the barrier's coverage explains how the scenario reaches its gestures from a screen
scrolled to an unintended offset. Every selector-addressed actuator on the adb driver — `tap`,
`doubleTap`, `longPress`, `pinch`, and `rotate` — resolves its target through `_center()`, which
calls `_settle()`, which waits out the pending catch-up barrier first. A directional `swipe` does
not. Its endpoints are computed above the driver, in `_directional_endpoints`
(`bajutsu/orchestrator/actions/handlers/gestures.py`), from a bare `driver.query()`: one read, no
settle, no barrier. The driver then receives two coordinates and never learns which element they
came from. `AdbDriver.scroll` does wait out the barrier, in `_pan_baseline`, but by then the
endpoints are already fixed.

The `gestures` scenario is exactly the shape that gap punishes. It brings its targets into view
with two consecutive `swipe: { on: …, direction: up }` steps. The second swipe resolves its anchor
from a single unbarriered read taken moments after the first swipe — the very window in which,
[BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) measured, the Android tree still
describes the pre-gesture screen. The measurement recorded in `AdbDriver._READ_LAG_S` is specific:
a swipe moved the Log form 73 pixels, and four consecutive reads spanning 1.2 seconds past the
gesture still reported the pre-swipe frames. An anchor 73 pixels off makes the second swipe start
in the wrong place, and the form comes to rest at an offset the scenario did not intend.

Fixing that one gap is worth doing on its own, and it is the first unit below. It does not, however,
close the class. Suppose the anchor is barriered and the read is perfect. The host has still read a
tree, computed a coordinate from it, and handed that coordinate to `adb shell input`, whose own
process startup takes a further fraction of a second before a touch reaches the screen. Any motion
inside that window — a fling still settling, a recomposition, a system dialog — lands the touch on
whatever occupies the coordinate by then. The window is small, the CI emulator is slow, and the lane
runs seventeen scenarios per toolkit on every relevant pull request. A small window sampled that
often is a flake.

The other two backends do not have the window at all, and the difference is structural rather than
incidental. The XCUITest backend resolves a selector to a unique element on the host, hands the
device a *handle* to it, and lets the runner resolve the handle against the live element tree and
act on whatever bounds that element holds at that instant (`_resolve_handle` and `_actuate`,
`bajutsu/drivers/xcuitest.py`). A handle whose element is gone comes back as `stale`, and the driver
re-resolves rather than touching a coordinate
([BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md));
the handle is derived from element identity, so an unchanged screen keeps its handles valid
([BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md)).
The Playwright backend computes a coordinate, but a browser answers a read synchronously and a page
does not publish its layout asynchronously behind the read, so no equivalent window exists. The adb
backend is the only one that computes a pixel coordinate from an asynchronously published tree and
then injects it blind.

Android already offers the seam to close the gap, and Bajutsu already runs on it. The resident
UI Automator server
([BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md))
is a long-lived instrumentation session that holds a warm `UiAutomation` and answers
`GET /source` over a loopback socket. The session that dumps the hierarchy can also find a node and
click it, in the same process, microseconds apart. Reads were moved onto the device for speed. Moving
actuation onto the device is the same move, for correctness.

## Detailed design

The design keeps the deterministic core exactly where it is and moves only the coordinate arithmetic.
Selector semantics stay on the host: `resolve_unique` decides which element a selector means, and an
ambiguous selector still fails immediately rather than acting on the first match (prime directive 2).
What crosses to the device is a *handle* — an address for one already-chosen element — and the
device's only judgement is whether that handle still names exactly one node. It answers `stale` when
it does not, and never falls back to a coordinate.

Two constraints bound every unit. Prime directive 3 keeps the change app-agnostic: the handle is
built from the accessibility attributes the tree already carries, and no per-app knob is introduced.
Prime directive 2 forbids a fixed `sleep`: the device-side path removes waits rather than adding
them, and where a wait remains it stays a condition wait with a ceiling.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

1. **Resolve a directional gesture's anchor through the driver's actuation-grade read.** A
   directional `swipe` and a `drag` resolve their anchor element in `_directional_endpoints` from a
   bare `driver.query()`, which is the one selector-addressed actuation path that skips the settle
   and the catch-up barrier every other one takes. Give the driver an optional way to say "read a
   tree I am willing to resolve an actuation target from", as a narrow structural protocol beside
   `ViewportProvider`, `ReadLagProvider`, and `ReadOrderProvider` in `bajutsu/drivers/base.py`. The
   adb driver implements it with its existing settle; every other backend does not implement it and
   keeps its single read byte-for-byte. This unit is independent of the rest and ships first.
2. **Serve actuation from the resident UI Automator server, addressed by handle.** Add
   `POST /act` to the resident server: a request names an action (`tap`, `doubleTap`, `longPress`
   with a duration, `pan` from an anchor), and a handle. The server resolves the handle against the
   live accessibility tree through `UiDevice`, reads the node's current bounds, and injects the
   gesture from the same warm `UiAutomation` session. A handle matching no node, or more than one,
   is answered `stale` with no injection. The double tap is injected in-process, so its two taps
   fall inside the platform's double-tap window without the raw `sendevent` sequence and the
   `adb root` it needs.
3. **Derive the handle from element identity, and route the adb driver's actuators through it.**
   The handle is a digest of the attributes the dumped tree and the live node both carry — the
   resource identifier, the content description, the class, and the ordinal among identical
   siblings — so a screen that has not changed keeps its handles valid, exactly as BE-0312
   established for XCUITest. `AdbDriver.tap`, `double_tap`, `long_press`, `pinch`, and `rotate`
   resolve the selector on the host, then send the handle. A `stale` reply re-queries and re-sends,
   bounded, as BE-0289 does; zero or many matches still raise `ElementNotFound` or
   `AmbiguousSelector` from the host and spend no attempt.
4. **Keep the coordinate path as the declared degraded mode, not the default.** A device without
   the resident channel — an older server, a channel that died mid-run — still has to work. The
   coordinate actuators stay, reached only when the channel is unavailable, and the degrade is
   logged the way the read channel's degrade already is. What must not survive is a silent choice
   between the two: a run reports which actuation path it used.
5. **Narrow the read-lag barrier to the reads that still need it.** With actuation resolved
   on-device, the catch-up barrier no longer guards a coordinate, and the actuator path stops arming
   it. The barrier stays where a read must genuinely postdate an action with no assertion to satisfy
   — the `extract` poll and the `scroll` stop condition — which is the narrower claim BE-0332's
   Unit 1 makes. Removing the arming from `tap`, `long_press`, and `double_tap` also removes the
   4-second concession those gestures pay whenever they change no frame.
6. **Cover the contract, deterministically and on-device.** The fast gate covers the handle digest,
   the `stale` re-resolve loop, the degrade to coordinates, and the new protocol's opt-out (a
   backend that does not implement it behaves as before). The driver conformance suite (BE-0114)
   adds the contract the fast gate cannot reach: after a gesture, the element the device acted on is
   the element the selector named. The Android lane runs the `gestures` scenario repeatedly on one
   dispatch, because a defect that appears in roughly one run in three is not disproved by one green
   run.

### Verification

The defect does not reproduce on a developer machine. An Apple-silicon `bajutsu_api34` emulator
answers every read current, and the whole Android lane passes locally; the lag and the flake belong
to the x86_64 CI emulator. Verification therefore runs there, dispatched with `gh workflow run
android-e2e.yml --ref <branch>` on a branch that narrows the lane to `gestures`, repeated enough
times in one dispatch to sample the failure rate rather than a single draw. The measurement that
proves the fix is the one that measures the defect: log, for every actuation, the bounds the host
resolved and the bounds the device acted on, and require the two to name the same element.

Units 1 and 5 also have a fast-gate consequence worth stating: neither may change the behavior of a
backend that reports no read lag. The deterministic suite pins that, so a regression in the opt-out
fails on Linux rather than on an emulator.

## Alternatives considered

**Widen the read-lag budget.** The budget already stands at four seconds, sized from two independent
measurements, and the failing run spent it twice without the tree confirming the gesture. Widening
it trades run time for a smaller failure rate and leaves the concession on expiry untouched, because
a gesture that legitimately moves no frame can never satisfy the barrier however long it runs.

**Fail the run when the barrier expires, instead of proceeding.** This converts a flake into a hard
failure, which is honest but not a fix: the benign case the barrier cannot distinguish — a pan
already at the end of the content, a tap that changes only a mirrored value — is routine, so the
lane would fail on correct scenarios. The driver cannot tell the two apart from the tree alone,
which is precisely why the answer has to come from the device.

**Verify after the fact: check that something changed, and retry the gesture if not.** Retrying an
actuation that may already have applied is not safe in general — a double tap that half-landed
becomes three taps — and the check has no sound stop condition, since a correct gesture may change
nothing observable. The device-side path avoids the question by never leaving the outcome in doubt.

**Re-read immediately before injecting the coordinate.** This shrinks the window without closing it,
and it pays an extra read on every gesture. The read is the dominant per-step cost on adb, which is
the whole subject of
[BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md), so the trade is a slower
run for a smaller version of the same flake.

**Move selector resolution onto the device as well, and send the selector rather than a handle.**
This is the larger version of the same idea, and it is rejected on prime directive 2. Deciding that
a selector matches exactly one element is a determinism-core decision; moving it into the Kotlin
server would put it beyond the reach of the deterministic suite and split one rule across two
languages. The handle keeps the decision on the host and sends only its result.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — a directional `swipe` / `drag` resolves its anchor through the driver's
      actuation-grade read.
- [x] Unit 2 — `POST /act` on the resident UI Automator server, resolving a handle and injecting
      from the warm session.
- [x] Unit 3 — identity-derived handles, and the adb driver's actuators routed through them. `tap`
      and `long_press` address an element by its four accessibility fields. `pinch` and `rotate` stay
      on coordinates because a two-finger gesture needs a frame, not a center; `double_tap` stays
      because the lane measured the device path losing the platform's double-tap window.
- [ ] Unit 4 — the coordinate path kept as a declared, logged degraded mode.
- [ ] Unit 5 — the read-lag barrier narrowed to the reads that still need it.
- [ ] Unit 6 — deterministic and conformance coverage, and a repeated Android-lane run.

Log:

- 2026-08-02 — Unit 1. `_directional_endpoints` resolves its anchor through a new
  `SettledReadProvider` structural protocol rather than a bare `query()`, so a directional `swipe`
  and a `drag` wait out the catch-up barrier the other selector-addressed actuators already wait
  out. The adb driver implements the protocol with its existing settle; every other backend does not
  implement it and keeps its single read unchanged. Status → In progress. Units 2–6, which move
  actuation itself onto the device, remain.

- 2026-08-02 — Host half of Unit 3 (PR #1455): `ActRequest`, `parse_hierarchy_with_identities`, the
  resident-channel `act` client, and `tap` routed through it, with the coordinate actuators kept as the
  degraded path. Unit 2's Kotlin endpoint is written but not landed — it cannot be compiled in the
  authoring environment (a JDK, but no Android SDK and no `kotlinc`), and an APK build that fails would
  take `smoke`, `golden`, and `conformance` down together. Only `tap` is routed; `double_tap`,
  `long_press`, `pinch`, and `rotate` stay on coordinates until the endpoint is proven on the lane.

- 2026-08-03 — Units 2 and 3 (PR #1455). `POST /act` landed on the resident server: it honors the
  request's `since` mark, settles its own dump, matches the four identity fields in document order,
  and injects from the warm session — answering `409` rather than guessing when the identity no
  longer names the same number of nodes. `long_press` and `double_tap` join `tap` on that path, so
  the rooted `sendevent` double tap (BE-0208) and the zero-length `input swipe` press-and-hold both
  become the degraded path and neither needs root any more. Landed without a local compile: the
  authoring environment has a JDK but no Android SDK, and the policy on its network denies
  `dl.google.com`, so the Android lane is the first compiler this endpoint meets. Every branch it
  adds is reachable from the host on the first run, which is what makes that run informative.
  The evidence that host-side plumbing alone could not close the flake is in the `52d4ee1` run: the
  failure moved from `gestures` (a long press) to `controls` (a plain `tap`), and every lease logged
  `resident server has no /act endpoint (HTTP 404)`.

- 2026-08-03 — What the lane said about the endpoint, on the first run that had one (`f519a4a`). It
  compiled, `/act` answered — the `HTTP 404` that every previous lease logged is gone — and twelve
  scenarios passed, `controls` among them, the scenario the run before had failed. **The long press
  passed**: `gestures` no longer fails on `log.longpress.value`, which is the residual
  coordinate-staleness class closing. The one regression was mine: routing `double_tap` through the
  endpoint as two in-process `UiDevice.click` calls failed with `log.doubletap.value` still `0`,
  where the rooted `sendevent` sequence had been passing. `click` settles internally between the two
  calls, so the pair lands outside the platform's double-tap window — and resolving on the device
  buys a double tap nothing anyway, since both taps share one center and it is the *interval* that is
  delicate. `double_tap` is back on `sendevent`, and the endpoint no longer offers the kind.

- 2026-08-03 — Run diagnostics (PR #1455), so the next timing failure is diagnosable from a log
  rather than a re-run. Nothing configured logging before, so `logger.debug` across the codebase
  reached nobody and four investigations of this flake each ended in a re-run instead of a cause.
  `BAJUTSU_LOG_LEVEL` (`bajutsu/diagnostics.py`) turns it on, and the Android lane sets it by
  default. The adb driver now records each read with its device mark, when the barrier opened and
  what closed it, how the tree settled, the frame every selector resolved to, and which path each
  gesture took. Two of them are warnings, because each means an actuator is about to resolve against
  a screen the driver cannot vouch for: the read-lag barrier spending its budget — now naming the
  marks it waited on, which is what separates "the device published nothing" from "the gesture moved
  no frame" — and the settle poll falling through its deadline, which had been silent.

## References

- [BE-0332](../BE-0332-read-lag-barrier/BE-0332-read-lag-barrier.md) — the read-lag barrier this
  item narrows, and the source of the 73-pixel measurement quoted in *Motivation*.
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) —
  the resident UI Automator server that Unit 2 extends from reads to actuation.
- [BE-0289](../BE-0289-xcuitest-stale-handle-reresolve/BE-0289-xcuitest-stale-handle-reresolve.md) —
  the stale-handle re-resolve loop Unit 3 mirrors from the XCUITest backend.
- [BE-0312](../BE-0312-xcuitest-content-addressed-snapshot-handle/BE-0312-xcuitest-content-addressed-snapshot-handle.md) —
  why a handle is derived from element identity rather than a snapshot generation.
- [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md) — the Android CI
  lane where the flake appears, and the `sendevent` double tap Unit 2 retires.
- [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) — why an extra Android
  read is expensive enough to rule out re-reading before every gesture.
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the driver
  conformance suite where Unit 6 checks the contract against the real actuator.

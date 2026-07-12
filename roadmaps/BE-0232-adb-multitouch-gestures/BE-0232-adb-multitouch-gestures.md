**English** · [日本語](BE-0232-adb-multitouch-gestures-ja.md)

# BE-0232 — Multi-touch gestures on the adb driver (pinch / rotate)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0232](BE-0232-adb-multitouch-gestures.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0232") |
| Implementing PR | _pending_ |
| Topic | Platform expansion (Android / Web / Flutter) |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md) |
<!-- /BE-METADATA -->

## Introduction

The multi-touch abstraction already exists across the tool. `Driver.pinch` / `Driver.rotate`
are part of the driver protocol ([`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)),
guarded by the `multiTouch` capability; the preflight check
([BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)) rejects a pinch or
rotate on a single-touch backend before any device work; and the shared scenario
[`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml)
exercises both, passing today on the iOS `xcuitest (multi-touch)` job. The one backend that cannot
run it is adb: the Android driver's `pinch` / `rotate` raise `UnsupportedAction`, and its capability
set omits `multiTouch`, because adb actuation has been single-touch through the `input` command.
This item makes the adb driver perform genuine two-finger gestures, so the existing shared scenario
runs unchanged on Android — completing the last scenario held out of the Android on-device e2e lane
([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)).

## Motivation

BE-0208's Android e2e lane now runs the shared set down to its single-touch limit — tab navigation
([BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md)), deep scroll and
segmented controls (BE-0208 Unit 5), a rooted `sendevent` double-tap, and runtime permissions
(BE-0208 Unit 6). `gestures_multitouch` is the one scenario left out, and the reason is specific: a
pinch or a rotate needs two contacts moving at once, which the `input` command cannot express. So
the shared scenario that iOS runs on XCUITest has no Android counterpart, and the third backend's
on-device coverage stops one scenario short of the iOS set.

The gap is not in the scenario — it is a driver-layer capability, exactly where prime directive 3
(app-agnostic) places it. A scenario is authored once (`pinch: { sel: …, scale: 2.0 }`) and must
resolve on every backend that advertises `multiTouch`; whether the platform can synthesize two
simultaneous contacts lives inside the Driver. The precedent is close at hand:
[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) already
taught the adb driver a rooted-device `sendevent` path for the double-tap, because chaining two
`input tap` calls overran the platform's double-tap window. That path established the machinery this
item extends — discovering the emulator's touch input node (the lowest-numbered
`virtio_input_multi_touch_*` `eventN`, BE-0208 Unit 5), scaling a screen coordinate into the touch
device's raw range, and emitting a protocol-B contact as `sendevent` lines in one `adb shell`
round-trip. A double-tap uses one contact slot; a pinch or rotate uses two.

The determinism contract holds throughout. The gesture's outcome is machine-checkable, not
inferred: the showcase's gesture screen flips a mirrored accessibility value (`idle` → `pinched` /
`rotated`) only once the platform's own recognizer fires, so the assertion proves the two-finger
actuation actually landed. No LLM enters the path, and the waits are condition waits on those
values, never a fixed sleep (prime directives 1 and 2).

One honest limit shapes the design. The `sendevent` path requires a rooted device — the same
precondition BE-0210's double-tap carries — and unlike the double-tap, a two-finger gesture has **no
single-touch fallback**: there is nothing to approximate it with. On a non-rooted device the driver
must fail clearly rather than emit a degraded gesture that silently passes. BE-0208's e2e lane runs
`adb root` on the emulator, so CI satisfies the precondition.

## Detailed design

The work rests on the `sendevent` machinery BE-0210 established, extends it from one contact to two,
declares the capability, gives the Android showcase the gesture screen the shared scenario expects,
and folds the scenario into the e2e lane. It touches the adb command builders, the adb Driver, the
Android showcase app, and the BE-0208 wiring; it changes no shared scenario and adds no LLM to any
path.

### Work breakdown (MECE)

1. **Two-contact `sendevent` sequences in the command builders.** Add protocol-B pinch and rotate
   sequences to [`bajutsu/adb.py`](../../bajutsu/adb.py), alongside the existing
   `sendevent_double_tap_cmd`. Both drive two tracking slots (slot 0 and slot 1) that move together
   across a series of interleaved `SYN_REPORT` frames, so the gesture animates rather than
   teleporting — the platform's `GestureDetector` needs the motion to classify a scale or a rotation.
   Pinch moves the two contacts along a line through the target centre, spreading apart for
   `scale > 1` and together for `scale < 1`; rotate places them on a diameter and sweeps that
   diameter through `radians`. Both reuse the raw-coordinate scaling and touch-node discovery from
   the double-tap path. Pure command-builder functions, unit-testable without a device.
2. **Implement `pinch` / `rotate` in the adb driver.** Replace the `UnsupportedAction` stubs in
   [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) with implementations that resolve `sel`
   to its element frame, take the centre, and emit the two-contact sequence via the existing `_run`
   seam. A rooted device is required: reuse `_rooted()` and, when it is false, raise a clear
   `UnsupportedAction` naming the root precondition — there is no single-touch fallback for a
   two-finger gesture, so a degraded approximation is never emitted.
3. **Declare the `multiTouch` capability.** Add `base.Capability.MULTI_TOUCH` to the driver's static
   `CAPABILITIES` frozenset. The set is a class constant the preflight (BE-0082) reads via
   `backends.capabilities_for` **with no device**, so the declaration is necessarily static; the
   root precondition is enforced at actuation time (item 2), not in the capability set. Document the
   consequence plainly: `gestures_multitouch` is admitted on adb by preflight, and on a non-rooted
   device it fails fast at the gesture step rather than before it.
4. **Give the Android showcase a gesture screen.** Add a Compose gesture screen gated by the
   `SHOWCASE_GESTURES` launch env, mirroring the iOS
   [`GestureView`](../../demos/showcase/ios/swiftui/Sources/GestureView.swift): two flat, scroll-free
   targets tagged `log.pinch` / `log.rotate` whose mirrored accessibility values start `idle` and
   flip to `pinched` / `rotated` when Compose's `detectTransformGestures` recognizes a zoom or a
   rotation. Because the targets carry the same ids the shared scenario already uses, the existing
   [`gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml) runs
   unchanged — no Android twin scenario, unlike the permission flow. Scope the a11y Compose target
   (`showcase-compose`), matching the lane; the Views toolkit is a documented follow-up.
5. **Fold `gestures_multitouch` into the e2e lane.** Add it to
   [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile)'s `E2E_SCENARIOS`; the
   `e2e` target already runs `adb root`, so the rooted precondition is met. Update the CI docs
   ([`docs/ci.md`](../../docs/ci.md) and its Japanese mirror). This discharges the last scenario
   BE-0208 holds out.
6. **Tests.** Unit-test the command builders (the two-slot protocol-B frame shape for a given centre,
   scale, and radians) and the driver's root gate (non-rooted raises `UnsupportedAction`; rooted
   emits the sequence through a patched `_run`), following the existing double-tap tests. Optionally
   extend the driver conformance suite
   ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) with a
   pinch/rotate case on the backends that advertise `multiTouch`.

## Alternatives considered

- **Approximate a pinch with two sequential `input swipe`s.** Rejected: two swipes are two
  single-touch drags in sequence, not two simultaneous contacts, so the platform never recognizes a
  scale or rotation — it would either do nothing or scroll. A gesture that "runs" but the recognizer
  ignores is worse than an honest `UnsupportedAction`, and it would make the assertion pass or fail
  for the wrong reason.
- **Drive multi-touch through on-device UiAutomator instrumentation instead of `sendevent`.**
  Rejected for this item: an instrumentation APK can synthesize multi-pointer gestures without root,
  but it diverges from the adb driver's shell-only actuation model, adds a device-side test artifact
  to build and install, and is a larger surface than the double-tap machinery already in place. It
  stays a documented future option for lifting the root precondition.
- **Leave `gestures_multitouch` iOS-only and mark Android multi-touch out of scope.** Rejected: it
  leaves a permanent backend-parity gap in a capability the tool already models end to end
  (`Driver.pinch` / `rotate`, the `multiTouch` capability, the preflight, the shared scenario). The
  rooted `sendevent` path is a bounded, precedented way to close it on the emulator the lane already
  runs.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Two-contact protocol-B pinch / rotate sequences in `bajutsu/adb.py`.
- [x] Implement `pinch` / `rotate` in the adb driver, gated on a rooted device (no fallback).
- [x] Declare `multiTouch` in the adb driver's static capability set; document the root precondition.
- [x] Add the Compose `SHOWCASE_GESTURES` gesture screen (`log.pinch` / `log.rotate` mirrors).
- [x] Fold `gestures_multitouch` into the BE-0208 e2e lane; update `docs/ci.md` (+ja).
- [x] Unit-test the command builders and the driver root gate. (Conformance-suite extension deferred:
      the shared `gestures_multitouch` scenario already exercises pinch / rotate on adb on-device.)

**Log**

- Implemented in _pending_: two-slot protocol-B `sendevent_gesture_cmd` + `pinch_contacts` /
  `rotate_contacts` geometry in `bajutsu/adb.py`; `pinch` / `rotate` on the adb driver gated on a
  rooted device via `_two_finger_gesture` (no single-touch fallback); `MULTI_TOUCH` declared in the
  driver's static capability set; the Compose `SHOWCASE_GESTURES` gesture screen; `gestures_multitouch`
  folded into the Android e2e lane; unit tests for the builders, geometry, and the root gate.

## References

[BE-0007 — Android backend](../BE-0007-android-backend/BE-0007-android-backend.md),
[BE-0208 — Android on-device e2e in CI](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md),
[BE-0210 — Android on-device actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md),
[BE-0082 — Preflight capability check](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md),
[BE-0223 — Reach every Android tab over adb](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md),
[BE-0114 — Driver conformance suite](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md),
[`bajutsu/adb.py`](../../bajutsu/adb.py),
[`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py),
[`bajutsu/orchestrator/actions/handlers/gestures.py`](../../bajutsu/orchestrator/actions/handlers/gestures.py),
[`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml),
[`demos/showcase/ios/swiftui/Sources/GestureView.swift`](../../demos/showcase/ios/swiftui/Sources/GestureView.swift)

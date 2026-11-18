**English** · [日本語](BE-0232-adb-multitouch-gestures-ja.md)

# BE-0232 — Multi-touch gestures on the adb driver (pinch / rotate)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0232](BE-0232-adb-multitouch-gestures.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0232") |
| Implementing PR | [#947](https://github.com/bajutsu-e2e/bajutsu/pull/947) |
| Topic | Platform support |
| Related | [BE-0007](../BE-0007-android-backend/BE-0007-android-backend.md), [BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md), [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md), [BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md) |
<!-- /BE-METADATA -->

## Introduction

The multi-touch abstraction already exists across the tool. `Driver.pinch` and `Driver.rotate` are
part of the [driver](../../docs/glossary.md#driver-backend-actuator-platform) protocol
([`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py)). The `multiTouch` capability guards
them. The preflight check
([BE-0082](../BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md)) rejects a
pinch or rotate on a single-touch backend before any device work.

The shared scenario
[`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml)
exercises both gestures. It passes today on the iOS `xcuitest (multi-touch)` job.

The one backend that cannot run that scenario is adb. The Android driver's `pinch` and `rotate`
raise `UnsupportedAction`, and its capability set omits `multiTouch`. Adb actuation has been
single-touch through the `input` command.

This item makes the adb driver perform genuine two-finger gestures, so the existing shared scenario
runs unchanged on Android. That completes the last scenario held out of the Android on-device e2e
lane ([BE-0208](../BE-0208-android-emulator-e2e-ci/BE-0208-android-emulator-e2e-ci.md)).

## Motivation

BE-0208's Android e2e lane now runs the shared set down to its single-touch limit:

- tab navigation ([BE-0223](../BE-0223-adb-tab-bar-navigation/BE-0223-adb-tab-bar-navigation.md));
- deep scroll and segmented controls (BE-0208 Unit 5);
- a rooted `sendevent` double-tap;
- runtime permissions (BE-0208 Unit 6).

`gestures_multitouch` is the one scenario left out, for a specific reason. A pinch or a rotate needs
two contacts moving at once, which the `input` command cannot express. The shared scenario that iOS
runs on XCUITest therefore has no Android counterpart. The third backend's on-device coverage stops
one scenario short of the iOS set.

The gap is not in the scenario. It is a driver-layer capability, which is where prime directive 3
(app-agnostic) places it. A scenario is authored once (`pinch: { sel: …, scale: 2.0 }`). It must
resolve on every backend that advertises `multiTouch`. Whether the platform can synthesize two
simultaneous contacts lives inside the Driver.

The precedent is close at hand.
[BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md) already taught
the adb driver a rooted-device `sendevent` path for the double-tap. Chaining two `input tap` calls
overran the platform's double-tap window. That path established the machinery this item extends:

- discovering the emulator's touch input node (the lowest-numbered `virtio_input_multi_touch_*`
  `eventN`, BE-0208 Unit 5);
- scaling a screen coordinate into the touch device's raw range;
- emitting a protocol-B contact as `sendevent` lines in one `adb shell` round-trip.

A double-tap uses one contact slot. A pinch or rotate uses two.

The determinism contract holds throughout. The gesture's outcome is machine-checkable, not inferred.
The showcase's gesture screen flips a mirrored accessibility value (`idle` → `pinched` / `rotated`)
only once the platform's own recognizer fires. The assertion therefore proves that the two-finger
actuation actually landed. No LLM enters the path. The waits are condition waits on those values,
never a fixed sleep (prime directives 1 and 2).

One honest limit shapes the design. The `sendevent` path requires a rooted device, the same
precondition BE-0210's double-tap carries. Unlike the double-tap, a two-finger gesture has **no
single-touch fallback**, because nothing approximates it. On a non-rooted device the driver must fail
clearly. It must not emit a degraded gesture that silently passes. BE-0208's e2e lane runs `adb root`
on the emulator, so CI satisfies the precondition.

## Detailed design

The work rests on the `sendevent` machinery BE-0210 established and extends it from one contact to
two. It declares the capability, gives the Android showcase the gesture screen the shared scenario
expects, and folds the scenario into the e2e lane.

The change touches the adb command builders, the adb Driver, the Android showcase app, and the
BE-0208 wiring. It changes no shared scenario and adds no LLM to any path.

### Work breakdown (MECE)

1. **Two-contact `sendevent` sequences in the command builders.** Add protocol-B pinch and rotate
   sequences to [`bajutsu/adb.py`](../../bajutsu/adb.py), alongside the existing
   `sendevent_double_tap_cmd`. Both drive two tracking slots (slot 0 and slot 1). The slots move
   together across a series of interleaved `SYN_REPORT` frames, so the gesture animates rather than
   teleporting. The platform's `GestureDetector` needs the motion to classify a scale or a rotation.
   - Pinch moves the two contacts along a line through the target centre. They spread apart for
     `scale > 1` and come together for `scale < 1`.
   - Rotate places the contacts on a diameter and sweeps that diameter through `radians`.

   Both reuse the raw-coordinate scaling and touch-node discovery from the double-tap path. They are
   pure command-builder functions, unit-testable without a device.
2. **Implement `pinch` / `rotate` in the adb driver.** Replace the `UnsupportedAction` stubs in
   [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py). The new implementations resolve `sel` to
   its element frame, take the centre, and emit the two-contact sequence via the existing `_run`
   seam. A rooted device is required. Reuse `_rooted()` and, when it is false, raise a clear
   `UnsupportedAction` naming the root precondition. A two-finger gesture has no single-touch
   fallback, so a degraded approximation is never emitted.
3. **Declare the `multiTouch` capability.** Add `base.Capability.MULTI_TOUCH` to the driver's static
   `CAPABILITIES` frozenset. The set is a class constant that the preflight (BE-0082) reads via
   `backends.capabilities_for` **with no device**, so the declaration is necessarily static. The root
   precondition is enforced at actuation time (item 2), not in the capability set. Document the consequence plainly: preflight admits `gestures_multitouch` on adb. On a non-rooted device it fails fast at the
   gesture step rather than before it.
4. **Give the Android showcase a gesture screen.** Add a Compose gesture screen gated by the
   `SHOWCASE_GESTURES` launch env. It mirrors the iOS
   [`GestureView`](../../demos/showcase/ios/swiftui/Sources/GestureView.swift). Two flat, scroll-free
   targets are tagged `log.pinch` and `log.rotate`. Their mirrored accessibility values start `idle`
   and flip to `pinched` / `rotated` when Compose's `detectTransformGestures` recognizes a zoom or a
   rotation.

   The targets carry the same ids the shared scenario already uses. The existing
   [`gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml) therefore
   runs unchanged, with no Android twin scenario, unlike the permission flow. Scope the a11y Compose
   target (`showcase-compose`), matching the lane. The Views toolkit is a documented follow-up.
5. **Fold `gestures_multitouch` into the e2e lane.** Add it to
   [`demos/showcase/android/Makefile`](../../demos/showcase/android/Makefile)'s `E2E_SCENARIOS`. The
   `e2e` target already runs `adb root`, so the rooted precondition is met. Update the CI docs
   ([`docs/ci.md`](../../docs/ci.md) and its Japanese mirror). This discharges the last scenario
   BE-0208 holds out.
6. **Tests.** Unit-test two things, following the existing double-tap tests:
   - the command builders (the two-slot protocol-B frame shape for a given centre, scale, and
     radians);
   - the driver's root gate (non-rooted raises `UnsupportedAction`; rooted emits the sequence through
     a patched `_run`).

   Optionally extend the driver conformance suite
   ([BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md)) with a
   pinch/rotate case on the backends that advertise `multiTouch`.

## Alternatives considered

- **Approximate a pinch with two sequential `input swipe`s.** Rejected. Two swipes are two
  single-touch drags in sequence, not two simultaneous contacts. The platform never recognizes a
  scale or rotation, so the gesture would either do nothing or scroll. A gesture that "runs" but the
  recognizer ignores is worse than an honest `UnsupportedAction`. It would make the assertion pass or
  fail for the wrong reason.
- **Drive multi-touch through on-device UiAutomator instrumentation instead of `sendevent`.**
  Rejected for this item. An instrumentation APK can synthesize multi-pointer gestures without root.
  But it diverges from the adb driver's shell-only actuation model and adds a device-side test
  artifact to build and install. It is also a larger surface than the double-tap machinery already in
  place. It stays a documented future option for lifting the root precondition. That option has
  since landed: the resident UI Automator server injects both fingers without root, and the
  `sendevent` sweep remains as its rooted fallback
  ([issue #2391](https://github.com/bajutsu-e2e/bajutsu/issues/2391)).
- **Leave `gestures_multitouch` iOS-only and mark Android multi-touch out of scope.** Rejected. It
  leaves a permanent backend-parity gap in a capability the tool already models end to end
  (`Driver.pinch` / `rotate`, the `multiTouch` capability, the preflight, the shared scenario). The
  rooted `sendevent` path is a bounded, precedented way to close the gap on the emulator the lane
  already runs.

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

- Implemented in [#947](https://github.com/bajutsu-e2e/bajutsu/pull/947): two-slot protocol-B `sendevent_gesture_cmd` + `pinch_contacts` /
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

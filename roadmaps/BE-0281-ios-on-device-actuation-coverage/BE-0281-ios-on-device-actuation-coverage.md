**English** · [日本語](BE-0281-ios-on-device-actuation-coverage-ja.md)

# BE-0281 — Add real on-device actuation coverage to the iOS CI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0281](BE-0281-ios-on-device-actuation-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0281") |
| Implementing PR | [#1181](https://github.com/bajutsu-e2e/bajutsu/pull/1181) |
| Topic | Platform support |
| Related | [BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md), [BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0240](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md) |
<!-- /BE-METADATA -->

## Introduction

The idb scenarios in the iOS E2E lane (smoke, golden, visual) check only whether the screen shows the
right content. They use `wait` and `expect`, and none contains a `tap` / `type` / `swipe` / `back` or gesture
step. The one real action that idb actuates in CI is `tap`, through the conformance job. Its other
actuators (`type_text`, `swipe`, `scroll`, `back`, `long_press`, `double_tap`, `tap_point`) and the
whole device-control family are actuated on no iOS lane.

Android already exercises all of these on a real device
([BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md)), and iOS
should reach the same bar. The showcase already carries the scenarios (`gestures.yaml`,
`device.yaml`, `push.yaml`), but they run only in local-only Makefile targets and not in CI. This
item wires real iOS actuation into the existing `ios-e2e.yml`.

## Motivation

Android is by far the most-exercised backend. Today it is also the only backend that proves `swipe` /
`scroll` / `back` / `longPress` / `doubleTap` / `relaunch` / `setLocation` and clipboard on a real device
([BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md),
[BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md)).
iOS's actuators are proven only at the command-construction level, with mocked-subprocess unit
tests. The asymmetry undercuts the backend-agnostic promise for the platform that ships the required
`E2E` gate. iOS actuators bypass shared code in the XCUITest runner channel and the idb companion,
so a per-backend regression there would fail no iOS lane today.

The gap is missing CI wiring, not missing scenarios, because the scenarios exist. Two additions
close the gesture and text side:

- an interaction job on `ios-e2e` (tap / type / swipe / scroll / back, plus doubleTap and longPress
  from `gestures.yaml`)
- one XCUITest scenario that exercises `/type`, `/swipe`, and `/back` on the runner channel

Device control (push / status-bar override and clear / keychain / foreground and background) is
advertised by idb and XCUITest through `DEVICE_CONTROL_ALL`, with simctl builders that are
unit-tested. But `device.yaml` and `push.yaml` run on no iOS lane, so only Android proves any device
control on a real device, and only `setLocation` and clipboard.

macOS runners are metered at ten times the Linux rate, so the design decides deliberately which new
jobs gate and which only signal. The design reuses the existing `ios-e2e.yml` and adds no new
workflow. It follows the structural-parity shape that
[BE-0271](../BE-0271-e2e-workflow-structural-parity/BE-0271-e2e-workflow-structural-parity.md)
established. In the spirit of the readiness-stability work in
[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md),
new jobs start as a non-gating signal.

## Detailed design

This section works at proposal altitude. The work divides into the units below, which are mutually
exclusive and collectively exhaustive (MECE).

- **iOS idb interaction job.** Promote an interaction scenario (tap / type / swipe / scroll / back, plus
  doubleTap and longPress from `gestures.yaml`) into a metered `ios-e2e` job, so idb's actuators run
  against a real Simulator and not only in the conformance contract.
- **XCUITest actuation.** Add a scenario that exercises `/type`, `/swipe`, and `/back` on the
  `XcuitestDriver` runner channel. Today that channel reaches on-device CI only for tap, pinch, and
  rotate.
- **iOS device control.** Connect `device.yaml` and `push.yaml` as a non-gating job that exercises
  `setLocation` / clipboard / status-bar override and clear / keychain reset / foreground and
  background against a real Simulator.
- **Cost discipline.** Right-size which jobs are required and which only signal, given the macOS
  metering. Reuse the single `ios-e2e.yml` file and the per-job split shape instead of adding a new
  workflow.

## Alternatives considered

- **Rely on Android for cross-backend actuation confidence.** Rejected. Android's breadth does not
  transfer, because iOS actuators run through the XCUITest runner channel and the idb companion, code
  that the Android lane never touches. Trusting Android for iOS behavior is exactly the
  backend-agnostic assumption that the conformance work exists to check and not to take on faith.
- **Make every new job a required gate.** Rejected. The macOS ten-times metering makes that costly,
  and the Simulator lane's flakiness history
  ([BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md))
  argues for landing new actuation jobs as a signal first and promoting them only once they prove
  stable.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] iOS idb interaction job (`back`; `type`/`swipe`/`scroll`/`doubleTap`/`longPress` stay on
      XCUITest — idb collapses the native tab bar into one opaque group and cannot reach any tab, a
      constraint the proposal did not anticipate; verified on-device before landing, see the log).
- [x] XCUITest actuation scenario (`/type` via `search.yaml`, `/swipe` + `/back` via `notices.yaml`
      on the runner channel — reusing existing shared scenarios rather than a new file).
- [x] iOS device-control job (`device.yaml` / `push.yaml`), non-gating.
- [x] Right-size gating versus signal for the macOS-metered jobs; reuse `ios-e2e.yml`.

### Log

- [#1181](https://github.com/bajutsu-e2e/bajutsu/pull/1181) — Landed as one new non-gating `actuation (idb)` job
  (`navigation.yaml` for `back`, `device.yaml` + `push.yaml` for device control) plus two extra runs
  wired into the existing `xcuitest (multi-touch)` job (`search.yaml` for `/type`, `notices.yaml` for
  `/swipe` + `/back`) — one metered job added, not two, since idb and XCUITest coverage share a build
  where possible. Scope narrowed from the original design: idb cannot tap the native tab bar at all
  (SPEC.md §3, BE-0107), so the interaction job could not run the tab-crossing `gestures.yaml` /
  `controls.yaml` scenarios over idb as the proposal assumed — confirmed by an on-device run before
  implementation (`一致なし` on a `Log` tab tap). `type` / `swipe` / `scroll` / `longPress` /
  `doubleTap` therefore stay proven over idb only by mocked unit tests; closing that gap needs a new
  tab-free showcase screen, left for a follow-up item.

## References

- [BE-0210 — Android on-device actuation fidelity](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md)
- [BE-0221 — Guarantee shared showcase scenarios run unchanged on Android](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md)
- [BE-0218 — Stabilize the E2E Simulator gate](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
- [BE-0240 — Capability-aware automatic actuator selection for iOS](../BE-0240-ios-capability-aware-actuator-selection/BE-0240-ios-capability-aware-actuator-selection.md)
- `.github/workflows/ios-e2e.yml`, `demos/showcase/scenarios/navigation.yaml`, `demos/showcase/scenarios/device.yaml`, `demos/showcase/scenarios/push.yaml`, `demos/showcase/scenarios/search.yaml`, `demos/showcase/scenarios/notices.yaml`

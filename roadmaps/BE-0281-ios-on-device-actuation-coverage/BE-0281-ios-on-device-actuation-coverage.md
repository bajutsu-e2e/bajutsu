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

The iOS E2E lane's idb scenarios — smoke, golden, visual — only check whether the screen shows the right content: they use `wait` and
`expect` and contain no `tap` / `type` / `swipe` / `back` / gesture step. idb's only real action
actuation in CI is `tap`, via the conformance job. Its other actuators (`type_text`, `swipe`,
`scroll`, `back`, `long_press`, `double_tap`, `tap_point`) and the whole device-control family are
actuated on no iOS lane. Android already exercises all of these on a real device
([BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md)); iOS
should reach the same bar. The showcase already carries the scenarios (`gestures.yaml`,
`device.yaml`, `push.yaml`) — they run only in local-only Makefile targets, not CI. This item
wires real iOS actuation into the existing `ios-e2e.yml`.

## Motivation

Android is by far the most-exercised backend and today the only one proving `swipe`, `scroll`,
`back`, `longPress`, `doubleTap`, `relaunch`, `setLocation`, and clipboard on a real device
([BE-0210](../BE-0210-android-actuation-fidelity/BE-0210-android-actuation-fidelity.md),
[BE-0221](../BE-0221-android-scenario-portability-guarantee/BE-0221-android-scenario-portability-guarantee.md)).
iOS's actuators are proven only at the command-construction level (mocked-subprocess unit tests).
This asymmetry undercuts the backend-agnostic promise for the platform that ships the required
`E2E` gate: iOS actuators bypass shared code in the XCUITest runner channel and the idb companion,
so a per-backend regression there would fail no iOS lane today.

The gap is not missing scenarios — they exist — but missing CI wiring. An interaction job on
`ios-e2e` (tap / type / swipe / scroll / back, plus doubleTap and longPress from `gestures.yaml`),
and one XCUITest scenario exercising `/type` / `/swipe` / `/back` on the runner channel, close the
gesture and text side. Device control (push, status-bar override/clear, keychain, foreground /
background) is advertised by idb and XCUITest via `DEVICE_CONTROL_ALL` with simctl builders that
are unit-tested, but `device.yaml` / `push.yaml` run on no iOS lane, so only Android proves any
device control (and only `setLocation` + clipboard) on a real device.

macOS runners are metered at ten times the Linux rate, so the design is deliberate about which new
jobs gate versus signal. It reuses the existing `ios-e2e.yml` (no new workflow) and follows the
structural-parity shape [BE-0271](../BE-0271-e2e-workflow-structural-parity/BE-0271-e2e-workflow-structural-parity.md)
established, and it starts new jobs as non-gating signal in the spirit of the readiness-stability
work in [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md).

## Detailed design

Proposal altitude. The work is MECE along the units below.

- **iOS idb interaction job.** Promote an interaction scenario (tap / type / swipe / scroll / back,
  plus doubleTap and longPress from `gestures.yaml`) into a metered `ios-e2e` job, so idb's
  actuators run against a real Simulator rather than only in the conformance contract.
- **XCUITest actuation.** Add a scenario exercising `/type`, `/swipe`, and `/back` on the
  `XcuitestDriver` runner channel, which today reaches on-device CI only for tap / pinch / rotate.
- **iOS device control.** Connect `device.yaml` / `push.yaml` as a non-gating job exercising
  `setLocation`, clipboard, status-bar override / clear, keychain reset, and foreground /
  background against a real Simulator.
- **Cost discipline.** Right-size which jobs are required versus signal given the macOS metering,
  reusing the single `ios-e2e.yml` file and the per-job split shape rather than a new workflow.

## Alternatives considered

- **Rely on Android for cross-backend actuation confidence.** Android's breadth does not transfer:
  iOS actuators run through the XCUITest runner channel and the idb companion, code the Android
  lane never touches. Trusting Android for iOS behavior is exactly the backend-agnostic assumption
  the conformance work exists to check, not to take on faith.
- **Make every new job a required gate.** The macOS ten-times metering makes that costly, and the
  Simulator lane's flakiness history ([BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md))
  argues for landing new actuation jobs as signal first and promoting only once they prove stable.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] iOS idb interaction job (`back`; `tap`/`type`/`swipe`/`scroll`/`doubleTap`/`longPress` stay on
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
- `.github/workflows/ios-e2e.yml`, `demos/showcase/scenarios/gestures.yaml`, `demos/showcase/scenarios/device.yaml`, `demos/showcase/scenarios/push.yaml`

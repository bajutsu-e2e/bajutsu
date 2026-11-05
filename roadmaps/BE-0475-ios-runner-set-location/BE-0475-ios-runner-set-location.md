**English** · [日本語](BE-0475-ios-runner-set-location-ja.md)

# BE-0475 — Set the location through the XCUITest runner on a real iOS device

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0475](BE-0475-ios-runner-set-location.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0475") |
| Topic | Platform support |
| Related | [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), [BE-0458](../BE-0458-real-device-host-channels/BE-0458-real-device-host-channels.md), [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md) |
<!-- /BE-METADATA -->

## Introduction

The `setLocation` step runs on the iOS Simulator and the Android emulator, but a scenario that uses
the step is skipped on a real iPhone. The step calls `simctl location`, and `simctl` reaches only the
Simulator.

Xcode 16.3 added `XCUIDevice.location`, a property that a UI test assigns to simulate the device's
position. Its documentation states no Simulator-only restriction.

This item adds a `/location` route to the XCUITest runner. The route sets that property inside the
test process, and `setLocation` goes through the route. The scenario step keeps its shape, and a real
device gains the `deviceControl.setLocation` capability.

## Motivation

Today the XCUITest backend advertises the whole device-control family and the permission grants on
the Simulator. `capabilities_for_run` subtracts all of them for a real device
(`bajutsu/common/backends.py`), because `simctl` backs every one. The preflight therefore skips a
scenario that contains `setLocation`, with the reason "needs `deviceControl.setLocation`".

A location-dependent flow (a store finder, a geofence, a map screen) cannot run on real hardware.
This holds whether the device is attached locally or reserved on Device Farm. Core Location behaves
differently from the Simulator on those devices, so a team wants those runs most.

The runner already lives on the device and already receives every actuation over HTTP. The gap is a
missing route, not a missing platform feature.

A reader can check the outcome after this item ships. A scenario with `setLocation: {lat, lon}`,
followed by an assertion on a coordinate the app renders, passes on a real iPhone, where the scenario
is skipped today. On a real-device target, `bajutsu doctor --scenario` no longer reports that the
scenario needs `deviceControl.setLocation`.

## Detailed design

### What the API provides

`XCUIDevice.shared.location` is an optional `XCUILocation`, readable and writable, available from
Xcode 16.3 and iOS 16.4. `XCUILocation(location: CLLocation)` carries latitude, longitude, and
course. Assigning `nil` returns the device to its physical location.

Apple's documentation states that the property supplies a proxy location "a test uses". It names the
UI-automation path as the way to simulate location. The test-plan Simulated Location setting does not
reach an app that runs in its own process.

The documentation does not say whether a physical device honors the property. Unit 1 settles that on
hardware before any other unit starts.

### Routing

`setLocation` moves from `simctl` to the runner on both targets. One route for both targets, instead
of a second path for real devices, gives a scenario the same behavior on every target.

| Target | Before | After |
|---|---|---|
| Simulator | `simctl location set` | runner `POST /location`; `simctl location set` when the runner answers `unsupported` |
| Real device | not supported (skipped) | runner `POST /location` |
| Live WebDriver endpoint | not supported | unchanged (out of scope) |

The Simulator keeps `simctl` as a fallback, so the change regresses no existing setup. A Simulator
runtime older than iOS 16.4 answers `unsupported`. So does a runner built with a toolchain older than
Xcode 16.3. `setLocation` then runs `simctl location set` as it does today, and the location keeps the
`simctl` lifetime described below. A real device has no fallback, so `unsupported` fails the step
there.

The other device-control operations (`push`, the clipboard steps, `clearKeychain`, `background`,
`foreground`, the status-bar overrides) and the permission grants keep `simctl`, and stay Simulator-only.

### Runner (`BajutsuKit/Sources/BajutsuRunner`, `BajutsuKit/Runner/Sources`)

- **Route.** `openapi.yaml` declares `POST /location` with body `{lat: number, lon: number}`.
  `APIHandler.swift` implements the route. It is the generated `APIProtocol` that `RunnerServer`
  serves through `LegacyBackedTransport`. The handler runs on the main thread, like the other
  actuations. It builds `XCUILocation(location: CLLocation(latitude:longitude:))` and assigns the
  value to `XCUIDevice.shared.location`. The reply is `{"status": "ok"}`, or `{"status":
  "unsupported"}` when the runner cannot honor the call. A body with a missing or out-of-range
  coordinate (latitude outside −90…90, longitude outside −180…180) answers 400. `DELETE /location`
  assigns `nil` and answers the same statuses. The host uses it to reset the device between
  scenarios, and no scenario step exposes it.
- **Provider seam.** `ElementProviding` gains `setLocation(lat:lon:) -> LocationResult` and
  `clearLocation() -> LocationResult`. A fake provider can then test `APIHandler`, as it does for the
  other routes. The XCUITest-backed provider (`BajutsuKit/Runner/Sources/XcuitestElementProvider.swift`)
  guards both calls with `#available(iOS 16.4, *)` and `#if compiler(>=6.1)`. The compiler check works
  here, and `#if swift(>=6.1)` does not. The runner target builds in Swift 5.9 language mode, so a
  language-mode check would compile the call out on every toolchain. The runner's deployment target
  stays at iOS 15 (`BajutsuKit/Runner/project.yml`). A toolchain older than Xcode 16.3 still builds the
  runner, which then answers `unsupported`.
- **Contract.** The server stubs are generated from `openapi.yaml`, so the route and its reply are
  declared there first. `Router.swift` is legacy (BE-0381), and this item does not extend it.

### Host driver and wiring (`bajutsu/`)

- **Driver.** `XcuitestDriver` gains `set_location(lat, lon)` and `clear_location()`. Both call
  `/location` through the existing transport. Both map `unsupported` to `UnsupportedAction`, with a
  message that names iOS 16.4 and Xcode 16.3 as the floor.
- **Controller.** The `controller()` of the XCUITest environments returns a `DeviceControl`. Its
  `set_location` calls the live driver, and its other operations remain the `simctl` ones
  (`bajutsu/common/platform_lifecycle/device_control.py`). On the Simulator, its `set_location` catches
  the driver's `UnsupportedAction` and runs `simctl location set` instead. On a real device, the other
  operations are absent from the capability set, so the preflight never reaches them. Like the
  environment's `_foreground`, the controller's `set_location` reads the lease's driver at call time.
  The driver is `_lease_driver`, or the member's driver on a device group. The read happens at call
  time because `controller(eff)` takes no driver argument.
- **Capabilities.** `capabilities_for_run` stops subtracting `Capability.DC_SET_LOCATION` for a
  real-device target:

  ```python
  caps - (base.DEVICE_CONTROL_ALL - {base.Capability.DC_SET_LOCATION}) - ...
  ```

  The live WebDriver branch is untouched, because `XcuitestLiveDriver.CAPABILITIES` is its own
  source of truth. `bajutsu doctor` reads the same narrowed set. Its capability preflight stops
  flagging `setLocation` on a real-device target, with no change of its own.

  The host computes the advertised set from config before it acquires a device. The set cannot know
  the device's iOS version or the toolchain that built the runner. A real device below the iOS 16.4 /
  Xcode 16.3 floor therefore advertises `deviceControl.setLocation` and fails the step at run time,
  where the preflight skips the scenario today.

  That is a deliberate exception to the invariant `capabilities_for_run` documents: an advertised
  capability never raises `UnsupportedAction`. No config field carries the device's OS version at
  preflight time, which forces the exception. Unit 4 covers the failure and the message that names the
  floor.

### Lifetime of the simulated location

`simctl location set` persists on the Simulator until something clears it. `XCUIDevice.location` is a
proxy held by the runner's test process. The runner's own lifetime differs by target.

On the Simulator, Bajutsu keeps the runner warm across scenarios
([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md))
and respawns it cold after `_MAX_WARM_REUSES` reuses or after a crash. On a real device, the runner
lasts one lease.

Left alone, a location set in one scenario would carry into the next few scenarios on the Simulator.
The location would then vanish at the next cold respawn. What a scenario observes would depend on its
position in the run. Prime directive 2 rules that out, so the host pins the location to the scenario:

- **Reset at every lease start.** When a lease begins, cold or warm (`_resume_warm`), the environment
  calls `DELETE /location`. Every scenario starts from the device's physical position, whatever ran
  before it. On the Simulator, the reset also runs `simctl location clear`
  (`simctl.Env.clear_location`). Assigning `nil` drops merely the runner's proxy. The device then
  returns to the location `simctl` last set, including one the fallback path above wrote. An
  `unsupported` reply from the runner is then a no-op on its own.
- **No re-apply after a crash.** When the runner crashes mid-scenario, the run pipeline discards the
  lease. It re-runs the whole scenario from the start on a fresh lease
  (`bajutsu/common/runner/pipeline.py`). The lease-start reset fires, and the scenario's own
  `setLocation` step runs again. The coordinate returns without any state the host must remember.

Unit 1 also records what the app sees after the test process exits, and the docs state the result.
Today nothing in Bajutsu clears a `simctl` location between scenarios. The reset at lease start
therefore changes behavior only for a scenario that relied on a location an earlier scenario set.

### Permissions on a real device

The location permission grant is a `simctl privacy` operation and stays unavailable on a real device.
An app without location access prompts for it. The existing `handleSystemAlert` step and
`systemAlertHandling.rules` answer that prompt. The docs say so next to the new capability.

### Work breakdown (MECE)

1. **Spike on hardware and Simulator.** A throwaway UI test assigns `XCUIDevice.shared.location`, and
   an app screen renders `CLLocationManager`'s coordinate. Record three facts:
   - whether a real iPhone and the Simulator honor the assignment;
   - whether an app that `simctl launch` started (rather than `XCUIApplication`) sees the assignment;
   - what the app sees after the test process exits.

   **Gate:** if the real device does not honor the assignment, close the item as `Rejected` with the
   evidence. If only the Simulator differs, keep `simctl` there and ship the real-device path alone.
2. **Runner route.** `POST` and `DELETE /location` in `openapi.yaml` and `APIHandler.swift`, the
   provider methods, and `BajutsuRunnerTests` covering success, `unsupported`, and the 400 cases.
3. **Host driver.** `XcuitestDriver.set_location` and `clear_location`, with unit tests on a fake
   transport.
4. **Controller, lifetime, and capabilities.** The composed `DeviceControl` with its Simulator
   fallback, the reset at lease start (cold and warm, plus `simctl location clear` on the Simulator),
   and the `capabilities_for_run` narrowing. Tests show three things:
   - A warm-reused runner starts the next scenario at the physical position, even after a scenario
     took the `simctl` fallback.
   - An `unsupported` reply falls back to `simctl` on the Simulator. On a real device, the reply fails
     the step with a message naming the iOS 16.4 / Xcode 16.3 floor.
   - A real-device target runs `setLocation` while still skipping `push`.
5. **Documentation.** `docs/drivers.md` (capability table), `docs/ios-device-cloud.md`,
   `docs/devicefarm.md`, and `docs/architecture.md`, each with its `docs/ja/` mirror.
6. **On-device verification.** A showcase scenario that sets a location and asserts the rendered
   coordinate, run on a real iPhone and on the Simulator.

## Alternatives considered

| Option | Why it was not chosen |
|---|---|
| Real device only, keeping `simctl` on the Simulator | The blast radius is smaller, and the option remains the fallback if Unit 1 shows the Simulator behaves differently. It leaves two code paths for one step, so a scenario could pass on one target and differ on the other. |
| Host-side location service (`idevicesetlocation`, `pymobiledevice3`) | It adds a host dependency and needs a developer-image or tunnel setup that changes across iOS releases. It cannot run on Device Farm, where the device cloud owns the host. |
| Xcode test-plan Simulated Location or a GPX file | The location is fixed per test run and cannot change mid-scenario. Apple's documentation also states that the setting does not reach an app that runs in its own process, which is the UI-automation case. |
| An app-side mock in BajutsuKit | The app must link and honor a Bajutsu hook, so the app no longer sees the real Core Location stack. That breaks the app-agnostic principle for a capability the OS already offers. |
| A `clearLocation` step | Assigning `nil` makes the step possible, but this item keeps the scenario schema unchanged. Add the step once a scenario needs to return to the physical position within a run. |
| Appium `mobile:` command on the live WebDriver route | A different transport with its own capability set. Cover it in a separate item after this one proves the native path. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — spike: do a real iPhone and the Simulator honor `XCUIDevice.location`
- [ ] Unit 2 — runner `POST`/`DELETE /location`, provider methods, OpenAPI contract, Swift tests
- [ ] Unit 3 — `XcuitestDriver.set_location` / `clear_location` and tests
- [ ] Unit 4 — composed `DeviceControl` with Simulator fallback, lease-start reset, `capabilities_for_run` narrowing, tests
- [ ] Unit 5 — documentation in English and Japanese
- [ ] Unit 6 — on-device verification on a real iPhone and the Simulator

## References

- [`XCUIDevice.location`](https://developer.apple.com/documentation/xcuiautomation/xcuidevice/location),
  [`XCUILocation`](https://developer.apple.com/documentation/xcuiautomation/xcuilocation), and
  [Simulating location in tests](https://developer.apple.com/documentation/xcode/simulating-location-in-tests).
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), which
  removed the `simctl`-backed capabilities from a real-device run.
- [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), the precedent for
  backing a subset of device control per platform.

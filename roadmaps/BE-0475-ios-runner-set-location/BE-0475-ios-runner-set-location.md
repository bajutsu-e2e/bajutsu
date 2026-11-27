**English** · [日本語](BE-0475-ios-runner-set-location-ja.md)

# BE-0475 — Set the location through the XCUITest runner on a real iOS device

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0475](BE-0475-ios-runner-set-location.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0475") |
| Implementing PR | [#2615](https://github.com/bajutsu-e2e/bajutsu/pull/2615) (units 2–4) |
| Topic | Platform support |
| Related | [BE-0128](../BE-0128-device-step-capability-preflight/BE-0128-device-step-capability-preflight.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), [BE-0458](../BE-0458-real-device-host-channels/BE-0458-real-device-host-channels.md), [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md) |
<!-- /BE-METADATA -->

## Introduction

The `setLocation` step runs on the iOS Simulator and the Android emulator. Bajutsu skips a scenario
that uses the step on a real iPhone. The step calls `simctl location`, and `simctl` reaches the
Simulator alone.

Xcode 16.3 added `XCUIDevice.location`. A UI test assigns that property to simulate the device's
position. Its documentation states no Simulator restriction.

This item adds a `/location` route to the XCUITest runner. The route assigns the property inside the
test process, and `setLocation` goes through the route. The scenario step keeps its shape. A real
device gains the `deviceControl.setLocation` capability.

## Motivation

Today the XCUITest backend advertises device control and permission grants on the Simulator.
`capabilities_for_run` subtracts them for a real device (`bajutsu/common/backends.py`). `simctl`
backs every one of them. The preflight then skips a scenario with `setLocation`. The reason reads
"needs `deviceControl.setLocation`".

A location-dependent flow (a store finder, a geofence, a map screen) cannot run on real hardware.
That holds for a local device and for a device reserved on Device Farm. Core Location behaves
differently from the Simulator on real devices. A team wants those runs most.

The runner already lives on the device and already receives every actuation over HTTP. The gap is a
missing route, not a missing platform feature.

A reader can check the outcome after this item ships. Today a scenario with
`setLocation: {lat, lon}` never runs on a real iPhone. After this item, it runs there. An assertion
on a coordinate the app renders passes. `bajutsu doctor --scenario` no longer reports a missing
`deviceControl.setLocation` there.

## Detailed design

### What Xcode provides

`XCUIDevice.shared.location` is an optional `XCUILocation`, readable and writable. The property
needs Xcode 16.3 and iOS 16.4. `XCUILocation(location: CLLocation)` carries latitude, longitude, and
course. Assigning `nil` returns the device to its physical location.

Apple's documentation states that the property supplies a proxy location "a test uses". It names the
UI-automation path as the way to simulate location. The test-plan Simulated Location setting does not
reach an app that runs in its own process.

The documentation does not say whether a physical device honors the property. Unit 1 settles that on
hardware before any other unit starts.

### Routing

`setLocation` moves from `simctl` to the runner on both targets. One route for both targets gives a
scenario the same behavior everywhere. A second path for real devices would not.

| Target | Before | After |
|---|---|---|
| Simulator | `simctl location set` | runner `POST /location`, with `simctl location set` on `unsupported` |
| Real device | not supported (skipped) | runner `POST /location` |
| Live WebDriver endpoint | not supported | unchanged (out of scope) |

The Simulator keeps `simctl` as a fallback, so the change regresses no existing setup. A Simulator
runtime older than iOS 16.4 answers `unsupported`. So does a runner built with a toolchain older than
Xcode 16.3. `setLocation` then runs `simctl location set` as it does today. The location keeps the
`simctl` lifetime described below. A real device has no fallback, so `unsupported` fails the step
there.

The other device-control operations keep `simctl` and stay on the Simulator:

- `push` and the clipboard steps;
- `clearKeychain`, `background`, and `foreground`;
- the status-bar overrides;
- the permission grants.

### Runner (`BajutsuKit/Sources/BajutsuRunner`, `BajutsuKit/Runner/Sources`)

- **Route.** `openapi.yaml` declares `POST /location` with body `{lat: number, lon: number}`.
  `APIHandler.swift` implements the route on the generated `APIProtocol`. `RunnerServer` serves that
  protocol through `LegacyBackedTransport`. The handler runs on the main thread, like the other
  actuations.
- **Request and reply.** The handler builds `XCUILocation(location: CLLocation(latitude:longitude:))`.
  It assigns the value to `XCUIDevice.shared.location`. The reply is `{"status": "ok"}`, or
  `{"status": "unsupported"}` when the runner cannot honor the call. An assignment that raises
  answers `{"status": "error"}`. That reply is a runner fault, and it never falls back to `simctl`.
  A body with a missing or
  out-of-range coordinate answers 400. Latitude ranges over −90…90 and longitude over −180…180.
- **Reset route.** `DELETE /location` assigns `nil` and answers the same statuses. The Python side
  uses it to reset a real device between scenarios. No scenario step exposes it.
- **Provider seam.** `ElementProviding` gains `setLocation(lat:lon:) -> LocationResult` and
  `clearLocation() -> LocationResult`. A fake provider can then test `APIHandler`, as it does for the
  other routes.
- **Toolchain guard.** `BajutsuKit/Runner/Sources/XcuitestElementProvider.swift` guards both calls.
  The guard is `#available(iOS 16.4, *)` plus `#if compiler(>=6.1)`. The compiler check works here,
  and `#if swift(>=6.1)` does not. The runner target builds in Swift 5.9 language mode. A
  language-mode check would compile the call out on every toolchain.
- **Deployment target.** The target stays at iOS 15 (`BajutsuKit/Runner/project.yml`). A toolchain
  older than Xcode 16.3 still builds the runner. The runner then answers `unsupported`.
- **Contract.** The server stubs come from `openapi.yaml`. Declare the route and its reply there
  first. `Router.swift` is legacy (BE-0381), and this item does not extend it.

### Python driver and wiring (`bajutsu/`)

- **Driver.** `XcuitestDriver` gains `set_location(lat, lon)` and `clear_location()`. Both call
  `/location` through the existing transport. Both map `unsupported` to `UnsupportedAction`. The
  message names iOS 16.4 and Xcode 16.3 as the floor.
- **Controller.** The `controller()` of the XCUITest environments returns a `DeviceControl`. Its
  `set_location` calls the live driver. Its other operations stay the `simctl` ones
  (`bajutsu/common/platform_lifecycle/device_control.py`).
- **Simulator fallback.** On the Simulator, `set_location` catches `UnsupportedAction` from the
  driver. It then runs `simctl location set`. On a real device, the capability set omits the other
  operations. The preflight never reaches them.
- **Driver lookup.** The controller's `set_location` reads the lease's driver at call time, like
  `_foreground`. The driver is always `_lease_driver`. The location is device-wide, and every
  device-group member shares that runner. The lookup happens at call time because
  `controller(eff)` takes no driver argument.
- **Capabilities.** The real-device narrowing lives in `capabilities_for_route("ios-device")`.
  `capabilities_for_run` and the server's worker dispatch both read that route. The route stops
  subtracting `Capability.DC_SET_LOCATION` for a real-device target:

  ```python
  caps - (base.DEVICE_CONTROL_ALL - {base.Capability.DC_SET_LOCATION}) - ...
  ```

  The live WebDriver branch keeps its own source of truth, `XcuitestLiveDriver.CAPABILITIES`.
  `bajutsu doctor` reads the same narrowed set. Its capability preflight stops flagging
  `setLocation` on a real-device target. It needs no change.

  The Python side computes the advertised set from config before it acquires a device. The set cannot
  know the device's iOS version or the toolchain that built the runner. A real device below the
  iOS 16.4 / Xcode 16.3 floor still advertises `deviceControl.setLocation`. The step then fails at
  run time. Today the preflight skips that scenario.

  `capabilities_for_run` documents an invariant. An advertised capability never raises
  `UnsupportedAction`. This item makes a deliberate exception. No config field carries the device's OS version at
  preflight time, which forces the exception. Unit 4 covers the error and the message that names the
  floor.

### Lifetime of the simulated location

`simctl location set` persists on the Simulator until something clears it. The Unit 1 spike showed
that `XCUIDevice.location` persists the same way there. The assignment writes the same device-wide
value as `simctl location set`. The later write wins, and the value outlives the runner's test
process. Whether a real device keeps the value after the runner exits is still open.

The runner's own lifetime differs by target. On the Simulator, Bajutsu keeps the runner warm across
scenarios
([BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md)).
It respawns the runner cold after `_MAX_WARM_REUSES` reuses or after a runner exit mid-scenario. On a
real device, the runner lasts one lease.

Left alone, a location set in one scenario would carry into every later scenario on the Simulator.
That holds across warm reuses and cold respawns alike. What a scenario observes would depend on its
position in the run. Prime directive 2 rules that out, so Bajutsu pins the location to the scenario:

- **Simulator reset.** Each lease start, cold or warm (`_resume_warm`), runs
  `simctl location clear` in the device preparation, before the app launches. Every scenario then
  starts from the device's own position, even in a location request at app launch. The spike showed
  that the runner writes the same device-wide value, so this one call clears both routes.
- **Real-device reset.** A real device has no `simctl`. The lease start calls `DELETE /location`
  instead, after the runner has launched the app. An `unsupported` reply means the runner never set
  a location. Any other reply discards the runner and fails the lease.
- **Attach.** Attaching touches nothing device-wide, so a `repl --attach` run skips the reset.
- **No re-apply.** When the runner exits mid-scenario, the run pipeline discards the lease. It
  re-runs the whole scenario on a fresh lease (`bajutsu/common/runner/pipeline.py`). The lease-start
  reset runs, and the scenario's own `setLocation` step runs again. The coordinate returns without
  any state that Bajutsu must remember.

Unit 1 also records what the app sees after the test process exits, and the docs state the result.
Today nothing in Bajutsu clears a `simctl` location between scenarios. The reset changes behavior
for a scenario that relied on an earlier scenario's location.

### Permissions on a real device

The location permission grant is a `simctl privacy` operation. It stays unavailable on a real device.
An app without location access prompts for it. The existing `handleSystemAlert` step and
`systemAlertHandling.rules` answer that prompt. The docs say so next to the new capability.

### Work breakdown (MECE)

1. **Spike on hardware and Simulator.** A throwaway UI test assigns `XCUIDevice.shared.location`.
   An app screen renders the coordinate from `CLLocationManager`. The spike records three facts:
   - whether a real iPhone and the Simulator honor the assignment;
   - whether an app that `simctl launch` started (rather than `XCUIApplication`) sees the assignment;
   - what the app sees after the test process exits.

   **Gate:** if a real device ignores the assignment, close the item as `Rejected` with evidence.
   If the Simulator alone differs, keep `simctl` there and ship the real-device path.
2. **Runner route.** `POST` and `DELETE /location` in `openapi.yaml` and `APIHandler.swift`.
   The provider methods come with them. `BajutsuRunnerTests` covers success, `unsupported`, and 400.
3. **Python driver.** `XcuitestDriver.set_location` and `clear_location`, tested on a fake
   transport.
4. **Controller, lifetime, and capabilities.** The composed `DeviceControl` with its Simulator
   fallback. The reset at lease start: `simctl location clear` on the Simulator, cold and warm, and
   `DELETE /location` on a real device.
   The `capabilities_for_run` narrowing. Tests show three results:
   - A warm runner starts the next scenario at the physical position, even after a fallback.
   - An `unsupported` reply falls back to `simctl` on the Simulator. On a real device, the reply
     fails the step. The message names the iOS 16.4 / Xcode 16.3 floor.
   - A real-device target runs `setLocation` while still skipping `push`.
5. **Documentation.** `docs/drivers.md` (capability table), `docs/ios-device-cloud.md`,
   `docs/devicefarm.md`, and `docs/architecture.md`, each with its `docs/ja/` mirror.
6. **On-device verification.** A showcase scenario sets a location and asserts the coordinate.
   It runs on a real iPhone and on the Simulator.

## Alternatives considered

| Option | Why it was not chosen |
|---|---|
| Real device alone, keeping `simctl` on the Simulator | The change is smaller, and the option stays the fallback if Unit 1 shows the Simulator differs. It leaves two code paths for one step, so a scenario could pass on one target and differ on the other. |
| Location service on the developer machine (`idevicesetlocation`, `pymobiledevice3`) | It adds a dependency and needs a developer-image or tunnel setup that changes across iOS releases. It cannot run on Device Farm, where the device cloud owns the machine. |
| Xcode test-plan Simulated Location or a Global Positioning System (GPS) Exchange Format file | The location is fixed per test run and cannot change mid-scenario. Apple's documentation also states that the setting does not reach an app that runs in its own process. |
| An app-side mock in BajutsuKit | The app must link and honor a Bajutsu entry point, so the app no longer sees the real Core Location stack. That breaks the app-agnostic principle for a capability the OS already offers. |
| A `clearLocation` step | Assigning `nil` makes the step possible, but this item keeps the scenario schema unchanged. Add the step once a scenario needs to return to the physical position within a run. |
| Appium `mobile:` command on the live WebDriver route | A different transport with its own capability set. Cover it in a separate item after this one proves the native path. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — spike: do a real iPhone and the Simulator honor `XCUIDevice.location`
- [x] Unit 2 — runner `POST`/`DELETE /location`, provider methods, OpenAPI contract, Swift tests
- [x] Unit 3 — `XcuitestDriver.set_location` / `clear_location` and tests
- [x] Unit 4 — `DeviceControl` fallback, lease-start reset, `capabilities_for_run` narrowing, tests
- [ ] Unit 5 — documentation and its `docs/ja/` mirror
- [ ] Unit 6 — on-device verification on a real iPhone and the Simulator

Log:

- 2026-11-28 — Units 2–4 landed together in [#2615](https://github.com/bajutsu-e2e/bajutsu/pull/2615). The Simulator half of the Unit 1 spike ran on an iOS 27.0
  Simulator built with Xcode 27.0. It found four results:
  - Apps launched by `XCUIApplication` or `simctl launch` both received the assigned coordinate.
  - After the test process exited, a fresh app launch still read the last assigned coordinate.
  - `XCUIDevice.location` and `simctl location set` write one device-wide value. The later write
    wins.
  - Assigning `nil` leaves no simulated location at all, as `simctl location clear` does. The value
    `simctl` set earlier does not come back.

  The real-device half has not run, so Unit 1 stays open. The lease-start reset follows the spike.
  On the Simulator, one `simctl location clear` before the app launches clears both routes. A real
  device resets through `DELETE /location`. A runner built before the `/location` route answers with
  a 404 status. The driver then fails the lease with a message to rebuild the runner.

## References

- [`XCUIDevice.location`](https://developer.apple.com/documentation/xcuiautomation/xcuidevice/location),
  [`XCUILocation`](https://developer.apple.com/documentation/xcuiautomation/xcuilocation), and
  [Simulating location in tests](https://developer.apple.com/documentation/xcode/simulating-location-in-tests).
- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), which
  removed the `simctl`-backed capabilities from a real-device run.
- [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), the precedent for
  backing a subset of device control per platform.

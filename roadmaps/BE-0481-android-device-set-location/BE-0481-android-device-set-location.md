**English** · [日本語](BE-0481-android-device-set-location-ja.md)

# BE-0481 — Set the location through BajutsuAndroid on a real Android device

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0481](BE-0481-android-device-set-location.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0481") |
| Implementing PR | [#2418](https://github.com/bajutsu-e2e/bajutsu/pull/2418) (units 2–6), [#2600](https://github.com/bajutsu-e2e/bajutsu/pull/2600) (units 1 and 7) |
| Topic | Platform support |
| Related | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0475](../BE-0475-ios-runner-set-location/BE-0475-ios-runner-set-location.md) |
<!-- /BE-METADATA -->

## Introduction

The `setLocation` step works on the Android emulator and fails on a real Android device. The adb
backend sets the location with `adb emu geo fix`, a command of the emulator console. A physical
device has no such console.

We propose a second route for a real device. BajutsuAndroid is the test-support library that the
app under test embeds. It registers mock location providers from inside the app. bajutsu first
grants the app the mock-location permission over adb. It then sends the coordinates in a
broadcast. The clipboard steps already reach the app the same way. The emulator keeps
`emu geo fix`. bajutsu picks the route by asking whether the device is an emulator.

## Motivation

The adb backend advertises the `deviceControl.setLocation` capability on every Android target. As
a result, the capability preflight lets a `setLocation` step through on a real device. The step
then fails at run time, because `adb emu` reaches the emulator console alone. Consider a team that
tests a location-dependent flow, such as a store finder or a delivery tracker. That team cannot run
the scenario on an attached phone or on a device cloud. The team must keep a second copy of the
suite for the emulator, or skip the flow on hardware.

The emulator result also misleads. The emulator passes and the real device fails. The scenario then
looks flaky on hardware, although the device merely lacks support.

The in-app route closes that gap with machinery bajutsu already has. Since Android 10, a shell
process cannot read or write the clipboard. For that reason,
[BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md) moved the clipboard
into BajutsuAndroid. bajutsu sends an ordered `am broadcast` to a receiver that runs in the app
process. Android's mock location provider has the same shape. The platform offers
`LocationManager.addTestProvider` as an Application Programming Interface (API). That API works
from an app that holds the mock-location permission. adb can grant that permission with `appops`.
As a result, a location receiver next to the clipboard receiver needs no new channel.

Once this item ships, the outcome shows on hardware. A scenario on a real Android device sets a
location and asserts the coordinates the app displays. The scenario passes. The next scenario on
the same device starts from the device's real location. The behavior table in
`docs/platform-parity.md` changes too. From then on, the row "Neither backend sets the location on
a real device" no longer lists Android.

## Detailed design

### Routing

The Android device control chooses the route per device, at the first `setLocation` of a lease.
bajutsu reads two system properties with `adb shell getprop`: `ro.boot.qemu` and `ro.kernel.qemu`.
`bajutsu/common/devices/facts.py` already keeps the same pair as `_EMULATOR_PROPS`. Unit 3 lifts
that tuple to a shared constant both readers import. A future image family that renames the
property then needs one fix, not two.
Either one equal to `1` marks an emulator. Recent emulator images set the first property, and older
images set the second. The console route also needs a local console. adb reaches that console
through an `emulator-<port>` serial alone. The device control takes `emu geo fix` when both of
these conditions hold:

- the property marks an emulator
- the serial has the `emulator-<port>` form

Every other target takes the in-app route. The device control caches the answer for the lease, so
later steps cost no extra adb call.

| Target | Route | Needs BajutsuAndroid |
|---|---|---|
| Local emulator (`emulator-<port>`) | `adb emu geo fix <lon> <lat>` (unchanged) | No |
| Real device, or an emulator reached with `adb connect` | `appops` grant, then the location broadcast | Yes |

Neither check suffices alone. The serial names an emulator by convention, so the property confirms
the device is one. Take an emulator reached with `adb connect`, such as a cloud emulator. It reports
the property, but its serial is an address and a port. adb has no console to reach for it, so such
an emulator takes the in-app route like a real device.

### BajutsuAndroid: the location receiver

`LocationReceiver` joins `ClipboardReceiver` in `BajutsuAndroid/src/main/java/dev/bajutsu/android/`.
It listens on a new action, `dev.bajutsu.LOCATION`, and handles two operations in the `op` extra:

- **`set`** reads `lat` and `lon` as strings, so the shell argument needs no quoting. For each of
  the `gps` and `network` providers, the receiver adds a test provider and enables it.
  On API 31 and later the receiver passes `ProviderProperties`. The library supports API 26 and up.
  Below API 31 the receiver calls the older overload that takes each property as an argument.
  Each provider then receives a complete `Location`, with accuracy, time, and elapsed-realtime set. A `Location`
  without those fields raises `IllegalArgumentException`.
- **`clear`** removes both test providers. A provider that is absent counts as already removed.

The receiver sets `Bajutsu.RESULT_OK` on success, like the clipboard receiver. Two error cases get
their own result codes, so bajutsu can name the cause:

| Result | Meaning | bajutsu's error message points at |
|---|---|---|
| `RESULT_OK` | The location is set or cleared | — |
| `RESULT_NO_MOCK_PERMISSION` | `addTestProvider` raised `SecurityException` | the `appops` grant |
| unset (0) | No receiver handled the broadcast | `BajutsuAndroid/README.md` |

`Bajutsu` declares `RESULT_NO_MOCK_PERMISSION` as `2`, and `_functions.py` mirrors it as a
constant. `CLIPBOARD_RESULT_OK` mirrors `RESULT_OK` the same way. `Bajutsu.startLocation` keeps its
own registration flag, separate from the clipboard's.

An app enables the receiver by calling the new `Bajutsu.startLocation(context)` function. The app
calls it from `Application.onCreate`, in a test or debug build alone. The function mirrors
`Bajutsu.startClipboard` in shape. The receiver has the exported flag, so a release build must
never register it. The library manifest stays empty for the same reason. Unit 1 may find that
`appops` needs the `ACCESS_MOCK_LOCATION` permission in the manifest. In that case, the README
tells the app to declare the permission in its debug manifest alone.

We mock the `gps` and `network` providers and leave the fused provider of Google Play services
alone. Mocking those two providers adds no Play services dependency to BajutsuAndroid. A
fused-provider client may or may not see the mocked location. The answer depends on the device and
its Play services version. Unit 1 records the observed behavior on hardware, and the README states
it.

### Host: commands and environment

This section covers `bajutsu/common/backend_cli/adb/`.

`_functions.py` gains two command builders next to the clipboard ones. The emulator probe reuses the existing `get_prop_cmd`. The two builders are:

- `mock_location_appop_cmd(serial, package, mode)` runs
  `appops set <package> android:mock_location <mode>`, where the mode is `allow` or `default`.
- `location_broadcast_cmd(serial, package, op, lat, lon)` sends the ordered broadcast. It mirrors
  `_clipboard_broadcast_cmd`.

`parse_location_result(out)` reads the result code. On an error it raises `DeviceError` with the
cause from the table above. `Env` in `env.py` gains three methods:

- `has_emulator_console()`
- `set_mock_location(package, lat, lon)`
- `clear_mock_location(package)`

`set_mock_location` grants the app-op and then broadcasts the `set` operation. The grant is
idempotent, so it runs on every call.

### Host: device control and lifecycle

This section covers `bajutsu/common/platform_lifecycle/`.

The `android_device_control` function lives in the `device_control.py` module. It asks
`Env.has_emulator_console()` which route `set_location` takes. A local emulator keeps `Env.set_location`, which runs the `emu geo fix`
command. Every other target calls `Env.set_mock_location`. The device control then records that
the lease set a mock location.

The environment owns that record. `controller()` hands the device control a callback for that
record. The `foreground` and `background` callbacks travel the same path. The new callback stores the coordinates on the
environment, keyed by package. The device control writes the record before the `appops` grant.
Teardown then reverts even a grant whose broadcast fails. `teardown` and the relaunch paths
(`relauncher` and `crawl_reset`) read the record from the environment.

A device-group member's lease ends in `end_member`, not in the `teardown` method. `end_member`
runs the same clear-then-revoke steps for its own package before its force-stop. `teardown` covers
the packages still recorded.

`android_environment.py` holds the Android environment's `teardown` method. That method clears the
mock before it force-stops the app. The order matters. The clipboard and location receivers
register at run time. A broadcast reaches a receiver while the app process lives, never after. The
teardown runs these steps for a target that set a mock location during the lease:

1. Broadcast `clear` to remove the test providers.
2. Run `appops set <package> android:mock_location default` to drop the grant.
3. Force-stop the app, as the teardown does today.

An error in step 1 or step 2 logs a warning and does not fail the run. The scenario's verdict is
already final by then. Step 2 alone stops the app from mocking afterward. On Android, `teardown`
runs at the release of each lease. The next scenario on the device then starts from the real
location.

The mock may not survive a relaunch of the app. A `relaunch` step ends the process that registered
the providers. Android may remove the providers along with it. Unit 1 settles the behavior on
hardware. Suppose the providers do not survive. The device control then re-sends the last location
after the environment's relaunch. A scenario then sees the same location the emulator would keep.

An `installApp` step loses the mock the same way. `install_member` force-stops the app before it
installs. The providers go away with the process on both `keepData` levels. An uninstall drops the
`appops` grant as well. The device control re-sends the recorded location after an `installApp`
step too. For the same reason, `install_member` already re-grants the runtime permissions
(BE-0210). It also re-pins the per-app locale (BE-0478).

### Capability and preflight

`deviceControl.setLocation` stays in the adb backend's static capability set.
`capabilities_for_run` gains no Android branch. An app that does not embed BajutsuAndroid fails the
step at run time. The step reports the "no receiver" error from the table above. The clipboard
steps behave the same way today
([BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md)). Narrowing the set
before the run would need adb calls inside the capability computation. Today that computation
depends on configuration alone.

### The showcase scenario

Both Android showcase apps, Compose and Views, call the `Bajutsu.startLocation` function. Each also
gains a small location readout. A text view in the readout shows the latest latitude and
longitude from the `LocationManager` service. A scenario sets a location and asserts the displayed
coordinates. A local emulator takes the console route, and a real device takes the in-app route.
The emulator lane alone would exercise the console route and leave the receiver untested. The lane
runs the scenario a second time over `adb connect`, against the same emulator. The routing rule
sends that connection in-app. CI then covers the receiver, the `appops` grant, and the result
codes. Coverage then does not rest on the on-device verification of Unit 7 alone. The showcase
grants the location permission with the existing permission preconditions. As a result, no system
dialog interrupts the run.

### Work breakdown (MECE)

| Unit | Work | Done when |
|---|---|---|
| 1 | Spike on a real device: `appops` with and without the manifest permission, the mock across a relaunch, removal on revoke, and fused-provider visibility | Findings recorded in this item's Progress log |
| 2 | BajutsuAndroid `LocationReceiver`, `Bajutsu.startLocation`, and result codes | `make -C demos/showcase/android build-all` builds every showcase variant against the library |
| 3 | adb command builders, `parse_location_result`, and the `Env` methods | Unit tests cover each builder's argv and each result code; `Env` and `facts.py` import one shared emulator-property constant |
| 4 | Route selection in `android_device_control` and teardown in the Android environment | Unit tests cover both routes, the cached probe, the re-send after a relaunch and an `installApp`, and the order and tolerance of `teardown` and `end_member` |
| 5 | Showcase location readout in the Compose and Views apps and the location scenario | The scenario passes on the emulator lane over both the `emulator-<port>` serial and `adb connect` |
| 6 | Documentation in both languages: `docs/platform-parity.md` and `BajutsuAndroid/README.md`, plus the in-code comments and docstrings that call Android's `setLocation` console-only (`device_control.android_device_control`, the device-control comment in `adb/env.py`, the class docstring and `controller` in `android_environment.py`, and `adb_driver.py`) | Both pages and their mirrors (`docs/ja/platform-parity.md`, `BajutsuAndroid/README.ja.md`) describe both routes, including the `deviceControl.setLocation` row of the capability table and the `setLocation` row of the behavior table; no comment or docstring still describes `setLocation` as an emulator-console operation alone |
| 7 | On-device verification | The scenario passes on a real device, and the next scenario sees the real location; the result is recorded in the PR |

## Alternatives considered

| Option | Summary | Why we did not take it |
|---|---|---|
| Always use the in-app route | One route on every target, as BE-0475 does on iOS | An emulator target without BajutsuAndroid supports `setLocation` today and would lose it |
| In-app route first, `emu geo fix` as a fallback | Try the receiver, fall back on an emulator when no receiver answers | Two error paths per call, and the route a run took would depend on the app build |
| A separate mock-location helper app | An app that bajutsu installs on each device to own the mock | Works without app changes, but adds an install step per device and a second artifact to version |
| Mock the fused provider as well | `FusedLocationProviderClient.setMockMode` reaches fused-only apps | Adds a Play services dependency to BajutsuAndroid, which does not work on a device without Play services |
| Mock the fused provider alone | The simplest receiver | Misses every app that reads the `gps` provider directly |
| Narrow the capability before the run | `capabilities_for_run` probes for a real device and a receiver | Puts adb calls into a computation that depends on configuration alone; the clipboard precedent fails at run time instead |
| Clear the mock at the end of the run, not the lease | Fewer adb calls | The outcome of a scenario would depend on which scenario ran before it on the device |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] ~~Unit 1 — spike on a real device: `appops`, relaunch, revoke, fused visibility~~
  - Closed on the emulator findings. The spike ran on an API 34 emulator reached over
    `adb connect`, and the 2026-11-11 log entry records what it showed. We had no real Android
    device to run it on, and we accept the emulator result as the spike's answer.
- [x] Unit 2 — BajutsuAndroid `LocationReceiver` and `Bajutsu.startLocation`
- [x] Unit 3 — adb command builders, result parsing, and `Env` methods
- [x] Unit 4 — route selection and lease teardown
- [x] Unit 5 — showcase location readout and scenario
- [x] Unit 6 — documentation in both languages
- [x] ~~Unit 7 — on-device verification~~
  - Closed without a run on hardware. The CI emulator lane runs the location scenario twice on
    every PR. One run goes over the `emulator-<port>` serial, and the other goes over an
    `adb connect` connection. That lane covers both routes, the `appops` grant, and the result
    codes.

Log:

- 2026-11-11 — Units 2–6 landed together ([#2418](https://github.com/bajutsu-e2e/bajutsu/pull/2418)). Unit 1 ran on an API 34 emulator reached over
  `adb connect`, not on hardware, so it stays open with Unit 7. The emulator showed five results:
  - `appops set <package> android:mock_location allow` grants the app-op with no
    `ACCESS_MOCK_LOCATION` in the manifest, and an unknown package exits non-zero.
  - Without the grant the receiver answers `2`; with it, `1`.
  - The mock providers survived a force-stop and relaunch, and `clear` removed them.
  - The emulator's own fused provider also reported the mocked location. Play services was not involved.
  - Revoking the app-op leaves the providers in place. The shell cannot remove them either. For
    the shell's uid 2000, `cmd location providers remove-test-provider` raises a
    `SecurityException` error. For that reason, a teardown launches a stopped app before it
    broadcasts the `clear` operation.

  Four deviations from the Detailed design:
  - The environment re-sends the recorded location after every launch it makes. It does not wait
    for a relaunch to drop the providers. The re-send is idempotent, so it holds on hardware
    whatever Unit 1 finds there.
  - After an `installApp` step the re-send happens at the next launch, not inside `install_member`.
    The receiver registers at run time, so it needs a live process.
  - `Env` splits the design's `clear_mock_location` into `clear_mock_location` (the broadcast) and
    `revoke_mock_location` (the `appops` reset), so a teardown revokes the grant even when the clear
    fails.
  - The receiver also has a third error code, `3`, for a mock that the platform refuses. The
    platform signals the refusal by raising `IllegalArgumentException`. A refused
    mock then neither stops the app abnormally nor reads as a missing receiver.
- 2026-11-27 — We closed Units 1 and 7 on the emulator evidence
  ([#2600](https://github.com/bajutsu-e2e/bajutsu/pull/2600)). The item is now Implemented.
  A real device would still show two behaviors the emulator could not:
  - the routing on a device that reports neither `ro.boot.qemu` nor `ro.kernel.qemu`
  - whether a fused-provider client sees the mock through Play services

## References

- GitHub issue [#2392](https://github.com/bajutsu-e2e/bajutsu/issues/2392), which reported the gap.
- [`LocationManager.addTestProvider`](https://developer.android.com/reference/android/location/LocationManager#addTestProvider(java.lang.String,%20android.location.provider.ProviderProperties))
  and [`setTestProviderLocation`](https://developer.android.com/reference/android/location/LocationManager#setTestProviderLocation(java.lang.String,%20android.location.Location)).
- [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), which added
  `setLocation` to the emulator through `emu geo fix`.
- [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), the in-app
  receiver this item follows.
- [BE-0475](../BE-0475-ios-runner-set-location/BE-0475-ios-runner-set-location.md), the same gap on
  a real iOS device.

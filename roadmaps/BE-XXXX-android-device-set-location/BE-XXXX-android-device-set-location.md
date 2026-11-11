**English** · [日本語](BE-XXXX-android-device-set-location-ja.md)

# BE-XXXX — Set the location through BajutsuAndroid on a real Android device

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-device-set-location.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0211](../BE-0211-android-device-control/BE-0211-android-device-control.md), [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md), [BE-0212](../BE-0212-granular-device-control-capabilities/BE-0212-granular-device-control-capabilities.md), [BE-0475](../BE-0475-ios-runner-set-location/BE-0475-ios-runner-set-location.md) |
<!-- /BE-METADATA -->

## Introduction

The `setLocation` step works on the Android emulator and fails on a real Android device. The adb
backend sets the location with `adb emu geo fix`, a command of the emulator console. A physical
device has no such console.

We propose a second route for a real device. BajutsuAndroid, the test-support library that the app
under test embeds, registers mock location providers from inside the app. bajutsu first grants the
app the mock-location permission over adb. It then sends the coordinates in a broadcast, the same
way the clipboard steps already reach the app. The emulator keeps `emu geo fix`. bajutsu picks the
route by asking the device whether it is an emulator.

## Motivation

The adb backend advertises the `deviceControl.setLocation` capability on every Android target. The
capability preflight therefore lets a `setLocation` step through on a real device. The step then
fails at run time, because `adb emu` reaches the emulator console alone. Consider a team that tests a
location-dependent flow, such as a store finder or a delivery tracker. That team cannot run the
scenario on an attached phone or on a device cloud. The team must keep a second, emulator-only copy
of the suite, or skip the flow on hardware.

The emulator result also misleads. The emulator passes, the real device fails, and the scenario
looks flaky on hardware when it is merely unsupported.

The in-app route closes that gap with machinery bajutsu already has. Since Android 10, a shell
process cannot read or write the clipboard, so [BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md)
moved the clipboard into BajutsuAndroid. bajutsu sends an ordered `am broadcast` to a receiver that
runs in the app process. Android's mock location provider has the same shape. The platform application programming interface (API)
`LocationManager.addTestProvider` works from an app that holds the mock-location permission. adb
can grant that permission with `appops`. A location receiver next to the clipboard receiver
therefore needs no new channel.

Once this item ships, the outcome is observable on hardware. A scenario on a real Android device
sets a location, then asserts the coordinates the app displays, and passes. The next scenario on the
same device starts from the device's real location. The behavior table in `docs/platform-parity.md`
no longer lists Android under "Neither backend sets the location on a real device".

## Detailed design

### Routing

The Android device control chooses the route per device, at the first `setLocation` of a lease.
bajutsu reads two system properties with `adb shell getprop`: `ro.boot.qemu` and `ro.kernel.qemu`.
`bajutsu/common/devices/facts.py` already keeps the same pair as `_EMULATOR_PROPS`. Unit 3 lifts
that tuple to a shared constant both readers import. A future image family that renames the
property then needs one fix, not two.
Either one equal to `1` marks an emulator. Recent emulator images set the first property, and older
images set the second. The console route also needs a local console. adb reaches that console
through an `emulator-<port>` serial alone. The device control therefore takes `emu geo fix` when
the property marks an emulator and the serial has the `emulator-<port>` form. Every other target
takes the in-app route. The device control caches the answer for the lease, so later steps cost no
extra adb call.

| Target | Route | Needs BajutsuAndroid |
|---|---|---|
| Local emulator (`emulator-<port>`) | `adb emu geo fix <lon> <lat>` (unchanged) | No |
| Real device, or an emulator reached with `adb connect` | `appops` grant, then the location broadcast | Yes |

Neither check suffices alone. The serial names an emulator by convention, so the property confirms
the device is one. An emulator reached with `adb connect` (a cloud emulator, for example) reports
the property, but carries a host and port as its serial. adb has no console to reach for it, so
such an emulator takes the in-app route like a real device.

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

The receiver sets `Bajutsu.RESULT_OK` on success, like the clipboard receiver. Two failures get
their own result codes, so bajutsu can name the cause:

| Result | Meaning | bajutsu's error message points at |
|---|---|---|
| `RESULT_OK` | The location is set or cleared | — |
| `RESULT_NO_MOCK_PERMISSION` | `addTestProvider` raised `SecurityException` | the `appops` grant |
| unset (0) | No receiver handled the broadcast | `BajutsuAndroid/README.md` |

`RESULT_NO_MOCK_PERMISSION` is `2`. `Bajutsu` declares it, and `_functions.py` mirrors it as a constant, the same way `CLIPBOARD_RESULT_OK` mirrors `RESULT_OK`. `Bajutsu.startLocation` keeps its own registration flag, separate from the clipboard's.

An app enables the receiver with a new `Bajutsu.startLocation(context)`. The app calls it from
`Application.onCreate`, in a test or debug build alone. The function mirrors `Bajutsu.startClipboard`. The receiver is
exported, so a release build must never register it. The library manifest stays empty for the same
reason. If Unit 1 finds that `appops` needs the `ACCESS_MOCK_LOCATION` permission in the manifest,
the README tells the app to declare the permission in its debug manifest alone.

We mock the `gps` and `network` providers and leave the fused provider of Google Play services
alone. Mocking those two providers adds no Play services dependency to BajutsuAndroid. Whether a
fused-provider client on a given device sees the mocked location depends on the device and its Play
services version. Unit 1 records the observed behavior on hardware; the README states it.

### Host: commands and environment

This section covers `bajutsu/common/backend_cli/adb/`.

`_functions.py` gains two command builders next to the clipboard ones. The emulator probe reuses the existing `get_prop_cmd`. The two builders are:

- `mock_location_appop_cmd(serial, package, mode)` runs
  `appops set <package> android:mock_location <mode>`, where the mode is `allow` or `default`.
- `location_broadcast_cmd(serial, package, op, lat, lon)` sends the ordered broadcast. It mirrors
  `_clipboard_broadcast_cmd`.

`parse_location_result(out)` reads the result code and raises `DeviceError` with the cause from the
table above. `Env` in `env.py` gains `has_emulator_console()`, `set_mock_location(package, lat, lon)`, and
`clear_mock_location(package)`. `set_mock_location` grants the app-op and then broadcasts `set`; the
grant is idempotent, so it runs on every call.

### Host: device control and lifecycle

This section covers `bajutsu/common/platform_lifecycle/`.

`android_device_control` in `device_control.py` routes `set_location` through `Env.has_emulator_console()`.
A local emulator keeps `Env.set_location`, which runs `emu geo fix`. Every other target calls
`Env.set_mock_location`, and the device control records that the lease set a mock location.

The environment owns that record. `controller()` passes `android_device_control` a callback, in the
same way as `foreground` and `background`. The callback stores the coordinates on the environment,
keyed by package. The device control writes the record before the `appops` grant, so a grant whose
broadcast then fails is still reverted at teardown. `teardown` and the relaunch paths (`relauncher`
and `crawl_reset`) read the record from the environment.

A device-group member's lease ends in `end_member`, not in `teardown`. `end_member` therefore runs
the same clear-then-revoke steps for its own package before its force-stop. `teardown` covers the
packages still recorded.

The Android environment's `teardown` in `android_environment.py` clears the mock before it
force-stops the app. The order matters. The clipboard and location receivers register at run time,
so a broadcast reaches a receiver only while the app process lives. The teardown runs these steps,
which apply to a target that set a mock location during the lease:

1. Broadcast `clear` to remove the test providers.
2. Run `appops set <package> android:mock_location default` to drop the grant.
3. Force-stop the app, as the teardown does today.

A failure in step 1 or step 2 logs a warning and does not fail the run. The scenario's verdict is
already decided by then, and step 2 alone stops the app from mocking afterward. On Android,
`teardown` runs at the release of each lease, so the next scenario on the device starts from the
real location.

The mock may not survive a relaunch of the app. A `relaunch` step kills the process that registered
the providers, and Android may remove the providers along with it. Unit 1 settles the behavior on
hardware. If the providers do not survive, the device control re-sends the last location after the
environment's relaunch, so a scenario sees the same location the emulator would keep.

An `installApp` step loses the mock the same way. `install_member` force-stops the app before it
installs. The providers go away with the process on both `keepData` levels, and an uninstall drops
the `appops` grant as well. The device control re-sends the recorded location after an
`installApp` too. `install_member` already re-grants the runtime permissions (BE-0210) and re-pins
the per-app locale (BE-0478) for the same reason.

### Capability and preflight

`deviceControl.setLocation` stays in the adb backend's static capability set, and
`capabilities_for_run` gains no Android branch. An app that does not embed BajutsuAndroid fails the
step at run time, with the "no receiver" error from the table above. The clipboard steps behave the
same way today ([BE-0233](../BE-0233-adb-clipboard-fidelity/BE-0233-adb-clipboard-fidelity.md)).
Narrowing the set before the run would need adb calls inside the capability computation, which
today depends on configuration alone.

### The showcase scenario

Both Android showcase apps, Compose and Views, call `Bajutsu.startLocation` and gain a small location readout: a text view that shows the latest
latitude and longitude from `LocationManager`. A scenario sets a location and asserts the displayed
coordinates. A local emulator takes the console route, and a real device takes the in-app route.
The emulator lane alone would exercise the console route and leave the receiver untested. The lane
runs the scenario a second time, against the same emulator reached over `adb connect`. The routing
rule sends that connection in-app. CI then covers the receiver, the `appops`
grant, and the result codes, not merely the on-device verification of Unit 7. The showcase grants the location permission with the existing permission
preconditions, so no system dialog interrupts the run.

### Work breakdown (MECE)

| Unit | Work | Done when |
|---|---|---|
| 1 | Spike on a real device: `appops` with and without the manifest permission, the mock across a relaunch, removal on revoke, and fused-provider visibility | Findings recorded in this item's Progress log |
| 2 | BajutsuAndroid `LocationReceiver`, `Bajutsu.startLocation`, and result codes | `make -C demos/showcase/android build-all` builds every showcase variant against the library |
| 3 | adb command builders, `parse_location_result`, and the `Env` methods | Unit tests cover each builder's argv and each result code; `Env` and `facts.py` import one shared emulator-property constant |
| 4 | Route selection in `android_device_control` and teardown in the Android environment | Unit tests cover both routes, the cached probe, the re-send after a relaunch and an `installApp`, and the order and tolerance of `teardown` and `end_member` |
| 5 | Showcase location readout in the Compose and Views apps and the location scenario | The scenario passes on the emulator lane over both the `emulator-<port>` serial and `adb connect` |
| 6 | Documentation in English and Japanese: `docs/platform-parity.md` and `BajutsuAndroid/README.md` | Both pages and their Japanese mirrors (`docs/ja/platform-parity.md`, `BajutsuAndroid/README.ja.md`) describe both routes, including the `deviceControl.setLocation` row of the capability table and the `setLocation` row of the behavior table |
| 7 | On-device verification | The scenario passes on a real device, and the next scenario sees the real location; the result is recorded in the PR |

## Alternatives considered

| Option | Summary | Why we did not take it |
|---|---|---|
| Always use the in-app route | One route on every target, as BE-0475 does on iOS | An emulator target without BajutsuAndroid supports `setLocation` today and would lose it |
| In-app route first, `emu geo fix` as a fallback | Try the receiver, fall back on an emulator when no receiver answers | Two failure paths per call, and the route a run took would depend on the app build |
| A separate mock-location helper app | An app that bajutsu installs on each device to own the mock | Works without app changes, but adds an install step per device and a second artifact to version |
| Mock the fused provider as well | `FusedLocationProviderClient.setMockMode` reaches fused-only apps | Adds a Play services dependency to BajutsuAndroid, which does not work on a device without Play services |
| Mock the fused provider alone | The simplest receiver | Misses every app that reads the `gps` provider directly |
| Narrow the capability before the run | `capabilities_for_run` probes for a real device and a receiver | Puts adb calls into a computation that depends on configuration alone; the clipboard precedent fails at run time instead |
| Clear the mock at the end of the run, not the lease | Fewer adb calls | The outcome of a scenario would depend on which scenario ran before it on the device |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — spike on a real device: `appops`, relaunch, revoke, fused visibility
- [ ] Unit 2 — BajutsuAndroid `LocationReceiver` and `Bajutsu.startLocation`
- [ ] Unit 3 — adb command builders, result parsing, and `Env` methods
- [ ] Unit 4 — route selection and lease teardown
- [ ] Unit 5 — showcase location readout and scenario
- [ ] Unit 6 — documentation in English and Japanese
- [ ] Unit 7 — on-device verification

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

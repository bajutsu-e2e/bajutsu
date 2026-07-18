**English** · [日本語](BE-0238-ios-device-cloud-execution-ja.md)

# BE-0238 — iOS device-cloud execution

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0238](BE-0238-ios-device-cloud-execution.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0238") |
| Implementing PR | [#1192](https://github.com/bajutsu-e2e/bajutsu/pull/1192) (Unit 1: XCUITest real-device targeting); [#1193](https://github.com/bajutsu-e2e/bajutsu/pull/1193) (Unit 2: batch packaging) |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

Device clouds run iOS on **real devices**. Bajutsu's current iOS actuators cannot reach them:
`simctl` targets the Simulator only (no real device exists in a real-device cloud), and `idb` needs
a Mac-resident `idb_companion` daemon that the managed macOS hosts do not expose. What the clouds
*do* speak on iOS is **XCTest** and, on AWS Device Farm, **Appium's XCUITest driver**. This item
adds an iOS execution path that produces those artifacts, built on the already-shipped **XCUITest
backend (BE-0019)** — unlocking iOS real-device automation both in the cloud and, as a side effect,
locally.

## Motivation

iOS is the one platform where the cloud story needs real new code, because the existing iOS
actuators are structurally cloud-incompatible — not a config gap. Confirming the constraint so it is
not misdiagnosed later:

- **`simctl`** drives the Simulator; a real-device cloud has no simulator to target.
- **`idb`** requires `idb_companion` running on the Mac host; managed cloud macOS hosts provide a
  limited shell and do not host arbitrary daemons.

Both AWS Device Farm and Firebase run iOS tests on physical devices via XCTest (Device Farm also via
Appium's XCUITest driver); neither documents `idb`/`simctl`. So the path forward is to speak XCTest /
Appium-XCUITest, reusing the **BE-0019 XCUITest backend** as the driving layer rather than inventing
a new one. This also has standalone value: it is the same work that lets Bajutsu drive a **local
iOS real device** (via `xcodebuild` against a device target), which today is effectively
Simulator-only.

This is the heaviest of the device-cloud items and is intentionally independent: the Android
providers reuse the adb backend almost verbatim, whereas iOS needs a real-device-capable driving
path and cloud packaging. It is sequenced after the foundation seam so the *live* iOS route (e.g. an
Appium endpoint provider) can reuse *device-cloud-provider-abstraction*, while the *batch* route
(Device Farm XCTest/Appium package) reuses the *aws-device-farm-submitter* packaging.

## Detailed design

Two routes, sharing the XCUITest driving layer:

- **Batch (AWS Device Farm).** Package the app plus an XCTest/Appium-XCUITest bundle that carries the
  Bajutsu scenario execution, submit via the *aws-device-farm-submitter* machinery, and collect
  artifacts. Must account for Device Farm's iOS constraints: apps are **re-signed** for the device
  (stripping some entitlements such as App Groups / Push), device builds of the `.ipa` are required
  (no simulator build), and XCTest is *not* customisable in the same way the Appium path is.
- **Live (remote device, later).** Where a cloud exposes an Appium/WebDriver endpoint for a reserved
  iOS device, model it as a `DeviceProvider` on the live seam (yielding an endpoint instead of an adb
  serial), and drive it through an Appium-XCUITest path. This reuses the foundation seam directly.

The unifying work is making the **XCUITest backend (BE-0019) real-device-capable**: today it targets
the Simulator through `xcodebuild`; this item generalises target selection to a real device
(local first, then cloud-packaged), which is the reusable core of both routes.

### Work breakdown (MECE)

1. **XCUITest real-device targeting** — generalise the BE-0019 backend to drive a real iOS device
   (local `xcodebuild` device target), the shared foundation for both routes.
2. **Batch packaging (Device Farm)** — build the XCTest/Appium-XCUITest package carrying scenario
   execution; integrate with the *aws-device-farm-submitter* upload/collect flow.
3. **Re-signing / entitlement handling** — document and handle the Device Farm re-sign (which
   entitlements drop, and how scenarios that depend on them degrade or are skipped via preflight).
4. **Live route (Appium endpoint provider)** — a `DeviceProvider` yielding an Appium endpoint for a
   reserved iOS device, driven through the XCUITest/Appium path (may land as a follow-on slice).
5. **Tests** — real-device targeting resolution and packaging assembly, faked at the
   `xcodebuild`/toolchain boundary; no live cloud in the gate.
6. **Docs** — an iOS device-cloud how-to (both languages): why `idb`/`simctl` do not apply, the
   XCTest/Appium routes, and the re-signing caveats.

### Prime-directive compliance

- **AI out of the gate.** Real-device iOS execution is deterministic XCTest/XCUITest; no model on the
  verdict path.
- **Determinism first.** No fixed sleeps introduced; readiness stays condition-based as in the
  existing backends.
- **App-agnostic.** iOS cloud specifics live in target config and (for batch) the CI-side submitter;
  the runner and scenario format are unchanged.

## Alternatives considered

- **Port `idb`/`simctl` to the cloud.** Not possible: no simulator on a real-device cloud, and no
  daemon hosting on managed macOS hosts. Rejected as structurally infeasible; hence the XCTest/Appium
  route.
- **A brand-new iOS cloud backend from scratch.** Duplicates the BE-0019 XCUITest backend. Rejected —
  generalise BE-0019 to real devices instead.
- **Do iOS at the same time as Android.** Android reuses the adb backend almost verbatim; iOS needs
  real new driving/packaging code. Kept separate so the Android path can ship first without waiting
  on the heavier iOS work.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] XCUITest real-device targeting (generalise BE-0019 beyond the Simulator)
- [x] Batch packaging for Device Farm (XCTest / Appium-XCUITest)
- [ ] Re-signing / entitlement handling (document + preflight degradation)
- [ ] Live route: Appium-endpoint `DeviceProvider` (follow-on slice)
- [ ] Tests (faked `xcodebuild`/toolchain boundary) — real-device targeting covered by Unit 1
- [ ] Docs (iOS device-cloud how-to; idb/simctl rationale; re-sign caveats)

**Log.**

- Unit 1 ([#1192](https://github.com/bajutsu-e2e/bajutsu/pull/1192)): added `xcuitest.deviceType` (`simulator` default / `device`) and generalised
  the XCUITest environment's `-destination` to `platform=iOS` for a real device, sharing the same
  `xcodebuild test-without-building` driving layer. A real device skips simctl device-prep; the
  simctl-only preconditions it cannot honour (erase / `appPath` install / permission grants) now
  fail loudly, deferred to Units 2–3. Faked at the `xcodebuild`/toolchain boundary; no Simulator on
  the gate.
- Unit 2 ([#1193](https://github.com/bajutsu-e2e/bajutsu/pull/1193)): generalised the *aws-device-farm-submitter* (`scripts/devicefarm_submit.py`)
  from Android-only to also emit an iOS submission. A `platform` selects the app upload type
  (`ANDROID_APP` / `IOS_APP`) and the per-platform run — iOS drives `bajutsu run --backend xcuitest`
  against the reserved device's `$DEVICEFARM_DEVICE_UDID` (reusing Unit 1's real-device targeting),
  where Android keeps `--backend adb --udid booted`. The Appium-Python custom-environment test
  package/spec types are unchanged. Tests fake only the AWS SDK seam. The showcase iOS Device Farm
  config and the CI workflow job wait on Unit 3 (re-signing), since a device `.ipa` cannot be built
  unsigned.

## References

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [AWS Device Farm — iOS custom test environment hosts](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments-hosts-ios.html)
- [AWS Device Farm — Appium test types](https://docs.aws.amazon.com/devicefarm/latest/developerguide/test-types-appium.html)
- [Firebase Test Lab — iOS (XCTest)](https://firebase.google.com/docs/test-lab/ios/get-started)
- Depends on sibling items: **device-cloud-provider-abstraction** (live seam), **aws-device-farm-submitter** (batch packaging)

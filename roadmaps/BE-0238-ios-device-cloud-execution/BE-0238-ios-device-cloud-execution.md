**English** · [日本語](BE-0238-ios-device-cloud-execution-ja.md)

# BE-0238 — iOS device-cloud execution

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0238](BE-0238-ios-device-cloud-execution.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0238") |
| Implementing PR | [#1192](https://github.com/bajutsu-e2e/bajutsu/pull/1192) (Unit 1: XCUITest real-device targeting), [#1193](https://github.com/bajutsu-e2e/bajutsu/pull/1193) (Unit 2: batch packaging), [#1195](https://github.com/bajutsu-e2e/bajutsu/pull/1195) (Unit 3: re-signing / real-device capability preflight), [#1196](https://github.com/bajutsu-e2e/bajutsu/pull/1196) (Unit 4: live-route Appium-endpoint provider, seam only), [#1197](https://github.com/bajutsu-e2e/bajutsu/pull/1197) (Unit 5: faked-boundary tests), [#1198](https://github.com/bajutsu-e2e/bajutsu/pull/1198) (Unit 6: iOS device-cloud how-to) |
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

### Live route transport (follow-on slices)

Unit 4 landed the `appium` `DeviceProvider` — the live seam — which hands a run the fixed Appium /
WebDriver endpoint of a reserved iOS device as its udid spec. Driving that endpoint is a separate
transport, still unbuilt, and it cannot merely layer a WebDriver client onto today's path: the udid
spec flows unchanged into `XcuitestEnvironment`, whose `_destination()` runs it through
`simctl.validated_udid`, and the shared `device_id` charset excludes the `/` in a URL — so a real
`http(s)://` endpoint raises `DeviceError: invalid udid` today. The design below closes that gap.

**Speak W3C WebDriver from Python, not the runner channel.** The local XCUITest path drives a
resident BajutsuKit runner over a bespoke loopback HTTP channel; an Appium grid exposes no such
runner, only a World Wide Web Consortium (W3C) WebDriver endpoint (Appium's XCUITest driver). So the
live transport speaks W3C WebDriver to the remote endpoint directly from Python, rather than
tunnelling the runner channel to a port the grid does not serve.

**Keep element resolution on the Python side.** WebDriver resolves a locator on the server, but
determinism first (prime directive 2) requires Bajutsu to resolve a selector Python-side so an
ambiguous selector fails immediately rather than acting on whichever element matched first. The live
driver therefore reuses the shape of the existing `XcuitestDriver`: query the whole screen with one
broad locator (`findElements` over `//*` or an iOS class chain), build the `base.Element` list,
resolve the selector against that list with `resolve_unique`, and act on the chosen element by the
WebDriver element id the query returned — the same query-resolve-act-by-handle flow the runner
channel already follows, with the WebDriver element id standing in for the runner's per-snapshot
handle.

**Reuse the injectable-transport seam, with a minimal in-house client.** `XcuitestDriver` already
takes its wire protocol as an injectable transport — `(method, path, body)` in, a decoded reply out —
so its request and response logic runs against a fake with no Simulator on the gate. The live driver
takes a minimal in-house W3C WebDriver client built on the standard library's `http.client`, injected
the same way. The in-house client matches the runner channel's own stdlib client, keeps the gate free
of a third-party WebDriver dependency, and lets the gate exercise the WebDriver mapping against a fake
endpoint at the network boundary.

**Route the endpoint around the simctl and xcodebuild machinery.** `environment_for` selects the
environment for a run; when the xcuitest actuator's udid spec is an `http(s)://` endpoint, the run
takes a live path that skips simctl bring-up and the `xcodebuild test-without-building` subprocess
entirely, opening a WebDriver session against the endpoint, driving it through the live driver, and
closing the session on teardown. The URL scheme is the routing signal: it is exactly the value
`validated_udid` rejects, so recognising the scheme up front both selects the live path and replaces
the misleading `invalid udid` error. The endpoint therefore never reaches the udid machinery, which
structurally cannot carry a URL. The BE-0236 `ProvisionProfile` already reports the reserved device
booted with its build installed, so the live path honours no simctl bring-up flags — the real-device
path (Unit 1) already skips them.

The follow-on lands in three slices, each faked at the WebDriver boundary:

- **Slice A — session and a minimal driver.** The in-house W3C client (create and delete session),
  the live driver's `query`, `tap`, `screenshot`, and readiness wait, and the `environment_for`
  routing that opens the session and skips `xcodebuild`. This slice makes a live run that taps and
  asserts end to end.
- **Slice B — input and gestures.** Map `swipe`, `scroll`, `type_text`, `delete_text`, `select_all`,
  `copy_selection`, `double_tap`, `long_press`, `tap_point`, `pinch`, and `rotate` onto WebDriver
  actions — the W3C Actions application programming interface (API) or Appium's `mobile:` commands.
  `double_tap` and `long_press` share the same `/tap` runner endpoint as plain `tap` today (via
  extra params), so their WebDriver mapping is a similar-sized piece of work to the other gestures;
  `tap_point` (the handle-less coordinate tap used for system alerts) maps to the same Actions path.
- **Slice C — capabilities, config, and docs.** Narrow the live route's run-time capability set to
  what Appium-XCUITest reaches (through `capabilities_for_run`, the Unit 3 mechanism), add a showcase
  target for a live grid, and extend the how-to with the live route.

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
- [x] Batch packaging for Device Farm (submitter integration)
- [x] Re-signing / entitlement handling (document + preflight degradation)
- [ ] Live route: Appium-endpoint `DeviceProvider` (follow-on slice)
- [x] Tests (faked `xcodebuild`/toolchain boundary) — real-device targeting covered by Unit 1
- [x] Docs (iOS device-cloud how-to; idb/simctl rationale; re-sign caveats)

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
  whereas Android keeps `--backend adb --udid booted`. The Appium-Python custom-environment test
  package/spec types are unchanged: rather than build a separate XCTest/Appium-XCUITest bundle, Unit 2
  deliberately reuses the existing Appium-Python custom-environment package — which already carries the
  Bajutsu scenario run — and only threads the iOS run through it, so `build_package` is untouched and
  the change is confined to `render_test_spec` and the CLI plumbing (backend, `--udid`, upload type).
  Tests fake only the AWS SDK seam. The showcase iOS Device Farm config and the CI workflow job wait on
  Unit 3 (re-signing), since a device `.ipa` cannot be built unsigned.
- Unit 3 ([#1195](https://github.com/bajutsu-e2e/bajutsu/pull/1195)): taught the preflight that a real iOS device
  (`xcuitest.deviceType: device`) loses the simctl-backed capabilities. A new
  `backends.capabilities_for_run(actuator, eff)` narrows the static XCUITest set — dropping the whole
  `DeviceControl` family and the simctl-privacy permission grants, which reach only the Simulator — so a
  scenario that uses device control or a permission grant is skipped up front (BE-0082) with a clear
  reason instead of failing late with a `simctl` error, the preflight counterpart of Unit 1's loud
  runtime fail. Documented the Device Farm re-sign in `docs/devicefarm.md` (both languages): it strips
  entitlements (Push / App Groups), so an app feature depending on one behaves as re-signed, and the
  simctl-backed steps are skipped on a real device. The showcase iOS Device Farm config and the CI
  workflow job still await device-signing infrastructure (an unsigned device `.ipa` cannot be built),
  so they remain a follow-on.
- Unit 4 ([#1196](https://github.com/bajutsu-e2e/bajutsu/pull/1196)): landed the live-route `DeviceProvider` (seam only), on the
  BE-0236 provider seam. A new built-in `appium` provider (`deviceProvider.kind: appium`) hands a run
  the fixed Appium / WebDriver `endpoint` of a reserved iOS device (a self-hosted grid) as its udid
  spec, reporting the device booted with its build installed — a live remote device Bajutsu never
  boots or installs through simctl — and nothing to release (the reservation is the grid's). A missing
  `endpoint` fails closed at resolution, mirroring the unknown-`kind` guard. This is the seam only:
  driving the endpoint over the Appium / WebDriver protocol is a follow-on transport (the XCUITest
  backend today speaks a bespoke runner channel, not W3C WebDriver), so the box stays unchecked and
  the live route is not yet end-to-end runnable. That transport cannot merely layer a WebDriver client
  on today's path: the udid spec flows unchanged into `XcuitestEnvironment`, whose `_destination()`
  runs it through `simctl.validated_udid`, and the shared `device_id` charset excludes the `/` in a URL
  — so a real `http(s)://` endpoint raises `DeviceError: invalid udid` today. The follow-on slice must
  route this value around the simctl / xcodebuild udid machinery entirely, which structurally cannot
  carry a URL. The Android environment's `ProvisionProfile` wiring
  was deliberately not replicated for XCUITest: the real-device path (Unit 1) already skips all simctl
  bring-up, so honoring the flags there would be unreachable. Off the verdict path; faked, no device.
- Unit 5 ([#1197](https://github.com/bajutsu-e2e/bajutsu/pull/1197)): filled the remaining test gaps left after Units 1–4, all
  pure and on the gate (no Simulator, no cloud). Pinned the Unit 4 live-route boundary as an
  executable fact: the Appium / WebDriver endpoint the `appium` provider yields is exactly the udid
  spec that flows into `_destination`, so a real `http(s)://` endpoint is rejected by the shared
  `device_id` policy (`invalid udid`) today — documenting that the live route is not yet end-to-end
  runnable, and set to break visibly when the follow-on transport routes the endpoint around the
  simctl / xcodebuild udid machinery. Added direct unit tests for the `xcuitest_targets_real_device`
  accessor backing the Unit 3 capability narrowing (device / simulator / omitted-block / non-iOS),
  which had been exercised only indirectly, and covered the `appium` provider's empty-endpoint branch
  (falsy but not `None`), distinct from the missing-endpoint case.
- Unit 6 ([#1198](https://github.com/bajutsu-e2e/bajutsu/pull/1198)): added the iOS device-cloud how-to
  (`docs/ios-device-cloud.md` + the `docs/ja/` mirror), the *Docs* unit of the breakdown. The page
  explains why `idb`/`simctl` are structurally cloud-incompatible on a real device (Simulator-only /
  daemon-hosting), the reusable `xcuitest.deviceType: device` core that generalises the BE-0019
  backend's `-destination` to a real device (a locally attached one included), the batch route
  (cross-linked to `docs/devicefarm.md` rather than duplicating its submitter machinery) and the
  seam-only live route (the `appium` endpoint provider and why the endpoint is not yet end-to-end
  runnable), and the real-device caveats (re-signing strips entitlements; the simctl-backed device
  control and permission grants degrade via the preflight). Added to the mkdocs nav; documentation
  only, no product-code change. The live-route transport remains the last open box.
- Live route Slice A (_pending_): landed the first live-transport slice — the endpoint the `appium`
  provider yields is now drivable end to end for a tap-and-assert run. A new in-house W3C WebDriver
  client (`drivers/xcuitest_live.py`, `http.client`-based, its wire injected the same way
  `XcuitestDriver` injects its transport, so no third-party WebDriver dependency reaches the gate) and
  an `XcuitestLiveDriver` that reuses the query-resolve-act-by-handle shape: one broad `findElements`
  builds the `base.Element` list, `resolve_unique` resolves the selector Python-side (an ambiguous
  selector still fails before any actuation — determinism first), and the tap acts by the WebDriver
  element id the query returned, standing in for the runner's per-snapshot handle. `environment_for`
  now recognises an `http(s)://` udid spec (the routing signal — exactly the value the shared
  `device_id` policy rejects) and returns a new `XcuitestLiveEnvironment`, which opens a WebDriver
  session against the endpoint and skips simctl / `xcodebuild` entirely; the simctl-backed seam
  methods (`resolve_device`, device catalog, `DeviceControl` controller, relauncher) are overridden to
  the live route's shape, since each would otherwise build a `simctl.Env(endpoint)` that rejects the
  URL. This closes the Unit 4/5 boundary: a real endpoint no longer reaches `_destination`, so the
  boundary test is updated to assert the new routing. Slice A covers `query` / `tap` / `screenshot` /
  readiness; input and gestures (Slice B) and the run-time capability narrowing / config / docs
  (Slice C) remain, so the live-route box stays unchecked. Faked at the WebDriver network boundary; no
  grid, no device on the gate.

## References

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [AWS Device Farm — iOS custom test environment hosts](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments-hosts-ios.html)
- [AWS Device Farm — Appium test types](https://docs.aws.amazon.com/devicefarm/latest/developerguide/test-types-appium.html)
- [Firebase Test Lab — iOS (XCTest)](https://firebase.google.com/docs/test-lab/ios/get-started)
- Depends on sibling items: **device-cloud-provider-abstraction** (live seam), **aws-device-farm-submitter** (batch packaging)

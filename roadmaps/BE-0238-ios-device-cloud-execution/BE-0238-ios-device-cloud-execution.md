**English** · [日本語](BE-0238-ios-device-cloud-execution-ja.md)

# BE-0238 — iOS device-cloud execution

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0238](BE-0238-ios-device-cloud-execution.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0238") |
| Implementing PR | [#1192](https://github.com/bajutsu-e2e/bajutsu/pull/1192) (Unit 1: XCUITest real-device targeting), [#1193](https://github.com/bajutsu-e2e/bajutsu/pull/1193) (Unit 2: batch packaging), [#1195](https://github.com/bajutsu-e2e/bajutsu/pull/1195) (Unit 3: re-signing / real-device capability preflight), [#1196](https://github.com/bajutsu-e2e/bajutsu/pull/1196) (Unit 4: live-route Appium-endpoint provider, seam only), [#1197](https://github.com/bajutsu-e2e/bajutsu/pull/1197) (Unit 5: faked-boundary tests), [#1198](https://github.com/bajutsu-e2e/bajutsu/pull/1198) (Unit 6: iOS device-cloud how-to), [#1201](https://github.com/bajutsu-e2e/bajutsu/pull/1201) (Live route Slice A: WebDriver transport), [#1203](https://github.com/bajutsu-e2e/bajutsu/pull/1203) (Live route Slice B: input and gestures), [#1205](https://github.com/bajutsu-e2e/bajutsu/pull/1205) (Live route Slice C: capability narrowing, showcase target, how-to) |
| Topic | Device-cloud execution |
<!-- /BE-METADATA -->

## Introduction

Device clouds run iOS on **real devices**. Bajutsu's current iOS actuators cannot reach those
devices.

- `simctl` targets the Simulator only, and a real-device cloud has no Simulator.
- `idb` needs a Mac-resident `idb_companion` daemon. The managed macOS hosts do not expose one.

On iOS, the clouds *do* speak **XCTest**. AWS Device Farm also speaks **Appium's XCUITest driver**.

This item adds an iOS execution path that produces those artifacts. The path builds on the
already-shipped **XCUITest backend (BE-0019)**. It unlocks iOS real-device automation in the cloud
and, as a side effect, locally.

## Motivation

iOS is the one platform where the cloud story needs real new code. The existing iOS actuators are
structurally cloud-incompatible, and a config change cannot close the gap. The constraint is
confirmed here, so that nobody misdiagnoses it later:

- **`simctl`** drives the Simulator. A real-device cloud has no Simulator to target.
- **`idb`** requires `idb_companion` running on the Mac host. Managed cloud macOS hosts provide a
  limited shell and do not host arbitrary daemons.

AWS Device Farm and Firebase both run iOS tests on physical devices via XCTest. Device Farm also
supports Appium's XCUITest driver. Neither documents `idb` or `simctl`.

The path forward is therefore to speak XCTest and Appium-XCUITest. The **BE-0019 XCUITest backend**
serves as the driving layer, so no new driving layer is needed.

The work also has standalone value. It is the same work that lets Bajutsu drive a **local iOS real
device** via `xcodebuild` against a device target. Today that path is effectively Simulator-only.

This is the heaviest of the device-cloud items, and it is intentionally independent. The Android
providers reuse the adb backend almost verbatim. iOS needs a real-device-capable driving path and
cloud packaging.

The item is sequenced after the foundation seam, for two reasons:

- The *live* iOS route (e.g. an Appium endpoint provider) can reuse
  *device-cloud-provider-abstraction*.
- The *batch* route (a Device Farm XCTest/Appium package) can reuse the *aws-device-farm-submitter*
  packaging.

## Detailed design

Two routes share the XCUITest driving layer:

- **Batch (AWS Device Farm).** Package the app plus an XCTest/Appium-XCUITest bundle that carries the
  Bajutsu scenario execution. Submit it via the *aws-device-farm-submitter* machinery, and collect
  artifacts. The route must account for Device Farm's iOS constraints:
  - Apps are **re-signed** for the device. The re-sign strips some entitlements, such as App Groups
    and Push.
  - Device builds of the `.ipa` are required (no simulator build).
  - XCTest is *not* customisable in the same way as the Appium path.
- **Live (remote device, later).** A cloud may expose an Appium/WebDriver endpoint for a reserved iOS
  device. In that case, model the cloud as a `DeviceProvider` on the live seam, which yields an
  endpoint instead of an adb serial. Drive the endpoint through an Appium-XCUITest path. This route
  reuses the foundation seam directly.

The unifying work makes the **XCUITest backend (BE-0019) real-device-capable**. Today the backend
targets the Simulator through `xcodebuild`. This item generalises target selection to a real device,
local first and then cloud-packaged. That generalisation is the reusable core of both routes.

### Work breakdown (MECE)

1. **XCUITest real-device targeting.** Generalise the BE-0019 backend to drive a real iOS device
   (a local `xcodebuild` device target). It is the shared foundation for both routes.
2. **Batch packaging (Device Farm).** Build the XCTest/Appium-XCUITest package that carries scenario
   execution. Integrate it with the *aws-device-farm-submitter* upload/collect flow.
3. **Re-signing / entitlement handling.** Document and handle the Device Farm re-sign: which
   entitlements drop, and how scenarios that depend on them degrade or get skipped via preflight.
4. **Live route (Appium endpoint provider).** Add a `DeviceProvider` that yields an Appium endpoint
   for a reserved iOS device. Drive it through the XCUITest/Appium path. It may land as a follow-on
   slice.
5. **Tests.** Cover real-device targeting resolution and packaging assembly. Fake the
   `xcodebuild`/toolchain boundary. No live cloud enters the gate.
6. **Docs.** Add an iOS device-cloud how-to (both languages). It explains why `idb`/`simctl` do not
   apply, the XCTest/Appium routes, and the re-signing caveats.

### Live route transport (follow-on slices)

Unit 4 landed the `appium` `DeviceProvider`, which is the live seam. It hands a run the fixed Appium /
WebDriver endpoint of a reserved iOS device as its udid spec. Driving that endpoint needs a separate
transport, which is still unbuilt.

The transport cannot merely layer a WebDriver client onto today's path. The udid spec flows unchanged
into `XcuitestEnvironment`, whose `_destination()` runs it through `simctl.validated_udid`. The shared
`device_id` charset excludes the `/` in a URL. A real `http(s)://` endpoint therefore raises
`DeviceError: invalid udid` today. The design below closes that gap.

**Speak W3C WebDriver from Python, not the runner channel.** The local XCUITest path drives a
resident BajutsuKit runner over a bespoke loopback HTTP channel. An Appium grid exposes no such
runner. It exposes only a World Wide Web Consortium (W3C) WebDriver endpoint (Appium's XCUITest
driver). The live transport therefore speaks W3C WebDriver to the remote endpoint directly from
Python. It does not tunnel the runner channel to a port that the grid does not serve.

**Keep element resolution on the Python side.** WebDriver resolves a locator on the server.
Determinism first (prime directive 2) requires Bajutsu to resolve a selector Python-side instead. An
ambiguous selector then fails immediately, rather than acting on whichever element matched first.

The live driver therefore reuses the shape of the existing `XcuitestDriver`:

- Query the whole screen with one broad locator (`findElements` over `//*` or an iOS class chain).
- Build the `base.Element` list.
- Resolve the selector against that list with `resolve_unique`.
- Act on the chosen element by the WebDriver element id that the query returned.

This is the query-resolve-act-by-handle flow that the runner channel already follows. The WebDriver
element id stands in for the runner's per-snapshot handle.

**Reuse the injectable-transport seam, with a minimal in-house client.** `XcuitestDriver` already
takes its wire protocol as an injectable transport: `(method, path, body)` in, a decoded reply out.
Its request and response logic therefore runs against a fake, with no Simulator on the gate.

The live driver takes a minimal in-house W3C WebDriver client built on the standard library's
`http.client`. The client is injected the same way. It matches the runner channel's own stdlib
client. It keeps the gate free of a third-party WebDriver dependency. It also lets the gate exercise
the WebDriver mapping against a fake endpoint at the network boundary.

**Route the endpoint around the simctl and xcodebuild machinery.** `environment_for` selects the
environment for a run. When the xcuitest actuator's udid spec is an `http(s)://` endpoint, the run
takes a live path. The live path skips simctl bring-up and the `xcodebuild test-without-building`
subprocess entirely. It opens a WebDriver session against the endpoint, drives it through the live
driver, and closes the session on teardown.

The URL scheme is the routing signal. It is exactly the value that `validated_udid` rejects. Recognizing
the scheme up front both selects the live path and replaces the misleading `invalid udid` error. The
endpoint never reaches the udid machinery, which structurally cannot carry a URL.

The BE-0236 `ProvisionProfile` already reports the reserved device as booted, with its build
installed. The live path therefore honours no simctl bring-up flags. The real-device path (Unit 1)
already skips them.

The follow-on lands in three slices. Each slice is faked at the WebDriver boundary.

- **Slice A: session and a minimal driver.** This slice adds four pieces:
  - the in-house W3C client (create and delete session);
  - the live driver's `query`, `tap`, `screenshot`, and readiness wait;
  - the `environment_for` routing that opens the session;
  - the skip of `xcodebuild`.

  With the slice, a live run taps and asserts end to end.
- **Slice B: input and gestures.** Map `swipe`, `scroll`, `type_text`, `delete_text`, `select_all`,
  `copy_selection`, `double_tap`, `long_press`, `tap_point`, `pinch`, and `rotate` onto WebDriver
  actions. The actions are the W3C Actions application programming interface (API) or Appium's
  `mobile:` commands. Today `double_tap` and `long_press` share the same `/tap` runner endpoint as
  plain `tap` (via extra params). Their WebDriver mapping is therefore a piece of work similar in
  size to the other gestures. `tap_point` is the handle-less coordinate tap used for system alerts.
  It maps to the same Actions path.
- **Slice C: capabilities, config, and docs.** Narrow the live route's run-time capability set to
  what Appium-XCUITest reaches, through `capabilities_for_run` (the Unit 3 mechanism). Add a showcase
  target for a live grid. Extend the how-to with the live route.

### Prime-directive compliance

- **AI out of the gate.** Real-device iOS execution is deterministic XCTest/XCUITest. No model sits
  on the verdict path.
- **Determinism first.** No fixed sleeps are introduced. Readiness stays condition-based, as in the
  existing backends.
- **App-agnostic.** iOS cloud specifics live in target config and, for batch, in the CI-side
  submitter. The runner and scenario format are unchanged.

## Alternatives considered

- **Port `idb`/`simctl` to the cloud.** Rejected as structurally infeasible. A real-device cloud has
  no simulator, and managed macOS hosts cannot host daemons. Hence the XCTest/Appium route.
- **A brand-new iOS cloud backend from scratch.** Rejected. It duplicates the BE-0019 XCUITest
  backend. The BE-0019 backend is generalised to real devices instead.
- **Do iOS at the same time as Android.** Rejected. Android reuses the adb backend almost verbatim,
  while iOS needs real new driving and packaging code. The two stay separate, so that the Android
  path can ship first without waiting on the heavier iOS work.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] XCUITest real-device targeting (generalise BE-0019 beyond the Simulator)
- [x] Batch packaging for Device Farm (submitter integration)
- [x] Re-signing / entitlement handling (document + preflight degradation)
- [x] Live route: Appium-endpoint `DeviceProvider`, driven end to end over a WebDriver transport
  (Slices A–C)
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
- Live route Slice A ([#1201](https://github.com/bajutsu-e2e/bajutsu/pull/1201)): landed the first live-transport slice — the endpoint the `appium`
  provider yields is now drivable end to end for a tap-and-assert run. This slice adds a new in-house W3C WebDriver
  client (`drivers/xcuitest_live.py`, `http.client`-based, its wire injected the same way
  `XcuitestDriver` injects its transport, so no third-party WebDriver dependency reaches the gate) and
  an `XcuitestLiveDriver` that reuses the query-resolve-act-by-handle shape: one broad `findElements`
  builds the `base.Element` list, `resolve_unique` resolves the selector Python-side (an ambiguous
  selector still fails before any actuation — determinism first), and the tap acts by the WebDriver
  element id the query returned, standing in for the runner's per-snapshot handle. `environment_for`
  now recognizes an `http(s)://` udid spec (the routing signal — exactly the value the shared
  `device_id` policy rejects) and returns a new `XcuitestLiveEnvironment`, which opens a WebDriver
  session against the endpoint and skips simctl / `xcodebuild` entirely; the simctl-backed seam
  methods (`resolve_device`, device catalog, `DeviceControl` controller, relauncher) are overridden to
  the live route's shape, since each would otherwise build a `simctl.Env(endpoint)` that rejects the
  URL. This closes the Unit 4/5 boundary: a real endpoint no longer reaches `_destination`, so the
  boundary test is updated to assert the new routing. Slice A covers `query` / `tap` / `screenshot` /
  readiness; input and gestures (Slice B) and the run-time capability narrowing / config / docs
  (Slice C) remain, so the live-route box stays unchecked. Faked at the WebDriver network boundary; no
  grid, no device on the gate.
- Live route Slice B ([#1203](https://github.com/bajutsu-e2e/bajutsu/pull/1203)): wired the live route's input and
  gestures onto Appium's XCUITest `mobile:` commands (over `POST /execute/sync`), the native
  counterparts of the local runner's semantic endpoints — `tap_point` → `mobile: tap`, `double_tap` →
  `mobile: doubleTap`, `long_press` → `mobile: touchAndHold`, `swipe` / `scroll` →
  `mobile: dragFromToForDuration`, `pinch` → `mobile: pinch`, and `rotate` → `mobile: rotateElement`.
  Every element-targeted gesture resolves its selector Python-side through the same `_resolve_handle`
  the tap uses, so an ambiguous selector still fails before any actuation (determinism first). Text
  entry types into the focused field with standard W3C send-keys to the active element (`type_text`
  and, once per character, the backspace key for `delete_text`). Two actions have no first-class
  Appium XCUITest command — the local runner does `select_all` / `copy_selection` natively — so on the
  live route they fail loudly with `UnsupportedAction` rather than silently no-op'ing; the run-time
  capability narrowing that skips them up front is Slice C. The pinch / rotate wiring now advertises
  `MULTI_TOUCH`, so the driver reports exactly what it drives. Slice C (run-time capability narrowing,
  a showcase live-grid target, and the how-to's live-route section) remains, so the live-route box
  stays unchecked. Faked at the WebDriver network boundary; no grid, no device on the gate.
- Live route Slice C ([#1205](https://github.com/bajutsu-e2e/bajutsu/pull/1205)): closed the live route — the run-time capability
  narrowing, a worked showcase config, and the how-to. `capabilities_for_run` now narrows the XCUITest
  set on a live run to the live driver's own `CAPABILITIES` — the single source of truth, so preflight
  and the driver stay in lockstep — dropping the three families the WebDriver transport cannot drive:
  `TEXT_SELECTION` (no first-class `mobile:` command for select-all / copy), and, as on any real
  device, the `DeviceControl` family and the simctl-privacy permission grants. A scenario needing one
  is now skipped up front (BE-0082) with a clear reason instead of failing late with
  `UnsupportedAction` mid-run, the live-route counterpart of Unit 3's real-device narrowing.
  `capabilities_for_run` keys the run's resolved udid spec through `is_webdriver_endpoint` — the very
  predicate `environment_for` routes on — so preflight and routing can never diverge (an `appium`
  provider's endpoint and a raw `--udid https://…` under the default local provider narrow
  identically). `demos/showcase/live/`
  carries a worked example config (the `showcase-swiftui` target with an `appium` provider and no local
  `appPath` / runner), and the how-to's live section (both languages) is rewritten from "seam only" to
  the end-to-end route, documenting the narrowing. Faked at the config / capability boundary; no grid,
  no device on the gate. This checks the live-route box; with every unit landed, BE-0238 is
  Implemented. (The batch route's showcase CI job still awaits device-signing infrastructure — an
  unsigned device `.ipa` cannot be built — and remains a follow-on outside this item's MECE units.)

## References

- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [AWS Device Farm — iOS custom test environment hosts](https://docs.aws.amazon.com/devicefarm/latest/developerguide/custom-test-environments-hosts-ios.html)
- [AWS Device Farm — Appium test types](https://docs.aws.amazon.com/devicefarm/latest/developerguide/test-types-appium.html)
- [Firebase Test Lab — iOS (XCTest)](https://firebase.google.com/docs/test-lab/ios/get-started)
- Depends on sibling items: **device-cloud-provider-abstraction** (live seam), **aws-device-farm-submitter** (batch packaging)

**English** · [日本語](ja/ios-device-cloud.md)

# iOS on a real device and in a device cloud

A device cloud runs iOS on **real hardware** — there is no Simulator on the far side. Bajutsu's
iOS [backend](glossary.md#driver-backend-actuator-platform) targets the Simulator, so it cannot
reach that hardware, and reaching a device cloud takes real new code rather than a configuration
switch. This page explains why the Simulator-bound backend falls short, the one change that fixes
it — making the XCUITest backend drive a real device — and the two routes that change opens: a
**batch** route through AWS Device Farm and a
**live** route through a reserved device behind an Appium endpoint. The same real-device work also
lets Bajutsu drive a **locally attached** iPhone or iPad, which was effectively Simulator-only before.

## Why the Simulator backend does not reach a real device

The gap is structural, not a missing option. The iOS backend, driving the Simulator, depends on
something a real-device cloud does not provide.

- **`simctl` drives the Simulator only.** It is the command-line control surface for Apple's iOS
  Simulator, so it has nothing to target on a cloud that runs physical devices — there is no
  simulator there to command. The XCUITest backend leans on `simctl` for the Simulator bring-up
  (erase, install, permission grants) around each run, none of which a physical device offers.

What the clouds *do* speak on iOS is Apple's own **XCTest**, and on AWS Device Farm also **Appium's
XCUITest driver**. Both build on the same XCUITest machinery that Bajutsu's
[XCUITest backend (BE-0019)](../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
already drives through `xcodebuild`. The path forward is therefore to generalize that backend to a
real device, not to write a new iOS backend from scratch.

## The reusable core: XCUITest real-device targeting

The XCUITest backend targets the Simulator by default. A single config key on the
[target](glossary.md#target-app-device) selects a real device instead:

```yaml
targets:
  my-app:
    xcuitest:
      deviceType: device   # "simulator" (the default) or "device"
```

With `deviceType: device`, the backend generalizes its `xcodebuild` `-destination` from a named
Simulator to `platform=iOS`, so the same `xcodebuild test-without-building` driving layer runs
against a real device. The device the destination resolves to comes from the run's udid — a locally
attached device's udid, or the udid a device cloud hands the run.

A real device also skips the Simulator bring-up that `simctl` performs, which drops three
preconditions the Simulator path takes for granted: erasing the device, installing the app from a
local `appPath`, and granting permissions up front. A scenario that needs one of those on a real
device fails loudly rather than silently doing nothing, and the Device Farm route below installs the
app through the cloud instead. This same key drives a locally attached iPhone or iPad, so the
real-device work is useful on its own, before any cloud is involved.

## Two routes to a device cloud

Both routes share the real-device XCUITest core above; they differ in how a device is reserved and
how the run reaches it.

### Batch — AWS Device Farm

AWS Device Farm is a **batch** service: it runs your commands on its host, which already has the
reserved device attached, rather than lending you a device to drive over the network. Bajutsu reaches
it through a CI-side submitter that packages the app plus your scenarios, uploads them with a
test-spec that runs `bajutsu run --backend xcuitest` against the reserved device's udid, and collects
the artifacts. The verdict still comes from Bajutsu's own machine-checkable assertions, never from
Device Farm's own pass/fail classification.

The submitter, the test-spec it renders, the re-signing caveats, and the manual proof-of-concept
procedure are all documented on the [AWS Device Farm](devicefarm.md) page — the iOS run reuses that
same batch machinery, selecting the iOS app upload and the `xcuitest` backend by platform.

### Live — an Appium endpoint provider

Where a cloud reserves a single iOS device and exposes it behind an Appium / WebDriver **endpoint** —
a self-hosted grid, for example — Bajutsu models that reservation as a device provider on the live
seam ([BE-0236](../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)).
A built-in `appium` provider yields the run the fixed endpoint of the reserved device instead of a
locally attached udid:

```yaml
targets:
  my-app:
    deviceProvider:
      kind: appium
      endpoint: https://grid.example.com/wd/hub   # the reserved device's Appium / WebDriver address
```

The provider treats the reservation as a device Bajutsu never boots or installs through `simctl`: it
reports the device ready with its build in place and has nothing to release, since the reservation
belongs to the grid. A missing `endpoint` fails closed when the run resolves the provider, mirroring
the guard on an unknown provider `kind`.

Bajutsu drives that endpoint over a live **W3C WebDriver transport**. The endpoint's `http(s)://`
scheme is the routing signal: it is exactly the value the shared `device_id` character set rejects
(a URL carries a `/`), so it can never collide with a real `simctl` udid, and the run routes it
**around** the `simctl` / `xcodebuild` udid machinery entirely — that machinery structurally cannot
carry a URL. A tap-and-assert flow works end to end. The semantic actions the local runner drives
natively map onto Appium's XCUITest `mobile:` commands over the endpoint: `tap`, `query`, screenshots,
condition waits, the input steps (`type` / `delete`, `swipe` / `scroll`), and the two-finger
`pinch` / `rotate` gestures. As on the local backends, an ambiguous selector still fails **before any
actuation** — the selector resolves in Bajutsu, not on the grid.

What the WebDriver transport cannot drive is narrowed away up front, the same way the real-device
path narrows the `simctl`-backed families:

- **Native text selection** (`select` / `copy`) has no first-class Appium XCUITest command — the
  local runner does select-all and clipboard copy natively, but the endpoint exposes no faithful
  counterpart.
- **`simctl` device control and permission grants** do not apply on the live route either, exactly
  as on any other real device (see the caveats below).

So on the live route the preflight ([BE-0082](../roadmaps/BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md))
advertises only what the transport actually drives and **skips** a scenario that needs one of the
narrowed capabilities — with a clear reason, before any device work — rather than failing late with
`UnsupportedAction` mid-run.

A worked example config lives at
[`demos/showcase/live/showcase.live.config.yaml`](../demos/showcase/live/showcase.live.config.yaml):
it mirrors the local `showcase-swiftui` target but carries a `deviceProvider` of kind `appium` and no
local `appPath` / `xcuitest.testRunner`, since the reserved device already holds the build and the
live route speaks WebDriver rather than the runner channel. Point its `endpoint` at your own grid,
then run the shared showcase suite against it:

```bash
bajutsu run --target showcase-swiftui-live --config demos/showcase/live/showcase.live.config.yaml
```

## Real-device caveats: re-signing and capability degradation

Running on a real device rather than the Simulator changes two things Bajutsu accounts for up front.
Both are properties of physical hardware, not of any one cloud, so they hold for every real device
the XCUITest backend drives — whether reached by `xcuitest.deviceType: device` (Device Farm or a
locally attached device) or over the live WebDriver route above.

- **Re-signing strips entitlements.** A device cloud re-signs the uploaded app with its own
  provisioning profile so it installs on the reserved device, and the re-sign drops the entitlements
  the new profile does not carry — commonly Push and App Groups. An app feature that depends on a
  dropped entitlement behaves as the re-signed build does, so a scenario asserting on such a feature
  should expect the re-signed behavior rather than the App Store one.
- **`simctl` device control and permission grants do not apply.** Bajutsu's iOS device control
  (`setLocation`, the clipboard steps, `push`, `clearKeychain`, `background` / `foreground`, and the
  status-bar overrides) and its permission grants are all backed by `simctl`, which reaches only the
  Simulator. On a real device the XCUITest backend advertises neither, so a scenario that uses one is
  **skipped by the preflight** (BE-0082) with a clear reason, before any device work, rather than
  failing late with a `simctl` error mid-run. The on-device capabilities the XCTest runner drives
  itself — query, elements, screenshots, taps, and two-finger gestures — are unaffected.

The [AWS Device Farm](devicefarm.md#ios-re-signing-and-real-device-capabilities) page covers the same
two caveats in the batch context, including the specific entitlement keys Device Farm's re-sign drops.

## References

- [AWS Device Farm](devicefarm.md) — the batch route: the submitter, the test-spec, and the manual
  proof of concept.
- [Drivers](drivers.md) — the `Driver` interface and the backends behind it, including XCUITest.
- [Configuration](configuration.md) — the `xcuitest.deviceType` and `deviceProvider` target keys.
- [BE-0019 — XCUITest backend](../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [BE-0236 — device-cloud provider abstraction](../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)
- [BE-0238 — iOS device-cloud execution](../roadmaps/BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)

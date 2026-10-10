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

## Reaching a real device's channels

On the Simulator, the host and the app share one loopback address, `127.0.0.1`. Every channel
between Bajutsu and an iOS app relies on that. A real device has a loopback of its own. With
`deviceType: device`, each channel crosses the host–device boundary another way. The table lists
how.

| Channel | Who listens | Direction | On a real device |
|---|---|---|---|
| XCUITest runner | the runner, on the device | host → device | usbmuxd bridge |
| `nativeZ` responder | the app, on the device | host → device | usbmuxd bridge |
| WebView bridge | the app, on the device | host → device | usbmuxd bridge |
| Network collector | Bajutsu, on the host | device → host | an exchanged host address |

### Host to device: the usbmuxd bridge

usbmuxd is the macOS service that Xcode and `iproxy` use to reach a device. Its `Connect` request
joins a socket to a port on the device's loopback. Bajutsu speaks the usbmuxd protocol itself, over
`/var/run/usbmuxd`. The host needs no extra tool. Bajutsu listens on the host's `127.0.0.1` for each
channel. It tunnels every connection to the device port. The Android resident channel does the same
with `adb forward`.

The bridge prefers a device attached over Universal Serial Bus (USB). It falls back to a network
attachment when usbmuxd lists nothing else. Suppose usbmuxd does not list the device at all. An
unplugged or untrusted device is the usual cause. The runner's spawn then fails at once and names
that reason, instead of timing out. The `nativeZ` field is diagnostic. A `nativeZ` bridge that cannot
open leaves the field absent and lets the run continue.

The listeners on the device stay on its loopback. The runner has no authentication, so it must stay
out of reach of the network.

### Device to host: an exchanged host address

The app reports network exchanges to a collector on the host. usbmuxd carries no connection that the
device opens. This channel needs a host address the device can route to. On a real device, the
collector binds every interface of the host. The app receives one collector URL per candidate host
address. The app sends each candidate an authenticated `GET /ping`. It keeps the first one, in the
host's order, that answers. The search runs in the background, so the launch never waits for it. It
retries for up to two minutes. Up to 1,000 reports the app makes meanwhile wait. They go out in
order once a collector answers. The app writes any report it drops to the device log. The collector
answers 401 to a request without the per-run token. This run's app alone holds that token.

Bajutsu resolves the candidates at each run. A host whose address changes keeps working that way. The
candidates come from the first of these sources that has a value:

1. The `BAJUTSU_HOST_ADDRESS` environment variable.
2. The target's `xcuitest.hostAddress`
   ([configuration](configuration.md#real-device-host-address-xcuitesthostaddress)).
3. Every routable IPv4 and IPv6 address of the host's active interfaces. The list includes the IPv6
   address of the CoreDevice tunnel. Xcode itself uses that tunnel for a real-device test.

Suppose a run records network exchanges and no candidate exists. The run then fails before any
device work, and the error names both settings. A device that reaches none of the candidates records
no exchanges. A network assertion then fails on the empty record.

Two properties of this route need care on the device side:

- From iOS 14, an app asks for **Local Network permission** before its first local-network
  connection. The search makes such a connection, so a fresh install raises the prompt during
  launch. Every Device Farm job is a fresh install. Declare `{ prompt: localNetwork, choice: grant }`
  in `systemAlertHandling.rules` ([scenarios](scenarios.md)). The guard then answers the prompt,
  including one already up when the scenario starts. Until the grant, the search gets no answer and
  the reports wait. We measured this on an iPhone
  with iOS 27.0.1. iOS showed the prompt even with no `NSLocalNetworkUsageDescription` entry.
- The probe and every report travel as **cleartext** HTTP over the chosen route. The token travels
  the same way. On a shared network, pin `BAJUTSU_HOST_ADDRESS` to the
  CoreDevice tunnel's address instead.

### Checking the channels before a run

Run `bajutsu doctor --udid <udid> --environment-only` for a `deviceType: device` target. It reports
both routes. A real device needs no booted Simulator. In that check's place,
doctor checks whether usbmuxd lists the device, and over which attachment. A device usbmuxd does not list
fails that check, and doctor exits non-zero. Doctor also lists the host addresses that the app would
receive. That list informs alone, since a scenario recording network exchanges is the one that needs it. A
device-cloud job can run doctor before `bajutsu run` to see which route its host lacks. The Device
Farm route depends on the same two routes. Nobody has verified them on Device Farm yet.

## The signed device runner

A real device installs an XCUITest runner signed by a team the device trusts. Bajutsu cannot ship
such a runner prebuilt, unlike the Simulator runner. Each user builds the device runner with
`bajutsu runner build --device`. One build serves until an input listed under Building changes. The build signs with that user's own Apple Developer account. A target with
`deviceType: device` and no `xcuitest.testRunner` then resolves to that build. The target config
stays free of anything specific to one user.

### The signing file

Signing belongs to the user, not to a target. The runner stays generic, and the target config
travels through the repository. A team ID written in the config would reach every other user. The
settings live in a per-user file outside the repository instead. Bajutsu looks for the file in
this order, and the first hit wins:

1. `--signing <path>` on `bajutsu runner build` (the build command alone has this flag).
2. The `BAJUTSU_SIGNING_FILE` environment variable.
3. `$XDG_CONFIG_HOME/bajutsu/signing.yaml`, falling back to `~/.config/bajutsu/signing.yaml`.

A run consults entries 2 and 3 alone. A user who builds with `--signing` must point
`BAJUTSU_SIGNING_FILE` at the same file when running. The alternative is to copy the products out
with `--out` and name them in `testRunner`.

```yaml
bundleIdPrefix: com.acme           # host app = com.acme.bajutsu.runner-host,
                                   # UI-test bundle = com.acme.bajutsu.runner-uitests
# bundleIds:                       # instead of bundleIdPrefix: name both identifiers yourself
#   host: com.acme.e2e.runner-host
#   uitests: com.acme.e2e.runner-tests
teamId: ABCDE12345                 # required
signing: automatic                 # automatic (the default) or manual
# manual:                          # required when signing: manual (ignored under automatic)
#   identity: "Apple Development: Jane Doe (ABCDE12345)"
#   profile: "Acme Bajutsu Wildcard"         # one profile covering every identifier, or
#   profiles:                                # one profile per signed product
#     host: "Acme Bajutsu Host"
#     runner: "Acme Bajutsu Runner"          # the UI-test bundle's .xctrunner app
```

The file holds no secret. A team ID, a certificate name, and a profile name merely point at keys
and profiles. Those stay in the Keychain and in `~/Library/MobileDevice/Provisioning Profiles/`.

| Key | Meaning |
|---|---|
| `bundleIdPrefix` | Derives both runner identifiers. Give this key or `bundleIds`, never both. |
| `bundleIds.host`, `bundleIds.uitests` | Name the two identifiers outright, for an account that already holds App IDs and profiles under other names. The two values must differ. |
| `teamId` | The Apple Developer team that signs both products. |
| `signing` | `automatic` lets Xcode mint the profiles. `manual` pins the certificate and profiles below. |
| `manual.identity` | The codesigning identity, by name or by its certificate hash. |
| `manual.profile` / `manual.profiles` | One profile for every product, or one each for the runner's main app (`profiles.host`) and the `.xctrunner` app (`profiles.runner`). Give exactly one form. When two unexpired profiles share a name, give the profile by the identifier in its `UUID` field instead. |

The UI-test bundle's `.xctrunner` app takes the identifier `<uitests identifier>.xctrunner`. A
manual build with one profile per product needs a profile for that identifier too.

Keep the runner identifiers distinct from the app under test. A runner that shares the app's
identifier overwrites the app when it installs. Bajutsu cannot check for that clash, because the
build knows no target.

### Building

```bash
bajutsu runner build --device [--signing PATH] [--out DIR] [--force]
```

The command works in three steps:

1. Check the prerequisites: macOS, `xcodebuild`, and `xcodegen`. Manual signing adds the identity
   and every named profile. Each gap appears with its fix.
2. Copy the runner sources into a scratch directory, and rewrite that copy for the signing file.
   The checkout stays untouched. In a wheel install, the sources come from a copy the wheel ships.
3. Build the scratch copy with `xcodebuild build-for-testing`, and print the path of the built
   `BajutsuRunner.xctestrun`.

The products land in Bajutsu's cache under `xcuitest-runner-device/<key>/Products/`. The key covers
every input that changes the signed products:

- the runner sources;
- the signing fields, with both resolved identifiers;
- the Xcode build version;
- for manual signing, the certificate hash and a digest of each profile file.

A certificate or profile renewed under the same name yields a new key. A repeat build
with unchanged inputs reuses the cached products, and `--force` rebuilds anyway. `--out DIR`
copies the `Products` directory to `DIR` as well. Device Farm packaging needs that copy: the
`.xctestrun` expects its test bundles beside itself.

### What a run does

A run with `deviceType: device` and no `testRunner` reads the signing file and computes the same
key. The run then uses the cached `.xctestrun`. An explicit `testRunner` still wins over the cache.

A run never builds the runner itself. A signing build is slow, can raise a Keychain prompt, and can
register identifiers with Apple. Each miss fails at once and names the fix:

- **No signing file:** the error names both lookup locations.
- **No build for the current inputs:** the error names `bajutsu runner build --device`.
- **An expired profile:** the error names `bajutsu runner build --device --force`.

`bajutsu doctor` reports which signing file a device target would use. The report builds nothing and
computes no key.

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
- [Command reference](cli.md#runner) — `bajutsu runner build --device`.
- [BE-0019 — XCUITest backend](../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [BE-0236 — device-cloud provider abstraction](../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)
- [BE-0238 — iOS device-cloud execution](../roadmaps/BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)
- [BE-0456 — per-user signed device build of the XCUITest runner](../roadmaps/BE-0456-runner-device-signing-build/BE-0456-runner-device-signing-build.md)

**English** · [日本語](ja/ios-device-cloud.md)

# iOS on a real device and in a device cloud

A device cloud runs iOS on **real hardware**, with no Simulator on the far side. Bajutsu's iOS
[backend](glossary.md#driver-backend-actuator-platform) targets the Simulator, so it cannot reach
that hardware. Reaching a device cloud takes new code, not a configuration switch.

This page explains why the Simulator-bound backend falls short. It then describes the one change
that fixes the gap: making the XCUITest backend drive a real device. Last, it covers the two routes
that change opens. The **batch** route goes through AWS Device Farm. The **live** route goes through
a reserved device behind an Appium endpoint. The same real-device work also lets Bajutsu drive a
**locally attached** iPhone or iPad. Before, that was effectively Simulator-only.

## Why the Simulator backend does not reach a real device

The gap is structural, not a missing option. When the iOS backend drives the Simulator, it depends
on something that a real-device cloud does not provide.

- **`simctl` drives the Simulator alone.** It is the command-line control surface for Apple's iOS
  Simulator. A cloud that runs physical devices has no simulator for it to command. The XCUITest
  backend leans on `simctl` for the Simulator bring-up around each run (erase, install, permission
  grants). A physical device offers none of those.

What the clouds *do* speak on iOS is Apple's own **XCTest**. AWS Device Farm also speaks
**Appium's XCUITest driver**. Both build on the XCUITest machinery that Bajutsu's
[XCUITest backend (BE-0019)](../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
already drives through `xcodebuild`. The path forward is to generalize that backend to a real
device, not to write a new iOS backend from scratch.

## The reusable core: XCUITest real-device targeting

The XCUITest backend targets the Simulator by default. A single config key on the
[target](glossary.md#target-app-device) selects a real device instead.

```yaml
targets:
  my-app:
    platform: ios
    subject: { id: com.example.myapp }
    runsOn:
      ios:
        kind: device   # "simulator" (the default) or "device"
```

With `runsOn.ios.kind: device`, the backend generalizes its `xcodebuild` `-destination` from a named
Simulator to `platform=iOS`. The same `xcodebuild test-without-building` driving layer then runs
against a real device. The destination resolves to the device that the run's udid names. That udid comes from a locally
attached device or from the device cloud that hands the run its device.

A real device skips the Simulator bring-up that `simctl` performs. That drops three preconditions
the Simulator path takes for granted:

- erasing the device;
- installing the app from a local `subject.path`;
- granting permissions up front.

A scenario that needs one of those on a real device fails loudly rather than silently doing
nothing. The Device Farm route below installs the app through the cloud instead. The same key also
drives a locally attached iPhone or iPad. The real-device work is useful on its own, before any
cloud is involved.

## Reaching a real device's channels

On the Simulator, the host and the app share one loopback address, `127.0.0.1`. Every channel
between Bajutsu and an iOS app relies on that shared address. A real device has a loopback of its
own. With `runsOn.ios.kind: device`, each channel crosses the host–device boundary another way. The
table lists how.

| Channel | Who listens | Direction | On a real device |
|---|---|---|---|
| XCUITest runner | the runner, on the device | host → device | usbmuxd bridge |
| `nativeZ` responder | the app, on the device | host → device | usbmuxd bridge |
| WebView bridge | the app, on the device | host → device | usbmuxd bridge |
| Network collector | Bajutsu, on the host | device → host | an exchanged host address |

### Host to device: the usbmuxd bridge

usbmuxd is the macOS service that Xcode and `iproxy` use to reach a device. Its `Connect` request
joins a socket to a port on the device's loopback. Bajutsu speaks the usbmuxd protocol itself, over
`/var/run/usbmuxd`, so the host needs no extra tool. For each channel, Bajutsu listens on the
host's `127.0.0.1`. It tunnels every connection to the device port. The Android resident channel does the
same with `adb forward`.

The bridge prefers a device attached over Universal Serial Bus (USB). It falls back to a network
attachment when usbmuxd lists nothing else.

Suppose usbmuxd does not list the device at all. An unplugged or untrusted device is the usual
cause. The runner's spawn then fails at once and names that reason, instead of timing out.

The `nativeZ` field is diagnostic. A `nativeZ` bridge that cannot open leaves the field absent and
lets the run continue.

The listeners on the device stay on its loopback. The runner has no authentication, so it must stay
out of reach of the network.

### Device to host: an exchanged host address

The app reports network exchanges to a collector on the host. usbmuxd carries no connection that the
device opens. This channel needs a host address that the device can route to. On a real device,
the collector binds every interface of the host.

The app receives one collector URL per candidate host address. It sends each candidate a signed
`GET /ping`. It keeps the first candidate, in the host's order, that answers with the collector's
signature. The search runs in the background, so the launch never waits for it. It retries for up
to two minutes.

Meanwhile, up to 1,000 reports the app makes wait. They go out in order once a collector answers.
The app writes any report it drops to the device log.

Every request must prove the per-run token, which this run's app alone holds. The token itself
never crosses the network, as
[how the device-to-host route is authenticated](#how-the-device-to-host-route-is-authenticated)
describes.

Bajutsu resolves the candidates at each run, so a host whose address changes keeps working. The
candidates come from the first of these sources that has a value:

1. The `BAJUTSU_HOST_ADDRESS` environment variable.
2. The target's `driver.runner.hostAddress`
   ([configuration](configuration.md#real-device-host-address-driverrunnerhostaddress)).
3. Every routable IPv4 and IPv6 address of the host's active interfaces. The list includes the IPv6
   address of the CoreDevice tunnel. Xcode itself uses that tunnel for a real-device test.

Suppose a run records network exchanges and no candidate exists. The run then fails before any
device work, and the error names both settings. A device that reaches none of the candidates records
no exchanges. A network assertion then fails on the empty record.

Two properties of this route need care on the device side:

- **Local Network permission.** From iOS 14, an app asks for this permission before its first
  local-network connection. The search makes such a connection, so a fresh install raises the
  prompt during launch. Every Device Farm job is a fresh install. Declare a
  `systemAlert: localNetwork` entry with `choice: grant` in the target's `hooks.interrupts` or the
  scenario's `interrupts` ([scenarios](scenarios.md)). The guard then answers the prompt, including
  one already up when the scenario starts. Until the grant, the search gets no answer and the
  reports wait. We measured this on an iPhone with iOS 27.0.1. iOS showed the prompt even with no
  `NSLocalNetworkUsageDescription` entry.
- **Plain HTTP.** The probe and every report travel as plain HTTP over the chosen route. The token
  stays off the wire, but each report's captured bodies still travel in the clear. On a shared
  network, pin `BAJUTSU_HOST_ADDRESS` to the CoreDevice tunnel's address.

### How the device-to-host route is authenticated

The device reaches the collector over a network that Bajutsu does not control. On Amazon Web
Services (AWS) Device Farm, the network belongs to the farm. A reader of a request that carried the
per-run token could copy the token. That reader could then write fabricated exchanges into the
run's evidence. The app and the collector never send the token. They prove to each other that they hold it. Each side
computes a Keyed-Hash Message Authentication Code (HMAC) with the token as its key:

- **Requests are signed.** Each request carries
  `Authorization: Bajutsu-HMAC-SHA256 nonce=<nonce>, signature=<signature>`. The signature covers
  the method, the path, a fresh random nonce, and a digest of the body. The digest uses the Secure
  Hash Algorithm (SHA) with 256 bits.
- **A replay is refused.** The collector keeps every nonce it has accepted for the whole run. A
  copied request gets a 409 and adds nothing to `network.json`.
- **Answers are signed.** The collector signs each answer against the request's nonce, in the
  `X-Bajutsu-Signature` header. The app applies a `/commands` answer once its signature verifies.
  A probe answer without a valid signature never wins the search.
- **The bearer header is refused.** Beyond the loopback, the collector answers 401 to a bearer
  header. The run log warns once when such a header carried this run's token. The warning means the
  app's BajutsuKit predates signed requests and needs a rebuild.

Bajutsu announces the signed method in the launch environment. It sets
`BAJUTSU_COLLECTOR_AUTH=hmac` beside `BAJUTSU_COLLECTOR_TOKEN`. An app built against an older
BajutsuKit ignores the announcement. A newer BajutsuKit driven by an older Bajutsu sees no
announcement. That BajutsuKit sends the bearer header that the older collector expects.

The signed method keeps the token secret and proves that each request and answer is genuine and
fresh. Two properties stay out of its reach:

- **Readable bodies.** A party on the route can read the captured bodies in each report.
- **No availability guarantee.** A party on the route can drop or delay a request. The step that
  waits on that request then fails on its existing timeout. No forged exchange or command reaches
  the verdict.

### Checking the channels before a run

Run `bajutsu doctor --udid <udid> --environment-only` for a `runsOn.ios.kind: device` target. The
command reports both routes.

A real device needs no booted Simulator. In place of that check, doctor checks whether usbmuxd
lists the device, and over which attachment. A device that usbmuxd does not list fails the check,
and doctor exits non-zero. Doctor also lists the host addresses that the app would receive. That
list is informational, since only a scenario that records network exchanges needs it.

A device-cloud job can run doctor before `bajutsu run` to see which route its host lacks. The
Device Farm route depends on both routes. Nobody has verified them on Device Farm yet.

## The signed device runner

A real device installs an XCUITest runner signed by a team the device trusts. Unlike the Simulator
runner, Bajutsu cannot ship such a runner prebuilt. Each user builds the device runner with
`bajutsu runner build --device`. The build signs with that user's own Apple Developer account. One
build serves until an input listed under Building changes. A target with `runsOn.ios.kind: device` and no
`driver.runner.testRunner` then resolves to that build. The target config stays free of anything
specific to one user.

### The signing file

Signing belongs to the user, not to a target. The runner stays generic, and the target config
travels through the repository. A team ID written in the config would reach every other user. The
settings live in a per-user file outside the repository. Bajutsu looks for the file in this order,
and the first hit wins:

1. `--signing <path>` on `bajutsu runner build` (the build command alone has this flag).
2. The `BAJUTSU_SIGNING_FILE` environment variable.
3. `$XDG_CONFIG_HOME/bajutsu/signing.yaml`, falling back to `~/.config/bajutsu/signing.yaml`.

A run consults entries 2 and 3 alone. A user who builds with `--signing` must point
`BAJUTSU_SIGNING_FILE` at the same file when running. The alternative is to copy the products out
with `--out` and name them in `driver.runner.testRunner`.

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
and profiles. The keys and profiles stay in the Keychain and in the profile directories. Xcode 16 and later keep
profiles in `~/Library/Developer/Xcode/UserData/Provisioning Profiles/`. Earlier releases use
`~/Library/MobileDevice/Provisioning Profiles/`. Bajutsu searches both.

| Key | Meaning |
|---|---|
| `bundleIdPrefix` | Derives both runner identifiers. Give this key or `bundleIds`, never both. |
| `bundleIds.host`, `bundleIds.uitests` | Name the two identifiers outright, for an account that already holds App IDs and profiles under other names. The two values must differ. |
| `teamId` | The Apple Developer team that signs both products. |
| `signing` | `automatic` lets Xcode mint the profiles. `manual` pins the certificate and profiles below. |
| `manual.identity` | The codesigning identity, by name or by its certificate hash. |
| `manual.profile` / `manual.profiles` | One profile for every product, or one each for the runner's main app (`profiles.host`) and the `.xctrunner` app (`profiles.runner`). Give exactly one form. When two unexpired profiles share a name, give the profile by the identifier in its `UUID` field instead. A profile Xcode manages, such as `iOS Team Provisioning Profile: *`, does not qualify: Xcode refuses it under manual signing, so the check before the build rejects it. Create a development profile in the Apple Developer portal, or use `signing: automatic`. |

The UI-test bundle's `.xctrunner` app takes the identifier `<uitests identifier>.xctrunner`. A
manual build with one profile per product needs a profile for that identifier too.

Keep the runner identifiers distinct from the identifier of the app under test. A runner that
shares the app's identifier overwrites the app when it installs. The build knows no target, so
Bajutsu cannot check for that clash.

### Building

```bash
bajutsu runner build --device [--signing PATH] [--out DIR] [--force]
```

The command works in three steps:

1. Check the prerequisites: macOS, `xcodebuild`, and `xcodegen`. Manual signing adds the identity
   and every named profile. Each gap appears with its fix.
2. Copy the runner sources into a scratch directory, and rewrite that copy for the signing file.
   The checkout stays untouched. In a wheel install, the sources come from a copy that the wheel
   ships.
3. Build the scratch copy with `xcodebuild build-for-testing`, and print the path of the built
   `BajutsuRunner.xctestrun`.

The products land in Bajutsu's cache under `xcuitest-runner-device/<key>/Products/`. The key covers
every input that changes the signed products:

- the runner sources;
- the signing fields, with both resolved identifiers;
- the Xcode build version;
- for manual signing, the certificate hash and a digest of each profile file.

A certificate or profile renewed under the same name yields a new key. A repeat build with
unchanged inputs reuses the cached products, and `--force` rebuilds anyway. `--out DIR` copies the
`Products` directory to `DIR` as well. Device Farm packaging needs that copy, because the
`.xctestrun` expects its test bundles beside itself.

### What a run does

A `runsOn.ios.kind: device` run with no `driver.runner.testRunner` reads the signing file and
computes the same key. The run then uses the cached `.xctestrun`. An explicit `testRunner` still
wins over the cache.

A run never builds the runner itself. A signing build is slow, can raise a Keychain prompt, and
can register identifiers with Apple. Each miss fails at once and names the fix:

- **No signing file:** the error names both lookup locations.
- **No build for the current inputs:** the error names `bajutsu runner build --device`.
- **An expired profile:** the error names `bajutsu runner build --device --force`.

`bajutsu doctor` reports which signing file a device target would use. The report builds nothing and
computes no key.

## Two routes to a device cloud

Both routes share the real-device XCUITest core above. They differ in how a device is reserved and
how the run reaches it.

### Batch — AWS Device Farm

AWS Device Farm is a **batch** service. It runs your commands on its own host, which already has the
reserved device attached. It does not lend you a device to drive over the network.

Bajutsu reaches Device Farm through a CI-side submitter. The submitter packages the app plus your
scenarios. It uploads them with a test-spec that runs `bajutsu run --backend xcuitest` against the
reserved device's udid. It then collects the artifacts. The verdict still comes from Bajutsu's own
machine-checkable assertions, never from Device Farm's own pass/fail classification.

The [AWS Device Farm](devicefarm.md) page documents four things: the submitter, the test-spec it
renders, the re-signing caveats, and the manual proof-of-concept procedure. The iOS run reuses that batch
machinery and selects the iOS app upload and the `xcuitest` backend by platform.

### Live — an Appium endpoint provider

Some clouds reserve a single iOS device and expose it behind an Appium / WebDriver **endpoint**.
A self-hosted grid does this. Bajutsu models that reservation as a device provider on the live seam
([BE-0236](../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)).
Which grid serves a run is infrastructure, not a property of the target. The endpoint lives in an
`appium` environment of a worker capability file (`worker.yaml`), not in the target config
([BE-0452](../roadmaps/BE-0452-target-config-restructure/BE-0452-target-config-restructure.md)):

```yaml
version: 1
environment: appium
appium:
  endpoint: https://grid.example.com/wd/hub   # the reserved device's Appium / WebDriver address
  platform: ios                               # the one value today
drivers: [xcuitest, adb, playwright, fake]
```

Pass the file with `--worker-config` to `run` / `record` / `crawl` / `repl` / `audit` / `doctor` /
`bajutsu mcp`. Each target of the grid's platform then takes the endpoint as its device, in place of
a local udid. The scenario's other targets stay on this host. Bajutsu refuses a scenario with two
grid targets. `--udid` no longer accepts a URL, so name the grid in this file instead.

Bajutsu never boots the reserved device or installs onto it through `simctl`. The grid owns the
reservation. The device arrives with its build in place, and nothing needs releasing.

Bajutsu drives the endpoint over a live **W3C WebDriver transport**. The run routes the endpoint
**around** the `simctl` / `xcodebuild` udid machinery entirely. That machinery structurally cannot
carry a URL. A tap-and-assert flow works end to end.

The semantic actions that the local runner drives natively map onto Appium's XCUITest `mobile:`
commands over the endpoint:

- `tap`, `query`, screenshots, and condition waits;
- the input steps (`type` / `delete`, `swipe` / `scroll`);
- the two-finger `pinch` / `rotate` gestures.

As on the local backends, an ambiguous selector still fails **before any actuation**. The selector
resolves in Bajutsu, not on the grid.

Bajutsu narrows away up front what the WebDriver transport cannot drive. The real-device path
narrows the `simctl`-backed families the same way:

- **Text selection and paste** (`select` / `copy` / `paste`) have no first-class Appium XCUITest
  command. The local driver composes all three from taps on the system edit menu. That composition
  is not wired onto the live route.
- **`simctl` device control and permission grants** do not apply on the live route either, exactly
  as on any other real device (see the caveats below).

The preflight ([BE-0082](../roadmaps/BE-0082-capability-preflight-check/BE-0082-capability-preflight-check.md))
on the live route advertises what the transport actually drives and nothing more. It **skips** a
scenario that needs one of the narrowed capabilities, with a clear reason, before any device work.
The scenario does not fail late with `UnsupportedAction` mid-run.

A worked example config lives at
[`demos/showcase/live/showcase.live.config.yaml`](../demos/showcase/live/showcase.live.config.yaml).
It mirrors the local `showcase-swiftui` target but carries no local `subject.path` or
`driver.runner.testRunner`. The reserved device already holds the build. The live route speaks
WebDriver rather than the runner channel. The
[`demos/showcase/live/worker.yaml`](../demos/showcase/live/worker.yaml) file beside it names the
grid. Point that file's `endpoint` at your own grid, then run the shared showcase suite against it:

```bash
bajutsu run --target showcase-swiftui-live --config demos/showcase/live/showcase.live.config.yaml \
  --worker-config demos/showcase/live/worker.yaml
```

## Real-device caveats: re-signing and capability degradation

Running on a real device rather than the Simulator changes two things that Bajutsu accounts for up
front. Both are properties of physical hardware, not of any one cloud. They hold for every real
device the XCUITest backend drives. Such a device may be reached by `runsOn.ios.kind: device`
(Device Farm or a locally attached device) or over the live WebDriver route above.

- **Re-signing strips entitlements.** A device cloud re-signs the uploaded app with its own
  provisioning profile. The re-sign lets the app install on the reserved device. It drops the
  entitlements the new profile does not carry, commonly Push and App Groups. An app feature that
  depends on a dropped entitlement behaves as the re-signed build does. A scenario that asserts on
  such a feature should expect the re-signed behavior rather than the App Store one.
- **`simctl` device control and permission grants do not apply.** `simctl` backs Bajutsu's iOS
  device control and its permission grants, and `simctl` reaches the Simulator alone. The device
  control covers `setLocation`, the clipboard steps, `push`, `clearKeychain`,
  `background` / `foreground`, and the status-bar overrides. On a real device the XCUITest backend
  advertises neither. The preflight (BE-0082) **skips** a scenario that uses one, with a
  clear reason, before any device work. The scenario does not fail late with a `simctl` error mid-run. The on-device capabilities
  that the XCTest runner drives itself are unaffected: query, elements, screenshots, taps, and
  two-finger gestures.

The [AWS Device Farm](devicefarm.md#ios-re-signing-and-real-device-capabilities) page covers the
same two caveats in the batch context. It also lists the specific entitlement keys that Device
Farm's re-sign drops.

## References

- [AWS Device Farm](devicefarm.md) — the batch route: the submitter, the test-spec, and the manual
  proof of concept.
- [Drivers](drivers.md) — the `Driver` interface and the backends behind it, including XCUITest.
- [Configuration](configuration.md) — the `runsOn.ios.kind` target key.
- [Self-hosting](self-hosting.md) — the `appium` environment of a worker capability file.
- [Command reference](cli.md#runner) — `bajutsu runner build --device`.
- [BE-0019 — XCUITest backend](../roadmaps/BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md)
- [BE-0236 — device-cloud provider abstraction](../roadmaps/BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)
- [BE-0238 — iOS device-cloud execution](../roadmaps/BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)
- [BE-0456 — per-user signed device build of the XCUITest runner](../roadmaps/BE-0456-runner-device-signing-build/BE-0456-runner-device-signing-build.md)

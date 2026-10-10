**English** · [日本語](BE-XXXX-real-device-host-channels-ja.md)

# BE-XXXX — Reach a real iOS device's channels without a shared loopback

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-real-device-host-channels.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#2143](https://github.com/bajutsu-e2e/bajutsu/pull/2143) |
| Topic | Device-cloud execution |
| Related | [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md), [BE-0456](../BE-0456-runner-device-signing-build/BE-0456-runner-device-signing-build.md), [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture.md), [BE-0355](../BE-0355-native-z-position/BE-0355-native-z-position.md), [BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md) |
<!-- /BE-METADATA -->

## Introduction

Every channel between Bajutsu and an iOS app assumed that the host and the app share one loopback
address, `127.0.0.1`. That holds on the iOS Simulator, which runs as a process on the Mac. It does
not hold on a real iPhone or iPad, whose `127.0.0.1` is the device's own. With
`xcuitest.deviceType: device`, the host therefore could not reach the XCUITest runner at all, and a
run never got past the runner's health check.

This item replaces each loopback assumption on a real device with a transport that crosses the
host–device boundary, and keeps the Simulator path unchanged. Channels that the host opens to the
device ride usbmuxd, the macOS service that Xcode uses to reach a device attached over Universal
Serial Bus (USB). The one channel that the app opens to the host, the network collector, needs a host
address the device can route to. Bajutsu exchanges that address at run time and accepts an explicit
override, so a host whose address changes between runs still works.

## Motivation

A locally attached iPhone could not run a single scenario. The signed runner built, `xcodebuild`
launched it, and the runner launched the app. The host's `/health` poll then timed out at both 120
and 30 seconds. The runner binds its server to the device's `127.0.0.1`. The host polled its own
`127.0.0.1`, where nothing listened. The same gap affects the AWS Device Farm batch route
([BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md)), whose
host also drives a physical device.

The runner is the first of four channels with this flaw:

| Channel | Who listens | Direction | Launch environment key |
|---|---|---|---|
| XCUITest runner | runner, on the device | host → device | `BAJUTSU_RUNNER_PORT` |
| `nativeZ` responder ([BE-0355](../BE-0355-native-z-position/BE-0355-native-z-position.md)) | app, on the device | host → device | `BAJUTSU_ZORDER_PORT` |
| WebView bridge ([BE-0037](../BE-0037-webview-hybrid-support/BE-0037-webview-hybrid-support.md)) | app, on the device | host → device | `BAJUTSU_WEBVIEW_PORT` |
| Network collector ([BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture.md)) | Bajutsu, on the host | device → host | `BAJUTSU_COLLECTOR` |

The control channel ([BE-0365](../BE-0365-in-app-control-channel/BE-0365-in-app-control-channel.md)) and the screen-transition reports both ride the collector, so they
share its fate. On a real device, the last three channels fail without a clear error. `nativeZ` reads
as absent, a WebView reads as empty, and a network assertion sees no exchanges.

The observable outcome: `bajutsu run` with `deviceType: device` passes the showcase `firstlook`
scenario on a USB-attached iPhone. A network scenario on that iPhone records its exchanges instead
of none. When no host address can be offered, the run fails before it touches the device and names
the configuration key that fixes it.

## Detailed design

### Host-to-device channels: a usbmuxd bridge

usbmuxd listens on `/var/run/usbmuxd` and multiplexes TCP connections to an attached device. A
`Connect` request names a device and a port, and the reply turns the socket into a raw byte stream
to that port on the device's loopback. `iproxy` and Xcode use the same request.

Bajutsu speaks the usbmuxd plist protocol directly in a small module,
`environments/xcuitest/_usbmux.py`, so the host needs no extra tool or package. A `UsbmuxForwarder`
listens on the host's `127.0.0.1` and tunnels each accepted connection to a device port.

- **Device lookup.** The forwarder finds the device by its udid, which usbmuxd lists with or without
  the hyphen. A USB attachment is preferred over a network one. The lookup runs per connection,
  because usbmuxd's handle changes when the cable is replugged.
- **Handshake ceiling.** Each request and reply with usbmuxd is bounded at five seconds. A joined
  tunnel carries no idle deadline, since the driver keeps it open between calls.
- **Refusal.** A port with no listener yet is refused. The forwarder closes the host connection,
  which the runner's health poll reads as not-ready and retries. Any other failure, such as an
  unplugged device, logs a warning that names the device.

The three host-to-device channels use the forwarder in two shapes:

- **The runner** binds whatever port Bajutsu tells it. The forwarder binds an ephemeral host port
  first, and that same number becomes the device port. One number names both ends, so no second
  allocation can race the first. The bridge lives for one spawn attempt.
- **The `nativeZ` responder and the WebView bridge** bind a port the run injected before the device
  started. The forwarder targets that fixed device port from a separate ephemeral host port. Each
  bridge lives for one scenario's lease.

A device that usbmuxd does not list fails the runner spawn at once, with that reason, instead of
timing out. The WebView bridge fails its lease the same way, since a scenario may drive a WebView.
The `nativeZ` field is diagnostic, so its bridge degrades instead. A `nativeZ` bridge that cannot
open leaves `nativeZ` absent with a warning, the same result as an app without the responder.

### Device-to-host channel: an exchanged host address

usbmuxd carries connections in one direction only, from the host to the device. The app reports to
the collector on its own initiative, so it needs an address of the host that it can route to. That
address changes with the network, so a fixed value in a config file breaks when the host's address
changes. Bajutsu therefore offers the app a list of candidate addresses at each run, and the app
picks one that answers.

1. **Candidates.** On a real device, the host resolves its candidate addresses in this order:
   - The `BAJUTSU_HOST_ADDRESS` environment variable, when set.
   - The target's `xcuitest.hostAddress`, when set.
   - Otherwise, every routable IPv4 and IPv6 address of the host's active interfaces, from
     `ifconfig`. IPv6 stays in on purpose: the CoreDevice tunnel that Xcode uses for a real-device
     test is IPv6, and it may be the one route from the device back to the host.

   An explicit value is one address or a comma-separated list. An environment value wins over the
   config, so a run whose host changes per job, such as on Device Farm, overrides the config without
   editing it.
2. **Fail fast.** When the run needs the collector and no candidate exists, the run fails before any
   device work. The error names `xcuitest.hostAddress` and `BAJUTSU_HOST_ADDRESS`.
3. **Binding.** On a real device, the collector binds every IPv4 and IPv6 interface (`::`, falling
   back to `0.0.0.0` on a host without IPv6) instead of `127.0.0.1`. The collector already requires the per-run token on every request, so a device on
   the local network that lacks the token gets a 401. The Simulator keeps the loopback binding.
4. **Injection.** `BAJUTSU_COLLECTOR` carries one URL per candidate, separated by commas. A single
   URL, as on the Simulator, keeps today's format.
5. **Selection in the app.** BajutsuKit splits the value. Unless the value is the Simulator's
   loopback URL, it probes each candidate with an authenticated `GET /ping` in parallel, even a
   single one. It keeps the first candidate, in the host's
   order, that answered 204. The search runs in the background, so the launch never waits for it.
   It retries every second for up to two minutes, because a fresh install holds every
   local-network connection until the Local Network prompt is answered. Reports made meanwhile wait
   in a buffer of 1,000 and go out in order once a collector answers. With no answer by the
   deadline, the buffer is dropped and the app reports nothing, as an app without a collector does.
   Either loss, an overflow or a discarded buffer, goes to the device log with its count.
6. **Interceptor guard.** BajutsuKit's `URLProtocol` skips loopback requests, so its own report
   POSTs are never intercepted and re-reported. A collector on a network address would slip past
   that guard. The guard now also skips the host and port of every offered candidate, since the
   search probes them all.

The collector gains one route, `GET /ping`, which answers 204 to an authenticated request and
changes no state. The existing `GET /commands` cannot serve as the probe, because it drains the
control channel's queue.

### Answering the Local Network prompt: `localNetwork`

The device-to-host route raises the iOS Local Network prompt, so `systemAlertHandling.rules` gains a
`localNetwork` prompt. Its buttons are the notification prompt's ("Allow" / "Don’t Allow"), so the
guard also reads the alert's title. The runner's `/systemAlert/query` adds one untappable entry per
alert, carrying its title. The guard reduces that title to Apple's template, with every “…”-quoted span
(the app's name) replaced by “%@”, and adds it to the matched labels with a `title: ` prefix.

- `localNetwork` names that marker among its identifying labels.
- `notifications` excludes it, so neither rule answers the other's prompt.
- The interruption policy carries a rule's exclusions to the runner, which builds the same marker.
- A `handleSystemAlert` step that names a prompt checks the title before it taps. The runner can
  also tap a prompt during an interruption. The step runs the same check before it counts such a
  tap as its own. The runner's drain lists the labels that matched each tapped alert
  (`tappedAlerts`).

The templates come from `NetworkExtension.framework` (`APP_WANTS_LOCAL_NETWORK_HEADER`), identical on
iOS 18.6, 26.5, and 27.0, in English and Japanese. A scenario or target declares
`{ prompt: localNetwork, choice: grant }` like any other prompt; nothing answers it implicitly.

The prompt is raised during launch, before the first step, while the guard otherwise first looks
inside a pending wait. A first `tap` would land on it. The guard therefore runs its native probe once
before a scenario's first step. That probe answers a declared prompt already on screen, records it
on the first step, and touches neither the app's own buttons nor an undeclared alert.

### Checking the routes: `bajutsu doctor`

`bajutsu doctor --environment-only` reports both routes for a `deviceType: device` target, in the
CLI and the serve panel alike.

- **usbmuxd, as a runnability check.** A real device needs no booted Simulator, so the
  booted-Simulator check gives way to one asking whether usbmuxd lists the device, and over which
  attachment. A device usbmuxd does not list fails it, and doctor exits non-zero. The default
  `--udid booted` names a Simulator, so on a real-device target that check asks for `--udid`.
- **Host addresses, as information.** Doctor lists the addresses the app would receive, with their
  source. A missing one leaves the exit status alone, since only a network-recording scenario needs
  one.

A device-cloud job can run doctor before `bajutsu run`, so a missing route shows up as one line
instead of a startup timeout.

### Device Farm

AWS Device Farm runs the test spec on its own Mac host, with the reserved device attached. Nobody on
this project has a Device Farm account with a real iOS device to measure that host. The design
therefore rests on two expectations and gives each a check:

- **usbmuxd lists the reserved device.** `xcodebuild test-without-building` reaches the device
  through the same service, so the device should be listed. The doctor line above confirms it in
  one job. If usbmuxd does not list it, the runner's spawn fails at once with that reason.
- **The device routes to one host address.** The device's Wi-Fi network may not reach the host. The
  CoreDevice tunnel's IPv6 address is the likelier route, and the candidate list includes it. If no
  candidate answers, the run records no exchanges. `BAJUTSU_HOST_ADDRESS` then pins the address
  once a diagnostic run has found it.

The runner's port is a number that was free on the host, reused on the device. A device process that
already holds that number makes the runner's bind fail. The runner then exits, and the existing
cold-spawn retry allocates another number.

Two device-side properties of the host-address route carry over to every real device:

- **Local Network permission.** From iOS 14, an app asks before its first local-network connection,
  and the probe is one. The device must grant the permission. A fresh install, as on every Device
  Farm job, starts without it. Until then, the probe gets no answer, and the system alert can cover
  the first screen. The manual proof measured this on a fresh install of the showcase app. That app
  declares neither `NSLocalNetworkUsageDescription` nor an App Transport Security (ATS) exception.
  iOS still showed the prompt. Once the guard granted it, cleartext HTTP to the host's IP address
  went through.
- **Cleartext.** The probe and every report travel as plain HTTP, token included. The docs advise
  pinning `BAJUTSU_HOST_ADDRESS` to the CoreDevice tunnel's address on a shared network.

Explicit addresses are validated when the run resolves them. An entry with a port, a zone, or a
prefix fails before any device work, naming the entry and its source. A bracketed IPv6 literal is
unbracketed. When `ifconfig` itself fails, the reason stays in the source that the error and doctor
print.

### Where the hooks live

The pool stays platform-agnostic. It asks the lease's environment through two new methods on the
`RunEnvironment` protocol:

- `collector_host(eff)` returns the address the collector binds and the addresses the app receives.
  Every environment returns the loopback, except the XCUITest environment for a real device.
- `reach_device_port(eff, port)` returns the host port that reaches a device port, plus its
  teardown. Every environment returns the port itself and a no-op, except the XCUITest environment
  for a real device, which opens a usbmuxd bridge.

The `nativeZ` bridge stays inside the XCUITest environment, because that environment already builds
the `nativeZ` client from the launch environment.

### Out of scope

- **Binding a device-side listener to a network address.** The runner has no authentication, so
  exposing it to the network would hand control of the device to anyone on it. Every host-to-device
  channel stays on the device's loopback and rides usbmuxd.
- **A device that usbmuxd does not list.** The fallback would bridge over the CoreDevice tunnel
  instead, with the runner bound beyond loopback and given a per-run token. That is a follow-up item,
  to open only if a Device Farm diagnostic run shows that usbmuxd does not list the device.
- **Keeping the token off an untrusted candidate.** The probe sends the token to every candidate. An
  unauthenticated `/ping` returning a per-run nonce would send it only to the proven collector. That
  change is a follow-up, if a shared-network deployment needs it.
- **Verifying that the app reached the collector.** The host does not wait for the app's probe. A
  device that cannot route to any candidate records no exchanges, and a network assertion then fails
  on the empty record.

### Verification

The fast gate covers each piece without a device. A fake usbmuxd on a UNIX socket exercises the
framing, lookup, refusal, handshake ceiling, forwarding, and teardown. The environment tests check
that the runner's and the diagnostic channels' ports reach the right bridge, that a failed spawn
closes its bridges, and that the Simulator opens none. The pool tests check the collector's binding,
the injected URL list, and the fail-fast error. The candidate-resolution tests cover the
environment, config, and interface sources. The BajutsuKit candidate selection is Swift, which the
fast gate does not compile; the iOS end-to-end job builds it.

The real-device proof is manual: the showcase `firstlook` scenario on a USB-attached iPhone, then a
network scenario on the same device.

## Alternatives considered

- **`pymobiledevice3` for forwarding.** The package forwards ports over usbmuxd and the CoreDevice
  tunnel. It is a large dependency, which this item would add for one socket splice.
- **Bind the device-side listeners to the CoreDevice tunnel.** iOS 17 and later reach a device over
  an IPv6 tunnel, so the host could dial the device's tunnel address. The runner would then listen
  beyond loopback without authentication, the exposure ruled out above.
- **A fixed host address in config alone.** Simple, but a host on DHCP or a per-job cloud host gets a
  different address each time, so the run would break without a config edit. The exchange keeps the
  explicit override for the cases that need it.
- **Relay the collector through the runner.** The app could post to a port that the runner serves,
  and the host could drain it over usbmuxd. That relay needs a new store-and-forward protocol in the
  runner, while the address exchange reuses the collector unchanged.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] usbmuxd client and `UsbmuxForwarder`, with a fake-usbmuxd test suite.
- [x] Runner port bridged on a real device; spawn failure closes the bridge.
- [x] `nativeZ` responder bridged per lease; a failed bridge degrades to absent.
- [x] WebView bridge reached through `reach_device_port`.
- [x] Collector host: candidate resolution, all-interface binding, URL list, fail-fast error, `GET /ping`.
- [x] BajutsuKit: candidate selection by authenticated probe, and the interceptor guard.
- [x] `bajutsu doctor`: the usbmuxd and host-address lines for a real-device target.
- [x] `localNetwork` prompt: title marker, runner title entries, and exclusions in the
  interruption policy.
- [x] Scenario-entry native probe for a declared prompt raised during launch.
- [x] BajutsuKit: background collector search with buffered reports.
- [x] Docs in both languages: `docs/ios-device-cloud.md`, `docs/configuration.md`,
  `docs/devicefarm.md`, `docs/architecture.md`.
- [x] Manual real-device proof: `firstlook` on a USB-attached iPhone, then a network scenario.
- [ ] Device Farm diagnostic run: `bajutsu doctor --environment-only`, then `firstlook`, on a
  reserved iOS device.

Log:

- 2026-10-09 — [#2143](https://github.com/bajutsu-e2e/bajutsu/pull/2143) landed every code unit and the docs.
  The first commit bridged the runner alone, and `firstlook` passed on an iPhone 14 Pro (iOS 27.0.1)
  over USB at a 30-second startup ceiling. The `nativeZ`, WebView, and collector units followed after
  the device was unplugged, so the real-device proof stays open for them. Both manual units stay
  open, so the item stays In progress.

- 2026-10-10 — The rest of the real-device proof, on the same iPhone over USB. A first run without
  a rule stopped at the Local Network prompt. With `{ prompt: localNetwork, choice: grant }`, the
  guard identified the prompt by its title and tapped "Allow". After the grant, `firstlook` and
  `network_mock` both passed, and `network_mock` recorded its stubbed `POST /post` (201) over the
  host-address route. On a fresh install, two gaps followed: the scenario's first tap landed on the
  prompt, and the app's two-second probe at launch timed out while the prompt held its connections.
  The scenario-entry probe and the background search with buffered reports close both.
- 2026-10-10 — The manual real-device proof, at commit 61633cb66, on the same iPhone 14 Pro
  (iOS 27.0.1) over USB, from a fresh install of the showcase app:
  - `bajutsu doctor --environment-only` reported usbmuxd reaching the device over USB, and five host
    addresses from the host's interfaces.
  - `network_mock` passed. The scenario-entry probe answered the Local Network prompt with "Allow"
    on step 0, and `network.json` recorded the stubbed `POST /post` (201).
  - `firstlook` passed with a 30-second startup ceiling. Its `elements.json` carried `nativeZ` on the
    identified elements, so the `nativeZ` bridge answered.
  - A probe through `reach_device_port` reached the app's WebView bridge (`GET /webview/dom`
    answered 200 with an empty list), while the same port on the host's loopback refused the
    connection.
  The Device Farm diagnostic run stays open.

## References

- [BE-0238](../BE-0238-ios-device-cloud-execution/BE-0238-ios-device-cloud-execution.md): the
  real-device targeting this item makes reachable.
- [BE-0456](../BE-0456-runner-device-signing-build/BE-0456-runner-device-signing-build.md): the
  signed device runner that the bridge connects to.
- [BE-0283](../BE-0283-android-network-capture/BE-0283-android-network-capture.md): the
  collector and the Android `adb reverse` tunnel that this item's host address is the iOS answer to.

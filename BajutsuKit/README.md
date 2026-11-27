# BajutsuKit

BajutsuKit is the in-app network observation package for [bajutsu](../). This Swift package belongs
in test and debug builds alone. BajutsuKit captures the app's `URLSession` traffic and reports each
request and response to bajutsu. A scenario can then verify the traffic with a `request` assertion.

## Why in-app capture

A Simulator app runs as an ordinary Mac process and shares the Mac's loopback. When bajutsu runs a
scenario, it starts a collector on `127.0.0.1:<port>` for that run. bajutsu then injects the
collector's address into the app. The address travels as a Uniform Resource Locator (URL) in the `BAJUTSU_COLLECTOR` launch env.
BajutsuKit installs a `URLProtocol` subclass that records each exchange. Each record holds the
request's method, URL, and path. It also holds the status, headers, bodies, and timing. BajutsuKit
then sends each record to the collector in an HTTP request.

Compared to a proxy, in-app capture has three advantages:

- It sees traffic after Transport Layer Security (TLS) decryption. It needs no certificate
  authority (CA) install and has no certificate-pinning conflict.
- It works under any backend (backend-agnostic).
- bajutsu reads the exchanges programmatically. A network inspector that merely displays traffic
  on screen offers no such access.

## Integrate

Add the package, then call `startIfEnabled()` once, at app launch:

```swift
import BajutsuKit

@main
struct MyApp: App {
    init() {
        BajutsuNet.startIfEnabled()   // no-op unless a BAJUTSU_* launch env asks for a feature
    }
    // ...
}
```

`startIfEnabled()` is the single entry point for every BajutsuKit feature. Each feature has its own
launch-env key. The function stays inert unless the launch env carries one of the keys:

| Key | What it activates |
|---|---|
| `BAJUTSU_COLLECTOR` | network capture, and the screen-transition swizzle alongside it |
| `BAJUTSU_MOCKS` | request stubbing |
| `BAJUTSU_TOUCH_MARKERS` | the in-app touch visualization, which swizzles `-[UIWindow sendEvent:]` and draws a layer over the app's own window |
| `BAJUTSU_ZORDER_PORT` + `BAJUTSU_ZORDER_TOKEN` | the `nativeZ` responder (BE-0355), a loopback-only HTTP listener that reports each element's front-to-back position and answers only requests bearing the token |
| `BAJUTSU_CONTROL_CHANNEL=1` | the in-app control channel ([below](#in-app-control-channel-be-0365)), and only in a build compiled with `BAJUTSU_ENABLE_CONTROL_CHANNEL` |

bajutsu writes these keys on `run`, so the function is inert in normal use. Network capture
activates when the launch env carries `BAJUTSU_COLLECTOR`, and stays off otherwise. Capture
registers globally, which covers `URLSession.shared`. It also swizzles
`URLSessionConfiguration.protocolClasses`, which covers app-built sessions too. BajutsuKit never
intercepts the collector's own address, so the report request cannot loop. A Simulator app reports
to the loopback. A real device reports to the address of the machine running bajutsu.

## Safety

BajutsuKit captures headers and bodies, so use it in **test/debug builds only**. Keep it out of a
release build with a compilation condition, and treat that gate as mandatory. The launch-env keys
are not a safe alternative. Suppose a release binary still calls `startIfEnabled()` at launch. An
environment variable can then turn on any feature in the table above. Three features carry
particular risk:

- `BAJUTSU_TOUCH_MARKERS` installs a process-wide event interception. It also draws over the
  app's own window.
- The `nativeZ` responder opens a listening socket that names every element on screen. The socket listens
  on loopback alone and requires a bearer token. Even so, iOS loopback is not isolated between apps.
- The control channel lets a process outside the app change the app's behavior while it runs. The
  launch env cannot reach this feature by itself. Its compilation condition,
  `BAJUTSU_ENABLE_CONTROL_CHANNEL`, keeps the channel out of any build that does not ask for it.
  In a release build that links BajutsuKit, every other feature stays reachable through the
  environment.

Configure bajutsu `redact` to mask secrets in the written evidence (`network.json`).

## Coverage

`URLProtocol` sees HTTP traffic, encrypted or not, when it goes through `URLSession`. It sees
nothing else. It does **not** see raw sockets or `WKWebView` traffic. Nor does it
see `Network.framework` (`NWConnection`) traffic. Third-party SDKs that bypass `URLSession` stay
invisible too.

## Then, in a scenario

```yaml
steps:
  - tap: { id: net.fetch }
  - wait: { until: settled, timeout: 8 }
expect:
  - request: { method: GET, status: 200 }            # at least one matching exchange
  - request: { method: POST, pathMatches: "^/login", count: 1 }
```

## Screen-transition observation (BE-0310)

`BajutsuNet.startIfEnabled()` also activates the `BajutsuScreen` component. `BajutsuScreen` swizzles
`UIViewController.viewDidAppear(_:)` for that purpose. The swizzle uses the
`method_exchangeImplementations` idiom. `BajutsuURLProtocol.swift` already uses the same idiom for
network capture. Each completed appearance reports a transition to the collector's `/transitions`
endpoint.

`viewDidAppear` runs after the appearance transition settles. That timing matches what readiness
wants. The swizzle covers UIKit and SwiftUI alike. A `UIHostingController` backs each of
these SwiftUI transitions:

- a `NavigationStack` push
- a `.sheet` / `.fullScreenCover` presentation
- a tab switch

That controller's `viewDidAppear` runs when the transition finishes. The swizzle thus gives two
consumers a positive signal in place of tree-diff polling. The consumers are bajutsu's post-launch
readiness gate and the `settled` wait.

The swizzle has one boundary. An in-place SwiftUI view swap *within* a single hosting controller
posts no appearance. No new controller appears in that case. That swap is a within-screen update, not a
container transition. BE-0299 already documents the swap as out of scope.

Like network capture, the swizzle stays inert unless the launch env carries `BAJUTSU_COLLECTOR`. It
needs no change to app screens, because it swizzles a framework method and never app screen code.
Both waits keep their tree-diff fallback. A target that does not link BajutsuKit thus sees no
change.

## In-app control channel (BE-0365)

Every other feature here reads its launch env once, when `startIfEnabled()` runs. The control
channel lets bajutsu change one of BajutsuKit's own capabilities while a scenario runs. bajutsu
queues a command on the collector the app already reports to. The app polls the collector's
`GET /commands` endpoint about every 0.15 seconds. It applies each command it drains from the
queue. For each command, the
app reports on `/commands/ack` whether it applied the command. The app opens no socket, and the
collector's per-run token guards the commands.

The channel carries two commands today:

| Capability | What the command does |
|---|---|
| `touch_visualization` | hides or restores the touch markers around the one capture a `visual` assertion compares |
| `stub_table` | replaces the whole stub table, for a scenario's `setMocks` step |

The channel has two gates, and both must open:

1. The build compiles the channel in. Pass `-DBAJUTSU_ENABLE_CONTROL_CHANNEL` to the Swift compiler
   for BajutsuKit. With SwiftPM, pass `-Xswiftc -DBAJUTSU_ENABLE_CONTROL_CHANNEL` to the build
   command instead. Without the condition, the channel's code is absent from the binary.
2. The launch env sets `BAJUTSU_CONTROL_CHANNEL=1`. bajutsu writes that key itself when a scenario
   needs the channel. Any other value, or no value, leaves the channel idle with no poll timer.
   The channel also needs `BAJUTSU_COLLECTOR`, because the collector is what it polls.

The channel addresses bajutsu's own instrumentation alone, never the app's state or navigation. No
assertion reads from the channel, so the app never takes part in its own verdict. The full
description is in [the network guide](../docs/network.md#the-in-app-control-channel).

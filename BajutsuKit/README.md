# BajutsuKit

BajutsuKit is the in-app network observation package for [bajutsu](../). It is a test/debug-only Swift
package that captures the app's `URLSession` traffic and reports each request/response to bajutsu, so
a scenario can verify the traffic with a `request` assertion.

## Why in-app capture

A Simulator app runs as a host process and shares the Mac's loopback. When bajutsu runs a scenario, it
starts a collector on `127.0.0.1:<port>` and injects the collector's URL into the app through the
`BAJUTSU_COLLECTOR` launch env. BajutsuKit installs a `URLProtocol` that records each exchange
(method / url / path / status / headers / bodies / timing) and POSTs it to the collector.

Compared to a proxy, in-app capture has three advantages:

- It sees traffic after TLS, so it needs no CA install and has no certificate-pinning conflict.
- It works under any backend (backend-agnostic).
- bajutsu reads the exchanges programmatically, unlike RocketSim's GUI-only network inspector.

## Integrate

Add the package, then call `startIfEnabled()` once, early:

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
launch-env key, so the function is inert unless one of the keys is set:

| Key | What it activates |
|---|---|
| `BAJUTSU_COLLECTOR` | network capture, and the screen-transition hook alongside it |
| `BAJUTSU_MOCKS` | request stubbing |
| `BAJUTSU_TOUCH_MARKERS` | the in-app touch visualization, which swizzles `-[UIWindow sendEvent:]` and draws a layer over the app's own window |
| `BAJUTSU_ZORDER_PORT` + `BAJUTSU_ZORDER_TOKEN` | the `nativeZ` responder (BE-0355), a loopback-only HTTP listener that reports each element's front-to-back position and answers only requests bearing the token |

bajutsu sets these keys on `run`, so the function is inert in normal use. Network capture activates
only when `BAJUTSU_COLLECTOR` is present. It registers globally, which covers `URLSession.shared`, and
it swizzles `URLSessionConfiguration.protocolClasses`, which covers app-built sessions too. The
collector host (`127.0.0.1` / `localhost`) is never intercepted, so the report POST cannot loop.

## Safety

BajutsuKit captures headers and bodies, so use it in **test/debug builds only**. Gate it out of
release with a compilation condition rather than leaning on the launch-env keys alone. A release binary
that still calls `startIfEnabled()` can have any feature in the table above turned on by an
environment variable. Two features carry particular risk:

- `BAJUTSU_TOUCH_MARKERS` installs a process-wide event hook and draws over the app's own window.
- The `nativeZ` responder opens a listening socket that names every element on screen. The socket is
  loopback-only and bearer-token gated, but iOS loopback is not isolated between apps.

Configure bajutsu `redact` to mask secrets in the written evidence (`network.json`).

## Coverage

`URLProtocol` sees `URLSession`-based HTTP(S). It does **not** see raw sockets, `Network.framework`
(`NWConnection`), `WKWebView`, or third-party SDKs that bypass `URLSession`.

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

`BajutsuNet.startIfEnabled()` also activates `BajutsuScreen`. `BajutsuScreen` swizzles
`UIViewController.viewDidAppear(_:)`, using the same `method_exchangeImplementations` idiom that
`BajutsuURLProtocol.swift` already uses. Every completed view-controller appearance reports a
transition to the collector's `/transitions` endpoint.

`viewDidAppear` fires only after the appearance transition settles, which matches the timing that
readiness wants. The hook covers UIKit and SwiftUI equally. A `NavigationStack` push, a `.sheet` /
`.fullScreenCover` presentation, and a tab switch are each backed by a `UIHostingController` whose
`viewDidAppear` fires when the transition finishes. The hook thus gives bajutsu's post-launch readiness
gate and the `settled` wait a positive signal in place of tree-diff polling.

The hook has one boundary. An in-place SwiftUI view swap *within* a single hosting controller (no new
controller presented) posts no appearance. That swap is a within-screen update, not a container
transition, and BE-0299 already documents it as out of scope.

Like network capture, the hook is inert unless `BAJUTSU_COLLECTOR` is set, and it needs no app-screen
change: it hooks a framework method, never app screen code. A target that does not link BajutsuKit is
unaffected, because both waits keep their tree-diff fallback.

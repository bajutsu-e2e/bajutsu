# BajutsuKit

In-app network observation for [bajutsu](../). A test/debug-only Swift package that
captures the app's `URLSession` traffic and reports each request/response to bajutsu so
a scenario can verify it with a `request` assertion.

## Why in-app capture

A Simulator app runs as a host process and shares the Mac's loopback. When bajutsu runs
a scenario it starts a collector on `127.0.0.1:<port>` and injects its URL into the app
via the `BAJUTSU_COLLECTOR` launch env. BajutsuKit installs a `URLProtocol` that records
each exchange (method / url / path / status / headers / bodies / timing) and POSTs it to
the collector.

Compared to a proxy: it sees traffic after TLS (no CA install, no certificate-pinning
conflict), it works under any backend (backend-agnostic), and bajutsu reads the exchanges
programmatically (unlike RocketSim's GUI-only network inspector).

## Integrate

Add the package and call `startIfEnabled()` once, early:

```swift
import BajutsuKit

@main
struct MyApp: App {
    init() {
        BajutsuNet.startIfEnabled()   // no-op unless BAJUTSU_COLLECTOR is set
    }
    // ...
}
```

`startIfEnabled()` activates capture only when `BAJUTSU_COLLECTOR` is present (bajutsu
sets it on `run`), so it is inert in normal use. It registers globally (covering
`URLSession.shared`) and swizzles `URLSessionConfiguration.protocolClasses` so app-built
sessions are covered too. The collector host (`127.0.0.1` / `localhost`) is never
intercepted, so the report POST can't loop.

## Safety

It captures headers and bodies — **test/debug builds only**. Gate it out of release (or
rely on the `BAJUTSU_COLLECTOR` guard), and configure bajutsu `redact` to mask secrets in
the written evidence (`network.json`).

## Coverage

`URLProtocol` sees `URLSession`-based HTTP(S). It does **not** see raw sockets /
`Network.framework` (`NWConnection`), `WKWebView`, or third-party SDKs that bypass
`URLSession`.

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

`BajutsuNet.startIfEnabled()` also activates `BajutsuScreen`, which observes
`UIAccessibility.screenChangedNotification` — the notification UIKit posts automatically
after a standard container transition completes (a navigation push/pop, a modal
presentation/dismissal, a tab switch), covering SwiftUI's `NavigationStack` too, since it
is `UINavigationController`-backed underneath. Each observed transition is reported to the
collector's `/transitions` endpoint, giving bajutsu's post-launch readiness gate and the
`settled` wait a positive signal in place of tree-diff polling. Like network capture, it is
inert unless `BAJUTSU_COLLECTOR` is set and needs no app-screen change: it never observes a
notification an app must post by hand (e.g. `UIAccessibility.pageScrolledNotification`). A
target that does not link BajutsuKit is unaffected — both waits keep their tree-diff
fallback.

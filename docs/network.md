**English** · [日本語](ja/network.md)

# Network observation (`request` assertions)

> Verify the HTTP(S) traffic an app makes, as a step/expect assertion. On iOS and Android,
> observation is **in-app**: the app reports each exchange to a collector bajutsu runs. On web,
> Playwright observes the page's traffic natively, with no app-side reporting involved. On every
> platform, a `request` assertion checks the accumulated exchanges.
>
> Implementation:
>
> - `bajutsu/common/evidence/network.py`: the model and the collector
> - `bajutsu/assertions/network.py`: the `request` evaluation
> - `bajutsu/common/drivers/web_network.py`: the Playwright-native collector and mocking (BE-0054)
> - the in-app software development kit (SDK): [`BajutsuKit`](../BajutsuKit/README.md) on iOS and
>   [`BajutsuAndroid`](../BajutsuAndroid/README.md) on Android
>
> The web (Playwright) backend needs no SDK.

Related: [scenarios](scenarios.md) · [evidence](evidence.md)

---

## How traffic is observed

A Simulator app runs as a host process and shares the Mac's loopback. Traffic observation then
proceeds in three stages:

1. On `run`, bajutsu starts a **collector** (`NetworkCollector`) on `127.0.0.1:<port>` and
   injects its URL into the app via the `BAJUTSU_COLLECTOR` launch env, together with a
   per-run shared token via `BAJUTSU_COLLECTOR_TOKEN`.
2. The app (linked with **BajutsuKit**) installs a `URLProtocol` that records each
   request/response and POSTs it to the collector **after TLS (Transport Layer Security)**. The
   capture uses no proxy and no certificate authority (CA). It works regardless of which backend
   drives the app, and programs can read it. Each POST proves the token. The collector
   rejects any request that does not (401), so another local process cannot inject fabricated
   exchanges into the run's evidence.
   - bajutsu always announces `BAJUTSU_COLLECTOR_AUTH=hmac`. A current BajutsuKit then signs each
     request with the token.
   - An older BajutsuKit sends the token as an `Authorization: Bearer` header instead. A loopback
     collector still accepts that header.
   - On a real device the collector accepts signed requests alone
     ([the real-device guide](ios-device-cloud.md#how-the-device-to-host-route-is-authenticated)).
3. The collector keeps the exchanges in memory. A step's `request` assertion is evaluated
   against them in real time, and bajutsu writes them (redacted) to `<sid>/network.json` as
   scenario evidence.

`--no-network` disables the collector. Apps without the SDK report nothing, so the collector stays
empty; the feature is opt-in per app.

On iOS, the same collector also receives screen-transition events on a separate `/transitions`
endpoint (BE-0310). `BajutsuKit`'s `BajutsuScreen` swizzles `UIViewController.viewDidAppear(_:)`
and reports each completed view-controller appearance there. The collector keeps these events in
their own store, independent of the network exchanges above. The signal does not concern the
`request` assertion: the post-launch readiness gate and the `settled` wait consult it instead. See
[run-loop](run-loop.md#waits-condition-waits-only).

### Android

Android works the same way, with two differences (BE-0283).

- The app links [`BajutsuAndroid`](../BajutsuAndroid/README.md) and adds `BajutsuNet.interceptor()`
  to its OkHttp client. Android has no single OS-level HTTP hook like iOS's `URLProtocol`, so the
  interceptor is per-client and captures **OkHttp** traffic.
- The emulator's `127.0.0.1` is its own loopback, not the host's. bajutsu bridges the collector to
  the device with `adb reverse`, using the same port on both sides so the injected
  `BAJUTSU_COLLECTOR` URL resolves unchanged.

That one port number must be bindable on **both** sides, so an Android lane takes the collector's
port from a small reserved band rather than letting the operating system pick a free one. An
operating-system pick comes from the host's ephemeral range, which is also the range the emulator
hands out to its own sockets. The pick periodically names a port the guest already holds, and the
bridge then fails to bind.

The band applies to Android alone. The iOS Simulator shares the Mac's loopback, bridges nothing,
and keeps the operating-system pick. The collector, the token check, and the assertion pipeline are
identical.

### Web

Web (Playwright) needs none of this in-app machinery, because Playwright already sees every
request the page makes (BE-0054). `PlaywrightDriver.network_collector()` hooks the page's
`requestfinished` event directly into the same `NetworkExchange` model. The page needs no
`BAJUTSU_COLLECTOR` launch env, no port, and no token; the driver is the collector. The
resulting `NetworkExchange` records match the same `request` assertion as on iOS and Android.

> The in-app path is the chosen mechanism. RocketSim's GUI network inspector and a TLS-intercepting
> proxy were both rejected. The inspector is not exposed on its CLI, so automated assertions cannot
> use it. The proxy needs a CA install and breaks on certificate pinning. See the design notes.

## The `request` assertion

`request` is an assertion kind (alongside `exists` / `value` / `count` / …). Match fields
are AND-ed. Each plain `request` corresponds to **one** observed exchange. Each `request`
assertion in a block pairs **one-to-one** with a distinct exchange, so two `request` lines
need two separate requests. `count` is the exception: an explicit aggregate (exact when set,
otherwise the lone matcher needs at least one).

```yaml
expect:
  - request: { method: POST, path: /login, status: 200, bodyMatches: "\"user\"" }  # one login POST
  - request: { method: GET, urlMatches: "q=hello&n=42" }                           # a *different* request
  - request: { pathMatches: "^/items", count: 2 }                                  # aggregate: exactly 2
```

| field | meaning |
|---|---|
| `method` | HTTP method (case-insensitive) |
| `url` | exact full URL (the endpoint) |
| `urlMatches` | regex/substring over the full URL |
| `path` | exact URL path (query ignored) |
| `pathMatches` | regex over the path |
| `query` | key/value dictionary over the URL-decoded query parameters, order-independent (BE-0463) |
| `body` | key/value dictionary over the JavaScript Object Notation (JSON) request body, nested maps and lists included (BE-0463) |
| `status` | response status code |
| `bodyMatches` | regex/substring over the raw request body |
| `count` | exact number of matching exchanges; an aggregate, exempt from the 1:1 rule (omit ⇒ at least one) |

## Deterministic mocks

A scenario's `mocks` make the network deterministic. When an outgoing request matches a
rule, the platform's collector returns the canned response **instead of hitting the network**, so
a test never depends on a live server and runs offline.

- On iOS, BajutsuKit serves the stub inside the URL protocol, after TLS, with no proxy or CA.
- On web, `WebNetworkCollector` fulfills the stub in-process via Playwright's `page.route`.
- BajutsuAndroid observes but does not yet stub (a follow-up to BE-0283).

On iOS and web, the stub is still observed: `network.json` lists it flagged `mocked`, and
`request` assertions match it like any exchange.

```yaml
mocks:
  - match: { method: GET, urlMatches: "example.com" }   # request-side matcher
    respond:
      status: 418                                        # default 200
      headers: { Content-Type: text/plain }
      body: "stubbed by bajutsu"
      # delayMs: 200                                     # optional artificial latency
  - match: { method: POST, pathMatches: "/login$" }
    respond: { status: 201, body: "{\"token\":\"t\"}" }
```

The first matching rule wins. `match` reuses the request matcher's request-side fields
(`method` / `url` / `urlMatches` / `path` / `pathMatches` / `bodyMatches`). A mock refuses the
`query` / `body` dictionaries, since the in-app matcher would ignore them.

Mocks ride the same channel as observation, so they need `--network`. On iOS, the rules reach the
app via the `BAJUTSU_MOCKS` launch env (like `BAJUTSU_COLLECTOR`). On web, no app launches,
so `network_collector()` takes the scenario's `mocks` directly and fulfills them in-process.

On iOS, a `setMocks` step replaces the stub table while the app runs (BE-0365). The collector
queues the new table, and the app drains it over the in-app control channel. The step waits until
the app confirms the new table. `setMocks` needs the `xcuitest` backend with network collection on.
`run` refuses a scenario that uses it anywhere else, before the scenario starts.
[scenarios](scenarios.md#changing-the-mocks-mid-scenario-setmocks) covers the step in full.

## Timing

Network I/O is asynchronous, so a step can run before the response lands. Bridge the gap with a
wait on the UI that reflects the response **before** the `request` assertion. Use
`wait: { until: settled }`, or wait for an element the response reveals.

On iOS and Android the SDK POSTs on completion, and on web Playwright's own `requestfinished`
event fires then. On every platform, the exchange is in the collector by the time the UI has
updated.

## App contract

**iOS:** link [BajutsuKit](../BajutsuKit/README.md) and call `BajutsuNet.startIfEnabled()` early.
The call is inert unless `BAJUTSU_COLLECTOR` is set, captures `URLSession` HTTP(S) only, and is
**test/debug-only**. It records headers and bodies, so keep it out of release builds and use
`redact`.

BajutsuKit forwards the intercepted request as-is, with the same method, headers, body, and
timeout. `URLProtocol` cannot recover the app's own `URLSessionConfiguration`, though. An app
with TLS pinning or client-cert auth in its delegate can see the check fail once intercepted.

**Android:** link [BajutsuAndroid](../BajutsuAndroid/README.md), call `BajutsuNet.configure(env)` at
launch (with the launch-env map), and add `BajutsuNet.interceptor()` to the app's `OkHttpClient`.
The interceptor is inert unless `BAJUTSU_COLLECTOR` is set, captures **OkHttp** HTTP(S) only, and
is likewise **test/debug-only**: it records headers and bodies, so keep it out of release builds
and use `redact`, the same caveat as on iOS.

Android also needs a `network_security_config` cleartext exception for `127.0.0.1` in the
test/debug build. The collector URL is plain HTTP, which API 28+ blocks by default. iOS exempts
loopback from App Transport Security (ATS) and needs no exception. Without the exception, the
report POST fails. The app logs the error and stays otherwise silent, so no exchange ever reaches
the collector.

**Web:** the app has no side of the contract at all. Playwright observes and mocks the page's traffic
natively, so nothing in the app under test needs to change.

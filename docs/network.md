**English** · [日本語](ja/network.md)

# Network observation (`request` assertions)

> Verify the HTTP(S) traffic an app makes, as a step/expect assertion. On iOS and Android,
> observation is **in-app**: the app reports each exchange to a collector bajutsu runs. On web,
> Playwright observes the page's traffic natively, with no app-side reporting involved. Either
> way, a `request` assertion checks the accumulated exchanges.
>
> Implementation: `bajutsu/common/evidence/network.py` (model + collector), `bajutsu/assertions/network.py`
> (`request` eval), `bajutsu/web_network.py` (the Playwright-native collector and mocking, BE-0054),
> and the in-app SDK (software development kit) — [`BajutsuKit`](../BajutsuKit/README.md) on iOS,
> [`BajutsuAndroid`](../BajutsuAndroid/README.md) on Android. The web (Playwright) backend needs no
> SDK: it observes the page's traffic natively.

Related: [scenarios](scenarios.md) · [evidence](evidence.md)

---

## How traffic is observed

A Simulator app runs as a host process and shares the Mac's loopback, so:

1. On `run`, bajutsu starts a **collector** (`NetworkCollector`) on `127.0.0.1:<port>` and
   injects its URL into the app via the `BAJUTSU_COLLECTOR` launch env, together with a
   per-run shared token via `BAJUTSU_COLLECTOR_TOKEN`.
2. The app (linked with **BajutsuKit**) installs a `URLProtocol` that records each
   request/response and POSTs it to the collector — **after TLS (Transport Layer Security)** (no proxy, no CA / certificate authority), so it
   works regardless of which backend drives the app and is readable programmatically. Each POST carries the token as an
   `Authorization: Bearer` header, and the collector rejects any request without the
   matching token (401), so another local process can't inject fabricated exchanges into
   the run's evidence.
3. The collector keeps the exchanges in memory; a step's `request` assertion is evaluated
   against them in real time, and bajutsu writes them (redacted) to `<sid>/network.json` as
   scenario evidence.

`--no-network` disables the collector. Apps without the SDK report nothing (the
collector stays empty); the feature is opt-in per app.

The same collector, on iOS, also receives screen-transition events on a separate `/transitions`
endpoint (BE-0310): `BajutsuKit`'s `BajutsuScreen` swizzles `UIViewController.viewDidAppear(_:)`
and reports each completed view-controller appearance there, kept in its own store independent of the
network exchanges above. That signal is not a `request`-assertion concern — the post-launch
readiness gate and the `settled` wait consult it instead; see [run-loop](run-loop.md#waits-condition-waits-only).

**Android** works the same way, with two differences (BE-0283). The app links
[`BajutsuAndroid`](../BajutsuAndroid/README.md) and adds `BajutsuNet.interceptor()` to its OkHttp
client — Android has no single OS-level HTTP hook like iOS's `URLProtocol`, so the interceptor is
per-client and captures **OkHttp** traffic. And the emulator's `127.0.0.1` is its own loopback, not
the host's, so bajutsu bridges the collector to the device with `adb reverse` (the same port both
ways, so the injected `BAJUTSU_COLLECTOR` URL resolves unchanged). Because that one number has to be
bindable on **both** sides, an Android lane takes the collector's port from a small reserved band
rather than letting the operating system pick a free one. An operating-system pick comes from the
host's ephemeral range, which is also the range the emulator hands out to its own sockets, so it
periodically names a port the guest already holds and the bridge fails to bind. The band is scoped to
Android alone: the iOS Simulator shares the Mac's loopback, bridges nothing, and keeps the
operating-system pick. The collector, the token check, and the assertion pipeline are identical.

**Web** (Playwright) needs none of this in-app machinery: Playwright already sees every request the
page makes (BE-0054). `PlaywrightDriver.network_collector()` hooks the page's `requestfinished`
event directly into the same `NetworkExchange` model. There is no `BAJUTSU_COLLECTOR` launch env,
no port, and no token to inject — the driver is the collector. The resulting `NetworkExchange`
records match the same `request` assertion as the iOS and Android path.

> This mechanism is the in-app path. RocketSim's GUI network inspector and a TLS-intercepting proxy
> were both rejected — the former is not exposed on its CLI (unusable for automated
> assertions), the latter needs CA install and breaks on pinning. See the design notes.

## The `request` assertion

`request` is an assertion kind (alongside `exists` / `value` / `count` / …). Match fields
are AND-ed. Each plain `request` corresponds to **one** observed exchange: multiple
`request` assertions in a block are matched **one-to-one** to distinct exchanges — two
`request` lines need two separate requests. `count` is the exception: it is an explicit
aggregate (exact when set, otherwise the lone matcher needs at least one).

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
| `urlMatches` | regex/substring over the URL (query strings live here) |
| `path` | exact URL path (query ignored) |
| `pathMatches` | regex over the path |
| `status` | response status code |
| `bodyMatches` | regex/substring over the request body |
| `count` | exact number of matching exchanges — an aggregate, exempt from the 1:1 rule (omit ⇒ at least one) |

## Deterministic mocks

A scenario's `mocks` make the network deterministic: when an outgoing request matches a
rule, the platform's collector returns the canned response **instead of hitting the network**, so
a test never depends on a live server (and runs offline). On iOS, BajutsuKit serves the stub
inside the URL protocol — after TLS, no proxy/CA. On web, `WebNetworkCollector` fulfills it
in-process via Playwright's `page.route`. Either way the stub is still observed (it appears in
`network.json` flagged `mocked`, and `request` assertions match it like any exchange). BajutsuAndroid
observes but does not yet stub (a follow-up to BE-0283).

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
(`method` / `url` / `urlMatches` / `path` / `pathMatches` / `bodyMatches`). Mocks ride the
same channel as observation, so they need `--network`. On iOS, the rules reach the app via the
`BAJUTSU_MOCKS` launch env (like `BAJUTSU_COLLECTOR`); on web there is no app to launch into, so
`network_collector()` takes the scenario's `mocks` directly and fulfills them in-process.

## Timing

Network I/O is asynchronous, so a step can run before the response lands. Bridge the gap with a wait
on the UI that reflects the response (e.g. `wait: { until: settled }`, or wait for an
element the response reveals) **before** the `request` assertion. On iOS and Android the SDK POSTs
on completion; on web, Playwright's own `requestfinished` event fires then. Either way, by the
time the UI has updated, the exchange is in the collector.

## App contract

**iOS** — link [BajutsuKit](../BajutsuKit/README.md) and call `BajutsuNet.startIfEnabled()` early.
It is inert unless `BAJUTSU_COLLECTOR` is set, captures `URLSession` HTTP(S) only, and is
**test/debug-only** (it records headers/bodies — keep it out of release and use `redact`).

**Android** — link [BajutsuAndroid](../BajutsuAndroid/README.md), call `BajutsuNet.configure(env)` at
launch (with the launch-env map), and add `BajutsuNet.interceptor()` to the app's `OkHttpClient`. It
is inert unless `BAJUTSU_COLLECTOR` is set, captures **OkHttp** HTTP(S) only, and is likewise
**test/debug-only** (it records headers/bodies — keep it out of release and use `redact`, the same
caveat as iOS above). Android also needs a `network_security_config` cleartext exception for
`127.0.0.1` in the test/debug build (the collector URL is plain HTTP, which API 28+ blocks by default;
iOS exempts loopback from App Transport Security (ATS), so it needs none) — without it the report POST
fails — logged, but otherwise silent, so no exchange ever reaches the collector.

**Web** — no app-side contract at all: Playwright observes and mocks the page's traffic natively,
so nothing in the app under test needs to change.

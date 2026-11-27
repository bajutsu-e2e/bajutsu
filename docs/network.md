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
> - the in-app Software Development Kit (SDK): [`BajutsuKit`](../BajutsuKit/README.md) on iOS and
>   [`BajutsuAndroid`](../BajutsuAndroid/README.md) on Android
>
> The web (Playwright) backend needs no SDK.

Related: [scenarios](scenarios.md) · [evidence](evidence.md)

---

## How traffic is observed

A Simulator app runs as a process on the Mac and shares the Mac's loopback. Traffic observation then
proceeds in three stages:

1. On `run`, bajutsu starts a **collector** (`NetworkCollector`) that listens on
   `127.0.0.1:<port>` for the whole run. bajutsu injects the collector's Uniform Resource Locator
   (URL) into the app through the `BAJUTSU_COLLECTOR` launch env. A per-run shared token goes in a
   second launch env key, `BAJUTSU_COLLECTOR_TOKEN`, in the same way.
2. The app (linked with **BajutsuKit**) installs a `URLProtocol` that records each
   request/response. The app sends each record to the collector with an HTTP `POST` **after TLS
   (Transport Layer Security)**. The capture uses no proxy and no certificate authority (CA). It
   works regardless of which backend drives the app, and programs can read it. Each `POST` proves
   the token. The collector answers any request without that proof with a 401 status. Another
   local process thus cannot inject fabricated exchanges into the run's evidence.
   - bajutsu always announces `BAJUTSU_COLLECTOR_AUTH=hmac`. A current BajutsuKit then signs each
     request with the token.
   - An older BajutsuKit sends the token as an `Authorization: Bearer` header instead. A loopback
     collector still accepts that header.
   - On a real device the collector accepts signed requests alone
     ([the real-device guide](ios-device-cloud.md#how-the-device-to-host-route-is-authenticated)).
3. The collector keeps the exchanges in memory. A step's `request` assertion checks them in
   real time. bajutsu also writes them (redacted) to `<sid>/network.json` as scenario evidence.

`--no-network` disables the collector. Apps without the SDK report nothing, so the collector stays
empty; the feature is opt-in per app.

On iOS, the same collector also receives screen-transition events, a feature BE-0310 added. Those
events arrive on a separate `/transitions` endpoint of their own. In `BajutsuKit`, `BajutsuScreen`
swizzles `UIViewController.viewDidAppear(_:)` for this purpose. Through that swizzle, it reports
each completed view-controller appearance to the endpoint. The collector keeps these events in their own store, independent of
the network exchanges above. The `request` assertion ignores this signal. The post-launch
readiness gate and the `settled` wait read it instead. See
[run-loop](run-loop.md#waits-condition-waits-only).

### Android

Android works the same way, with two differences (BE-0283).

- The app links [`BajutsuAndroid`](../BajutsuAndroid/README.md) and adds `BajutsuNet.interceptor()`
  to its OkHttp client. Android has no single OS-level HTTP interception point like iOS's
  `URLProtocol`. The interceptor is thus per-client and captures **OkHttp** traffic.
- The emulator's `127.0.0.1` is its own loopback, not the loopback of the machine running bajutsu.
  bajutsu bridges the collector to the device with `adb reverse`. The bridge uses one port on both
  sides. The injected `BAJUTSU_COLLECTOR` URL thus resolves unchanged.

That one port number must be bindable on **both** sides. An Android lane thus draws the
collector's port from a small reserved band. The operating system never picks that port. An
operating-system pick comes from the ephemeral range of the machine running bajutsu. The emulator
hands out ports from that same range to its own sockets. The pick periodically names a port the guest already holds, and the
bridge then fails to bind.

The band applies to Android alone. The iOS Simulator shares the Mac's loopback, bridges nothing,
and keeps the operating-system pick. The collector, the token check, and the assertion pipeline are
identical.

### Web

Web (Playwright) needs none of this in-app machinery. Playwright already sees every request the
page makes (BE-0054) without help from the app. `PlaywrightDriver.network_collector()` subscribes
to the page's `requestfinished` event. It converts each event directly into the same
`NetworkExchange` model. The page needs no
`BAJUTSU_COLLECTOR` launch env, no port, and no token; the driver is the collector. The
resulting `NetworkExchange` records match the same `request` assertion as on iOS and Android.

> The in-app path is the chosen mechanism. A Simulator utility's Graphical User Interface (GUI) network
> inspector and a TLS-intercepting proxy were both rejected. The inspector is not exposed on its
> Command Line Interface (CLI), so automated assertions cannot use it. The proxy needs a CA install
> and breaks on certificate pinning. See the design notes.

## The `request` assertion

`request` is an assertion kind (alongside `exists` / `value` / `count` / …). Every match field
in a `request` must hold at once. Each plain `request` corresponds to **one** observed exchange.
Each `request` assertion in a block pairs **one-to-one** with a distinct exchange. Two `request`
lines thus need two separate requests. `count` is the exception, an explicit total across
exchanges. With `count` present, the number of matches must equal it. Without it, the lone
matcher needs at least one exchange.

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
| `count` | exact number of matching exchanges; a total across exchanges, outside the 1:1 rule (omit ⇒ at least one) |

## Deterministic mocks

A scenario's `mocks` make the network deterministic. Each mock rule matches outgoing
requests. On a match, the platform's collector serves the canned response **instead of hitting the
network**. A
test thus never depends on a live server and runs offline.

- On iOS, BajutsuKit serves the stub inside the URL protocol, after TLS, with no proxy or CA.
- On web, `WebNetworkCollector` fulfills the stub in-process via Playwright's `page.route`.
- BajutsuAndroid observes but does not yet stub. Android stubbing is an open follow-up to
  BE-0283.

On iOS and web, the collector still observes the stub. `network.json` lists it flagged `mocked`,
and `request` assertions match it like any exchange.

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

The first matching rule wins. `match` reuses the request matcher's request-side fields. The six
fields are `method` / `url` / `urlMatches` / `path` / `pathMatches` / `bodyMatches` in the table. A mock refuses the
`query` / `body` dictionaries, since the in-app matcher would ignore them.

Mocks ride the same channel as observation, so they need the `--network` flag. On iOS, the rules
reach the app via the `BAJUTSU_MOCKS` launch env, as `BAJUTSU_COLLECTOR` does. On web, no app launches.
`network_collector()` takes the scenario's `mocks` directly and fulfills them in-process.

On iOS, a `setMocks` step replaces the stub table while the app runs (BE-0365). The collector
queues the new table, and the app drains it over the in-app control channel. The step waits until
the app confirms the new table. `setMocks` needs the `xcuitest` backend with network collection on.
`run` refuses a scenario that uses it anywhere else, before the scenario starts.
[scenarios](scenarios.md#changing-the-mocks-mid-scenario-setmocks) covers the step in full.

## Timing

Network I/O is asynchronous, so a step can run before the response lands. Bridge the gap with a
wait on the UI that reflects the response **before** the `request` assertion. Use
`wait: { until: settled }`, or wait for an element the response reveals.

On iOS and Android, the SDK sends its `POST` when the exchange completes. On web, Playwright
emits its own `requestfinished` event at that moment. On every platform, the exchange is in the collector by the time the UI has
updated.

## App contract

**iOS:** link [BajutsuKit](../BajutsuKit/README.md) and call `BajutsuNet.startIfEnabled()` when the
app launches. The call does nothing unless the launch env carries `BAJUTSU_COLLECTOR`. It captures
`URLSession` HTTP(S) traffic alone. Treat the call as **test/debug-only**. It records headers and
bodies, so keep it out of release builds and use `redact`. The in-app control channel adds a compile-time gate of its own
([below](#the-in-app-control-channel)).

BajutsuKit forwards the intercepted request as-is. The method, headers, body, and timeout all stay
the same. `URLProtocol` cannot recover the app's own `URLSessionConfiguration`, though. An app
with TLS pinning or client-cert auth in its delegate can see the check fail once intercepted.

**Android:** link [BajutsuAndroid](../BajutsuAndroid/README.md) into the app first. At launch, call
`BajutsuNet.configure(env)` with the launch-env map. Then add `BajutsuNet.interceptor()` to the
app's `OkHttpClient` instance. The interceptor does nothing unless the launch env carries
`BAJUTSU_COLLECTOR`. It captures **OkHttp** HTTP(S) traffic alone. Treat it as
**test/debug-only** as well, because it records headers and bodies. As on iOS, keep it out of release builds
and apply `redact` to what it records.

Android also needs one more setting in the test/debug build. Its `network_security_config` must
allow cleartext traffic to `127.0.0.1` as an exception. The collector URL is plain HTTP.
From Application Programming Interface (API) level 28, Android blocks plain HTTP by default. iOS exempts
loopback from App Transport Security (ATS) and needs no exception. Without the exception, the
report `POST` fails. The app logs the error and stays otherwise silent, so no exchange ever reaches
the collector.

**Web:** the app has no side of the contract at all. Playwright observes and mocks the page's
traffic natively. Nothing in the app under test needs to change.

## The in-app control channel

The launch env reaches the app once, when the app starts. Every BajutsuKit feature above thus
keeps its launch-time setting for the life of the process. The in-app control channel
([BE-0365](../roadmaps/BE-0365-in-app-control-channel/BE-0365-in-app-control-channel.md)) removes
that limit for iOS. bajutsu can change one of its own in-app capabilities while a scenario runs. The
channel rides the collector described above, so it needs no new server, port, or credential.

### How a command reaches the app

A command names one capability and the state the capability should take. The command travels in four
stages:

1. The run loop queues the command on the collector.
2. The app polls the collector's `GET /commands` about every 0.15 seconds and drains the queue. The
   collector checks the per-run token on that request, as it does on every report.
3. The app applies the command and reports on `/commands/ack`. The report carries `applied`, and
   when the app could not apply the command, the app's own `reason`.
4. The run loop waits on that report as a condition, for up to five seconds.

The wait in stage 4 keeps the channel deterministic. The run loop never pauses for a fixed interval
and hopes the command landed. A command either provably took effect before the next action, or the
step does not pass:

- A report with `applied: false` stops the step at once and quotes the app's reason.
- No report within the timeout stops the step with a message naming the command.

The collector serves `/commands/ack` ahead of the path that stores network exchanges. A report thus
never enters `network.json` or the exchanges a `request` assertion reads. Between scenarios, the
collector drops any command the app has not drained. One scenario's command thus never reaches the
next.

### The commands the channel carries

| Capability | Used by | What it does |
|---|---|---|
| `touch_visualization` | `--touch-markers` ([evidence](evidence.md#touch-markers-in-the-recording---touch-markers)) | hides the touch markers for the one capture a `visual` assertion compares, then restores them |
| `stub_table` | the `setMocks` step ([scenarios](scenarios.md#changing-the-mocks-mid-scenario-setmocks)) | replaces the app's whole stub table mid-scenario |

### Where the channel is available

The channel needs the `xcuitest` actuator with network collection on. BajutsuKit's poll loop runs
inside the Simulator app. No other combination pairs a running poll loop with a collector. The
other actuators fall short:

- `fake` starts a collector, but no app ever polls it.
- `playwright` observes the network through the driver. No app has an HTTP collector to poll.
- `adb` starts a collector, but no Android app answers a command.

A command that the channel cannot deliver is never skipped in silence. In any other configuration,
`run` refuses a `setMocks` scenario before touching a device. For a screenshot-comparing scenario,
`--touch-markers` falls back to drawing no markers. It says so on stderr.

### The two gates inside the app

The channel stays closed unless two gates both open. The launch env is never the sole guard:

| Gate | What opens it |
|---|---|
| A compilation condition | the app's build passes `-DBAJUTSU_ENABLE_CONTROL_CHANNEL` to BajutsuKit; without it, the channel's code is absent from the binary |
| A launch-env key | `BAJUTSU_CONTROL_CHANNEL=1`, which `run` writes for a scenario that needs the channel; without it, the app starts no poll timer |

An app built without the compilation condition acknowledges nothing. The step then stops after the
timeout and names both gates. The compilation condition also explains a rule in the
[BajutsuKit Safety guidance](../BajutsuKit/README.md#safety). That guidance makes the release-build
gate mandatory rather than advisory.

### What the channel never carries

The channel has three boundaries, and each one keeps a prime directive intact:

- **No judgment.** No assertion reads from the channel, and nothing on it decides whether a step
  passes. The app under test never takes part in its own verdict.
- **Off by default.** The two gates keep the channel out of a build or launch that does not ask for
  it.
- **bajutsu's own instrumentation alone.** A command addresses the touch markers or the stub table. A
  command that seeded app data or drove navigation would move per-app knowledge into the tool. The
  channel carries no such command.

### What polling costs

Polling buys the absence of a listener inside the app. The price is latency: a command takes effect no
sooner than the app's next poll. The run loop pays that latency once per command, behind the wait
described above. The touch markers cost it twice per screenshot-comparing step, and `setMocks` once per
step. The poll timer runs while the channel is open and never otherwise. A scenario that never asks for
the channel thus leaves the app's own timing untouched.

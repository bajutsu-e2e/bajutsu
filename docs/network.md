**English** · 日本語 (todo)

# Network observation (`request` assertions)

> Verify the HTTP(S) traffic an app makes, as a step/expect assertion. Observation is
> **in-app**: the app reports each exchange to a collector bajutsu runs, and a `request`
> assertion checks the accumulated exchanges.
>
> Implementation: `bajutsu/network.py` (model + collector), `bajutsu/assertions.py`
> (`request` eval), the in-app SDK [`BajutsuKit`](../BajutsuKit/README.md).

Related: [scenarios](scenarios.md) · [evidence](evidence.md)

---

## How traffic is observed

A Simulator app runs as a host process and shares the Mac's loopback, so:

1. On `run`, bajutsu starts a **collector** (`NetworkCollector`) on `127.0.0.1:<port>` and
   injects its URL into the app via the `BAJUTSU_COLLECTOR` launch env.
2. The app (linked with **BajutsuKit**) installs a `URLProtocol` that records each
   request/response and POSTs it to the collector — **after TLS** (no proxy / CA), so it
   works under idb and is readable programmatically.
3. The collector keeps the exchanges in memory; a step's `request` assertion is evaluated
   against them in real time, and they are written to `<sid>/network.json` (redacted) as
   scenario evidence.

`--no-network` disables the collector. Apps without BajutsuKit simply report nothing (the
collector stays empty); the feature is opt-in per app.

> This is the in-app path. RocketSim's GUI network inspector and a TLS-intercepting proxy
> were both rejected — the former is not exposed on its CLI (unusable for automated
> assertions), the latter needs CA install and breaks on pinning. See the design notes.

## The `request` assertion

`request` is an assertion kind (alongside `exists` / `value` / `count` / …). Match fields
are AND-ed; `count` checks how many exchanges matched (exact when set, otherwise ≥ 1):

```yaml
expect:
  - request: { method: GET, status: 200 }                 # ≥ 1 GET that returned 200
  - request: { method: POST, path: /login, status: 200 }  # exact path
  - request: { pathMatches: "^/items", count: 2 }          # regex on path, exactly 2
```

| field | meaning |
|---|---|
| `method` | HTTP method (case-insensitive) |
| `path` | exact URL path (query ignored) |
| `pathMatches` | regex over the path |
| `status` | response status code |
| `count` | exact number of matching exchanges (omit ⇒ at least one) |

## Timing

Network is async, so a step can run before the response lands. Bridge the gap with a wait
on the UI that reflects the response (e.g. `wait: { until: settled }`, or wait for an
element the response reveals) **before** the `request` assertion. The SDK POSTs on
completion, so by the time the UI has updated the exchange is in the collector.

## App contract

Link [BajutsuKit](../BajutsuKit/README.md) and call `BajutsuNet.startIfEnabled()` early.
It is inert unless `BAJUTSU_COLLECTOR` is set, captures `URLSession` HTTP(S) only, and is
**test/debug-only** (it records headers/bodies — keep it out of release and use `redact`).

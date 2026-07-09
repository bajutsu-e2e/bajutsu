**English** · [日本語](BE-XXXX-e2e-simulator-flaky-readiness-actuation-ja.md)

# BE-XXXX — Stabilize the E2E Simulator gate: namespace-aware readiness and a bounded actuation timeout

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-e2e-simulator-flaky-readiness-actuation.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#850](https://github.com/bajutsu-e2e/bajutsu/pull/850) |
| Topic | On-device validation (M1 close-out) |
<!-- /BE-METADATA -->

## Introduction

The `E2E (Simulator)` gate (`.github/workflows/e2e.yml`) ran flaky: hard failures in two distinct
jobs, from two distinct transport-vs-timing causes, neither of which is a real defect in the code
under test. This item fixes both at their source — **without** weakening the verdict — so the gate
stops flaking:

1. **App-readiness (`smoke (idb)`, the dominant flake).** `_await_ready` (the post-launch gate in
   `bajutsu/platform_lifecycle.py`) could declare the app "ready" before it had foregrounded, so the
   first scenario step then raced a slow cold launch and timed out.
2. **Actuation timeout (`xcuitest (multi-touch)`, the secondary flake).** A single two-finger
   gesture on a loaded CI host could exceed the runner channel's one 15-second socket window, and
   because [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
   deliberately does not retry a write after delivery (double-actuation risk), that stall failed
   hard.

## Motivation

Over the last 80 `E2E (Simulator)` runs there were two hard failures (the rest were success, or
`cancelled` by `concurrency: cancel-in-progress` on a new push). Both hard failures were the
**same** `smoke (idb)` signature:

```
step 0 (wait): wait timeout: for {'id': 'stable.row.1'} (10.0s)
```

The `bajutsu doctor` output logged immediately before each run is the tell: it graded the screen
`Partial` / `Blocked` with off-namespace ids such as `Fitness, Watch, Contacts, Files, Safari,
Messages` — those are the **Home screen (SpringBoard) app icons**, not the app under test. So at the
moment the scenario started, the app had not yet come to the foreground.

The cause is in `_await_ready`. The `showcase-swiftui` target declares no `readyWhen`, so readiness
fell back to the heuristic "the tree has 2+ elements". On a slow cold boot the device query returns
SpringBoard's tree — many off-namespace icons — which trivially satisfies "2+ elements", so
`_await_ready` returned prematurely. The scenario's first step (`wait for stable.row.1`, timeout
10s) then became the *real* readiness gate, and on a contended macOS runner the cold launch did not
render `stable.row.1` within 10s. This is not a determinism defect in the scenario; it is a
readiness gate that accepted the wrong screen.

One of the two runs also failed `xcuitest (multi-touch)` with:

```
XcuitestChannelError: runner channel POST /gesture failed: timed out
```

This run was *after* BE-0207 (#824) merged, so the transient-retry was already in place — yet the
gesture still failed hard, because BE-0207's classification (correctly) never retries a *write*
after delivery. The runner channel uses one socket-timeout window (`_SOCKET_TIMEOUT_SECONDS = 15`)
for every call. A read that blips is absorbed by the retry; a write that is merely slow (a
multi-touch event synthesized by XCUITest on a loaded host) has no retry to lean on, so a single
stall past 15s sinks the run.

Both are transport/timing fragility, not verdict signal. A red gate that carries no information
about whether anything is actually wrong is exactly the flaky-gate cost this fixes.

## Detailed design

1. **Namespace-aware readiness in `_await_ready`.** Thread the target's `idNamespaces` into the
   gate. When no usable `readyWhen` selector is present, prefer a stronger signal than raw element
   count: the app is ready once **any queried element's id belongs to a declared namespace**
   (`namespace_of(id) in idNamespaces`). SpringBoard's off-namespace icons no longer satisfy the
   gate, so it waits for the app itself. Readiness precedence is: `readyWhen` selector (strongest,
   explicit) → in-namespace element → the existing "2+ elements" count (unchanged fallback for a
   target that declares no namespaces, e.g. a `-noax` app or web).
2. **Per-method socket timeout for the XCUITest channel.** Split the single window into
   `_SOCKET_TIMEOUT_SECONDS` (reads, unchanged at 15s) and `_ACTUATION_TIMEOUT_SECONDS` (writes,
   30s), selected by `_timeout_for(method)`. A read leans on the BE-0207 retry for a transient blip;
   a write cannot be retried after delivery, so it gets one longer *bounded* window to tolerate a
   slow actuation on a loaded host. Both stay bounded, so a genuinely wedged runner still fails
   loudly per attempt rather than hanging.
3. **Determinism preserved (prime directive 1 & 2).** Neither change touches a real outcome:
   readiness still fails at its deadline if the app never foregrounds; an exhausted retry / an
   over-budget write still raises the same loud `XcuitestChannelError`; `stale` / `not-found` remain
   decoded outcomes, never retried. No LLM enters the gate; no fixed `sleep` is added (readiness is a
   condition poll, the timeout is a per-attempt bound).
4. **Unit-tested off-device.** The readiness change is covered against a scripted fake driver
   (a SpringBoard-only screen must not satisfy the gate; a single in-namespace element does; an empty
   namespace list keeps the count heuristic; an explicit `readyWhen` still governs). The timeout
   change is covered by `_timeout_for` and by faking the `http.client` boundary to assert the
   per-method window reaches the connection.

## Alternatives considered

- **Set `readyWhen` on the `showcase-swiftui` target instead of touching `_await_ready`.** A
  per-target selector cannot cover both launch screens the suite uses (smoke launches the Stable
  tab, gestures launch the pinch/rotate screen), and it only patches one app. The namespace signal
  is app-agnostic (it reuses the `idNamespaces` every target already declares) and fixes the gate
  for every identifier-bearing target at once.
- **Just raise the readiness / step-0 timeout.** Trades one flake for a slower one: it does not stop
  the gate accepting SpringBoard, it only lengthens how long the subsequent step waits before it
  times out anyway.
- **Retry the write actuation (drop BE-0207's idempotency split).** Can double-apply a gesture whose
  response merely timed out, silently corrupting the determinism the gate protects. Rejected — the
  longer bounded window is the safe lever for a non-retryable write.
- **Inflate `_SOCKET_TIMEOUT_SECONDS` globally.** Lengthens the wedged-runner path for *reads* too,
  which already have the retry; the per-method split keeps reads tight and only the non-retryable
  write generous.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Namespace-aware readiness in `_await_ready` (readyWhen → in-namespace → count), `idNamespaces` threaded through the callers
- [x] Per-method socket timeout (`_timeout_for`: reads 15s, actuation writes 30s) in the XCUITest channel
- [x] Determinism preserved — loud failures and decoded outcomes unchanged
- [x] Off-device unit tests for both changes

### Log

- [#850](https://github.com/bajutsu-e2e/bajutsu/pull/850) — Namespace-aware `_await_ready` (an off-namespace SpringBoard screen no longer satisfies
  the gate; a single in-namespace element does; empty `idNamespaces` keeps the 2+ count; an explicit
  `readyWhen` still takes precedence), and a per-method XCUITest channel timeout (`_timeout_for`:
  reads keep the tight 15s window backed by the BE-0207 retry, actuation writes get a bounded 30s
  window since a write cannot be retried after delivery). Covered by off-device unit tests.

## References

- [`bajutsu/platform_lifecycle.py`](../../bajutsu/platform_lifecycle.py) — `_await_ready`, the post-launch readiness gate
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — `_timeout_for`, `_SOCKET_TIMEOUT_SECONDS`, `_ACTUATION_TIMEOUT_SECONDS`, `_raw_http_transport`
- [`bajutsu/doctor.py`](../../bajutsu/doctor.py) — `namespace_of`, the id→namespace split reused by the readiness gate
- [`.github/workflows/e2e.yml`](../../.github/workflows/e2e.yml) — the `E2E (Simulator)` gate this stabilizes
- [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md) — the transient-retry policy this timeout split complements
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — the determinism stance both changes stay consistent with (tolerate transport/timing, never a verdict)

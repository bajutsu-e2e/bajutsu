**English** · [日本語](BE-0218-e2e-simulator-flaky-readiness-actuation-ja.md)

# BE-0218 — Stabilize the E2E Simulator gate: namespace-aware readiness and a bounded actuation timeout

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0218](BE-0218-e2e-simulator-flaky-readiness-actuation.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0218") |
| Implementing PR | [#850](https://github.com/bajutsu-e2e/bajutsu/pull/850) |
| Topic | Platform support |
| Related | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md) |
<!-- /BE-METADATA -->

## Introduction

The `E2E (Simulator)` gate (`.github/workflows/e2e.yml`) ran flaky. It failed hard in two distinct
jobs, from two distinct transport-versus-timing causes. Neither cause is a real defect in the code
under test. This item fixes both at their source, **without** weakening the verdict, so the gate
stops flaking:

1. **App-readiness (`smoke (idb)`, the dominant flake).** `_await_ready` is the post-launch gate in
   `bajutsu/platform_lifecycle.py`. It could declare the app "ready" before the app had foregrounded.
   The first scenario step then raced a slow cold launch and timed out.
2. **Actuation timeout (`xcuitest (multi-touch)`, the secondary flake).** A single two-finger gesture
   on a loaded CI host could exceed the one 15-second socket window of the runner channel.
   [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
   deliberately does not retry a write after delivery, because retrying risks double actuation. That
   stall therefore failed hard.

## Motivation

Over the last 80 `E2E (Simulator)` runs there were two hard failures. The other runs were successes,
or were `cancelled` by `concurrency: cancel-in-progress` on a new push. Both hard failures had the
**same** `smoke (idb)` signature:

```
step 0 (wait): wait timeout: for {'id': 'stable.row.1'} (10.0s)
```

The `bajutsu doctor` output logged immediately before each run is the tell. It graded the screen
`Partial` / `Blocked`, with off-namespace ids such as `Fitness, Watch, Contacts, Files, Safari,
Messages`. Those ids are the **Home screen (SpringBoard) app icons**, not the app under test. So at
the moment the scenario started, the app had not yet come to the foreground.

The cause is in `_await_ready`. The `showcase-swiftui` target declares no `readyWhen`, so readiness
fell back to the heuristic "the tree has 2+ elements". On a slow cold boot, the device query returns
the tree of SpringBoard, which holds many off-namespace icons. That tree trivially satisfies "2+
elements", so `_await_ready` returned prematurely. The first step of the scenario (`wait for
stable.row.1`, timeout 10s) then became the *real* readiness gate. On a contended macOS runner, the
cold launch did not render `stable.row.1` within 10s. The scenario has no determinism defect here.
The readiness gate accepted the wrong screen.

One of the two runs also failed `xcuitest (multi-touch)` with:

```
XcuitestChannelError: runner channel POST /gesture failed: timed out
```

This run came *after* BE-0207 (#824) merged, so the transient retry was already in place. The gesture
still failed hard, because the classification of BE-0207 (correctly) never retries a *write* after
delivery. The runner channel uses one socket-timeout window (`_SOCKET_TIMEOUT_SECONDS = 15`) for
every call. The retry absorbs a read that blips. A write that is merely slow has no retry to lean on.
A multi-touch event synthesized by XCUITest on a loaded host is such a write. A single stall past 15s
sinks the run.

Both failures are transport/timing fragility, not verdict signal. A red gate that carries no
information about whether anything is actually wrong is exactly the flaky-gate cost that this item
removes.

## Detailed design

1. **Namespace-aware readiness in `_await_ready`.** Thread the `idNamespaces` of the target into the
   gate. When no usable `readyWhen` selector is present, prefer a stronger signal than the raw element
   count. The app is ready once **any queried element has an id that belongs to a declared namespace**
   (`namespace_of(id) in idNamespaces`). The off-namespace icons of SpringBoard no longer satisfy the
   gate, so the gate waits for the app itself. The precedence of readiness is:
   - the `readyWhen` selector (strongest, explicit);
   - an in-namespace element;
   - the existing "2+ elements" count, an unchanged fallback for a target that declares no namespaces
     (for example a `-noax` app or web).
2. **Per-method socket timeout for the XCUITest channel.** Split the single window into
   `_SOCKET_TIMEOUT_SECONDS` (reads, unchanged at 15s) and `_ACTUATION_TIMEOUT_SECONDS` (writes, 30s).
   The function `_timeout_for(method)` selects between them.
   - A read leans on the BE-0207 retry for a transient blip.
   - A write cannot be retried after delivery. It gets one longer *bounded* window to tolerate a slow
     actuation on a loaded host.
   - Both windows stay bounded. A genuinely wedged runner still fails loudly per attempt rather than
     hanging.
3. **Determinism preserved (prime directives 1 and 2).** Neither change touches a real outcome:
   - Readiness still fails at its deadline if the app never foregrounds.
   - An exhausted retry or an over-budget write still raises the same loud `XcuitestChannelError`.
   - `stale` and `not-found` remain decoded outcomes, never retried.
   - No LLM enters the gate. No fixed `sleep` is added: readiness is a condition poll, and the timeout
     is a per-attempt bound.
4. **Unit-tested off-device.** A scripted fake driver covers the readiness change in four cases:
   - A SpringBoard-only screen must not satisfy the gate.
   - A single in-namespace element does satisfy it.
   - An empty namespace list keeps the count heuristic.
   - An explicit `readyWhen` still governs.

   The timeout change is covered by `_timeout_for`, and by faking the `http.client` boundary to
   assert that the per-method window reaches the connection.

## Alternatives considered

- **Set `readyWhen` on the `showcase-swiftui` target instead of touching `_await_ready`.** A
  per-target selector cannot cover both launch screens that the suite uses. The smoke scenario
  launches the Stable tab, and the gesture scenarios launch the pinch/rotate screen. A per-target
  selector also patches only one app. The namespace signal is app-agnostic, since it reuses the
  `idNamespaces` that every target already declares. It fixes the gate for every identifier-bearing
  target at once.
- **Just raise the readiness / step-0 timeout.** This trades one flake for a slower one. It does not
  stop the gate from accepting SpringBoard. It only lengthens how long the next step waits before it
  times out anyway.
- **Retry the write actuation (drop the idempotency split of BE-0207).** A retry can double-apply a
  gesture whose response merely timed out. That silently corrupts the determinism that the gate
  protects. Rejected. The longer bounded window is the safe lever for a non-retryable write.
- **Inflate `_SOCKET_TIMEOUT_SECONDS` globally.** This lengthens the wedged-runner path for *reads*
  too, and reads already have the retry. The per-method split keeps reads tight and makes only the
  non-retryable write generous.

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

**English** · [日本語](BE-XXXX-xcuitest-channel-transient-retry-ja.md)

# BE-XXXX — Make the XCUITest runner channel robust to transient timeouts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-channel-transient-retry.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | On-device validation (M1 close-out) |
<!-- /BE-METADATA -->

## Introduction

The XCUITest runner channel (`bajutsu/drivers/xcuitest.py`) talks to the on-device runner over a
tiny HTTP loopback server, one request per driver call (`GET /elements`, `POST /gesture`, `GET
/screenshot`, …). Each call is a **single** `http.client` request bounded by a 15-second socket
timeout, and any transport-level `OSError` — including a socket timeout — is turned straight into a
fatal `XcuitestChannelError`. There is no retry. This item makes the channel tolerate a *transient*
transport hiccup (retry with backoff, then fail loudly) so the on-device E2E gate stops flaking,
**without** weakening its verdict: real element outcomes and exhausted retries still fail hard.

## Motivation

The heaviest gesture scenario, `demos/showcase/scenarios/gestures_multitouch.yaml`, fails
intermittently on CI with:

```
XcuitestChannelError: runner channel POST /gesture failed: timed out
XcuitestChannelError: runner channel GET /screenshot failed: timed out
```

Observed twice in a row on one PR (#808 — surfaced there incidentally; that PR was a pure run-id
refactor unrelated to gestures) and on the most recent `E2E (Simulator)` run on `main`. The
timeout lands on a *different* channel call each time (`/gesture` one run, `/screenshot` the next),
which is the signature of a transient runner-channel stall under a loaded CI host — the runner is
briefly slow to answer, the single 15-second window elapses, and the **whole E2E job fails**.

This is not a determinism defect in the scenario or the code under test; it is transport
fragility. A single dropped/slow channel response should not sink a run any more than a single
dropped TCP segment should — yet today it does, because `_http_transport` gives each call exactly
one attempt. The cost is a flaky gate: contributors re-run jobs by hand, and a red E2E check
carries no signal about whether anything is actually wrong.

The fix must respect **determinism first** (prime directive 2): tolerance applies *only* to
transport failure, never to a real result. `not-found` / `stale` are decoded outcomes
(`_decode`), not transport errors, and must never be retried — retrying them would be exactly the
"tolerate flakiness by absorption" stance Bajutsu rejects (and that [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
was written to expose). The goal is the opposite: keep every real failure loud, and stop letting a
recoverable transport blip masquerade as one.

## Detailed design

1. **Classify channel failures by idempotency.** Reads are safe to re-issue: `GET /elements`,
   `GET /screenshot`, `GET /health`. Side-effecting writes are not blindly safe: re-sending a
   `POST /gesture` / `POST /tap` / `POST /swipe` / `POST /type` after a *response* timeout could
   double-apply the action. Distinguish the two failure points:
   - **connect / send failed, no request delivered** (e.g. `ConnectionRefusedError`, connect
     timeout) → safe to retry for *any* method, because the runner never acted;
   - **request delivered, response timed out** → safe to retry only for idempotent reads; for a
     write, fail loudly rather than risk a double actuation.
2. **Add a bounded retry with backoff to `_http_transport`** honoring that classification: a small
   fixed number of attempts (e.g. 3) with short exponential backoff, retrying only the
   retry-eligible failures from (1). On exhaustion, raise the same `XcuitestChannelError` as today
   — the loud, deterministic failure is preserved; only recoverable blips are absorbed.
3. **Keep the socket timeout bounded and named.** `_SOCKET_TIMEOUT_SECONDS` stays the per-attempt
   window (a wedged runner must still fail fast per attempt); retry count/backoff become named
   constants beside it. Do not paper over the flake by inflating the timeout — that would make a
   genuinely wedged runner hang far longer.
4. **Emit a diagnostic on each retry** (which method/path, attempt N of M, the transient error) so
   a retried-then-passed run is visible in the log and a real regression in channel reliability is
   still discoverable — the retry is never silent.
5. **Unit-test the transport policy off-device.** `_http_transport`'s socket path is
   `# pragma: no cover` (on-device only), but the retry/classification logic can be lifted behind
   the existing `TransportFn` seam and tested with a fake transport: a transient-then-success read
   retries and succeeds; a write that times out *after* delivery fails without re-sending; a
   persistent failure exhausts attempts and raises; a `not-found`/`stale` reply is never retried.

## Alternatives considered

- **Just raise `_SOCKET_TIMEOUT_SECONDS`.** Trades one flake for a slower failure: a genuinely
  wedged runner then hangs the job for the inflated window every time, and a transient stall longer
  than the new bound still fails. Bounded retry targets the actual failure mode (a brief stall)
  without lengthening the wedged-runner path.
- **Retry every call unconditionally, including writes.** Simpler, but can double-apply a gesture
  whose response merely timed out — silently corrupting the very determinism the gate exists to
  protect. Rejected; the idempotency split in (1) is the point.
- **Mark the scenario `retry`-tolerant at the scenario layer (Maestro-style).** This is absorption
  at the wrong altitude — it hides *scenario* flakiness, which Bajutsu deliberately refuses
  (BE-0049). The fix belongs in the transport, not the verdict.
- **Do nothing / re-run by hand.** The status quo: a non-signal red check and wasted contributor
  time; the flake recurs on `main`, so it is not PR-specific.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Failure classification (connect-vs-response, read-vs-write idempotency) defined
- [ ] Bounded retry + backoff added to `_http_transport`, respecting the classification
- [ ] Per-attempt socket timeout kept bounded; retry knobs named beside it
- [ ] Retry diagnostic logged (never silent)
- [ ] Off-device unit tests for the transport policy (transient-then-success, write-after-delivery, exhaustion, never-retry-outcomes)

## References

- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — `_http_transport`, `_SOCKET_TIMEOUT_SECONDS`, `_decode`, `TransportFn`
- [`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml) — the scenario that surfaces the flake
- [`.github/workflows/e2e.yml`](../../.github/workflows/e2e.yml) — the `E2E (Simulator)` gate this stabilizes
- [BE-0019](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md) — the XCUITest backend this channel belongs to
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — the determinism-audit stance this item stays consistent with (tolerate transport, never a verdict)

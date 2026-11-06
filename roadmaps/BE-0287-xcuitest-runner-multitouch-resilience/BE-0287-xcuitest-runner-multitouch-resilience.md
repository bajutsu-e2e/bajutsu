**English** · [日本語](BE-0287-xcuitest-runner-multitouch-resilience-ja.md)

# BE-0287 — XCUITest runner-channel resilience under multi-touch actuation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0287](BE-0287-xcuitest-runner-multitouch-resilience.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0287") |
| Implementing PR | [#1200](https://github.com/bajutsu-e2e/bajutsu/pull/1200) (Unit 3: mid-run crash detection & recovery; Unit 4: recovery visibility), [#1202](https://github.com/bajutsu-e2e/bajutsu/pull/1202) (Units 1 & 2: runner-side diagnosis and the HTTP-server root-cause fix), [#1211](https://github.com/bajutsu-e2e/bajutsu/pull/1211) (Unit 5: exclude frame from the tap-time attribute re-check) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

The `xcuitest (multi-touch)` on-device end-to-end (E2E) job flakes. The XCUITest runner becomes unreachable while a two-finger gesture is being actuated, and the driver cannot tell a mid-run runner crash apart from a lost actuation.

This item proposes to make the runner channel resilient to a crash that happens mid-run:

- Detect that the runner has died.
- Surface the death as a clear, deterministic runner-crash failure.
- Where idempotency allows, recover by waiting for the runner to come back.

Without this change, a lost pinch or rotate masquerades as an assertion mismatch.

The change stays inside the xcuitest backend. It touches only the runner, the retry seam, and the deterministic verdict. A follow-on unit closes a second, distinct source of stale elements, which the on-device conformance suite surfaced later: the tap-time attribute re-check in the runner read a still-settling layout as a vanished element.

## Motivation

A single re-run of the iOS E2E workflow of one pull request showed the flake directly. Across three attempts of the `xcuitest (multi-touch)` job, one passed and two failed, each failure in a different way. The scenario is [`gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml). It pinches and rotates a two-finger target on the showcase app. It asserts that the mirrored accessibility value of each target flips to `pinched` or `rotated`.

The two failures share one cause but present differently:

- **A runner crash.** A `GET /screenshot` call raised `XcuitestChannelError: runner channel GET /screenshot failed: [Errno 61] Connection refused`. The loopback server that the runner hosts had stopped accepting connections.
- **A runner that stopped answering.** The runner did not answer `GET /health` for roughly thirty seconds, which showed up as a burst of `runner channel GET /health failed ... Connection refused` retries. By the time the runner recovered, the pinch had never landed on the app. The final assertion therefore read `expect: expected equals='pinched' but actual='idle'`.

The two-finger actuation destabilizes the runner. Timing decides whether the disturbance surfaces as a hard channel error or as a silently dropped gesture.

The flake is expensive in three ways:

- A required aggregator check (`E2E (iOS)`) that fails at random erodes trust in the gate. A maintainer cannot distinguish the failure from a real regression, so the failure invites either a reflexive re-run or a wasted investigation.
- Every re-run burns metered macOS-runner minutes. The job builds the app and boots a Simulator before it even reaches the gesture.
- The `actual='idle'` message actively misleads. It reads as though the pinch verb is broken, while the real event is that the runner dropped the gesture. An investigator looks in the wrong place.

The existing retry seam ([BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)) does not cover this case, and it was never meant to. The seam absorbs a *transient* blip within a single channel call. It attempts an eligible request up to three times in total, with a half-second exponential backoff between attempts. The whole budget is therefore spent in about one and a half seconds. A runner that is gone for thirty seconds outlives that budget many times over.

The seam is also deliberately conservative about writes. It refuses to re-issue a delivered, side-effecting request, because re-sending a pinch that may already have applied could double-actuate. Even with a larger budget, the seam could not paper over a lost gesture on its own. Recovering from a mid-run crash is a different problem from smoothing a sub-second blip, and it needs its own mechanism.

## Detailed design

The work splits into five units:

- Unit 1 and Unit 2 address the root cause on the runner side. They need on-device iteration.
- Unit 3 and Unit 4 harden the Python driver channel. Tests can cover them against a simulated transport.
- Unit 5 closes a distinct stale source that the on-device conformance suite surfaced later.

The units are ordered by dependency, but they can land as separate pull requests.

**Unit 1 — Characterize the runner crash under two-finger actuation.** Identify the root cause deterministically. Either analyze the runner source statically, or reproduce the failure on a Simulator and capture the runner-side diagnostic. The open question has three candidates:

- The pinch or rotate actuation crashes the XCUITest runner process.
- The actuation wedges the loopback HTTP server that the runner hosts.
- The `idb_companion` beneath the runner dies.

In practice, the source often answers the question directly. The serial accept loop and the backlog of 1 in `HTTPServer` make the wedge mechanistically inevitable. A fresh on-device capture is then not always required to settle which of the three candidates fails. This unit produces a diagnosis, not code.

**Unit 2 — Fix the root cause where it lives.** Unit 1 may show a defect in the gesture handling of the runner itself (the XCUITest side in [`BajutsuKit/`](../../BajutsuKit)). In that case, fix the defect there, so the two-finger actuation no longer destabilizes the runner. The fault may instead lie in the companion or a lower layer that Bajutsu does not own. In that case, this unit documents the upstream limitation and hands off to Unit 3, which makes the limitation survivable rather than fatal. The diagnosis of Unit 1 decides which path applies, so the two units are written in sequence.

**Unit 3 — Detect a mid-run crash and surface it deterministically.** Suppose the channel stays unreachable past the retry budget of a single call. The driver must then decide deterministically between two outcomes, instead of letting the run drift into a misleading assertion.

- For an idempotent read (a `GET`, or a write that never reached the runner), the driver waits for the runner to come back, reusing the existing `await_ready` health poll, and continues. Re-issuing a read is safe.
- For a side-effecting write that may already have been delivered, the driver does not silently retry. It fails with a distinct runner-crash diagnostic that names the crash. The run then stops on an honest "the runner died mid-gesture" message, not on `actual='idle'`.

The distinction is exactly the `delivered` split that the retry seam already draws. This unit therefore extends the seam instead of inventing a parallel one.

**Unit 4 — Keep every recovery visible in the record.** Prime directive 2 of [`CLAUDE.md`](../../CLAUDE.md) puts determinism first. It forbids smoothing a genuine failure into a pass, so a recovery must never pass silently. Every recovery logs the crash as visibly as the retry seam logs a retried blip. A run that crashed and recovered is then never indistinguishable from a run that never crashed. A recovered run stays reproducible and auditable. A crash that cannot be recovered idempotently stays the loud failure that Unit 3 raises.

Not re-applying a delivered write is the job of Unit 3. Unit 4 adds only the observability guarantee.

**Unit 5 — Stop a still-settling layout from being read as a vanished element.** The on-device conformance suite surfaced a second stale source, distinct from the multi-touch crash above. The opening `tap` of `test_text_selection` intermittently failed with `element vanished (stale handle)`.

At tap time, the runner re-derives the live `XCUIElement` from its recorded position path. It re-checks the recorded attributes before acting ([BE-0105](../BE-0105-xcuitest-single-snapshot-query/BE-0105-xcuitest-single-snapshot-query.md)). That re-check included the frame of the element, within a one-point tolerance.

A snapshot, however, is taken while the UI may still be settling. Moving between conformance screens animates the field into place. The same field moved 49 pt vertically between the snapshot and the tap, and the frame re-check read a still-valid element as stale.

The fix drops the frame from the identity match. The position path together with identifier, label, and traits already distinguishes a genuinely different element. The frame legitimately changes as a layout settles, so matching on the frame only turned a valid element into a false stale.

Two other suspects were ruled out first: the generation-based handle scheme and the concurrency of the HTTP server. The store's own stale path never fired, and every stale came from the attribute re-check. This unit therefore touches only `attributesMatch`, not the transport of the runner.

## Alternatives considered

**Quarantine or auto-retry the whole job in CI.** Marking `xcuitest (multi-touch)` as allowed-to-fail, or wrapping the job in a job-level retry, would turn the check green without touching the crash. We reject this option for three reasons:

- It hides a real runner defect behind a green check.
- It keeps burning macOS minutes on every silent retry.
- It weakens the on-device actuation coverage that [BE-0281](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md) is building up, instead of extending that coverage.

**Enlarge the retry seam's budget.** Raising `_MAX_ATTEMPTS` or the backoff would let a single call wait out a thirty-second outage. The case that matters would still not improve, because a delivered pinch cannot be re-issued without risking a double-actuation. The larger budget would also slow every genuine channel failure to a thirty-second crawl. The retry seam smooths sub-second blips by design. Stretching it to cover a crash conflates two different problems.

**Add a fixed settle delay after each gesture.** Sleeping after a pinch before asserting would be a fixed `sleep` that ignores the condition, which prime directive 2 forbids outright. It would not help anyway. The `expect` block of the scenario is already a bounded condition wait that re-reads the tree until its deadline. The value stays `idle` because the gesture was lost, not because the assertion reads too early. More waiting cannot recover an actuation that never landed.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — Diagnose which layer dies: the runner *process* stays alive; its loopback HTTP server
      wedges. `HTTPServer` accepts one connection at a time (a serial accept loop over a `listen`
      backlog of 1), so a `POST /gesture` that blocks the loop on the main thread (`pinch`/`rotate`)
      leaves a concurrent `GET /health` or `GET /screenshot` unserved until the backlog fills and the
      kernel refuses it — the `Connection refused` and ~30 s of refused `/health` both follow from
      that one cause. Not `idb_companion`.
- [x] Unit 2 — Fix the root cause in the runner (`BajutsuKit/`): the accept loop now hands each
      connection to a concurrent queue and the `listen` backlog is widened, so `/health` (which
      touches no shared state) stays answerable while a gesture holds the main thread — which is
      exactly what lets Unit 3's health-poll recovery tell "runner busy" from "runner dead".
      Concurrency made the previously serial `SnapshotStore` reachable from two handlers at once, so
      it is now lock-guarded. The lost-gesture `actual='idle'` case is an upstream XCUITest/idb
      multi-touch-delivery limitation, left to Unit 3's idempotent recovery.
- [x] Unit 3 — Detect a mid-run crash past the per-call retry budget and surface it deterministically:
      idempotent recovery for reads / undelivered writes, a distinct runner-crash failure otherwise.
- [x] Unit 4 — Keep every recovery visible in the record: log each recovery as visibly as a retried
      blip, so a crashed-and-recovered run is never indistinguishable from one that never crashed
      (never-re-apply-a-delivered-write is Unit 3's job).
- [x] Unit 5 — Exclude the frame from the runner's tap-time attribute re-check (`attributesMatch`), so
      an element that only moved between the snapshot and the tap — a still-settling layout — is not
      read as a vanished element. Identifier, label, and traits plus the position path still catch a
      genuinely different element.

Log:

- [#1200](https://github.com/bajutsu-e2e/bajutsu/pull/1200) — Units 3 & 4: add `XcuitestRunnerCrashError` and a `_with_crash_recovery` seam over the BE-0207 retry.
  - A mid-run crash now waits out the runner. It then re-issues an idempotent read, or a write that never reached the runner.
  - A delivered write fails loudly with a distinct crash diagnostic.
  - Every crash and recovery is logged.
  - Units 1 & 2 (the on-device diagnosis and the `BajutsuKit` root-cause fix) remain open.
- [#1202](https://github.com/bajutsu-e2e/bajutsu/pull/1202) — Units 1 & 2: diagnose the flake as a wedged loopback server, not a dead process or `idb_companion`, and fix it in `BajutsuKit/`.
  - `HTTPServer` served connections one at a time over a `listen` backlog of 1. A `pinch` or `rotate` that blocked the accept loop on the main thread starved concurrent `/health` and `/screenshot` polls until the kernel refused them. That starvation was the source of both the `Connection refused` crash and the ~30 s health outage.
  - The accept loop now dispatches each connection to a concurrent queue, and the backlog is widened. `/health` stays answerable during a gesture, so the recovery of Unit 3 can tell a busy runner from a dead one.
  - `SnapshotStore` is now reachable concurrently, so it is lock-guarded.
  - Verified with `swift test`, including `--sanitize=thread`. The diagnosis comes from static analysis of the runner source. It corroborates the two observed failure signatures, and it is not a fresh on-device capture.
- [#1211](https://github.com/bajutsu-e2e/bajutsu/pull/1211) — Unit 5: exclude the frame from the tap-time `attributesMatch` of the runner.
  - On-device conformance intermittently failed the opening `tap` of `test_text_selection` with `element vanished (stale handle)`.
  - A local reproduction looped the full xcuitest conformance suite on a Simulator. It traced the failure to the attribute re-check, which read a still-settling layout as stale. It did not trace the failure to the generation handle scheme or to HTTP concurrency, because the stale path of the store never fired.
  - The field moved 49 pt vertically (y 399 → 448) between the snapshot and the tap, while its identifier and traits were unchanged. The 1 pt frame tolerance rejected it.
  - Dropping the frame from `attributesMatch` keeps identity on identifier, label, traits, and position path.
  - Verified by looping the suite on-device with the frame excluded, where the stale no longer reproduces, and with `swift test`.

## References

- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md):
  the transient-blip retry the driver already has, and the `delivered` idempotency split this item extends.
- [BE-0281 — Add real on-device actuation coverage to the iOS CI](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md):
  the on-device actuation coverage this flake undermines.
- [BE-0019 — XCUITest backend](../BE-0019-xcuitest-backend/BE-0019-xcuitest-backend.md):
  the backend whose runner channel this item hardens, and where idb's single-touch `UnsupportedAction` sends multi-touch to the xcuitest and adb lanes.
- [`demos/showcase/scenarios/gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml):
  the pinch / rotate scenario that exposes the flake.
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py): `_with_retry`, `_is_retry_eligible`,
  and `await_ready` — the retry seam and health-poll this item builds on.

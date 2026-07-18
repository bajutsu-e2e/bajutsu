**English** · [日本語](BE-XXXX-xcuitest-runner-multitouch-resilience-ja.md)

# BE-XXXX — XCUITest runner-channel resilience under multi-touch actuation

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-runner-multitouch-resilience.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

The `xcuitest (multi-touch)` on-device end-to-end (E2E) job flakes because the XCUITest runner
becomes unreachable while a two-finger gesture is being actuated, and the driver has no way to tell a
mid-run runner crash apart from a lost actuation. This item proposes to make the runner channel
resilient to a crash that happens mid-run: detect that the runner has died, surface it as a clear,
deterministic runner-crash failure, and — where idempotency allows — recover by waiting for the
runner to come back, instead of letting a lost pinch or rotate masquerade as an assertion mismatch.
The change stays inside the xcuitest backend; the runner, the retry seam, and the deterministic
verdict are the only surfaces it touches.

## Motivation

A single re-run of one pull request's iOS E2E workflow showed the flake directly: across three
attempts of the `xcuitest (multi-touch)` job, one passed and two failed, each in a different way.
The scenario is [`gestures_multitouch.yaml`](../../demos/showcase/scenarios/gestures_multitouch.yaml),
which pinches and rotates a two-finger target on the showcase app and asserts that each target's
mirrored accessibility value flips to `pinched` / `rotated`.

The two failures share one cause but present differently. In the first, the runner crashed outright:
a `GET /screenshot` call raised `XcuitestChannelError: runner channel GET /screenshot failed:
[Errno 61] Connection refused`, because the loopback server the runner hosts had stopped accepting
connections. In the second, the runner did not answer `GET /health` for roughly thirty seconds — a
burst of `runner channel GET /health failed ... Connection refused` retries — and by the time it
recovered, the pinch had never landed on the app, so the final assertion read `expect: expected
equals='pinched' but actual='idle'`. The two-finger actuation destabilizes the runner; whether the
disturbance surfaces as a hard channel error or as a silently dropped gesture is a matter of timing.

This flake is expensive in three ways. A required aggregator check (`E2E (iOS)`) that fails at
random erodes trust in the gate and, because a maintainer cannot distinguish it from a real
regression, invites either a reflexive re-run or a wasted investigation. Every re-run burns metered
macOS-runner minutes on a job that builds the app and boots a Simulator before it even reaches the
gesture. Worst of all, the `actual='idle'` message actively misleads: it reads as though the pinch
verb is broken, when the real event is that the runner dropped the gesture — so an investigator
looks in the wrong place.

The existing retry seam ([BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md))
does not cover this case, and it was never meant to. That seam absorbs a *transient* blip within a
single channel call: it retries an eligible request up to three times with a half-second exponential
backoff, so its whole budget is spent in about one and a half seconds. A runner that is gone for
thirty seconds outlives that budget many times over. The seam is also deliberately conservative about
writes — it refuses to re-issue a delivered side-effecting request, because re-sending a pinch that
may already have applied could double-actuate — so it cannot, on its own, paper over a lost gesture
even if the budget were larger. Recovering from a mid-run crash is a different problem from smoothing
a sub-second blip, and it needs its own mechanism.

## Detailed design

The work splits into four independent units. Unit 1 and Unit 2 address the root cause on the runner
side and need on-device iteration; Unit 3 and Unit 4 harden the Python driver channel and are unit-
testable against a simulated transport. The units are ordered by dependency but can land as separate
pull requests.

**Unit 1 — Characterize the runner crash under two-finger actuation.** Reproduce the failure on a
Simulator and capture the runner-side diagnostic, so the root cause is known rather than inferred.
The open question is whether the pinch / rotate actuation itself crashes the XCUITest runner process,
whether it wedges the loopback HTTP server the runner hosts, or whether the `idb_companion` beneath
it is the one that dies. Collect the runner's crash log and console output for a reproducing run, and
record which of the three actually fails. This unit produces a diagnosis, not code.

**Unit 2 — Fix the root cause where it lives.** If Unit 1 shows a defect in the runner's own gesture
handling (the XCUITest side in [`BajutsuKit/`](../../BajutsuKit)), fix it there so the two-finger
actuation no longer destabilizes the runner. If the fault is in the companion or a lower layer that
Bajutsu does not own, this unit instead documents the upstream limitation and hands off to Unit 3,
which makes the limitation survivable rather than fatal. Which path applies is decided by Unit 1's
diagnosis, so the two units are written in sequence.

**Unit 3 — Detect a mid-run crash and surface it deterministically.** When the channel stays
unreachable past a single call's retry budget, the driver must decide, deterministically, between two
outcomes rather than letting the run drift into a misleading assertion. For an idempotent read (a
`GET`, or a write that never reached the runner), wait for the runner to come back — reusing the
existing `await_ready` health-poll — and continue, because re-issuing a read is safe. For a
side-effecting write that may already have been delivered, do not silently retry it; instead fail
with a distinct runner-crash diagnostic that names the crash, so the run stops on an honest "the
runner died mid-gesture" message rather than on `actual='idle'`. The distinction is exactly the
`delivered` split the retry seam already draws, so this unit extends that seam rather than inventing a
parallel one.

**Unit 4 — Keep the recovery from absorbing real failures.** Determinism-first
([`CLAUDE.md`](../../CLAUDE.md) prime directive 2) forbids smoothing a genuine failure into a pass, so
the recovery path carries two guardrails. It must never re-apply a delivered write — the
`_is_retry_eligible` rule stays the gate — and every recovery must log the crash as visibly as the
retry seam logs a retried blip, so a run that crashed and recovered is never indistinguishable from
one that never crashed. A recovered run stays reproducible and auditable; a crash that cannot be
recovered idempotently stays a loud failure.

## Alternatives considered

**Quarantine or auto-retry the whole job in CI.** Marking `xcuitest (multi-touch)` as
allowed-to-fail, or wrapping it in a job-level retry, would turn the check green without touching the
crash. We reject it: it hides a real runner defect behind a green check, it keeps burning macOS
minutes on every silent retry, and it weakens the on-device actuation coverage that
[BE-0281](../BE-0281-ios-on-device-actuation-coverage/BE-0281-ios-on-device-actuation-coverage.md)
is building up rather than tearing down.

**Enlarge the retry seam's budget.** Raising `_MAX_ATTEMPTS` or the backoff so a single call waits
out a thirty-second outage would still not help the case that matters — a delivered pinch cannot be
re-issued without risking a double-actuation — and it would slow every genuine channel failure to a
thirty-second crawl. The retry seam smooths sub-second blips by design; stretching it to cover a
crash conflates two different problems.

**Add a fixed settle delay after each gesture.** Sleeping after a pinch before asserting would be a
fixed `sleep` that ignores the condition, which prime directive 2 forbids outright — and it would not
help anyway, because the scenario's `expect` block is already a bounded condition wait that re-reads
the tree until its deadline. The value stays `idle` not because the assertion reads too early but
because the gesture was lost; more waiting cannot recover an actuation that never landed.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — Reproduce the crash on a Simulator and capture the runner-side diagnostic; record
      whether the runner process, its loopback server, or `idb_companion` is the one that dies.
- [ ] Unit 2 — Fix the root cause in the runner (`BajutsuKit/`) if it is a runner defect, or document
      the upstream limitation and hand off to Unit 3.
- [ ] Unit 3 — Detect a mid-run crash past the per-call retry budget and surface it deterministically:
      idempotent recovery for reads / undelivered writes, a distinct runner-crash failure otherwise.
- [ ] Unit 4 — Guard the recovery: never re-apply a delivered write, and log every recovery as
      visibly as a retried blip.

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

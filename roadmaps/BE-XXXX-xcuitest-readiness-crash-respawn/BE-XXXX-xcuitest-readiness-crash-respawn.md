**English** · [日本語](BE-XXXX-xcuitest-readiness-crash-respawn-ja.md)

# BE-XXXX — Recover the XCUITest cold launch when the runner crashes during the readiness gate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-readiness-crash-respawn.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#TBD](https://github.com/bajutsu-e2e/bajutsu/pull/TBD) |
| Topic | Platform support |
| Related | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md) |
<!-- /BE-METADATA -->

## Introduction

The run pipeline gained a backend-crash recovery (PR
[#1368](https://github.com/bajutsu-e2e/bajutsu/pull/1368)): a mid-scenario `base.BackendCrashError`
is treated as infrastructure, not a verdict — the dead lease is discarded, a fresh device is leased
(a cold respawn), and the whole scenario is re-run, bounded by `crash_retries`. Two residual gaps
still let the crash that motivated it fail the run, and this item closes both. First, the recovery
leases *inside* its retry loop but *outside* its `try`, so a crash during the **lease itself** — the
launch and readiness gate, before any scenario step — escapes the loop and fails the run. Second,
when the runner's `xcodebuild` **process has exited**, the crash-recovery layer still polls
`GET /health` for the full 60-second recovery window before giving up, spending a minute on a
recovery that cannot come. This item moves the lease inside the retry so a bring-up crash is
recovered like any other, and fails the recovery fast when the runner process is gone.

## Motivation

The required `run (xcuitest)` job flaked with a setup-time failure, not a scenario assertion:

```
XcuitestRunnerCrashError: runner channel GET /elements failed: the runner crashed mid-run and did
not recover within 60s
```

The traceback places the failure in the launch path — `launch_driver` → `_await_ready` →
`driver.query()` → `GET /elements` — before any scenario body ran. The sequence is:

1. The cold spawn (`_spawn_cold_with_retry`) waits for the runner to answer `GET /health` and hands
   back a driver: the runner *did* come up.
2. `launch_driver` calls `_await_ready`, whose first `driver.query()` issues `GET /elements`.
3. The runner crashes here — the app-launch/screen-appearance crash
   [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
   and [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)
   describe, reproduced only on loaded CI hosts — and its `xcodebuild` process exits.
4. The crash-recovery layer (`_with_crash_recovery`) catches the transport failure, and — because a
   `GET` is idempotent — waits for the runner to come back by polling `GET /health` for the
   60-second recovery window. The process is gone, nothing respawns it, so the poll refuses for the
   full 60 seconds and then raises `XcuitestRunnerCrashError`.
5. That error surfaces out of `_await_ready` and out of `launch_driver` — which is what the pipeline
   calls to build a lease. The pipeline's crash recovery leases inside its retry loop, but the
   `lease` call sits *outside* the `try` that catches `BackendCrashError`, so a lease-time crash is
   never caught: it propagates out of `run_all` and fails the whole run.

The crash surfaced on the branch that already carries #1368's recovery, so the recovery demonstrably
does not cover this window. It does not, for two independent reasons:

- **The crash is at lease time, not step time.** #1368 wraps `_run_on_lease` — the scenario body — in
  the crash-retry `try`, but the `self.lease(...)` that precedes it (which runs `launch_driver` and
  its readiness gate) is outside that `try`. A crash raised while *building* the lease is exactly the
  case the retry does not see.
- **The recovery spends the whole window on a dead process.** Even once the crash is caught, the
  60-second poll of a runner whose process has exited is dead time before an inevitable failure.
  [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md)
  already fails a *cold spawn* fast when its `xcodebuild` process dies, but the mid-run crash-recovery
  path has no such liveness check.

The result is the flaky-gate cost
[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
set out to remove: a red required check carrying no signal about whether anything is actually wrong,
cleared only by re-running the job by hand.

## Detailed design

The work breaks into three independent units. Units 1 and 2 are the two gaps above — recover the
bring-up crash, then stop spending the window on a dead process — and unit 3 tests both over
injectable seams, no Simulator required.

1. **Lease inside the crash-retry `try`.** In `_ScenarioRunner.run_one`
   (`bajutsu/runner/pipeline.py`), move the `self.lease(...)` call from just before the `try` to
   inside it, so a `base.BackendCrashError` raised while building the lease — the launch and
   readiness gate — is caught by the same recovery that already re-runs a scenario whose step
   crashed. The retry then leases afresh (a cold respawn, since the pool drops the dead warm runner),
   exactly as for a step-time crash. `_run_on_lease` keeps releasing the lease in its own `finally`,
   so a mid-step crash still never leaks the lease; a lease-time crash leaves no lease to release (the
   pool tears down its own failed lease), so the move adds no leak. The `crash_retries` bound is
   unchanged, so a bring-up that crashes every attempt still fails loudly
   ([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)).

2. **Fail fast in crash-recovery when the runner process has exited.** The crash-recovery layer
   (`_with_crash_recovery` in `bajutsu/drivers/xcuitest.py`) waits out the recovery window on the
   assumption that the runner will come back. That assumption splits cleanly on the runner
   *process*: a process still alive but momentarily unreachable is
   [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)'s
   recoverable case and must keep waiting; a process that has **exited** will never answer `/health`
   again — nothing respawns it on that port mid-recovery — so waiting the full window is dead time.
   Thread an optional runner-liveness predicate (`runner_alive: () -> bool`) from the environment,
   which owns the `xcodebuild` subprocess handle, through `make_driver` into the crash-recovery layer.
   On a crash, if the predicate reports the process gone, fail immediately with a distinct diagnostic
   instead of polling the dead port; the pipeline's unit-1 recovery then leases a fresh device and
   re-runs the scenario. Absent (a test fake) or reporting the process alive, the behavior is exactly
   BE-0287's — so this only ever *shortens* an already-doomed wait, never converts a recoverable blip
   into a failure.

3. **Off-device tests over both seams.** Both units are exercisable without a Simulator, the same
   isolation the pipeline and channel tests already use by injecting fakes. Cover: a lease whose
   bring-up raises `BackendCrashError` is recovered on the retry's fresh lease, and one that crashes
   every attempt fails loudly after exactly the bounded number of leases (unit 1, in
   `tests/runner/test_pipeline.py`); a crash whose liveness predicate reports the process **gone**
   fails fast without polling the recovery window, one whose predicate reports it **alive** still
   waits the window (BE-0287 intact), and the absent-predicate default is unchanged (unit 2, in
   `tests/test_xcuitest.py`).

## Alternatives considered

- **Retry only inside `launch_driver` instead of the pipeline.** A launch-local retry would respawn
  the runner without the pipeline knowing, duplicating the recovery the pipeline already owns for
  step-time crashes and drawing a second, parallel boundary around "the backend crashed". Moving the
  lease inside the pipeline's existing `try` reuses one recovery for both the bring-up and the step
  crash. Rejected in favor of the single seam.
- **Shorten the 60-second recovery window globally.** A shorter window would fail this case faster
  but also cut the real BE-0287 recovery (a runner unreachable for ~30s that *does* come back) off
  early, reintroducing the flake that window exists to ride out. Unit 2 keys the fast path on the
  *process* being gone, not on a shorter clock, so the recoverable case keeps its full window.
  Rejected.
- **An unbounded retry.** Retrying without a bound would absorb a real, repeatable crash and mask a
  broken build or app, exactly the absorption
  [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) rejects.
  The recovery keeps #1368's `crash_retries` bound; the only change is *which* crashes it catches.
- **A job-level rerun (a GitHub re-run or `pytest-rerunfailures`).** A rerun hides the flake rather
  than removing its cause, re-does the whole job (build included) to recover from one bad spawn, and
  still spends the 60-second dead-poll on the way down. The in-process recovery is cheaper and keeps
  the run green in one pass. A manual job rerun stays a complementary operational fallback, not the
  fix.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — lease inside the pipeline's crash-retry `try`, so a bring-up (launch/readiness) crash
  is recovered like a step-time crash.
- [x] Unit 2 — fail fast in crash-recovery when the runner process has exited (liveness predicate
  threaded from the environment; BE-0287's process-alive case unchanged).
- [x] Unit 3 — off-device tests over the pipeline and channel seams.

## References

- [PR #1368](https://github.com/bajutsu-e2e/bajutsu/pull/1368) — the pipeline backend-crash recovery this completes (`base.BackendCrashError`, `crash_retries`).
- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
- [BE-0218 — Stabilize the E2E Simulator gate](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
- [BE-0310 — iOS accessibility screen-change readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)
- [BE-0319 — Make the XCUITest cold runner spawn diagnosable and self-healing](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md)
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
- `bajutsu/runner/pipeline.py` — `_ScenarioRunner.run_one` (the crash-retry loop the lease moves inside).
- `bajutsu/drivers/xcuitest.py` — `_with_crash_recovery`, `_http_transport`, `XcuitestDriver` (the channel and its crash-recovery seam).
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — `XcuitestEnvironment` (owns the `xcodebuild` subprocess handle the liveness predicate reads).

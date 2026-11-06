**English** · [日本語](BE-0323-xcuitest-readiness-crash-respawn-ja.md)

# BE-0323 — Recover the XCUITest cold launch when the runner crashes during the readiness gate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0323](BE-0323-xcuitest-readiness-crash-respawn.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0323") |
| Implementing PR | [#1358](https://github.com/bajutsu-e2e/bajutsu/pull/1358) |
| Topic | Platform support |
| Related | [BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md), [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md) |
<!-- /BE-METADATA -->

## Introduction

The run pipeline gained a backend-crash recovery (PR
[#1368](https://github.com/bajutsu-e2e/bajutsu/pull/1368)). A mid-scenario `base.BackendCrashError`
counts as infrastructure, not a verdict. The recovery discards the dead lease and leases a fresh
device (a cold respawn). Then it re-runs the whole scenario, bounded by `crash_retries`.

Two residual gaps still let the original crash fail the run. This item closes both.

- **A crash during the lease itself.** The recovery leases *inside* its retry loop but *outside* its
  `try`. A crash during the launch and readiness gate escapes the loop. That gate runs before any
  scenario step, and the escape fails the run.
- **A dead runner that is still polled.** Suppose the runner's `xcodebuild` **process has exited**.
  The crash-recovery layer still polls `GET /health` for the full 60-second recovery window. The
  layer spends a minute on a recovery that cannot come.

This item moves the lease inside the retry, so a bring-up crash recovers like any other crash. The
item also fails the recovery fast when the runner process is gone.

The first fix's own CI run exposed the layer beneath. On a contended CI host, the resident runner
crashes *repeatedly*. Both the initial launch and the respawned retry can die. The `conformance` gate
is a pytest suite, not a `bajutsu run`, so it never reaches the pipeline recovery at all.

Chasing the crash to its source, we found two facts about the runner. Its HTTP server serves
connections concurrently. And `app.snapshot()` and `app.screenshot()` pump the main run loop while
they wait on the app.

Two concurrent `DispatchQueue.main.sync` operations then re-enter. One is a scenario `/elements`, and
the other is an evidence `/screenshot`. The second XCUITest call runs on the main thread *inside* the
first. XCUITest forbids that re-entry, and the XCTest host aborts.

This item also **serializes the runner's XCUITest operations**. The serialization removes the
re-entrancy at the source and helps every job, `conformance` included. The item also makes the
**crash-retry budget configurable**. The metered on-device lane can then ride out a residual one-off
crash.

## Motivation

The required `run (xcuitest)` job flaked with a setup-time failure, not a scenario assertion:

```
XcuitestRunnerCrashError: runner channel GET /elements failed: the runner crashed mid-run and did
not recover within 60s
```

The traceback places the failure in the launch path, before any scenario body ran: `launch_driver` →
`_await_ready` → `driver.query()` → `GET /elements`. The sequence runs as follows.

1. The cold spawn (`_spawn_cold_with_retry`) waits for the runner to answer `GET /health`. Then it
   hands back a driver. The runner *did* come up.
2. `launch_driver` calls `_await_ready`. The first `driver.query()` there issues `GET /elements`.
3. The runner crashes here, and its `xcodebuild` process exits. This is the app-launch and
   screen-appearance crash that
   [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
   and [BE-0310](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)
   describe. The crash reproduces only on loaded CI hosts.
4. The crash-recovery layer (`_with_crash_recovery`) catches the transport failure. A `GET` is
   idempotent, so the layer waits for the runner by polling `GET /health` for the 60-second window.
   The process has gone, and nothing respawns it. The poll meets refusals for the full 60 seconds.
   Then the layer raises `XcuitestRunnerCrashError`.
5. The error surfaces out of `_await_ready` and out of `launch_driver`. The pipeline calls
   `launch_driver` to build a lease. The pipeline's crash recovery leases inside its retry loop.
   But the `lease` call sits *outside* the `try` that catches `BackendCrashError`. Nothing catches a
   lease-time crash, so the crash propagates out of `run_all` and fails the whole run.

The crash surfaced on a branch that already carried #1368's recovery. The recovery demonstrably does
not cover this window, for two independent reasons.

- **The crash is at lease time, not step time.** #1368 wraps `_run_on_lease` (the scenario body) in
  the crash-retry `try`. The `self.lease(...)` call precedes `_run_on_lease`. That call runs
  `launch_driver` and its readiness gate, and it sits outside the `try`. A crash raised while
  *building* the lease is exactly the case the retry does not see.
- **The recovery spends the whole window on a dead process.** Suppose the recovery catches the crash.
  Polling a runner whose process has exited for 60 seconds is still dead time before an inevitable
  failure.
  [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md)
  already fails a *cold spawn* fast when its `xcodebuild` process dies. The mid-run crash-recovery
  path has no such liveness check.

The result is the flaky-gate cost
[BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
set out to remove. A red required check carries no signal about whether anything is wrong. Only a
manual re-run of the job clears the check.

## Detailed design

The work breaks into five units. Units 1 and 2 close the two gaps above. Unit 1 recovers the bring-up
crash, and unit 2 stops spending the window on a dead process. Unit 3 tests both over injectable
seams.

Units 4 and 5 close the re-entrancy crash beneath them. Unit 4 serializes the runner's XCUITest
operations (the root cause). Unit 5 raises the crash-retry budget as its safety margin. No Simulator
is required.

1. **Lease inside the crash-retry `try`.** In `_ScenarioRunner.run_one`
   (`bajutsu/runner/pipeline.py`), move the `self.lease(...)` call from just before the `try` to
   inside it. The same recovery then catches a `base.BackendCrashError` raised while building the
   lease. That recovery already re-runs a scenario whose step crashed.

   The retry leases afresh, exactly as for a step-time crash. The retry is a cold respawn, since the
   pool drops the dead warm runner.

   `_run_on_lease` keeps releasing the lease in its own `finally`. A mid-step crash thus never leaks
   the lease. A lease-time crash leaves no lease to release, because the pool tears down its own
   failed lease. The move adds no leak.

   The `crash_retries` bound is unchanged. A bring-up that crashes every attempt still fails loudly
   ([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)).

2. **Fail fast in crash-recovery when the runner process has exited.** The crash-recovery layer
   (`_with_crash_recovery` in `bajutsu/drivers/xcuitest.py`) waits out the recovery window. It
   assumes that the runner will come back. That assumption splits cleanly on the runner *process*.

   - A process that is alive but momentarily unreachable is
     [BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)'s
     recoverable case. The layer must keep waiting.
   - A process that has **exited** will never answer `/health` again. Nothing respawns it on that
     port mid-recovery. Waiting the full window is dead time.

   Thread an optional runner-liveness predicate (`runner_alive: () -> bool`) from the environment
   through `make_driver` into the crash-recovery layer. The environment owns the `xcodebuild`
   subprocess handle.

   On a crash, the layer asks the predicate. If the predicate reports the process gone, the layer
   fails immediately with a distinct diagnostic and skips polling the dead port. The unit-1 pipeline
   recovery then leases a fresh device and re-runs the scenario.

   Suppose the predicate is absent (a test fake) or reports the process alive. The behavior then
   matches BE-0287 exactly. Unit 2 shortens an already-doomed wait. It never converts a recoverable
   blip into a failure.

3. **Off-device tests over both seams.** Units 1 and 2 are exercisable without a Simulator. The tests
   inject fakes, the same isolation the pipeline and channel tests already use. They cover these
   cases.

   - Unit 1, in `tests/runner/test_pipeline.py`: a lease whose bring-up raises `BackendCrashError`
     recovers on the retry's fresh lease. A lease that crashes every attempt fails loudly after
     exactly the bounded number of leases.
   - Unit 2, in `tests/test_xcuitest.py`: a crash whose predicate reports the process **gone** fails
     fast. It never polls the recovery window. A crash whose predicate reports the process **alive**
     still waits the window (BE-0287 intact). The absent-predicate default is unchanged.

4. **Serialize the runner's XCUITest operations to remove the re-entrancy crash (root cause).** The
   resident runner's HTTP server (`BajutsuRunner`) handles connections concurrently. The concurrency
   is deliberate: a `/health` poll stays answerable during a long gesture (BE-0287).

   Every XCUITest call moves to the main thread through `DispatchQueue.main.sync`. But
   `app.snapshot()`, `app.screenshot()`, and an interaction pump the main run loop while they wait on
   the app. That spin drains the main dispatch queue. A second concurrent operation's block then runs
   *inside* the first and re-enters XCUITest. The re-entry aborts the XCTest host.

   This is the CI-only mid-run crash. A contended host widens the window.

   Add a lock (`Router.actuationLock`). The *connection* thread holds the lock before dispatching to
   main. A second operation then never enqueues onto main while the first is in flight. The run-loop
   spin has nothing re-entrant to drain.

   `/health` never takes the lock, because it touches no XCUITest state. The concurrent server keeps
   its "runner busy, not dead" signal.

   This is the one fix that reaches the `conformance` job. That job never runs through the pipeline
   recovery of units 1 and 2. A `BajutsuRunner` test covers the fix. The test drives two concurrent
   reads off the main thread and asserts that they never overlap or re-enter.

5. **Make the crash-retry budget configurable, raised for the on-device lane.** The pipeline's
   `crash_retries` was a hard-coded default of 1 (two attempts). Read the budget from
   `BAJUTSU_CRASH_RETRIES` (unset keeps 1). The metered on-device CI lane then raises the budget
   without a code change. Set it to 2 (three attempts) there.

   The runner crash is the *test host* dying, not an app verdict. Riding out a one-off crash is not
   flakiness-by-absorption. A scenario that crashes every attempt still fails once the budget is
   spent ([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)).
   The raised budget is the safety margin *behind* unit 4's root-cause fix, not a substitute for it.

   Tests cover two points. The environment variable sets the default (unset or invalid → 1, `0`
   disables). And `run_all` honors that default end to end.

## Alternatives considered

- **Retry inside `launch_driver` instead of the pipeline.** A launch-local retry would respawn the
  runner without the pipeline knowing. It would duplicate the recovery that the pipeline already owns
  for step-time crashes. It would also draw a second, parallel boundary around "the backend crashed".
  Moving the lease inside the pipeline's existing `try` reuses one recovery for the bring-up crash and
  the step crash. Rejected in favor of the single seam.
- **Shorten the 60-second recovery window globally.** A shorter window would fail this case faster.
  It would also cut off the real BE-0287 recovery early: a runner unreachable for about 30 seconds
  that *does* come back. That cut would reintroduce the flake the window exists to ride out. Unit 2
  keys the fast path on the *process* being gone, not on a shorter clock. The recoverable case keeps
  its full window. Rejected.
- **An unbounded retry.** Retrying without a bound would absorb a real, repeatable crash and mask a
  broken build or app.
  [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) rejects
  exactly this absorption. The recovery keeps #1368's `crash_retries` bound. The change alters
  *which* crashes the recovery catches.
- **A job-level rerun (a GitHub re-run or `pytest-rerunfailures`).** A rerun hides the flake rather
  than removing its cause. It re-does the whole job (build included) to recover from one bad spawn.
  It still spends the 60-second dead poll on the way down. The in-process recovery is cheaper and
  keeps the run green in one pass. A manual job rerun stays a complementary operational fallback, not
  the fix.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — lease inside the pipeline's crash-retry `try`, so a bring-up (launch/readiness) crash
  is recovered like a step-time crash.
- [x] Unit 2 — fail fast in crash-recovery when the runner process has exited (liveness predicate
  threaded from the environment; BE-0287's process-alive case unchanged).
- [x] Unit 3 — off-device tests over the pipeline and channel seams.
- [x] Unit 4 — serialize the runner's XCUITest operations (`Router.actuationLock`) to remove the
  concurrent-snapshot re-entrancy that aborts the XCTest host; reaches the `conformance` gate too.
- [x] Unit 5 — make the crash-retry budget configurable (`BAJUTSU_CRASH_RETRIES`), raised to 2 on the
  on-device lane as the safety margin behind unit 4.

## References

- [PR #1368](https://github.com/bajutsu-e2e/bajutsu/pull/1368) — the pipeline backend-crash recovery this completes (`base.BackendCrashError`, `crash_retries`).
- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md)
- [BE-0218 — Stabilize the E2E Simulator gate](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
- [BE-0310 — iOS accessibility screen-change readiness](../BE-0310-ios-accessibility-screen-change-readiness/BE-0310-ios-accessibility-screen-change-readiness.md)
- [BE-0319 — Make the XCUITest cold runner spawn diagnosable and self-healing](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md)
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
- `bajutsu/runner/pipeline.py` — `_ScenarioRunner.run_one` (the crash-retry loop the lease moves inside), `_default_crash_retries` (the `BAJUTSU_CRASH_RETRIES` budget).
- `bajutsu/drivers/xcuitest.py` — `_with_crash_recovery`, `_http_transport`, `XcuitestDriver` (the channel and its crash-recovery seam).
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — `XcuitestEnvironment` (owns the `xcodebuild` subprocess handle the liveness predicate reads).
- `BajutsuKit/Sources/BajutsuRunner/Router.swift` — `Router.actuationLock` (serializes the runner's XCUITest operations to remove the re-entrancy crash).
- `.github/workflows/ios-e2e.yml` — the on-device lane's `BAJUTSU_CRASH_RETRIES` budget.

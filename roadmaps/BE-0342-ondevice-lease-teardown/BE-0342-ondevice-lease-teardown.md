**English** · [日本語](BE-0342-ondevice-lease-teardown-ja.md)

# BE-0342 — Give the on-device suites' lease a teardown that reaches the runner

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0342](BE-0342-ondevice-lease-teardown.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0342") |
| Implementing PR | [#1491](https://github.com/bajutsu-e2e/bajutsu/pull/1491) |
| Topic | Platform support |
| Related | [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0305](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection.md), [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) |
<!-- /BE-METADATA -->

## Introduction

Two of Bajutsu's continuous integration (CI) jobs drive a real iOS Simulator from a `pytest` harness
rather than through `bajutsu run`. They are the driver conformance suite and the fault-injection
suite. Both suites share one device across a module. Both discard that shared lease when a test
finds the resident runner dead, so the next test starts from a freshly launched runner.
[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md)
built that discard as `LeaseHolder.invalidate()`.

On the iOS backend, the discard never reaches the runner. It calls `close()` on the
[driver](../../docs/glossary.md#driver-backend-actuator-platform), and the XCUITest driver has no
`close()`. The `xcodebuild` process that hosts the runner therefore keeps running, and XCTest goes
on restarting the runner inside it. The next test launches a second runner on the same Simulator.
Only one automation session can exist there, so the two runners take it from each other.

This item gives the lease a teardown that reaches the runner process. A discarded lease is then gone
before the next one starts.

## Motivation

Each suite shares one device across its whole module, so a discarded lease is the module's problem.
The runner that a discard fails to stop still holds the device that the next test in the module
leases. On 2026-08-05, the fault-injection job left both runners in its uploaded logs. The
timestamps put them on the device at the same moment:

| | first runner (port 50870) | second runner (port 52816) |
|---|---|---|
| launched | 04:14:15 | **04:16:24** |
| last served a request | `Find the Application`, to 04:16:16 | `Find the Application`, to ≈04:16:32 |
| then | **04:16:33 restarted its host process** | 04:16:57 restarted, ran 0 tests |

Both restarts report the same line. XCTest writes it when the process hosting the tests dies and it
starts another:

```
Restarting after unexpected exit, crash, or test timeout; summary will include totals from previous launches.
```

The second runner is the one that the failing test had just launched. It answered some requests and
then died. The test that launched it saw its channel refuse a connection, and reported the runner as
crashed:

```
FAILED tests/test_fault_injection_ondevice.py::test_a_killed_runner_fails_loudly_with_a_crash_diagnosis
 - XcuitestRunnerCrashError: runner channel GET /elements failed:
   the runner crashed mid-run and did not recover within 60s
```

That failure arrives at the test's *first* statement, the property access that launches the lease.
No fault that the test itself injects can explain it. The test injects its fault later in its body.

The first runner explains the failure, because nothing had stopped it. `LeaseHolder.invalidate()`
calls `dead.close()` inside a `try`. The `except Exception` logs at debug level and moves on. That
call is not merely unimplemented. It lies outside the interface that the lease holds.

`Driver` is the Protocol that a lease's value satisfies, and it declares no `close()` at all.
`close()` belongs to the separate `BackendLifecycle` Protocol. The `platform_lifecycle` environments
reach it through `cast(base.BackendLifecycle, driver)`, under a platform invariant that has already
established which backend they hold.

Of the drivers that the tree ships (fake, adb, Playwright, XCUITest, and the live XCUITest route),
only the Playwright driver implements `close()`. For a browser, the driver owns the context that it
would close. On iOS and Android, the runner process belongs to the environment.

So on the iOS backend, `dead.close()` raises `AttributeError`. The `except Exception` swallows the
error, and `invalidate()` returns having discarded nothing. Three things kept that silent:

- The log line sits at debug level.
- mypy never sees the call. It runs over `bajutsu demos scripts`, not over `tests/`.
- Every fake driver in the harness's own off-device suite
  (`tests/runner/test_backend_crash_recovery.py`) defines a `close()`. Those cases exercise the
  shape that only the Playwright driver has, and never the XCUITest shape that these suites lease.

Both places that describe the discard thus describe something that does not happen. The docstring of
`invalidate()` says "Discard the current (dead) lease so the next `driver` access cold-respawns".
The fault-injection suite's comment on the fixture that the discard re-leases from says "the
killed-runner case discards it so the next case respawns onto a fresh device instead of inheriting
the dead runner".

The consequence is the failure mode that BE-0334 set out to remove. That item argued that a
Simulator fault during the conformance suite reddens `E2E (iOS)`, a required check, on a pull
request that could not have caused the fault. The harness should therefore classify infrastructure
faults and recover from them the way the run pipeline does. A discard that leaves the old runner
alive turns its own recovery into the next fault. The re-lease that the discard performs is what
puts a second runner on the device.

Which check the defect reaches depends on the suite. The fault-injection lane, which produced the
logs above, was at the time deliberately kept out of the `needs:` of the `E2E (iOS)` aggregate,
alongside `actuation`, `golden`, and `visual`. A red result there blocked no merge. That is why the
defect surfaced on a non-gating signal (BE-0305 has since promoted that lane onto the gate). The
conformance suite discards a lease the same way, and it *is* in that aggregate. The same defect
there lands on the required check.

Nothing about this depends on any one branch. The same test fails with the same message in [run
30971636417](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30971636417), on an unrelated
branch whose changes touch none of this machinery. That rules out reading the failure as a
regression from whatever is in flight. The logs quoted above are from [run
30971507268](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30971507268).

## Detailed design

The work breaks into three units. Unit 1 is the fix. Unit 2 stops the class of silence that hid the
defect. Unit 3 is the off-device coverage that keeps both from regressing.

1. **Let the lease tear down the environment that owns the runner.** The launch thunk that a suite
   supplies returns its teardown alongside its driver. The `LeaseHolder` then discards through the
   platform's own teardown instead of through `driver.close()`.

   Each suite's `_backend_launch` already calls `launch_driver`, which accepts a prepared
   `environment`. The thunk builds an environment on each call, passes it, and returns a teardown
   that closes over that environment and driver. The thunk builds the environment, not the
   module-scoped fixture.

   This widens the opt-in contract of the plugin, so change the contract in the same breath. The
   `launch` type of `LeaseHolder` and the module docstring both name the driver alone today. The
   docstring specifies "a zero-arg callable returning a fresh `base.Driver`". The two descriptions
   that this item's Motivation quotes need no edit, because unit 1 is what makes them true.

   Build a **fresh environment per lease** rather than retaining one across re-leases. A retained
   XCUITest environment would make every later cold spawn an in-place respawn. An in-place respawn
   takes the lane's tighter respawn ceiling (90s) instead of the cold one (300s), on exactly the
   contended host that this item is about. A retained environment would also hand `teardown` a
   driver from an earlier lease.

   For the iOS backend, the teardown then runs two steps. It terminates the `xcodebuild` host
   process, which ends the XCTest session that the runner lives in. It also terminates the app under
   test through `simctl`. This machinery already exists, and until now had no path from these
   suites. The web environment's own `teardown` is already that `close()` on the browser context, so
   the lease needs no per-backend branch.

   Unit 1 also has to cover a launch that fails partway. `launch_driver` calls `env.start`, which
   returns a driver. It then calls `_await_ready`, which can raise on a runner that dies during its
   readiness probe (`bajutsu/runner/launch.py:79-82`). Today that exception propagates out of
   `launch_driver` without handing the driver back. The thunk that called `launch_driver` holds the
   environment but has no driver to pass to `env.teardown(driver, eff)`.

   The run pipeline already guards this same seam. `bajutsu/runner/pool.py:371-383` tears the
   environment down inside `except BaseException:`, and still lets the original launch error
   propagate. Unit 1 still owes one decision. Either `launch_driver` absorbs the same guard itself,
   so every caller gains it, or the suite's own thunk wraps the call and tears down on its own
   catch. The decision is not an implementation detail to leave implicit.

2. **Extract the guarded teardown, and stop swallowing one that could not run.** A *mid-run* discard
   runs on a failure path, where raising would mask the fault that prompted it. Catching there is
   right. Logging at debug level is not, because a teardown that is *structurally* impossible then
   looks identical to one that merely failed this time. A missing method should reach a maintainer
   on the first run, and not sit behind a log level that a reader has to raise.

   The run pipeline already has a policy for what may be swallowed. Its three teardown sites each
   warn on a `CalledProcessError` or an `OSError`, such as a runner that had already exited or an
   unreachable `xcrun`. Anything else surfaces. Restating that policy in the harness would put a
   fourth copy of the same pair of exception classes in the tree, held in sync by nothing but a
   comment.

   Extract the guarded teardown into one helper instead, and have the three sites of the pool and
   the lease all call it. `bajutsu/runner/recovery.py` is where the helper belongs. The harness
   already borrows the pipeline's retry count and recovery budget from that module by import rather
   than by restatement. That makes "the two recovery paths do not drift" a property of the code
   instead of a promise in prose.

   Whether a wiring defect surfaces depends on which path called the helper. A mid-run discard
   swallows the defect into the warning. Two of that path's three call sites are `finally` blocks in
   the fault-injection suite, and one of them guards the `BackendCrashError` that a test asserts.
   The third is the plugin's own discard, which runs outside any test. There, an escaping exception
   would abort the session instead of failing one case. On the module's **final** release, let a
   wiring defect fail the module teardown. No fault is in flight at that point, and a runner that
   survives leaks into the rest of the job.

3. **Pin the teardown off device.** The harness is already exercisable without a Simulator.
   `tests/runner/test_backend_crash_recovery.py` drives the plugin through `pytester` with fake
   launch thunks, so extend that file rather than starting a parallel one. Extending it means moving
   all thirteen inner launch thunks onto the new contract. That also stops the fakes from drifting
   from the shipped drivers again.

   Cover eight cases:

   - `invalidate()` runs the teardown exactly once for the discarded lease.
   - A successful final release runs the teardown exactly once too.
   - The next `driver` access launches a fresh lease.
   - A mid-run teardown that raises `CalledProcessError` or `OSError` is reported as a warning.
   - A mid-run teardown that raises anything else is *also* swallowed into that warning, and does
     not propagate.
   - The final release propagates a wiring defect, while it still warns on `CalledProcessError` /
     `OSError`.
   - A launch that raises after `env.start` still tears the environment down before the error
     propagates.
   - A lease that was never launched tears nothing down.

   These cases belong in the deterministic suite that the fast gate runs. The fix then holds on
   Linux with no device.

The Android backend has the same shape, because `AdbDriver` has no `close()` either. No suite leases
a device through `LeaseHolder` on Android today. If one is ever added, the seam of unit 1 makes it
correct by construction. Nothing in this item is Simulator-specific: the teardown that a suite
supplies is its platform's own.

## Alternatives considered

- **Implement `close()` on the XCUITest driver.** This is the smallest diff, and it needs no change
  to `LeaseHolder`. But it puts process teardown on the wrong seam. The driver reaches the runner
  over a loopback port and holds no handle to the `xcodebuild` process. The environment owns that
  process, as well as the Simulator lifecycle around it. A `close()` on the driver would have to
  reach back into an environment that the driver does not hold. That inverts the seam:
  [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) put the
  per-platform bring-up behind the environment precisely so that the driver would not carry it.
  Rejected in favor of letting the lease hold the environment, which already owns the teardown.
- **Require every driver to implement `close()`.** This would make the gap a type error rather than
  a silent `AttributeError`, which is attractive. It also contradicts what `BackendLifecycle` is
  for. Its own docstring calls it "a *typing umbrella* for the call sites, not a conformance
  target". Its point is to make each hook a mypy-checked fact at the call site "without forcing a
  lifecycle-free backend to stub no-op methods". Four of the five shipped drivers have nothing of
  their own to close. Rejected for the same reason as the previous alternative.
- **Have the fault-injection suite kill the leftover runner itself.** The suite already finds runner
  processes by command line for its own fault injection, so it could reuse that to clean up. That
  would fix the one job that surfaced the problem. It would leave the conformance suite, which
  discards a lease on every recovered crash, with the same defect. It would also put process
  management in a test file rather than behind the seam that owns it. Rejected as a workaround for
  one symptom.
- **Leave it, since both suites usually pass.** The suites do pass most runs, because the defect
  bites when a lease is discarded, which takes a crash or an injected fault first. When it does
  bite, it produces a red check on a pull request that cannot have caused it. That is the exact
  outcome that BE-0334 exists to prevent, arriving through the recovery path of BE-0334 itself. On
  the fault-injection lane, that check blocked no merge at the time (BE-0305 has since promoted the
  lane onto the gate). On the conformance suite, which discards a lease the same way, it is the
  required `E2E (iOS)`. Rejected.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — let the lease tear down the environment that owns the runner, one per lease.
- [x] Unit 2 — extract the guarded teardown into `recovery.py`; warn on a failed mid-run teardown and
      let the final release propagate a wiring defect.
- [x] Unit 3 — off-device cases over the lease's launch/teardown seam.

Log:

- 2026-08-05 — Units 1–3 ([#1491](https://github.com/bajutsu-e2e/bajutsu/pull/1491)). Both on-device
  suites now share one thunk factory: `xcuitest_lease_launch`, in the new `tests/xcuitest_lease.py`.
  It builds a fresh environment per lease and returns that environment's teardown alongside the
  driver, so `LeaseHolder` discards through the platform's own teardown rather than `driver.close()`;
  it stays out of the backend-agnostic `backend_crash_recovery` plugin, which never learns a backend
  name. Unit 1's open decision is
  resolved in favor of `launch_driver` absorbing the guard, so every caller inherits it. The
  guarded-teardown policy moved to `bajutsu/runner/recovery.py`, shared by the pool's own sites (the
  collector start-up rollback, the actuator switch, the failed lease with its bridge and collector
  stops, `release()`'s own teardown with its bridge and collector stops, and `shutdown()`'s device
  and collector loops), `launch_driver`, and the lease discard; a mid-run wiring defect now warns
  rather than logging at debug. `shutdown()` finishes its whole sweep before propagating the first
  defect it met, so one device's defect no longer leaves the later devices' runners and the
  collector sockets up. `release()` gets the same guard for the normal end of a lease, and evicts
  (falling back to a full teardown) a warm resident whose `end_lease` did not finish, so a
  half-ended app is never resumed by the next lease. A *wiring* defect on that end-of-lease teardown
  still cannot raise from `release()`: doing so would replace the scenario's own result and skip
  returning the device to the pool. `release()` stashes the defect instead, and `shutdown()` raises
  it once every lease returns its device — closing the one path (a backend with no warm resident)
  where such a defect would otherwise warn forever and never fail the run. Status → Implemented.

## References

- [BE-0334 — Give the on-device conformance suite the infrastructure-fault recovery the run pipeline already has](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md) — the item that built the lease and its discard.
- [BE-0114 — Driver conformance suite for backend-agnostic behavior](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the suite whose lease this fixes.
- [BE-0305 — Real-device fault-injection coverage for driver resilience paths](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection.md) — the item that built the fault-injection suite whose logs this item reads.
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) — the environment seam that owns the runner process.
- `tests/backend_crash_recovery.py` — `LeaseHolder`, its `invalidate()`, and the plugin that re-leases between attempts.
- `tests/runner/test_backend_crash_recovery.py` — the off-device cases unit 3 extends.
- `bajutsu/drivers/base.py` — the `BackendLifecycle` Protocol whose `close()` only the web driver implements, and the `Driver` Protocol that declares none.
- `tests/test_driver_conformance_ondevice.py`, `tests/test_fault_injection_ondevice.py` — the two suites that lease a device through `LeaseHolder`.

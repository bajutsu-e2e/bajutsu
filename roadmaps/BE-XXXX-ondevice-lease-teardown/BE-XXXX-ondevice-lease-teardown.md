**English** · [日本語](BE-XXXX-ondevice-lease-teardown-ja.md)

# BE-XXXX — Give the on-device suites' lease a teardown that reaches the runner

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ondevice-lease-teardown.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md), [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) |
<!-- /BE-METADATA -->

## Introduction

Two of Bajutsu's continuous integration (CI) jobs drive a real iOS Simulator from a `pytest` harness
rather than through `bajutsu run`: the driver conformance suite and the fault-injection suite. Both
share one device across a module, and both discard that shared lease when a test finds the resident
runner dead, so the next test starts from a freshly launched one.
[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md)
built that discard, as `LeaseHolder.invalidate()`.

The discard never reaches the runner on the iOS backend. It calls `close()` on the
[driver](../../docs/glossary.md#driver-backend-actuator-platform), and the XCUITest driver has no
`close()` — so the `xcodebuild` process that hosts the runner keeps running,
and XCTest goes on restarting the runner inside it. The next test launches a second runner on the
same Simulator, where only one automation session can exist, and the two take it from each other.
This item gives the lease a teardown that reaches the runner process, so a discarded lease is
actually gone before the next one starts.

## Motivation

Each suite shares one device across its whole module, so a discarded lease is the module's problem:
the runner a discard fails to stop is still holding the device the next test in that module leases.
On 2026-08-05 the fault-injection job left both runners in its uploaded logs, and the timestamps put
them on the device at the same moment:

| | first runner (port 50870) | second runner (port 52816) |
|---|---|---|
| launched | 04:14:15 | **04:16:24** |
| last served a request | `Find the Application`, to 04:16:16 | `Find the Application`, to ≈04:16:32 |
| then | **04:16:33 restarted its host process** | 04:16:57 restarted, ran 0 tests |

Both restarts report the same line, which XCTest writes when the process hosting the tests dies and
it starts another:

```
Restarting after unexpected exit, crash, or test timeout; summary will include totals from previous launches.
```

The second runner is the one the failing test had just launched. It answered a few requests and then
died, so the test that launched it saw its channel refuse a connection and reported the runner as
crashed:

```
FAILED tests/test_fault_injection_ondevice.py::test_a_killed_runner_fails_loudly_with_a_crash_diagnosis
 - XcuitestRunnerCrashError: runner channel GET /elements failed:
   the runner crashed mid-run and did not recover within 60s
```

That failure arrives at the test's *first* statement — the property access that launches the lease —
so no fault the test itself injects can explain it. What the test would have injected comes several
lines later.

The first runner is what explains it, because nothing had stopped it. `LeaseHolder.invalidate()`
calls `dead.close()` inside a `try` whose `except Exception` logs at debug level and moves on. That
call is not merely unimplemented — it is outside the interface the lease holds. `Driver`, the
Protocol a lease's value satisfies, declares no `close()` at all; `close()` belongs to the separate
`BackendLifecycle` Protocol, which the `platform_lifecycle` environments reach through
`cast(base.BackendLifecycle, driver)` under a platform invariant that has already established which
backend they hold. Of the drivers the tree ships — fake, adb, Playwright, XCUITest, and the live
XCUITest route — only the Playwright one implements `close()`: for a browser the driver does own the
context it would close, while on iOS and Android the runner process belongs to the environment.
So on the iOS backend `dead.close()` raises `AttributeError`, the `except Exception` swallows it, and
`invalidate()` returns having discarded nothing. Three things kept that silent. The log line sits at
debug level; mypy never sees the call, because it runs over `bajutsu demos scripts` and not over
`tests/`; and every fake driver in the harness's own off-device suite
(`tests/runner/test_backend_crash_recovery.py`) defines a `close()`, so those cases exercise a shape
no shipped driver has.

Both places that describe the discard therefore describe something that does not happen.
`invalidate()`'s own docstring says "Discard the current (dead) lease so the next `driver` access
cold-respawns", and the fault-injection suite's comment on the fixture the discard re-leases from
says "the killed-runner case discards it so the next case respawns onto a fresh device instead of
inheriting the dead runner".

The consequence is the failure mode BE-0334 set out to remove. That item's argument was that a
Simulator fault during the conformance suite reddens `E2E (iOS)`, a required check, on a pull request
that could not have caused it — so the harness should classify infrastructure faults and recover from
them the way the run pipeline does. A discard that leaves the old runner alive turns its own recovery
into the next fault: the re-lease it performs is what puts a second runner on the device.

Which check that reaches depends on the suite. The fault-injection lane the logs above come from is
deliberately kept out of the `E2E (iOS)` aggregate's `needs:`, alongside `actuation`, `golden`, and
`visual`, so a red one there blocks no merge — which is why the defect surfaced on a non-gating
signal. The conformance suite discards a lease the same way and *is* in that aggregate, so the same
defect there lands on the required check.

Nothing about this depends on any one branch. The same test fails with the same message in
[run 30971636417](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30971636417), on an unrelated
branch whose changes touch none of this machinery, which is what rules out reading the failure as a
regression from whatever is in flight. The logs quoted above are from
[run 30971507268](https://github.com/bajutsu-e2e/bajutsu/actions/runs/30971507268).

## Detailed design

The work breaks into three units. Unit 1 is the fix; unit 2 stops the class of silence that hid it;
unit 3 is the off-device coverage that keeps both from regressing.

1. **Let the lease tear down the environment that owns the runner.** The launch thunk a suite
   supplies returns its teardown alongside its driver, so the `LeaseHolder` discards through the
   platform's own teardown instead of through `driver.close()`. Each suite's `_backend_launch` already
   calls `launch_driver`, which accepts a prepared `environment`, so the fixture builds one, passes it,
   and returns a teardown closing over that environment and driver. That widens the plugin's opt-in
   contract, so change it in the same breath: `LeaseHolder`'s `launch` type and the module docstring
   that specifies "a zero-arg callable returning a fresh `base.Driver`" both name the driver alone
   today. The two descriptions this item's Motivation quotes need no edit — unit 1 is what makes them
   true. Build a **fresh environment per
   lease** rather than retaining one across re-leases: a retained XCUITest environment would make
   every later cold spawn an in-place respawn, which takes the lane's tighter respawn ceiling (90s)
   instead of the cold one (300s) on exactly the contended host this item is about, and it would hand
   `teardown` a driver from an earlier lease. For the iOS backend the teardown that then runs
   terminates the `xcodebuild` host process, which ends the XCTest session the runner lives in, and
   terminates the app under test through `simctl` — machinery that already exists, and that until now
   had no path from these suites. The web environment's own `teardown` is already that `close()` on the
   browser context, so the lease needs no per-backend branch.

2. **Extract the guarded teardown, and stop swallowing one that could not run.** A *mid-run* discard
   runs on a failure path, where raising would mask the fault that prompted it, so catching there is
   right; logging at debug level is not, because a teardown that is *structurally* impossible then looks
   identical to one that merely failed this time. A missing method should reach a maintainer on the
   first run, not sit behind a log level a reader has to raise.

   What may be swallowed is a policy the run pipeline already has: its three teardown sites each warn on
   a `CalledProcessError` or an `OSError` — a runner that had already exited, an unreachable `xcrun` —
   and let anything else surface. Restating that policy in the harness would put a fourth copy of the
   same pair of exception classes in the tree, held in sync by nothing but a comment, so extract the
   guarded teardown into one helper instead and have the pool's three sites and the lease both call it.
   `bajutsu/runner/recovery.py` is where it belongs: the harness already borrows the pipeline's retry
   count and recovery budget from that module by import rather than by restatement, which is what makes
   "the two recovery paths do not drift" a property of the code instead of a promise in prose.

   Whether a wiring defect surfaces depends on which path called the helper. A mid-run discard swallows
   it into the warning, because one of that path's two call sites sits in a `finally` guarding the very
   `BackendCrashError` a test asserts, and the other runs outside any test, where an escaping exception
   would abort the session instead of failing one case. On the module's **final** release let a wiring
   defect fail the module teardown, since no fault is in flight there and a runner that survives leaks
   into the rest of the job.

3. **Pin the teardown off device.** The harness is already exercisable without a Simulator:
   `tests/runner/test_backend_crash_recovery.py` drives the plugin through `pytester` with fake launch
   thunks, so extend that file rather than starting a parallel one. Extending it means moving all
   thirteen inner launch thunks onto the new contract, which is also what stops the fakes drifting from
   the shipped drivers again. Cover seven cases: that `invalidate()` runs the teardown exactly once for
   the discarded lease; that a successful final release runs it exactly once too; that the next `driver`
   access launches a fresh one; that a mid-run teardown raising `CalledProcessError` or `OSError` is
   reported as a warning; that a mid-run teardown raising anything else is *also* swallowed into that
   warning rather than propagating; that the final release propagates a wiring defect while still
   warning on `CalledProcessError` / `OSError`; and that a lease which was never launched tears nothing
   down. These belong in the deterministic suite the fast gate runs, so the fix holds on
   Linux with no device.

The Android backend has the same shape — `AdbDriver` has no `close()` either — but no suite leases a
device through `LeaseHolder` on it today, so unit 1's seam is what makes an Android on-device suite
correct by construction if one is ever added. Nothing in this item is Simulator-specific: the
teardown a suite supplies is its platform's own.

## Alternatives considered

- **Implement `close()` on the XCUITest driver.** The smallest diff, and it needs no change to
  `LeaseHolder` — but it puts process teardown on the wrong seam. The driver reaches the runner over a
  loopback port and holds no handle to the `xcodebuild` process; the environment owns that process, as
  well as the Simulator lifecycle around it. A `close()` on the driver would therefore have to reach
  back into an environment the driver does not hold, inverting the seam
  ([BE-0009](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) put the
  per-platform bring-up behind the environment precisely so the driver would not carry it). Rejected
  in favor of letting the lease hold the environment, which is what already owns the teardown.
- **Require every driver to implement `close()`.** This would make the gap a type error rather than a
  silent `AttributeError`, which is attractive. It also contradicts what `BackendLifecycle` is for:
  its own docstring calls it "a *typing umbrella* for the call sites, not a conformance target", whose
  point is to make each hook a mypy-checked fact at the call site "without forcing a lifecycle-free
  backend to stub no-op methods". Four of the five shipped drivers have nothing of their own to close.
  Rejected for the same reason as the previous alternative.
- **Have the fault-injection suite kill the leftover runner itself.** The suite already finds runner
  processes by command line for its own fault injection, so it could reuse that to clean up. It would
  fix the one job that surfaced the problem and leave the conformance suite — which discards a lease
  on every recovered crash — with the same defect, and it would put process management in a test file
  rather than behind the seam that owns it. Rejected as a workaround for one symptom.
- **Leave it, since both suites usually pass.** The suites do pass most runs, because the defect only
  bites when a lease is discarded, which takes a crash or an injected fault first. What it produces
  when it does bite is a red check on a pull request that cannot have caused it — the exact outcome
  BE-0334 exists to prevent, arriving through BE-0334's own recovery path. On the fault-injection lane
  that check blocks no merge; on the conformance suite, which discards a lease the same way, it is the
  required `E2E (iOS)`. Rejected.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — let the lease tear down the environment that owns the runner, one per lease.
- [ ] Unit 2 — extract the guarded teardown into `recovery.py`; warn on a failed mid-run teardown and
      let the final release propagate a wiring defect.
- [ ] Unit 3 — off-device cases over the lease's launch/teardown seam.

## References

- [BE-0334 — Give the on-device conformance suite the infrastructure-fault recovery the run pipeline already has](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md) — the item that built the lease and its discard.
- [BE-0114 — Driver conformance suite for backend-agnostic behavior](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the suite whose lease this fixes.
- [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md) — the environment seam that owns the runner process.
- `tests/backend_crash_recovery.py` — `LeaseHolder`, its `invalidate()`, and the plugin that re-leases between attempts.
- `tests/runner/test_backend_crash_recovery.py` — the off-device cases unit 3 extends.
- `bajutsu/drivers/base.py` — the `BackendLifecycle` Protocol whose `close()` only the web driver implements, and the `Driver` Protocol that declares none.
- `tests/test_driver_conformance_ondevice.py`, `tests/test_fault_injection_ondevice.py` — the two suites that lease a device through `LeaseHolder`.

**English** · [日本語](BE-0334-conformance-suite-infra-fault-recovery-ja.md)

# BE-0334 — Give the on-device conformance suite the infrastructure-fault recovery the run pipeline already has

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0334](BE-0334-conformance-suite-infra-fault-recovery.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0334") |
| Topic | Platform support |
| Related | [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md) |
<!-- /BE-METADATA -->

## Introduction

`bajutsu run` recovers from a Simulator infrastructure fault. A mid-scenario
`base.BackendCrashError` is treated as infrastructure rather than as a verdict: the dead lease is
discarded, a fresh device is leased in a cold respawn, and the scenario is re-run, bounded by
`crash_retries` (BE-0323). The cold spawn itself is diagnosable and self-healing (BE-0319).

The on-device driver conformance suite inherits none of that recovery, and it is the one iOS job
besides `run` that **gates a merge**. It obtains its device by calling `launch_driver` directly from a
module-scoped `pytest` fixture, so the run pipeline holding the recovery is never on the path — the iOS
workflow says as much in a comment: "a pytest-ondevice harness, not a `bajutsu run`". A Simulator fault
during the suite therefore reddens the required `E2E (iOS)` check on a pull request that cannot have
caused it. This item proposes classifying an infrastructure fault in the harness and recovering from it
the way the pipeline does, while a genuine contract violation keeps failing as loudly as it does today.

## Motivation

The failure is observed, not hypothetical. On PR
[#1405](https://github.com/bajutsu-e2e/bajutsu/pull/1405), whose diff touched only CI path-filter
logic, a Makefile lint list, one docstring, and documentation, `conformance (xcuitest)` failed:

```
bajutsu.drivers.xcuitest.XcuitestRunnerCrashError: runner channel POST /tap failed: timed out
FAILED tests/test_driver_conformance_ondevice.py::TestXcuitestDriverConformance::test_unique_match_acts_without_error
1 failed, 16 passed in 221.27s
```

Re-running the job with no code change passed. Within the same hour, `golden (xcuitest)` failed with
`xcuitest runner did not come up: health never ready`, and an unrelated branch's
`actuation (xcuitest)` failed with the same readiness signature — the Simulator lane's known flakiness
(BE-0218), landing on whichever job draws a contended host.

Two properties make the conformance job the worst place for that fault to land. It **gates**: unlike
`golden` and `visual`, which are deliberately excluded from the aggregator's `needs:`, `conformance` is
required, so the flake blocks a merge rather than surfacing as a signal. And its lease is
**module-scoped**: one crash leaves every later test in the module driving a dead runner, so a single
infrastructure fault can cascade across the whole suite rather than costing one test.

The asymmetry is the point. Both jobs drive the same XCUITest backend against the same Simulator, and
`run` survives exactly the fault that fails `conformance`. Nothing about the driver contract makes it
less deserving of recovery; it simply never went through the code path where recovery lives.

## Detailed design

Four units, each landable on its own.

### Unit 1 — Classify the fault in the harness

Separate an infrastructure fault from a contract violation at the harness boundary. A runner crash
(`XcuitestRunnerCrashError`), a readiness timeout, and a lease bring-up failure are infrastructure. A
selector that resolved wrongly, an actuator that did nothing, and a tree that came back malformed are
contract violations, and they must keep failing immediately.

The distinction rests on the exception type the driver already raises, so the decision stays a
deterministic branch on a Python class. No large language model is involved, and the suite remains the
judge of the driver contract, which prime directive 1 requires.

### Unit 2 — Re-lease and retry on an infrastructure fault

On an infrastructure fault, discard the lease, cold-respawn a device, and re-run the affected test,
bounded by a retry budget mirroring the pipeline's `crash_retries`. Reuse the pipeline's existing
recovery rather than writing a second implementation, so the two cannot drift.

The budget must be small and explicit. A suite that retries indefinitely converts a chronic
infrastructure problem into a slow green, which is worse than a red: it removes the signal that the
lane needs attention.

### Unit 3 — Contain the module-scoped lease

Decide whether the lease stays module-scoped. A module-scoped fixture amortizes one expensive cold
spawn across seventeen tests, which is why it exists, but it also lets one crash poison every later
test. Re-leasing lazily on the next test after a fault preserves the amortization in the common case
and stops the cascade in the bad one.

### Unit 4 — Report every retry

Emit each recovery into the job log and the uploaded artifacts, counting them. A retry that leaves no
trace turns a degrading lane into a lane that merely looks slower, and the count is what tells a
maintainer that the underlying fault is getting worse rather than staying rare.

## Alternatives considered

- **Demote `conformance (xcuitest)` from the required gate.** Rejected. The driver contract is
  deterministic and host-independent, which is exactly the property the repository requires of a
  gating check; the flake comes from the Simulator underneath, not from the check. Demoting it would
  give up a real gate to dodge an infrastructure problem.
- **Keep re-running the job by hand.** The status quo. It costs human attention on every occurrence and
  teaches contributors to re-run a red required check without reading it, which is how a genuine
  regression eventually gets waved through.
- **Run the conformance suite through `bajutsu run`.** Rejected. The suite is a `pytest` contract
  harness by design, shared in structure with the Android and web lanes' conformance jobs, and its
  assertions are driver-level rather than scenario-level. Reshaping it into scenarios to inherit the
  recovery would distort the contract to reach the plumbing.
- **Raise the readiness ceiling instead.** Insufficient on its own. BE-0319 already made the cold spawn
  diagnosable and self-healing and the `health never ready` failure still occurs, so a larger budget
  moves the threshold without addressing a crash that happens mid-suite.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — classify infrastructure faults against contract violations at the harness boundary.
- [ ] Unit 2 — re-lease and retry on an infrastructure fault, reusing the pipeline's recovery.
- [ ] Unit 3 — contain the module-scoped lease so one crash cannot cascade.
- [ ] Unit 4 — report and count every recovery in the log and the artifacts.

## References

- [`tests/test_driver_conformance_ondevice.py`](../../tests/test_driver_conformance_ondevice.py) — the
  harness this item changes; it calls `launch_driver` from a module-scoped fixture.
- [`bajutsu/runner/pipeline.py`](../../bajutsu/runner/pipeline.py) — where `crash_retries` and the
  existing recovery live.
- [`docs/ci.md`](../../docs/ci.md) — which iOS jobs gate a merge and which stay signals.
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the driver
  conformance contract the suite enforces.
- [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md) —
  the readiness and crash respawn the run pipeline gained, and the recovery Unit 2 reuses.
- [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md) — the
  diagnosable, self-healing cold spawn, whose `health never ready` failure still reaches CI.
- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
  — the Simulator lane's flakiness history, and the reason `golden` and `visual` are not required.

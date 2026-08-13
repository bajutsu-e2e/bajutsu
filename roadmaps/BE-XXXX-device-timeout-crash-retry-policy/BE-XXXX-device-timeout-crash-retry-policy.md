**English** · [日本語](BE-XXXX-device-timeout-crash-retry-policy-ja.md)

# BE-XXXX — Fail the scenario when a crash retry's device prep times out, instead of retrying onto the wedged device

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-device-timeout-crash-retry-policy.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0260](../BE-0260-cli-bringup-consolidation/BE-0260-cli-bringup-consolidation.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's run pipeline treats a mid-scenario backend crash as infrastructure rather than a verdict.
It discards the dead lease — the pipeline's hold on one prepared device and the runner process on it
— leases a fresh device, and re-runs the whole scenario, bounded by a retry count and a wall-clock
budget. Since
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md),
a retry also forces the device recovery a scenario otherwise gets only by declaring
`preconditions.erase: true`: on the iOS Simulator backend, `simctl shutdown`, `erase`, and `boot`,
then a reinstall of the app under test. That preparation can itself fail, and the pipeline answers a
failure by *degrading* — it catches the device fault, leases the same device again without the forced
erase, and lets the scenario run. Degrading protects the run, because the fault would otherwise
escape the retry loop and abort every scenario, discarding the verdicts already earned.

[BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) gives every
one-shot `simctl` call a deadline, so a wedged CoreSimulator — the macOS service that owns Simulator
devices — raises a named `simctl.DeviceTimeout` rather than hanging until the continuous integration
(CI) job's own `timeout-minutes` cancels it. The degradation branch does not distinguish that new
type from any other device fault. A device that never answered `shutdown` is therefore retried on
exactly the terms written for a device that answered by refusing.

This item draws the distinction the degradation branch is missing. A device-preparation timeout on a
crash retry ends that one scenario with a named failure instead of degrading, and latches the run so
no later scenario spends its own attempt against the same wedged host. Ending the scenario rather
than the run is the point: every verdict already earned survives, which is the property the
degradation was written to protect in the first place. Drawing the distinction at all needs a
platform-neutral name for the fault, since the pipeline is backend-agnostic and cannot import an iOS
backend module to name the exception it catches.

## Motivation

The reason for degrading is a claim about one specific fault, and
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
states it plainly: "the fault this attempt hits stays this attempt's problem, not the whole run's."
A device that rejects `erase` has answered. Its refusal is evidence about one operation on one
device, and the scenario deserves the bare respawn it would have received before the forced erase
existed. A device that never answers has produced no evidence about the operation at all — only
evidence that the service behind every operation has stopped serving. Retrying on that evidence
assumes precisely what the timeout disproved.

Two costs follow from treating the two faults alike, and the second is the serious one. The first is
wasted wall-clock: the degraded lease re-enters the same `simctl` surface on the same host, so it
spends its own deadlines before failing the same way — a second full deadline bought to learn what
the first already established.

The second cost is a verdict of unknown worth. A degraded lease carries `erase: false`, and that is
exactly the condition under which the XCUITest environment reuses a warm resident runner instead of
preparing the device cold. Nothing on that reuse path touches `simctl`: the environment checks that
the runner process is alive and that it answers a bounded `/health` query over the runner's own
channel, both of which a runner inside a Simulator can keep doing while the host service that owns
that Simulator has stopped answering. The scenario then runs to completion and reports `ok` or a
failure. A run whose whole purpose is a deterministic verdict would be publishing one produced on a
host that had just failed to answer `shutdown`, and neither the verdict nor the report would say so.
Prime directive 2 — determinism first — rules that out: an ambiguous result fails rather than
resolves itself in whichever direction the machine happened to go.

Failing that one scenario is not enough on its own, because a wedged CoreSimulator is a property of
the host, not of the scenario that happened to meet it. Every later scenario in the run would reach
its own first crash, force its own erase, and spend its own deadline against the same service. The
run pipeline already has the mechanism for exactly this shape of problem: once an earlier scenario
has failed *because* the run-level crash-recovery budget was the binding constraint, a latch makes
every later scenario fail before it is ever leased, since that is real evidence the device is not
recovering. A `simctl` call that never returned is the same kind of evidence, arrived at sooner and
more directly.

Naming the fault where the pipeline can see it is a prerequisite rather than a detail.
`bajutsu/runner/pipeline.py` is backend-agnostic by prime directive 3 — a platform is a backend, and
the deterministic core stays unchanged across targets — so it catches
`bajutsu/device_errors.DeviceError`, the shared base
[BE-0260](../BE-0260-cli-bringup-consolidation/BE-0260-cli-bringup-consolidation.md) added
for this purpose, and never imports `bajutsu/simctl.py`. The timeout type
[BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) introduces lives
in the iOS backend module, so the pipeline has no way to ask "is this fault a timeout?" without
breaking that separation. The Android backend has the same shape waiting for it: `bajutsu/adb.py`
raises its own `adb.DeviceError` beside the iOS one, so whatever the pipeline learns to do with a
timeout should hold for an Android emulator the day adb calls carry deadlines of their own.

## Detailed design

Three units. Unit 2 depends on unit 1, and unit 3 depends on unit 2.

1. **Give the device-timeout fault a platform-neutral name.** Add `DeviceTimeout` to
   `bajutsu/device_errors.py` as a subclass of the `DeviceError` already there, documenting it as
   what every backend raises when a device operation exceeded its deadline rather than failing.
   Then give `simctl.DeviceTimeout` the neutral type as a second base, keeping `simctl.DeviceError`
   as the first: the iOS timeout stays everything it is today — a `simctl.DeviceError`, and through
   it a `device_errors.DeviceError` — and gains the platform-neutral name on top. Adding a base
   rather than swapping one is what keeps every existing handler working unchanged, including the
   three groups
   [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) reasoned
   about individually: the deliberate suppressions that key on `subprocess.CalledProcessError` alone
   and therefore still let a timeout through, the best-effort probes that fold a timeout into their
   documented fallback, and the guarded teardown that absorbs a timeout with a warning so a run whose
   scenarios all passed still reports its verdicts.

   Leave `bajutsu/adb.py` alone in this item. Android has no timeout type to place: its `_real_run`
   passes no `timeout` to `subprocess.run`, so no adb call can raise a timeout at all today. Bounding
   the adb surface is the Android counterpart of
   [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) and wants its
   own item, with its own measured bounds and its own audit of which adb call sites suppress a
   failure — see *Alternatives considered*. Defining the neutral type here with one producer is what
   lets that later item add `adb.DeviceTimeout` beneath it and inherit this item's pipeline policy
   with no pipeline change at all.

2. **Stop degrading a timed-out preparation, and fail the scenario instead.** In
   `bajutsu/runner/pipeline.py`, `_ScenarioRunner.run_one` catches `device_errors.DeviceError` around
   the forced-erase lease and degrades to a bare respawn. Split that handler: a
   `device_errors.DeviceTimeout` skips the degraded lease, and every other device fault degrades
   exactly as it does today, since nothing about the refusal case has changed.

   Fail the scenario by leaving the retry loop and returning a `RunResult` with `ok=False` and a
   failure that names the timeout — the shape `run_one` already builds when crash recovery ends
   without the scenario passing. Do not let the timeout propagate out of `run_one`: nothing between
   `run_one` and `run_all` catches `device_errors.DeviceError`, so propagating would abort the whole
   run and discard every already-passed scenario's verdict, which is the exact cost
   [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
   refused to pay and the reason the degradation exists (see *Alternatives considered*, where the
   propagating variant is treated on its own terms).

   Keep the accounting the loop already does. The scenario's own recovery time is billed in the
   `finally` around the retry loop, and the progress callback reports the failure to the operator; a
   return that leaves the loop early must go through both, not around them. The failure text should
   name the command and the deadline it exceeded, which the exception raised in
   `bajutsu/simctl.py`'s runner helper already carries, so an operator reading the report learns the
   host was wedged rather than that a scenario failed for unexplained reasons.

3. **Latch the run once a preparation has timed out.** `bajutsu/runner/recovery.py`'s
   `RunCrashRecoveryBudget` already carries the latch and `bajutsu/runner/pipeline.py` already reads
   it before leasing: `mark_given_up()` records that recovery has been abandoned for this run, and
   `given_up()` makes every later scenario fail before its first lease rather than pay a full
   cold-spawn attempt against a device that has demonstrated it is not recovering. Set that latch
   from unit 2's timeout branch, so the wedge is established once instead of rediscovered by every
   remaining scenario.

   The latch is set unconditionally on a timeout, unlike the budget path that sets it only when the
   run-level budget was the binding constraint. The budget is a measurement that a slow-but-successful
   recovery can also trip, which is why that path needs the extra qualification; a `simctl` call that
   never returned is not a measurement of slowness but a direct observation that the service stopped
   answering, so it needs none.

   The message a latched scenario reports names the budget today, and a run latched
   by a timeout never had a budget as its cause — `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` may well be
   unset, in which case the budget is unbounded and reporting it would be false. Give
   `mark_given_up()` a cause the latch stores and `given_up()`'s consumers report, so the pre-lease
   failure says the run was abandoned because a device operation timed out or because the run-level
   budget was spent, whichever actually happened. Keeping one latch rather than adding a second is
   deliberate — see *Alternatives considered*.

Verification needs no Simulator and belongs in the deterministic gate. The pipeline's existing tests
already drive `run_one` with a substituted lease callable, so a lease that raises
`simctl.DeviceTimeout` on the forced-erase attempt asserts three things at once: the scenario fails,
the degraded second lease was never attempted, and the run-level latch is set. A lease that raises an
ordinary `simctl.DeviceError` asserts the degradation still happens, and a second scenario run after
a latched timeout asserts it fails before its first lease with the timeout named as the cause.

## Alternatives considered

- **Let the timeout propagate out of `run_one` and abort the run.** The literal reading of the review
  finding that prompted this item: if the degradation is wrong, remove the degradation. Rejected on
  what propagating actually does rather than on what it sounds like. No handler between `run_one` and
  `run_all` catches `device_errors.DeviceError`, so the exception leaves `run_all` and the run reports
  nothing at all — not a failure for the affected scenario, but the loss of every verdict every
  earlier scenario had already earned. That is the outcome
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  weighed and refused, and nothing about a timeout makes the earlier scenarios' verdicts less true.
  Failing the one scenario and latching the run delivers the same loud, fast, diagnosable failure
  while keeping them.
- **Keep degrading and rely on the run-level crash-recovery budget to stop the bleeding.** The budget
  from
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  does bound how long a run may spend recovering, so a wedged host is eventually caught. Rejected on
  two counts. The budget is unset by default, so on any lane that has not opted in it bounds nothing;
  and even where it is set, it stops the run only after the wasted deadlines have been spent, having
  meanwhile let the warm-resident path publish verdicts from the wedged host. A budget bounds how much
  of a bad outcome a run may suffer, which is not the same as declining to enter it on evidence
  already in hand.
- **Apply the same policy to a timeout on a scenario's first, non-forced-erase lease.** Tempting for
  symmetry, since a wedged host is a wedged host whichever lease met it, and the first lease's own
  `device_errors.DeviceError` propagates out of `run_all` today. Left out deliberately: that is the
  pre-existing behavior of the ordinary lease path, which
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  explicitly did not touch, and changing it would convert an established fail-loudly route into a
  per-scenario failure for every device fault, not only for a timeout. This item's scope is the branch
  the forced-erase retry added, where the degradation is new and the reasoning behind it demonstrably
  does not cover a timeout. Whether the ordinary lease path wants the same treatment is a separate
  question about a different guarantee.
- **Bound the adb calls in this item, so the neutral type ships with two producers.** Rejected as a
  second item's work wearing this item's name. `bajutsu/adb.py` passes no `timeout` anywhere, so
  giving it one means choosing bounds for the adb command surface, auditing every call site that
  suppresses a failure, and deciding what each best-effort probe does with a timeout — the whole of
  what [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) did for
  `simctl`, on a backend whose commands have not been measured. A neutral type with one producer costs
  nothing and is the seam that later item plugs into.
- **Restart CoreSimulator when a preparation times out, rather than abandoning the run.** The remedy a
  wedged service invites, and the recovery ladder from
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) is where such a
  rung would live. Rejected here for the reason
  [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) already gave
  when it declined the same remedy: no failure reviewed so far shows the service-level signature a
  restart would target, and a bounded call is the prerequisite for ever deciding that from data. This
  item makes the wedge a fact the pipeline acts on; a rung that repairs it can be added later without
  revisiting anything here.
- **Track the timeout latch on a new object instead of extending `RunCrashRecoveryBudget`.** The tidy
  reading, since a wedged host is not a budget concern and `RunCrashRecoveryBudget` is named for
  accounting. Rejected because the latch it holds is already not accounting: `given_up()` answers
  "has recovery been abandoned for this run", and budget exhaustion is merely today's only cause. A
  second object would need its own read in the same pre-lease check, its own threading discipline
  under `run_all`'s parallel path, and its own place in the failure message — three copies of
  machinery that exists, to express one more value of a field. Extending the cause the existing latch
  records keeps one answer to one question.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — add `device_errors.DeviceTimeout`, and give `simctl.DeviceTimeout` that neutral type
      as a second base beside `simctl.DeviceError`, leaving every existing handler and
      `bajutsu/adb.py` unchanged.
- [ ] Unit 2 — split `run_one`'s forced-erase-prep handler so a `device_errors.DeviceTimeout` fails
      the scenario with a named failure instead of degrading to a bare respawn, while every other
      device fault still degrades.
- [ ] Unit 3 — latch the run from that branch through `mark_given_up()`, and give the latch a cause
      so a latched scenario reports the timeout rather than a budget it may never have had.

## References

- [BE-0363 — Bound every simctl call with a timeout so a wedged CoreSimulator fails with a named cause](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md)
  — introduces `simctl.DeviceTimeout`, the fault this item teaches the pipeline to act on.
- [BE-0353 — Force device recovery on a backend-crash retry and cap total crash-recovery time per run](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  — adds the forced-erase retry, its degradation branch, and the run-level budget whose latch this
  item reuses.
- [BE-0344 — Repair the Simulator between XCUITest cold-spawn retry attempts](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)
  — the recovery ladder a service-level restart would belong to.
- [BE-0260 — Consolidate the duplicated CLI command bring-up and add a neutral DeviceError](../BE-0260-cli-bringup-consolidation/BE-0260-cli-bringup-consolidation.md)
  — the shared `DeviceError` this item's `DeviceTimeout` sits beneath.
- `bajutsu/device_errors.py`, `bajutsu/simctl.py`, `bajutsu/adb.py` — the fault types this item
  extends and the Android module it deliberately leaves alone.
- `bajutsu/runner/pipeline.py`, `bajutsu/runner/recovery.py` — the forced-erase-prep handler and the
  run-level latch this item changes.
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — the warm-resident reuse path a degraded
  lease can take on a wedged host.

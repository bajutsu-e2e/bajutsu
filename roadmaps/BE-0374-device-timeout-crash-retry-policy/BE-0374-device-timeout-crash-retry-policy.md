**English** · [日本語](BE-0374-device-timeout-crash-retry-policy-ja.md)

# BE-0374 — Fail the scenario when a crash retry's device prep times out, instead of retrying onto the wedged device

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0374](BE-0374-device-timeout-crash-retry-policy.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0374") |
| Implementing PR | [#1910](https://github.com/bajutsu-e2e/bajutsu/pull/1910) |
| Topic | Platform support |
| Related | [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0260](../BE-0260-cli-bringup-consolidation/BE-0260-cli-bringup-consolidation.md) |
<!-- /BE-METADATA -->

## Introduction

The run pipeline of Bajutsu treats a mid-scenario backend crash as infrastructure, not as a verdict.
The pipeline discards the dead lease, which is its hold on one prepared device and the runner process
on it. It leases a fresh device and re-runs the whole scenario. A retry count and a wall-clock budget
bound the loop.

Since
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md),
a retry also forces the device recovery that a scenario otherwise gets only by declaring
`preconditions.erase: true`. On the iOS Simulator backend, that recovery is `simctl shutdown`,
`erase`, and `boot`, then a reinstall of the app under test.

That preparation can itself fail. The pipeline answers a failure by *degrading*. It catches the
device fault, leases the same device again without the forced erase, and lets the scenario run.
Degrading protects the run. Without it, the fault would escape the retry loop and abort every
scenario, discarding the verdicts already earned.

[BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) gives every
one-shot `simctl` call a deadline. CoreSimulator, the macOS service that owns Simulator devices,
now raises a named `simctl.DeviceTimeout` when it wedges, instead of hanging until the
`timeout-minutes` of the continuous integration (CI) job cancels it.

The degradation branch does not distinguish that new type from any other device fault. A device that
never answered `shutdown` is retried on the terms written for a device that answered by refusing.

This item draws the distinction that the degradation branch is missing. A device-preparation timeout
on a crash retry ends that one scenario with a named failure instead of degrading. It also latches the
run, so no later scenario spends its own attempt against the same wedged host.

Ending the scenario rather than the run is the point. Every verdict already earned survives, which is
the property the degradation was written to protect in the first place.

Drawing the distinction needs a platform-neutral name for the fault. The pipeline is backend-agnostic.
It cannot import an iOS backend module to name the exception it catches.

## Motivation

The reason for degrading is a claim about one specific fault.
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
states the claim plainly: "the fault this attempt hits stays this attempt's problem, not the whole
run's."

A device that rejects `erase` has answered. Its refusal is evidence about one operation on one
device. The scenario deserves the bare respawn that it would have received before the forced erase
existed.

A device that never answers has produced no evidence about the operation at all. It shows only that
the service behind every operation has stopped serving. Retrying on that evidence assumes what the
timeout disproved.

Treating the two faults alike has two costs, and the second is the serious one.

The first cost is wasted wall-clock time. The degraded lease re-enters the same `simctl` surface on
the same host. It spends its own deadlines before failing the same way. A second full deadline is
bought to learn what the first already established.

The second cost is what that second failure then does. A degraded lease drops the forced erase. That
is one of the conditions under which the XCUITest environment reuses a warm resident runner, instead
of preparing the device cold.

The reuse path is not free of `simctl`. Only the health check avoids it. `_healthy_resident_driver()`
is a `poll()` plus a bounded `/health` query over the own channel of the runner. `_resume_warm` then
runs the following steps of `_prepare_simulator(cold=False)`:

- the uninstall, the install, and the permission writes
- `terminate`, `launch`, and `openurl`

Every one of these is a `simctl` call that
[BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) bounds.

The degraded lease thus resolves one of two ways, and this item is better than both.

- **The host is wedged for those calls too.** The second timeout is a `device_errors.DeviceError`
  that nothing between `run_one` and `run_all` catches. The run aborts, and every earned verdict is
  lost. That is the exact cost the degradation exists to avoid, paid one deadline later.
- **The host answers those calls.** The scenario runs to completion. It reports `ok`, or a failure
  produced on a host that had just failed to answer `shutdown`. Neither the verdict nor the report
  would say so.

Prime directive 2, determinism first, rules out the second outcome. An ambiguous result fails. It
does not resolve itself in whichever direction the machine happened to go.

Failing that one scenario is not enough on its own. A wedged CoreSimulator is a property of the host,
not of the scenario that happened to meet it. Every later scenario in the run would reach its own
first crash, force its own erase, and spend its own deadline against the same service.

The run pipeline already has a mechanism for this shape of problem. Suppose an earlier scenario has
failed *because* the run-level crash-recovery budget was the binding constraint. A latch then makes
every later scenario fail before it is ever leased, because that is real evidence that the device is
not recovering. A `simctl` call that never returned is the same kind of evidence. It arrives sooner
and more directly.

Naming the fault where the pipeline can see it is a prerequisite, not a detail.
`bajutsu/runner/pipeline.py` is backend-agnostic by prime directive 3. A platform is a backend, and
the deterministic core stays unchanged across targets. The pipeline catches
`bajutsu/device_errors.DeviceError`, the shared base that
[BE-0260](../BE-0260-cli-bringup-consolidation/BE-0260-cli-bringup-consolidation.md) added for this
purpose. It never imports `bajutsu/simctl.py`.

The timeout type that
[BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) introduces lives
in the iOS backend module. The pipeline cannot ask "is this fault a timeout?" without breaking the
separation.

The Android backend has the same shape waiting for it. `bajutsu/adb.py` raises its own
`adb.DeviceError` beside the iOS one. Whatever the pipeline learns to do with a timeout should hold
for an Android emulator, on the day adb calls carry deadlines of their own.

## Detailed design

The design has three units. Unit 2 depends on unit 1, and unit 3 depends on unit 2.

1. **Give the device-timeout fault a platform-neutral name.** Add `DeviceTimeout` to
   `bajutsu/device_errors.py` as a subclass of the `DeviceError` already there. Document it as what
   every backend raises when a device operation exceeded its deadline rather than failing.

   Then give `simctl.DeviceTimeout` the neutral type as a second base. Keep `simctl.DeviceError` as
   the first base. The iOS timeout stays everything it is today: a `simctl.DeviceError`, and through
   it a `device_errors.DeviceError`. It gains the platform-neutral name on top.

   Adding a base, rather than swapping one, keeps every existing handler working unchanged. That
   includes the three groups that
   [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) reasoned about
   individually:

   - the deliberate suppressions that key on `subprocess.CalledProcessError` alone, and so still let
     a timeout through
   - the best-effort probes that fold a timeout into their documented fallback
   - the guarded teardown that absorbs a timeout with a warning, so a run whose scenarios all passed
     still reports its verdicts

   Leave `bajutsu/adb.py` alone in this item. Android has no timeout type to place. Its `_real_run`
   passes no `timeout` to `subprocess.run`, so no adb call can raise a timeout today.

   Bounding the adb surface is the Android counterpart of
   [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md). It wants its
   own item, with its own measured bounds and its own audit of which adb call sites suppress a
   failure. See *Alternatives considered*.

   Defining the neutral type here, with one producer, lets that later item add `adb.DeviceTimeout`
   beneath it. The later item then inherits the pipeline policy of this item with no pipeline change.

2. **Stop degrading a timed-out preparation, and fail the scenario instead.** In
   `bajutsu/runner/pipeline.py`, `_ScenarioRunner.run_one` catches `device_errors.DeviceError` around
   the forced-erase lease and degrades to a bare respawn. Split that handler.

   - A `device_errors.DeviceTimeout` skips the degraded lease.
   - Every other device fault degrades as it does today. Nothing about the refusal case has changed.

   Fail the scenario by leaving the retry loop. Return a `RunResult` with `ok=False` and a failure
   that names the timeout. `run_one` already builds that shape when crash recovery ends without the
   scenario passing.

   Do not let the timeout propagate out of `run_one`. Nothing between `run_one` and `run_all` catches
   `device_errors.DeviceError`. Propagating would abort the whole run and discard the verdict of every
   already-passed scenario. That is the exact cost that
   [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
   refused to pay, and the reason the degradation exists. *Alternatives considered* treats the
   propagating variant on its own terms.

   Keep the accounting that the loop already does.

   - The recovery time of the scenario is billed in the `finally` around the retry loop.
   - The progress callback reports the failure to the operator.
   - A return that leaves the loop early must go through both, not around them.

   The text of the callback is fixed prose today: `(backend crashed mid-run)`. The timeout branch
   needs wording of its own there, as well as in the failure.

   The failure text should name the command and the deadline it exceeded. The exception raised in the
   runner helper of `bajutsu/simctl.py` already carries both. An operator reading the report then
   learns that the host was wedged. The report does not leave the failure of a scenario unexplained.

   The split governs the escalated lease too, and that is intended.
   [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
   lets a repeated crash retry escalate above the forced erase. The escalation goes to a replacement
   device that the pool creates or clones. Such an attempt reaches this same handler with the forced
   erase still on it.

   A `simctl create` or `clone` that never returns is the same wedge as a `shutdown` that never
   returns. It earns the same treatment: the scenario fails and the run latches. A `DeviceError` from
   that call keeps degrading as today. Verification covers the escalated lease as its own case. It
   does not assume that the forced-erase case generalises.

3. **Latch the run once a preparation has timed out.** `RunCrashRecoveryBudget` in
   `bajutsu/runner/recovery.py` already carries the latch. `bajutsu/runner/pipeline.py` already reads
   it before leasing.

   - `mark_given_up()` records that recovery has been abandoned for this run.
   - `given_up()` makes every later scenario fail before its first lease. Such a scenario does not
     pay a full cold-spawn attempt against a device that has demonstrated it is not recovering.

   Set that latch from the timeout branch of unit 2. The wedge is then established once. Every
   remaining scenario does not rediscover it.

   The latch is set unconditionally on a timeout. The budget path differs: it sets the latch only when
   the run-level budget was the binding constraint. The budget is a measurement, and a
   slow-but-successful recovery can also trip it. That is why the budget path needs the extra
   qualification. A `simctl` call that never returned is not a measurement of slowness. It is a direct
   observation that the service stopped answering, so it needs none.

   The message that a latched scenario reports names the budget today. A run latched by a timeout
   never had a budget as its cause. `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` may well be unset. Then
   `run_crash_budget.budget` is `None`, and the pre-lease failure formats it with `:g`. That raises a
   `TypeError` out of `run_one`, which aborts `run_all` and discards every verdict the run had earned.
   Giving the latch a cause is thus a correctness requirement, not a matter of wording.

   Give `mark_given_up()` a cause that the latch stores and the consumers of `given_up()` report. The
   pre-lease failure then says why the run was abandoned: a device operation timed out, or the
   run-level budget was spent. Keeping one latch, rather than adding a second, is deliberate. See
   *Alternatives considered*.

Verification needs no Simulator and belongs in the deterministic gate. The existing tests of the
pipeline already drive `run_one` with a substituted lease callable. Four cases follow.

- A lease raises `simctl.DeviceTimeout` on the forced-erase attempt. The test asserts three things at
  once: the scenario fails, the degraded second lease was never attempted, and the run-level latch is
  set.
- A lease raises an ordinary `simctl.DeviceError`. The test asserts that the degradation still
  happens.
- An escalated attempt has a replacement-device lease that times out. The test asserts the same three
  things. The behavior of the
  [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  rung is then pinned, not inferred.
- A second scenario runs after a latched timeout, with `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` unset. The
  test asserts that it fails before its first lease, with the timeout named as the cause. Today, that
  case formats a `None` budget.

## Alternatives considered

- **Let the timeout propagate out of `run_one` and abort the run.** This is the literal reading of the
  review finding that prompted this item: if the degradation is wrong, remove the degradation.
  Rejected on what propagating does, not on what it sounds like.
  - No handler between `run_one` and `run_all` catches `device_errors.DeviceError`. The exception
    leaves `run_all`, and the run reports nothing at all.
  - The affected scenario gets no failure. Every earlier scenario loses the verdict it had already
    earned.
  - [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
    weighed that outcome and refused it. Nothing about a timeout makes the verdicts of the earlier
    scenarios less true.
  - Failing the one scenario and latching the run delivers the same loud, fast, diagnosable failure,
    while keeping those verdicts.
- **Keep degrading and rely on the run-level crash-recovery budget to stop the bleeding.** The budget
  from
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  does bound how long a run may spend recovering, so a wedged host is eventually caught. Rejected on
  two counts.
  - The budget is unset by default, so on any lane that has not opted in it bounds nothing.
  - Even where it is set, it stops the run after the wasted deadlines have been spent. Meanwhile the
    warm-resident path has published verdicts from the wedged host.

  A budget bounds how much of a bad outcome a run may suffer. That differs from declining to enter
  the outcome on evidence already in hand.
- **Apply the same policy to a timeout on the first, non-forced-erase lease of a scenario.** The idea
  is tempting for symmetry. A wedged host is a wedged host, whichever lease met it. Today, the first
  lease's own `device_errors.DeviceError` propagates out of `run_all`. Left out deliberately:
  - That is the pre-existing behavior of the ordinary lease path. BE-0353 explicitly did not touch
    it.
  - Changing it would convert an established fail-loudly route into a per-scenario failure for every
    device fault, not only for a timeout.
  - The scope of this item is the branch that the forced-erase retry added. There the degradation is
    new, and its reasoning demonstrably does not cover a timeout.

  Whether the ordinary lease path wants the same treatment is a separate question about a different
  guarantee.
- **Bound the adb calls in this item, so the neutral type ships with two producers.** Rejected as the
  work of a second item wearing the name of this one. `bajutsu/adb.py` passes no `timeout` anywhere.
  Giving it one means three jobs:
  - choosing bounds for the adb command surface
  - auditing every call site that suppresses a failure
  - deciding what each best-effort probe does with a timeout

  That is the whole of what
  [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) did for
  `simctl`, on a backend whose commands have not been measured. A neutral type with one producer costs
  nothing. It is the seam that the later item plugs into.
- **Restart CoreSimulator when a preparation times out, rather than abandoning the run.** A wedged
  service invites this remedy. The recovery ladder from
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) is where such a
  rung would live. Rejected here for the reason that
  [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) already gave
  when it declined the same remedy. No failure reviewed so far shows the service-level signature that
  a restart would target. A bounded call is the prerequisite for ever deciding that from data. This
  item makes the wedge a fact that the pipeline acts on. A rung that repairs the wedge can be added
  later without revisiting anything here.
- **Track the timeout latch on a new object instead of extending `RunCrashRecoveryBudget`.** This is
  the tidy reading, since a wedged host is not a budget concern, and `RunCrashRecoveryBudget` is named
  for accounting. Rejected, because the latch it holds is already not accounting. `given_up()` answers
  "has recovery been abandoned for this run", and budget exhaustion is merely the only cause today.
  A second object would need three things:
  - its own read in the same pre-lease check
  - its own threading discipline under the parallel path of `run_all`
  - its own place in the failure message

  That is three copies of existing machinery, to express one more value of a field. Extending the
  cause that the existing latch records keeps one answer to one question.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1: add `device_errors.DeviceTimeout`, and give `simctl.DeviceTimeout` that neutral type
      as a second base beside `simctl.DeviceError`, leaving every existing handler and
      `bajutsu/adb.py` unchanged.
- [x] Unit 2: split the forced-erase-prep handler of `run_one` so a `device_errors.DeviceTimeout`
      fails the scenario with a named failure and its own progress wording instead of degrading to a
      bare respawn, while every other device fault still degrades. The split covers the escalated
      replacement-device lease (BE-0354), which reaches the same handler.
- [x] Unit 3: latch the run from that branch through `mark_given_up()`, and give the latch a cause
      so a latched scenario reports the timeout rather than formatting an unset budget, which raises
      a `TypeError` out of `run_all` today.

Log:

- [#1910](https://github.com/bajutsu-e2e/bajutsu/pull/1910): all three units in one change.
  - Added `device_errors.DeviceTimeout` and gave `simctl.DeviceTimeout` that neutral type as a second
    base. Every handler that BE-0363 audited still matches, unchanged. `bajutsu/common/backend_cli/adb.py`
    is untouched. The `is_host_fault` of `recovery` now names the neutral type, as its own docstring
    had promised this item would.
  - Split the forced-erase-prep handler of `run_one`. A timeout leaves the retry loop and fails the
    scenario, through the same `finally` that bills recovery time. The failure names the command and
    deadline that the exception carries, and has its own progress wording. Every other device fault
    still degrades. The escalated replacement-device lease (BE-0354) is covered by the same branch and
    pinned by its own test.
  - Gave the run-level latch a cause: `mark_given_up(cause)` and `given_up_cause()` replace the bare
    `given_up()` predicate. That removes the `TypeError` which an unset
    `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET` used to raise out of `run_all`, when a latched scenario
    formatted a `None` budget.

## References

- [BE-0363 — Bound every simctl call with a timeout so a wedged CoreSimulator fails with a named cause](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md)
  — introduces `simctl.DeviceTimeout`, the fault this item teaches the pipeline to act on.
- [BE-0353 — Force device recovery on a backend-crash retry and cap total crash-recovery time per run](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  — adds the forced-erase retry, its degradation branch, and the run-level budget whose latch this
  item reuses.
- [BE-0354 — Detect a wedged XCUITest session fast and escalate a repeated crash retry to a replacement device](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  — the escalation rung whose replacement-device lease reaches the same handler this item splits.
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

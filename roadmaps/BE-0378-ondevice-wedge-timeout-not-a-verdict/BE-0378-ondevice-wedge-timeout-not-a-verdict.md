**English** · [日本語](BE-0378-ondevice-wedge-timeout-not-a-verdict-ja.md)

# BE-0378 — Stop a wedged-Simulator timeout from failing the on-device conformance suite as a verdict

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0378](BE-0378-ondevice-wedge-timeout-not-a-verdict.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0378") |
| Implementing PR | [#1725](https://github.com/bajutsu-e2e/bajutsu/pull/1725) |
| Topic | Platform support |
| Related | [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md), [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md), [BE-0374](../BE-0374-device-timeout-crash-retry-policy/BE-0374-device-timeout-crash-retry-policy.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md) |
<!-- /BE-METADATA -->

## Introduction

Every backend Bajutsu drives implements one interface, the [driver](../../docs/glossary.md#driver-backend-actuator-platform). The driver is the single platform-specific seam in an otherwise platform-neutral core. A conformance suite checks that each implementation honours the same contract. On iOS the suite is a pytest harness that drives a real Simulator. It gates a merge: the required `E2E (iOS)` check waits on `conformance (xcuitest)`.

BE-0334 gave that harness the recovery that `bajutsu run` already had. A resident-runner crash mid-suite is infrastructure, not a verdict. The harness therefore discards the dead lease, leases a fresh [device](../../docs/glossary.md#target-app-device), and re-runs the affected test. A single line decides what earns that treatment: `isinstance(exc, base.BackendCrashError)`.

A wedged CoreSimulator raises a different type. CoreSimulator is the macOS service that owns Simulator devices. BE-0363 bounded every `simctl` call with a deadline and named the exceeded deadline `simctl.DeviceTimeout`, a subclass of `DeviceError` introduced for exactly this fault. The recovery predicate reads a statement about the host as a statement about the code under test. A wedge that lands inside the conformance harness fails a required check outright.

This item proposes three changes and argues against a fourth.

- The harness resolves the app's data container once per lease instead of once per test. That is where the exposure comes from.
- The harness retries the one remaining read once when it times out. The stall behind the observed failure had cleared 41 seconds later.
- The classifier's two questions become two predicates. The questions are what a respawn can repair, and what the lane should report as a host fault. A wedge that outlasts both attempts is then recorded as infrastructure and is not handed to a retry.

The fourth change, widening the retry predicate, is the obvious remedy and the wrong one. A respawn is a heavier instrument than this fault needs. The rungs it would climb are made of the same `simctl` calls that just stalled.

## Motivation

The failure is measured, not hypothetical. On 2026-08-19 the first attempt of the iOS end-to-end workflow on [PR #1657](https://github.com/bajutsu-e2e/bajutsu/pull/1657) failed [`conformance (xcuitest)`](https://github.com/bajutsu-e2e/bajutsu/actions/runs/32204192080/job/95925025295) after 26 minutes:

```
E   bajutsu.simctl.DeviceTimeout: device operation timed out after 60s: xcrun simctl
    get_app_container 2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA com.bajutsu.showcase.ios.swiftui data
    (this host's CoreSimulator may be wedged)
ERROR tests/test_driver_conformance_ondevice.py::TestXcuitestDriverConformance::test_picker_wheel_capability_matches_behavior
======== 20 passed, 2 skipped, 3 warnings, 1 error in 939.23s (0:15:39) ========
```

No conformance assertion disagreed with the driver contract. Twenty tests passed and two skipped. The one error arrived at the *setup* of the 12th of 23 collected items. The timeout struck a fixture, before the test body it was preparing ever ran.

That fixture runs once per test, and the fact it fetches never changes. `_spec_path` resolves the installed app's data container by running `simctl get_app_container`. The result is the path of the specification file that the app polls to render each conformance screen. The `harness` fixture that calls `_spec_path` is function-scoped. A suite of 23 collected items therefore makes 23 identical device round trips for one path, and that path stays fixed for as long as the app installation does.

The stall was brief, and the log dates it precisely:

- The 11th item passed at 01:48:22.
- The 12th recorded its error at 01:50:13, after its 60-second deadline elapsed.
- The 13th passed at 01:50:54. Its own setup ran the identical `simctl get_app_container` against the same device and got an answer, 41 seconds after the failed call was abandoned.

Eleven further items then ran to completion, nine passing and two skipping. The job's diagnostics step went on to collect a 7.6 MB `simctl diagnose` archive from the same device with exit code 0. Re-running the whole workflow on the same commit passed. The daemon had not stopped serving. It stalled past one deadline and served again well inside the next test.

The cost lands on a required check and on a person. The job spent 26 minutes to report a host fault as a red gate. A maintainer then had to read it, decide it was infrastructure, and re-run it. BE-0334 already disposed of the two responses that suggest themselves at that moment:

- Demoting the check gives up a real gate to dodge an infrastructure problem.
- Re-running by hand teaches contributors to re-run a red required check without reading it.

Its argument holds unchanged here.

A reader who has followed the failure this far will conclude that `is_infrastructure_fault` is too narrow. The reader will also conclude that admitting `simctl.DeviceTimeout` would have absorbed the wedge. Widening the predicate would indeed trigger a retry, and the retry would discard the lease and cold-spawn a new one. That respawn does inherit a device repair ladder. BE-0344 repairs the Simulator between cold attempts, and the conformance harness reaches the ladder through the same `launch_driver` path the run pipeline uses. The harness is not without a remedy.

Two properties of the ladder make it wrong for this fault:

- Its rungs are themselves `simctl` calls, a property BE-0344's own design records. A rung that wedges is cut off by BE-0363's per-call deadline and surfaces as a device fault.
- The rung that replaces the device outright fires when `simctl list` no longer shows the device. A device that answers 41 seconds later is still listed.

What the harness genuinely lacks sits above the spawn layer. The run pipeline drives two steps, and the harness has no path to either. One is the forced erase that a crash retry gained in BE-0353. The other is the escalation to a replacement device that the pool performs in BE-0354.

The arithmetic finishes the argument. Each re-lease pays a cold spawn, and the iOS lane allows up to 300 seconds for one, bounded by a 300-second crash-recovery budget. The lane would spend up to five minutes rebooting and reinstalling to answer a stall that cleared in 41 seconds on its own.

The risk of widening the predicate deserves a precise name, because the obvious worry is the wrong one. The predicate guarantees that a contract violation keeps failing at once. That guarantee protects a run from retrying a genuine failure into a slow green. `simctl.DeviceTimeout` cannot violate it: a subprocess deadline raises a timeout, never an assertion, so a timeout is never a verdict about the app. Proportion disqualifies the widening, not safety. A remedy that rebuilds the device is far too heavy for a fault that outlives one deadline and not the next.

## Detailed design

The work breaks into three units. Unit 2 depends on unit 1. Unit 3 depends on neither.

1. **Resolve the app's data container once per lease, not once per test.** The container path belongs to the app installation on the leased device. The harness should fetch the path when it takes a lease and reuse it for every test that lease serves.

   The cached path must be dropped when the lease is discarded. The reason is specific, not precautionary. Each lease launches the app under the default preconditions, whose `reinstall` mode is `clean` (uninstall, then install). An uninstall takes the app's data container with it. A path cached for the module's lifetime would survive that reinstall and point into a directory the next lease no longer uses. The harness would then write each screen specification where the app is not reading. The suite would fail on a conformance assertion: a host fault reported as a driver-contract defect, which is a worse outcome than the timeout it replaced.

   The invalidation point already exists. `LeaseHolder` discards a dead lease in `invalidate()` and re-launches on the next access. Give the holder a per-lease identity, a generation counter incremented at each launch. The harness then memoises the container path against that identity. Keep the memo in the harness, not in the recovery plugin. A data container is an iOS notion, and the plugin's own design constraint is that it never learns which backend it drives.

2. **Retry the one remaining read once when it times out.** After unit 1, the harness makes a single `get_app_container` call per lease, on its own behalf, before any test body runs. When the first attempt exceeds its deadline, give the call one further attempt. When the second attempt times out too, fail with the timeout.

   The measurement above sizes the retry. The identical call succeeded 41 seconds after the failed one was abandoned, so one more attempt would have cleared the observed occurrence outright. A second attempt is enough because the retry does not try to outlast an arbitrary wedge. A stall that survives two full deadlines is the persistent fault that unit 3 exists to report.

   The retry does not reopen the policy that BE-0363 settled. BE-0363 rejected retrying inside the shared `simctl` runner helper. A retry there would apply to every call in the Simulator lifecycle, including calls whose result is a scenario's own data. The retry proposed here is the harness retrying its own preparatory read. The call is idempotent, its result is a path rather than a verdict, and no scenario has begun. Leave `bajutsu/simctl.py` unchanged.

3. **Separate diagnosing a host fault from deciding a retry.** Today `is_infrastructure_fault` answers one question: whether a respawn may recover the exception. Its single consumer, the recovery plugin's report hook, uses the answer for exactly that decision. The lane needs an answer to a second question that nothing answers today: whether the failure was the host's fault rather than the code's.

   Give each question its own predicate in `bajutsu/runner/recovery.py`, where the recovery contract already lives. Each docstring states which question the predicate answers.

   - `recovers_by_respawn` takes the retry decision. It holds today's exact `base.BackendCrashError` membership, so every existing call site keeps its behaviour byte for byte.
   - `is_host_fault` takes the diagnosis. It holds `base.BackendCrashError` and `simctl.DeviceTimeout`.

   Retire the name `is_infrastructure_fault` instead of reusing it for the diagnosis. Retiring it keeps the module honest. The module's own docstring and the plugin's `RECOVER_MARKER` text both define an infrastructure fault as the set that gets re-leased. A diagnosis predicate wearing the old name would let a later caller rely on those sentences and silently receive the wider set. That would reintroduce the retry onto a wedge that this item rejects on proportion. Rewrite both sentences in the same change.

   Which type the diagnosis predicate names depends on which of two items lands first. Neither blocks the other. `bajutsu/runner/recovery.py` already imports `simctl` and already catches `simctl.DeviceTimeout` in its guarded teardown. Naming that type in a predicate beside it adds no coupling the module does not have today. BE-0374 proposes a platform-neutral `device_errors.DeviceTimeout` for the run pipeline's own timeout policy, and the iOS type would gain it as a second base. Once that name exists, this predicate names it instead and covers every backend that later adopts it.

   The diagnosis predicate must never reach the retry decision. Keeping both predicates in one module makes that boundary checkable in one place. BE-0334 extracted this module to prevent exactly the drift of two predicates whose consumers drift apart.

   The diagnosis then has two destinations, and both already exist:

   - The job log gains a line naming a host fault that was deliberately not retried. A person reading the red check then sees the wedge rather than a conformance failure.
   - The recovery report at `BAJUTSU_BACKEND_RECOVERY_REPORT`, uploaded as an artifact since BE-0334, gains an event kind for the same fault. The event carries the command and the deadline it exceeded. A degrading lane then shows up as a rising wedge count beside the respawn count that the report already keeps, not as a red check somebody re-ran.

Verification stays in the deterministic gate, which needs no Simulator. Both predicates are pure functions of an exception type. Existing tests already cover the recovery plugin by driving the whole pytest protocol against a fake driver with no device present. "A host fault is reported and not retried" joins them as one more case. Two more tests cover the other units:

- The per-lease memo is exercised by driving `LeaseHolder` directly across an `invalidate()`.
- The retry is exercised by a substituted runner that raises `simctl.DeviceTimeout` once and then answers, and by one that raises twice.

The wedge itself cannot be reproduced off macOS. This item claims no coverage of it.

This item changes the behaviour of the on-device pytest harnesses alone. Unit 3 does edit `bajutsu/runner/recovery.py`, a shipped module rather than a harness. No run-pipeline caller consumes either predicate: outside the tests, the recovery plugin's report hook is the only caller of `is_infrastructure_fault` today.

How `bajutsu run` handles a device timeout raised while leasing is BE-0374's subject. BE-0374 makes the pipeline's forced-erase degradation branch fail the scenario on a timeout instead of retrying onto the wedged device, and latch the run. The two proposals meet only at the module they both touch. They answer for different callers: the pipeline drives the erase-and-replace rungs above the spawn layer, and the conformance harness leases one pinned device with none of them.

## Alternatives considered

- **Admit `simctl.DeviceTimeout` into the retry predicate.** This is the obvious remedy and the closest rival to unit 3. Rejected on proportion. A respawn rebuilds the device to answer a stall that cleared in 41 seconds, and it spends up to the lane's 300-second recovery budget. The repair rungs it would climb are `simctl` calls that a genuinely wedged daemon would stall in turn. The rejection is conditional, not permanent. A recurrence that outlasts unit 2's second attempt is a different fault from the one measured here, and evidence of one would reopen this choice.
- **Retry every timed-out `simctl` call inside the shared runner helper.** This is the general form of unit 2, and BE-0363 weighed and rejected it. A call that never returns says the daemon has stopped serving, so a retry would stall the same way. The helper also covers calls whose result is a scenario's own data. The occurrence above shows the premise does not hold for every wedge, since the same daemon answered 41 seconds later. One occurrence is thin ground for reopening a policy that governs the whole Simulator lifecycle. Unit 2 takes the narrow form instead, in the harness's own code, and leaves `bajutsu/simctl.py` untouched.
- **Cache the container path for the module's lifetime.** This is simpler than a per-lease memo, and wrong for the reason unit 1 gives. The `clean` reinstall that each lease performs replaces the container, so the cached path outlives what it names.
- **Have the app under test report its own container path.** The showcase app already cooperates with the harness, since it polls the specification file for each screen. It could report its Documents path over the collector channel and remove the `simctl` read altogether. Rejected as more machinery than the problem needs. The report would have to arrive before the first test and be re-established after every respawn. That is the same per-lease lifetime that unit 1 covers with a memo.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — resolve the app's data container once per lease and memoise it against the
      lease's identity, so a cold respawn's `clean` reinstall drops the cached path.
- [x] Unit 2 — give that one remaining read a second attempt on a timeout, in the harness rather
      than in the shared `simctl` helper.
- [x] Unit 3 — split `is_infrastructure_fault` in two. `recovers_by_respawn` takes the retry
      decision with its membership unchanged, and `is_host_fault` takes the diagnosis and gains
      `simctl.DeviceTimeout`. Rewrite the module docstring and the `RECOVER_MARKER` text the old
      name leaves behind, and report a non-retried host fault in the job log and the recovery
      report.

## References

- [`bajutsu/runner/recovery.py`](../../bajutsu/runner/recovery.py) — `is_infrastructure_fault`, the
  one-line classifier this item splits in two.
- [`bajutsu/simctl.py`](../../bajutsu/simctl.py) — `DeviceTimeout` and the deadline that raises it.
- [`tests/test_driver_conformance_ondevice.py`](../../tests/test_driver_conformance_ondevice.py) —
  the harness whose function-scoped `harness` fixture resolves the data container through the
  `_spec_path` helper once per test.
- [`tests/backend_crash_recovery.py`](../../tests/backend_crash_recovery.py) — the recovery plugin,
  its `LeaseHolder`, and the report this item extends.
- [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md)
  — gave the conformance harness the pipeline's crash recovery, and wrote the classifier this item
  revisits.
- [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md) — bounded
  every `simctl` call and introduced `DeviceTimeout` as the named symptom of a wedged CoreSimulator.
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — the
  cold-spawn repair ladder both the run pipeline and the on-device harness reach, and the per-rung
  limits that make it the wrong instrument for a brief stall.
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  — the forced device erase a crash-triggered retry gained in the run pipeline.
- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  — the replacement device a repeated crash retry can escalate to in the run pipeline.
- [BE-0374](../BE-0374-device-timeout-crash-retry-policy/BE-0374-device-timeout-crash-retry-policy.md)
  — the run pipeline's own answer to a device timeout, and the platform-neutral name for the fault.

**English** · [日本語](BE-XXXX-ondevice-wedge-timeout-not-a-verdict-ja.md)

# BE-XXXX — Stop a wedged-Simulator timeout from failing the on-device conformance suite as a verdict

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ondevice-wedge-timeout-not-a-verdict.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md), [BE-0363](../BE-0363-simctl-subprocess-timeout/BE-0363-simctl-subprocess-timeout.md), [BE-0374](../BE-0374-device-timeout-crash-retry-policy/BE-0374-device-timeout-crash-retry-policy.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md) |
<!-- /BE-METADATA -->

## Introduction

Every backend Bajutsu drives implements one interface — the [driver](../../docs/glossary.md#driver-backend-actuator-platform), the single
platform-specific seam in an otherwise platform-neutral core — and a conformance suite checks that
each implementation honours the same contract. On iOS that suite is a pytest harness driving a real
Simulator, and it gates a merge: the required `E2E (iOS)` check waits on `conformance (xcuitest)`.

BE-0334 gave that harness the recovery `bajutsu run` already had. A resident-runner crash mid-suite
is infrastructure rather than a verdict, so the harness discards the dead lease, leases a fresh
[device](../../docs/glossary.md#target-app-device), and re-runs the affected test. One predicate decides what earns that treatment, and
that predicate is a single line: `isinstance(exc, base.BackendCrashError)`.

A wedged CoreSimulator — the macOS service that owns Simulator devices — raises a different type.
BE-0363 bounded every `simctl` call with a deadline and named the exceeded deadline
`simctl.DeviceTimeout`, a subclass of `DeviceError` introduced for exactly this fault. The
recovery predicate therefore reads a statement about the host as a statement about the code under
test, and a wedge that lands inside the conformance harness fails a required check outright.

This item proposes three changes and argues against a fourth. The harness resolves the app's data
container once per lease rather than once per test, which is where the exposure comes from, and
retries that one remaining read once when it times out, because the stall that produced the observed
failure had cleared 41 seconds later. The classifier's two questions — what a respawn can repair, and
what the lane should report as a host fault — become two predicates, so a wedge that outlasts both
attempts is recorded as infrastructure rather than handed to a retry. Widening the retry predicate
itself is the obvious remedy and the wrong one: a respawn is a heavier instrument than this fault
needs, and the rungs it would climb are made of the same `simctl` calls that just stalled.

## Motivation

The failure is measured, not hypothetical. On 2026-08-19 the first attempt of the iOS end-to-end
workflow on [PR #1657](https://github.com/bajutsu-e2e/bajutsu/pull/1657) failed
[`conformance (xcuitest)`](https://github.com/bajutsu-e2e/bajutsu/actions/runs/32204192080/job/95925025295)
after 26 minutes:

```
E   bajutsu.simctl.DeviceTimeout: device operation timed out after 60s: xcrun simctl
    get_app_container 2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA com.bajutsu.showcase.ios.swiftui data
    (this host's CoreSimulator may be wedged)
ERROR tests/test_driver_conformance_ondevice.py::TestXcuitestDriverConformance::test_picker_wheel_capability_matches_behavior
======== 20 passed, 2 skipped, 3 warnings, 1 error in 939.23s (0:15:39) ========
```

No conformance assertion disagreed with the driver contract. Twenty tests passed, two skipped, and
the one error arrived at the *setup* of the 12th of 23 collected items — the timeout struck a
fixture, before the test body it was preparing ever ran.

That fixture runs once per test, and the fact it fetches never changes. `_spec_path` resolves the
installed app's data container by running `simctl get_app_container`. It returns the path of the
specification file the app polls to render each conformance screen. Because the `harness` fixture
that calls it is function-scoped, a suite of 23 collected items makes 23 identical device round trips
for a single path — a path that is fixed for as long as the app installation is.

The stall was brief, and the log dates it precisely. The 11th item passed at 01:48:22, the 12th
recorded its error at 01:50:13 after its 60-second deadline elapsed, and the 13th passed at 01:50:54:
that test's own setup ran the identical `simctl get_app_container` against the same device and got an
answer, 41 seconds after the failed call was abandoned. Eleven further items then ran to completion,
nine passing and two skipping, and the job's diagnostics step went on to collect a 7.6 MB
`simctl diagnose` archive from the same device with exit code 0. Re-running the whole workflow on the
same commit passed. The daemon had not stopped serving, then: it stalled past one deadline and served
again well inside the next test.

The cost lands on a required check and on a person. The job spent 26 minutes to report a host fault
as a red gate, and a maintainer had to read it, decide it was infrastructure, and re-run it. BE-0334
already disposed of the two responses that suggest themselves at that moment — demoting the check
gives up a real gate to dodge an infrastructure problem, and re-running by hand teaches contributors
to re-run a red required check without reading it — and its argument holds unchanged here.

A reader who has followed the failure this far will conclude that `is_infrastructure_fault` is too
narrow, and that admitting `simctl.DeviceTimeout` would have absorbed the wedge. Widening the
predicate would indeed trigger a retry, and the retry would discard the lease and cold-spawn a new
one. That respawn does inherit a device repair ladder: BE-0344 repairs the Simulator between cold
attempts, and the conformance harness reaches it through the same `launch_driver` path the run
pipeline uses, so the harness is not without a remedy. Both properties of the remedy are wrong for
this fault, though. Its rungs are themselves `simctl` calls — a property BE-0344's own design
records — so a rung that wedges is cut off by BE-0363's per-call deadline and surfaces as a device
fault; and the rung that replaces the device outright fires only when `simctl list` no longer shows
it, which a device that answers 41 seconds later still does. What the harness genuinely lacks sits above the
spawn layer: the forced erase a crash retry gained in BE-0353, and the escalation to a replacement
device the pool performs in BE-0354, both of which the run pipeline drives and the harness has no
path to.

The arithmetic finishes the argument. Each re-lease pays a cold spawn for which the iOS lane allows
up to 300 seconds, bounded by a 300-second crash-recovery budget, so the lane would spend up to five
minutes rebooting and reinstalling to answer a stall that cleared in 41 seconds on its own.

The risk of widening the predicate is worth naming precisely, because the obvious worry is the wrong
one. The predicate's guarantee — a contract violation keeps failing at once — protects a run from
retrying a genuine failure into a slow green, and `simctl.DeviceTimeout` cannot violate that
guarantee: a timeout is raised by a subprocess deadline, never by an assertion, so it is never a
verdict about the app. What disqualifies the widening is proportion rather than safety. A remedy that
rebuilds the device is far too heavy an answer to a fault that outlives one deadline and not the next.

## Detailed design

The work breaks into three units. Unit 2 depends on unit 1; unit 3 depends on neither.

1. **Resolve the app's data container once per lease, not once per test.** The container path
   belongs to the app installation on the leased device, so the harness should fetch it when it
   takes a lease and reuse it for every test that lease serves.

   The cached path must be dropped when the lease is discarded, and the reason is specific rather
   than precautionary. Each lease launches the app under the default preconditions, whose
   `reinstall` mode is `clean` — uninstall, then install — and an uninstall takes the app's data
   container with it. A path cached for the module's lifetime would survive that reinstall and point
   into a directory the next lease no longer uses, so the harness would write each screen
   specification where the app is not reading. The suite would then fail on a conformance assertion:
   a host fault reported as a driver-contract defect, which is a worse outcome than the timeout it
   replaced.

   The invalidation point already exists. `LeaseHolder` discards a dead lease in `invalidate()` and
   re-launches on the next access, so give the holder a per-lease identity — a generation counter
   incremented at each launch — and have the harness memoise the container path against that
   identity. Keep the memo in the harness rather than in the recovery plugin: a data container is an
   iOS notion, and the plugin's own design constraint is that it never learns which backend it
   drives.

2. **Retry the one remaining read once when it times out.** After unit 1 the harness makes a single
   `get_app_container` call per lease, on its own behalf, before any test body runs. Give that call
   one further attempt when the first exceeds its deadline, and fail with the timeout when the second
   does too.

   The measurement above sizes the retry and justifies it: the identical call succeeded 41 seconds
   after the failed one was abandoned, so one more attempt would have cleared the observed occurrence
   outright. A second attempt is enough because the retry is not trying to outlast an arbitrary
   wedge — a stall that survives two full deadlines is the persistent fault unit 3 exists to report.

   This does not reopen the policy BE-0363 settled. That item rejected retrying inside the shared
   `simctl` runner helper, where a retry would apply to every call in the Simulator lifecycle,
   including the ones whose result is a scenario's own data. The retry proposed here is the harness
   retrying its own preparatory read: the call is idempotent, its result is a path rather than a
   verdict, and no scenario has begun. Leave `bajutsu/simctl.py` unchanged.

3. **Separate diagnosing a host fault from deciding a retry.** `is_infrastructure_fault` answers one
   question today, whether a respawn may recover the exception, and its single consumer — the
   recovery plugin's report hook — uses the answer for exactly that decision. The lane needs an
   answer to a second question that nothing answers today: whether the failure was the host's fault
   rather than the code's.

   Give each question its own predicate in `bajutsu/runner/recovery.py`, where the recovery
   contract already lives, with a docstring stating which question each one answers. The retry
   decision moves to a predicate whose name states it — `recovers_by_respawn`, holding today's exact
   `base.BackendCrashError` membership, so every existing call site keeps its behaviour byte for
   byte. `is_infrastructure_fault` keeps its name, which already describes a diagnosis rather than a
   remedy, and gains `simctl.DeviceTimeout`.

   Which type the diagnosis predicate names depends on which of two items lands first, and neither
   blocks the other. `bajutsu/runner/recovery.py` already imports `simctl` and already catches
   `simctl.DeviceTimeout` in its guarded teardown, so naming that type in a predicate beside it adds
   no coupling the module does not have today. BE-0374 proposes a platform-neutral
   `device_errors.DeviceTimeout`, which the iOS type would gain as a second base, for the run
   pipeline's own timeout policy; once that name exists, this predicate names it instead and covers
   every backend that later adopts it.

   The diagnosis predicate must never reach the retry decision, and keeping both in one module is
   what makes that boundary checkable in one place. Two predicates whose consumers drift apart is
   precisely the drift BE-0334 extracted this module to prevent.

   The diagnosis then has two destinations, both of which already exist. The job log gains a line
   naming a host fault that was deliberately not retried, so a person reading the red check sees the
   wedge rather than a conformance failure. The recovery report at
   `BAJUTSU_BACKEND_RECOVERY_REPORT`, uploaded as an artifact since BE-0334, gains an event kind for
   the same fault, carrying the command and the deadline it exceeded. A degrading lane then shows up
   as a rising wedge count beside the respawn count the report already keeps, rather than as a
   red check somebody re-ran.

Verification stays in the deterministic gate, which needs no Simulator. Both predicates are pure
functions of an exception type. The recovery plugin is already covered by tests that drive the whole
pytest protocol against a fake driver with no device present, so "a host fault is reported and not
retried" joins them as one more case. The per-lease memo is exercised by driving `LeaseHolder`
directly across an `invalidate()`, and the retry by a substituted runner that raises
`simctl.DeviceTimeout` once and then answers, and by one that raises twice. What cannot be reproduced
off macOS is the wedge itself; this item claims no coverage of it.

This item changes the on-device pytest harnesses alone. How `bajutsu run` handles a device timeout
raised while leasing is BE-0374's subject: that proposal makes the pipeline's forced-erase
degradation branch fail the scenario on a timeout rather than retry onto the wedged device, and
latch the run. The two proposals meet only at the module they both touch, and they answer for
different callers — the pipeline drives the erase-and-replace rungs above the spawn layer, and the
conformance harness leases one pinned device with none of them.

## Alternatives considered

- **Admit `simctl.DeviceTimeout` into the retry predicate.** The obvious remedy, and the closest
  rival to unit 3. Rejected on proportion: a respawn rebuilds the device to answer a stall that
  cleared in 41 seconds, spending up to the lane's 300-second recovery budget, and the repair rungs
  it would climb are `simctl` calls that a genuinely wedged daemon would stall in turn. The rejection
  is conditional rather than permanent — a recurrence that outlasts unit 2's second attempt is a
  different fault from the one measured here, and evidence of one would reopen this choice.
- **Retry every timed-out `simctl` call inside the shared runner helper.** The general form of unit
  2, and the change BE-0363 weighed and rejected: a call that never returns says the daemon has
  stopped serving, so a retry would stall the same way, and the helper covers calls whose result is a
  scenario's own data. The occurrence above shows the premise does not hold for every wedge, since
  the same daemon answered 41 seconds later, but one occurrence is thin ground for reopening a policy
  that governs the whole Simulator lifecycle. Unit 2 takes the narrow form instead, in the harness's
  own code, leaving `bajutsu/simctl.py` untouched.
- **Cache the container path for the module's lifetime.** Simpler than a per-lease memo, and wrong
  for the reason unit 1 gives: the `clean` reinstall each lease performs replaces the container, so
  the cached path outlives what it names.
- **Have the app under test report its own container path.** The showcase app already cooperates with
  the harness, since it polls the specification file for each screen, so it could report its
  Documents path over the collector channel and remove the `simctl` read altogether. Rejected as more
  machinery than the problem needs: the report would have to arrive before the first test and be
  re-established after every respawn, which is the same per-lease lifetime unit 1 covers with a memo.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — resolve the app's data container once per lease and memoise it against the
      lease's identity, so a cold respawn's `clean` reinstall drops the cached path.
- [ ] Unit 2 — give that one remaining read a second attempt on a timeout, in the harness rather
      than in the shared `simctl` helper.
- [ ] Unit 3 — split the retry decision from the host-fault diagnosis, admit
      `simctl.DeviceTimeout` into the diagnosis alone, and report a non-retried host fault in
      the job log and the recovery report.

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

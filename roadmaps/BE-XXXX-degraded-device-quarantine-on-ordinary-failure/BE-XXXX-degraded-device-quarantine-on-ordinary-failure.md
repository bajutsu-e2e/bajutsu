**English** · [日本語](BE-XXXX-degraded-device-quarantine-on-ordinary-failure-ja.md)

# BE-XXXX — Quarantine a device whose capture pipeline stalled, even when the scenario failed on its own assertion

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-degraded-device-quarantine-on-ordinary-failure.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) |
<!-- /BE-METADATA -->

## Introduction

The run pipeline can move a scenario off an iOS Simulator whose services have degraded past what an
erase clears, by asking the next bring-up for a replacement device
([BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)).
That escalation lives entirely inside the crash handler — the `except BackendCrashError` arm of
`run_one` (`bajutsu/runner/pipeline.py:452`) — so it is reachable only by a scenario whose attempt
*crashed*. A scenario whose respawned attempt instead runs to completion and fails on an ordinary
assertion never reaches it, even when the very signal the escalation reads, a video recording that
never confirmed it started, is standing at that moment. The degraded device is then released back to
the pool, and every later scenario in the run leases it. This proposal reads that standing signal on
the ordinary-failure path as well and quarantines the device there. It re-runs nothing and changes no
scenario's verdict: a failed scenario stays failed, and the signal decides only whether the *device*
goes on serving the scenarios that follow.

## Motivation

A degraded device does not always announce itself by crashing. The `actuation (xcuitest)` job of pull
request [#1556](https://github.com/bajutsu-e2e/bajutsu/pull/1556) recorded the full sequence. Its
first scenario hit a wedged `GET /screenshot` twice, and the channel's fast-fail plus the pipeline's
respawn worked exactly as designed. The respawned attempt then ran normally and failed on a wait:

```
step 1 (for_each): step 3 (wait): wait timeout: for {'id': ['horse.title', 'horse_title']} (20.0s)
```

Twenty seconds is a long time for a push transition that takes well under a second on a healthy host.
The same job's log carries the reason, once per scenario, for all ten of them:

```
recordVideo produced no new bytes in runs/.../scenario.mp4 within 20.0s
```

That warning is the video-start confirmation the evidence layer already logs;
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
surfaced it on the lease as the stall signal precisely to name this degradation class — the capture
services are not producing, so the device is not painting either. It was standing when the retry failed, and the retry's failure was not
a crash, so nothing read it. The device was never replaced. Scenarios two through ten ran on it.

Two costs follow, and this proposal treats them differently. The first is that every later scenario
inherits a device already known to be degraded, which is what turns one bad device into a job-long
run of unreliable results. That is the cost this proposal fixes. The second is that the run's own
verdict blames the scenario: a `wait` timeout on `horse.title` reads as a defect in the app or the
scenario, while the evidence that the device was not painting sits in the same log with nothing
connecting the two. This proposal improves that only by recording the connection, deliberately —
turning a red verdict green on the strength of an infrastructure signal is the one thing it must not
do (see *Prime directives preserved*).

## Detailed design

### The signal, read where it is currently dropped

`Lease.video_start_stalled()` (`bajutsu/runner/pool.py:290`) already reports, per lease, whether this
lease's video recording ever confirmed it started. `run_one` reads it in exactly one place, inside
the crash handler, where it selects the replacement rung over the forced erase
(`bajutsu/runner/pipeline.py:484`). The ordinary path — `return self._run_on_lease(lz, handler, i, s,
sid)` (`bajutsu/runner/pipeline.py:451`) — returns the scenario's `RunResult` without consulting it.

The change is to consult it there too: capture the result, and when the scenario did **not** pass
while the stall signal stands, ask for the same replacement the crash handler would have asked for,
then return the result unchanged.

The new condition sits behind the same `can_replace` gate the crash handler already computes
(`bajutsu/runner/pipeline.py:360`): the operator's `--no-erase`, the scenario's `reinstall: overwrite`,
and `device_replacement_supported`'s pinned-run and `appPath` exclusions (`bajutsu/backends.py`) apply
unchanged. The caller must hold that gate itself — every exclusion is config-static precisely so the
environment never has to second-guess a request — and its pool-of-one guarantee is what makes a remedy
served on a later lease land on the run's only device.

unchanged. The caller applies that gate itself, because every exclusion is config-static precisely so
the environment never has to second-guess a request. That gate also guarantees a pool of one device,
which is what lands a remedy served on a later lease on the run's only device.
(`bajutsu/runner/types.py:73`, wired to the environment's method in `bajutsu/runner/pool.py:520`), the
mechanism
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
already built. It records the escalation rather than acting on it; the *next* bring-up serves it by
shutting the degraded device down, creating a replacement, and letting the pool re-key everything it
holds by the new udid. Nothing about the failing scenario changes: it keeps the device it ran on, it
keeps its evidence, and it keeps its verdict. What changes is which device the scenarios after it
lease.

That the remedy lands on a later lease is what makes this proposal verdict-neutral by construction
rather than by promise. There is no path from this signal to a re-run, so there is no path from it to
a different pass/fail answer for the scenario that raised it.

### Why a failing verdict is part of the condition

The stall signal alone is not the trigger; the scenario also has to have failed. A scenario that
passed proves the automation path served it, whatever the video did, so replacing the device under it
would spend a device on a run that is working. Requiring both keeps the minting bounded to runs that
are already going wrong — the same restraint
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
applies when it keeps the erase rung first for a one-off crash, and the reason
[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) weighs the
residue a `bajutsu-recovered-*` device leaves on a developer's Mac.

The allowance must stay bounded for the same reason — but BE-0354's per-scenario allowance bounds only
what one scenario can mint, so under it a chronically stalling host still mints one device per failing
scenario. Unit 0 settles whether that per-scenario bound is enough in practice or this path needs a
per-run cap.

### What Unit 0 has to confirm before the rest lands

`_run_on_lease` releases the lease in its own `finally`, so by the time its `RunResult` reaches
`run_one` the lease may already be released, and `request_device_replacement()` sets a flag on the
environment the pool keeps behind it. A post-release request is the timing the `Lease` was built for: the pool documents that both callables
are read by the crash retry after the lease is released, with the warm environment carrying the
request to the next bring-up (`bajutsu/runner/pool.py:516`). What differs on this path is the gap —
the next bring-up belongs to a later scenario, so the eviction windows BE-0354 accepts as a dropped
request (a failed `end_lease`, an actuator switch by the next scenario) have longer to occur. Unit 0
confirms on the lane that the request survives that gap in practice, and settles the allowance
question, before Units 1 and 2 encode either answer.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

0. **Spike.** Confirm on the lane that a replacement requested after `_run_on_lease` has released the
   lease survives the inter-scenario gap to the next bring-up. Decide the
   allowance this path spends against (BE-0354's per-scenario one, or a per-run cap). Record both
   findings in *Detailed design* above. Blocks Units 1 and 2.
1. **The quarantine.** Read `video_start_stalled()` on the ordinary-failure path of `run_one` and
   request the replacement when the scenario did not pass and `can_replace` holds, bounded by Unit 0's
   allowance. No re-run, no verdict change.
2. **The recorded connection.** Surface the standing stall in the failing scenario's own diagnosis,
   so a reader of the result sees why the device is suspect instead of having to correlate two
   unrelated log lines by hand.
3. **Tests.** A pipeline test per branch: a failing scenario with the stall standing requests the
   replacement; a passing scenario with the same signal does not; a failing scenario with no stall
   does not; and the failing scenario's verdict is byte-identical either way — the regression that
   pins verdict-neutrality.
4. **Docs.** [`docs/architecture.md`](../../docs/architecture.md),
   [`docs/run-loop.md`](../../docs/run-loop.md), and their `docs/ja/` mirrors — both pages currently
   describe the stall as selecting the crash retry's rung — stating that the stall signal also
   quarantines a device on an ordinary failure and never re-runs a scenario or alters a verdict.
   Lands in the same change as Unit 1, per the BE-0113 norm.

### Prime directives preserved

- **AI never judges.** The signal is a recorded fact about a video recording, read by deterministic
  code. No model call enters any path that produces or reads it.
- **Determinism first.** This is the directive the design is shaped around. A red verdict never
  becomes green here: nothing is re-run, and the failing scenario's result is returned unchanged. The
  signal decides only which device later scenarios lease, which is a scheduling decision rather than
  a verdict one.
- **App-agnostic.** The condition reads the pipeline's own per-lease signal, with no per-app
  branching. A backend that cannot serve a replacement (every route `device_replacement_supported` excludes —
  today, everything but the Simulator XCUITest one) is unaffected.

## Alternatives considered

- **Re-run the failed scenario on a replacement device.** This is the change that would fix the
  misattributed verdict rather than merely record it, and it is the one this proposal refuses. A
  scenario that failed on its own assertion produced an honest verdict; re-running it because an
  infrastructure signal happened to be standing is a path from red to green that
  [`DESIGN.md`](../../DESIGN.md)'s determinism directive rules out, and it would mask a genuinely
  flaky app exactly where the tool is supposed to expose one. Left out deliberately, not deferred.
- **Quarantine on a standing stall regardless of the scenario's verdict.** Simpler to state, and it
  would catch a device that degrades during a passing scenario and only bites the next one. Rejected
  for the minting cost: a chronically stalling host would replace the device after every scenario,
  and a passing scenario is evidence the automation path still works. Worth revisiting if Unit 0's
  measurements show the stall rarely stands under a passing scenario.
- **Treat the retry's ordinary failure as a crash and let the existing handler escalate.** It would
  reuse the escalation unchanged, with no new call site. Rejected because it corrupts the distinction
  the handler is built on: `BackendCrashError` means the lease is dead and the scenario never got a
  verdict, while a wait timeout means the scenario ran and answered. Collapsing the two would send
  every ordinary failure through crash recovery, re-running scenarios that already failed honestly —
  the first alternative, reached by a different route.
- **Raise the wait timeout so a slow device still passes.** The 20-second wait that failed was
  already generous for a sub-second transition. A larger one buys silence rather than information,
  the same trade
  [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  and [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) both
  measured for their own budgets, and it would hide the degradation this proposal exists to act on.
- **Extend the same quarantine to the Android emulator.** The adb backend confirms its video start
  too (`start_screenrecord` polls for the device-side process, so its lease can report the stall);
  what is missing there is the remedy — an emulator or handset is brought up out of band, so
  `request_device_replacement` is a no-op (`bajutsu/platform_lifecycle/environments/android.py`).
  Unchanged from
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)'s
  own deferral of the emulator-process restart.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 0 — spike: does a post-release replacement request survive the inter-scenario gap, and which
      allowance does this path spend
- [ ] Unit 1 — the quarantine on the ordinary-failure path
- [ ] Unit 2 — the recorded connection in the failing scenario's diagnosis
- [ ] Unit 3 — tests, including the verdict-neutrality regression
- [ ] Unit 4 — docs (`architecture.md`, `run-loop.md`, and their `ja` mirrors)

## References

- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md) —
  the wedge fast-fail and the device-replacement rung whose escalation this proposal reaches from a
  second path
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) —
  the crash-retry device recovery this sits alongside
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — the
  device-creation machinery the replacement reuses, and its weighing of the residue a created device
  leaves behind
- [`bajutsu/runner/pipeline.py`](../../bajutsu/runner/pipeline.py) — `run_one`'s crash-recovery loop,
  its `except BackendCrashError` escalation, and the ordinary return this proposal adds a check to
- [`bajutsu/runner/pool.py`](../../bajutsu/runner/pool.py) — `Lease.video_start_stalled()` and the
  pool's re-keying when an environment follows a replacement
- [`actuation (xcuitest)` on pull request #1556](https://github.com/bajutsu-e2e/bajutsu/actions/runs/31559685322/job/93999696836) —
  the recorded occurrence this proposal is written against

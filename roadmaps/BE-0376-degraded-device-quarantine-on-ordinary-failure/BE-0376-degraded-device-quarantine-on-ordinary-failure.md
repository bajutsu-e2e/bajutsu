**English** · [日本語](BE-0376-degraded-device-quarantine-on-ordinary-failure-ja.md)

# BE-0376 — Quarantine a device whose capture pipeline stalled, even when the scenario failed on its own assertion

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0376](BE-0376-degraded-device-quarantine-on-ordinary-failure.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0376") |
| Topic | Platform support |
| Related | [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) |
<!-- /BE-METADATA -->

## Introduction

The run pipeline can move a scenario off an iOS Simulator whose services have degraded past what an
erase clears. It asks the next bring-up for a replacement device
([BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)).
That escalation lives entirely inside the crash handler, the `except BackendCrashError` arm of
`run_one` (`bajutsu/runner/pipeline.py:452`). Only a scenario whose attempt *crashed* can reach it.

Consider a scenario whose respawned attempt runs to completion and fails on an ordinary assertion.
It never reaches the escalation, even when the signal the escalation reads is standing at that
moment: a video recording that never confirmed it started. The pipeline then releases the degraded
device back to the pool, and every later scenario in the run leases it.

This proposal reads that standing signal on the ordinary-failure path as well, and quarantines the
device there. It re-runs nothing and changes no scenario's verdict. A failed scenario stays failed.
The signal decides whether the *device* goes on serving the scenarios that follow, and nothing else.

## Motivation

A degraded device does not always announce itself by crashing. The `actuation (xcuitest)` job of
pull request [#1556](https://github.com/bajutsu-e2e/bajutsu/pull/1556) recorded the full sequence.
Its first scenario hit a wedged `GET /screenshot` twice. The channel's fast-fail and the
pipeline's respawn worked exactly as designed. The respawned attempt then ran normally and failed
on a wait:

```
step 1 (for_each): step 3 (wait): wait timeout: for {'id': ['horse.title', 'horse_title']} (20.0s)
```

Twenty seconds is a long time for a push transition that takes well under a second on a healthy
host. The same job's log carries the reason, once per scenario, for all ten of them:

```
recordVideo produced no new bytes in runs/.../scenario.mp4 within 20.0s
```

That warning is the video-start confirmation that the evidence layer already logs.
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
surfaced it on the lease as the stall signal, precisely to name this degradation class. The capture
services are not producing, so the device is not painting either. The signal stood throughout, and
the pipeline never replaced the device. Scenarios two through ten ran on it.

Two separate reasons kept the device from being replaced, and this proposal owns only one of them.
That lane runs **pinned**. `ios-e2e.yml` passes the concrete UUID `boot-simulator` minted, and
`.github/actions/bajutsu-e2e` forwards it as `--udid <uuid>`. `device_replacement_supported`
(`bajutsu/backends.py`, which requires `udid_spec == "booted"`) therefore returned `False`, and
`can_replace` was `False` for the whole run.

The first scenario *did* crash twice with the stall standing. The crash handler reached
`lz.video_start_stalled()` at `bajutsu/runner/pipeline.py:484` and still could not escalate. The
pin blocked it there, not the shape of the later failure. Stated plainly: on this recorded lane,
neither BE-0354's rung nor this proposal's new check could have fired, because both sit behind the
same gate.

The occurrence therefore establishes the degradation class and its cost. It does not establish the
gap this item closes. That gap is reachable on an **unpinned** run, `--udid booted`.
`_resolve_lanes` resolves `--udid booted` to a pool of one device served by one worker. A
developer's Mac takes this shape, and BE-0354's own deferred-remedy reasoning depends on it. On
such a run, a crash followed by a respawned attempt that fails on an ordinary assertion leaves the
degraded device in the pool, and nothing has read the standing signal.

The actuation lane could also be made able to benefit. It could run unpinned, or the project could
revisit the `booted` restriction that carries the pool-of-one invariant. This item does not settle
that design question; see *Alternatives considered*.

Two costs follow, and this proposal treats them differently.

- **Every later scenario inherits a device already known to be degraded.** This turns one bad device
  into a job-long run of unreliable results. This proposal fixes this cost.
- **The run's own verdict blames the scenario.** A `wait` timeout on `horse.title` reads as a defect
  in the app or the scenario. The evidence that the device was not painting sits in the same log,
  and nothing connects the two. This proposal improves that cost by recording the connection, and
  deliberately goes no further. Turning a red verdict green on the strength of an infrastructure
  signal is the one thing it must not do (see *Prime directives preserved*).

## Detailed design

### The signal, read where it is currently dropped

`Lease.video_start_stalled()` (`bajutsu/runner/pool.py:290`) already reports, per lease, whether this
lease's video recording ever confirmed it started. `run_one` reads the signal in exactly one place:
inside the crash handler, where it selects the replacement rung over the forced erase
(`bajutsu/runner/pipeline.py:484`). The ordinary path, `return self._run_on_lease(lz, handler, i, s,
sid)` (`bajutsu/runner/pipeline.py:451`), returns the scenario's `RunResult` without consulting it.

The change consults the signal there too. It captures the result. When the scenario did **not** pass
while the stall signal stands, it asks for the same replacement the crash handler would have asked
for, then returns the result unchanged.

The new condition sits behind the same `can_replace` gate the crash handler already computes
(`bajutsu/runner/pipeline.py:360`): the operator's `--no-erase`, the scenario's `reinstall: overwrite`,
and `device_replacement_supported`'s pinned-run and `appPath` exclusions (`bajutsu/backends.py`) apply
unchanged. The caller applies that gate itself, because every exclusion is config-static precisely so
the environment never has to second-guess a request. That gate also guarantees a pool of one device,
which is what lands a remedy served on a later lease on the run's only device.

### Quarantine, not retry

The request goes through the lease's own `request_device_replacement`
(`bajutsu/runner/types.py:73`, wired to the environment's method in `bajutsu/runner/pool.py:520`), the
mechanism
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
already built. It records the escalation rather than acting on it; the *next* bring-up serves it by
shutting the degraded device down, creating a replacement, and letting the pool re-key everything it
holds by the new udid. Nothing about the failing scenario changes: it keeps the device it ran on, it
keeps its evidence, and it keeps its verdict. What changes is which device the scenarios after it
lease.

That the remedy lands on a later lease makes this proposal verdict-neutral by construction, not by
promise. No path leads from this signal to a re-run, so no path leads from it to a different
pass/fail answer for the scenario that raised it.

### Why a failing verdict is part of the condition

The stall signal alone is not the trigger. The scenario also has to have failed. A scenario that
passed proves the automation path served it, whatever the video did. Replacing the device under it
would spend a device on a run that is working. Requiring both conditions bounds the minting to runs
that are already going wrong. Two earlier items show the same restraint:

- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  keeps the erase rung first for a one-off crash.
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) weighs the
  residue a `bajutsu-recovered-*` device leaves on a developer's Mac.

The allowance must stay bounded for the same reason. BE-0354's per-scenario allowance bounds what
one scenario can mint, and no more. Under it, a chronically stalling host still mints one device
per failing scenario. Unit 0 settles two questions:

- whether the per-scenario bound is enough in practice, or this path needs a per-run cap
- whether this call site spends the crash handler's own `replaced` allowance

Sharing the allowance makes the quarantine a no-op once a crash on the same scenario already
escalated. Not sharing it lets one scenario mint two devices.

### What Unit 0 has to confirm before the rest lands

`_run_on_lease` releases the lease in its own `finally`. By the time its `RunResult` reaches
`run_one`, the lease may already be released. `request_device_replacement()` sets a flag on the
environment that the pool keeps behind the lease.

A post-release request is the timing the `Lease` was built for. The pool documents that the crash
retry reads both callables after the lease is released, and that the warm environment carries the
request to the next bring-up (`bajutsu/runner/pool.py:516`). What differs on this path is the gap.
The next bring-up belongs to a later scenario, so the eviction windows that BE-0354 accepts as a
dropped request have longer to occur. Those windows are a failed `end_lease` and an actuator
switch by the next scenario.

Unit 0 confirms on the lane that the request survives that gap in practice. It also settles the
allowance question. Both answers come before Units 1 and 2 encode either one.

### What Unit 0 found

The lane could not answer the survival question. The actuation lane runs pinned, and pinning
disables both this path and BE-0354's rung. A developer's Mac offers no clean substitute. An
unpinned `--udid booted` run there would drive whichever Simulator is already booted, and a
shared machine must not allow that. Unit 0 settled survival by reading the pool code instead, and
tests pin the answer. The modules now live under `bajutsu/common/runner/`, so the paths above name
an older tree.

- **The request survives the gap in the common case.** The XCUITest environment records the
  request on itself. Its next `start` consumes the request. The pool keeps that environment warm per
  device, and a same-actuator lease resumes it rather than building a fresh one. `can_replace`
  implies a pool of one device, so the next scenario's lease serves the request. A pool test pins
  this path.
- **Three windows still drop the request, as BE-0354 accepts.** A teardown error in `end_lease`
  evicts the warm environment, and a second pool test pins that window. A next scenario that
  resolves to another actuator tears the warm environment down. The run's last scenario leaves no
  bring-up to serve the request, and no later scenario wants one.
- **The allowance is shared, and capped per run.** The quarantine reads the per-scenario `replaced`
  flag that the crash retry sets. Suppose a crash retry already escalated in this scenario. Its
  final attempt then ran on the fresh device, so the stalled device is the new one. On top of
  that, the run spends at most one quarantine. A replacement that stalls again points at the
  machine, and one more device would not fix the machine.

The recorded connection (Unit 2) is a new `RunResult.device_suspect` field. Rewriting the reason
text instead would change the verdict, and the new field keeps the verdict byte-identical. The
field also records a stall on a run that cannot replace the device. The pinned lane that motivated
this item is one such run.

### Work breakdown (`MECE`)

Mutually Exclusive, Collectively Exhaustive (`MECE`) units of work follow.

0. **Spike.** Confirm on the lane that a replacement requested after `_run_on_lease` has released the
   lease survives the inter-scenario gap to the next bring-up. Decide the
   allowance this path spends against (BE-0354's per-scenario one, or a per-run cap). Record both
   findings in *Detailed design* above. Blocks Units 1 and 2.
1. **The quarantine.** Read `video_start_stalled()` on the ordinary-failure path of `run_one` and
   request the replacement when the scenario did not pass and `can_replace` holds, bounded by Unit 0's
   allowance. No re-run, no verdict change.
2. **The recorded connection.** Surface the standing stall in the failing scenario's own diagnosis.
   A reader of the result then sees why the device is suspect, without correlating two unrelated
   log lines by hand.
3. **Tests.** Write a pipeline test per branch:
   - A failing scenario with the stall standing requests the replacement.
   - A passing scenario with the same signal does not.
   - A failing scenario with no stall does not.
   - The failing scenario's verdict is byte-identical either way. This regression pins
     verdict-neutrality.
4. **Docs.** Update [`docs/architecture.md`](../../docs/architecture.md),
   [`docs/run-loop.md`](../../docs/run-loop.md), [`docs/reporting.md`](../../docs/reporting.md),
   and their `docs/ja/` mirrors. The first two pages
   describe the stall as selecting the crash retry's rung. The update states that the stall signal
   also quarantines a device on an ordinary failure, and never re-runs a scenario or alters a
   verdict. It lands in the same change as Unit 1, per the BE-0113 norm.

### Prime directives preserved

- **AI never judges.** The signal is a recorded fact about a video recording, read by deterministic
  code. No model call enters any path that produces or reads it.
- **Determinism first.** The design is shaped around this directive. A red verdict never becomes
  green here: nothing is re-run, and the failing scenario's result is returned unchanged. The
  signal decides which device later scenarios lease. That is a scheduling decision, not a verdict
  decision.
- **App-agnostic.** The condition reads the pipeline's own per-lease signal, with no per-app
  branching. A backend that cannot serve a replacement is unaffected. That covers every route
  `device_replacement_supported` excludes, which today is everything but the Simulator XCUITest
  one.

## Alternatives considered

- **Re-run the failed scenario on a replacement device.** This change would fix the misattributed
  verdict rather than merely record it, and this proposal refuses it. A scenario that failed on its
  own assertion produced an honest verdict. Re-running it because an infrastructure signal happened
  to be standing is a path from red to green. [`DESIGN.md`](../../DESIGN.md)'s determinism
  directive rules that path out. The re-run would also mask a genuinely flaky app exactly where the
  tool is supposed to expose one. The proposal leaves it out deliberately, not as a deferral.
- **Quarantine on a standing stall regardless of the scenario's verdict.** This is simpler to
  state, and it would catch a device that degrades during a passing scenario and only bites the
  next one. Rejected for the minting cost. A chronically stalling host would replace the device
  after every scenario, and a passing scenario is evidence that the automation path still works.
  Worth revisiting if Unit 0's measurements show the stall rarely stands under a passing scenario.
- **Treat the retry's ordinary failure as a crash and let the existing handler escalate.** This
  would reuse the escalation unchanged, with no new call site. Rejected because it corrupts the
  distinction the handler is built on. `BackendCrashError` means the lease is dead and the scenario
  never got a verdict. A wait timeout means the scenario ran and answered. Collapsing the two would
  send every ordinary failure through crash recovery and re-run scenarios that already failed
  honestly. That is the first alternative, reached by a different route.
- **Raise the wait timeout so a slow device still passes.** The 20-second wait that failed was
  already generous for a sub-second transition. A larger one buys silence rather than information.
  [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  and [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) both
  measured the same trade for their own budgets. A larger wait would also hide the degradation this
  proposal exists to act on.
- **Unpin the actuation lane, or widen `device_replacement_supported` past `booted`, so CI can benefit.**
  Neither this proposal's check nor BE-0354's own rung can fire on a pinned run, and every
  `ios-e2e.yml` job is pinned today. The lane that surfaced this degradation is also the lane that
  neither mechanism can help. The question stays open here because both routes are load-bearing:
  - `--udid` names a device the operator meant.
  - The `booted` restriction guarantees the pool of one device. That guarantee makes a remedy served
    on a *later* lease land on the device that asked for it (`bajutsu/backends.py`).

  Widening the restriction would need the request routed through the pool instead. This needs its
  own item.
- **Extend the same quarantine to the Android emulator.** The adb backend confirms its video start
  too, because `start_screenrecord` polls for the device-side process. Its lease can therefore
  report the stall. The remedy is what is missing there. An emulator or handset is brought up out of
  band, so `request_device_replacement` is a no-op
  (`bajutsu/platform_lifecycle/environments/android.py`). This is unchanged from
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)'s
  own deferral of the emulator-process restart.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 0 — spike: does a post-release replacement request survive the inter-scenario gap, and which
      allowance does this path spend
- [x] Unit 1 — the quarantine on the ordinary-failure path
- [x] Unit 2 — the recorded connection in the failing scenario's diagnosis
- [x] Unit 3 — tests, including the verdict-neutrality regression
- [x] Unit 4 — docs (`architecture.md`, `run-loop.md`, `reporting.md`, and their `ja` mirrors)

Log:

- 2026-11-28 (PR #PRNUM) — Shipped Units 0–4 in one change. `run_one` now reads the stall signal
  when a scenario ends without passing. It requests a replacement behind the same gate as the
  retry rung. The request shares the per-scenario allowance and spends a new allowance of one per
  run. The new `device_suspect` field records the stall in three places: the manifest, the CTRF
  export, and the HTML Environment panel. The manifest's schema version is now 20. Unit 0 settled
  survival in code and in pool tests, because the pinned lane cannot run this path.

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

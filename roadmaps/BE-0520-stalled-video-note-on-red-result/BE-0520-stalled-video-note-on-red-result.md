**English** · [日本語](BE-0520-stalled-video-note-on-red-result-ja.md)

# BE-0520 — Record a stalled video start on the result of a scenario that did not pass

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0520](BE-0520-stalled-video-note-on-red-result.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0520") |
| Implementing PR | [#2619](https://github.com/bajutsu-e2e/bajutsu/pull/2619) |
| Topic | Platform support |
| Related | [BE-0376](../BE-0376-degraded-device-quarantine-on-ordinary-failure/BE-0376-degraded-device-quarantine-on-ordinary-failure.md), [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) |
<!-- /BE-METADATA -->

## Introduction

A scenario can hit a wait timeout because the device stopped painting. The app itself may be fine.
The run already records a sign of that state. Each lease notes whether its video recording ever
confirmed it started. We call that fact the *stall signal*.

[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
added the stall signal. The respawn retry reads it to pick a remedy once the backend stops
answering.

The scenario's own result never shows the stall signal. A reader of a red result sees the wait
timeout and nothing else. The video warning sits on a separate log line, and nothing on the result links the two.

This proposal records the stall signal on the result of every scenario that did not pass. The
record is evidence and never a verdict. Nothing runs again, and no pass or fail answer changes.

This item splits off from [BE-0376](../BE-0376-degraded-device-quarantine-on-ordinary-failure/BE-0376-degraded-device-quarantine-on-ordinary-failure.md). That item also proposed two steps after
such a result. It would quarantine the device and ask for a replacement device. The project rejected the quarantine, and
*Alternatives considered* gives the reasons.

## Motivation

The `actuation (xcuitest)` job of pull request
[#1556](https://github.com/bajutsu-e2e/bajutsu/pull/1556) shows the cost. Its first scenario hit a
wedged `GET /screenshot` twice. The pipeline respawned the backend. The second attempt then ran to
the end and reported a wait timeout:

```
step 1 (for_each): step 3 (wait): wait timeout: for {'id': ['horse.title', 'horse_title']} (20.0s)
```

On a healthy machine, that push transition takes well under a second. The same job's log gives the
reason once per scenario, for all ten of them:

```
recordVideo produced no new bytes in runs/.../scenario.mp4 within 20.0s
```

The capture services produced no output, so the device was not painting either. Yet the result
names `horse.title` and points at the app or the scenario. The same stall already feeds the stall
diagnostics of [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md).
The workflow `ios-e2e.yml` arms them for every job, through the `BAJUTSU_STALL_DIAGNOSTICS`
variable. That capture
names no scenario, and it stops after two captures per reason in one process. In the ten-scenario
job, the later stalls leave no capture at all. A reader still has to find the video warning in the
job log and match it to the scenario by hand.

The stall signal does not prove a bad device. On these CI runners the video warning also appears
under scenarios that pass. A note on the result states a fact for the reader to weigh. The note
claims no cause, and it has no effect on the verdict.

After this change, a reader can see the stall from the result alone. A red scenario whose lease saw
a stalled video start carries a note that says so. The note appears in the run's manifest and in
its report, next to the step that did not pass.

## Detailed design

### Where the signal is read today

The method `Lease.video_start_stalled()` reports the stall signal for one lease. The pool in
`bajutsu/common/runner/pool.py` keeps the flag local to each lease. The value stays valid after the
lease goes back to the pool.

The function `run_one` in `bajutsu/common/runner/pipeline.py` reads the flag in one place. That
place is its `except BackendCrashError` arm. There the flag picks a forced erase or a replacement
device. The ordinary return of `_run_on_lease` hands back the `RunResult` and never reads the flag.

### The note on the result

The function `run_one` reads the flag before it returns a result. When the result did not pass and
the flag stands, `run_one` adds one note to the result. The note says the video never confirmed its
start. The device may not have been painting.

The check covers both ways a scenario can end red:

- **A red result from `_run_on_lease`**. The check reads the lease the scenario ran on.
- **A respawn loop that gave up after its last attempt**. The check reads the last lease that
  stopped answering.

A passing result gets no note. The signal also stands under passing scenarios, so a note there would
add noise and explain nothing.

The note lives in a field of `RunResult` that the verdict never reads. The implementing change
chooses between two candidates and states why:

- **Reuse `channel_notes`**. This choice adds no field. That list describes the driver's read
  channel, which is a different subject.
- **Add a new field**. This choice names the meaning plainly. The manifest writer and the report
  loader then both need to learn the field.

Either way, the manifest and the report carry the note. The report shows the note beside the red
step.

BE-0361's stall diagnostics may already hold a capture of the same stall. The capture sits in the
directory that the `BAJUTSU_STALL_DIAGNOSTICS` variable names. The implementing change decides
whether the note points there when a job sets it. The capture can be missing even then, because of the cap
per reason.

### Prime directives preserved

- **AI never judges**. Deterministic code reads a recorded fact about a video recording. No model
  call enters any path that produces or reads the note.
- **Determinism first**. The note changes no verdict and runs nothing again. The fields `ok` and
  `failure` stay byte-identical with and without the note, and a test pins that property.
- **App-agnostic**. The check reads the pipeline's own per-lease signal, with no per-app branch. A
  backend whose lease never reports a stall never gets the note.

### Work breakdown

The units of work below are Mutually Exclusive and Collectively Exhaustive (MECE).

1. **The note**. Read `video_start_stalled()` in `run_one` on both red exits. Add the note to the
   result, choose its field, and carry it through the manifest and the report.
2. **Tests**. Write a pipeline test per branch:
   - A red scenario with the signal standing carries the note.
   - A passing scenario with the signal standing carries none.
   - A red scenario with no signal carries none.
   - The fields `ok` and `failure` stay byte-identical with and without the note.
3. **Docs**. Update [`docs/run-loop.md`](../../docs/run-loop.md) and its `docs/ja/` mirror. That
   page now presents the stall signal as an input to the respawn retry alone. The update adds the
   note on a red result. Per the BE-0113 norm, it lands in the same change as unit 1.

## Alternatives considered

- **Quarantine the device after a red result, and ask for a replacement**. [BE-0376](../BE-0376-degraded-device-quarantine-on-ordinary-failure/BE-0376-degraded-device-quarantine-on-ordinary-failure.md) proposed that
  scope. The pipeline would call `request_device_replacement()` when a red scenario's lease saw a
  stall. The project rejected that scope for three reasons:
  - **CI never benefits**. Every `ios-e2e.yml` job passes a concrete `--udid`. The function
    `device_replacement_supported` requires `--udid booted`, so a pinned run never replaces a
    device. The job behind #1556 is a pinned run.
  - **The reach is small**. A `--udid booted` run uses one device on one worker. A developer's Mac
    has that shape. There, the developer sees the stuck device and can reboot it.
  - **The cost is real**. A spike would have to show that a replacement request lasts until the
    next scenario. The design would also need a cap on the devices one run may create.

  The question of CI reach stays open. The lane could run unpinned. The project could also widen
  `device_replacement_supported` past `booted`, which would route the request through the pool. Each
  route needs its own item, and a quarantine proposal could return once one of them lands.
- **Run the red scenario again on a replacement device**. This would turn red into green on an
  infrastructure signal. The determinism directive in [`DESIGN.md`](../../DESIGN.md) rules that
  out. A second run would also hide a flaky app at the point where the tool should expose one.
- **Raise the wait timeout so a slow device still passes**. A 20-second wait already covers a
  sub-second transition with room to spare. A longer wait buys silence, not information. It would
  also hide the degradation this note exists to show.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — the note on a red result, through the manifest and the report
- [x] Unit 2 — tests, including the verdict-neutrality regression
- [x] Unit 3 — docs (`run-loop.md` and its `ja` mirror)

Log:

- [#2619](https://github.com/bajutsu-e2e/bajutsu/pull/2619): all three units landed together.
  - The note lives in a new field, `RunResult.device_notes` (manifest `schemaVersion` 20). We did
    not reuse `channel_notes`, because that list describes the driver's read channel.
  - The report shows the note under the first step that did not pass. Without a red step row, the
    note appears above the step table.
  - The note does not point at BE-0361's stall capture. The cap per reason means the capture can be
    missing, so a pointer could name an absent file.

## References

- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md):
  the stall signal on the lease, and the respawn retry that reads it
- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md):
  the in-process stall capture that the same trigger already feeds on iOS
- [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md):
  the matching stall capture on Android
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md):
  device recovery during the respawn retry
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md): the
  device-creation code a replacement would reuse
- [`bajutsu/common/runner/pipeline.py`](../../bajutsu/common/runner/pipeline.py): `run_one`, where
  the check goes
- [`bajutsu/common/stall_diagnostics.py`](../../bajutsu/common/stall_diagnostics.py): the stall
  capture and its cap of two captures per reason
- [`bajutsu/common/runner/pool.py`](../../bajutsu/common/runner/pool.py):
  `Lease.video_start_stalled()` and its lease-local flag
- [`run_result.py`](../../bajutsu/common/orchestrator/types/run_result.py): `RunResult`, which
  carries the note
- [The `actuation (xcuitest)` job](https://github.com/bajutsu-e2e/bajutsu/actions/runs/31559685322/job/93999696836)
  of pull request #1556: the recorded occurrence

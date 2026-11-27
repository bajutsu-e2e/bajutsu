**English** · [日本語](BE-0376-degraded-device-quarantine-on-ordinary-failure-ja.md)

# BE-0376 — Quarantine a device whose capture pipeline stalled, even when the scenario ended red on its own assertion

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0376](BE-0376-degraded-device-quarantine-on-ordinary-failure.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Rejected** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0376") |
| Topic | Platform support |
| Related | [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) |
<!-- /BE-METADATA -->

## Introduction

> **Rejected — the quarantine can never fire in CI, and its local reach does not repay its cost.**
> Every `ios-e2e.yml` job pins a concrete `--udid`, and the replacement rung requires `--udid
> booted`. Rejected fits better than Deferred, because no planned change makes the design fire. A
> CI benefit needs a different design that routes the request through the pool. The useful part
> of this item moves to a separate item. That item records the stall signal on a red result.

A scenario can hit a wait timeout because the device stopped painting. Each lease records whether
its video recording ever confirmed it started. We call that fact the *stall signal*.
[BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
added the stall signal. The respawn retry reads it before asking for a new device.

This proposal would read the same signal after a scenario ended red on its own assertion. When the
signal stood, the pipeline would ask for a new device for the next scenarios. The
red verdict would not change.

## Motivation

The `actuation (xcuitest)` job of pull request
[#1556](https://github.com/bajutsu-e2e/bajutsu/pull/1556) prompted this item. Its first scenario hit
a wedged `GET /screenshot` twice. The respawned attempt then reported a wait timeout. The job's log
showed a stalled video start for all ten scenarios. Every scenario after the first ran on the
same device.

The same job also shows the limit of this proposal. That lane runs pinned. The function
`device_replacement_supported` returns false for a pinned run. Neither this proposal nor the rung
from BE-0354 could have replaced the device in that job.

## Detailed design

The design had four parts:

1. After `_run_on_lease` returned a red result, `run_one` would read `video_start_stalled()`.
2. When the signal stood and `can_replace` held, `run_one` would call
   `request_device_replacement()`. The next lease would then start on a fresh device.
3. A spike would confirm that the request lasts until the next scenario. The spike would also
   pick a cap on the devices one run may create.
4. Tests would pin that the red verdict stays byte-identical.

The project rejected the design for three reasons:

- **CI never benefits**. Every `ios-e2e.yml` job passes a concrete `--udid`. The function
  `device_replacement_supported` requires `--udid booted`, so a pinned run never replaces a device.
- **The reach is small**. A `--udid booted` run uses one device on one worker. A developer's Mac
  has that shape. There, the developer sees the stuck device and can reboot it.
- **The cost is real**. The spike and the device cap add work and risk for that small reach.

## Alternatives considered

- **Record the stall signal on the red result, without the quarantine**. A separate item keeps
  this part. A note on the result helps every run, pinned CI jobs included.
  The note changes no verdict.
- **Unpin the CI lane, or widen `device_replacement_supported` past `booted`**. Either route would
  let a replacement reach CI. A wider pool would need the request routed through the pool. Each
  route needs its own item, and a quarantine proposal could return once one of them lands.
- **Run the red scenario again on a replacement device**. This would turn red into green on an
  infrastructure signal. The determinism directive in [`DESIGN.md`](../../DESIGN.md) rules that
  out.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

Rejected before any work began.

## References

- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md):
  the stall signal on the lease, and the respawn retry that reads it
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md):
  device recovery during the respawn retry
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md): the
  device-creation code a replacement would reuse
- [The `actuation (xcuitest)` job](https://github.com/bajutsu-e2e/bajutsu/actions/runs/31559685322/job/93999696836)
  of pull request #1556: the recorded occurrence

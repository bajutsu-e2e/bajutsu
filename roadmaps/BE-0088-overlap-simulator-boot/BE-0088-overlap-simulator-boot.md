**English** · [日本語](BE-0088-overlap-simulator-boot-ja.md)

# BE-0088 — Overlap the Simulator boot with the build

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0088](BE-0088-overlap-simulator-boot.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0088") |
| Implementing PR | [#303](https://github.com/bajutsu-e2e/bajutsu/pull/303) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This item takes the iOS Simulator boot off the critical path of the on-device E2E jobs. The boot
starts asynchronously and runs concurrently with the app build. The boot itself does not get
faster. Its ~80s moves into time that the build already spends, so the boot no longer adds
wall-clock time.

## Motivation

On a fresh GitHub macOS runner, the first boot of a Simulator costs ~80s because of data migration
on first boot. Each runner is ephemeral, so no warm-boot benefit exists. A recent run measured the
on-device jobs spending the boot strictly *after* the build, even though the two are independent:

- **smoke**: Build 36s → Boot 81s → Install 13s → Run 218s (job ~384s)
- **xcuitest**: Boot 85s → Codegen+xcodebuild 212s (job ~335s)

The boot does not depend on the build, so running the two back to back wastes the overlap.

`simctl boot` merely asks CoreSimulator (a launchd service) to boot the device and returns
immediately. The boot then proceeds independently of the step's shell. We can therefore start the
boot first and block on "booted" just before the app is installed or run.

Determinism is preserved. We still wait for the booted state before any actuation, so nothing ever
touches a device that is not yet ready.

## Detailed design

- **`boot-simulator` action becomes start-only.** It selects a device — preferring one that is
  *already booted* (zero boot cost), then a pre-installed available iPhone, then a created one —
  issues `simctl boot` (async), and returns the UDID without waiting for `bootstatus`.
- **smoke** starts the boot before `make sample-build`, so the ~80s boot overlaps the 36s build.
  A dedicated `simctl bootstatus` step then blocks for the residual before installing the app.
- **xcuitest** starts the boot before `make ui-test`. No explicit wait is added, because
  `xcodebuild test` blocks until the destination is ready when it reaches the test phase. By then
  the boot, overlapped with the codegen build, has finished.

The net effect on the E2E gate is ~36s off smoke's critical path, because the gate's wall-clock is
the longer job, smoke. The xcuitest job sheds up to ~85s on its own, which improves that job's
feedback time even though smoke remains the gate's long pole. The dominant smoke cost (Run
scenarios, ~218s of idb actuation) is unaffected. That cost is
[BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md)'s territory, not boot.

## Alternatives considered

- **Cache the CoreSimulator device data dir to get a warm boot.** Rejected. The device UDID is
  embedded in the cached paths, the runtime version must match exactly, and the data dir is large
  and holds absolute paths. A stale restore also risks build and boot failures. The option takes
  high effort and is fragile. It belongs to the same class of "cache that never reliably hits" that
  we just removed for DerivedData.
- **Pick a lighter device type to boot faster.** The gain is marginal and unpredictable, because
  the first-boot data migration dominates regardless of device type.
- **Skip the boot wait and let actuation retry.** Rejected, because acting on a device that is not
  yet ready breaks determinism, which the prime directives forbid.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md): idb action
  timing robustness (the Run-scenarios cost, separate from boot).
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md):
  determinism / flakiness audit.

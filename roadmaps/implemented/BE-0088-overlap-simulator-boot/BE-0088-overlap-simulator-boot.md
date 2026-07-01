**English** · [日本語](BE-0088-overlap-simulator-boot-ja.md)

# BE-0088 — Overlap the Simulator boot with the build

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0088](BE-0088-overlap-simulator-boot.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Implementing PR | [#303](https://github.com/bajutsu-e2e/bajutsu/pull/303) |
| Topic | On-device validation (M1 close-out) |
<!-- /BE-METADATA -->

## Introduction

Take the iOS Simulator boot off the critical path of the on-device E2E jobs by starting it
asynchronously and letting it run concurrently with the app build. The boot itself is not made
faster; it is moved so its ~80s no longer adds wall-clock time the build was already spending.

## Motivation

On a fresh GitHub macOS runner the first boot of a Simulator costs ~80s (data migration on first
boot), and there is no warm-boot benefit because each runner is ephemeral. Measured on a recent
run, the on-device jobs spent boot strictly *after* the build, even though the two are independent:

- **smoke**: Build 36s → Boot 81s → Install 13s → Run 218s (job ~384s)
- **xcuitest**: Boot 85s → Codegen+xcodebuild 212s (job ~335s)

The boot does not depend on the build, so running them back to back wastes the overlap. Because
`simctl boot` only asks CoreSimulator (a launchd service) to boot the device and returns
immediately — the boot then proceeds independently of the step's shell — we can start the boot
first and block on "booted" only right before the app is installed/run. Determinism is preserved:
we still wait for the booted state before any actuation, so nothing ever touches a not-yet-ready
device.

## Detailed design

- **`boot-simulator` action becomes start-only.** It selects a device — preferring one that is
  *already booted* (zero boot cost), then a pre-installed available iPhone, then a created one —
  issues `simctl boot` (async), and returns the UDID without waiting for `bootstatus`.
- **smoke** starts the boot before `make sample-build`, so the ~80s boot overlaps the 36s build,
  then a dedicated `simctl bootstatus` step blocks for the residual before installing the app.
- **xcuitest** starts the boot before `make ui-test`; no explicit wait is added because
  `xcodebuild test` blocks on the destination becoming ready when it reaches the test phase, by
  which point the boot — overlapped with the codegen build — has finished.

Net effect on the E2E gate (its wall-clock is the longer job, smoke): ~36s off smoke's critical
path. The xcuitest job sheds up to ~85s on its own, improving that job's feedback time even though
smoke remains the gate's long pole. The dominant smoke cost (Run scenarios, ~218s of idb
actuation) is unaffected — that is [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md)'s
territory, not boot.

## Alternatives considered

- **Cache the CoreSimulator device data dir to get a warm boot.** Rejected: the device UDID is
  embedded in the cached paths, the runtime version must match exactly, the data dir is large and
  holds absolute paths, and a stale restore risks build/boot failures — high effort, fragile, and
  the same class of "cache that never reliably hits" we just removed for DerivedData.
- **Pick a lighter device type to boot faster.** Marginal and unpredictable; the first-boot data
  migration dominates regardless of device type.
- **Skip the boot wait and let actuation retry.** Rejected — it breaks determinism (acting on a
  not-yet-ready device), which the prime directives forbid.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [BE-0087](../BE-0087-idb-action-settle/BE-0087-idb-action-settle.md) — idb action
  timing robustness (the Run-scenarios cost, separate from boot).
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) —
  determinism / flakiness audit.

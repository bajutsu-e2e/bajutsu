**English** · [日本語](BE-0359-xcuitest-boot-completion-wait-ja.md)

# BE-0359 — Wait for the Simulator to finish booting before installing and launching the app

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0359](BE-0359-xcuitest-boot-completion-wait.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0359") |
| Implementing PR | [#1599](https://github.com/bajutsu-e2e/bajutsu/pull/1599) |
| Topic | Platform support |
| Related | [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's Simulator preparation asks for a boot but does not wait for it to finish. This item adds the wait in the two places that boot a device and then use it. On this project's own hardware, the gap on a first boot measured **39.4 seconds**.

Before Bajutsu can drive an iOS Simulator, the device must be booted far enough that SpringBoard accepts a launch request. SpringBoard is the iOS process that owns the home screen and launches apps. The Simulator control tool `simctl` separates asking from waiting:

- `simctl boot` returns as soon as the boot has been *requested*.
- `simctl bootstatus` blocks until the boot has actually *finished*.

Today the Simulator preparation calls `boot`, then installs the app and launches the test runner. The device may still be starting up at that point. This item inserts the wait in two places:

- the shared device preparation
- the system-locale pin, which reboots a device to make the new locale take effect

Continuous integration (CI) does not pay that gap on its first bring-up, and this item does not claim it does. Every iOS end-to-end job runs its own `bootstatus` step before the run step. That step is [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md)'s design working as intended. The unwaited window therefore appears in two places:

- a developer's Mac, on every run
- CI, in the erase-carrying retry, which boots again long after that step has returned

## Motivation

Two of the four places that boot a device already wait. That makes the other two look like an oversight rather than a decision.

The recovery ladder from [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) reboots a degraded Simulator and replaces a vanished one. Both rungs run `bootstatus` before handing the device on. The shared preparation that those rungs then call does not wait, and neither does the locale pin's own reboot. Across the whole XCUITest lifecycle module, `bootstatus` appears at exactly two call sites, both inside that ladder.

The window is not theoretical. Measured on a freshly created iPhone 17 device running iOS 26.5 under Xcode 26.5, `simctl boot` returned in **0.56 seconds**. `bootstatus` did not complete until **39.94 seconds**. During those 39.4 seconds, the preparation goes on to install the app, apply permission grants, and start `xcodebuild`.

A CI runner is slower still, and the project already knows it. The comment that documents the crash-recovery budget in the iOS end-to-end workflow sizes the unbounded preparation at roughly 80 to 150 seconds. It cites one job's own 80-second Simulator-boot estimate. That figure is a wall-clock allowance for the boot, not a synchronisation with it.

Launching an app into a half-booted SpringBoard produces a recognisable failure. After about 40 seconds, XCUITest reports `Failed to launch <bundle id>: Timed out attempting to launch app`. That duration matches the measured window closely enough to name the connection. The signature was the dominant iOS flake through early August 2026. It drove the entire recovery ladder of [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), which repairs the device *after* an attempt fails on it.

The signature is absent from CI at the time of writing, and this item claims no current CI failure. The claim is that the exposure grew while the symptom receded:

- CI passes no flag that suppresses the erase precondition.
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) now forces that precondition on every crash-triggered retry.
- The retry therefore takes the `shutdown` → `erase` → `boot` path.
- A freshly erased device boots from a genuine first-boot state, the slowest case measured above.
- The retry then proceeds into the unwaited window.

The longest form of the gap now sits on the path taken right after the failure that a retry exists to absorb.

A developer's own Mac is exposed more directly, through the locale pin. The pin exists so that SpringBoard renders its permission-prompt buttons in a predictable language ([BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)). The pin reboots the device because a running SpringBoard does not pick up a global-domain write.

A newly created Simulator inherits the host's own language settings. On a host whose region is not the United States, the inherited value fails the pin's match test, and the pin fires. On a Japanese-region host, a fresh device seeded two language entries and the matching locale. A two-entry list fails the pin's "exactly one entry" test. The pin therefore fires, reboots, and returns into the unwaited window on every fresh device.

Today's logs cannot say whether the pin fires on GitHub's own runners, because the pin writes nothing when it fires. That silence is worth fixing, and the fix is cheap. One log line turns an open question into a fact that the next failing job answers on its own.

## Detailed design

The work breaks into three units. Unit 1 is independent. Units 2 and 3 depend on unit 1 only because they use the helper it names, so all three can land together.

1. **Name the wait once.** The argv builder is already shared and named: `bootstatus_cmd`. The web UI's own boot wait builds from it too. This unit therefore adds no second builder, and the `-b` flag stays in exactly one place.

   The two lifecycle call sites duplicate the pair of steps around that builder: running it, and converting a `simctl` failure into the module's device-fault type. Give the Simulator environment a small private helper that performs both for the device it holds. Route the two existing call sites through it, so the new call sites below do not each restate the pair.

   Put the helper on the environment class rather than in the `simctl` module, and accept the cost. `Env.boot()` suppresses its own failure, so any future `Env.boot()` caller outside this class stays outside the wait. The two existing call sites already live within that boundary. Narrowing it would mean changing `Env.boot()`'s contract, which this item does not open.

2. **Wait after the preparation's own boot.** In the shared device preparation, follow the `boot` call with the wait from unit 1. Place it before the device type is recorded and before the locale pin runs. A cold bring-up and the recovery ladder's re-preparation pass both go through this one place, so one insertion covers every caller.

   The wait is unbounded, exactly as the preparation's install and permission steps already are. A device that takes 80 seconds to come up has not failed. The run-level and job-level ceilings above this layer bound a device that never comes up.

3. **Wait after the locale pin's reboot, and say when the pin fires.** The pin shuts the device down and boots it again to make a global-domain write take effect. It then reads the value back to confirm it. Insert the wait between that boot and the read-back. The confirmation then reads a device that finished starting, and the caller returns into a ready device rather than a starting one.

   The wait alone does not establish that the reboot happened, so pair it with the same read-back that the recovery ladder's reboot rung already performs. Three facts make the pairing necessary:

   - `Env.shutdown()` and `Env.boot()` both suppress their own failure.
   - `bootstatus -b` returns at once on a device that never left `Booted`.
   - The plist read-back reads the value the write already changed, not what SpringBoard loaded, so it cannot tell the two cases apart.

   On a CoreSimulator wedged enough to refuse `shutdown`, the pin would otherwise return a "ready" device and record a confirmed pin while SpringBoard still renders the old language. That defeats the determinism [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md) exists for. Read the device's booted state back after `shutdown`, three-valued as the ladder's rung reads it. Treat a device that did not go down as a pin that was not confirmed.

   Separately, log one informational line when the pin decides to write, naming the locale it is pinning. The pin is silent today except when the read-back fails. That silence is why nobody can tell from a CI log whether the pin fired at all. The answer decides how much of unit 2's exposure applies to CI rather than only to a developer's Mac.

A reviewer can reproduce the measurements this item rests on with two commands and a stopwatch: `simctl boot` followed by `bootstatus -b` on a freshly created device. The window can be confirmed without a CI run. The existing lifecycle tests cover the behavioural change. They already assert the ordering of `simctl` calls in the preparation and in the recovery ladder, and each new wait adds one expected call to those sequences.

## Alternatives considered

- **Bound the wait with a timeout and fail the run when it expires.** This would turn a hung boot into a named failure rather than a job-level timeout. Rejected for this item, because `simctl`'s own subprocess calls carry no timeout at all. Bounding this wait alone would address one call among many while implying the rest are bounded. Bounding every `simctl` call is a separate, larger change with its own choice of timeout and failure mode. It belongs in its own item.
- **Poll SpringBoard for readiness instead of calling `bootstatus`.** A readiness probe would answer the question the launch cares about, rather than the one the boot reports. Rejected as redundant. `bootstatus` already waits on the device's own boot completion, and Apple provides it for that purpose. The recovery ladder has used it since [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) without needing a finer signal.
- **Wait only when the preconditions carry an erase.** An erased device is the slowest case, so the wait would go where the window is widest and leave the common path untouched. Rejected because the window is not exclusive to an erase. A device booting for the first time in a job is equally affected. A conditional wait would also keep the asymmetry this item exists to remove, with one more condition to explain.
- **Log the pin's firing and measure before inserting any wait.** This is the cheapest first step, and it is why unit 3's log line is worth having on its own. Rejected as the whole item. The measurement already establishes the window on a developer's Mac, where the pin demonstrably fires. Waiting for CI evidence would withhold a fix from the case that is already proven.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — add a private helper on the Simulator environment that runs the shared `bootstatus`
      builder and converts a failure, and route the two existing call sites through it.
      `XcuitestEnvironment._await_boot` in
      `bajutsu/platform_lifecycle/environments/xcuitest.py`; both call sites shed their own
      `try` / `except subprocess.CalledProcessError` with the conversion, since the `bootstatus` run
      was the only step inside either that could raise it (`Env.shutdown()`, `Env.boot()`, and
      `device_booted` all absorb their own failure).
- [x] Unit 2 — wait after the shared device preparation's own `boot`, before the device type is
      recorded and the locale pin runs.
- [x] Unit 3 — wait after the locale pin's reboot, read the booted state back after `shutdown` so a
      refused shutdown is not recorded as a confirmed pin, and log one informational line when the
      pin decides to write. The read-back's verdict is applied *after* the plist read-back rather
      than instead of it, so it only ever downgrades a confirmation: a device reading back another
      locale still fails the run loudly, which a refused shutdown would otherwise mask on a host
      whose booted listing is unreadable — a wedged CoreSimulator makes both calls fail together.
      The boot and its wait run either way, because a listing that could not be read may well be a
      device that did shut down, and the caller is about to install onto it.

## References

- [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md) — records that
  `simctl boot` returns before the boot completes, the property this item synchronises with.
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
  — introduced the system-locale pin and its reboot, the second unwaited boot.
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — added the
  recovery ladder whose two rungs already wait, and whose flake this window is a candidate cause of.
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  — forces the erase precondition on a crash retry, putting the slowest boot on the retry path.

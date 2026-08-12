**English** · [日本語](BE-XXXX-xcuitest-boot-completion-wait-ja.md)

# BE-XXXX — Wait for the Simulator to finish booting before installing and launching the app

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-boot-completion-wait.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Platform support |
| Related | [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md), [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) |
<!-- /BE-METADATA -->

## Introduction

Before Bajutsu can drive an iOS Simulator, the device must be running: booted far enough that
SpringBoard, the iOS process that owns the home screen and launches apps, will accept a launch
request. The Simulator control tool `simctl` separates asking from waiting. Its `boot` subcommand
returns as soon as the boot has been *requested*, and a second subcommand, `bootstatus`, blocks until
the boot has actually *finished*.

Bajutsu's Simulator preparation asks without waiting. It calls `boot`, then installs the app and
launches the test runner against a device that may still be starting up. This item inserts the wait,
in the two places that boot a device and then use it: the shared device preparation, and the
system-locale pin that reboots a device to make the new locale take effect. On this project's own
hardware the gap between the two subcommands measured **39.4 seconds** on a first boot, and the
project's continuous integration (CI) configuration already estimates roughly 80 seconds on a CI
runner. Bajutsu spends that entire window assuming a device that is not yet ready.

## Motivation

Two of the four places that boot a device already wait, which is what makes the other two look like
an oversight rather than a decision. The recovery ladder added by
[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) reboots a degraded
Simulator and replaces a vanished one, and both rungs run `bootstatus` before handing the device on.
The shared preparation those rungs then call does not, and neither does the locale pin's own reboot.
Across the whole XCUITest lifecycle module, `bootstatus` appears at exactly two call sites, both
inside that ladder.

The window is not theoretical. Measured on a freshly created iPhone 17 device running iOS 26.5 under
Xcode 26.5, `simctl boot` returned in **0.56 seconds** while `bootstatus` did not complete until
**39.94 seconds** — a 39.4-second stretch in which the preparation proceeds to install the app,
apply permission grants, and start `xcodebuild`. A CI runner is slower still, and the project already
knows it: the comment documenting the crash-recovery budget in the iOS end-to-end workflow sizes the
unbounded preparation at roughly 80 to 150 seconds, citing one job's own 80-second Simulator-boot
estimate. That is a wall-clock allowance for the boot, not a synchronisation with it.

Launching an app into a half-booted SpringBoard produces a recognisable failure. XCUITest reports
`Failed to launch <bundle id>: Timed out attempting to launch app` after about 40 seconds, a duration
that matches the measured window closely enough to make the connection worth naming. That signature
was the dominant iOS flake through early August 2026 and drove
[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)'s entire recovery
ladder, which repairs the device *after* an attempt fails on it.

The signature is absent from CI at the time of writing, and this item claims no current CI failure.
What it claims is that the exposure grew while the symptom receded. CI passes no flag that suppresses
the erase precondition, and
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
now forces that precondition on every crash-triggered retry, which sends the retry down the
`shutdown` → `erase` → `boot` path. A freshly erased device boots from a genuine first-boot state,
the slowest case measured above, and the retry then proceeds into the unwaited window. So the
longest form of the gap is now on the path taken immediately after the failure that a retry exists to
absorb.

A developer's own Mac is exposed more directly, through the locale pin. The pin exists so that
SpringBoard renders its permission-prompt buttons in a predictable language
([BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)),
and it reboots the device because a running SpringBoard does not pick up a global-domain write. A
newly created Simulator inherits the host's own language settings. On a host whose region is not the
United States, the inherited value fails the pin's match test and the pin fires. Measured on a
Japanese-region host, a fresh device seeded two language entries and the locale to match them. A
two-entry list fails the pin's "exactly one entry" test. The pin therefore fires, reboots, and
returns into the unwaited window on every fresh device.

Whether the pin fires on GitHub's own runners cannot be answered from the logs today, because the pin
writes nothing when it fires. That silence is itself worth fixing, and it is cheap: one log line
turns an open question into a fact the next failing job answers on its own.

## Detailed design

The work breaks into three units. Unit 1 is independent; units 2 and 3 both depend on it only in the
sense that they use the helper it names, and can land together with it.

1. **Name the wait once.** The two existing call sites invoke the wait by building the `bootstatus`
   argument list inline and running it. Give the Simulator environment a small private helper that
   performs the wait for the device it holds, and route the existing two call sites through it, so the
   new call sites added below do not each restate the command. The helper raises what the surrounding
   preparation already raises on a `simctl` failure, keeping one failure mode for the whole
   preparation rather than a second one that callers must learn.

2. **Wait after the preparation's own boot.** In the shared device preparation, follow the `boot` call
   with the wait from unit 1, before the device type is recorded and before the locale pin runs. The
   preparation is the single place both a cold bring-up and the recovery ladder's re-preparation pass
   through, so one insertion covers every caller. The wait is unbounded, exactly as the preparation's
   install and permission steps already are: a device that takes 80 seconds to come up has not failed,
   and the run-level and job-level ceilings above this layer are what bound a device that never does.

3. **Wait after the locale pin's reboot, and say when the pin fires.** The pin shuts the device down
   and boots it again to make a global-domain write take effect, then reads the value back to confirm
   it. Insert the wait between that boot and the read-back, so the confirmation reads a device that
   finished starting and the caller returns into a ready device rather than a starting one. Separately,
   log one informational line when the pin decides to write, naming the locale it is pinning. The pin
   is silent today except when the read-back fails, which is why nobody can tell from a CI log whether
   the pin fired at all — and the answer decides how much of unit 2's exposure applies to CI rather
   than only to a developer's Mac.

The measurements this item rests on are reproducible with two commands and a stopwatch — `simctl
boot` followed by `bootstatus -b` on a freshly created device — so a reviewer can confirm the window
without a CI run. The behavioural change is covered by the existing lifecycle tests, which already
assert the ordering of `simctl` calls in the preparation and in the recovery ladder; each new wait
adds one expected call to those sequences.

## Alternatives considered

- **Bound the wait with a timeout and fail the run when it expires.** Attractive because it would
  turn a hung boot into a named failure rather than a job-level timeout. Rejected for this item
  because `simctl`'s own subprocess calls carry no timeout at all, so bounding the wait alone would
  address one call among many while implying the rest are bounded. Bounding every `simctl` call is a
  separate, larger change with its own choice of timeout and failure mode, and it belongs in its own
  item rather than riding along here.
- **Poll SpringBoard for readiness instead of calling `bootstatus`.** A readiness probe against
  SpringBoard would answer the question the launch actually cares about, rather than the one the boot
  reports. Rejected as redundant: `bootstatus` already waits on the device's own boot completion, it
  is the tool Apple provides for the purpose, and the recovery ladder has used it since
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) without needing a
  finer signal.
- **Wait only when the preconditions carry an erase.** An erased device is the slowest case, so the
  wait would go where the window is widest and leave the common path untouched. Rejected because the
  window is not exclusive to an erase — a device booting for the first time in a job is equally
  affected — and a conditional wait would leave the very asymmetry this item exists to remove, with
  one more condition to explain.
- **Log the pin's firing and measure before inserting any wait.** The cheapest possible first step,
  and the reason unit 3's log line is worth having on its own. Rejected as the whole of the item: the
  measurement already establishes the window on a developer's Mac, where the pin demonstrably fires,
  so waiting for CI evidence would withhold a fix from the case that is already proven.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — add a private boot-completion wait helper and route the two existing `bootstatus` call
      sites through it.
- [ ] Unit 2 — wait after the shared device preparation's own `boot`, before the device type is
      recorded and the locale pin runs.
- [ ] Unit 3 — wait after the locale pin's reboot, before the read-back, and log one informational
      line when the pin decides to write.

## References

- [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md) — records that
  `simctl boot` returns before the boot completes, the property this item synchronises with.
- [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
  — introduced the system-locale pin and its reboot, the second unwaited boot.
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — added the
  recovery ladder whose two rungs already wait, and whose flake this window is a candidate cause of.
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  — forces the erase precondition on a crash retry, putting the slowest boot on the retry path.

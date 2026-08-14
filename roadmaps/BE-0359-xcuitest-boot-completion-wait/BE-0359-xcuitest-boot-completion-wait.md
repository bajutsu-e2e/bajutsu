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

Before Bajutsu can drive an iOS Simulator, the device must be running: booted far enough that
SpringBoard, the iOS process that owns the home screen and launches apps, will accept a launch
request. The Simulator control tool `simctl` separates asking from waiting. Its `boot` subcommand
returns as soon as the boot has been *requested*, and a second subcommand, `bootstatus`, blocks until
the boot has actually *finished*.

Bajutsu's Simulator preparation asks without waiting. It calls `boot`, then installs the app and
launches the test runner against a device that may still be starting up. This item inserts the wait,
in the two places that boot a device and then use it: the shared device preparation, and the
system-locale pin that reboots a device to make the new locale take effect. On this project's own
hardware the gap between the two subcommands measured **39.4 seconds** on a first boot.

Continuous integration (CI) does not pay that gap on its first bring-up, and the item does not claim
it does. Every iOS end-to-end job runs its own `bootstatus` step before the run step, which is
[BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md)'s design working as
intended. The window Bajutsu genuinely leaves unwaited is a developer's Mac on every run, and on CI
the erase-carrying retry, which boots again long after that step has returned.

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

1. **Name the wait once.** The argv builder is already shared and already named — `bootstatus_cmd`,
   which the web UI's own boot wait builds from too — so this unit adds no second builder beside it;
   the `-b` flag stays in exactly one place. What the two lifecycle call sites duplicate is the pair
   of steps around that builder: running it, and converting a `simctl` failure into the module's
   device-fault type. Give the Simulator environment a small private helper that performs both for
   the device it holds, and route the existing two call sites through it, so the new call sites added
   below do not each restate the pair.

   Put the helper on the environment class rather than in the `simctl` module, and accept what that
   costs: `Env.boot()` suppresses its own failure, so any future `Env.boot()` caller outside this
   class stays outside the wait this item names. That is the same boundary the two existing call
   sites already live within, and narrowing it would mean changing `Env.boot()`'s contract, which
   this item does not open.

2. **Wait after the preparation's own boot.** In the shared device preparation, follow the `boot` call
   with the wait from unit 1, before the device type is recorded and before the locale pin runs. The
   preparation is the single place both a cold bring-up and the recovery ladder's re-preparation pass
   through, so one insertion covers every caller. The wait is unbounded, exactly as the preparation's
   install and permission steps already are: a device that takes 80 seconds to come up has not failed,
   and the run-level and job-level ceilings above this layer are what bound a device that never does.

3. **Wait after the locale pin's reboot, and say when the pin fires.** The pin shuts the device down
   and boots it again to make a global-domain write take effect, then reads the value back to confirm
   it. Insert the wait between that boot and the read-back, so the confirmation reads a device that
   finished starting and the caller returns into a ready device rather than a starting one.

   The wait alone does not establish that the reboot happened, so pair it with the same read-back the
   recovery ladder's reboot rung already performs. `Env.shutdown()` and `Env.boot()` both suppress
   their own failure, and `bootstatus -b` returns at once on a device that never left `Booted` — so on
   a CoreSimulator wedged enough to refuse `shutdown`, the pin would otherwise return a "ready" device
   and record a confirmed pin while SpringBoard still renders the old language, defeating the
   determinism
   [BE-0320](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md)
   exists for. The plist read-back cannot separate the two cases, because it reads the value the write
   already changed rather than what SpringBoard loaded. Read the device's booted state back after
   `shutdown`, three-valued as the ladder's rung reads it, and treat a device that did not go down as
   a pin that was not confirmed rather than one that was. Separately,
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

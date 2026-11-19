**English** · [日本語](BE-0344-xcuitest-device-recovery-ja.md)

# BE-0344 — Repair the Simulator between XCUITest cold-spawn retry attempts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0344](BE-0344-xcuitest-device-recovery.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0344") |
| Implementing PR | [#1481](https://github.com/bajutsu-e2e/bajutsu/pull/1481) |
| Topic | Platform support |
| Related | [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's iOS backend runs scenarios through a resident test runner. It starts the runner on an iOS
Simulator with `xcodebuild test-without-building`. When that cold start fails, it is retried once,
so a one-off blip does not redden a whole continuous integration (CI) job.

The retry isolates every *host*-side resource per attempt: the loopback port that the runner binds,
the patched `.xctestrun` that it launches from, and the file that captures its output. But the retry
gets the same Simulator that the first attempt failed on. Suppose the first attempt failed *because*
that Simulator had stopped honouring automation. Then the retry could not succeed, and every
occurrence spent minutes proving it.

This item makes the cold spawn repair the device between attempts. After a failed attempt, the spawn
probes the Simulator and picks a remedy from what the attempt failed on:

- A device that the Simulator control tool `simctl` no longer lists is replaced with a freshly
  created one.
- A device that stopped honouring automation is rebooted and re-prepared.
- A runner process that exited on its own needs nothing beyond the teardown that already ran.

A repaired device earns a fresh readiness ceiling, because the next attempt runs against a device
that demonstrably came back up. A device that cannot be repaired fails the run as a device fault. It
does not fund another attempt that cannot work.

## Motivation

On 2026-08-04, four iOS CI jobs failed independently across two unrelated pull requests:
`actuation`, `run`, `bundled-runner`, and `golden`. Every one of them failed the same way, before a
single scenario ran:

```
XcuitestChannelError: xcuitest runner did not come up:
attempt 1/2: the xctest run ended (Test Suite 'All tests' failed) before the runner bound its port
attempt 2/2: health never ready within 166.9s
```

The captured runner output tells the story that the message alone does not. On the first attempt,
`xcodebuild` started the test host and the runner test began. Then the app under test never came to
the foreground:

```
    t =     2.27s Open com.bajutsu.showcase.ios.swiftui
    t =     2.28s     Launch com.bajutsu.showcase.ios.swiftui
<unknown>:0: error: Failed to launch com.bajutsu.showcase.ios.swiftui: Timed out attempting to launch app.
```

A launch that times out after 40 seconds, on a Simulator whose boot already completed, is a
statement about the device. It is not a statement about the build. The sibling jobs of the same
commit installed and launched the same app without trouble. The first attempt thus ends by
discarding its runner, and the retry spawns onto the device that had just refused to foreground an
app.

What the retry then did is the finding. In three of the four failures, `xcodebuild` resolved its
destination and emitted two lines about it. It then produced no further output at all. It never
reached `Running tests...` until the remaining budget ran out 150 to 180 seconds later. In the
fourth failure, it exited with code 70 after about 70 seconds:

```
xcodebuild: error: Unable to find a device matching the provided destination specifier:
		{ platform:iOS Simulator, id:2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA }

	The requested device could not be found because no available devices matched the request.
```

The destination list that `xcodebuild` printed alongside that error contained no iOS Simulator at
all. The entire iOS device set of the host had gone while the job was running. No crash report was
collected, so nothing had crashed. CoreSimulator, the macOS service that owns Simulator devices, had
stopped offering them.

A retry cannot absorb either outcome, because the retry changes nothing about the condition that
produced it.
[BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md) built
that retry and hardened it twice. It fails fast when the `xcodebuild` process dies, and it ends the
wait when the test run ends rather than at the ceiling. Both hardenings watch the *spawn*.

The "Alternatives considered" section of BE-0319 came close to naming the gap, while rejecting a
different fix. A relaunch inside a runner "whose XCTest automation session has already wedged reuses
the very session that just timed out". The reasoning was recorded, and then applied only to the
rejected alternative. Nobody carried it across to the chosen design, where the retry spawns a whole
new runner onto the same wedged device.

Two smaller gaps in the teardown between attempts compound the problem:

- The runner is terminated with a signal to the `xcodebuild` process alone. `xcodebuild` spawns the
  XCTest-host plumbing that drives the device. Those children survive a teardown that stops at the
  parent.
- Nothing terminates the app under test. The Swift runner calls `_exit` on a pre-serving failure
  instead of unwinding XCTest, deliberately, so that `xcodebuild` does not linger. XCTest therefore
  never brings the app down either. An app left mid-launch by the timeout that failed one attempt is
  exactly what the next attempt calls `launch()` on.

## Detailed design

The work breaks into five units. Units 1 and 2 are independent. Unit 3 depends on the failure
classification in unit 1. Unit 4 depends on units 2 and 3, and unit 5 depends on unit 4.

1. **Classify why an attempt failed.** The readiness wait returns a prose reason today. A caller
   that wanted to act on the *kind* of failure would have to parse it. Return a small record
   instead. The record carries a `kind` and the sentence that the failing error already quotes. The
   kind is one of three: the runner process exited, the test run ended, or the wait reached its
   ceiling.

   The recovery keys on the kind alone, so the remedy never depends on wording. The run-ended reason
   also names the app-launch timeout when the capture shows one. A reader needs that signature to
   tell "this device needs rebooting" from "this build is broken".

2. **Terminate the process group and the app under test.** Signal the whole process group of the
   runner rather than the `xcodebuild` process alone, so the XCTest-host children go with it. Give
   the runner its own group at spawn time for that signal to reach.

   Send `SIGTERM` to the group, wait out a grace period for the leader, and then send `SIGKILL` to
   the group **unconditionally**. Do not send it only when the leader outlived the grace. The exit
   of the leader says nothing about its children. This unit exists to prevent one state: an
   `xcodebuild` that unwinds within the grace while a child keeps the automation session.

   Then terminate the app under test through `simctl`, best-effort. Every step is suppressed,
   because a teardown runs on the failure path. There, raising would mask the error that caused the
   teardown.

3. **Probe the device and pick a remedy.** Between attempts, ask `simctl` whether it still lists the
   leased device, and choose one of four remedies:

   - **The device is gone.** Create a replacement and wait out its first boot. Then re-run the
     device preparation on it: install the app, apply the permission grants, and pin the system
     locale.

     The device type of the replacement is cloned from the vanished device's own. It is captured at
     the first preparation, while the device was still listed. By the time a replacement is needed,
     the device is no longer there to ask. The runtime is cloned with it, so the replacement stays
     on the iOS version that the scenario was written against. If that runtime is gone too, the
     clone falls back to whichever runtime `simctl` pairs.

     Two fallbacks follow the type. The first is the configured device model. The second applies to
     an iPhone target only: whichever iPhone the Xcode of this host ships, since a configured model
     can outlive the Xcode that had it.

     The last fallback is scoped to an iPhone target because it is not scoped to a device *class*.
     `eff.device` matches the device-type name of `simctl` exactly, and the name of an iPad carries
     parentheses, so an exact-match miss is ordinary. Substituting an iPhone for a missed iPad would
     finish the run on a layout that the scenario was never written against. An iPad target with no
     matching type therefore fails loudly instead.

     A target that configures no `appPath` also fails loudly here. It fails before the create, not
     after it. A replacement is a blank device, so with nothing to install, the retry would spawn
     onto a device with no app to launch.
   - **The probe itself failed.** Change nothing. A host too sick to list its devices must not have
     a device replaced on that evidence. That is why the probe has three values (present, absent,
     unknown) rather than a boolean.
   - **The runner process exited on its own.** Change nothing beyond the teardown. A fast,
     self-inflicted exit is the transient blip that the retry was written for, and it says nothing
     about the device.
   - **Anything else**, such as an app-launch timeout, or a wait that reached its ceiling. Shut the
     Simulator down, boot it, wait for the boot to complete, and re-run the device preparation.

   A new `BAJUTSU_XCUITEST_RECOVERY_TIMEOUT` (180 seconds by default) checks the repair proper once
   a rung returns. The repair proper is the probe, plus the reboot or the create that follows it. It
   excludes the device preparation that the first and last bullets end with. That preparation runs
   unbounded, exactly as the first cold bring-up's does.

   The check cannot preempt a rung in progress, because the `simctl` steps of a rung are blocking
   calls with no subprocess-level timeout of their own. The check catches a device that took
   absurdly long to come back. It does not catch a `simctl` call that never returns at all. Only the
   outer timeout of the run catches a genuinely wedged CoreSimulator. A rung that did overrun has
   spent the budget that the retry would need anyway.

4. **Give a repaired device a fresh readiness ceiling.** Today the startup ceiling is a budget
   shared across the two attempts, and deliberately so. An attempt that spends the whole ceiling
   leaves nothing for a retry. A second full wait against an unchanged device would double the worst
   case for no new information.

   That reasoning stops holding once a rung has rebooted or replaced the device. A recovery
   therefore reports whether it changed anything, and a retry that follows a repair restarts the
   ceiling. This is what makes the dominant flake recoverable at all. An app-launch timeout ends the
   first attempt quickly, but what it leaves behind is a degraded device, not spare seconds.

   The retry always restarts on the full cold ceiling. It does not use the tighter ceiling that a
   respawn lane chose. A reboot leaves the device booted, about to reinstall the app. A replacement
   device has run nothing yet. Either way, `xcodebuild test-without-building` runs against a device
   in a first-boot state. That state already earns the full cold ceiling on the cold spawn's own
   erase path. The ceiling applies regardless of the respawn history of this instance. Giving a
   rebooted respawn the tighter lane ceiling alone would undersize that first bring-up.

   The note of every rung is folded into the failing error, alongside the captured tail of each
   attempt. A reader then sees which remedy ran and what the device looked like.

5. **Follow the lease onto a replacement device.** The device pool holds five things keyed by device
   id:

   - the per-device network collector,
   - the warm-runner cache,
   - the `simctl` captures of the evidence sink,
   - the device attribution of the result, and
   - the free-device queue.

   A replacement that the pool did not hear about would leave every one of them naming a device that
   no longer exists.

   Add one query to the environment protocol: the id that the environment moved to, or nothing.
   Every other platform answers with nothing. The pool re-keys what it holds after the bring-up
   returns. The vanished device is never freed again. The replacement takes its place, which
   quarantines the dead device.

   The `crawl` command builds a second environment from the raw lane id for its reset seam. After a
   replacement, that environment would reset a different device. `crawl` therefore shares one
   environment instead.

## Alternatives considered

- **Give each attempt its own full ceiling, and change nothing else.** This is the first shape that
  the investigation considered, and the one that the failure log invites: attempt 2 was cut short by
  a budget that attempt 1 had spent. Measuring what attempt 2 did ruled it out. In three of four
  failures, attempt 2 never reached `Running tests...` at all, so a larger budget would have bought
  more silence. In the fourth, the device was gone, and no budget helps. The cost is real, too.
  Every iOS lane shares the ceiling, so doubling the worst case would press against the
  `timeout-minutes` of each job. A fresh ceiling is worth granting once the device has been
  repaired, which is unit 4.
- **Fail fast on a vanished device instead of creating a replacement.** Creating a Simulator needs a
  runtime. The observed failure had lost its runtimes along with its devices, so a replacement will
  sometimes be impossible. A replacement also changes the device id mid-lease, which unit 5 exists
  to propagate. Rejected in favour of attempting the replacement. When creation succeeds, the job
  recovers instead of costing a human a re-run. When it cannot, the run fails in seconds with the
  cause named, which is still better than today's silent stall.
- **Delete the replacement when the run ends.** The replacement outlives the run, deliberately. It
  is a healthy device that a later run can lease. Deleting it would make the next run pay another
  creation on a host that has already shown it loses devices. It is also the evidence that a
  replacement happened at all.

  The cost is one new device named `<model> (bajutsu-recovered-<old udid>)`, so an operator can tell
  which recovery minted which. The device is minted per *run* that leases a udid `simctl` no longer
  lists, not per loss. The `booted` alias self-heals, because the replacement is left booted and
  resolves next time. But a config or `--udid` pinned to a permanently vanished device mints a
  fresh, identically named replacement on every run. It does not converge on the one already
  created. The operator clears the devices with `xcrun simctl delete unavailable`, by deleting them,
  or by re-pointing the pinned config at the replacement.

  The model leads that name because two consumers read the name of a device as its human model. One
  is the device row of the report. The other is the capability inventory of `serve`, which takes the
  `iphone` / `ipad` class token out of the name by substring. On an ephemeral CI runner none of this
  is visible. The deliberate choice is for the developer's own Mac and a long-lived `serve` process.
- **Re-create the device on every failed attempt, without probing first.** This is cheaper to write
  than the ladder, and wrong in the common case. A Simulator that is merely wedged is repaired by a
  reboot in a fraction of the time that a create-plus-first-boot takes. A probe that could not run
  would make every transient blip replace a healthy device.
- **Gate the reboot rung on the app-launch-timeout marker instead of every non-process-exit
  failure.** The classifier already tells a launch timeout apart from an ordinary `run-ended` (a
  broken build, a bad signature, a crash on launch) and from a wait that reached its ceiling. Gating
  on the marker would spare the two failures that a reboot cannot fix:
  - A broken build pays one reboot-and-reinstall cycle before failing anyway. The cycle costs
    roughly the ~80s Simulator boot of the `golden` job, plus the install.
  - A wait that reached its ceiling is exactly the case about which BE-0319 argued that a second
    full wait "would double the worst case for no new information".

  Rejected, because the marker is a *detected* signature. It is not the full set of ways a Simulator
  stops honouring automation. Gating on it would let a device-wedge failure that does not log the
  literal launch-timeout text fall through to the do-nothing rung. That reproduces the stall from
  before this item, for exactly the case that this item exists to recover.

  The cost of a repeatable failure is bounded: one reboot cycle, not a multiplying one. BE-0049's
  guarantee holds either way: a repeatable failure still fails once `attempts` runs out.
  Under-recovering the dominant flake, by contrast, would not be caught by any test that the gate
  can express.
- **Retry the app launch inside the Swift runner.** BE-0319 rejected this because a relaunch reuses
  the wedged automation session. A second reason rejects it again here. The fix would ship in the
  Swift package, so every lane would need a rebuilt runner to pick it up. The recovery in the logic
  core reaches every lane at once.
- **Re-run the whole job on this failure.** A job re-run is the operational fallback that exists
  today, and this item removes the need for it. A re-run costs a human's attention and a full
  metered macOS job to recover from one bad device, and it leaves no record of what was wrong.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — classify an attempt's failure, and name an app-launch timeout in its reason.
- [x] Unit 2 — terminate the runner's process group and the app under test on discard.
- [x] Unit 3 — probe the device between attempts and run the matching recovery rung, bounded by
      `BAJUTSU_XCUITEST_RECOVERY_TIMEOUT`.
- [x] Unit 4 — restart the readiness ceiling after a repair, and fold every rung's note into the
      failing error.
- [x] Unit 5 — follow a lease onto a replacement device through the pool's per-device state.

## References

- [BE-0319 — Make the XCUITest cold runner spawn diagnosable and self-healing](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md) — the retry this item repairs the device for.
- [BE-0218 — Stabilize the E2E Simulator gate](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
- [BE-0291 — Reuse the XCUITest runner across scenarios](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md) — the warm-resident cache the pool re-keys on a replacement.
- [BE-0320 — Make iOS system-alert handling locale-deterministic](../BE-0320-ios-system-alert-locale-determinism/BE-0320-ios-system-alert-locale-determinism.md) — the locale pin a re-prepared or replaced device re-establishes.
- [BE-0049 — Determinism and flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — the "flakiness is never tolerated by absorption" stance the bounded recovery preserves.
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — the cold spawn, its retry, and the recovery ladder.
- `bajutsu/simctl.py` — the device probe and the replacement's creation.
- `bajutsu/runner/pool.py` — the per-device state re-keyed onto a replacement.

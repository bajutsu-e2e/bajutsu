**English** · [日本語](BE-XXXX-xcuitest-device-recovery-ja.md)

# BE-XXXX — Repair the Simulator between XCUITest cold-spawn retry attempts

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-xcuitest-device-recovery.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1481](https://github.com/bajutsu-e2e/bajutsu/pull/1481) |
| Topic | Platform support |
| Related | [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0291](../BE-0291-xcuitest-runner-reuse-across-scenarios/BE-0291-xcuitest-runner-reuse-across-scenarios.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's iOS backend runs scenarios through a resident test runner that it starts on an iOS
Simulator with `xcodebuild test-without-building`. That cold start is retried once when it fails, so
a one-off blip does not redden a whole continuous integration (CI) job. The retry isolates every
*host*-side resource per attempt — the loopback port the runner binds, the patched `.xctestrun` it is
launched from, the file its output is captured into — but it hands the retry the same Simulator the
first attempt failed on. When the first attempt failed *because* that Simulator had stopped honouring
automation, the retry could not succeed, and every occurrence spent minutes proving it.

This item makes the cold spawn repair the device between attempts. After a failed attempt the spawn
probes the Simulator and picks a remedy from what the attempt failed on: a device the Simulator
control tool `simctl` no longer lists is replaced with a freshly created one, a device that stopped
honouring automation is rebooted and re-prepared, and a runner process that exited on its own needs
nothing beyond the teardown that already ran. A repaired device earns a fresh readiness ceiling,
because the next attempt runs against a device that demonstrably came back up. A device that cannot
be repaired fails the run as a device fault rather than funding another attempt that cannot work.

## Motivation

On 2026-08-04 four iOS CI jobs failed independently across two unrelated pull requests. The four are
`actuation`, `run`, `bundled-runner`, and `golden`. Every one of them failed the same way, before a
single scenario ran:

```
XcuitestChannelError: xcuitest runner did not come up:
attempt 1/2: the xctest run ended (Test Suite 'All tests' failed) before the runner bound its port
attempt 2/2: health never ready within 166.9s
```

The captured runner output tells the story the message alone does not. On the first attempt
`xcodebuild` started the test host, the runner test began, and then the app under test never came to
the foreground:

```
    t =     2.27s Open com.bajutsu.showcase.ios.swiftui
    t =     2.28s     Launch com.bajutsu.showcase.ios.swiftui
<unknown>:0: error: Failed to launch com.bajutsu.showcase.ios.swiftui: Timed out attempting to launch app.
```

A launch that times out after 40 seconds on a Simulator whose boot already completed is a statement
about the device, not about the build: the same commit's sibling jobs installed and launched the same
app without trouble. So the first attempt ends by discarding its runner, and the retry spawns onto
that device — the one that had just refused to foreground an app.

What the retry then did is the finding. In three of the four failures `xcodebuild` resolved its
destination, emitted two lines about it, and then produced no further output at all — it never
reached `Running tests...` — until the remaining budget ran out 150 to 180 seconds later. In the
fourth it exited with code 70 after about 70 seconds:

```
xcodebuild: error: Unable to find a device matching the provided destination specifier:
		{ platform:iOS Simulator, id:2A6DC5A9-CE8C-4BC5-959D-F98D5F4BD9AA }

	The requested device could not be found because no available devices matched the request.
```

The destination list `xcodebuild` printed alongside that error contained no iOS Simulator at all: the
host's entire iOS device set had gone while the job was running. No crash report was collected, so
nothing had crashed — CoreSimulator, the macOS service that owns Simulator devices, had simply
stopped offering them.

Neither outcome is something a retry can absorb, because the retry changes nothing about the
condition that produced it. [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md)
built that retry and hardened it twice — it fails fast when the `xcodebuild` process dies, and it
ends the wait when the test run ends rather than at the ceiling — but both hardenings watch the
*spawn*. Its own "Alternatives considered" section came close to naming the gap, in the course of
rejecting a different fix: a relaunch inside a runner "whose XCTest automation session has already
wedged reuses the very session that just timed out". The reasoning was recorded and then applied only
to the rejected alternative. Nobody carried it across to the chosen design, where the retry spawns a
whole new runner onto the same wedged device.

Two smaller gaps in the teardown between attempts compound the problem. The runner is terminated with
a signal to the `xcodebuild` process alone, and `xcodebuild` spawns the XCTest-host plumbing that
actually drives the device, so those children survive a teardown that stops at the parent. And
nothing terminates the app under test: the Swift runner calls `_exit` on a pre-serving failure rather
than unwinding XCTest (deliberately, so `xcodebuild` does not linger), which means XCTest never
brings the app down either. An app left mid-launch by the timeout that failed one attempt is
therefore exactly what the next attempt calls `launch()` on.

## Detailed design

The work breaks into five units. Units 1 and 2 are independent; unit 3 depends on the failure
classification in unit 1, unit 4 on units 2 and 3, and unit 5 on unit 4.

1. **Classify why an attempt failed.** The readiness wait returns a prose reason today, so a caller
   that wanted to act on the *kind* of failure would have to parse it. Return a small record instead,
   carrying both a `kind` — the runner process exited, the test run ended, or the wait reached its
   ceiling — and the sentence the failing error already quotes. The recovery keys on the kind alone,
   so the remedy never depends on wording. The run-ended reason also names the app-launch timeout when
   the capture shows one, because that is the signature a reader needs to tell "this device needs
   rebooting" from "this build is broken".

2. **Terminate the process group and the app under test.** Signal the runner's whole process group
   rather than the `xcodebuild` process alone, so the XCTest-host children go with it, and give the
   runner its own group at spawn time for that signal to reach. `SIGTERM` the group, wait out a grace
   period for the leader, then `SIGKILL` the group **unconditionally** rather than only when the leader
   outlived the grace: the leader's exit says nothing about its children, and an `xcodebuild` that
   unwinds within the grace while a child keeps the automation session is the state this unit exists
   to prevent. Then terminate the app under test through `simctl`, best-effort. Every step is suppressed:
   a teardown runs on the failure path, where raising would mask the error that caused it.

3. **Probe the device and pick a remedy.** Between attempts, ask `simctl` whether it still lists the
   leased device, and choose:

   - **The device is gone.** Create a replacement, wait out its first boot, then re-run the device
     preparation on it — installing the app, applying permission grants, and pinning the system
     locale. The
     replacement's device type is cloned from the vanished device's own, captured at the first
     preparation while the device was still listed — by the time a replacement is needed the device is
     no longer there to ask. Two fallbacks follow it: the configured device model, then whichever
     iPhone this host's Xcode ships, since a configured model can outlive the Xcode that had it.
   - **The probe itself failed.** Change nothing. A host too sick to list its devices must not have a
     device replaced on that evidence, which is why the probe is three-valued (present, absent,
     unknown) rather than a boolean.
   - **The runner process exited on its own.** Change nothing beyond the teardown. A fast,
     self-inflicted exit is the transient blip the retry was written for, and it says nothing about
     the device.
   - **Anything else** — an app-launch timeout, or a wait that reached its ceiling. Shut the Simulator
     down, boot it, wait for the boot to complete, and re-run the device preparation.

   The whole ladder is bounded by a new `BAJUTSU_XCUITEST_RECOVERY_TIMEOUT` (180 seconds by default).
   The bound is checked after a rung rather than inside it, since the `simctl` steps are blocking calls
   the check cannot preempt: it catches a device that took absurdly long to come back, and a rung that
   overran has spent the budget the retry would need anyway.

4. **Give a repaired device a fresh readiness ceiling.** The startup ceiling is a budget shared across
   the two attempts today, and deliberately so: an attempt that spends the whole ceiling leaves nothing
   for a retry, because a second full wait against an unchanged device would double the worst case for
   no new information. That reasoning stops holding once a rung has rebooted or replaced the device, so
   a recovery reports whether it changed anything, and a retry that follows a repair restarts the
   ceiling. This is what makes the dominant flake recoverable at all: an app-launch timeout ends the
   first attempt quickly, but what it leaves behind is a degraded device rather than spare seconds.
   Every rung's note is folded into the failing error alongside each attempt's captured tail, so a
   reader sees which remedy ran and what the device looked like.

5. **Follow the lease onto a replacement device.** Five things the device pool holds are keyed by
   device id: the per-device network collector, the warm-runner cache, the evidence sink's `simctl`
   captures, the result's device attribution, and the free-device queue. A replacement the pool did not
   hear about would leave every one of them naming a device that no longer exists. Add one predicate to
   the environment
   protocol — the id the environment moved to, or nothing — which every other platform answers with
   nothing, and have the pool re-key what it holds after the bring-up returns. The vanished device is
   never freed again; the replacement takes its place, which is what quarantines the dead one. The
   `crawl` command builds a second environment from the raw lane id for its reset seam, which would
   reset a different device after a replacement, so it shares one environment instead.

## Alternatives considered

- **Give each attempt its own full ceiling, and change nothing else.** The first shape this
  investigation considered, and the one the failure log invites: attempt 2 was cut short by a budget
  attempt 1 had spent. Measuring what attempt 2 actually did ruled it out — in three of four failures
  it never reached `Running tests...` at all, so a larger budget would have bought more silence, and in
  the fourth the device was gone, where no budget helps. The cost is real, too: the ceiling is shared
  by every iOS lane, so doubling the worst case would press against each job's `timeout-minutes`.
  A fresh ceiling is worth granting once the device has been repaired, which is unit 4.
- **Fail fast on a vanished device instead of creating a replacement.** Creating a Simulator needs a
  runtime, and the observed failure had lost its runtimes along with its devices, so a replacement will
  sometimes be impossible — and a replacement changes the device id mid-lease, which unit 5 exists to
  propagate. Rejected in favour of attempting the replacement: when creation does succeed the job
  recovers instead of costing a human a re-run, and when it cannot the run fails in seconds with the
  cause named, which is still better than today's silent stall.
- **Re-create the device on every failed attempt, without probing first.** Cheaper to write than the
  ladder, and wrong in the common case: a Simulator that is merely wedged is repaired by a reboot in a
  fraction of the time a create-plus-first-boot takes, and a probe that could not run would make every
  transient blip replace a healthy device.
- **Retry the app launch inside the Swift runner.** BE-0319 rejected this because a relaunch reuses
  the wedged automation session, and a second reason rejects it again here: the fix would ship
  in the Swift package, so every lane would need a rebuilt runner to pick it up, while the recovery in
  the logic core reaches every lane at once.
- **Re-run the whole job on this failure.** A job re-run is the operational fallback that exists
  today, and it is what this item removes the need for. It costs a human's attention and a full
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

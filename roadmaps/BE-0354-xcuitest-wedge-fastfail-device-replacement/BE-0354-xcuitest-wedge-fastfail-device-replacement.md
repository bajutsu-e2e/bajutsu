**English** · [日本語](BE-0354-xcuitest-wedge-fastfail-device-replacement-ja.md)

# BE-0354 — Detect a wedged XCUITest session fast and escalate a repeated crash retry to a replacement device

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0354](BE-0354-xcuitest-wedge-fastfail-device-replacement.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0354") |
| Implementing PR | [#1557](https://github.com/bajutsu-e2e/bajutsu/pull/1557) |
| Topic | Platform support |
| Related | [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md), [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md), [BE-0305](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection.md) |
<!-- /BE-METADATA -->

## Introduction

Bajutsu's iOS backend drives scenarios through a resident test runner.
The runner is an XCUITest process.
It serves HTTP on a loopback port inside an iOS Simulator. Three shipped layers already recover its
failures.

- The runner channel rides out a mid-run crash. It polls the runner's health endpoint and
  re-issues the idempotent call
  ([BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md),
  [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)).
- A failed cold spawn repairs the device between attempts
  ([BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)).
- A crash-triggered scenario retry forces a device erase before it respawns
  ([BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)).

One failure class defeats all three at once.
The continuous integration (CI) fleet showed it in August 2026. The Simulator's screen-capture pipeline wedges while the runner stays otherwise healthy.
Every screenshot read hangs. The erase that the retry forces brings the same wedge
straight back.

Two layers engage: the channel's in-place recovery and the erase-forcing retry. Each spends its
full budget proving a dead end. One remedy changes more than the device's data: a replacement.
The mid-run path never reaches it. The job then fails with no scenario verdict.
It first spends ten minutes or more on machinery that cannot work.

This item makes the recovery stack classify that dead end in seconds.
It lands the retry on a device that can serve it. It has four parts.

- The runner channel learns to tell a wedged automation session from a transient crash.
The recovery loop was built for the transient crash. In a wedged session, the runner's health endpoint answers.
The same idempotent call times out on every re-issue. The channel fails over to the pipeline as soon
  as the wedge is provable.
- The liveness probe that the channel consults learns to read the runner's captured output. An
  XCTest run that already ended then stops being polled for a recovery that cannot come.
- The crash-triggered scenario retry gains an escalation rung above the forced erase of
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md).
  A retry whose erase was already tried replaces the Simulator outright, reusing the replacement
  machinery that
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) built for a
  vanished device.
- The evidence layer already detects a screen-capture stall.
The stall appears as the recording file confirmation timing out.
It selects that rung directly. The stall identifies the degradation class that an erase
  was observed not to clear.

## Motivation

Between 2026-08-06 and 2026-08-10, the iOS end-to-end lane failed on most workflow runs that executed XCUITest scenarios.
The failures spanned at least six unrelated branches. The many green runs in the same window were documentation-only changes.
The change filter skipped their Simulator jobs. The failing jobs all show one signature.
They are `golden`, `bundled-runner`, `visual`, `actuation`, and `fault-injection`.

One occurrence is fully logged.
It is the `bundled-runner (xcuitest)` job of pull request [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538), on 2026-08-09. The scenario's video
recording failed its start confirmation first:

```
recordVideo produced no new bytes in runs/20260809-233753/00-stable-catalog-smoke/scenario.mp4 within 20.0s; ...
```

Fifteen seconds later the runner channel began a cascade that repeated for three minutes.

1. The screenshot read timed out past its retry budget.
2. The crash-recovery layer polled the runner's health endpoint.
3. Health *answered*.
4. The layer re-issued the read.
5. The read timed out again.

```
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
runner channel GET /screenshot failed (attempt 2/3), retrying: timed out
runner channel GET /screenshot: the runner became unreachable past the retry budget — a mid-run crash: ...
runner channel GET /screenshot: the runner recovered from a mid-run crash; re-issuing the idempotent call (recovery 1/3)
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
```

The re-issue loop exists for a runner that crashes once and comes back serving.
[BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
observed that case mid-gesture. Here the loop rode out three full recovery cycles, each one a
timeout ladder plus a health wait. The session was never going to serve the read.
The device's screen-capture pipeline had wedged underneath a runner whose HTTP server was fine.

The signal that separates the two cases was present from the first cycle. A *recovered* runner may time out again on the very next re-issue of the same idempotent call.
Such a runner is not flapping.
It is wedged. Three minutes after the first timeout, the channel finally gave up.
The crash recovery of the pipeline took over.

The pipeline's retry then did what
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
ships. It discarded the lease and respawned with a forced device erase.
The erase runs `simctl shutdown`, `erase`, `boot`, and a reinstall. Three minutes later, the second attempt reproduced the first to the letter. The same video-start confirmation timed out.
The same screenshot cascade ran.
The same crash ended it. The per-scenario recovery budget was spent, and the job failed:

```
scenario stable catalog smoke: backend crashed mid-run (attempt 2/3): runner channel GET /screenshot failed: the runner crashed mid-run and did not recover within 60s
##[error]backend crashed mid-run and did not recover within the 300s crash-recovery budget (spent respawning across 2 attempt(s)): ...
```

The erase rung was the deliberate first choice of
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md),
and it shipped as the strongest remedy of the crash retry. The log above shows the class of
degradation that the remedy cannot reach. An erase resets the device's data.
The wedge lives in the capture services of the device, so the erased device came back wedged.

The only shipped remedy that changes more than the device's data is the replacement rung of
[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), a freshly
created Simulator. It is reachable from a failed *cold spawn* alone.
There it applies only when the device has vanished from `simctl` entirely. A mid-run wedge whose respawn comes up answering health meets
neither condition.

One more shipped defense should have cut the waste and did not.
[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
made the channel's recovery fail fast when the runner process is gone, so that a recovery that
cannot come is not waited out. Its probe, however, reads the `xcodebuild` process handle alone.

The fault-injection measurements of
[BE-0305](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection.md)
already recorded that blind spot on `main`. A killed test host does not take the process-exited fast path.
`_runner_alive` reads the `xcodebuild` parent, and that parent outlives the host. The
measurements left "a tighter liveness check" to a separate item; this item is that one.

An earlier investigation of this flake family examined four failed CI logs, all four alike. XCTest
restarted the in-Simulator test host after the crash and re-ran zero tests. The run is over and the
port will never bind again, yet `xcodebuild` lives on. The probe keeps answering "alive", and every
recovery episode waits its full 60-second window.

The captured runner output names the state plainly.
It uses the `Test Suite 'All tests'` markers that the cold-spawn gate already string-matches. Nothing on the mid-run path reads it.

## Detailed design

The design has four units.

- Units 1 and 2 are independent detection fixes in the seam of the runner channel.
- Unit 3 adds the escalation rung that the retry lacks.
- Unit 4 connects the existing stall signal of the evidence layer to the rung choice of unit 3.

Nothing here touches the verdict. Every unit reroutes or shortens *infrastructure* recovery.
A scenario that keeps failing still fails loudly. The stance of
[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) that
"flakiness is never tolerated by absorption" is unchanged.

1. **Classify a wedged automation session in the runner channel.** In
   `bajutsu/drivers/xcuitest.py`, `_with_crash_recovery` today re-issues an idempotent call after a
   confirmed health recovery, up to `_MAX_CRASH_RECOVERIES` consecutive crashes. The loop stays for
   the failure it was built for. One case is split out of it: the same read **timing out again on
   consecutive re-issues** while health keeps answering.

   A timeout here means a call that reached the runner and hung. The channel must tag this shape
   distinctly. The existing `delivered` tag
   ([BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md))
   marks a mid-response connection reset the same way, and only the hang identifies the wedge.

   One healthy state shares the surface of the hang for a while.
   [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
   serialized the runner's XCUITest operations while `/health` deliberately bypasses the
   serialization ("runner busy, not dead"). A long operation can therefore hold the lock while a
   concurrent read times out.

   The two states separate on the *second* post-recovery timeout of the same read. A single lock-holder cannot span that window.
Its own call fails its own retry ladder first. A
   read that still hangs then is wedged: the runner accepted it three times and answered none. The channel raises the crash error at that point.
The error carries a distinct "wedged automation session" diagnostic. It does not ride out the remaining recovery cycles and the final health wait.

   A connection-level post-recovery failure keeps today's flap-riding behavior.
Such a failure is a refusal or a reset mid-response. That shape is the genuinely crashing runner that the loop was built to ride
   out. Only the device-level retry of the pipeline can help a wedged session.
The job of the channel is to hand over fast, not to absorb.

2. **Let the liveness probe read the captured output.** The environment already answers the
   channel's "is the runner process alive" probe through `_runner_alive`, the seam that
   [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
   added. On the cold-spawn path, the environment also already scans the runner's captured output
   for the run-ended markers (`_RUN_ENDED_MARKERS`, the `Test Suite 'All tests' failed` / `passed`
   lines) in `_run_ended_probe`.

   This unit composes the two on the mid-run path. The probe reports the runner dead in two cases.
Either the process has exited, or the capture has *ever* shown the test run ended.

   The run-ended half **latches**, because the two sides pulse differently. `_run_ended_probe` is edge-triggered.
It advances a private offset.
It reports a marker from the read window that first contains it, and from no later window. The mid-run predicate is level-triggered and is re-asked once per recovery
   episode. An unlatched composition would answer "dead" once.
It would answer "alive" again for every later episode.

   One latched probe per spawn serves the cold gate and the mid-run predicate alike. The cold-spawn instance already consumes this same capture.
A second, independent instance would race it for the marker.

   A run that printed its suite-ended line serves nothing afterwards. This is the state that the
   earlier four-log investigation found `xcodebuild` outliving. The health wait of the recovery then fails at the next read of the probe.
It no longer waits for its 60-second ceiling.

   The markers are the unlocalized output of `xcodebuild` itself, and the cold gate has
   string-matched them since
   [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md).
   If a future Xcode rewords them, the probe degrades to today's full wait, never to a false
   "dead".

3. **Escalate the second crash retry to a replacement device.** The forced erase of
   [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
   stays the first rung. It is the cheaper remedy, and it recovers the app-data corruption class.
   But when a forced-erase retry itself ends in another backend crash, the next lease escalates.

   The run pipeline requests a device replacement on the lease.
The request comes from the retry loop of `run_one` in `bajutsu/runner/pipeline.py`. The XCUITest environment serves the request with the same
   create-boot-prepare path that the vanished-device rung of
   [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) uses. That
   includes its device-type and runtime cloning, its `bajutsu-recovered-*` naming, and the pool
   re-keying that follows the lease onto the new device.

   The degraded device is shut down and never freed back to the pool. This quarantines it exactly as
   a vanished device is quarantined today.

   The replacement lease also suppresses the forced erase for its own attempt. A freshly created device has nothing to erase.
Honoring `erase=True` on it would pay a second shutdown-and-boot cycle for no state change.

   The escalation request is a no-op wherever replacement is wrong or impossible.
Each such route keeps the strongest retry it has today.

   - A real device and the live WebDriver endpoint keep the bare respawn. Both reject an `erase` precondition outright.
That is why `erase_precondition_supported` already excludes them from the erase rung.
   - A run pinned to a concrete Simulator keeps the erase-level retry.
The pin is `--udid` or a config-pinned device. A replacement would silently move the run off the device that the operator
     named. It would also mint the per-run `bajutsu-recovered-*` residue, whose cost
     [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) documents
     for the pinned case.
   - Every other platform ignores the request.
The tool and runner stay app- and platform-agnostic.

   The existing bounds are untouched. The replacement spends the same `crash_retries` attempt as the respawn it replaces.
It spends the same per-scenario and per-run recovery budgets too. The bounds also
   limit reachability. Under the default `crash_retries = 1` there is no third attempt, so the
   second-crash trigger fires only on a lane that raises the count (the iOS lane sets
   `BAJUTSU_CRASH_RETRIES: 2`). A default run reaches the rung through the stall promotion of unit 4
   alone. The default deliberately stays put: a second full scenario replay is a cost that the
   metered on-device lanes opted into, not a new default.

   The rung changes behavior that `docs/architecture.md` and `docs/run-loop.md` document.
The behavior is the strongest remedy of the crash retry. Both pages and their `docs/ja/` mirrors move in the same
   change, per the BE-0113 norm.

4. **Let the video-start stall select the replacement rung.** The evidence layer already detects the
   earliest symptom of the wedge. `start_video` confirms that the recording file grows within the
   `_VIDEO_START_TIMEOUT` ceiling, and logs a warning when it does not.
   [BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md)
   proposes making the ceiling tunable as `BAJUTSU_VIDEO_START_TIMEOUT`.

   This unit surfaces that outcome on the lease. Suppose the video-start confirmation of a scenario attempt timed out.
Suppose the attempt then ends in a backend crash.
The next retry escalates directly to the replacement rung of unit 3.
It skips the forced erase.

   The stall identifies a degradation class of the capture pipeline.
An erase was observed not to clear it. The two remedies cost similar wall-clock time anyway. An erase is a shutdown, erase, boot,
   and reinstall; a replacement is a create, boot, and install. Even a false-positive stall (a slow
   but healthy encoder) pays roughly the erase it skipped.

   The signal stays advisory to *recovery rung choice* alone. It never fails an attempt on its own,
   and a run with video evidence disabled never produces it.

## Alternatives considered

- **Skip video evidence on retry attempts instead of reading the stall as a signal.** Dropping
  `recordVideo` from a retry would remove one load source from a degraded device. But the
  screenshot path rides the same capture services, so the retry would still hang as observed. The
  video of the retry is also the evidence a human needs when the retry fails too. Unit 4 extracts
  the same information from the stall at no evidence cost.
- **A session nonce in the health reply of the runner instead of reading the captured output
  (unit 2).** A restarted test host that re-serves would answer with a fresh nonce.
The nonce cleanly distinguishes "recovered" from "replaced". Rejected for the reason that
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) rejected a
  Swift-side launch retry. The fix would ship in the Swift package, so every lane would need a
  rebuilt runner to pick it up, while the captured-output probe lands in the logic core and reaches
  every lane at once. The nonce also cannot cover the measured failure mode at all: a host that
  re-runs zero tests never serves a nonce to compare.
- **Replace the device on the first crash retry, skipping the erase rung.** The wedge class that unit 4 identifies does justify going straight to replacement.
That holds when the stall signal is present, and not otherwise. An unconditional first-retry replacement would mint a `bajutsu-recovered-*` device for
  every one-off crash, and on a developer's Mac those accumulate (the alternatives of
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) weigh that
  residue). The erase rung also recovers the app-data corruption class at no residue, so it stays
  first when no stall was seen.
- **Restart the CoreSimulator daemon as a recovery rung.** The earlier investigation of this flake
  family measured it. A daemon restart is fast (about one second) and non-destructive.
It matched zero of the nine CI failure logs examined.
No upstream runner-images report recommends it. The one defensible slot found was an escalation after a `simctl shutdown` hang.
That is a narrower follow-up than this item. Left out.
- **Raise the budgets and ceilings further.** The lane already raised its job timeouts to 60
  minutes. The fully logged occurrence ran with its video-start ceiling already raised to 20
  seconds, which was the lane setting of pull request
  [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538), applied through the override that
  [BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md)
  proposes. The occurrence shows where the time actually goes: into recovery layers proving a dead
  end. More budget buys more silence.
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) measured the
  same thing for the cold-spawn retry ("a larger budget would have bought more silence").
- **Extend the replacement rung to the Android emulator in the same change.** The adb backend has
  no runner channel with this wedge shape, and
  [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
  already named the emulator-process restart a separate follow-up. Unchanged here.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — tag a timed-out (hung) call distinctly from a connection-level failure, classify the
      second post-recovery timeout of the same read as a wedged session, and fail over to the
      pipeline with a distinct diagnostic.
- [x] Unit 2 — compose the run-ended capture probe into the mid-run liveness probe (latched, one
      probe instance per spawn shared with the cold gate), so an ended test run fails the
      recovery's health wait fast.
- [x] Unit 3 — add the replacement escalation to `run_one`'s crash retry, reusing the
      vanished-device rung's creation, naming, and pool re-keying; suppress the forced erase on the
      replacement attempt; quarantine the replaced device; keep the bare respawn on routes that
      reject erase and the erase retry on a pinned Simulator; update the crash-recovery passages in
      `docs/architecture.md` / `docs/run-loop.md` and their `docs/ja/` mirrors.
- [x] Unit 4 — surface the video-start confirmation timeout on the lease and let it select the
      replacement rung for the attempt's own crash retry.

## References

- [BE-0344 — Repair the Simulator between XCUITest cold-spawn retry attempts](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — the replacement machinery and pool re-keying unit 3 reuses.
- [BE-0353 — Force device recovery on a backend-crash retry and cap total crash-recovery time per run](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) — the erase rung this item escalates above.
- [BE-0305 — Real-device fault-injection coverage for driver resilience paths](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection.md) — the measurement that recorded unit 2's blind spot and deferred the tighter liveness check this item ships; its fault-injection lane is the on-device harness that can exercise units 1 and 2.
- [BE-0323 — Recover the XCUITest cold launch when the runner crashes during the readiness gate](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md) — the liveness-probe seam unit 2 extends.
- [BE-0319 — Make the XCUITest cold runner spawn diagnosable and self-healing](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md) — the default runner-output capture unit 2 reads.
- [BE-0287 — XCUITest runner-channel resilience under multi-touch actuation](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md) — the in-channel recovery loop unit 1 splits the wedged case out of.
- [BE-0207 — Make the XCUITest runner channel robust to transient timeouts](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md) — the `delivered` tag unit 1 keys on.
- [BE-0348 — Record video, step, and network timestamps as absolute wall-clock time](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md) — the tunable video-start confirmation unit 4 reads.
- [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — the never-absorb stance every unit preserves.
- Pull request [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538)'s `bundled-runner (xcuitest)` job on 2026-08-09 ([run 31241662509](https://github.com/bajutsu-e2e/bajutsu/actions/runs/31241662509)) — the fully logged occurrence quoted in *Motivation*.
- `bajutsu/drivers/xcuitest.py` — the channel recovery loop (unit 1) and the liveness-probe seam (unit 2).
- `bajutsu/platform_lifecycle/environments/xcuitest.py` — the run-ended capture probe and the replacement rung.
- `bajutsu/runner/pipeline.py`, `bajutsu/runner/pool.py` — the crash-retry loop and the per-device state that follows a replacement.
- `bajutsu/evidence/intervals.py`, `bajutsu/evidence/core.py` — the video-start confirmation unit 4 surfaces.

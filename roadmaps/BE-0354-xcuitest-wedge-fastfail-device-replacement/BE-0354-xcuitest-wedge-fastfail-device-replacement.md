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

Bajutsu's iOS backend drives scenarios through a resident test runner — an XCUITest process serving
HTTP on a loopback port inside an iOS Simulator — and three shipped layers already recover its
failures: the runner channel rides out a mid-run crash by polling the runner's health endpoint and
re-issuing the idempotent call ([BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md),
[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)),
a failed cold spawn repairs the device between attempts
([BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)), and a
crash-triggered scenario retry forces a device erase before it respawns
([BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)).
A failure class observed across the continuous integration (CI) fleet in August 2026 defeats all
three at once: the Simulator's screen-capture pipeline wedges while the runner stays otherwise
healthy, every screenshot read hangs, and the erase that the retry forces brings the same wedge
straight back. The two layers that do engage — the channel's in-place recovery and the erase-forcing
retry — each spend their full budget proving a dead end, the one remedy that changes more than the
device's data (a replacement) is never reachable from the mid-run path, and the job fails with no
scenario verdict after ten minutes or more of machinery that cannot work.

This item makes the recovery stack classify that dead end in seconds and land the retry on a device
that can actually serve it. The runner channel learns to tell a wedged automation session — the
runner's health endpoint answers while the same idempotent call times out on every re-issue — from
the transient crash its recovery loop was built for, and fails over to the pipeline as soon as the
wedge is provable. The
liveness probe the channel consults learns to read the runner's captured output, so an XCTest run
that already ended stops being polled for a recovery that cannot come. And the crash-triggered
scenario retry gains the escalation rung above [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)'s
forced erase: a retry whose erase was already tried replaces the Simulator outright, reusing the
replacement machinery [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)
built for a vanished device. A screen-capture stall that the evidence layer already detects — the
recording file confirmation timing out — selects that rung directly, because it identifies the
degradation class an erase was observed not to clear.

## Motivation

Between 2026-08-06 and 2026-08-10, the iOS end-to-end lane failed on most of the workflow runs that
actually executed XCUITest scenarios, across at least six unrelated branches — the many green runs in
the same window were documentation-only changes whose Simulator jobs the change filter skipped. The
failing jobs (`golden` / `bundled-runner` / `visual` / `actuation` / `fault-injection`) all show one
signature, and one occurrence is fully logged: the `bundled-runner (xcuitest)` job of pull request
[#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538), on 2026-08-09. The scenario's video
recording failed its start confirmation first:

```
recordVideo produced no new bytes in runs/20260809-233753/00-stable-catalog-smoke/scenario.mp4 within 20.0s; ...
```

Fifteen seconds later the runner channel began a cascade that repeated for three minutes: the
screenshot read timed out past its retry budget; the crash-recovery layer polled the runner's health
endpoint; health *answered*; the layer re-issued the read; and the read timed out again.

```
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
runner channel GET /screenshot failed (attempt 2/3), retrying: timed out
runner channel GET /screenshot: the runner became unreachable past the retry budget — a mid-run crash: ...
runner channel GET /screenshot: the runner recovered from a mid-run crash; re-issuing the idempotent call (recovery 1/3)
runner channel GET /screenshot failed (attempt 1/3), retrying: timed out
```

The re-issue loop exists for a runner that crashes once and comes back serving
([BE-0287](../BE-0287-xcuitest-runner-multitouch-resilience/BE-0287-xcuitest-runner-multitouch-resilience.md)
observed exactly that, mid-gesture). Here it rode out three full recovery cycles — each one a
timeout ladder plus a health wait — on a session that was never going to serve the read, because the
device's screen-capture pipeline had wedged underneath a runner whose HTTP server was fine. The
signal separating the two cases was present from the first cycle: a *recovered* runner that times
out again on the very next re-issue of the same idempotent call is not flapping, it is wedged. Three
minutes after the first timeout, the channel finally gave up and the pipeline's crash recovery took
over.

The pipeline's retry then did what
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
ships: it discarded the lease and respawned with a forced device erase — `simctl shutdown`, `erase`,
`boot`, reinstall. Three minutes later, the second attempt reproduced the first to the letter: the
same video-start confirmation timed out, the same screenshot cascade ran, and the same crash ended
it. The per-scenario recovery budget was spent, and the job failed:

```
scenario stable catalog smoke: backend crashed mid-run (attempt 2/3): runner channel GET /screenshot failed: the runner crashed mid-run and did not recover within 60s
##[error]backend crashed mid-run and did not recover within the 300s crash-recovery budget (spent respawning across 2 attempt(s)): ...
```

The erase rung was
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)'s
deliberate first choice, and it shipped as the crash retry's strongest remedy. The log above shows
the class of degradation that remedy cannot reach: an erase resets the device's data, but the wedge
lives in the device's capture services, and the erased device came back wedged. The only shipped
remedy that changes more than the device's data is
[BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)'s replacement
rung — a freshly created Simulator — and it is reachable only from a failed *cold spawn*, and there
only when the device has vanished from `simctl` entirely. A mid-run wedge whose respawn comes up
answering health meets neither condition.

One more shipped defense should have cut the waste and did not.
[BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
made the channel's recovery fail fast when the runner process is gone, precisely so a recovery that
cannot come is not waited out — but its probe reads the `xcodebuild` process handle alone.
[BE-0305](../BE-0305-driver-resilience-fault-injection/BE-0305-driver-resilience-fault-injection.md)'s
fault-injection measurements already recorded that blind spot on `main` — a killed test host does
not take the process-exited fast path, because `_runner_alive` reads the `xcodebuild` parent that
outlives it — and left "a tighter liveness check" to a separate item; this is that item. An
earlier investigation of this flake family examined four failed CI logs, all four alike: XCTest
restarted the in-Simulator test host after the crash and re-ran zero tests. The run is over and the
port will never bind again, yet `xcodebuild` lives on — so the probe keeps answering "alive" and
every recovery episode waits its full 60-second window. The captured runner output names the state
plainly — the same `Test Suite 'All tests'` markers the cold-spawn gate already string-matches — but
nothing on the mid-run path reads it.

## Detailed design

Four units. Units 1 and 2 are independent detection fixes in the runner channel's seam; unit 3 adds
the escalation rung the retry lacks; unit 4 connects the evidence layer's existing stall signal to
unit 3's rung choice. Nothing here touches the verdict: every unit reroutes or shortens
*infrastructure* recovery, and a scenario that keeps failing still fails loudly
([BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)'s
"flakiness is never tolerated by absorption" stance is unchanged).

1. **Classify a wedged automation session in the runner channel.** In
   `bajutsu/drivers/xcuitest.py`, `_with_crash_recovery` today re-issues an idempotent call after a
   confirmed health recovery, up to `_MAX_CRASH_RECOVERIES` consecutive crashes. Keep that loop for
   the failure it was built for, but split one case out of it: the same read **timing out again on
   consecutive re-issues** while health keeps answering. A timeout here means a call that reached
   the runner and hung — a shape the channel must tag distinctly, because the existing `delivered`
   tag ([BE-0207](../BE-0207-xcuitest-channel-transient-retry/BE-0207-xcuitest-channel-transient-retry.md))
   marks a mid-response connection reset the same way, and only the hang identifies the wedge. One
   healthy state shares the hang's surface for a while:
   [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
   serialized the runner's XCUITest operations while `/health` deliberately bypasses the
   serialization ("runner busy, not dead"), so a long operation can hold the lock while a concurrent
   read times out. The two states separate on the *second* post-recovery timeout of the same read: a
   single lock-holder cannot span that window, because its own call fails its own retry ladder
   first. A read still hanging then is wedged — the runner accepted it three times and answered
   none — so the channel raises the crash error at that point, with a distinct "wedged automation
   session" diagnostic, instead of riding out the remaining recovery cycles and the final health
   wait. A connection-level post-recovery failure (refused, or reset mid-response) keeps today's
   flap-riding behavior — that shape is the genuinely crashing runner the loop was built to ride
   out. The pipeline's device-level retry is the only remedy that can help a wedged session, so the
   channel's job is to hand over fast, not to absorb.

2. **Let the liveness probe read the captured output.** The environment already answers the
   channel's "is the runner process alive" probe — `_runner_alive`, the seam
   [BE-0323](../BE-0323-xcuitest-readiness-crash-respawn/BE-0323-xcuitest-readiness-crash-respawn.md)
   added — and already scans the runner's captured output for the run-ended markers
   (`_RUN_ENDED_MARKERS`, the `Test Suite 'All tests' failed` / `passed` lines) on the cold-spawn
   path (`_run_ended_probe`). Compose the two on the mid-run path: the probe reports the runner dead
   when the process has exited *or* the capture has *ever* shown the test run ended. The run-ended
   half **latches**, because the two sides pulse differently: `_run_ended_probe` is edge-triggered —
   it advances a private offset and reports a marker only from the read window that first contains
   it — while the mid-run predicate is level-triggered, re-asked once per recovery episode, so an
   unlatched composition would answer "dead" once and then "alive" again for every later episode.
   One latched probe per spawn serves the cold gate and the mid-run predicate alike: the cold-spawn
   instance already consumes this same capture, and a second, independent instance would race it
   for the marker. A run that printed
   its suite-ended line serves nothing afterwards — the state the earlier four-log investigation
   found `xcodebuild` outliving — so the recovery's health wait fails in the probe's next read
   instead of at its 60-second ceiling. The markers are `xcodebuild`'s own unlocalized output, and
   the cold gate has string-matched them since
   [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md);
   if a future Xcode rewords them the probe degrades to today's full wait, never to a false
   "dead".

3. **Escalate the second crash retry to a replacement device.**
   [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)'s
   forced erase stays the first rung: it is the cheaper remedy, and it recovers the app-data
   corruption class. But when a forced-erase retry itself ends in another backend crash, the next
   lease escalates: the run pipeline (`bajutsu/runner/pipeline.py`, `run_one`'s retry loop) requests
   a device replacement on the lease, and the XCUITest environment serves it with the same
   create-boot-prepare path
   [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)'s
   vanished-device rung uses — including its device-type and runtime cloning, its `bajutsu-recovered-*`
   naming, and the pool re-keying that follows the lease onto the new device. The degraded device is
   shut down and never freed back to the pool, which quarantines it exactly as a vanished device is
   quarantined today. The replacement lease also suppresses the forced erase for its own attempt: a
   freshly created device has nothing to erase, and honoring `erase=True` on it would pay a second
   shutdown-and-boot cycle for no state change. The escalation request is a no-op wherever
   replacement is wrong or impossible, and each such route keeps the strongest retry it has today. A
   real device and the live WebDriver endpoint keep the bare respawn — both reject an `erase`
   precondition outright, which is why `erase_precondition_supported` already excludes them from the
   erase rung. A run pinned to a concrete Simulator (`--udid`, or a config-pinned device) keeps the
   erase-level retry: a replacement would silently move the run off the device the operator named,
   and mint the per-run `bajutsu-recovered-*` residue whose cost
   [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) documents for
   exactly the pinned case. Every other platform ignores the request, so the tool and runner stay
   app- and platform-agnostic. The existing bounds are untouched — the replacement spends the same
   `crash_retries` attempt and the same per-scenario and per-run recovery budgets as the respawn it
   replaces — and they also bound reachability: under the default `crash_retries = 1` there is no
   third attempt, so this second-crash trigger fires only on a lane that raises the count (the iOS
   lane sets `BAJUTSU_CRASH_RETRIES: 2`), while a default run reaches the rung through unit 4's
   stall promotion alone. The default deliberately stays put — a second full scenario replay is a
   cost the metered on-device lanes opted into, not a new default. The rung changes behavior
   `docs/architecture.md` and `docs/run-loop.md` document (the crash retry's strongest remedy), so
   both pages and their `docs/ja/` mirrors move in the same change, per the BE-0113 norm.

4. **Let the video-start stall select the replacement rung.** The evidence layer already detects
   the wedge's earliest symptom: `start_video` confirms that the recording file grows within the
   `_VIDEO_START_TIMEOUT` ceiling
   ([BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md)
   proposes making it tunable as `BAJUTSU_VIDEO_START_TIMEOUT`), and logs a warning when it does
   not. Surface that outcome on the
   lease: when a scenario attempt whose video-start confirmation timed out then ends in a backend
   crash, the next retry escalates directly to unit 3's replacement rung, skipping the forced erase.
   The stall identifies the capture-pipeline degradation class the erase was observed not to clear,
   and the two remedies cost similar wall-clock anyway — an erase is a shutdown, erase, boot, and
   reinstall; a replacement is a create, boot, and install — so even a false-positive stall (a slow
   but healthy encoder) pays roughly the erase it skipped. The signal stays advisory to *recovery
   rung choice* only: it never fails an attempt on its own, and a run with video evidence disabled
   never produces it.

## Alternatives considered

- **Skip video evidence on retry attempts instead of reading the stall as a signal.** Dropping
  `recordVideo` from a retry would remove one load source from a degraded device, but the screenshot
  path rides the same capture services, so the retry would still hang exactly as observed — and the
  retry's video is precisely the evidence a human needs when the retry fails too. Unit 4 extracts
  the same information from the stall at no evidence cost.
- **A session nonce in the runner's health reply instead of reading the captured output (unit 2).**
  A restarted test host that re-serves would answer with a fresh nonce, which cleanly distinguishes
  "recovered" from "replaced". Rejected for the same reason
  [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) rejected a
  Swift-side launch retry: the fix would ship in the Swift package, so every lane needs a rebuilt
  runner to pick it up, while the captured-output probe lands in the logic core and reaches every
  lane at once. And the nonce cannot cover the measured failure mode at all — a host that re-runs
  zero tests never serves a nonce to compare.
- **Replace the device on the first crash retry, skipping the erase rung.** The wedge class unit 4
  identifies does justify going straight to replacement — but only when the stall signal is present.
  An unconditional first-retry replacement would mint a `bajutsu-recovered-*` device for every
  one-off crash, and on a developer's Mac those accumulate
  ([BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md)'s own
  alternatives weigh that residue). The erase rung also recovers the app-data corruption class at no
  residue, so it stays first when no stall was seen.
- **Restart the CoreSimulator daemon as a recovery rung.** Measured during the earlier
  investigation of this flake family: a daemon restart is fast (about one second) and
  non-destructive, but it matched zero of the nine CI failure logs examined, and no upstream
  runner-images report recommends it. Left out; the only defensible slot found was as an escalation
  after a `simctl shutdown` hang, which is a narrower follow-up than this item.
- **Raise the budgets and ceilings further.** The lane already raised its job timeouts to 60
  minutes, and the fully logged occurrence ran with its video-start ceiling already raised to 20
  seconds — pull request [#1538](https://github.com/bajutsu-e2e/bajutsu/pull/1538)'s lane setting,
  through the override
  [BE-0348](../BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md)
  proposes; it shows where the time actually goes: into recovery layers proving a dead end. More budget buys more silence —
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

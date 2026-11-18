**English** · [日本語](BE-0361-ios-ci-simulator-diagnostics-ja.md)

# BE-0361 — Collect the layered diagnostics an iOS CI failure needs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0361](BE-0361-ios-ci-simulator-diagnostics.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0361") |
| Implementing PR | [#1619](https://github.com/bajutsu-e2e/bajutsu/pull/1619) |
| Topic | CI / build infrastructure |
| Related | [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md) |
<!-- /BE-METADATA -->

## Introduction

The iOS lanes of continuous integration (CI) on GitHub Actions keep failing with mid-run crashes of the resident XCUITest runner. The most visible are the `run` and `actuation` jobs of `.github/workflows/ios-e2e.yml`. The artifacts CI uploads today cannot say why the runner crashed.

The runner is an XCTest host. `bajutsu` spawns it on the macOS runner with `xcodebuild test-without-building` (`bajutsu/platform_lifecycle/environments/xcuitest.py`). It drives the runner over a loopback Hypertext Transfer Protocol (`HTTP`) channel.

When the runner exits mid-run, the pipeline respawns it. The retry runs under the crash-recovery budgets that [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) added.

Those budgets made a degrading run fail with an error instead of hanging until `timeout-minutes`. They cannot answer the question that decides the fix: *what* killed the runner.

This proposal adds a layered diagnostics collection, so the next failing run carries the evidence its own root-cause analysis needs. The layers sit in three places:

- inside `bajutsu`
- in the Simulator and CoreSimulator
- on the macOS host

Everything lands under `runs/`, which the consuming on-device jobs already upload. Nothing touches the deterministic verdict path.

## Motivation

The captured runner logs ([BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md)'s capture, retained under `runs/runner-logs/`) already narrowed the failing runs of 2026-08-12 down to one signature:

1. The runner's own XCTest failure reads `Failed to get screenshot: Timed out while requesting screenshot.` A sibling is `cannot request screenshot data because it has an empty frame`, and another is `Lost connection to the application`.
2. The test method then ends, and `xcodebuild` exits with code 65.
3. The Python side sees `GET /screenshot` time out, then `Connection refused`. It declares a mid-run crash.

Two observations point past `bajutsu`'s own code and into the platform underneath it:

- **The video pipeline is down.** `recordVideo produced no new bytes` fires for **every** scenario on these runners, in green runs as much as red ones. The Simulator's video pipeline never produces a byte on GitHub Actions. The screenshot service that fails hard in red runs is degraded in green runs too.
- **The OS-side artifact is empty.** The existing `Collect crash diagnostics` step of `.github/actions/bajutsu-e2e/action.yml` collects zero files every time. No process crashed, so the one OS-side artifact CI gathers today is empty by construction for this failure class.

Today's artifacts cannot distinguish which of four hypotheses holds:

1. The Simulator's render or screenshot service wedges. This is the front-runner, given the video pipeline that is down.
2. The virtualized macOS host starves the Simulator of processor time or memory when the stall hits.
3. The app process itself is terminated. The `empty frame` and `Lost connection` variants hint at this.
4. The failures cluster on a particular runner image or hardware generation.

Each hypothesis names evidence that exists at failure time and is gone by the time a human looks:

- the XCTest result bundle that the runner's `xcodebuild` writes (its path is printed in the runner log and then discarded; only the `codegen` job uploads an `.xcresult` today)
- the unified log entries of the processes that serve screenshots
- the host's load at the stall
- thread samples of the wedged processes

Collecting this evidence is cheap next to one more undiagnosable red run on a 10x-billed macOS runner.

## Detailed design

The collection has three layers. The first layer lives in `bajutsu`, because only the running process knows *when* a stall happens and *which* spawn failed. The other two live in CI, because they read host state that `bajutsu` has no business touching. Every layer writes under `runs/`, so the existing `Upload run artifacts` steps carry the results with no new upload wiring.

### Layer 1: what only `bajutsu` can capture

**The runner's XCTest result bundle.** The new environment variable `BAJUTSU_XCUITEST_RESULT_BUNDLES` can name a directory. In that case, `_spawn_runner` (`bajutsu/platform_lifecycle/environments/xcuitest.py`) gains a `-resultBundlePath` argument. It writes one bundle per spawn, keyed by the runner port. The runner log already uses the same keying, so a respawn never overwrites its predecessor's bundle. The bundle records what testmanagerd itself saw: the precise failure, its timestamps, and any attachments.

Retention follows from the variable. The variable that names the directory *is* the operator asking for the bundles. Every bundle is kept, as with an explicit `BAJUTSU_XCUITEST_RUNNER_LOG` directory. BE-0319 prunes its *default*, env-unset capture. This feature has no such default.

One honest limit applies. `xcodebuild` finalizes the bundle when the test run ends, which is what happens on the code-65 failures above. A runner that `bajutsu` kills mid-hang may leave a truncated bundle. The collection is best-effort there.

**A stall-time probe.** Two moments trigger the probe:

- The channel declares a mid-run crash (`bajutsu/drivers/xcuitest.py`, where the transient-retry budget is exhausted).
- The video watcher logs `recordVideo produced no new bytes` (`bajutsu/evidence/intervals.py`).

At either moment, the state that would answer hypothesis (1) versus (2) still exists and is about to vanish. When the new environment variable `BAJUTSU_STALL_DIAGNOSTICS` names a directory, both trigger points run a bounded, best-effort capture into it. The capture has three parts:

- a timed `xcrun simctl io <udid> screenshot`. If simctl's own path also stalls, the render service is wedged, not the runner.
- `sample` of the runner host process and the Simulator's screenshot-serving processes
- a `ps aux` plus `vm_stat` snapshot

Each command carries a short subprocess timeout, and the probe swallows failures. Captures are capped per run, covering only the first few stalls, so a crash-looping run cannot fill the disk or the wall clock. When both variables are unset, behavior stays as today. The hooks are CI's to opt into.

### Layer 2: Simulator and CoreSimulator state, from CI

A new composite action, `.github/actions/collect-ios-diagnostics`, owns the OS-side collection. The seven Simulator-driving macOS jobs then do not each grow their own shell steps. It replaces the `Collect crash diagnostics` step inside `bajutsu-e2e/action.yml`. It is also wired into the jobs that do not run through that action: `conformance`, `fault-injection`, and `visual`.

`codegen` is the one remaining macOS job that boots a Simulator, and it stays out. It uploads only its `.xcresult` and has no `runs/` artifact for the collection to ride. The action runs in two tiers:

- **Always** (cheap, every run):
  - Copy `~/Library/Logs/CoreSimulator/CoreSimulator.log` and the booted device's `~/Library/Logs/CoreSimulator/<UDID>/` directory.
  - Widen the crash-report sweep from `*.ips` / `*.crash` to `*.diag`, `*.spin`, `*.hang`, and `JetsamEvent*`. Add the system-wide `/Library/Logs/DiagnosticReports`.
  - Record an environment snapshot. It holds the output of these commands:
    - `sw_vers`
    - `sysctl hw.model hw.ncpu hw.memsize kern.hv_vmm_present`
    - `system_profiler SPDisplaysDataType`
    - `xcodebuild -version`
    - `xcrun simctl list -j`

    The snapshot is the cross-run key that hypothesis (4) needs.
- **On failure only** (heavy):
  - Run `xcrun simctl diagnose` with a timeout. It is Apple's own collector for CoreSimulator and device state.
  - Take targeted unified-log extracts via `log show --last <window> --predicate`. They cover the processes that serve rendering and screenshots: `backboardd`, `SpringBoard`, `testmanagerd`, and the CoreSimulator service.
  - Inside `bajutsu-e2e/action.yml`, this tier is gated on the run step's own outcome. In the pytest lanes, the caller gates it on the run step's outcome too, since `failure()` is rejected in a `with:` value.

Implementation measured five things this design had wrong. Each would have shipped a collector that quietly gathers nothing. That is the very failure the proposal exists to end.

- **The guest does not log to the host.** The claim above that "Simulator guest processes write to the host's unified log" is false. On a booted device, a host-side `log show` filtered on `SpringBoard`, `backboardd`, and `testmanagerd` returns its header and no entries. Those entries live in the device's own log store. The collection runs **two** extracts:
  - One on the host, for the CoreSimulator service processes. Their process name is `com.apple.CoreSimulator.CoreSimulatorService`, not `CoreSimulatorService`, and their subsystem needs a `CONTAINS` match.
  - One inside the guest, through `simctl spawn`.

  Measured over a 30-minute window, the host extract returned 34.5k lines and the guest extract 11.4k lines, about a second each.
- **`simctl diagnose` blocks on stdin and deletes its `--output` directory.** It prints a consent notice and waits for a newline. A CI step's stdin is `/dev/null`, where it reads EOF and exits 0 having collected nothing. It also treats `--output` as the archive's *base path*. It replaces that directory with `<path>.tar.gz` and deletes whatever else was in it, so pointing it at the collection directory would delete the rest of the collection. Measured at ~15s and 22–78MB for one booted device. Its flags additionally require the `--flag=value` form.
- **`CoreSimulator.log` is unbounded.** Copying it wholesale is a 183MB artifact on a long-lived host, on the *always* tier. The collection tails it instead.
- **One shared capture budget would have starved the trigger that matters.** The Motivation above records that `recordVideo produced no new bytes` fires for every scenario, green runs included. A job runs a dozen scenario documents in one `bajutsu run` process. A single per-process cap of three would be spent on the third scenario's video warning, so the runner crash finds nothing left — and that crash is the failure the capture exists to explain, as well as the only trigger that reaches the capture with the device's id. The budget is per trigger. A declined capture gets a warning rather than a debug line. The lane installs no log handler, and a debug line is no record at all.
- **A capture is charged to a clock it does not own.** BE-0353's `CrashRecoveryBudget` is a wall-clock deadline. It is set at the *first* crash and re-checked at each later one. Every second a capture spends is a second the recovery no longer has. The first CI run of this change showed it. A `visual` job missed its 300s recovery deadline by 75s. Two captures of up to 30s fell inside that window. A diagnostic turned a recoverable degradation into a failed run, which an observer may not do. The per-capture budget is 10s against that lane's 300s. The screenshot ceiling is 5s. The runner's own read timeout is 15s, so a `simctl` path unanswered at 5s has already answered the question. A test pins the relationship against the lane's configured budget rather than the bare number.

Two smaller shapes follow from the same measurements:

- `xcodebuild` refuses to start when its `-resultBundlePath` already exists. Unit 1 clears a leftover at its own key first. Otherwise a recycled ephemeral port would turn the diagnostics into a spawn failure.
- The `start` phase lives inside `bajutsu-e2e/action.yml` rather than in each calling job. The `bundled-runner` job invokes that action twice, and a job-level start would be stopped by the first invocation's `collect`.

### Layer 3: host telemetry over time

A stall correlates with host starvation only if the host's load is on record when the stall happens. The same composite action gains a `start` phase, which the consumer jobs call right after `bootstatus`. The phase does two things:

- It launches a background sampler. The sampler is an interval loop that appends `top`, `vm_stat`, and `memory_pressure` output to `runs/diagnostics/host-telemetry.log` every ~20 seconds.
- It runs a one-shot render-pipeline probe: a timed `simctl io screenshot` plus a five-second `recordVideo`. The byte count answers, per job, whether the video pipeline was down from the start.

The `collect` phase stops the sampler. The sampler is an observer off the verdict path. Its interval is a sampling cadence, not a wait, so prime directive 2's ban on fixed sleeps in the run loop is untouched.

### Work breakdown (`MECE`)

Mutually exclusive, collectively exhaustive (`MECE`) units of work follow.

1. **Result bundles.** Add `-resultBundlePath` in `_spawn_runner` behind `BAJUTSU_XCUITEST_RESULT_BUNDLES`. Use the runner log's per-port keying, and keep every bundle (the variable is the operator asking for them). `ios-e2e.yml` points the variable under `runs/runner-logs/`.
2. **Stall-time probe.** Add the bounded capture module behind `BAJUTSU_STALL_DIAGNOSTICS`. Wire its two trigger points (channel crash declaration, video no-bytes warning) and the per-run capture cap. Add the opt-in in `ios-e2e.yml`.
3. **The `collect-ios-diagnostics` composite action.** Build the always tier, the failure tier, and the `start` phase. Replace the `Collect crash diagnostics` step in `bajutsu-e2e/action.yml`. Wire the action into `conformance`, `fault-injection`, and `visual`.
4. **Docs.** Add a diagnostics section to `docs/ci.md` and its `docs/ja/` mirror. It covers the tiers, where each artifact lands in `runs/`, and how to read them against the four hypotheses.
5. **Tests.** The tests cover three things:
   - Unit tests for the two `bajutsu` hooks. The spawn argv gains `-resultBundlePath` when the variable is set, and not otherwise. The probe is a no-op when unset, and bounded and best-effort when set.
   - `tests/test_e2e_changes.py` names the new action in the iOS lane's positive path list. Without that, a change to the action would fire no lane at all.
   - The composite action's own shell. This unit first assumed that static gates cover that shell, and they do **not**. `actionlint` lints `.github/workflows/` only, and reports a missing `jobs` section when pointed at an `action.yml`. `make lint-sh` runs `shellcheck` over a named list of `.sh` files that reaches nothing under `.github/actions/`.

   That gap shipped a defect. The action's watchdog SIGKILLed a hung `simctl` screenshot. Then `wait` returned the child's 137. Actions itself puts an errexit on the shell line: `bash --noprofile --norc -e -o pipefail`. A script's own `set -uo pipefail` does not clear it. The errexit failed the step, skipped the real test step, and reddened five iOS jobs.

   The tests run the action's own `run:` blocks under that interpreter and those flags. They stub `xcrun` to hang (`tests/test_collect_ios_diagnostics_action.py`). They pin the one property that matters: a diagnostics step cannot fail a job. Writing them found a second unbounded call (`simctl list` on the *always* tier). On a wedged CoreSimulator, it would have hung every job. Closing the static-lint gap for every composite action is still its own follow-up.

### Prime directives preserved

- **AI never judges.** Every layer collects evidence. No model call enters any path this proposal touches, and no collected artifact feeds a verdict.
- **Determinism first.** The probes are bounded subprocess calls with timeouts, not sleeps. The telemetry sampler observes from outside the run loop. Pass/fail follows the same rule as before.
- **App-agnostic.** Nothing here reads the target app. The hooks key on the device and the runner process, and the CI action keys on the host. No per-target branching exists anywhere.

## Alternatives considered

- **Raising `BAJUTSU_LOG_LEVEL` instead of collecting OS-side state.** Rejected as the primary measure. The failing layer is the Simulator's screenshot service, outside `bajutsu`'s process, so no amount of `bajutsu`-side verbosity records it. More logging may still ride along, but it cannot substitute for layers 2 and 3.
- **A full `log collect` archive, or `sysdiagnose`, on failure.** Deferred. A `.logarchive` of the whole window runs to hundreds of megabytes per job. `sysdiagnose` takes minutes to produce gigabytes. The targeted `log show` extracts cover the processes the failure signature already names. Revisit only if the extracts prove insufficient.
- **Streaming the unified log for the whole job.** Rejected. The per-step `device.log` interval evidence already streams the guest log during scenarios. A retrospective `log show` at failure time yields the same host-side entries without a long-lived extra process.
- **Making the runner survive a screenshot failure instead of diagnosing it.** Out of scope by design. Routing `/screenshot` around the runner (for example through `simctl io screenshot`) or making the failure non-fatal in Swift is a *fix*. Choosing one before the diagnostics confirm the cause would be guessing. A follow-up proposal owns the fix once the evidence is in.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — result bundles per runner spawn
- [x] Unit 2 — stall-time probe hook
- [x] Unit 3 — the `collect-ios-diagnostics` composite action and its wiring
- [x] Unit 4 — docs (`docs/ci.md` and its `ja` mirror)
- [x] Unit 5 — tests

## References

- [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md) —
  the runner-output capture this proposal's result bundles and retention policy extend
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) /
  [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md) —
  the crash-recovery budgets and wedge detection that made these failures loud but not yet
  diagnosable
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) — the
  between-attempt device repair; the collected evidence should show whether its reboot and
  replacement rungs are needed
- [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md) — the video-anchor
  correction whose `recordVideo` no-bytes warning is one of this proposal's two stall triggers
- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md) —
  the Simulator lane's flakiness history
- [`bajutsu/platform_lifecycle/environments/xcuitest.py`](../../bajutsu/platform_lifecycle/environments/xcuitest.py) —
  `_spawn_runner`, the seam Unit 1 extends
- [`bajutsu/drivers/xcuitest.py`](../../bajutsu/drivers/xcuitest.py) — the channel whose crash
  declaration is Unit 2's first trigger
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py) — the video watcher whose
  no-bytes warning is Unit 2's second trigger
- [`.github/actions/bajutsu-e2e/action.yml`](../../.github/actions/bajutsu-e2e/action.yml) — the
  crash-diagnostics step Unit 3 replaces
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) — the lane whose jobs opt
  into every layer

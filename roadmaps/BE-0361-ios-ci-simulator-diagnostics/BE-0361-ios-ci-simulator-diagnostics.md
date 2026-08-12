**English** · [日本語](BE-0361-ios-ci-simulator-diagnostics-ja.md)

# BE-0361 — Collect the layered diagnostics an iOS CI failure needs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0361](BE-0361-ios-ci-simulator-diagnostics.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0361") |
| Topic | CI / build infrastructure |
| Related | [BE-0319](../BE-0319-xcuitest-cold-spawn-resilience/BE-0319-xcuitest-cold-spawn-resilience.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md), [BE-0346](../BE-0346-video-timing-sync/BE-0346-video-timing-sync.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md) |
<!-- /BE-METADATA -->

## Introduction

The iOS lanes of continuous integration (CI) on GitHub Actions — most visibly the `run` and
`actuation` jobs of `.github/workflows/ios-e2e.yml` — keep failing with mid-run crashes of the
resident XCUITest runner, and the artifacts CI uploads today cannot say why. The runner is an
XCTest host that `bajutsu` spawns on the macOS runner with `xcodebuild test-without-building`
(`bajutsu/platform_lifecycle/environments/xcuitest.py`) and drives over a loopback Hypertext
Transfer Protocol (`HTTP`) channel; when it dies mid-run, the pipeline respawns it and retries under
the crash-recovery budgets [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
added. Those budgets made a degrading run fail loudly instead of hanging to `timeout-minutes`, but
the question they cannot answer is the one that decides the fix: *what* killed the runner. This
proposal adds a layered diagnostics collection — inside `bajutsu`, from the Simulator and
CoreSimulator, and from the macOS host — so the next failing run carries the evidence its own
root-cause analysis needs. Everything lands under `runs/`, which the consuming on-device jobs
already upload, and nothing touches the deterministic verdict path.

## Motivation

The captured runner logs (BE-0319's capture, retained under `runs/runner-logs/`) already narrowed
the failing runs of 2026-08-12 down to one signature: the runner's own XCTest failure reads
`Failed to get screenshot: Timed out while requesting screenshot.` — or a sibling,
`cannot request screenshot data because it has an empty frame` and
`Lost connection to the application` — after which the test method ends, `xcodebuild` exits with
code 65, and the Python side sees `GET /screenshot` time out, then `Connection refused`, and
declares a mid-run crash. Two observations point past `bajutsu`'s own code and into the platform
underneath it. First, `recordVideo produced no new bytes` fires for **every** scenario on these
runners, in green runs as much as red ones — the Simulator's video pipeline never produces a byte
on GitHub Actions, so the screenshot service that fails hard in red runs is degraded in all of
them. Second, the existing `Collect crash diagnostics` step of
`.github/actions/bajutsu-e2e/action.yml` collects zero files every time: no process crashed, so
the one OS-side artifact CI gathers today is empty by construction for this failure class.

What today's artifacts cannot distinguish is which of four hypotheses holds: (1) the Simulator's
render or screenshot service wedges (the front-runner, given the dead video pipeline); (2) the
virtualized macOS host starves the Simulator of processor time or memory when the stall hits;
(3) the
app process itself is killed, which the `empty frame` and `Lost connection` variants hint at; or
(4) the failures cluster on a particular runner-image or hardware generation. Each hypothesis
names evidence that exists at failure time and is gone by the time a human looks: the XCTest
result bundle the runner's `xcodebuild` writes (its path is printed in the runner log and then
discarded — only the `codegen` job uploads an `.xcresult` today), the unified log entries of the
processes that serve screenshots, the host's load at the stall, and thread samples of the wedged
processes. Collecting them is cheap next to one more undiagnosable red run on a 10x-billed macOS
runner.

## Detailed design

The collection is three layers. The first layer lives in `bajutsu` because only the running
process knows *when* a stall happens and *which* spawn failed; the other two live in CI because
they read host state `bajutsu` has no business touching. Every layer writes under `runs/`, so the
existing `Upload run artifacts` steps carry the results with no new upload wiring.

### Layer 1: what only `bajutsu` can capture

**The runner's XCTest result bundle.** `_spawn_runner`
(`bajutsu/platform_lifecycle/environments/xcuitest.py`) gains a `-resultBundlePath` argument when
the new environment variable `BAJUTSU_XCUITEST_RESULT_BUNDLES` names a directory, writing one
bundle per spawn keyed by the runner port — the same keying the runner log already uses, so a
respawn never overwrites its predecessor's bundle. The bundle records what testmanagerd itself saw:
the precise failure, its timestamps, and any attachments. Retention: the variable naming the
directory *is* the operator asking for the bundles, so — as with an explicit
`BAJUTSU_XCUITEST_RUNNER_LOG` directory — every bundle is kept. BE-0319 prunes only its *default*,
env-unset capture, and this feature has no such default. One honest limit: `xcodebuild`
finalizes the bundle when the test run ends, which is exactly what happens on the code-65 failures
above, but a runner `bajutsu` kills mid-hang may leave a truncated bundle — the collection is
best-effort there.

**A stall-time probe.** The moment the channel declares a mid-run crash
(`bajutsu/drivers/xcuitest.py`, where the transient-retry budget is exhausted) or the video
watcher logs `recordVideo produced no new bytes` (`bajutsu/evidence/intervals.py`), the state that
would answer hypothesis (1) versus (2) exists and is about to vanish. When the new environment
variable `BAJUTSU_STALL_DIAGNOSTICS` names a directory, those two trigger points run a bounded,
best-effort capture into it: a timed `xcrun simctl io <udid> screenshot` (if simctl's own path
also stalls, the render service is wedged, not the runner), `sample` of the runner host process
and the Simulator's screenshot-serving processes, and a `ps aux` plus `vm_stat` snapshot. Each
command carries a short subprocess timeout, failures are swallowed, and captures are capped per
run (first few stalls only) so a crash-looping run cannot fill the disk or the wall clock. Unset,
both variables leave behavior exactly as today — the hooks are CI's to opt into.

### Layer 2: Simulator and CoreSimulator state, from CI

A new composite action, `.github/actions/collect-ios-diagnostics`, owns the OS-side collection so
the seven Simulator-driving macOS jobs do not each grow their own shell steps. It replaces the
`Collect crash diagnostics` step inside `bajutsu-e2e/action.yml` and is also wired into the jobs
that do not run through that action (`conformance`, `fault-injection`, `visual`); `codegen`, the
one remaining macOS job that boots a Simulator, stays out, because it uploads only its `.xcresult`
and has no `runs/` artifact for the collection to ride. The action runs in two tiers:

- **Always** (cheap, every run): copy `~/Library/Logs/CoreSimulator/CoreSimulator.log` and the
  booted device's `~/Library/Logs/CoreSimulator/<UDID>/` directory; widen the crash-report sweep
  from `*.ips` / `*.crash` to `*.diag`, `*.spin`, `*.hang`, and `JetsamEvent*`, and add the
  system-wide `/Library/Logs/DiagnosticReports`; and record an environment snapshot — `sw_vers`,
  `sysctl hw.model hw.ncpu hw.memsize kern.hv_vmm_present`, `system_profiler SPDisplaysDataType`,
  `xcodebuild -version`, and `xcrun simctl list -j` — the cross-run key hypothesis (4) needs.
- **On failure only** (heavy): `xcrun simctl diagnose` with a timeout, Apple's own collector for
  CoreSimulator and device state; and targeted unified-log extracts via `log show --last <window>
  --predicate` for the processes that serve rendering and screenshots — `backboardd`,
  `SpringBoard`, `testmanagerd`, `CoreSimulatorService`, and the `com.apple.CoreSimulator`
  subsystem. Simulator guest processes write to the host's unified log, so these extracts cover
  both sides of the virtualization boundary. Inside `bajutsu-e2e/action.yml` the tier is gated on
  the run step's own outcome; in the pytest lanes the caller gates it with `if: failure()`.

### Layer 3: host telemetry over time

A stall correlates with host starvation only if the host's load is on record when the stall
happens, so the same composite action gains a `start` phase the consumer jobs call right after
`bootstatus`: it launches a background sampler (an interval loop appending `top`, `vm_stat`, and
`memory_pressure` output to `runs/diagnostics/host-telemetry.log` every ~20 seconds) and runs a
one-shot render-pipeline probe — a timed `simctl io screenshot` plus a five-second `recordVideo`
whose byte count answers, per job, whether the video pipeline was dead from the start. The
`collect` phase stops the sampler. The sampler is an observer off the verdict path; its interval
is a sampling cadence, not a wait, so prime directive 2's ban on fixed sleeps in the run loop is
untouched.

### Work breakdown (`MECE`)

Mutually exclusive, collectively exhaustive (`MECE`) units of work follow.

1. **Result bundles.** `-resultBundlePath` in `_spawn_runner` behind
   `BAJUTSU_XCUITEST_RESULT_BUNDLES`, with the runner log's per-port keying and every bundle
   kept (the variable is the operator asking for them); `ios-e2e.yml` points it under
   `runs/runner-logs/`.
2. **Stall-time probe.** The bounded capture module behind `BAJUTSU_STALL_DIAGNOSTICS`, its two
   trigger points (channel crash declaration, video no-bytes warning), the per-run capture cap,
   and the `ios-e2e.yml` opt-in.
3. **The `collect-ios-diagnostics` composite action.** The always tier, the failure tier, and the
   `start` phase; replacing the `Collect crash diagnostics` step in `bajutsu-e2e/action.yml`;
   wiring into `conformance`, `fault-injection`, and `visual`.
4. **Docs.** A diagnostics section in `docs/ci.md` and its `docs/ja/` mirror: the tiers, where
   each artifact lands in `runs/`, and how to read them against the four hypotheses.
5. **Tests.** Unit tests for the two `bajutsu` hooks — the spawn argv gains `-resultBundlePath`
   exactly when the variable is set, the probe is a no-op when unset, bounded and best-effort when
   set; `make lint-actions` / `actionlint` cover the new composite action, and
   `tests/test_e2e_changes.py` is extended if the positive path list must name it.

### Prime directives preserved

- **AI never judges.** Every layer collects evidence; no model call enters any path this proposal
  touches, and no collected artifact feeds a verdict.
- **Determinism first.** The probes are bounded subprocess calls with timeouts, not sleeps; the
  telemetry sampler observes from outside the run loop. Pass/fail is decided exactly as before.
- **App-agnostic.** Nothing here reads the target app: the hooks key on the device and the runner
  process, and the CI action on the host — no per-target branching anywhere.

## Alternatives considered

- **Raising `BAJUTSU_LOG_LEVEL` instead of collecting OS-side state.** Rejected as the primary
  measure: the failing layer is the Simulator's screenshot service, outside `bajutsu`'s process,
  so no amount of `bajutsu`-side verbosity records it. More logging may still ride along, but it
  cannot substitute for layers 2 and 3.
- **A full `log collect` archive, or `sysdiagnose`, on failure.** Deferred: a `.logarchive` of the
  whole window runs to hundreds of megabytes per job and `sysdiagnose` takes minutes to produce
  gigabytes, while the targeted `log show` extracts cover the processes the failure signature
  already names. Revisit only if the extracts prove insufficient.
- **Streaming the unified log for the whole job.** Rejected: the per-step `device.log` interval
  evidence already streams the guest log during scenarios, and a retrospective `log show` at
  failure time yields the same host-side entries without a long-lived extra process.
- **Making the runner survive a screenshot failure instead of diagnosing it.** Out of scope by
  design: routing `/screenshot` around the runner (for example through `simctl io screenshot`) or
  making the failure non-fatal in Swift is a *fix*, and choosing one before the diagnostics
  confirm the cause would be guessing. A follow-up proposal owns the fix once the evidence is in.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — result bundles per runner spawn
- [ ] Unit 2 — stall-time probe hook
- [ ] Unit 3 — the `collect-ios-diagnostics` composite action and its wiring
- [ ] Unit 4 — docs (`docs/ci.md` and its `ja` mirror)
- [ ] Unit 5 — tests

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

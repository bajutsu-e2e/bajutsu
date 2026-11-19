**English** · [日本語](BE-XXXX-simslim-ci-simulator-slimming-ja.md)

# BE-XXXX — Slim the CI Simulator with simslim to speed up and steady the iOS E2E lane

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-simslim-ci-simulator-slimming.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Rejected** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | CI / build infrastructure |
| Related | [BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md), [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md), [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md), [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md) |
<!-- /BE-METADATA -->

## Introduction

> **Rejected — a measured experiment showed no speedup.** On GitHub's macOS runner, a slimmed
> Simulator booted 15–43 seconds slower than a stock one. The gating scenarios ran no faster. The
> one benefit left, steadier runs, rests on a single slow stock run out of six. We keep this item as
> the record of that experiment, so a later proposal starts from its numbers.

This item proposed running [simslim](https://github.com/mobai-app/simslim) before each iOS
Simulator boot in the on-device E2E lane (`.github/workflows/ios-e2e.yml`). simslim is a
third-party command-line tool for macOS. It disables background launchd services inside one
Simulator, such as Siri, Spotlight, photo analysis, and iCloud sync.

The proposal expected two gains: a faster boot and run, and fewer runs stalled by a starved host.
We built a throwaway workflow that compared a stock and a slimmed Simulator, three runs each. The
results rejected the speed claim and left the stability claim unproven.

## Motivation

The iOS E2E lane is the slowest and least stable part of this repository's CI. Each on-device job
boots a fresh Simulator on a GitHub-hosted macOS runner. A cold boot takes about a minute. Several
roadmap items exist because a loaded runner can wedge the Simulator mid-run:

- [BE-0218](../BE-0218-e2e-simulator-flaky-readiness-actuation/BE-0218-e2e-simulator-flaky-readiness-actuation.md)
  stabilized readiness and bounded the actuation timeout.
- [BE-0344](../BE-0344-xcuitest-device-recovery/BE-0344-xcuitest-device-recovery.md) repairs the
  Simulator between XCUITest cold-spawn attempts.
- [BE-0354](../BE-0354-xcuitest-wedge-fastfail-device-replacement/BE-0354-xcuitest-wedge-fastfail-device-replacement.md)
  detects a wedged session fast and replaces the device.

simslim's README reports that a stock iOS Simulator runs about 250 processes and uses about 4 GB of
memory. A slimmed one runs about 70 processes and uses about 0.9 GB. A GitHub macOS runner has
about 7 GB of memory and three virtual CPUs. A Simulator that leaves more of both to `xcodebuild`
and the XCUITest host could plausibly boot faster, run faster, and wedge less often.

The observable outcome was a set of numbers in the workflow's summary table. One is the mean boot
and run time per variant. The other is the count of runs far slower than the rest. The experiment
below measured both.

## Detailed design

This section records the experiment that decided the item, not a design to build.

### How simslim slims a Simulator

simslim writes `launchctl disable` entries into the launchd store of one Simulator. The Simulator
reads that store at its next boot. On a shutdown device, simslim writes the entries offline and
boots once, already slim. The source of v0.15.0 confirms this path (`ensureOffline` in `slim.go`).
As a result, the boot count matches a stock boot. The Mac and other Simulators stay untouched.

### The experiment workflow

A throwaway workflow, `simslim-experiment.yml`, ran on branch `claude/nice-gates-jrrbly` and never
reached `main`. It had three jobs:

| Job | What it did |
|---|---|
| `build` | Built the SwiftUI showcase app and the XCUITest runner once |
| `measure` | Ran a matrix of two variants (`stock`, `slim`) by three attempts, six jobs in parallel |
| `summarize` | Collected each job's timings into one table |

Each `measure` job mirrored the gating `run` job of `ios-e2e.yml` as it stood at commit `303c491`.
It ran that job's nine scenario files with the same XCUITest environment variables. The job has
listed ten files on `main` since then. One deliberate difference made the boot measurable:
the job booted the Simulator synchronously and timed it, instead of overlapping the boot with the
download ([BE-0088](../BE-0088-overlap-simulator-boot/BE-0088-overlap-simulator-boot.md)).

Both variants installed simslim through `brew install mobai-app/tap/simslim`. The `slim` variant
alone ran `simslim on <udid>` on the shutdown device in place of `xcrun simctl boot`. It kept
simslim's default profile, with no category exempted. The `stock` variant
used `simslim measure` alone, to report its memory footprint on the same scale.

The runner was the `macos-26-arm64` image with Xcode 26.6.

### Results

[Run 44284086371](https://github.com/bajutsu-e2e/bajutsu/actions/runs/44284086371) produced these
numbers. Times are in seconds. "Footprint" is simslim's summed `phys_footprint` of the Simulator's
process tree, taken after the app install. "Host free" is `memory_pressure`'s free percentage at
the same moment.

| Variant | # | Boot | Install | Run | Result | Processes | Footprint | Host free |
|---|---|---|---|---|---|---|---|---|
| stock | 1 | 46 | 12 | 404 | pass | 250 | 4.14 GB | 79% |
| stock | 2 | 47 | 9 | 426 | pass | 210 | 3.20 GB | 82% |
| stock | 3 | 168 | 21 | 2,423 | pass | 192 | 2.95 GB | 54% |
| slim | 1 | 62 | 9 | 399 | pass | 79 | 1.13 GB | 65% |
| slim | 2 | 89 | 15 | 495 | pass | 79 | 1.13 GB | 64% |
| slim | 3 | 62 | 9 | 369 | pass | 79 | 1.13 GB | 65% |

Every run passed all nine scenario files, the slimmed ones included.

### Why the item is rejected

The speed claim failed on the measured numbers:

- **Boot.** A slimmed boot took 62–89 seconds against 46–47 for a normal stock boot. The experiment
  timed the boot as one step, so it cannot say how much of that gap the override write accounts
  for. Either way, nothing in the boot wins the gap back.
- **Run.** The scenarios took 369–495 seconds slimmed against 404–426 stock. The spread within each
  variant is larger than any gap between them.

Applied across the six or more Simulator jobs in `ios-e2e.yml`, slimming would make every pull
request wait longer.

The stability claim stays unproven. Stock run 3 took 168 seconds to boot and 40 minutes to run, six
times a normal run. That runner started with 54% free memory against about 80% for the other stock
runs. The slow run fits the host starvation that BE-0218, BE-0344, and BE-0354 address. Even so,
one slow run in six cannot separate a slimming effect from a bad runner. The existing recovery
also carried stock run 3 to a pass.

Three costs weigh against adopting simslim without stronger evidence:

- **A private interface.** simslim writes CoreSimulator's undocumented launchd store. An Xcode or
  runner-image update could break it without notice.
- **An unpinned dependency.** The Homebrew tap installs its current release. The gating lane would
  depend on a third-party tool that changes outside this repository.
- **Lost services.** The default profile disables services some scenarios may need. Push delivery
  depends on `apsd`, which the `store` category turns off. A future scenario could fail for a
  reason that never appears in the scenario itself.

### What would make a new proposal worth writing

A new proposal should target stability, not speed. It needs evidence this experiment lacks. That
evidence is a larger sample, around ten runs per variant, with a threshold fixed before the run.
One example: stock runs show repeated outliers while slimmed runs show none. Even then, a pilot on
the lanes with a history of stalls would come before the whole workflow.

## Alternatives considered

- **Install simslim with `go install` at a pinned tag.** An earlier attempt in the same experiment
  pinned v0.15.0 through Homebrew's Go. It traded a one-line install for an exact version. The
  experiment kept the Homebrew tap to match the tool's documented install. Either way, the results
  above do not depend on the install method.
- **Slim a booted Simulator with `--no-reboot`.** That path keeps the first boot at stock cost and
  then slims the running session. It cannot shorten the boot at all, so it cannot rescue the speed
  claim.
- **Keep the stock Simulator and rely on the existing recovery.** This choice is the status quo.
  The experiment gives no reason to change it: stock run 3 still passed through that recovery.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Run the stock-versus-slim experiment on GitHub's macOS runner (run 44284086371)
- [ ] Not pursued: wire simslim into `.github/actions/boot-simulator` (rejected on the results)

## References

- [simslim](https://github.com/mobai-app/simslim) — the tool, its README, and its benchmarks.
- [Run 44284086371](https://github.com/bajutsu-e2e/bajutsu/actions/runs/44284086371) — the
  experiment run that produced the results table.
- [`simslim-experiment.yml` at commit `303c491`](https://github.com/bajutsu-e2e/bajutsu/blob/303c4912e0abd5c0beb6edeca5a3570c7dddf97c/.github/workflows/simslim-experiment.yml)
  — the experiment workflow, as it ran.

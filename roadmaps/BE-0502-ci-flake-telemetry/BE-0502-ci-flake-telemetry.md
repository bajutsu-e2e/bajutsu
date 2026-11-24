**English** · [日本語](BE-0502-ci-flake-telemetry-ja.md)

# BE-0502 — Measure every on-device CI job so a recurring flake can be traced to its cause

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0502](BE-0502-ci-flake-telemetry.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0502") |
| Implementing PR | [#2505](https://github.com/bajutsu-e2e/bajutsu/pull/2505) |
| Topic | CI / build infrastructure |
| Related | [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md), [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md), [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md), [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md) |
<!-- /BE-METADATA -->

## Introduction

The on-device end-to-end (E2E) jobs in continuous integration (CI) fail intermittently on pull
requests whose changes cannot explain the failure. The iOS jobs drive a Simulator through XCUITest.
The Android jobs drive an emulator through adb. A re-run usually turns the job green, and the cause
stays unknown.

We add one machine-readable record per job, written on green jobs as well as red ones. A script
then joins those records across runs and compares the red jobs with the green ones. The comparison
names which candidate cause each red job shares with other red jobs and not with green jobs.

## Motivation

One recent failure shows the shape of the problem. In the `actuation (xcuitest)` job, the first
`wait` of a scenario timed out after 20 seconds. In the same window, two runner-channel reads
(`GET /elements`) timed out and retried. Every scenario's `recordVideo` also failed to report its
start within five seconds. Nothing in the change under test touched any of those paths.

The evidence for each of those symptoms already exists, but each piece answers one question about
one job:

| Evidence | Where it lives today | What it cannot answer |
|---|---|---|
| Run manifests (step durations, verdicts) | the job's `runs/` artifact | whether the host was loaded |
| Host samples (`top`, `vm_stat`, `free`) | `runs/diagnostics/host-telemetry.log`, free text | how they compare with a green job |
| Render probe, stall captures | `runs/diagnostics/`, free text | how often they fire on green jobs |
| Runner image, hardware, Xcode version | `runs/diagnostics/host-environment.txt` (iOS), free text | the failure rate per image |
| Channel retries, unconfirmed recordings | the job log, as warnings | any count at all |
| Step durations, queue time | the GitHub application programming interface (API) | anything, unless someone asks |

A reader can diagnose one red job from these pieces. A reader cannot see a pattern across thirty
runs, because nothing lines the pieces up. Each flake ends with a re-run, so the cause never gets
fixed. [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
and [BE-0367](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md)
made one failure explainable. This item makes many failures comparable.

The outcome is observable: after this change, one command over a workflow's recent runs prints, for
each candidate cause, how many red jobs and how many green jobs carried its signal. It also prints
failure rates by runner image, hardware, toolchain version, and hour of day. A cause that fires on
red jobs and rarely on green ones is the one worth fixing first.

## Detailed design

The design keeps prime directive 1 intact. Every unit below observes and reports. None of them reads
a scenario's verdict to change it, and none of them involves a language model. The candidate causes
are fixed threshold rules over measured numbers, and a reader treats them as hints.

The design also keeps CI fast. The per-job step re-reads files the job has already written and makes
no device call and no network call. The heavy collection stays where BE-0361 and BE-0367 put it, on
failure alone.

### Unit 1: one telemetry record per job

`scripts/ci_telemetry.py summarize` runs as the last part of each lane's diagnostics `collect` phase:
the `collect-ios-diagnostics` and `collect-android-diagnostics` composite actions. Every job that
already calls them gets the record with no change to the job itself. The script uses the standard
library alone and runs on the runner's system `python3`, because some jobs reach `collect` before
any environment sync. The script stays compatible with Python 3.9, the interpreter Xcode ships.

The record, `ci-telemetry.json`, holds these fields:

- **Join keys:** the workflow, job, run id, attempt, commit, event, and runner name from the
  GitHub-provided environment, plus the runner image name and version.
- **Host key:** the OS version, hardware model, central processing unit (CPU) count, memory, and Xcode version. On iOS these
  come from the `host-environment.txt` that BE-0361 writes. On Android they come from the Linux host.
- **Host load:** the maximum one-minute load average, the minimum CPU idle, I/O wait, hypervisor
  steal, minimum free memory, and the largest disk-traffic burst. The script parses all of them from
  the existing sampler log.
- **Render probe (iOS):** how long the pre-run screenshot took, and whether the five-second recording
  produced bytes.
- **Stall captures:** a count per trigger, from the `stall-NN-<reason>-<pid>` directory names.
- **Scenarios:** for each scenario in each manifest, the verdict, the failing action, the total
  duration, the first `wait`'s duration, and the longest `wait`'s duration.

The first `wait` gets its own field because a slow device hurts it first. The app has just
launched, and a wait that usually settles in a second can run out its whole timeout.

The record lands in `runs/diagnostics/ci-telemetry/` beside a copy of every run manifest. The
action uploads that directory as its own artifact, `ci-telemetry-<job>-<attempt>-<unique suffix>`.
A separate artifact keeps the cross-run report from downloading each run's videos. The suffix keeps
the name unique when a job collects twice, as `bundled-runner (xcuitest)` does. The upload step
carries `continue-on-error`, so a telemetry upload can never fail a job.

### Unit 2: a CPU reading that measures an interval

Both samplers take one `top` sample every 20 seconds. A single `top` sample reports CPU time
averaged since boot, so its idle figure says nothing about the moment of a stall. Each sampler now
appends one more line from a two-sample `top` that measures the last second. The line carries an
`Interval` prefix, and the parser prefers it. On Linux the same line also gives I/O wait and
hypervisor steal. The extra cost is one second inside a background loop that already sleeps 20.

### Unit 3: joining the records with the API, across runs

`scripts/ci_telemetry.py fetch --workflow <file>` runs offline, by an operator with `gh` signed in.
For each of a workflow's last `N` completed runs, the command performs four steps:

1. List the run's jobs from the API, with each step's duration and conclusion and the job's queue
   time.
2. Download the run's `ci-telemetry-*` artifacts and join each record to its job by runner name.
3. Count the warnings in each job log that matter here: channel retries, mid-run channel crashes,
   unconfirmed recording starts, and network errors.
4. Count, for each job, how many other fetched jobs were running when it started.

A job with no telemetry artifact still gets a record from the API alone. That record keeps the job
in the denominator: a job that died before its `collect` step still counts as red. A job log that
the API withholds is recorded as unknown rather than as zero. A cancelled job is left out: the E2E
workflows cancel a superseded run when a newer push arrives, and that run carries no signal. A job
that its `timeout-minutes` killed is also reported as cancelled; the timeout annotation on the job
tells it apart, and that job counts as red.
The join uses the runner name, so jobs of one run that share a runner name stay API-only.

The log patterns copy warnings that `bajutsu` itself writes. A test pins each pattern against its
source template, so rewording a warning fails the gate instead of silently counting zero.

`scripts/ci_telemetry.py report <dir>` then prints four tables:

- each candidate cause with its count on red jobs and on green jobs
- the median of each metric on red jobs and on green jobs
- the failure rate by job, runner image, OS, Xcode version, hardware, and four-hour Coordinated Universal Time (UTC) window
- each red job with no candidate cause, by name and failing step

The fetch output also holds every manifest. `bajutsu flakiness --history <dir>/manifests` reads that
directory directly and ranks scenarios by how often their verdict flips at a constant fingerprint.

### Unit 4: the candidate causes and their signals

Each candidate cause maps to the signals that identify it. `candidate_causes` applies the rules to
every record, green ones included. A signal that fires as often on green jobs as on red ones does
not explain the red ones, and the report can show that only when both sides carry the same labels.

| Candidate cause | Signal and threshold |
|---|---|
| `host-resources` | load above 1.5 per CPU, measured idle below 5%, free memory below 10%, I/O wait above 20%, or steal above 10% |
| `device-slow-or-wedged` | a device boot or wait step above 240 seconds, a pre-run screenshot that hung or took over 10 seconds, or a `runner-crash` stall capture |
| `channel-timeout` | any runner-channel retry or mid-run channel crash in the job log |
| `video-start` | an unconfirmed recording start in the log, a zero-byte render-probe recording, or a `video-no-bytes` or `screenrecord-no-growth` stall capture |
| `network-or-deps` | a network error in the log, or a failing step that only sets up, installs, or downloads |
| `runner-contention` | a job that queued longer than 300 seconds |
| `app-wait-timeout` | a failing `wait` step with none of the signals above |
| `test-assertion` | any other failing step with none of the signals above |

The last two rules are residual. They name a failing scenario that no infrastructure signal
accompanies, which is where a real defect or a too-tight wait appears. A fetched red job whose log
could not be read gets `log-signals-unknown` instead, because its log signals were never checked. The report compares time of
day and runner image as dimensions rather than as causes, because no single job carries them as a
signal.

## Alternatives considered

- **Emit structured counters from `bajutsu` itself.** The runner could write channel retries and
  recording outcomes to a JavaScript Object Notation (JSON) file instead of a log line. That change would touch the driver and
  evidence code for a CI-only purpose. Counting the existing warnings from the job log needs no core
  change, and the source-pinning test removes the usual risk of log parsing.
- **Upload nothing new and read the `runs/` artifact.** The report would then download every run's
  videos and screenshots, hundreds of megabytes per job. A separate artifact of a few kilobytes keeps
  the report cheap enough to run over dozens of runs.
- **Use the job log for everything.** The host samples, render probe, and manifests never reach the
  job log, so the log alone cannot carry them.
- **Classify each failure with a language model.** That approach would put a non-deterministic judge
  next to the CI verdict, which prime directive 1 forbids. Fixed thresholds are reviewable, and the
  report's green-job column shows when a threshold is wrong.
- **Raise the sampler cadence.** A faster sampler would sharpen the load series but would add load
  to the host it measures. The interval CPU line adds one second per sample instead.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1: `summarize` writes `ci-telemetry.json` and the manifests in both lanes' `collect`
  phases, uploaded as their own artifact
- [x] Unit 2: both host samplers append a two-sample `Interval` CPU line
- [x] Unit 3: `fetch` joins the records with job, step, and log facts from the API, and `report`
  prints the cross-run tables
- [x] Unit 4: `candidate_causes` maps each cause to its signals; `docs/ci.md` and `docs/ja/ci.md`
  document the table and the analysis procedure

## References

- [BE-0361 — Collect the layered diagnostics an iOS CI failure needs](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
- [BE-0367 — Collect the layered diagnostics an Android CI failure needs](../BE-0367-android-ci-emulator-diagnostics/BE-0367-android-ci-emulator-diagnostics.md)
- [BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
- [BE-0220 — Flaky-test suggestion and cross-run fix proposals from DB run history](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)
- [`scripts/ci_telemetry.py`](../../scripts/ci_telemetry.py)
- [`docs/ci.md`](../../docs/ci.md)

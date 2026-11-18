**English** · [日本語](BE-0367-android-ci-emulator-diagnostics-ja.md)

# BE-0367 — Collect the layered diagnostics an Android CI failure needs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0367](BE-0367-android-ci-emulator-diagnostics.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0367") |
| Implementing PR | [#1631](https://github.com/bajutsu-e2e/bajutsu/pull/1631) |
| Topic | Platform support |
| Related | [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md), [BE-0350](../BE-0350-ondevice-conformance-evidence-capture/BE-0350-ondevice-conformance-evidence-capture.md) |
<!-- /BE-METADATA -->

## Introduction

`.github/workflows/android-e2e.yml` runs six jobs: `smoke`, `golden`, `network`, `conformance`,
`fault-injection`, and `visual`. Each job drives an Android Virtual Device (AVD) over the adb backend
(`bajutsu/platform_lifecycle/environments/android.py`, `bajutsu/drivers/adb.py`). The AVD boots under
KVM on a Linux GitHub Actions runner.

Every one of those jobs collects exactly one thing when it ends: the scenario's own `runs/` output.
That output holds the report, the screenshots, and the opt-in `video` / `deviceLog` interval
evidence. Nothing else is collected.

A job can fail for an infrastructure reason:

- The resident UI Automator server stops answering. Its code is `bajutsu/adb_resident.py`.
  See
  [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md).
- The emulator's own rendering wedges.
- The job runs out its `timeout-minutes`.

In each case the uploaded artifact carries no evidence of *why*. It shows only that a scenario or the
job itself did not finish.

This proposal adds the Android backend's own layered diagnostics collection. It builds on the
three-layer shape that
[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) proposes
for the XCUITest lane:

1. A `bajutsu`-internal hook that fires at the moment a stall is first observed.
2. A CI-side sweep of the emulator's own state.
3. A background sampler that records host load for the whole job.

One structural difference from the iOS lane runs through the whole design. The emulator exists only
inside each job's `reactivecircus/android-emulator-runner` step. The device-side sweep thus
lives in a script that the step invokes, not in a composite action beside it. *Detailed design*
returns to this detail.

Every artifact lands under `runs/`, which each job's existing `Upload run artifacts` step already
carries. No new upload wiring is needed.

## Motivation

Android's CI diagnostics gap is wider than the iOS gap that
[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) closes,
not narrower. The XCUITest lane's `bajutsu-e2e` composite action
(`.github/actions/bajutsu-e2e/action.yml`) already carries a `Collect crash diagnostics` step. The
step sweeps `~/Library/Logs/DiagnosticReports`. That directory is empty for the failure class
BE-0361 investigates, but a real crash report lands there when one exists.

`android-e2e.yml` has no such step anywhere. Every job's steps read `Run …` followed directly by
`Upload run artifacts`. A job can fail outside a scenario's own assertions: before, between, or after
the scenarios that `bajutsu run` drives. Such a job uploads nothing beyond whatever the scenario's own
opt-in `capture:` list already wrote.

The gap reaches past collection and into detection. The `BackendCrashError` docstring in
`bajutsu/drivers/base.py` names three processes a backend crash can mean: "the resident XCUITest
runner's XCTest host, an adb server, a browser process".
[BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
already designs the adb backend's own crash-triggered retry. Its recovery is "an app-level clean
state" (`uninstall`/`install` plus `pm clear`), as opposed to XCUITest's Simulator restart.

Yet today only one backend raises the error that this recovery reacts to.
`XcuitestRunnerCrashError` in `bajutsu/drivers/xcuitest.py` is the sole subclass of
`base.BackendCrashError` in the codebase. The adb driver's own infrastructure-failure signal,
`AdbResidentError` (`bajutsu/drivers/adb.py`), is deliberately *not* a `BackendCrashError`. It marks a
degrade-to-fallback that the driver already absorbs, not a scenario-ending crash. The pipeline's
crash-retry loop thus has no adb-side trigger at all.

Whether to close that gap in *detection* is a separate question (see *Alternatives considered*). For
*this* proposal, the gap means one thing. Layer 1's hook must attach to a signal that already fires
reliably today, not to a crash declaration that does not yet exist for this backend.

No specific Android CI incident anchors this proposal, unlike the 2026-08-12 runner log that anchors
BE-0361. The request behind this item is to close the gap before an unowned Android failure repeats
the iOS lane's story: "cancelled by `timeout-minutes`, no diagnosable cause".

Acting ahead of an incident is deliberate. The codebase evidence above already shows the structural
weakness that BE-0361 diagnoses: a backend that can fail silently, and CI collection that was never
built to catch it. Building the cheaper collection first does not require waiting for a reproduction
on the more expensive, harder-to-instrument platform.

## Detailed design

The collection follows BE-0361's three layers. Each layer carries evidence that a different observer
can see.

- `bajutsu` itself knows *when* a stall starts.
- The CI action reads the emulator's and the Linux host's state at that point.
- The telemetry sampler is the only layer that sees the whole job, not one moment in it.

### Layer 1: what only `bajutsu` can capture

**A screenrecord growth watcher.** The iOS `video` provider (`start_video` in
`bajutsu/evidence/intervals.py`) already confirms that recording has started. It polls the target
file's size past its pre-spawn baseline (`_await_video_file_growing`). When the size never grows, it
warns `"recordVideo produced no new bytes …"`. That warning is one of BE-0361's two stall triggers.

The Android twin, `start_screenrecord`, confirms only that the on-device `screenrecord` process exists
(`_await_screenrecord_started`). It never confirms that the process produces bytes, so Android has no
equivalent of that warning today. Add one:

- Poll the device-side recording file's size past its pre-spawn baseline. The poll is bounded. It uses
  `adb shell stat -c %s <path>`, or an `ls -l` fallback where `stat` is unavailable.
- Put the poll under the same `confirm_started` opt-in that `start_video` already uses.
- Log the same shape of warning when growth never confirms within the timeout.

**A stall-time probe.** The probe has two trigger points. Both are narrow on purpose.

1. The resident channel's *hierarchy-read* fallback. It sits in `bajutsu/drivers/adb.py`, in the
   `except AdbResidentError` around the hierarchy read. There the driver latches
   `_fetch_hierarchy = None` and degrades to the `uiautomator dump` subprocess for the rest of the
   lease. The *Motivation* above describes this site. It means the read channel is gone, not
   momentarily noisy.
2. The new screenrecord watcher's no-growth warning.

At either moment, the state that explains a wedged renderer versus a wedged host still exists. The
next frame is about to overwrite it.

The probe deliberately hooks *that propagation site*, not the `AdbResidentError` class. Two of the
class's subclasses fire during perfectly healthy runs and mean the opposite of a stall:

- `AdbActUnsupported` is a permanent, expected capability absence. An older resident server answers
  `/act` with a 404, and the driver latches that once and stops probing.
- `AdbActUncertain` is a lost-response race. The driver deliberately treats the gesture as having
  landed, so that it does not actuate twice.

Both are caught on the act path. There even the base class degrades one gesture while the channel
stays in use. Hooking the class would fire the capture on runs where nothing stalled. Those captures
would spend the per-run cap below, and a genuine stall later in the same run would find no budget
left. That later stall is the one case the probe exists for. The act path is thus excluded
outright.

When the environment variable `BAJUTSU_STALL_DIAGNOSTICS` names a directory, both trigger points run a
bounded, best-effort capture into it. The variable has the same name BE-0361 proposes, deliberately
without a backend prefix. The capture holds:

- `adb shell dumpsys SurfaceFlinger --latency`.
- A `logcat -d -t 200` tail of the most recent lines. This is cheap, because Android's logcat, unlike
  the iOS unified log, has no host-permission barrier to read.
- A host `ps aux` plus `top -bn1` snapshot.

Each command carries a short subprocess timeout. A failure is swallowed. Captures are capped per run,
mirroring BE-0361's own bounds.

Reusing BE-0361's variable name, rather than minting `BAJUTSU_ADB_STALL_DIAGNOSTICS`, lets one
operator setting turn the hook on for whichever backend a job runs. The two proposals share only that
name. Neither one's code depends on the other, so either can land first, or both can land
independently.

### Layer 2: emulator state, collected from inside the emulator's own step

**The constraint that shapes this layer is also the sharpest way the Android lane diverges from
BE-0361's iOS one.** On iOS, the Simulator lives on the host for the whole job. A composite action
running as its own step can reach it, and BE-0361's `collect-ios-diagnostics` assumes exactly that.

Android has no such window. Every device-touching step in `android-e2e.yml` runs *inside* a
`reactivecircus/android-emulator-runner` step. That step boots the emulator, runs its `script:`, and
stops the emulator when the step ends. An ordinary step placed after it would run with **no device
attached**. So would a step-level `if: failure()` gate. `adb logcat -d`, `adb bugreport`, and the
rooted tombstone and ANR pulls would each collect nothing at all.

The in-tree precedent is already there. The `poll_cpuinfo` sampler lives embedded in `smoke`'s
`script:` string for this very reason. It preserves the real exit code in `rc`, so it never masks a
scenario failure.

Device-side collection thus lives in a script, not a composite action:
`scripts/collect_android_diagnostics.sh`. Each job's own `script:` invokes it at the tail, where the
emulator is still alive. The failure tier keys off the run command's own `rc`, the same shape the
`poll_cpuinfo` poller already uses to survive a failing run. A step-level `if: failure()` would fire
after the device is gone.

The script is wired into every job that boots an AVD and drives it through the adb driver: `smoke`,
`golden`, `network`, `conformance`, `fault-injection`, and `visual`. These are the same six jobs that
the Android lane in `docs/ci.md` already lists.

`codegen` (`uiautomator (codegen)`) stays out, for the same reason BE-0361 excludes iOS's own
`codegen` job. It drives Gradle's `connectedAndroidTest` directly. It uploads only its own
`androidTest-results` / `codegen-diagnostics` report. It writes nothing under `runs/` for this
collection to ride.

The script runs two tiers.

- **Always** (cheap, every run):
  - A full-buffer `adb logcat -d -b main,system,crash,events,radio` dump. The per-scenario `deviceLog`
    interval streams only while a scenario with `capture: [deviceLog]` is running. This dump instead
    reads the device's own ring buffer directly. It still has something to show for a job that failed
    before any scenario's own capture began. It also covers a job whose stream cut off mid-write
    because the process serving it died.
  - `adb shell dumpsys meminfo`, `adb shell getprop`, and `adb devices -l`. Together they give an
    environment snapshot: API level, ABI, and the emulator's own command-line flags. The snapshot
    answers BE-0361's cross-run hypothesis (4), whether failures cluster on a particular emulator
    configuration.
- **On failure only** (heavier). The tier is gated on the captured `rc` described above, so it fires
  while the device is still attached.
  - `adb bugreport`, Android's own comprehensive collector and the direct analog of `simctl diagnose`.
    The report is zipped and copied under `runs/diagnostics/`.
  - A rooted pull of `/data/tombstones` (native crash reports) and `/data/anr/`. The second path holds
    Application Not Responding (ANR) traces. These jobs already use the AVD profile
    `target: google_apis`, not `google_apis_playstore`. That profile supports `adb root`. The iOS sweep gets these two crash-report classes
    for free from `~/Library/Logs/DiagnosticReports`. On Android they live device-side and need an
    explicit pull.

### Layer 3: host telemetry over time

Unlike Layer 2, this layer reads **only the Linux runner**, never the device. The constraint above
thus does not bind it. `top -bn1` and `free -m` work from an ordinary workflow step, whether or
not an emulator is attached.

Layer 3 is the one part of the collection that *can* be a composite action,
`.github/actions/collect-android-diagnostics`. The action has two phases:

- A `start` phase that each job calls before its emulator step. It starts a background sampler that
  appends to `runs/diagnostics/host-telemetry.log` every ~20 seconds.
- A `collect` phase that each job calls after its emulator step.

Bracketing the emulator step from outside has a second benefit. The telemetry covers a job whose
emulator step died outright. A sampler launched inside that step could not.

Every job already tunes the emulator's own resource ceiling (`-memory 8192 -cores 2`) against a shared
Linux runner. A stall that correlates with host memory or processor pressure is exactly as plausible
here as BE-0361's macOS-host hypothesis (2). This layer puts that correlation on record, instead of
leaving it a guess after the fact.

Like BE-0361's sampler, this sampler is an observer off the verdict path. Its interval is a sampling
cadence, not a wait. Prime directive 2's ban on fixed sleeps in the run loop stays untouched.

### Work breakdown (`MECE`)

Mutually exclusive, collectively exhaustive (`MECE`) units of work follow.

1. **Screenrecord growth watcher.** Add `_await_screenrecord_growing` (or an equivalent poll) to
   `start_screenrecord` in `bajutsu/evidence/intervals.py`. Gate it behind the same `confirm_started`
   opt-in that `start_video` already uses. Warn on no growth the same way the iOS provider does.
2. **Stall-time probe.** Build the following pieces:
   - The bounded capture module behind `BAJUTSU_STALL_DIAGNOSTICS`.
   - Its two adb-side trigger points: the hierarchy-read fallback that latches
     `_fetch_hierarchy = None`, and the new screenrecord no-growth warning. The act path is
     deliberately excluded.
   - The per-run capture cap.
   - The `android-e2e.yml` opt-in.
3. **`scripts/collect_android_diagnostics.sh`.** Implement the always tier and the `rc`-keyed failure
   tier. Invoke the script from the tail of each emulator step's own `script:` in `smoke`, `golden`,
   `network`, `conformance`, `fault-injection`, and `visual`. The call runs inside the step, where the
   device is still attached.
4. **The `collect-android-diagnostics` composite action.** Implement the `start` / `collect`
   host-telemetry phases only. They bracket each job's emulator step from outside it.
5. **Docs.** Extend the diagnostics section that
   [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) adds to
   `docs/ci.md` (and its `docs/ja/` mirror) with the Android tiers. One section then covers both
   backends, rather than two disconnected write-ups. The section also explains why the device-side
   half lives in a script rather than the action.
6. **Tests.** Write unit tests for the two `bajutsu` hooks.
   - The screenrecord watcher warns exactly when growth never confirms.
   - The stall probe is a no-op when the environment variable is unset. When the variable is set, the
     probe is bounded and best-effort. It never fires on the act path.
   - `make lint-sh` / `shellcheck` covers the new script. `make lint-actions` / `actionlint` covers
     the new composite action.

### Prime directives preserved

- **AI never judges.** Every layer collects evidence. No model call enters any path this proposal
  touches, and no collected artifact feeds a verdict.
- **Determinism first.** The probes are bounded subprocess calls with timeouts, not sleeps. The
  telemetry sampler observes from outside the run loop. Pass/fail is decided exactly as before.
- **App-agnostic.** Nothing here reads the target app. The hooks key on the device and the resident
  channel, and the CI action keys on the emulator and the host. No per-target branching exists
  anywhere.

## Alternatives considered

- **Fold Layer 3 into `android-e2e.yml`'s existing `poll_cpuinfo` sampler, or leave that input
  alone.** The lane already carries a background sampler for this exact question. A
  `workflow_dispatch` input, when set, appends `adb shell dumpsys cpuinfo` to `runs/cpuinfo.log` every
  two seconds during `smoke`. A flake-hunting dispatch can then be checked against host processor
  pressure.

  Layer 3 **coexists with it and does not remove it**, because the two measure different things from
  different sides. `poll_cpuinfo` reads the *guest's* view over adb. Layer 3 reads the *host's* own
  load.

  The reason `poll_cpuinfo` stays off for every `pull_request` and `merge_group` run does not carry
  over either. That input is off because "polling every 2s is itself continuous adb traffic and host
  load on the very lane whose CPU contention it measures". That is an observer effect. A host-side
  `top`/`free` every ~20 seconds does not have it: the sampler issues no adb traffic at all and samples
  a tenth as often.

  Layer 3 is thus always on where `poll_cpuinfo` is opt-in. An implementer should read the two as
  complementary, not as one superseding the other.
- **Share one composite action between the iOS and Android lanes.** Rejected. `collect-ios-diagnostics`
  reads `simctl`/CoreSimulator/the macOS unified log. `collect-android-diagnostics` reads
  `adb`/logcat/the Linux host. The underlying tools share no surface, so a single action would be an
  `if backend == …` fork rather than shared logic. The two stay separate actions. They share a design
  (the always/on-failure split, the `start`/`collect` telemetry phases) and, for Layer 1, a literal
  environment-variable name.
- **Gate this item on [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
  landing first, since it defines `BAJUTSU_STALL_DIAGNOSTICS`.** Rejected. BE-0361 is itself an
  unimplemented `Proposal`. Making Android's zero-collection gap wait on it would leave the wider gap
  open indefinitely, for no code-sharing reason. The two proposals share a variable-*name*
  convention, not a shared module that either depends on. Either can implement the variable first,
  and the other follows the same contract. Whichever lands first defines `BAJUTSU_STALL_DIAGNOSTICS`
  in the shared stall-probe documentation. The second wires its own backend's trigger points into the
  existing name.
- **Give the adb backend a real `AdbBackendCrashError(base.BackendCrashError)` and wire the stall probe
  to it, closing the crash-*detection* gap this item's Motivation surfaces.** Left out of scope.
  Deciding when an adb-side failure is severe enough to discard the current lease and retry the whole
  scenario is a recovery-semantics question. It asks what BE-0353's crash-triggered retry should
  actually trigger *on* for this backend. It is not a diagnostics-collection question. Conflating the
  two would make this item responsible for a behavior change well past what "collect more evidence"
  needs. A future item can take up detection. This item's Layer 1 hooks on the failure signal the adb
  driver already raises today (`AdbResidentError`), instead of waiting on one that does not yet exist.
- **Run `adb bugreport` on every job, not only on failure.** Rejected. A bugreport runs tens of
  seconds and produces a multi-megabyte archive. That is far lighter than macOS's `sysdiagnose`, which
  BE-0361 defers for exactly this reason. It is still a real cost when multiplied across six jobs on
  every green run. The always/on-failure split keeps the cheap tier unconditional and reserves the
  heavier collector for the runs that actually need it.
- **Stream the full `adb logcat` ring buffer for the whole job instead of a point-in-time dump.** The
  per-scenario `deviceLog` interval (`start_logcat` in `bajutsu/evidence/intervals.py`) already
  streams a scenario's own window. A second whole-job stream would duplicate most of that content. The
  always tier's `logcat -d` dump already provides the benefit at a fraction of the cost: it reads once,
  after the fact, from the device's own retained buffer. BE-0361 uses the same reasoning to reject
  streaming the iOS unified log for a whole job.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — screenrecord growth watcher for the Android `video` provider
- [x] Unit 2 — stall-time probe hook (hierarchy-read fallback, the new no-growth warning)
- [x] Unit 3 — `scripts/collect_android_diagnostics.sh` and its per-job `script:` wiring
- [x] Unit 4 — the `collect-android-diagnostics` composite action (host telemetry only)
- [x] Unit 5 — docs (`docs/ci.md` and its `ja` mirror)
- [x] Unit 6 — tests

[BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) landed
first. This item thus wired its two triggers into that item's existing
`bajutsu/stall_diagnostics.py`, rather than adding a second module beside it. *Alternatives
considered* anticipated this outcome when it refused to gate this item on BE-0361.

The wiring surfaced a seam that neither item had named. *When* to capture, what a capture may cost,
and where it lands are identical for both backends. *What to read* shares no command between them.
Unit 2 thus split the module along that seam:

- A `capture` owns the opt-in gate, the per-trigger budget, and the summary.
- A `ProbeSet` is what each backend contributes.

The two lanes' probes became `simulator_probes` and `device_probes`. A third backend adds a probe set
and nothing else. Unit 5 folded the two lanes' `docs/ci.md` write-ups into one section for the same
reason.

Units 3 and 4 also cover `uiautomator (codegen)`, which *Detailed design* above excludes. That
exclusion reasoned from the collection's *mechanism*: the job writes nothing under `runs/`, so
nothing rides there. It mistook the mechanism for the job's *need*, and the uploads show the opposite
of that need. Gradle runs the generated test and produces every path that `codegen` uploads. A
failure before that point thus met `if-no-files-found: ignore` and uploaded nothing at all.
Such failures include a wedged AVD boot, a codegen or build error, and the job's own
`timeout-minutes`.

The collection's output directory is a parameter, not a constraint. The job thus takes the same
two tiers and the same host sampler, and its upload gains `runs/`.

Layer 1 reaches `codegen` through one hook rather than two. No adb driver runs at test time, so only
the host-side recorder can fire:
[`demos/showcase/android/screenrecord.py`](../../demos/showcase/android/screenrecord.py), which now
confirms its recording is producing bytes. That is the right hook anyway, because a wedged renderer is
this lane's known flake.

## References

- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) — the
  three-layer diagnostics design this item ports to the Android backend, and the source of the
  shared `BAJUTSU_STALL_DIAGNOSTICS` variable name
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) —
  the adb crash-triggered retry whose own design already assumes a backend-crash signal that this
  item's Motivation shows does not yet exist for adb
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) —
  the resident channel whose `AdbResidentError` is this item's first stall trigger
- [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md) —
  the adb driver conformance contract that this item's collection instruments failures for, without
  changing
- [BE-0350](../BE-0350-ondevice-conformance-evidence-capture/BE-0350-ondevice-conformance-evidence-capture.md) —
  the video/deviceLog evidence that this item's collection supplements for the same on-device suites
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py) — `start_video` (the
  growth-watcher pattern Unit 1 ports) and `start_screenrecord` (the seam Unit 1 extends)
- [`bajutsu/drivers/adb.py`](../../bajutsu/drivers/adb.py) — the hierarchy-read fallback that is
  Unit 2's first trigger, and the act-path `AdbActUnsupported` / `AdbActUncertain` subclasses it
  deliberately excludes
- [`bajutsu/drivers/base.py`](../../bajutsu/drivers/base.py) — `BackendCrashError`, the detection
  gap this item's Motivation names but leaves for a future item (see *Alternatives considered*)
- [`.github/actions/bajutsu-e2e/action.yml`](../../.github/actions/bajutsu-e2e/action.yml) — the
  XCUITest lane's `Collect crash diagnostics` step, the precedent this item follows in intent, but
  not in mechanism (see Layer 2)
- [`.github/workflows/android-e2e.yml`](../../.github/workflows/android-e2e.yml) — the lane whose
  jobs opt into every layer, whose `reactivecircus/android-emulator-runner` steps bound where the
  device is reachable, and whose `poll_cpuinfo` sampler is both Unit 3's shape precedent and Layer
  3's neighbour
- [`docs/ci.md`](../../docs/ci.md) — the CI documentation Unit 5 extends, and the page whose Android
  lane already lists the six jobs this item wires

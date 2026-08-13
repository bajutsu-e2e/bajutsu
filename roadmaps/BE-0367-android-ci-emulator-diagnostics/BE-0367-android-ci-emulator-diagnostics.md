**English** · [日本語](BE-0367-android-ci-emulator-diagnostics-ja.md)

# BE-0367 — Collect the layered diagnostics an Android CI failure needs

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0367](BE-0367-android-ci-emulator-diagnostics.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0367") |
| Topic | Platform support |
| Related | [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md), [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md), [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md), [BE-0350](../BE-0350-ondevice-conformance-evidence-capture/BE-0350-ondevice-conformance-evidence-capture.md) |
<!-- /BE-METADATA -->

## Introduction

`.github/workflows/android-e2e.yml` runs six jobs — `smoke`, `golden`, `network`, `conformance`,
`fault-injection`, `visual` — against an Android Virtual Device (AVD) booted under KVM on a Linux
GitHub Actions runner, driven over the adb backend
(`bajutsu/platform_lifecycle/environments/android.py`,
`bajutsu/drivers/adb.py`). Every one of those jobs collects exactly one thing when it ends: the
scenario's own `runs/` output (report, screenshots, the opt-in `video` / `deviceLog` interval
evidence) and nothing else. When a job fails for an infrastructure reason — the resident UI
Automator server (`bajutsu/adb_resident.py`, [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md))
stops answering, the emulator's own rendering wedges, or the job simply runs out its
`timeout-minutes` — the uploaded artifact carries no evidence of *why*, only that a scenario or the
job itself did not finish. This proposal adds the Android backend's own layered diagnostics
collection, built on the same three-layer shape [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
proposes for the XCUITest lane: a `bajutsu`-internal hook that fires at the moment a stall is first
observed, a CI-side sweep of the emulator's own state, and a background sampler that records host
load for the whole job. One structural difference from the iOS lane runs through the whole design and
is worth stating up front: the emulator exists only inside each job's
`reactivecircus/android-emulator-runner` step, so the device-side sweep lives in a script that step
invokes rather than in a composite action beside it — the detail *Detailed design* returns to. Every
artifact lands under `runs/`, which each job's existing `Upload run artifacts` step already carries,
so no new upload wiring is needed.

## Motivation

Android's CI diagnostics gap is wider than the iOS gap [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
closes, not narrower. The XCUITest lane's `bajutsu-e2e` composite action
(`.github/actions/bajutsu-e2e/action.yml`) already carries a `Collect crash diagnostics` step that
sweeps `~/Library/Logs/DiagnosticReports` — empty for the failure class BE-0361 investigates, but
present, and a real crash report lands there when one exists. `android-e2e.yml` has no such step
anywhere: every job's steps read `Run …` followed directly by `Upload run artifacts`, so a job that
fails outside a scenario's own assertions — before, between, or after the scenarios `bajutsu run`
drives — uploads nothing beyond whatever that scenario's own opt-in `capture:` list already wrote.

The gap reaches past collection and into detection. `bajutsu/drivers/base.py`'s `BackendCrashError`
docstring names "the resident XCUITest runner's XCTest host, an adb server, a browser process" as
the three processes a backend crash can mean, and [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md)
already designs the adb backend's own crash-triggered retry ("an app-level clean state" —
`uninstall`/`install` plus `pm clear` — as opposed to XCUITest's Simulator restart). Yet today only
one backend actually raises the error that recovery reacts to:
`bajutsu/drivers/xcuitest.py`'s `XcuitestRunnerCrashError` is the sole subclass of
`base.BackendCrashError` in the codebase. The adb driver's own infrastructure-failure signal,
`AdbResidentError` (`bajutsu/drivers/adb.py`), is deliberately *not* a `BackendCrashError` — it marks
a degrade-to-fallback the driver already absorbs, not a scenario-ending crash — so the pipeline's
crash-retry loop has no adb-side trigger at all. Whether that gap in *detection* is worth closing is
a separate question (see *Alternatives considered*); what it means for *this* proposal is that
Layer 1's hook has to attach to a signal that already fires reliably today, not to a crash
declaration that does not yet exist for this backend.

No specific Android CI incident anchors this proposal the way the 2026-08-12 runner log anchors
BE-0361 — the request behind this item is to close the gap before an unowned Android failure repeats
the same "cancelled by `timeout-minutes`, no diagnosable cause" story the iOS lane already lived once.
Acting ahead of an incident is deliberate here: the codebase evidence above already shows the same
structural weakness BE-0361 diagnoses — a backend that can fail silently, and CI collection that
was never built to catch it — so waiting for a reproduction on the more expensive, harder-to-instrument
platform is not a prerequisite for building the cheaper collection first.

## Detailed design

The collection follows BE-0361's three layers, each carrying evidence a different observer can see:
`bajutsu` itself knows *when* a stall starts; the CI action reads the emulator's and the Linux
host's state at that point; the telemetry sampler is the only layer that sees the whole job, not one
moment in it.

### Layer 1: what only `bajutsu` can capture

**A screenrecord growth watcher.** The iOS `video` provider (`start_video` in
`bajutsu/evidence/intervals.py`) already confirms recording has actually started by polling the
target file's size past its pre-spawn baseline (`_await_video_file_growing`), and warns —
`"recordVideo produced no new bytes …"` — when it never does; that warning is one of BE-0361's two
stall triggers. The Android twin, `start_screenrecord`, confirms only that the on-device
`screenrecord` process exists (`_await_screenrecord_started`), never that it is producing bytes, so
there is no Android equivalent of that warning today. Add one: a bounded poll of the device-side
recording file's size (`adb shell stat -c %s <path>`, or an `ls -l` fallback where `stat` is
unavailable) past its pre-spawn baseline, under the same `confirm_started` opt-in `start_video`
already uses, logging the same shape of warning when growth never confirms within the timeout.

**A stall-time probe.** Two trigger points, both narrow on purpose. The first is the resident
channel's *hierarchy-read* fallback (`bajutsu/drivers/adb.py`, the `except AdbResidentError` around
the hierarchy read, where the driver latches `_fetch_hierarchy = None` and degrades to the
`uiautomator dump` subprocess for the rest of the lease) — the site the *Motivation* above describes,
and the one that means the read channel is gone rather than momentarily noisy. The second is the new
screenrecord watcher's no-growth warning. At either moment the state that explains a wedged renderer
versus a wedged host still exists and is about to be overwritten by the next frame.

The probe deliberately hooks *that propagation site*, not the `AdbResidentError` class: two of its
subclasses fire during perfectly healthy runs and mean the opposite of a stall. `AdbActUnsupported`
is a permanent, expected capability absence — an older resident server that answers `/act` with a
404, which the driver latches once and stops probing — and `AdbActUncertain` is a lost-response race
the driver deliberately treats as having landed, precisely so it does not actuate twice. Both are
caught on the act path, where even the base class degrades one gesture while keeping the channel in
use. Hooking the class would fire the capture on runs where nothing stalled and spend the per-run cap
below, so a genuine stall later in the same run would find no budget left — the one case the probe
exists for. The act path is therefore excluded outright.

When the environment variable `BAJUTSU_STALL_DIAGNOSTICS` — the same name BE-0361 proposes,
deliberately not prefixed by backend — names a directory, both trigger points run a bounded,
best-effort capture into it: `adb shell dumpsys SurfaceFlinger --latency`, a `logcat -d -t 200` tail of the most recent lines
(cheap, because Android's logcat, unlike the iOS unified log, has no host-permission barrier to
read), and a host `ps aux` plus `top -bn1` snapshot. Each command carries a short subprocess timeout,
a failure is swallowed, and captures are capped per run, mirroring BE-0361's own bounds. Reusing
BE-0361's variable name rather than minting `BAJUTSU_ADB_STALL_DIAGNOSTICS` means one operator
setting turns the hook on for whichever backend a job runs, and the two proposals share only that
name — neither's code depends on the other, so either can land first, or both can land independently
of each other.

### Layer 2: emulator state, collected from inside the emulator's own step

**The constraint that shapes this layer, and the sharpest way the Android lane diverges from
BE-0361's iOS one.** On iOS the Simulator lives on the host for the whole job, so a composite action
running as its own step reaches it — which is exactly what BE-0361's `collect-ios-diagnostics`
assumes. Android has no such window. Every device-touching step in `android-e2e.yml` runs *inside* a
`reactivecircus/android-emulator-runner` step, which boots the emulator, runs its `script:`, and
kills it when that step ends. An ordinary step placed after it — and a step-level `if: failure()`
gate — would run with **no device attached**, so `adb logcat -d`, `adb bugreport`, and the rooted
tombstone and ANR pulls would each collect nothing at all. The in-tree precedent is already there:
the `poll_cpuinfo` sampler lives embedded in `smoke`'s `script:` string for this very reason, and
preserves the real exit code in `rc` so it never masks a scenario failure.

Device-side collection therefore lives in a script, not a composite action:
`scripts/collect_android_diagnostics.sh`, invoked from the tail of each job's own `script:`, where
the emulator is still alive. The failure tier keys off the run command's own `rc` — the same shape
the `poll_cpuinfo` poller already uses to survive a failing run — rather than a step-level
`if: failure()` that would fire after the device is gone. It is wired into every job that boots an
AVD and drives it through the adb driver — `smoke`, `golden`, `network`, `conformance`,
`fault-injection`, `visual` — the same six jobs `docs/ci.md`'s Android lane already lists.
`codegen` (`uiautomator (codegen)`) stays out for the same reason BE-0361 excludes iOS's own
`codegen` job: it drives Gradle's `connectedAndroidTest` directly, uploads only its own
`androidTest-results` / `codegen-diagnostics` report, and writes nothing under `runs/` for this
collection to ride. The script runs two tiers:

- **Always** (cheap, every run): a full-buffer `adb logcat -d -b main,system,crash,events,radio`
  dump — unlike the per-scenario `deviceLog` interval, which streams only while a scenario with
  `capture: [deviceLog]` is running, this reads the device's own ring buffer directly, so it still
  has something to show for a job that failed before any scenario's own capture began, or whose
  stream cut off mid-write when the process serving it died; `adb shell dumpsys meminfo`, `adb
  shell getprop`, and `adb devices -l` for an environment snapshot (API level, ABI, the emulator's
  own command-line flags) that answers BE-0361's cross-run hypothesis (4) — whether failures cluster
  on a particular emulator configuration.
- **On failure only** (heavier): `adb bugreport`, Android's own comprehensive collector — the direct
  analog of `simctl diagnose` — zipped and copied under `runs/diagnostics/`; and, since the AVD
  profile these jobs already use (`target: google_apis`, not `google_apis_playstore`) supports
  `adb root`, a rooted pull of `/data/tombstones` (native crash reports) and `/data/anr/` (Application Not Responding
  (ANR) traces) — the two crash-report classes the iOS sweep gets for free from
  `~/Library/Logs/DiagnosticReports`, but which live device-side on Android and need an explicit
  pull. Gated on the captured `rc` described above, so it fires while the device is still attached.

### Layer 3: host telemetry over time

Unlike Layer 2, this layer reads **only the Linux runner**, never the device, so the constraint above
does not bind it: `top -bn1` and `free -m` work from an ordinary workflow step whether or not an
emulator is attached. Layer 3 is therefore the one part of the collection that *can* be a composite
action, `.github/actions/collect-android-diagnostics`, with a `start` phase each job calls before its
emulator step and a `collect` phase after it — a background sampler appending to
`runs/diagnostics/host-telemetry.log` every ~20 seconds. Bracketing the emulator step from outside is
also what lets the telemetry cover a job whose emulator step died outright, which a sampler launched
inside that step could not.

Every job already tunes the emulator's own resource ceiling (`-memory 8192 -cores 2`) against a
shared Linux runner, so a stall correlating with host memory or processor pressure is exactly as
plausible here as BE-0361's macOS-host hypothesis (2) — this layer is what puts that correlation on
record instead of leaving it a guess after the fact. Like BE-0361's sampler, this one is an observer
off the verdict path: its interval is a sampling cadence, not a wait, so prime directive 2's ban on
fixed sleeps in the run loop stays untouched.

### Work breakdown (`MECE`)

Mutually exclusive, collectively exhaustive (`MECE`) units of work follow.

1. **Screenrecord growth watcher.** `_await_screenrecord_growing` (or an equivalent poll) in
   `bajutsu/evidence/intervals.py`'s `start_screenrecord`, gated behind the same `confirm_started`
   opt-in `start_video` already uses, warning on no growth the same way the iOS provider does.
2. **Stall-time probe.** The bounded capture module behind `BAJUTSU_STALL_DIAGNOSTICS`, its two
   adb-side trigger points (the hierarchy-read fallback that latches `_fetch_hierarchy = None`, and
   the new screenrecord no-growth warning — the act path deliberately excluded), the per-run capture
   cap, and the `android-e2e.yml` opt-in.
3. **`scripts/collect_android_diagnostics.sh`.** The always tier and the `rc`-keyed failure tier,
   invoked from the tail of each emulator step's own `script:` in `smoke`, `golden`, `network`,
   `conformance`, `fault-injection`, `visual` — inside the step, where the device is still attached.
4. **The `collect-android-diagnostics` composite action.** The `start` / `collect` host-telemetry
   phases only, bracketing each job's emulator step from outside it.
5. **Docs.** Extend the diagnostics section [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
   adds to `docs/ci.md` (and its `docs/ja/` mirror) with the Android tiers, so one section covers
   both backends rather than two disconnected write-ups — including why the device-side half lives
   in a script rather than the action.
6. **Tests.** Unit tests for the two `bajutsu` hooks — the screenrecord watcher warns exactly when
   growth never confirms; the stall probe is a no-op when the environment variable is unset, bounded
   and best-effort when set, and never fires on the act path. `make lint-sh` / `shellcheck` covers the
   new script and `make lint-actions` / `actionlint` the new composite action.

### Prime directives preserved

- **AI never judges.** Every layer collects evidence; no model call enters any path this proposal
  touches, and no collected artifact feeds a verdict.
- **Determinism first.** The probes are bounded subprocess calls with timeouts, not sleeps; the
  telemetry sampler observes from outside the run loop. Pass/fail is decided exactly as before.
- **App-agnostic.** Nothing here reads the target app: the hooks key on the device and the resident
  channel, and the CI action on the emulator and the host — no per-target branching anywhere.

## Alternatives considered

- **Fold Layer 3 into `android-e2e.yml`'s existing `poll_cpuinfo` sampler, or leave that input
  alone.** The lane already carries a background sampler for this exact question: a `workflow_dispatch`
  input that, when set, appends `adb shell dumpsys cpuinfo` to `runs/cpuinfo.log` every two seconds
  during `smoke`, so a flake-hunting dispatch can be checked against host processor pressure. Layer 3
  **coexists with it and does not remove it**, because the two measure different things from different
  sides: `poll_cpuinfo` reads the *guest's* view over adb, Layer 3 reads the *host's* own load. The
  reason `poll_cpuinfo` stays off for every `pull_request` and `merge_group` run does not carry over
  either — that input is off because "polling every 2s is itself continuous adb traffic and host load
  on the very lane whose CPU contention it measures," an observer effect a host-side `top`/`free`
  every ~20 seconds does not have: it issues no adb traffic at all and samples a hundredth as often.
  Layer 3 is therefore always on where `poll_cpuinfo` is opt-in. An implementer should read the two as
  complementary, not as one superseding the other.
- **Share one composite action between the iOS and Android lanes.** Rejected: `collect-ios-diagnostics`
  reads `simctl`/CoreSimulator/the macOS unified log, and `collect-android-diagnostics` reads
  `adb`/logcat/the Linux host — the underlying tools share no surface, so a single action would be an
  `if backend == …` fork rather than shared logic. The two stay separate actions that happen to share
  a design (the always/on-failure split, the `start`/`collect` telemetry phases) and, for Layer 1, a
  literal environment-variable name.
- **Gate this item on [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md)
  landing first, since it defines `BAJUTSU_STALL_DIAGNOSTICS`.** Rejected: BE-0361 is itself an
  unimplemented `Proposal`, and making Android's zero-collection gap wait on it would leave the wider
  gap open indefinitely for no code-sharing reason — the two proposals share a variable-*name*
  convention, not a shared module either depends on, so either can implement it first and the other
  follows the same contract.
  Whichever lands first defines `BAJUTSU_STALL_DIAGNOSTICS` in the shared stall-probe documentation;
  the second wires its own backend's trigger points into the existing name.
- **Give the adb backend a real `AdbBackendCrashError(base.BackendCrashError)` and wire the stall probe
  to it, closing the crash-*detection* gap this item's Motivation surfaces.** Left out of scope:
  deciding when an adb-side failure is severe enough to discard the current lease and retry the whole
  scenario is a recovery-semantics question — what BE-0353's crash-triggered retry should actually
  trigger *on* for this backend — not a diagnostics-collection one, and conflating the two would make
  this item responsible for a behavior change well past what "collect more evidence" needs. A future
  item can take up detection; this one's Layer 1 hooks on the failure signal the adb driver already
  raises today (`AdbResidentError`) instead of waiting on one that does not yet exist.
- **Run `adb bugreport` on every job, not only on failure.** Rejected: a bugreport runs tens of
  seconds and produces a multi-megabyte archive — far lighter than macOS's `sysdiagnose`, which
  BE-0361 defers for exactly this reason, but still real cost multiplied across six jobs on
  every green run. The always/on-failure split keeps the cheap tier unconditional and reserves the
  heavier collector for the runs that actually need it.
- **Stream the full `adb logcat` ring buffer for the whole job instead of a point-in-time dump.** The
  per-scenario `deviceLog` interval (`bajutsu/evidence/intervals.py`'s `start_logcat`) already
  streams a scenario's own window; a second whole-job stream would duplicate most of that content
  for a benefit the always-tier's `logcat -d` dump — read once, after the fact, from the device's own
  retained buffer — already provides at a fraction of the cost, the same reasoning BE-0361 uses to
  reject streaming the iOS unified log for a whole job.

## Progress

> Keep this current as work proceeds. The checklist mirrors the `MECE` work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — screenrecord growth watcher for the Android `video` provider
- [ ] Unit 2 — stall-time probe hook (hierarchy-read fallback, the new no-growth warning)
- [ ] Unit 3 — `scripts/collect_android_diagnostics.sh` and its per-job `script:` wiring
- [ ] Unit 4 — the `collect-android-diagnostics` composite action (host telemetry only)
- [ ] Unit 5 — docs (`docs/ci.md` and its `ja` mirror)
- [ ] Unit 6 — tests

## References

- [BE-0361](../BE-0361-ios-ci-simulator-diagnostics/BE-0361-ios-ci-simulator-diagnostics.md) — the
  three-layer diagnostics design this item ports to the Android backend, and the source of the
  shared `BAJUTSU_STALL_DIAGNOSTICS` variable name
- [BE-0353](../BE-0353-xcuitest-adb-crash-retry-device-recovery/BE-0353-xcuitest-adb-crash-retry-device-recovery.md) —
  the adb crash-triggered retry whose own design already assumes a backend-crash signal this item's
  Motivation shows does not yet exist for adb
- [BE-0245](../BE-0245-adb-resident-uiautomator-server/BE-0245-adb-resident-uiautomator-server.md) —
  the resident channel whose `AdbResidentError` is this item's first stall trigger
- [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md) —
  the adb driver conformance contract this item's collection instruments failures for, without
  changing
- [BE-0350](../BE-0350-ondevice-conformance-evidence-capture/BE-0350-ondevice-conformance-evidence-capture.md) —
  the video/deviceLog evidence this item's collection supplements for the same on-device suites
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

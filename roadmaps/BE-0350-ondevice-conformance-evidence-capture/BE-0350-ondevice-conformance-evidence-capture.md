**English** · [日本語](BE-0350-ondevice-conformance-evidence-capture-ja.md)

# BE-0350 — Give the on-device conformance and fault-injection suites the video and deviceLog evidence every scenario-driven CI job already gets

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0350](BE-0350-ondevice-conformance-evidence-capture.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0350") |
| Implementing PR | [#1523](https://github.com/bajutsu-e2e/bajutsu/pull/1523) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Every CI job that runs a scenario through `bajutsu run` captures `video` and `deviceLog` evidence
for that scenario. Four on-device jobs do not: `conformance (adb)`, `fault-injection (adb)`, and
their iOS twins `conformance (xcuitest)` and `fault-injection (xcuitest)`. These jobs drive their
backend straight from pytest and inherit none of that capture. A failure in any of the four
produces no artifact to diagnose it.

This item gives all four suites the same interval evidence the pipeline already provides: a screen
recording and a device-log stream, one pair per test. The pair is kept for the test that produced it
only when that test fails. A failure at or after the test's own setup then ships with the same video
and device log that a scenario-driven failure already gets. Unit 2 notes the one gap this leaves: a
failure during a suite's own module-scoped backend bring-up, before that setup starts.

## Motivation

The gap is observed, not hypothetical. Pull request
[#1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520) only added `deviceLog` capture to
scenario YAML files. On that pull request, `conformance (adb)` failed
`test_a_read_postdates_a_content_moving_gesture` with `bajutsu.drivers.base.ElementNotFound:
scroll: {'id': 'conformance.scroll.row.19'} not found; the region did not change ... (end of
content)`. A screen recording would settle this kind of scroll-timing mismatch at a glance.

The job's own log ends with `No files were found with the provided path: runs/. No artifacts will
be uploaded.` The workflow's "Upload run artifacts" step already exists and already targets
`runs/`, the path every scenario-driven Android job populates. Neither on-device pytest suite ever
writes anything under that path.

The missing capture follows from how these suites reach the device. The capture code itself is not
at fault. `FileSink` in `bajutsu/evidence/core.py` starts and stops `video`/`deviceLog` intervals
from a scenario's `capture:` list, entirely inside the `bajutsu run` pipeline
(`bajutsu/runner/pool.py`). Four test files bypass that pipeline:

- `tests/test_driver_conformance_ondevice_android.py`
- `tests/test_fault_injection_ondevice_android.py`
- `tests/test_driver_conformance_ondevice.py`
- `tests/test_fault_injection_ondevice.py`

Each calls `launch_driver` directly from a module-scoped pytest fixture. This is a deliberate
design, recorded in
[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md).
BE-0334 gave the iOS conformance suite, which has this same pytest-harness shape,
infrastructure-fault recovery for the identical reason. Bypassing the pipeline buys a driver-level
contract test. It also costs everything the pipeline supplies for free, evidence capture included.

The iOS conformance job carries a second layer of the same gap. Its CI workflow (`ios-e2e.yml`)
uploads a BE-0334 recovery-count report for the job, but has no "Upload run artifacts" step for
`runs/` at all. Every other job has one:

- `run`, `actuation`, `golden`, and `bundled-runner` get the step from the shared `bajutsu-e2e`
  composite action.
- `fault-injection` and `visual` declare their own inline copy.
- Every on-device job in `android-e2e.yml` carries it too.

## Detailed design

Four units, each landable on its own.

### Unit 1 — A per-test, backend-agnostic capture fixture built on the pipeline's own primitives

Add a shared pytest fixture that wraps each test with two interval starters. These are the functions
the scenario pipeline itself calls, and they are already independent of any scenario or YAML:

- `bajutsu.evidence.intervals.start_screenrecord` and `start_logcat` for adb.
- `intervals.start_video` and `start_device_log` for XCUITest.

`demos/showcase/android/screenrecord.py` already calls `start_screenrecord` directly outside
`bajutsu run`, for the codegen lane's `connectedAndroidTest`. This unit applies the same approach
in a pytest fixture instead of a backgrounded script. The fixture stays backend-agnostic. It takes
the two starters as explicit parameters rather than hardcoding either backend, so one
implementation serves all four suites.

Two thin helpers sit between the fixture and the primitives:

- `android_screenrecord` pre-binds the adb-specific size, bit-rate, and time-limit bounds that both
  Android suites share, so neither call site repeats them. It also clears `adb.VIDEO_DEVICE_PATH`
  before every spawn. `start_screenrecord` records to that one fixed device-side path and pulls
  whatever sits there unconditionally. A clip that a prior test failed to pull would otherwise
  arrive as this test's evidence. That clip is non-empty, so the missing/empty check would not
  notice it.
- `xcuitest_video` pre-binds `confirm_started=True`, which `android_screenrecord` sets too. A bare
  spawn proves merely that the local client started. A fast-failing case can tear down before the
  recording produces anything, and then ships an absent or unplayable mp4 for exactly the failure
  this evidence exists to explain.

Scope the capture per test, not per module. The device-side `screenrecord` stops at an approximate
180-second ceiling, even when a caller sets `time_limit` explicitly. In the job that motivated this
item, the conformance module's 18 cases already ran for 177 seconds. A single module-spanning
recording would start truncating its tail the moment one more case is added, hiding the evidence a
diagnosis needs. A per-test recording stays well under the ceiling. It also lets a reader find a
failure's video by its own test name, instead of scrubbing one long recording for the moment the
failure happened.

### Unit 2 — Keep the artifact only when its test fails

Discard the recorded video and device log when the wrapped test passes. Keep them under
`runs/<lane>/<test-id>/` when the test fails. This mirrors the failure-only retention that the
Makefile target for `screenrecord.py` already applies to the codegen lane.

The decision cannot live inside the fixture's own teardown. `_evidence` is autouse and
function-scoped, so among a test's function-scoped fixtures it is set up first and torn down last.
Pytest builds the "teardown" `TestReport` after every finalizer for the item has run, the fixture's
own included. Code in the fixture's teardown can see the "call" phase's report, which exists by
then. It cannot see the teardown failure of a sibling fixture that was torn down earlier, because
that failure has not been reported yet.

The fault-injection suites make this a real case. The post-`yield` half of their per-test `driver`
fixture puts the display back to a known state. A display that will not wake is exactly the failure
a recording exists to explain.

Defer the decision to a `pytest_runtest_makereport` hook instead. The same mechanism is already used
by `tests/backend_crash_recovery.py` to classify a report for the on-device conformance suite
(BE-0334). Use the hook's "teardown" invocation, the first point that has seen the whole attempt.

The fixture registers its directory for that hook to sweep unconditionally, whether or not either
`start_*` call succeeded. A start failure is warned about, not raised, so it never reaches the
outcome tag. Leaving the directory unregistered would strand whatever the video alone managed to
record on a test that then passes.

A green run contributes nothing of its own. The "Upload run artifacts" step therefore carries just
what the job already wrote to `runs/`. On `conformance (xcuitest)`, that is `runs/runner-logs`, from
the workflow-level `BAJUTSU_XCUITEST_RUNNER_LOG`. The job's missing step also discarded that runner
log. The step starts carrying per-test evidence on the first real failure.

`_evidence`'s function scope is also this item's boundary. Each suite's backend bring-up runs at
module scope, ahead of every function-scoped fixture:

- `_eff`, `_adb_driver`, and `_component` in the Android suites
- `_backend_launch` in the iOS conformance suite
- `_backend_lease_holder` in `backend_crash_recovery.py`

All of these set up before `_evidence`, never after it. Two concrete triggers follow from that
ordering:

- `launch_driver` can fail to install or launch the app.
- The XCUITest runner can miss readiness within `BAJUTSU_XCUITEST_STARTUP_TIMEOUT` (300 seconds).

Either one aborts the module's setup at its bring-up fixture. `_evidence` then never runs, and
`runs/<lane>/<test-id>/` is never created. Every case in the module errors with neither a video nor
a device log. `backend_crash_recovery` (BE-0334) re-enters the same module-scoped bring-up on its
retry, still ahead of `_evidence`. A bring-up that keeps crashing until the retry budget runs out
publishes its failing setup report with nothing attached either.

The coverage claims in the Introduction and in Unit 3 therefore hold for a failure at or after each
test's own function-scoped setup. A failure during a suite's own module-scoped bring-up sits
outside this item's scope.

The hook must reset the per-attempt outcome at each "setup" report and accumulate it, never reset
it, afterward. Latching it true forever would be wrong. The iOS conformance suite's own
`backend_crash_recovery` marker (BE-0334) re-runs a whole item, this fixture included, via
`_initrequest()` on an infrastructure-fault retry. The re-run reuses the same `pytest.Item` and
therefore the same stash across attempts. A tag that only ever turned true would still read
"failed" on a later attempt that recovered and passed. That would wrongly keep evidence a passing
test does not need. By then the crashed attempt's own recording is gone anyway, cleared from the
same directory by the attempt that superseded it. Resetting at each attempt's "setup" report keeps
the terminal attempt's outcome in view, which is the attempt `backend_crash_recovery` publishes.

### Unit 3 — Wire the fixture into all four suites, autouse

Add the fixture to these files as an autouse fixture, so every case in all four suites gets it
without opting in per test:

- `tests/test_driver_conformance_ondevice_android.py`
- `tests/test_fault_injection_ondevice_android.py`
- `tests/test_driver_conformance_ondevice.py`
- `tests/test_fault_injection_ondevice.py`

The coverage runs from each test's own setup onward. Unit 2 notes the module-scoped bring-up window
it leaves out. Each suite passes its own lane name (`conformance-adb` / `fault-injection-adb` /
`conformance-xcuitest` / `fault-injection-xcuitest`). Every job's uploaded artifact then stays
self-contained, and no two collide on the same path.

### Unit 4 — Give the iOS conformance job an "Upload run artifacts" step

Add the upload step to the `conformance (xcuitest)` job, which had none before this item. Use the
same `path: runs/`, `if-no-files-found: ignore` step that other jobs already have:

- In `ios-e2e.yml`, the shared `bajutsu-e2e` composite action supplies it on `run`, `actuation`,
  `golden`, and `bundled-runner`. `fault-injection` and `visual` declare it inline.
- Every on-device job in `android-e2e.yml` already carries it.

Without the step, Unit 3's fixture would populate `runs/` in that job for nothing, since the
workflow would never pick the directory up.

## Alternatives considered

- **Ship Android only and defer iOS to a follow-up item.** Rejected. The explicit
  `start_video`/`start_log` injection in `capture()` (Unit 1) already generalizes across backends,
  and the shared function keeps no Android-specific assumption. Withholding iOS would cost a second
  review cycle and buy nothing. The iOS conformance job also needed the fix more. Its workflow was
  missing the "Upload run artifacts" step outright (Unit 4), beyond the missing fixture.
- **Route the suites through `bajutsu run` so they inherit `FileSink` verbatim.** Rejected, for the
  reason BE-0334 recorded for the same pytest-harness shape. These are driver-level contract tests,
  not scenario-level ones. Reshaping them into scenarios to reach the pipeline's plumbing would
  distort the contract to reach evidence capture, rather than test the driver.
- **Capture one continuous recording per module instead of per test.** Rejected. The approximate
  180-second ceiling of `screenrecord` is already within a few seconds of the conformance module's
  measured runtime. A module-spanning recording would start truncating as soon as one more case is
  added, hiding the tail a diagnosis needs most.
- **Always keep the artifact regardless of outcome.** Rejected. This matches neither the codebase's
  own precedent nor the point of a diagnostic artifact. The Makefile target for `screenrecord.py`
  discards its recording on a green run. A passing test has nothing to diagnose, and keeping its
  recording would grow the uploaded artifact with every case added, for no benefit.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — a per-test, backend-agnostic capture fixture built on
      `intervals.start_screenrecord`/`start_logcat` (adb) and `intervals.start_video`/
      `start_device_log` (XCUITest).
- [x] Unit 2 — keep the video and device log only for a failing test, decided from a
      `pytest_runtest_makereport` hook's "teardown" report rather than the fixture's own teardown,
      with the per-attempt outcome reset (never just latched) at each "setup" report.
- [x] Unit 3 — wire the fixture into all four suites (`conformance`/`fault-injection` × adb/XCUITest)
      as an autouse fixture.
- [x] Unit 4 — add the missing "Upload run artifacts" step to `conformance (xcuitest)`.

## References

- [PR #1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520) — the change whose `conformance
  (adb)` run motivated this item, failing with no artifact to diagnose it.
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py) — `start_screenrecord`/
  `start_logcat` (adb) and `start_video`/`start_device_log` (XCUITest), the primitives this item
  reuses directly.
- [`demos/showcase/android/screenrecord.py`](../../demos/showcase/android/screenrecord.py) — the
  existing precedent for calling those primitives outside `bajutsu run`, and for discarding the
  recording on a green run.
- [`tests/backend_crash_recovery.py`](../../tests/backend_crash_recovery.py) — the sibling on-device
  suite plugin whose `pytest_runtest_makereport` hook this item's Unit 2 reuses the same way, and
  whose item-reusing retry loop is why Unit 2's tag must be overwritten rather than latched.
- [`tests/test_driver_conformance_ondevice_android.py`](../../tests/test_driver_conformance_ondevice_android.py),
  [`tests/test_fault_injection_ondevice_android.py`](../../tests/test_fault_injection_ondevice_android.py),
  [`tests/test_driver_conformance_ondevice.py`](../../tests/test_driver_conformance_ondevice.py), and
  [`tests/test_fault_injection_ondevice.py`](../../tests/test_fault_injection_ondevice.py) — the four
  suites this item wires the fixture into.
- [`.github/workflows/ios-e2e.yml`](../../.github/workflows/ios-e2e.yml) — where Unit 4 adds the
  `conformance (xcuitest)` job's missing upload step.
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the driver
  conformance contract every `conformance` job enforces.
- [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md) —
  the on-device adb conformance suite this item instruments.
- [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md)
  — the infrastructure-fault recovery the iOS conformance suite already has; this item's Unit 2
  interacts with it directly (both retry the same item) rather than merely sharing its pytest-harness
  shape.

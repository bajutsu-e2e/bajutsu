**English** · [日本語](BE-XXXX-android-conformance-evidence-capture-ja.md)

# BE-XXXX — Give the Android on-device conformance and fault-injection suites the video and deviceLog evidence every scenario-driven CI job already gets

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-android-conformance-evidence-capture.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | TBD — filled in once the PR is opened (this is a BE-creation PR; the id and PR are not opened by this session) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Every Android CI job that runs a scenario through `bajutsu run` captures `video` and `deviceLog`
evidence for it, but the on-device `conformance (adb)` and `fault-injection (adb)` jobs drive the
adb driver straight from pytest and inherit none of that capture: a failure in either job produces
no artifact to diagnose it. This item adds the same interval evidence the pipeline already
provides — a screen recording and a `logcat` stream, one pair per test — to both suites, kept only
for the test that produced them when it fails, so a failure in either job ships with the same video
and device log a scenario-driven failure already gets.

## Motivation

The gap is observed, not hypothetical. On pull request
[#1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520), whose diff only added `deviceLog` capture
to scenario YAML files, `conformance (adb)` failed
`test_a_read_postdates_a_content_moving_gesture` with `bajutsu.drivers.base.ElementNotFound:
scroll: {'id': 'conformance.scroll.row.19'} not found; the region did not change ... (end of
content)` — exactly the kind of scroll-timing mismatch a screen recording would settle at a glance.
The job's own log ends with `No files were found with the provided path: runs/. No artifacts will
be uploaded.`: the workflow's "Upload run artifacts" step already exists and already targets
`runs/`, the same path every scenario-driven Android job populates, but neither on-device pytest
suite ever writes anything under it.

The missing capture is a consequence of how both suites reach the device, not an oversight in the
capture code itself. `bajutsu/evidence/core.py`'s `FileSink` starts and stops `video`/`deviceLog`
intervals from a scenario's `capture:` list, entirely inside the `bajutsu run` pipeline
(`bajutsu/runner/pool.py`). `tests/test_driver_conformance_ondevice_android.py` and
`tests/test_fault_injection_ondevice_android.py` call `launch_driver` directly from a module-scoped
pytest fixture instead — a deliberate design already recorded in
[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md),
which gave the iOS twin of this same pytest-harness shape its infrastructure-fault recovery for the
identical reason: bypassing the pipeline buys the suites a driver-level contract test, and it costs
them everything the pipeline supplies for free, evidence capture included.

## Detailed design

Three units, each landable on its own.

### Unit 1 — A per-test capture fixture built on the pipeline's own primitives

Add a shared pytest fixture that wraps each test with `bajutsu.evidence.intervals.start_screenrecord`
and `start_logcat` — the same functions the scenario pipeline itself calls, already independent of
any scenario or YAML. `demos/showcase/android/screenrecord.py` already calls
`start_screenrecord` directly outside `bajutsu run`, for the codegen lane's
`connectedAndroidTest`; this unit applies the same approach to a pytest fixture instead of a
backgrounded script.

Scope the capture per test, not per module. `screenrecord`'s device-side recording stops at an
approximately 180-second ceiling regardless of whether a caller sets `time_limit` explicitly, and
the conformance module's 18 cases already ran for 177 seconds in the job that motivated this item —
a single module-spanning recording would start truncating its tail the moment one more case is
added, hiding the exact evidence a diagnosis needs. A per-test recording stays well under that
ceiling and lets a failure's video be found by its own test name, rather than by scrubbing one long
recording for the moment it happened.

### Unit 2 — Keep the artifact only when its test fails

Discard the recorded video and device log when the test they wrap passes; keep them under
`runs/<lane>/<test-id>/` only when it fails, mirroring the failure-only retention
`screenrecord.py`'s own Makefile target already applies to the codegen lane. Telling pass from fail
inside a fixture's teardown needs the test's own report, which a fixture does not otherwise see;
tag it with a `pytest_runtest_makereport` hook, the same mechanism
`tests/backend_crash_recovery.py` already uses to classify a report for the on-device conformance
suite (BE-0334) at the same hook point. A green run then uploads nothing, so the existing "Upload
run artifacts" step's `if-no-files-found: ignore` keeps no-opping on a clean suite and starts
filling only on the first real failure.

### Unit 3 — Wire the fixture into both suites, autouse

Add the fixture to `tests/test_driver_conformance_ondevice_android.py` and
`tests/test_fault_injection_ondevice_android.py` as an autouse fixture, so every case in both suites
is covered without opting in per test. Each suite passes its own lane name (`conformance-adb` /
`fault-injection-adb`) so the two jobs' uploaded artifacts stay self-contained and never collide on
the same path.

## Alternatives considered

- **Route both suites through `bajutsu run` so they inherit `FileSink` verbatim.** Rejected for the
  reason BE-0334 already recorded for the same pytest-harness shape: both suites are driver-level
  contract tests, not scenario-level ones, and reshaping them into scenarios to reach the pipeline's
  plumbing would distort the contract to reach evidence capture rather than to test the driver.
- **Capture one continuous recording per module instead of per test.** Rejected. `screenrecord`'s
  approximately 180-second ceiling is already within a few seconds of the conformance module's own
  measured runtime, so a module-spanning recording would start truncating as soon as one more case
  is added, hiding the tail a diagnosis needs most.
- **Always keep the artifact regardless of outcome.** Rejected. It matches neither the codebase's
  own precedent (`screenrecord.py`'s Makefile target discards its recording on a green run) nor the
  point of a diagnostic artifact: a passing test has nothing to diagnose, and keeping its recording
  anyway would grow the uploaded artifact with every case added, for no benefit.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — a per-test capture fixture built on `intervals.start_screenrecord`/`start_logcat`.
- [x] Unit 2 — keep the video and device log only for a failing test, tagged via
      `pytest_runtest_makereport`.
- [x] Unit 3 — wire the fixture into both `conformance (adb)` and `fault-injection (adb)` as an
      autouse fixture.

## References

- [PR #1520](https://github.com/bajutsu-e2e/bajutsu/pull/1520) — the change whose `conformance
  (adb)` run motivated this item, failing with no artifact to diagnose it.
- [`bajutsu/evidence/intervals.py`](../../bajutsu/evidence/intervals.py) — `start_screenrecord` and
  `start_logcat`, the primitives this item reuses directly.
- [`demos/showcase/android/screenrecord.py`](../../demos/showcase/android/screenrecord.py) — the
  existing precedent for calling those primitives outside `bajutsu run`, and for discarding the
  recording on a green run.
- [`tests/backend_crash_recovery.py`](../../tests/backend_crash_recovery.py) — the sibling on-device
  suite plugin whose `pytest_runtest_makereport` hook this item's Unit 2 reuses the same way.
- [`tests/test_driver_conformance_ondevice_android.py`](../../tests/test_driver_conformance_ondevice_android.py)
  and
  [`tests/test_fault_injection_ondevice_android.py`](../../tests/test_fault_injection_ondevice_android.py)
  — the two suites this item wires the fixture into.
- [BE-0114](../BE-0114-driver-conformance-suite/BE-0114-driver-conformance-suite.md) — the driver
  conformance contract `conformance (adb)` enforces.
- [BE-0270](../BE-0270-android-adb-driver-conformance/BE-0270-android-adb-driver-conformance.md) —
  the on-device adb conformance suite this item instruments.
- [BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md)
  — the iOS twin of this same pytest-harness gap, closed for infrastructure-fault recovery rather
  than evidence capture; this item closes the evidence gap the Android suites share with it.

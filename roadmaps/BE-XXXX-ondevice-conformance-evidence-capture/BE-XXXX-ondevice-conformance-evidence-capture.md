**English** · [日本語](BE-XXXX-ondevice-conformance-evidence-capture-ja.md)

# BE-XXXX — Give the on-device conformance and fault-injection suites the video and deviceLog evidence every scenario-driven CI job already gets

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-ondevice-conformance-evidence-capture.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1523](https://github.com/bajutsu-e2e/bajutsu/pull/1523) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

Every CI job that runs a scenario through `bajutsu run` captures `video` and `deviceLog` evidence
for it, but the on-device `conformance (adb)`/`fault-injection (adb)` jobs and their iOS twins
`conformance (xcuitest)`/`fault-injection (xcuitest)` drive their backend straight from pytest and
inherit none of that capture: a failure in any of the four produces no artifact to diagnose it.
This item adds the same interval evidence the pipeline already provides — a screen recording and a
device-log stream, one pair per test — to all four suites, kept only for the test that produced
them when it fails, so a failure in any of them ships with the same video and device log a
scenario-driven failure already gets.

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

The missing capture is a consequence of how these suites reach the device, not an oversight in the
capture code itself. `bajutsu/evidence/core.py`'s `FileSink` starts and stops `video`/`deviceLog`
intervals from a scenario's `capture:` list, entirely inside the `bajutsu run` pipeline
(`bajutsu/runner/pool.py`). `tests/test_driver_conformance_ondevice_android.py`,
`tests/test_fault_injection_ondevice_android.py`, and their iOS twins
`tests/test_driver_conformance_ondevice.py`/`tests/test_fault_injection_ondevice.py` call
`launch_driver` directly from a module-scoped pytest fixture instead — a deliberate design already
recorded in
[BE-0334](../BE-0334-conformance-suite-infra-fault-recovery/BE-0334-conformance-suite-infra-fault-recovery.md),
which gave the iOS conformance suite's own version of this pytest-harness shape its
infrastructure-fault recovery for the identical reason: bypassing the pipeline buys the suites a
driver-level contract test, and it costs them everything the pipeline supplies for free, evidence
capture included. The iOS conformance job carries a second layer of the same gap: its CI workflow
(`ios-e2e.yml`) uploads a BE-0334 recovery-count report for it, but never an "Upload run artifacts"
step for `runs/` at all — unlike every other job in that workflow.

## Detailed design

Four units, each landable on its own.

### Unit 1 — A per-test, backend-agnostic capture fixture built on the pipeline's own primitives

Add a shared pytest fixture that wraps each test with two interval starters — the same functions
the scenario pipeline itself calls, already independent of any scenario or YAML:
`bajutsu.evidence.intervals.start_screenrecord`/`start_logcat` for adb,
`intervals.start_video`/`start_device_log` for XCUITest. `demos/showcase/android/screenrecord.py`
already calls `start_screenrecord` directly outside `bajutsu run`, for the codegen lane's
`connectedAndroidTest`; this unit applies the same approach to a pytest fixture instead of a
backgrounded script. The fixture itself stays backend-agnostic — it takes the two starters as
explicit parameters rather than hardcoding either backend — so a single implementation serves all
four suites; a small `android_screenrecord` helper pre-binds the adb-specific size, bit-rate, and
time-limit bounds both Android suites share, so neither call site repeats it.

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
`screenrecord.py`'s own Makefile target already applies to the codegen lane.

The decision cannot live inside the fixture's own teardown. `_evidence` is autouse, so it is set up
first among a test's fixtures and therefore torn down last; pytest only builds the "teardown"
`TestReport` after every finalizer for the item — the fixture's own included — has already run. A
fixture reading its test's outcome from inside its own teardown code can see the "call" phase's
report (already produced by then), but never an *earlier-torn-down sibling fixture's own teardown
failure*, since that failure has not been reported yet at the point the fixture's code runs. That is
not a corner case for the fault-injection suites: their per-test `driver` fixture's post-`yield` half
puts the display back to a known state, and a display that will not wake is exactly the failure a
recording exists to explain. Defer the decision to a `pytest_runtest_makereport` hook instead — the
same mechanism `tests/backend_crash_recovery.py` already uses to classify a report for the on-device
conformance suite (BE-0334) — specifically to the hook's own "teardown" invocation, the first point
that has actually seen the whole attempt. The fixture registers its directory as eligible for that
hook to sweep only once its own two `start_*` calls have already succeeded, so a setup failure in the
fixture itself leaves the directory unregistered, and therefore un-swept, by default. A green run
then uploads nothing, so the existing "Upload run artifacts" step's `if-no-files-found: ignore` keeps
no-opping on a clean suite and starts filling only on the first real failure.

The per-attempt outcome the hook tracks must be reset at each "setup" report and only accumulated
(never reset) afterward, rather than latched true forever, because the iOS conformance suite's own
`backend_crash_recovery` marker (BE-0334) re-runs a whole item — this fixture included — via
`_initrequest()` on an infrastructure-fault retry, reusing the same `pytest.Item` and therefore the
same stash across attempts. A tag that only ever turned true would still read "failed" on a later
attempt that recovered and passed, wrongly keeping evidence a passing test does not need — and by
then the crashed attempt's own recording is already gone anyway, overwritten at the same file path by
the attempt that superseded it. Resetting at each attempt's own "setup" report keeps only the
terminal attempt's outcome in view, which is exactly the attempt `backend_crash_recovery` itself
publishes.

### Unit 3 — Wire the fixture into all four suites, autouse

Add the fixture to `tests/test_driver_conformance_ondevice_android.py`,
`tests/test_fault_injection_ondevice_android.py`, `tests/test_driver_conformance_ondevice.py`, and
`tests/test_fault_injection_ondevice.py` as an autouse fixture, so every case in all four suites is
covered without opting in per test. Each suite passes its own lane name (`conformance-adb` /
`fault-injection-adb` / `conformance-xcuitest` / `fault-injection-xcuitest`) so every job's uploaded
artifact stays self-contained and none collide on the same path.

### Unit 4 — Give the iOS conformance job an "Upload run artifacts" step

Add the same `path: runs/`, `if-no-files-found: ignore` upload step every other on-device job in
`ios-e2e.yml`/`android-e2e.yml` already carries to the `conformance (xcuitest)` job, which had none
at all before this item — Unit 3's fixture would otherwise populate `runs/` in that job for nothing,
since the workflow never picked it up.

## Alternatives considered

- **Ship Android only and defer iOS to a follow-up item.** Rejected. `capture()`'s explicit
  `start_video`/`start_log` injection (Unit 1) already generalizes across backends with no
  Android-specific assumption left in the shared function, so withholding iOS would cost a second
  review cycle to buy nothing; the iOS conformance job also needed the fix more, since its own
  workflow was missing the "Upload run artifacts" step outright (Unit 4), not only the fixture.
- **Route the suites through `bajutsu run` so they inherit `FileSink` verbatim.** Rejected for the
  reason BE-0334 already recorded for the same pytest-harness shape: these are driver-level contract
  tests, not scenario-level ones, and reshaping them into scenarios to reach the pipeline's plumbing
  would distort the contract to reach evidence capture rather than to test the driver.
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

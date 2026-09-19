"""The pipeline's post-return app-crash scan and its `app-crash/` write (BE-0424).

The app-side sibling of `test_pipeline.py`'s BE-0421 crash-evidence tests, and deliberately a
*different* path: a backend crash escapes `run_scenario` as an exception and drives the retry loop,
while an app crash is classified in-band and reaches here as an ordinary failed `RunResult` carrying
an `app_crashed` outcome. These tests pin the three things that are easy to get wrong about that
seam — where the scan looks, when the tombstone layer is called, and what reaches the manifest.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

from _runner import _eff, _el, _fake_driver

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.evidence import NullSink
from bajutsu.common.platform_lifecycle.protocols import ReadinessResult
from bajutsu.common.report import manifest_dict, results_from_manifest
from bajutsu.common.runner import Lease, LeaseFn, run_all
from bajutsu.common.scenario import Scenario

_REPORT = ("Showcase.ips", b"Thread 0 crashed: fatalError()\n")
_TOMBSTONE = ("tombstone_00", b"backtrace:\n  #00 pc 0000 libapp.so\n")

# A gate that resolved on a real `readyWhen` signal, so the unconfirmed-launch latch starts cleared
# and the scenario's very first failing step is probed. A lease with no readiness at all suppresses
# that first probe by design — an app that never reached the foreground must not read as one that
# crashed — so every lease below has to state which case it is modelling.
_READY = ReadinessResult(ready=True, signal="readyWhen", elapsed_s=0.5)


class _CrashingAppDriver(FakeDriver):
    """A driver whose app is gone: every selector misses and the crash signal confirms why."""

    def app_crash_signal(self) -> str | None:
        return "the app under test is no longer running"


def _recording(
    calls: list[str], label: str, value: list[tuple[str, bytes]]
) -> Callable[[], list[tuple[str, bytes]]]:
    def call() -> list[tuple[str, bytes]]:
        calls.append(label)
        return value

    return call


def _app_crash_lease(
    *, tombstone: list[tuple[str, bytes]] | None = None, calls: list[str] | None = None
) -> LeaseFn:
    """A lease whose driver confirms an app crash and whose environment captures a report."""

    def capture() -> list[tuple[str, bytes]]:
        if calls is not None:
            calls.append("artifacts")
        return [_REPORT]

    def pull() -> list[tuple[str, bytes]]:
        if calls is not None:
            calls.append("tombstone")
        return list(tombstone or [])

    def lease(eff: object, scenario: object) -> Lease:
        return Lease(
            driver=_CrashingAppDriver([_el("other", "Other", ["button"])]),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            readiness=_READY,
            app_crash_artifacts=capture,
            app_crash_tombstone=pull,
        )

    return lease


def _scenario(steps: list[dict[str, object]] | None = None) -> Scenario:
    return Scenario.model_validate({"name": "a", "steps": steps or [{"tap": {"id": "ok"}}]})


def test_a_crashed_scenario_gets_its_report_written_and_named(tmp_path: Path) -> None:
    # The observable outcome BE-0424 exists for: the platform's own report lands beside the run's
    # other evidence, and the failure string names the directory so nobody has to already know it is
    # there — the same contract `crash-diagnostics/` holds for the backend's own crash.
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario()], _app_crash_lease(), run_dir=run_dir)

    directory = run_dir / "00-a" / "app-crash"
    assert (directory / "Showcase.ips").read_text(encoding="utf-8") == _REPORT[1].decode()
    assert str(directory) in (results[0].failure or "")


def test_the_tombstone_layer_is_appended_to_the_captured_report(tmp_path: Path) -> None:
    # Two layers, two call sites: the `logcat`/`.ips` layer was captured inside the step loop at
    # confirmation, the tombstone only here, once the scenario has genuinely ended. Both must land.
    run_dir = tmp_path / "runs" / "run1"
    run_all(_eff(), [_scenario()], _app_crash_lease(tombstone=[_TOMBSTONE]), run_dir=run_dir)

    directory = run_dir / "00-a" / "app-crash"
    assert (directory / "Showcase.ips").exists()
    assert (directory / "tombstone_00").exists()


def test_the_tombstone_is_pulled_once_and_only_after_the_capture(tmp_path: Path) -> None:
    # Ordering is the point, not just the count: firing `adb root` while the scenario was still
    # actuating would kill the resident server and discard the very `RunResult` this produces.
    calls: list[str] = []
    run_all(
        _eff(),
        [_scenario()],
        _app_crash_lease(tombstone=[_TOMBSTONE], calls=calls),
        run_dir=tmp_path / "runs" / "run1",
    )

    assert calls == ["artifacts", "tombstone"]


def test_a_failing_tombstone_pull_never_costs_the_layer_already_captured(tmp_path: Path) -> None:
    # A lost tombstone is strictly less evidence, never a different verdict — and never a reason to
    # drop the `logcat`/`.ips` layer that is already on the outcome.
    def exploding() -> list[tuple[str, bytes]]:
        raise OSError("adb root refused")

    def lease(eff: object, scenario: object) -> Lease:
        return Lease(
            driver=_CrashingAppDriver([_el("other", "Other", ["button"])]),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            readiness=_READY,
            app_crash_artifacts=lambda: [_REPORT],
            app_crash_tombstone=exploding,
        )

    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario()], lease, run_dir=run_dir)

    assert (run_dir / "00-a" / "app-crash" / "Showcase.ips").exists()
    assert not results[0].ok


def test_a_passing_scenario_writes_no_app_crash_directory(tmp_path: Path) -> None:
    # The scan keys on an `app_crashed` outcome, not on the lease: a run whose app never went down
    # must not pay a sweep or leave a directory behind.
    calls: list[str] = []
    run_dir = tmp_path / "runs" / "run1"

    def lease(eff: object, scenario: object) -> Lease:
        return Lease(
            driver=_fake_driver(),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            readiness=_READY,
            app_crash_artifacts=_recording(calls, "artifacts", [_REPORT]),
            app_crash_tombstone=_recording(calls, "tombstone", []),
        )

    results = run_all(_eff(), [_scenario()], lease, run_dir=run_dir)

    assert results[0].ok, results[0].failure
    assert calls == []
    assert not (run_dir / "00-a" / "app-crash").exists()


def test_the_scan_finds_a_crash_in_the_after_phase(tmp_path: Path) -> None:
    # `result.steps` alone is not enough: it is `[]` outright when a `before` step fails, and an
    # `after` rule records into its own list — so reading `steps[-1]` would miss both.
    scenario = Scenario.model_validate(
        {
            "name": "a",
            "steps": [{"tap": {"id": "other"}}],
            "after": [{"on": "always", "steps": [{"tap": {"id": "gone"}}]}],
        }
    )
    run_dir = tmp_path / "runs" / "run1"
    run_all(_eff(), [scenario], _app_crash_lease(), run_dir=run_dir)

    assert (run_dir / "00-a" / "app-crash" / "Showcase.ips").exists()


def test_the_scan_finds_a_crash_in_the_before_phase(tmp_path: Path) -> None:
    scenario = Scenario.model_validate(
        {"name": "a", "before": [{"tap": {"id": "gone"}}], "steps": [{"tap": {"id": "other"}}]}
    )
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [scenario], _app_crash_lease(), run_dir=run_dir)

    assert results[0].steps == []
    assert (run_dir / "00-a" / "app-crash" / "Showcase.ips").exists()


def test_an_app_crash_never_triggers_the_backend_crash_retry(tmp_path: Path) -> None:
    # The two paths must stay separate. A backend crash discards the lease and re-runs the scenario;
    # an app crash is a likely defect in the app, so retrying would spend the recovery budget on a
    # scenario likely to fail the same way and risk absorbing a real regression as flakiness.
    leased: list[int] = []

    def lease(eff: object, scenario: object) -> Lease:
        leased.append(1)
        return Lease(
            driver=_CrashingAppDriver([_el("other", "Other", ["button"])]),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            readiness=_READY,
            app_crash_artifacts=lambda: [_REPORT],
        )

    results = run_all(
        _eff(), [_scenario()], lease, crash_retries=3, run_dir=tmp_path / "runs" / "run1"
    )

    assert len(leased) == 1
    assert not results[0].ok


def test_the_report_is_written_through_the_redacting_text_path(tmp_path: Path) -> None:
    # A crash report is text a crashing app can echo a secret into, so it crosses the free-text scrub
    # like every other artifact this pipeline did not author (BE-0331) — `write_text`, not
    # `write_bytes`.
    secret = b"Thread 0 crashed\ntoken=ghp_0123456789abcdefghijklmnopqrstuvwxyz\n"

    def lease(eff: object, scenario: object) -> Lease:
        return Lease(
            driver=_CrashingAppDriver([_el("other", "Other", ["button"])]),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            readiness=_READY,
            app_crash_artifacts=lambda: [("Showcase.ips", secret)],
        )

    run_dir = tmp_path / "runs" / "run1"
    run_all(_eff(), [_scenario()], lease, run_dir=run_dir)

    written = (run_dir / "00-a" / "app-crash" / "Showcase.ips").read_text(encoding="utf-8")
    assert "ghp_0123456789abcdefghijklmnopqrstuvwxyz" not in written


def test_the_raw_bytes_never_reach_the_manifest(tmp_path: Path) -> None:
    # The regression `_scenario_dict`'s exclusion exists to prevent: raw `bytes` has no JSON
    # encoding and `write_json` carries no `default=`, so without the pop the first app-crash
    # scenario would raise `TypeError` writing `manifest.json` — taking the whole run's manifest and
    # HTML report down *after* the crash was correctly classified.
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario()], _app_crash_lease(), run_dir=run_dir)

    manifest = manifest_dict("run1", results)
    json.dumps(manifest)  # no `default=`: the point of the assertion

    scenarios = manifest["scenarios"]
    assert isinstance(scenarios, list)
    scenario = scenarios[0]
    assert isinstance(scenario, dict)
    for phase in ("steps", "before_outcomes", "after_outcomes"):
        for outcome in scenario.get(phase) or []:
            assert "app_crash_artifacts" not in outcome


def test_the_classification_round_trips_while_the_bytes_reconstruct_at_their_default(
    tmp_path: Path,
) -> None:
    # `app_crashed` and `reason` are plain scalars and must survive; the bytes must come back at
    # their `()` default, exactly like a normal step's already-empty one. The durable copy of the
    # evidence is the redacted file under `app-crash/`, not the in-memory field.
    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario()], _app_crash_lease(), run_dir=run_dir)

    # Through JSON the way the run dir does, then back.
    data = json.loads(json.dumps(manifest_dict("run1", results)))
    outcomes = [
        o
        for r in results_from_manifest(data)
        for o in (*r.before_outcomes, *r.steps, *r.after_outcomes)
        if o.app_crashed
    ]
    assert len(outcomes) == 1
    assert outcomes[0].app_crash_artifacts == ()
    assert "no longer running" in outcomes[0].reason


def test_a_backend_that_captures_nothing_leaves_the_failure_untouched(tmp_path: Path) -> None:
    # The web and fake backends declare both methods a no-op, so the write step is an empty iteration
    # and the failure string says what it said before this feature existed.
    def lease(eff: object, scenario: object) -> Lease:
        return Lease(
            driver=_CrashingAppDriver([_el("other", "Other", ["button"])]),
            sink=NullSink(),
            relaunch=None,
            control=None,
            collector=None,
            release=lambda: None,
            readiness=_READY,
        )

    run_dir = tmp_path / "runs" / "run1"
    results = run_all(_eff(), [_scenario()], lease, run_dir=run_dir)

    assert not (run_dir / "00-a" / "app-crash").exists()
    assert "app's own crash report" not in (results[0].failure or "")


def test_the_fake_driver_is_not_an_app_crash_signal() -> None:
    # The seam is a narrow opt-in: the plain fake must not accidentally satisfy it, or every
    # fast-suite failure above would be classified as a crash.
    assert not isinstance(FakeDriver([]), base.AppCrashSignal)
    assert isinstance(_CrashingAppDriver([]), base.AppCrashSignal)

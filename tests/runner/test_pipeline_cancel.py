"""Tests for a cancelled run at the pipeline level (BE-0370).

`run_all` must still return exactly one result per scenario in declaration order — a cancelled
scenario is a failed scenario — so `run_and_report` writes the same `manifest.json` / `report.html`
an ordinary failing run does, and the run lands in the history instead of leaving a silent gap.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from _runner import _eff, _lease

from bajutsu.cancellation import CANCELLED_FAILURE
from bajutsu.drivers.base import BackendCrashError
from bajutsu.orchestrator import RunResult
from bajutsu.runner import run_all, run_and_report, run_matrix_and_report
from bajutsu.scenario import Scenario


def _scenarios(*names: str) -> list[Scenario]:
    return [Scenario.model_validate({"name": n, "steps": [{"tap": {"id": "ok"}}]}) for n in names]


def test_a_cancel_before_the_run_fails_every_scenario_in_order() -> None:
    results = run_all(_eff(), _scenarios("a", "b", "c"), _lease, cancelled=lambda: True)
    # One result per scenario, in declaration order: nothing downstream has to know a cancellation
    # happened, and the manifest's all-must-pass `ok` is False because these carry ok=False.
    assert [r.scenario for r in results] == ["a", "b", "c"]
    assert [r.ok for r in results] == [False, False, False]
    assert {r.failure for r in results} == {CANCELLED_FAILURE}
    # The evidence-dir slug is still stamped, so a report links a cancelled scenario like any other.
    assert [r.sid for r in results] == ["00-a", "01-b", "02-c"]


def test_a_cancel_mid_run_keeps_the_verdicts_already_reached() -> None:
    state = {"cancelled": False}

    def cancelled() -> bool:
        return state["cancelled"]

    def lease(eff, scenario):  # type: ignore[no-untyped-def]
        lz = _lease(eff, scenario)
        if scenario.name != "a":
            return lz
        # The cancel lands as scenario "a" hands its device back — after its own verdict, before "b"
        # is leased at all.
        return replace(lz, release=lambda: state.__setitem__("cancelled", True))

    results = run_all(_eff(), _scenarios("a", "b", "c"), lease, cancelled=cancelled)
    assert [(r.scenario, r.ok, r.failure) for r in results] == [
        ("a", True, None),
        ("b", False, CANCELLED_FAILURE),
        ("c", False, CANCELLED_FAILURE),
    ]


def test_a_cancelled_run_still_writes_its_manifest_and_report(tmp_path: Path) -> None:
    # The failure this item removes: without a manifest there is no `PASS/FAIL runs/<id>/manifest.json`
    # line for serve to read the run id from, so nothing is persisted at all.
    results, manifest = run_and_report(
        _eff(), _scenarios("a", "b"), _lease, tmp_path / "runs", "run1", cancelled=lambda: True
    )
    assert manifest.exists()
    assert (manifest.parent / "report.html").exists()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert [s["failure"] for s in data["scenarios"]] == [CANCELLED_FAILURE, CANCELLED_FAILURE]
    assert [r.ok for r in results] == [False, False]


def test_a_cancel_starts_no_further_engine_pass(tmp_path: Path) -> None:
    # Each pass first builds a whole device_pool — resolving the environment, reading the device
    # catalog, starting the per-device collectors — so a cancelled matrix run must not pay a pass it
    # is only going to fail anyway.
    scenarios = _scenarios("login")
    seen: list[str] = []

    def run_pass(engine: str, run_dir: Path) -> list[RunResult]:
        seen.append(engine)
        return run_all(_eff(), scenarios, _lease, run_dir=run_dir, cancelled=lambda: True)

    results, manifest = run_matrix_and_report(
        _eff(),
        scenarios,
        ["chromium", "firefox", "webkit"],
        run_pass,
        tmp_path / "runs",
        "run1",
        cancelled=lambda: True,
    )
    assert seen == []  # no pool is brought up for an engine the cancel already precedes
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["ok"] is False
    # The matrix still names every requested engine: an axis that never ran carries a cancelled
    # verdict rather than vanishing from the report.
    assert data["matrix"]["engines"] == ["chromium", "firefox", "webkit"]
    assert [r.failure for r in results] == [CANCELLED_FAILURE] * 3


def test_a_cancel_stops_backend_crash_recovery_instead_of_leasing_again() -> None:
    # A retry leases afresh — on XCUITest a cold respawn plus a forced erase — and that bring-up can
    # outlive the grace window the canceller is waiting out, which would let the run be killed before
    # it wrote its manifest: this item's own silent gap, reintroduced on the crash path.
    state = {"cancelled": False}
    leases = 0

    def lease(eff, scenario):  # type: ignore[no-untyped-def]
        nonlocal leases
        leases += 1
        state["cancelled"] = True  # the cancel lands while the scenario is on its device
        raise BackendCrashError("runner crashed mid-scenario")

    results = run_all(
        _eff(),
        _scenarios("a"),
        lease,
        cancelled=lambda: state["cancelled"],
        crash_retries=3,
    )
    assert leases == 1  # the count still allowed three more attempts
    assert [r.ok for r in results] == [False]
    # The failure names the cancel as what ended recovery, rather than blaming a budget that was
    # nowhere near spent.
    assert results[0].failure is not None
    # Led by the exact spelling every other cancelled scenario carries, so a consumer that groups
    # them by prefix still catches this one, with the crash it was recovering from appended.
    assert results[0].failure.startswith(CANCELLED_FAILURE)
    assert "runner crashed mid-scenario" in results[0].failure


def test_a_cancel_after_a_green_engine_pass_still_records_a_failed_run(tmp_path: Path) -> None:
    # The inverse of the silent gap: dropping the engines a cancel skipped would leave the manifest's
    # all-must-pass `ok` aggregating only the pass that ran, so a cancel landing after a green first
    # pass would record a PASS for a run that never executed most of what it was asked to.
    scenarios = _scenarios("login")
    state = {"cancelled": False}

    def run_pass(engine: str, run_dir: Path) -> list[RunResult]:
        passed = run_all(_eff(), scenarios, _lease, run_dir=run_dir)
        state["cancelled"] = True  # the operator cancels as the first engine finishes, all green
        return passed

    results, manifest = run_matrix_and_report(
        _eff(),
        scenarios,
        ["chromium", "firefox"],
        run_pass,
        tmp_path / "runs",
        "run1",
        cancelled=lambda: state["cancelled"],
    )
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert data["ok"] is False
    assert [(r.engine, r.ok) for r in results] == [("chromium", True), ("firefox", False)]
    assert data["matrix"]["cells"]["login"]["firefox"]["failure"] == CANCELLED_FAILURE

"""The run matrix and not-applicable records across every report output (BE-0452 unit 10).

A scenario's `runsOn` conditions form several runs, and an open range a pool has no device for leaves
a not-applicable record. The HTML report, JUnit, CTRF, the manifest's history summary, and the
webhook payload each show both; a run no condition formed renders exactly as before.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from test_ctrf import _validate

from bajutsu.common.orchestrator import RunResult, StepOutcome
from bajutsu.common.report import ctrf_json, html_report, junit_xml, manifest_dict, write_report
from bajutsu.common.report.load import load_run, rerender_html
from bajutsu.common.report.manifest import history_summary, run_matrix
from bajutsu.run.notify import build_summary
from bajutsu.run.notify._functions import _render_slack

_SE17 = "iPhone SE (3rd generation) / iOS 17"
_SE18 = "iPhone SE (3rd generation) / iOS 18"
_NA = {
    "scenario": "checkout",
    "target": "app",
    "coordinates": _SE18,
    "reason": 'runsOn.ios.os  ">=18"  available: iOS 17.5 "iPhone SE (3rd generation)" (5A3F...)',
}


def _run(name: str, ok: bool, coordinates: str = "") -> RunResult:
    return RunResult(
        scenario=name,
        ok=ok,
        steps=[StepOutcome(index=0, action="tap", ok=ok, reason="" if ok else "not found")],
        failure=None if ok else "step 0 tap: not found",
        backend="xcuitest",
        coordinates=coordinates,
        run_coordinates=(
            {"app": {"model": "iPhone SE (3rd generation)", "os": coordinates[-6:]}}
            if coordinates
            else {}
        ),
    )


def _matrix_results() -> list[RunResult]:
    return [
        _run("login", True, _SE17),
        _run("login", True, _SE18),
        _run("checkout", False, _SE17),
    ]


# --- the aggregation ----------------------------------------------------------------------------


def test_run_matrix_places_runs_and_not_applicable_records_by_coordinates() -> None:
    grid = run_matrix(_matrix_results(), [_NA])
    assert grid == {
        "columns": [_SE17, _SE18],
        "scenarios": ["login", "checkout"],
        "cells": {
            "login": {_SE17: {"status": "passed"}, _SE18: {"status": "passed"}},
            "checkout": {
                _SE17: {"status": "failed", "failure": "step 0 tap: not found"},
                _SE18: {"status": "not-applicable", "reason": _NA["reason"]},
            },
        },
    }


def test_run_matrix_heads_an_unconditioned_run_and_splits_a_repeated_name() -> None:
    grid = run_matrix([_run("a", True, _SE17), _run("a", False, _SE17), _run("b", True)])
    assert grid is not None
    assert grid["columns"] == [_SE17, "any device"]
    assert grid["scenarios"] == ["a", "a (2)", "b"]


def test_run_matrix_is_absent_without_coordinates_or_records() -> None:
    assert run_matrix([_run("login", True), _run("checkout", False)]) is None


def test_manifest_carries_the_run_matrix_beside_the_records() -> None:
    data = manifest_dict("r1", _matrix_results(), not_applicable=[_NA])
    assert data["notApplicable"] == [_NA]
    assert data["runMatrix"] == run_matrix(_matrix_results(), [_NA])


# --- the HTML report ----------------------------------------------------------------------------


def test_html_shows_the_matrix_the_count_and_each_reason() -> None:
    html = html_report("r1", _matrix_results(), not_applicable=[_NA])
    assert '<h2 class="mxh">run matrix</h2>' in html
    assert f"<th>{_SE17}</th><th>{_SE18}</th>" in html
    assert ">N/A</td>" in html
    assert "mx-skip" in html
    assert "1 not applicable</span>" in html
    assert "app/checkout [iPhone SE (3rd generation) / iOS 18]" in html
    assert "runsOn.ios.os  &#34;&gt;=18&#34;" in html
    # Each scenario card names its run's coordinates beside the scenario.
    assert f'<span class="scoord" title="the run\'s coordinates">{_SE17}</span>' in html


def test_html_reports_fail_when_every_record_is_not_applicable() -> None:
    html = html_report("r1", [], not_applicable=[_NA])
    assert '<span class="fail">FAIL</span>' in html.split("</h1>")[0]


def test_an_unconditioned_run_renders_without_any_matrix_noise() -> None:
    html = html_report("r1", [_run("login", True), _run("checkout", False)])
    for noise in ("run matrix", "not applicable", "nachip", "scoord", "mx-skip", "nalist"):
        assert noise not in html.split("<script>")[0].split("</style>")[-1]


# --- JUnit and CTRF -----------------------------------------------------------------------------


def test_junit_lists_each_record_as_a_skipped_case() -> None:
    suite = ET.fromstring(junit_xml(_matrix_results(), [_NA]))
    assert suite.get("tests") == "4"
    assert suite.get("failures") == "1"
    assert suite.get("skipped") == "1"
    names = [case.get("name") for case in suite.iter("testcase")]
    assert names == [
        f"login [{_SE17}]",
        f"login [{_SE18}]",
        f"checkout [{_SE17}]",
        f"checkout [{_SE18}]",
    ]
    skipped = suite.findall("testcase")[-1].find("skipped")
    assert skipped is not None
    assert skipped.get("message") == f"not applicable: {_NA['reason']}"


def test_junit_without_records_keeps_its_shape() -> None:
    suite = ET.fromstring(junit_xml([_run("login", True)]))
    assert suite.get("skipped") is None
    assert suite.find("testcase/skipped") is None


def test_ctrf_counts_records_as_skipped_and_carries_the_grid() -> None:
    doc = ctrf_json("20260101-000000", _matrix_results(), not_applicable=[_NA])
    _validate(doc)
    results = doc["results"]
    assert isinstance(results, dict)
    summary = results["summary"]
    assert summary["tests"] == 4
    assert summary["skipped"] == 1
    tests = results["tests"]
    assert tests[-1]["status"] == "skipped"
    assert tests[-1]["name"] == f"checkout [{_SE18}]"
    assert tests[-1]["extra"] == {"notApplicable": True, "target": "app"}
    assert tests[0]["extra"]["coordinates"] == {
        "app": {"model": "iPhone SE (3rd generation)", "os": "iOS 17"}
    }
    assert results["extra"] == {"runMatrix": run_matrix(_matrix_results(), [_NA])}


def test_ctrf_without_records_keeps_its_shape() -> None:
    doc = ctrf_json("20260101-000000", [_run("login", True)])
    results = doc["results"]
    assert isinstance(results, dict)
    assert results["summary"]["skipped"] == 0
    assert "extra" not in results
    assert "coordinates" not in results["tests"][0]["extra"]


# --- stored runs --------------------------------------------------------------------------------


def _stored_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "r1"
    run_dir.mkdir()
    (run_dir / "scenario.yaml").write_text(
        "scenarios:\n"
        "  - name: login\n    steps:\n      - tap: { id: a }\n"
        "  - name: checkout\n    steps:\n      - tap: { id: b }\n",
        encoding="utf-8",
    )
    results = _matrix_results()
    definitions = [{"name": r.scenario} for r in results]
    write_report(run_dir, "r1", results, definitions, ["x"] * 3, not_applicable=[_NA])
    return run_dir


def test_a_stored_run_rerenders_with_its_records_and_aligned_plans(tmp_path: Path) -> None:
    run_dir = _stored_run(tmp_path)
    model = load_run(run_dir)
    assert model.not_applicable == [_NA]
    # One plan per run, found by the run's scenario name rather than its position.
    assert [d["name"] for d in model.definitions] == ["login", "login", "checkout"]
    html = rerender_html(run_dir)
    assert "run matrix" in html
    assert "1 not applicable" in html


def test_history_summary_names_each_scenario_once_and_counts_records(tmp_path: Path) -> None:
    data = json.loads((_stored_run(tmp_path) / "manifest.json").read_text(encoding="utf-8"))
    assert history_summary(data) == {
        "scenarios": ["login", "checkout"],
        "passed": 2,
        "total": 3,
        "notApplicable": 1,
    }


def test_history_summary_of_an_unconditioned_run_is_unchanged() -> None:
    data = manifest_dict("r1", [_run("a", True), _run("a", False)])
    assert history_summary(data) == {"scenarios": ["a", "a"], "passed": 1, "total": 2}


# --- notifications ------------------------------------------------------------------------------


def test_the_webhook_payload_names_coordinates_and_counts_records() -> None:
    summary = build_summary(
        _matrix_results(), run_id="r1", source_name="s", backend="xcuitest", not_applicable=1
    )
    assert summary.not_applicable == 1
    assert summary.failures[0]["scenario"] == f"checkout [{_SE17}]"
    text = json.dumps(_render_slack(summary))
    assert "2 passed, 1 failed / 3, 1 not applicable" in text


def test_a_webhook_with_nothing_but_records_is_not_ok() -> None:
    summary = build_summary([], run_id="r1", source_name="s", backend="x", not_applicable=2)
    assert not summary.ok


def test_an_unconditioned_webhook_payload_is_unchanged() -> None:
    summary = build_summary([_run("a", True)], run_id="r1", source_name="s", backend="x")
    text = json.dumps(_render_slack(summary))
    assert "not applicable" not in text
    assert summary.ok


def test_an_unconditioned_browsers_failure_keeps_its_bare_name() -> None:
    failed = _run("login", False)
    failed.engine = "webkit"
    summary = build_summary([failed], run_id="r1", source_name="s", backend="playwright")
    assert summary.failures[0]["scenario"] == "login"
    assert summary.engine == "webkit"


def test_ctrf_keeps_both_grids_side_by_side() -> None:
    engine = _run("login", True, _SE17)
    engine.engine = "webkit"
    results = ctrf_json("20260101-000000", [engine])["results"]
    assert isinstance(results, dict)
    assert set(results["extra"]) == {"matrix", "runMatrix"}


def test_two_same_named_scenarios_rerender_with_their_own_plans(tmp_path: Path) -> None:
    run_dir = tmp_path / "r2"
    run_dir.mkdir()
    (run_dir / "scenario.yaml").write_text(
        "scenarios:\n"
        "  - name: login\n    description: first\n    steps:\n      - tap: { id: a }\n"
        "  - name: login\n    description: second\n    steps:\n      - tap: { id: b }\n",
        encoding="utf-8",
    )
    results = [_run("login", True, _SE17), _run("login", True, _SE18)] * 2
    write_report(run_dir, "r2", results, [{}] * 4, ["x"] * 4)
    model = load_run(run_dir)
    assert [d["description"] for d in model.definitions] == ["first", "first", "second", "second"]


def test_a_result_without_a_stored_plan_rerenders_without_one(tmp_path: Path) -> None:
    run_dir = tmp_path / "r3"
    run_dir.mkdir()
    (run_dir / "scenario.yaml").write_text(
        "scenarios:\n  - name: login\n    steps:\n      - tap: { id: a }\n", encoding="utf-8"
    )
    write_report(run_dir, "r3", [_run("login", True, _SE17), _run("gone", False, _SE17)])
    assert load_run(run_dir).definitions[1] is None

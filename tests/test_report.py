"""Tests for reporting (manifest.json + JUnit XML).

Drives scenario -> run -> report end to end with the fake driver.
"""

from __future__ import annotations

import json
from pathlib import Path

from simyoke.drivers import base
from simyoke.drivers.fake import FakeDriver
from simyoke.orchestrator import RunResult, run_scenario
from simyoke.report import html_report, junit_xml, manifest_dict, write_report
from simyoke.scenario import Scenario


def _el(identifier: str, label: str, traits: list[str] | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _passing() -> RunResult:
    driver = FakeDriver([_el("home.title", "H"), _el("a", "A", ["button"])])
    return run_scenario(
        driver,
        Scenario.model_validate({
            "name": "s1",
            "steps": [{"tap": {"id": "a"}}],
            "expect": [{"exists": {"id": "home.title"}}],
        }),
    )


def _failing() -> RunResult:
    driver = FakeDriver([_el("a", "A", ["button"])])
    return run_scenario(
        driver,
        Scenario.model_validate({
            "name": "s2",
            "steps": [{"tap": {"id": "a"}}],
            "expect": [{"exists": {"id": "missing"}}],
        }),
    )


def test_manifest_structure() -> None:
    m = manifest_dict("run1", [_passing()])
    assert m["runId"] == "run1"
    assert m["ok"] is True
    scenarios = m["scenarios"]
    assert isinstance(scenarios, list)
    assert scenarios[0]["scenario"] == "s1"
    assert scenarios[0]["ok"] is True
    assert scenarios[0]["steps"][0]["action"] == "tap"


def test_manifest_overall_ok_is_and() -> None:
    assert manifest_dict("r", [_passing(), _failing()])["ok"] is False


def test_junit_pass_and_fail() -> None:
    ok_xml = junit_xml([_passing()])
    assert 'tests="1"' in ok_xml
    assert 'failures="0"' in ok_xml
    assert "<failure" not in ok_xml

    bad_xml = junit_xml([_failing()])
    assert 'failures="1"' in bad_xml
    assert "<failure" in bad_xml


def test_write_report(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "run3"
    manifest_path = write_report(run_dir, "run3", [_passing(), _failing()])
    assert manifest_path.exists()
    assert (run_dir / "junit.xml").exists()
    assert (run_dir / "report.html").exists()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert data["runId"] == "run3"
    assert data["ok"] is False
    assert len(data["scenarios"]) == 2


def test_html_report() -> None:
    out = html_report("run9", [_passing(), _failing()])
    assert "<!doctype html>" in out
    assert "run9" in out
    assert "s1" in out and "s2" in out
    assert "PASS" in out and "FAIL" in out

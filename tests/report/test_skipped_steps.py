"""A step `skipIfUnsupported` skipped (BE-0457) renders as skipped in every report, never as passed."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from pathlib import Path

from bajutsu.common.orchestrator import RunResult, StepOutcome
from bajutsu.common.report import ctrf_json, html_report, junit_xml, write_report
from bajutsu.common.report.load import results_from_manifest

_REASON = "a real device cannot spoof its location"


def _with_skip() -> RunResult:
    return RunResult(
        scenario="geo",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap"),
            StepOutcome(index=1, action="setLocation", skipped=True, reason=_REASON),
        ],
        backend="fake",
    )


def test_junit_keeps_the_verdict_and_lists_the_skip() -> None:
    case = ET.fromstring(junit_xml([_with_skip()])).find("testcase")
    assert case is not None
    assert case.find("failure") is None and case.find("skipped") is None
    prop = case.find("properties/property")
    assert prop is not None and prop.get("name") == "bajutsu.skippedSteps"
    assert prop.get("value") == "1"
    out = case.find("system-out")
    assert out is not None and out.text == f"step 1 setLocation: skipped {_REASON}"


def test_junit_writes_nothing_extra_without_a_skip() -> None:
    result = _with_skip()
    result.steps[1].skipped = False
    case = ET.fromstring(junit_xml([result])).find("testcase")
    assert case is not None
    assert case.find("properties") is None and case.find("system-out") is None


def test_ctrf_step_is_skipped_while_the_test_keeps_its_status() -> None:
    test = ctrf_json("20261016-101500", [_with_skip()])["results"]["tests"][0]  # type: ignore[index]
    assert test["status"] == "passed"
    assert [s["status"] for s in test["steps"]] == ["passed", "skipped"]
    assert test["steps"][1]["extra"]["reason"] == _REASON


def test_html_row_reads_skip_with_its_reason_not_pass() -> None:
    html = html_report("r1", [_with_skip()])
    assert "srow skipped" in html
    assert '<span class="exst skipped">SKIP</span>' in html
    assert _REASON in html


def test_failure_body_names_a_skipped_step_as_skipped() -> None:
    result = _with_skip()
    result.ok = False
    result.steps.append(StepOutcome(index=2, action="tap", ok=False, reason="no match"))
    failure = ET.fromstring(junit_xml([result])).find("testcase/failure")
    assert failure is not None and failure.text is not None
    assert f"step 1 setLocation: skipped {_REASON}" in failure.text


def test_manifest_round_trips_the_skip(tmp_path: Path) -> None:
    run_dir = tmp_path / "run1"
    manifest = write_report(run_dir, "run1", [_with_skip()])
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert [s["skipped"] for s in data["scenarios"][0]["steps"]] == [False, True]
    loaded = results_from_manifest(data)
    assert loaded[0].steps[1].skipped is True
    assert loaded[0].steps[1].reason == _REASON

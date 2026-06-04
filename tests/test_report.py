"""Tests for reporting (manifest.json + JUnit XML).

Drives scenario -> run -> report end to end with the fake driver.
"""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import Artifact
from bajutsu.orchestrator import RunResult, StepOutcome, run_scenario
from bajutsu.report import html_report, junit_xml, manifest_dict, write_report
from bajutsu.scenario import Scenario


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


def test_html_embeds_scenario_video() -> None:
    r = RunResult(
        scenario="s1", ok=True, steps=[], expect_results=[],
        artifacts=[Artifact("00-s1/scenario.mp4", "video", "simctl")],
    )
    out = html_report("run9", [r])
    assert "<video" in out
    assert 'src="00-s1/scenario.mp4"' in out
    # A scenario with no video artifact embeds no player.
    assert "<video" not in html_report("run9", [_passing()])


def test_html_interactive_structure(tmp_path: Path) -> None:
    sid = "00-s1"
    (tmp_path / sid).mkdir(parents=True)
    (tmp_path / sid / "device.log").write_text("line one\nERROR boom\nline three\n", encoding="utf-8")
    (tmp_path / sid / "appTrace.json").write_text(
        '[{"name":"reindex","begin":"t0","end":"t1","durationMs":12.3}]', encoding="utf-8"
    )
    r = RunResult(
        scenario="s1", ok=True, steps=[], expect_results=[],
        artifacts=[
            Artifact(f"{sid}/scenario.mp4", "video", "simctl"),
            Artifact(f"{sid}/device.log", "deviceLog", "simctl"),
            Artifact(f"{sid}/appTrace.json", "appTrace", "simctl"),
        ],
    )
    out = html_report("run1", [r], tmp_path)
    # collapsible section, tab switching, and the failure filter
    assert '<details class="scn"' in out
    assert 'data-tab="log"' in out and 'data-tab="trace"' in out
    assert 'class="logfilter"' in out
    assert 'onlyFailures(this)' in out
    # log embedded inline (filterable without a server) and trace rendered as a table
    assert "ERROR boom" in out
    assert "reindex" in out and "12.3" in out


def test_html_step_rows_carry_video_offset() -> None:
    r = RunResult(
        scenario="s1", ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, duration_s=0.2, started_at=0.0),
            StepOutcome(index=1, action="wait", ok=True, duration_s=1.1, started_at=1.5),
        ],
        expect_results=[],
        artifacts=[Artifact("00-s1/scenario.mp4", "video", "simctl")],
    )
    out = html_report("run1", [r])
    # Each step row is clickable and tagged with its offset into the recording…
    assert "class='srow ok' data-t='0.000'" in out
    assert "data-t='1.500'" in out
    # …and the JS seeks the video and highlights the playing step.
    assert "v.currentTime = t" in out
    assert "timeupdate" in out and "playing" in out

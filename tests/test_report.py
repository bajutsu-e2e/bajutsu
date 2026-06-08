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


def test_manifest_records_backend() -> None:
    # run_scenario stamps each result with the driver it ran (here the fake driver),
    # and the manifest summarizes the run's actuator at top level.
    m = manifest_dict("run1", [_passing()])
    assert m["backend"] == "fake"
    assert m["scenarios"][0]["backend"] == "fake"


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


def test_html_expectations_block() -> None:
    # Expects render as a table with PASS/FAIL in its own column.
    out = html_report("run9", [_passing(), _failing()])
    assert 'class="expects"' in out
    assert "class='extbl'" in out  # a table, not a list
    # target / comparison are split into their own cells.
    assert "<th>result</th><th>kind</th><th>target</th><th>comparison</th><th>reason</th>" in out
    assert 'class="exst ok">PASS' in out
    assert 'class="exst ng">FAIL' in out
    assert 'act-assert">exists' in out  # the assertion kind pill
    assert 'class="exreason"' in out  # the failing expect shows its reason


def test_expectations_tokenized_from_definition() -> None:
    # With the definition available, an evaluated expectation reuses the tokenized
    # description (id as a variable token), not the raw detail string.
    definition = {
        "name": "s1",
        "steps": [{"tap": {"id": "a"}}],
        "expect": [{"exists": {"id": "home.title"}}],
    }
    out = html_report("run9", [_passing()], definitions=[definition])
    assert 'class="expects"' in out
    # #home.title only appears in the expectation (the step taps #a), so this proves
    # the expectation itself is tokenized.
    assert '<span class="tk id">#home.title</span>' in out


def test_lightbox_arrows_navigate_screenshots() -> None:
    # The full-size lightbox has prev/next controls + arrow keys, walking every
    # screenshot in the run (the gallery is all `img.shot`, across scenarios).
    out = html_report("run1", [_passing()])
    assert 'class="lb-nav lb-prev"' in out and 'class="lb-nav lb-next"' in out
    assert "ArrowLeft" in out and "ArrowRight" in out
    assert "img.shot" in out  # gallery collected from every step thumbnail


def test_step_click_seeks_without_autoplay() -> None:
    # Clicking a step seeks the recording but never starts playback on a paused video.
    out = html_report("run9", [_passing()])
    assert "v.currentTime = t;" in out
    assert "v.play()" not in out


def test_html_shows_backend() -> None:
    # The actuator is shown both as a header chip and a per-scenario badge.
    out = html_report("run9", [_passing()])
    assert "driver: fake" in out
    assert '<span class="drv"' in out  # per-scenario row badge
    # The header chip uses a dedicated class (not the `.drv` row badge) so it sets
    # its own white text (not blue-on-blue) and centers in the flex row.
    assert '<span class="chip dchip"' in out
    assert ".chip.dchip{background:#2c5fb3;color:#fff" in out
    assert "align-items:center" in out and "display:inline-flex;align-items:center" in out


def test_merged_steps_show_rich_definition() -> None:
    # Steps and Scenario are one merged tab: each planned step renders richly, and
    # steps that never ran (the plan is longer than the outcomes) still appear.
    definition = {
        "name": "s1",
        "preconditions": {"erase": False, "launchEnv": {"SAMPLE_SEED": "5"}},
        "steps": [
            {"tap": {"id": "counter.increment"}},
            {"type": {"text": "Item 3", "into": {"id": "home.search"}}},
            {"wait": {"until": "settled", "timeout": 3.0}},
        ],
    }
    out = html_report("run9", [_passing()], definitions=[definition])
    assert 'data-tab="scenario"' not in out  # merged into the Steps tab
    assert 'data-tab="steps"' in out
    # Steps are a table parallel to the expectations table: result / action / detail.
    assert "class='sttbl'" in out
    assert "<th>#</th><th>result</th><th>action</th><th>detail</th>" in out
    # Selectors and string literals are tokenized (distinct from the action badges).
    assert '<span class="tk id">#counter.increment</span>' in out
    assert '<span class="tk str">“Item 3”</span> into <span class="tk id">#home.search</span>' in out
    assert "until settled (≤" in out and '<span class="tk num">3s</span>' in out
    # Preconditions are a collapsible table, not chips.
    assert '<details class="pre"' in out
    assert '<td class="pk">SAMPLE_SEED</td><td>5</td>' in out
    assert "class='skip'" in out  # planned-but-not-run steps are marked


def test_assert_step_splits_into_cells() -> None:
    # An assert step's multiple checks become a nested table, each split into
    # kind / target / comparison cells (instead of a joined "a; b; c" string).
    definition = {
        "name": "s1",
        "steps": [
            {
                "assert": [
                    {"value": {"sel": {"id": "ctrl.button.value"}, "equals": "0"}},
                    {"enabled": {"id": "ctrl.button"}},
                    {"disabled": {"id": "ctrl.buttonDisabled"}},
                ]
            }
        ],
    }
    out = html_report("run9", [_passing()], definitions=[definition])
    assert 'class="atbl"' in out
    assert '<span class="tk id">#ctrl.button.value</span>' in out
    assert '<span class="tk str">“0”</span>' in out
    assert '<span class="tk id">#ctrl.buttonDisabled</span>' in out
    assert "; enabled" not in out  # no longer joined on one line


def test_steps_section_has_label() -> None:
    out = html_report(
        "run9", [_passing()], definitions=[{"name": "s1", "steps": [{"tap": {"id": "a"}}]}]
    )
    assert 'class="steps-sec"' in out
    assert '<span class="deflbl">steps</span>' in out


def test_scenario_rich_yaml_toggle() -> None:
    # With raw YAML provided, the merged tab offers a Rich / YAML toggle.
    definition = {"name": "s1", "steps": [{"tap": {"id": "a"}}]}
    yaml = "- name: s1\n  steps:\n    - tap: { id: a }\n"
    out = html_report("run9", [_passing()], definitions=[definition], sources=[yaml])
    assert 'data-view="rich"' in out and 'data-view="yaml"' in out
    assert 'class="view view-rich active"' in out
    assert 'class="src"' in out and "tap: { id: a }" in out
    # No YAML source -> no toggle.
    assert 'data-view="yaml"' not in html_report("run9", [_passing()], definitions=[definition])


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


def test_html_shows_step_screenshot_and_tree() -> None:
    r = RunResult(
        scenario="s1", ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, started_at=0.0, artifacts=[
                Artifact("00-s1/step0/after.png", "screenshot", "driver"),
                Artifact("00-s1/step0/elements.json", "elements", "driver"),
            ]),
        ],
        expect_results=[], artifacts=[],
    )
    out = html_report("run1", [r])
    # the step's screenshot (lightbox thumbnail) and its element-tree link are shown
    assert 'class="shot"' in out and 'src="00-s1/step0/after.png"' in out
    assert 'href="00-s1/step0/elements.json"' in out
    # the lightbox overlay + opener are present
    assert 'id="lb"' in out and "openLightbox" in out

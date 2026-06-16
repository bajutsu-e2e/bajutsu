"""Tests for reporting (manifest.json + JUnit XML).

Drives scenario -> run -> report end to end with the fake driver.
"""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import Artifact
from bajutsu.network import NetworkExchange
from bajutsu.orchestrator import AlertEvent, RunResult, StepOutcome, run_scenario
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
        Scenario.model_validate(
            {
                "name": "s1",
                "steps": [{"tap": {"id": "a"}}],
                "expect": [{"exists": {"id": "home.title"}}],
            }
        ),
    )


def _failing() -> RunResult:
    driver = FakeDriver([_el("a", "A", ["button"])])
    return run_scenario(
        driver,
        Scenario.model_validate(
            {
                "name": "s2",
                "steps": [{"tap": {"id": "a"}}],
                "expect": [{"exists": {"id": "missing"}}],
            }
        ),
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


def test_html_report_shows_source_filename() -> None:
    out = html_report("run9", [_failing()], source_name="smoke.yaml")
    assert 'class="sfile">smoke.yaml' in out  # the scenario file name in the summary header
    assert 'class="sfile"' not in html_report("run9", [_failing()])  # omitted when unknown


def test_html_report_shows_descriptions() -> None:
    definition = {"name": "s2", "description": "what this scenario checks", "steps": []}
    out = html_report(
        "run9", [_failing()], definitions=[definition], description="what this file covers"
    )
    assert 'class="fdesc">what this file covers' in out  # file-level description in the header
    assert 'class="sdesc">what this scenario checks' in out  # per-scenario description in the card
    # both omitted when absent
    assert 'class="fdesc"' not in html_report("run9", [_failing()])


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


def test_expectations_request_kind_rendered() -> None:
    # A `request` expectation has no selector like the UI kinds, so it must render its
    # own kind pill + method/status cells instead of the unknown-kind "?" fallback.
    definition = {
        "name": "s1",
        "steps": [{"tap": {"id": "net.status"}}],
        "expect": [{"request": {"method": "GET", "status": 200}}],
    }
    result = run_scenario(
        FakeDriver([_el("net.status", "200")]),
        Scenario.model_validate(definition),
        network=lambda: [NetworkExchange(method="GET", path="/x", status=200)],
    )
    out = html_report("run9", [result], definitions=[definition])
    assert 'act-assert">request' in out  # the kind pill, not "?"
    assert 'act-assert">?' not in out
    assert '<span class="tk kw">GET</span>' in out
    assert 'status == <span class="tk num">200</span>' in out


def test_fmt_duration_compact() -> None:
    from bajutsu.report import _fmt_duration

    assert _fmt_duration(0.0) == "0.0s"
    assert _fmt_duration(0.84) == "0.8s"
    assert _fmt_duration(12.34) == "12.3s"
    assert _fmt_duration(83) == "1m 23s"  # rolls over to minutes past 60s


def test_html_shows_execution_time() -> None:
    # Each scenario shows its own duration in its row, and the header shows the run total
    # (the sum across scenarios).
    a = RunResult(scenario="s1", ok=True, steps=[], duration_s=1.5)
    b = RunResult(scenario="s2", ok=True, steps=[], duration_s=2.0)
    out = html_report("run1", [a, b])
    # Per-scenario badge in each summary row.
    assert out.count('class="sdur"') == 2
    assert ">1.5s</span>" in out and ">2.0s</span>" in out
    # Header total chip = sum of the scenario durations.
    assert 'class="chip tchip"' in out
    assert ">3.5s</span>" in out


def test_manifest_records_scenario_duration() -> None:
    r = RunResult(scenario="s1", ok=True, steps=[], duration_s=2.5)
    assert manifest_dict("run1", [r])["scenarios"][0]["duration_s"] == 2.5


def test_html_environment_tab_shows_simulator() -> None:
    # Each scenario gets an Environment tab (beside Result) naming the simulator it ran on:
    # device model, OS runtime, actuator, and udid.
    r = _passing()
    r.device, r.device_name, r.device_runtime = "SIM-ABC123", "iPhone 15", "iOS 17.2"
    out = html_report("run1", [r])
    assert 'data-tab="env"' in out and 'data-panel="env"' in out
    assert ">Environment</button>" in out  # the tab label
    assert '<span class="deflbl">simulator</span>' in out
    assert '<td class="pk">device</td><td>iPhone 15</td>' in out
    assert '<td class="pk">OS</td><td>iOS 17.2</td>' in out
    assert '<td class="pk">actuator</td><td>fake</td>' in out
    assert '<td class="pk">udid</td><td>SIM-ABC123</td>' in out
    # Result stays the active first tab; Environment is a sibling, not the default.
    assert out.index('data-tab="steps"') < out.index('data-tab="env"')


def test_html_preconditions_render_in_result_tab() -> None:
    # Preconditions render in the Result tab (the steps panel), ahead of the Environment tab.
    definition = {
        "name": "s1",
        "steps": [{"tap": {"id": "a"}}],
        "preconditions": {"locale": "ja_JP", "launchEnv": {"SAMPLE_SEED": "5"}},
    }
    out = html_report("run9", [_passing()], definitions=[definition])
    assert '<details class="pre"' in out
    assert '<td class="pk">locale</td><td>ja_JP</td>' in out
    assert '<td class="pk">SAMPLE_SEED</td><td>5</td>' in out
    # In the Result panel, which precedes the Environment panel.
    assert out.index('<details class="pre"') < out.index('data-panel="env"')


def test_manifest_records_device_environment() -> None:
    r = _passing()
    r.device, r.device_name, r.device_runtime = "SIM-1", "iPhone 15", "iOS 17.2"
    scenario = manifest_dict("run1", [r])["scenarios"][0]
    assert scenario["device"] == "SIM-1"
    assert scenario["device_name"] == "iPhone 15"
    assert scenario["device_runtime"] == "iOS 17.2"


def test_html_shows_dismissed_system_alert() -> None:
    # A step that only passed after the guard cleared a system prompt shows the dismissal
    # as a sub-row, so a retried step is not silently rendered as "just passing".
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(
                index=0, action="tap", ok=True, started_at=0.0, alerts=[AlertEvent(label="Not Now")]
            )
        ],
        expect_results=[],
        artifacts=[],
    )
    out = html_report("run1", [r], definitions=[{"name": "s1", "steps": [{"tap": {"id": "a"}}]}])
    assert "class='alertrow'" in out
    assert 'act-alert">system alert' in out
    assert '<span class="tk str">“Not Now”</span>' in out
    # A step with no dismissal renders no alert row.
    assert "class='alertrow'" not in html_report("run1", [_passing()])


def test_html_shows_expect_phase_dismissed_alert() -> None:
    # A prompt cleared right before the scenario-level expect re-checked is noted under
    # the expectations table (it belongs to no single step).
    r = _passing()  # carries an evaluated expectation
    r.expect_alerts = [AlertEvent(label="Allow")]
    out = html_report("run1", [r])
    assert 'class="alertnote"' in out
    assert 'act-alert">system alert' in out
    assert "dismissed before re-checking" in out
    assert '<span class="tk str">“Allow”</span>' in out
    # No expect-phase dismissal -> no note.
    assert 'class="alertnote"' not in html_report("run1", [_passing()])


def test_manifest_records_dismissed_alerts() -> None:
    # asdict captures the dismissals so the manifest (the source of truth) carries them too.
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[StepOutcome(index=0, action="tap", ok=True, alerts=[AlertEvent(label="Not Now")])],
        expect_alerts=[AlertEvent(label="Allow")],
    )
    m = manifest_dict("run1", [r])
    scenario = m["scenarios"][0]
    assert scenario["steps"][0]["alerts"] == [{"label": "Not Now"}]
    assert scenario["expect_alerts"] == [{"label": "Allow"}]


def test_screenshot_opens_element_viewer_and_arrows_navigate_steps() -> None:
    # The full-size screenshot preview (lightbox) is gone: clicking a step's screenshot
    # opens the element viewer, and ← / → walk through every step's elements across the run.
    out = html_report("run1", [_passing()])
    assert 'id="lb"' not in out and "lb-nav" not in out and "openLightbox" not in out
    assert "tvOpen(shot.closest('td.ev'))" in out  # the screenshot opens the element viewer
    assert (
        "ArrowLeft" in out and "ArrowRight" in out and "tvHosts" in out
    )  # arrow keys walk the steps


def test_step_click_seeks_without_autoplay() -> None:
    # Clicking a step seeks the recording but never starts playback on a paused video.
    # Playback is started only from the explicit play/pause control, never the seek path.
    out = html_report("run9", [_passing()])
    assert "v.currentTime = t;" in out  # step-row click seeks
    assert "if(v.paused) v.play();" in out  # play() is reachable only via the button
    # The seek handler stays seek-only (it has no .play() of its own).
    assert "Seek only" in out


def test_device_shown_in_manifest_and_report() -> None:
    # A parallel run records the device per scenario; the report shows a per-scenario badge
    # and a header "N devices" chip when the work was split across simulators.
    a, b = _passing(), _passing()
    a.device, b.device = "SIM-AAAA", "SIM-BBBB"
    m = manifest_dict("run1", [a, b])
    assert [s["device"] for s in m["scenarios"]] == ["SIM-AAAA", "SIM-BBBB"]
    out = html_report("run1", [a, b])
    assert 'class="dev"' in out and "ran on simulator SIM-AAAA" in out  # per-scenario badge
    assert "2 devices" in out  # header summary chip when split across devices
    # A single-device run omits the header devices chip.
    one = _passing()
    one.device = "SIM-AAAA"
    assert "devices</span>" not in html_report("run1", [one])


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
    assert (
        '<span class="tk str">“Item 3”</span> into <span class="tk id">#home.search</span>' in out
    )
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
        scenario="s1",
        ok=True,
        steps=[],
        expect_results=[],
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
    (tmp_path / sid / "device.log").write_text(
        "line one\nERROR boom\nline three\n", encoding="utf-8"
    )
    (tmp_path / sid / "appTrace.json").write_text(
        '[{"name":"reindex","begin":"t0","end":"t1","durationMs":12.3}]', encoding="utf-8"
    )
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[],
        expect_results=[],
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
    assert "onlyFailures(this)" in out
    # log embedded inline (filterable without a server) and trace rendered as a table
    assert "ERROR boom" in out
    assert "reindex" in out and "12.3" in out


def test_html_network_tab(tmp_path: Path) -> None:
    sid = "00-s1"
    (tmp_path / sid).mkdir(parents=True)
    (tmp_path / sid / "network.json").write_text(
        '[{"method":"GET","url":"https://example.com/items","path":"/items","status":200,'
        '"durationMs":75.4,"startedAt":0.8,"responseHeaders":{"Content-Type":"text/html"},'
        '"responseBody":"<html>hi</html>"}]',
        encoding="utf-8",
    )
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[],
        expect_results=[],
        artifacts=[Artifact(f"{sid}/network.json", "network", "collector")],
    )
    out = html_report("run1", [r], tmp_path)
    # A Network tab appears and renders the captured exchange: request time / method /
    # path / status, plus the headers and (HTML-escaped) body when expanded.
    assert 'data-tab="net"' in out
    assert "captured by BajutsuKit" in out
    assert 'class="nxat muted"' in out and ">0.8s</span>" in out  # the request time on the row
    assert 'class="nxm">GET' in out
    assert "/items" in out and 'nxs ok">200' in out
    assert "Content-Type" in out and "&lt;html&gt;hi&lt;/html&gt;" in out
    # No network artifact -> no Network tab.
    assert 'data-tab="net"' not in html_report("run1", [_passing()])


def test_html_dark_mode_and_log_highlight() -> None:
    out = html_report("run9", [_passing()])
    assert "@media (prefers-color-scheme: dark)" in out  # dark-mode CSS is bundled
    assert ".log mark{" in out  # highlight style for log matches
    assert "'<mark>'" in out  # the log filter wraps matches in <mark>


def test_html_exchanges_interleaved_into_steps(tmp_path: Path) -> None:
    sid = "00-s1"
    (tmp_path / sid).mkdir(parents=True)
    (tmp_path / sid / "network.json").write_text(
        '[{"method":"GET","url":"https://api.example.com/items","status":200,"durationMs":50,'
        '"responseHeaders":{"Content-Type":"application/json"},"responseBody":"hello-body","startedAt":0.5},'
        '{"method":"POST","url":"https://other.com/log","status":204,"startedAt":1.2}]',
        encoding="utf-8",
    )
    definition = {
        "name": "s1",
        "steps": [{"tap": {"id": "a"}}, {"wait": {"until": "settled", "timeout": 3}}],
    }
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, started_at=0.0),
            StepOutcome(index=1, action="wait", ok=True, started_at=1.0),
        ],
        expect_results=[],
        artifacts=[Artifact(f"{sid}/network.json", "network", "collector")],
    )
    out = html_report("run1", [r], tmp_path, definitions=[definition])
    # Each exchange splits into a request row (method badge) and a response row.
    assert 'class="act act-net">GET' in out and 'act-net">response' in out
    # The row is a click target; its settings expand into a full-width row below.
    assert 'class="xmark">▸' in out and "class='nxdetail' hidden" in out
    assert 'class="nxk">endpoint' in out and "https://api.example.com/items" in out
    # The response row carries the response headers and (viewable) body.
    assert 'class="nxk">headers' in out and "Content-Type" in out
    assert 'class="nxk">body' in out and "hello-body" in out
    # Time order: tap(0.0) -> GET request(0.5) -> wait(1.0) -> POST request(1.2).
    assert (
        out.index(">#a<")
        < out.index('act-net">GET')
        < out.index('act-wait">wait')
        < out.index('act-net">POST')
    )


def test_html_exchanges_filtered_by_domain(tmp_path: Path) -> None:
    sid = "00-s1"
    (tmp_path / sid).mkdir(parents=True)
    (tmp_path / sid / "network.json").write_text(
        '[{"method":"GET","url":"https://api.example.com/x","status":200,"startedAt":0.2},'
        '{"method":"POST","url":"https://tracker.io/log","status":204,"startedAt":0.3}]',
        encoding="utf-8",
    )
    definition = {
        "name": "s1",
        "steps": [{"tap": {"id": "a"}}],
        "network": {"filter": {"domains": ["example.com"]}},
    }
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[StepOutcome(index=0, action="tap", ok=True, started_at=0.0)],
        expect_results=[],
        artifacts=[Artifact(f"{sid}/network.json", "network", "collector")],
    )
    out = html_report("run1", [r], tmp_path, definitions=[definition])
    # Only the example.com exchange is interleaved into the result timeline (its request
    # + response rows = two act-net badges)...
    assert out.count('class="act act-net"') == 2 and "https://api.example.com/x" in out
    # ...but the filtered-out request still appears in the (unfiltered) Network tab.
    assert "tracker.io" in out


def test_html_wait_request_detail_is_rich() -> None:
    # A `wait: { until: { request } }` step renders a tokenized detail (method / url /
    # status), in the same tone as other details — not a raw `{'request': ...}` dump.
    definition = {
        "name": "s1",
        "steps": [
            {"tap": {"id": "net.fetch"}},
            {
                "wait": {
                    "until": {
                        "request": {"method": "GET", "url": "https://example.com", "status": 200}
                    },
                    "timeout": 8,
                }
            },
        ],
        "expect": [],
    }
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(index=0, action="tap", ok=True, started_at=0.0),
            StepOutcome(index=1, action="wait", ok=True, started_at=0.1),
        ],
        expect_results=[],
        artifacts=[],
    )
    out = html_report("run1", [r], definitions=[definition])
    assert "until request" in out
    assert '<span class="tk kw">GET</span>' in out
    assert '<span class="tk str">https://example.com</span>' in out
    assert 'status == <span class="tk num">200</span>' in out
    assert "{'request'" not in out  # not the raw python dict


def test_html_step_rows_carry_video_offset() -> None:
    r = RunResult(
        scenario="s1",
        ok=True,
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


def test_html_shows_step_screenshot_and_tree(tmp_path: Path) -> None:
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(
                index=0,
                action="tap",
                ok=True,
                started_at=0.0,
                artifacts=[
                    Artifact("00-s1/step0/after.png", "screenshot", "driver"),
                    Artifact("00-s1/step0/elements.json", "elements", "driver"),
                ],
            ),
        ],
        expect_results=[],
        artifacts=[],
    )
    step_dir = tmp_path / "00-s1" / "step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(
        json.dumps([_el("home.title", "Welcome", ["staticText"])]), encoding="utf-8"
    )
    out = html_report("run1", [r], tmp_path)
    # the step's screenshot thumbnail and its element viewer are shown
    assert 'class="shot"' in out and 'src="00-s1/step0/after.png"' in out
    # the element tree opens in-report (no new tab): a button + inline embedded data,
    # rendered into the #tv overlay rather than linking out to the json file.
    assert 'class="elnk treebtn"' in out
    assert 'target="_blank"' not in out
    assert "home.title" in out and "Welcome" in out
    assert 'id="tv"' in out and "tvFilter" in out
    # the screenshot preview (lightbox) is gone; the element viewer shows the step's own
    # info above the table instead.
    assert 'id="lb"' not in out and "openLightbox" not in out
    assert 'class="tv-step"' in out
    # prev / next buttons walk to the neighbouring step's screenshot + elements, and the
    # element filter sits in its own band below the step info (not in the head).
    assert 'class="tv-nav tv-prev"' in out and 'class="tv-nav tv-next"' in out
    assert 'class="tv-filter"' in out


def test_html_tree_rows_carry_frame_for_screenshot_highlight(tmp_path: Path) -> None:
    # Each element row embeds its raw frame (points) and the table the screen rect, so
    # the viewer can highlight the hovered element's location on the screenshot.
    el = {**_el("home.cta", "Buy", ["button"]), "frame": (12.0, 40.0, 100.0, 36.0)}
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(
                index=0,
                action="tap",
                ok=True,
                started_at=0.0,
                artifacts=[
                    Artifact("00-s1/step0/after.png", "screenshot", "driver"),
                    Artifact("00-s1/step0/elements.json", "elements", "driver"),
                ],
            ),
        ],
        expect_results=[],
        artifacts=[],
    )
    step_dir = tmp_path / "00-s1" / "step0"
    step_dir.mkdir(parents=True)
    (step_dir / "elements.json").write_text(json.dumps([el]), encoding="utf-8")
    out = html_report("run1", [r], tmp_path)
    # the row carries the frame; the table carries the screen extent (bbox: 112x76)
    assert 'class="tvrow" data-x="12" data-y="40" data-w="100" data-h="36"' in out
    assert 'data-sw="112" data-sh="76"' in out
    # the highlight overlay + frame wrapper are wired in JS/CSS
    assert "tv-hl" in out and "tv-shotframe" in out


def test_html_tree_falls_back_to_link_without_run_dir() -> None:
    # Structure-only render (no run_dir → no element data to embed): keep a link.
    r = RunResult(
        scenario="s1",
        ok=True,
        steps=[
            StepOutcome(
                index=0,
                action="tap",
                ok=True,
                started_at=0.0,
                artifacts=[
                    Artifact("00-s1/step0/elements.json", "elements", "driver"),
                ],
            ),
        ],
        expect_results=[],
        artifacts=[],
    )
    out = html_report("run1", [r])
    assert 'href="00-s1/step0/elements.json"' in out


def test_assert_parts_visual() -> None:
    from bajutsu.report import _assert_parts

    kind, target, comp = _assert_parts(
        {"visual": {"baseline": "home.png", "threshold": 0.5, "exclude": [{"x": 0}, {"x": 1}]}}
    )
    assert kind == "visual"
    assert ("str", "home.png") in target
    assert any(t == "num" and "0.5" in v for t, v in comp)  # threshold token
    assert any("2 excluded" in v for _, v in comp)  # exclude-region count


def test_html_visual_assertion_strip(tmp_path: Path) -> None:
    from bajutsu.assertions import AssertionResult, VisualEvidence

    ev = VisualEvidence(
        baseline_name="home.png",
        actual="00-s1/visual-actual.png",
        baseline="00-s1/baseline-home.png",
        diff="00-s1/diff-home.png",
        diff_pct=2.5,
    )
    ar = AssertionResult(False, "visual", "visual ≈ home.png", "diff 2.5%", visual=ev)
    r = RunResult(scenario="s1", ok=False, steps=[], expect_results=[ar], artifacts=[])
    definition = {
        "name": "s1",
        "steps": [],
        "expect": [{"visual": {"baseline": "home.png", "threshold": 0.5}}],
    }
    out = html_report("runV", [r], tmp_path, definitions=[definition])
    assert "data-run-id='runV'" in out
    assert 'src="00-s1/baseline-home.png"' in out  # comparator base layer
    assert 'src="00-s1/visual-actual.png"' in out  # comparator overlay
    assert 'src="00-s1/diff-home.png"' in out  # precomputed pixel-diff layer
    # the swipe / onion / mix-blend comparator with its modes
    assert 'class="vcmp mode-swipe"' in out
    for m in ("swipe", "onion", "blend", "diff"):
        assert f'data-mode="{m}"' in out
    assert "Approve as baseline" in out
    assert 'data-baseline="home.png"' in out
    assert 'data-sid="00-s1"' in out
    assert "diff 2.50%" in out


def test_html_visual_pass_has_comparator_without_diff_mode(tmp_path: Path) -> None:
    from bajutsu.assertions import AssertionResult, VisualEvidence

    # A passing visual check: baseline ≈ actual, no diff image → comparator but no Diff/Approve.
    ev = VisualEvidence(
        baseline_name="home.png",
        actual="00-s1/visual-actual.png",
        baseline="00-s1/baseline-home.png",
        diff=None,
        diff_pct=0.0,
    )
    ar = AssertionResult(True, "visual", "visual ≈ home.png", "", visual=ev)
    r = RunResult(scenario="s1", ok=True, steps=[], expect_results=[ar], artifacts=[])
    out = html_report("runP", [r], tmp_path)
    assert 'class="vcmp mode-swipe"' in out
    assert 'data-mode="blend"' in out
    assert 'data-mode="diff"' not in out  # no diff image → no Diff mode
    assert "Approve as baseline" not in out  # a passing check is not approvable


def test_html_visual_missing_baseline_no_approve_is_offered() -> None:
    from bajutsu.assertions import AssertionResult, VisualEvidence

    ev = VisualEvidence(baseline_name="home.png", actual="00-s1/visual-actual.png", missing=True)
    ar = AssertionResult(False, "visual", "visual ≈ home.png", "baseline not found", visual=ev)
    r = RunResult(scenario="s1", ok=False, steps=[], expect_results=[ar], artifacts=[])
    out = html_report("runV", [r])
    assert "no baseline yet" in out
    assert "Approve as baseline" in out  # missing baseline is approvable too

"""Tests for the HTML report core rendering (steps, expectations, structure)."""

from __future__ import annotations

from pathlib import Path

from _report import _el, _failing, _passing

from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence import Artifact
from bajutsu.network import NetworkExchange
from bajutsu.orchestrator import RunResult, run_scenario
from bajutsu.report import html_report
from bajutsu.scenario import Scenario


def test_html_report() -> None:
    out = html_report("run9", [_passing(), _failing()])
    assert "<!doctype html>" in out
    assert "run9" in out
    assert "s1" in out and "s2" in out
    assert "PASS" in out and "FAIL" in out


def test_html_report_renders_cross_browser_matrix() -> None:
    # A --browsers run renders the engine x scenario grid: a row green on chromium but red on webkit
    # is the machine-detected incompatibility this view exists to surface (BE-0076).
    results = [
        RunResult(scenario="login", ok=True, steps=[], backend="playwright", engine="chromium"),
        RunResult(scenario="login", ok=False, steps=[], backend="playwright", engine="webkit"),
    ]
    out = html_report("run9", results)
    assert 'class="matrix"' in out  # the grid is rendered
    assert "chromium" in out and "webkit" in out  # both engine columns
    assert "login" in out  # the scenario row


def test_html_report_aligns_definitions_per_engine() -> None:
    # A matrix run's results are the per-engine passes concatenated, but definitions carry one entry
    # per scenario. Each result's plan must cycle per engine, so every engine's panel shows the
    # scenario's description — not just the first engine's (BE-0076).
    results = [
        RunResult(scenario="login", ok=True, steps=[], backend="playwright", engine="chromium"),
        RunResult(scenario="login", ok=False, steps=[], backend="playwright", engine="webkit"),
    ]
    definition = {"name": "login", "description": "the login flow", "steps": []}
    out = html_report("run9", results, definitions=[definition])
    # The description appears once per engine result (both panels carry the plan), not just once.
    assert out.count('class="sdesc">the login flow') == 2


def test_html_report_omits_matrix_for_single_engine() -> None:
    # A non-matrix run carries no engine tag, so the grid is absent — today's report unchanged.
    assert 'class="matrix"' not in html_report("run9", [_passing(), _failing()])


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
    from bajutsu.report.format import _fmt_duration

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


def test_drag_step_renders_action_badge_and_direction() -> None:
    # `drag` (BE-0227) is wired into the report's action registries like swipe/pinch/rotate, so its
    # row shows the `drag` action badge and a tokenized "direction on <id> · amount" detail rather
    # than a blank cell (which an unregistered action would produce).
    definition = {
        "name": "s1",
        "steps": [{"drag": {"on": {"id": "gest.divider"}, "direction": "left", "amount": 0.5}}],
    }
    out = html_report("run9", [_passing()], definitions=[definition])
    assert ">drag</span>" in out  # the action badge from _ACTION_META
    assert 'left on <span class="tk id">#gest.divider</span>' in out
    assert '<span class="tk num">0.5</span>' in out


def test_steps_show_from_provenance_grouped() -> None:
    # Each planned step renders the natural-language phrase it was recorded from (BE-0044);
    # a run of identical consecutive `from:` is labeled once (emergent grouping). Only
    # `definitions` is passed (no `sources`), so the raw YAML isn't embedded and the count
    # reflects the rendered labels alone.
    definition = {
        "name": "s1",
        "steps": [
            {"tap": {"id": "settings.open"}, "from": "Open settings"},
            {"tap": {"id": "settings.reindex"}, "from": "Open settings"},
            {"wait": {"until": "settled", "timeout": 3.0}, "from": "Wait for the reindex"},
        ],
    }
    out = html_report("run9", [_passing()], definitions=[definition])
    assert 'class="stepfrom"' in out  # provenance rendered in the step detail
    assert "Wait for the reindex" in out
    assert out.count("Open settings") == 1  # the shared phrase is labeled once, not per step


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


def test_html_dark_mode_and_log_highlight() -> None:
    out = html_report("run9", [_passing()])
    assert "@media (prefers-color-scheme: dark)" in out  # dark-mode CSS is bundled
    assert ".log mark{" in out  # highlight style for log matches
    assert "'<mark>'" in out  # the log filter wraps matches in <mark>

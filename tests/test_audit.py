"""Tests for the static determinism audit (bajutsu/audit.py, BE-0049).

The audit is a pure, device-free, AI-free function of a scenario: it grades each selector on the
stability ladder (id > label/traits > index/coordinates), flags over-loose waits, and flags
coordinate gestures. It never runs the scenario and never decides pass/fail.
"""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bajutsu.audit import audit_scenario, render
from bajutsu.cli import app
from bajutsu.scenario import load_scenarios

runner = CliRunner()


def _audit(yaml: str):  # type: ignore[no-untyped-def]
    [scenario] = load_scenarios(yaml)
    return audit_scenario(scenario)


def test_all_id_selectors_grade_stable() -> None:
    report = _audit(
        "- name: x\n  steps:\n"
        "    - tap: { id: home.start }\n"
        "    - type: { text: a@b.com, into: { id: auth.email } }\n"
        "  expect:\n"
        "    - exists: { id: home.title }\n"
    )
    assert report.grade == "Stable"
    assert report.stability == 1.0
    assert report.stable == 3 and report.moderate == 0 and report.fragile == 0
    assert report.findings == []


def test_label_selector_is_moderate() -> None:
    report = _audit("- name: x\n  steps:\n    - tap: { label: Submit }\n")
    assert report.grade == "Moderate"
    assert report.moderate == 1 and report.stable == 0
    assert any(f.kind == "moderate-selector" for f in report.findings)


def test_index_selector_is_fragile() -> None:
    report = _audit("- name: x\n  steps:\n    - tap: { label: Row, index: 2 }\n")
    assert report.grade == "Fragile"
    assert report.fragile == 1
    assert any(f.kind == "fragile-selector" and "index" in f.detail for f in report.findings)


def test_index_makes_a_selector_fragile_even_with_id_matches() -> None:
    # `index` is nth-of-many regardless of the other fields, so it dominates the tier.
    report = _audit("- name: x\n  steps:\n    - tap: { idMatches: 'row.*', index: 1 }\n")
    assert report.grade == "Fragile"
    assert report.fragile == 1 and report.stable == 0


def test_within_scope_is_graded_too() -> None:
    # a stable outer id scoped within a fragile (index) container still carries a determinism risk
    report = _audit(
        "- name: x\n  steps:\n    - tap: { id: row.open, within: { label: List, index: 2 } }\n"
    )
    assert report.fragile == 1  # the nested within selector
    assert report.stable == 1  # the outer id
    assert report.grade == "Fragile"


def test_no_selectors_renders_cleanly() -> None:
    report = _audit("- name: x\n  steps:\n    - swipe: { from: [0.5, 0.8], to: [0.5, 0.2] }\n")
    assert report.selectors == 0
    assert "stability: n/a (no selectors)" in render(report)


def test_coordinate_swipe_is_flagged_and_fragile() -> None:
    report = _audit("- name: x\n  steps:\n    - swipe: { from: [0.5, 0.8], to: [0.5, 0.2] }\n")
    assert report.grade == "Fragile"
    assert any(f.kind == "coordinate-gesture" for f in report.findings)


def test_directional_swipe_on_an_id_is_stable() -> None:
    report = _audit("- name: x\n  steps:\n    - swipe: { on: { id: list }, direction: up }\n")
    assert report.grade == "Stable"
    assert report.stable == 1


def test_loose_wait_is_flagged() -> None:
    report = _audit(
        "- name: x\n  steps:\n"
        "    - tap: { id: home.start }\n"
        "    - wait: { until: screenChanged, timeout: 5 }\n"
    )
    # the id tap is stable, but the screenChanged wait waits for no concrete condition
    assert any(f.kind == "loose-wait" for f in report.findings)
    assert report.grade == "Moderate"


def test_concrete_waits_are_not_flagged() -> None:
    report = _audit(
        "- name: x\n  steps:\n"
        "    - wait: { for: { id: home.title }, timeout: 5 }\n"
        "    - wait: { until: { gone: { id: spinner } }, timeout: 3 }\n"
    )
    assert report.findings == []
    assert report.grade == "Stable"


def test_assertion_selectors_are_graded() -> None:
    report = _audit(
        "- name: x\n  steps:\n    - tap: { id: a }\n  expect:\n"
        "    - value: { sel: { label: Total }, equals: '10' }\n"
        "    - enabled: { id: submit }\n"
    )
    # tap id + enabled id = stable; the value's label selector = moderate
    assert report.stable == 2 and report.moderate == 1
    assert report.grade == "Moderate"


def test_control_flow_nested_steps_are_graded() -> None:
    report = _audit(
        "- name: x\n  steps:\n"
        "    - if:\n"
        "        condition: { exists: { id: gate } }\n"
        "        then:\n"
        "          - tap: { index: 0 }\n"
    )
    # the nested index tap is reached and graded fragile
    assert report.fragile == 1
    assert report.grade == "Fragile"


def test_render_is_human_readable() -> None:
    report = _audit("- name: checkout\n  steps:\n    - tap: { label: Buy }\n")
    text = render(report)
    assert "checkout" in text
    assert "grade: Moderate" in text
    assert "Buy" in text or "label" in text


def test_cli_audits_a_file_and_exits_zero(tmp_path: Path) -> None:
    scn = tmp_path / "s.yaml"
    scn.write_text("- name: x\n  steps:\n    - tap: { label: Submit }\n", encoding="utf-8")
    result = runner.invoke(app, ["audit", str(scn)])
    assert result.exit_code == 0  # advisory: never gates, even with findings
    assert "grade: Moderate" in result.output


def test_cli_json_output(tmp_path: Path) -> None:
    scn = tmp_path / "s.yaml"
    scn.write_text("- name: x\n  steps:\n    - tap: { id: ok }\n", encoding="utf-8")
    result = runner.invoke(app, ["audit", str(scn), "--json"])
    assert result.exit_code == 0
    [report] = json.loads(result.output)
    assert report["grade"] == "Stable"
    assert report["stable"] == 1


def test_cli_missing_file_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(app, ["audit", str(tmp_path / "nope.yaml")])
    assert result.exit_code == 2
    assert "scenario not found" in result.output

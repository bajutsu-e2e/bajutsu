"""Tests for the audit operations layer (BE-0145).

Operations-level tests for the /api/audit endpoint — the static determinism audit (BE-0049)
surfaced in the serve Web UI. No HTTP, no Simulator, no AI: the audit is a pure function of the
parsed scenario. The endpoint accepts either inline `yaml` (the editor's live, possibly-unsaved
content) or a `{target, path}` pair the server reads from disk (the Replay view).
"""

from __future__ import annotations

from pathlib import Path

from _shared import project

from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import ServeState


def _state(tmp_path: Path) -> ServeState:
    _scn_dir, cfg, runs = project(tmp_path)
    return ServeState(runs_dir=runs, config=cfg, cwd=tmp_path)


def test_inline_yaml_stable_scenario(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(
        state, {"yaml": "- name: s\n  steps:\n    - tap: { id: home.ok }\n"}
    )
    assert status == 200
    assert payload["ok"] is True
    (report,) = payload["reports"]
    assert report["scenario"] == "s"
    assert report["grade"] == "Stable"
    assert report["stable"] == 1
    assert report["stability"] == 1.0
    assert report["findings"] == []


def test_inline_yaml_fragile_index_selector(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(
        state, {"yaml": "- name: s\n  steps:\n    - tap: { id: x, index: 0 }\n"}
    )
    assert status == 200
    (report,) = payload["reports"]
    assert report["grade"] == "Fragile"
    assert report["fragile"] == 1
    assert any(f["kind"] == "fragile-selector" for f in report["findings"])


def test_inline_yaml_moderate_selector(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(
        state, {"yaml": "- name: s\n  steps:\n    - tap: { label: OK }\n"}
    )
    assert status == 200
    (report,) = payload["reports"]
    assert report["grade"] == "Moderate"
    assert report["moderate"] == 1


def test_inline_yaml_loose_wait_finding(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(
        state,
        {
            "yaml": "- name: s\n  steps:\n"
            "    - tap: { id: home.start }\n"
            "    - wait: { until: settled, timeout: 5 }\n"
        },
    )
    assert status == 200
    (report,) = payload["reports"]
    assert any(f["kind"] == "loose-wait" for f in report["findings"])


def test_inline_yaml_coordinate_gesture_finding(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(
        state,
        {"yaml": "- name: s\n  steps:\n    - swipe: { from: [0.5, 0.8], to: [0.5, 0.2] }\n"},
    )
    assert status == 200
    (report,) = payload["reports"]
    assert report["grade"] == "Fragile"
    assert any(f["kind"] == "coordinate-gesture" for f in report["findings"])


def test_multiple_scenarios_yield_multiple_reports(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(
        state,
        {
            "yaml": "- name: alpha\n  steps:\n    - tap: { id: a }\n"
            "- name: beta\n  steps:\n    - tap: { label: B }\n"
        },
    )
    assert status == 200
    grades = {r["scenario"]: r["grade"] for r in payload["reports"]}
    assert grades == {"alpha": "Stable", "beta": "Moderate"}


def test_path_reads_scenario_from_disk(tmp_path: Path) -> None:
    # No inline yaml: the server reads the saved file addressed by {target, path} (Replay view).
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(state, {"target": "demo", "path": "smoke.yaml"})
    assert status == 200
    assert payload["ok"] is True
    # _shared.SCENARIO holds two id-based scenarios (alpha, beta) — both Stable.
    assert {r["grade"] for r in payload["reports"]} == {"Stable"}


def test_missing_input_returns_400(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(state, {})
    assert status == 400
    assert "error" in payload


def test_path_without_config_returns_400(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runs.mkdir()
    state = ServeState(runs_dir=runs, config=None, cwd=tmp_path)
    payload, status = ops.audit_scenario(state, {"target": "demo", "path": "smoke.yaml"})
    assert status == 400
    assert "error" in payload


def test_unknown_scenario_path_returns_404(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(state, {"target": "demo", "path": "nope.yaml"})
    assert status == 404
    assert "error" in payload


def test_invalid_yaml_returns_400(tmp_path: Path) -> None:
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(state, {"yaml": "steps: [ this is not a scenario"})
    assert status == 400
    assert "error" in payload


def test_schema_invalid_scenario_returns_400(tmp_path: Path) -> None:
    # Parses as YAML but violates the scenario model (an unknown action) — a pydantic
    # ValidationError, which is a ValueError subclass, so it is caught as a 400 rather than a 500.
    state = _state(tmp_path)
    payload, status = ops.audit_scenario(
        state, {"yaml": "- name: s\n  steps:\n    - bogusAction: { id: x }\n"}
    )
    assert status == 400
    assert "error" in payload

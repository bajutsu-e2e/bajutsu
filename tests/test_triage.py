"""M4 triage skeleton: failure-context assembly + the rule-based agent + rendering."""

from __future__ import annotations

import json
from pathlib import Path

from bajutsu import triage
from bajutsu.triage import FailedStep, HeuristicTriageAgent, TriageContext


def _write_run(runs: Path, *, ok: bool, reason: str = "", step_action: str = "tap") -> Path:
    run = runs / "r"
    (run / "00-s" / "step0").mkdir(parents=True)
    manifest = {
        "runId": "r", "ok": ok, "backend": "idb",
        "scenarios": [{
            "scenario": "s", "ok": ok, "backend": "idb",
            "steps": [{
                "index": 0, "action": step_action, "ok": ok, "reason": reason,
                "artifacts": [{"name": "00-s/step0/elements.json", "kind": "elements", "provider": "driver"}],
            }],
            "expect_results": [],
            "failure": None if ok else f"step0 {step_action}: {reason}",
            "artifacts": [{"name": "00-s/device.log", "kind": "deviceLog", "provider": "simctl"}],
        }],
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "scenario.yaml").write_text(f"- name: s\n  steps:\n    - {step_action}: {{ id: home.titel }}\n",
                                       encoding="utf-8")
    (run / "00-s" / "step0" / "elements.json").write_text(json.dumps([
        {"identifier": "home.title", "label": "Home", "traits": ["button"], "value": None, "frame": [0, 0, 10, 10]},
    ]), encoding="utf-8")
    return run


def test_assemble_extracts_failure_context(tmp_path: Path) -> None:
    ctx = triage.assemble(_write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}"))
    assert ctx is not None
    assert ctx.scenario == "s"
    assert ctx.failed_step is not None and ctx.failed_step.action == "tap" and ctx.failed_step.index == 0
    assert ctx.target_id == "home.titel"                       # from scenario.yaml
    assert any(e["identifier"] == "home.title" for e in ctx.elements)  # the real screen
    assert "name: s" in ctx.scenario_yaml
    assert "deviceLog" in ctx.evidence


def test_assemble_none_when_run_passed(tmp_path: Path) -> None:
    assert triage.assemble(_write_run(tmp_path / "runs", ok=True)) is None  # nothing to triage


def test_heuristic_selector_suggests_close_id(tmp_path: Path) -> None:
    ctx = triage.assemble(_write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}"))
    assert ctx is not None
    result = HeuristicTriageAgent().triage(ctx)
    assert result.category == "selector"
    assert any("home.title" in s for s in result.suggestions)  # "did you mean home.title?"


def test_heuristic_ambiguous_selector() -> None:
    ctx = TriageContext(
        scenario="s", failure="...", failed_step=FailedStep(0, "tap", "2 件一致: ..."),
        failed_expectations=[], elements=[], scenario_yaml="", target_id="row.cell",
    )
    result = HeuristicTriageAgent().triage(ctx)
    assert result.category == "selector"
    assert any("within" in s or "index" in s for s in result.suggestions)


def test_heuristic_timing_and_assertion() -> None:
    timing = HeuristicTriageAgent().triage(TriageContext(
        scenario="s", failure="…", failed_step=FailedStep(1, "wait", "timeout"),
        failed_expectations=[], elements=[], scenario_yaml="", target_id="home.spinner",
    ))
    assert timing.category == "timing"

    assertion = HeuristicTriageAgent().triage(TriageContext(
        scenario="s", failure="expect: …", failed_step=None,
        failed_expectations=["value equals='2': id='counter'"], elements=[], scenario_yaml="", target_id=None,
    ))
    assert assertion.category == "assertion"


def test_render_has_diagnosis_and_fixes(tmp_path: Path) -> None:
    ctx = triage.assemble(_write_run(tmp_path / "runs", ok=False, reason="一致なし"))
    assert ctx is not None
    out = triage.render(ctx, HeuristicTriageAgent().triage(ctx))
    assert "triage · s" in out
    assert "diagnosis [selector]:" in out
    assert "suggested fixes:" in out

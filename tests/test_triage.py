"""M4 triage skeleton: failure-context assembly + the rule-based agent + rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu import triage
from bajutsu.cli import _rerun_command, app
from bajutsu.triage import FailedStep, Fix, HeuristicTriageAgent, TriageContext, apply_fix, diff_fix

runner = CliRunner()


def _write_run(
    runs: Path,
    *,
    ok: bool,
    reason: str = "",
    step_action: str = "tap",
    with_screenshot: bool = False,
    scenario_id: str = "home.titel",
) -> Path:
    run = runs / "r"
    (run / "00-s" / "step0").mkdir(parents=True)
    artifacts = [{"name": "00-s/step0/elements.json", "kind": "elements", "provider": "driver"}]
    if with_screenshot:
        artifacts.append(
            {"name": "00-s/step0/after.png", "kind": "screenshot", "provider": "driver"}
        )
        (run / "00-s" / "step0" / "after.png").write_bytes(b"\x89PNG\r\n\x1a\n demo")
    manifest = {
        "runId": "r",
        "ok": ok,
        "backend": "idb",
        "scenarios": [
            {
                "scenario": "s",
                "ok": ok,
                "backend": "idb",
                "steps": [
                    {
                        "index": 0,
                        "action": step_action,
                        "ok": ok,
                        "reason": reason,
                        "artifacts": artifacts,
                    }
                ],
                "expect_results": [],
                "failure": None if ok else f"step0 {step_action}: {reason}",
                "artifacts": [
                    {"name": "00-s/device.log", "kind": "deviceLog", "provider": "simctl"}
                ],
            }
        ],
    }
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run / "scenario.yaml").write_text(
        f"- name: s\n  steps:\n    - {step_action}: {{ id: {scenario_id} }}\n", encoding="utf-8"
    )
    (run / "00-s" / "step0" / "elements.json").write_text(
        json.dumps(
            [
                {
                    "identifier": "home.title",
                    "label": "Home",
                    "traits": ["button"],
                    "value": None,
                    "frame": [0, 0, 10, 10],
                },
            ]
        ),
        encoding="utf-8",
    )
    return run


def test_assemble_extracts_failure_context(tmp_path: Path) -> None:
    ctx = triage.assemble(
        _write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}")
    )
    assert ctx is not None
    assert ctx.scenario == "s"
    assert (
        ctx.failed_step is not None
        and ctx.failed_step.action == "tap"
        and ctx.failed_step.index == 0
    )
    assert ctx.target_id == "home.titel"  # from scenario.yaml
    assert any(e["identifier"] == "home.title" for e in ctx.elements)  # the real screen
    assert "name: s" in ctx.scenario_yaml
    assert "deviceLog" in ctx.evidence


def test_assemble_none_when_run_passed(tmp_path: Path) -> None:
    assert triage.assemble(_write_run(tmp_path / "runs", ok=True)) is None  # nothing to triage


def test_assemble_reads_failure_screenshot(tmp_path: Path) -> None:
    ctx = triage.assemble(_write_run(tmp_path / "runs", ok=False, reason="x", with_screenshot=True))
    assert ctx is not None
    assert ctx.screenshot == b"\x89PNG\r\n\x1a\n demo"


def test_assemble_no_screenshot_is_none(tmp_path: Path) -> None:
    ctx = triage.assemble(_write_run(tmp_path / "runs", ok=False, reason="x"))
    assert ctx is not None and ctx.screenshot is None


def test_heuristic_selector_suggests_close_id(tmp_path: Path) -> None:
    ctx = triage.assemble(
        _write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}")
    )
    assert ctx is not None
    result = HeuristicTriageAgent().triage(ctx)
    assert result.category == "selector"
    assert any("home.title" in s for s in result.suggestions)  # "did you mean home.title?"
    assert result.fix is not None and result.fix.kind == "renameId"
    assert (result.fix.find, result.fix.replace) == ("home.titel", "home.title")


def test_heuristic_ambiguous_selector() -> None:
    ctx = TriageContext(
        scenario="s",
        failure="...",
        failed_step=FailedStep(0, "tap", "2 件一致: ..."),
        failed_expectations=[],
        elements=[],
        scenario_yaml="",
        target_id="row.cell",
    )
    result = HeuristicTriageAgent().triage(ctx)
    assert result.category == "selector"
    assert any("within" in s or "index" in s for s in result.suggestions)
    assert result.fix is None  # ambiguity is not a mechanically-applicable rename


def test_apply_fix_renames_whole_token_only() -> None:
    text = "    - tap: { id: nav.setting }\n    - exists: { id: nav.settings }\n"
    patched, n = apply_fix(text, Fix("renameId", "rename", "nav.setting", "nav.settings"))
    assert n == 1  # only the exact token, never the substring inside nav.settings
    assert patched == "    - tap: { id: nav.settings }\n    - exists: { id: nav.settings }\n"


def test_apply_fix_addindex_replaces_exact_fragment() -> None:
    text = "    - tap: { id: row.cell }\n    - tap: { id: row.cellar }\n"
    patched, n = apply_fix(
        text, Fix("addIndex", "x", "{ id: row.cell }", "{ id: row.cell, index: 0 }")
    )
    assert n == 1 and "{ id: row.cell, index: 0 }" in patched  # the exact fragment, not row.cellar
    assert "{ id: row.cellar }" in patched


def test_apply_fix_fragment_absent_is_safe_noop() -> None:
    assert apply_fix(
        "nothing here\n", Fix("addIndex", "s", "{ id: gone }", "{ id: gone, index: 0 }")
    ) == ("nothing here\n", 0)


def test_diff_fix_shows_change() -> None:
    d = diff_fix("a: 1\n", "a: 2\n", "s.yaml")
    assert "-a: 1" in d and "+a: 2" in d and "s.yaml" in d


def test_cli_triage_apply_dry_run_then_write(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}")
    src = tmp_path / "src.yaml"
    src.write_text("- name: s\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8")

    dry = runner.invoke(app, ["triage", str(run), "--apply", str(src)])
    assert dry.exit_code == 0
    assert "rename id `home.titel` -> `home.title`" in dry.output
    assert "-    - tap: { id: home.titel }" in dry.output
    assert "+    - tap: { id: home.title }" in dry.output
    assert "dry-run" in dry.output
    assert "home.titel" in src.read_text(encoding="utf-8")  # unchanged on dry-run

    wrote = runner.invoke(app, ["triage", str(run), "--apply", str(src), "--write"])
    assert wrote.exit_code == 0 and "wrote" in wrote.output
    patched = src.read_text(encoding="utf-8")
    assert "home.title }" in patched and "home.titel" not in patched


def test_cli_triage_apply_no_fix_is_advisory(tmp_path: Path) -> None:
    # an ambiguous-match failure (target id IS on screen) has no applicable rename fix
    run = _write_run(
        tmp_path / "runs",
        ok=False,
        reason="2 件一致: {'id': 'home.title'}",
        scenario_id="home.title",
    )
    src = tmp_path / "src.yaml"
    src.write_text("- name: s\n  steps:\n    - tap: { id: home.title }\n", encoding="utf-8")
    r = runner.invoke(app, ["triage", str(run), "--apply", str(src)])
    assert r.exit_code == 0
    assert "no applicable structured fix" in r.output


def test_rerun_command_builder() -> None:
    cmd = _rerun_command("s.yaml", "demo", "idb", "DEAD-BEEF", "cfg.yaml")
    assert cmd[1:] == [
        "-m",
        "bajutsu",
        "run",
        "--scenario",
        "s.yaml",
        "--app",
        "demo",
        "--config",
        "cfg.yaml",
        "--no-erase",
        "--backend",
        "idb",
        "--udid",
        "DEAD-BEEF",
    ]
    bare = _rerun_command("s.yaml", "demo", "", "", "cfg.yaml")  # no backend, empty udid omitted
    assert "--backend" not in bare and "--udid" not in bare


def test_cli_rerun_needs_write(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}")
    src = tmp_path / "src.yaml"
    src.write_text("- name: s\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8")
    r = runner.invoke(app, ["triage", str(run), "--apply", str(src), "--rerun", "--app", "demo"])
    assert r.exit_code == 0
    assert "--rerun needs --write" in r.output
    assert "home.titel" in src.read_text(encoding="utf-8")  # untouched on dry-run


def test_cli_rerun_after_write(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    run = _write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}")
    src = tmp_path / "src.yaml"
    src.write_text("- name: s\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8")
    captured: dict[str, list[str]] = {}

    def fake_call(cmd: list[str]) -> int:
        captured["cmd"] = cmd
        return 0

    monkeypatch.setattr("bajutsu.cli.subprocess.call", fake_call)
    r = runner.invoke(
        app,
        [
            "triage",
            str(run),
            "--apply",
            str(src),
            "--write",
            "--rerun",
            "--app",
            "demo",
            "--backend",
            "idb",
        ],
    )
    assert r.exit_code == 0
    assert "wrote" in r.output and "fix verified" in r.output
    assert captured["cmd"][2:8] == ["bajutsu", "run", "--scenario", str(src), "--app", "demo"]
    assert "home.titel" not in src.read_text(encoding="utf-8")  # the fix was written


def test_heuristic_timing_and_assertion() -> None:
    timing = HeuristicTriageAgent().triage(
        TriageContext(
            scenario="s",
            failure="…",
            failed_step=FailedStep(1, "wait", "timeout"),
            failed_expectations=[],
            elements=[],
            scenario_yaml="",
            target_id="home.spinner",
        )
    )
    assert timing.category == "timing"

    assertion = HeuristicTriageAgent().triage(
        TriageContext(
            scenario="s",
            failure="expect: …",
            failed_step=None,
            failed_expectations=["value equals='2': id='counter'"],
            elements=[],
            scenario_yaml="",
            target_id=None,
        )
    )
    assert assertion.category == "assertion"


def test_render_has_diagnosis_and_fixes(tmp_path: Path) -> None:
    ctx = triage.assemble(_write_run(tmp_path / "runs", ok=False, reason="一致なし"))
    assert ctx is not None
    out = triage.render(ctx, HeuristicTriageAgent().triage(ctx))
    assert "triage · s" in out
    assert "diagnosis [selector]:" in out
    assert "suggested fixes:" in out

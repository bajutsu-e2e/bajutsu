"""M4 triage skeleton: failure-context assembly + the rule-based agent + rendering."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu import triage
from bajutsu.cli import app
from bajutsu.cli.commands.triage import _rerun_command
from bajutsu.triage import (
    FailedStep,
    Fix,
    HeuristicTriageAgent,
    TriageContext,
    _nearest_artifact,
    _read_artifact,
    _target_id,
    apply_fix,
    diff_fix,
)

runner = CliRunner()


def test_target_id_extracts_a_drag_targets_id() -> None:
    # `drag` is a selector-bearing action (BE-0227); a failing drag step must yield its `on` id so
    # triage can suggest disambiguation / a renameId self-heal, exactly as swipe/pinch/rotate do.
    from bajutsu.scenario import load_scenarios

    step = load_scenarios(
        "- name: x\n  steps:\n    - drag: { on: { id: gest.divider }, direction: left }\n"
    )[0].steps[0]
    assert _target_id(step) == "gest.divider"


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
        "backend": "xcuitest",
        "scenarios": [
            {
                "scenario": "s",
                "ok": ok,
                "backend": "xcuitest",
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
    cmd = _rerun_command("s.yaml", "demo", "xcuitest", "DEAD-BEEF", "cfg.yaml")
    assert cmd[1:] == [
        "-m",
        "bajutsu",
        "run",
        "--scenario",
        "s.yaml",
        "--target",
        "demo",
        "--config",
        "cfg.yaml",
        "--no-erase",
        "--backend",
        "xcuitest",
        "--udid",
        "DEAD-BEEF",
    ]
    bare = _rerun_command("s.yaml", "demo", "", "", "cfg.yaml")  # no backend, empty udid omitted
    assert "--backend" not in bare and "--udid" not in bare


def test_cli_rerun_needs_write(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}")
    src = tmp_path / "src.yaml"
    src.write_text("- name: s\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8")
    r = runner.invoke(app, ["triage", str(run), "--apply", str(src), "--rerun", "--target", "demo"])
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

    monkeypatch.setattr("bajutsu.cli.commands.triage.subprocess.call", fake_call)
    r = runner.invoke(
        app,
        [
            "triage",
            str(run),
            "--apply",
            str(src),
            "--write",
            "--rerun",
            "--target",
            "demo",
            "--backend",
            "xcuitest",
        ],
    )
    assert r.exit_code == 0
    assert "wrote" in r.output and "fix verified" in r.output
    assert captured["cmd"][2:8] == ["bajutsu", "run", "--scenario", str(src), "--target", "demo"]
    assert "home.titel" not in src.read_text(encoding="utf-8")  # the fix was written


def test_apply_result_packages_diff_and_patched() -> None:
    src = "    - tap: { id: home.titel }\n"
    ar = triage.apply_result(src, "s.yaml", Fix("renameId", "rename", "home.titel", "home.title"))
    assert ar.count == 1
    assert "home.title }" in ar.patched and "home.titel" not in ar.patched
    assert "-    - tap: { id: home.titel }" in ar.diff and "s.yaml" in ar.diff


def test_apply_result_noop_has_empty_diff() -> None:
    ar = triage.apply_result(
        "nothing here\n", "s.yaml", Fix("addIndex", "x", "{ id: gone }", "{ id: gone, index: 0 }")
    )
    assert ar.count == 0 and ar.diff == "" and ar.patched == "nothing here\n"  # a safe no-op


def test_result_payload_shape() -> None:
    ctx = TriageContext(
        scenario="s",
        failure="f",
        failed_step=FailedStep(0, "tap", "r"),
        failed_expectations=["e"],
        elements=[],
        scenario_yaml="",
        target_id="x",
        evidence=["deviceLog"],
    )
    tri = triage.Triage("sum", "selector", ["do x"], fix=Fix("renameId", "rename", "a", "b"))
    p = triage.result_payload(ctx, tri)
    assert p["scenario"] == "s" and p["category"] == "selector" and p["summary"] == "sum"
    assert p["failedStep"] == {"index": 0, "action": "tap", "reason": "r"}
    assert p["failedExpectations"] == ["e"] and p["evidence"] == ["deviceLog"]
    assert p["fix"] == {"kind": "renameId", "summary": "rename", "find": "a", "replace": "b"}
    assert p["apply"] is None  # no AppliedFix passed


def test_result_payload_no_fix_is_null() -> None:
    ctx = TriageContext(
        scenario="s",
        failure="f",
        failed_step=None,
        failed_expectations=[],
        elements=[],
        scenario_yaml="",
        target_id=None,
    )
    p = triage.result_payload(ctx, triage.Triage("s", "unknown", ["look"]))
    assert p["fix"] is None and p["failedStep"] is None and p["apply"] is None


def test_cli_triage_json_writes_result_with_diff(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", ok=False, reason="一致なし: {'id': 'home.titel'}")
    src = tmp_path / "src.yaml"
    src.write_text("- name: s\n  steps:\n    - tap: { id: home.titel }\n", encoding="utf-8")
    out = tmp_path / "triage.json"

    r = runner.invoke(app, ["triage", str(run), "--apply", str(src), "--json", str(out)])
    assert r.exit_code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["category"] == "selector"
    assert data["fix"] == {
        "kind": "renameId",
        "summary": "rename id `home.titel` -> `home.title`",
        "find": "home.titel",
        "replace": "home.title",
    }
    assert data["apply"]["count"] == 1
    assert "home.title }" in data["apply"]["patched"]
    assert "home.titel" in data["apply"]["diff"]
    # --json is a dry-run: the source file is untouched (apply is the UI's explicit save).
    assert "home.titel" in src.read_text(encoding="utf-8")


def test_cli_triage_json_without_apply_has_no_patch(tmp_path: Path) -> None:
    run = _write_run(tmp_path / "runs", ok=False, reason="一致なし")
    out = tmp_path / "t.json"
    r = runner.invoke(app, ["triage", str(run), "--json", str(out)])
    assert r.exit_code == 0
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["apply"] is None and data["summary"] and data["category"] == "selector"


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


# --- _read_artifact unit tests ---


def _make_steps(
    *kinds: str,
) -> list[dict[str, object]]:
    """Build a minimal steps list where each step has one artifact of the given kind."""
    return [
        {"index": i, "artifacts": [{"kind": k, "name": f"step{i}/{k}.dat"}]}
        for i, k in enumerate(kinds)
    ]


def test_nearest_artifact_prefers_failed_step_then_walks_back() -> None:
    # steps: [elements@0, screenshot@1, elements@2], failed at index 2
    steps = _make_steps("elements", "screenshot", "elements")
    # screenshot is only at step 1 -- nearest backward from index 2
    art = _nearest_artifact(steps, 2, "screenshot")
    assert art is not None and art["name"] == "step1/screenshot.dat"
    # elements is at the failed step itself (index 2)
    art2 = _nearest_artifact(steps, 2, "elements")
    assert art2 is not None and art2["name"] == "step2/elements.dat"


def test_nearest_artifact_ignores_steps_after_failed_index() -> None:
    # steps: [screenshot@0, elements@1], failed at index 0 -- step 1 must not be used
    steps = _make_steps("screenshot", "elements")
    art = _nearest_artifact(steps, 0, "elements")
    assert art is None  # step 1 is after the failure, so it is not scanned


def test_nearest_artifact_falls_back_to_last_when_no_failed_index() -> None:
    steps = _make_steps("screenshot", "elements", "screenshot")
    # no failed index -> scan from the end; last "screenshot" is at step 2
    art = _nearest_artifact(steps, None, "screenshot")
    assert art is not None and art["name"] == "step2/screenshot.dat"


def test_read_artifact_applies_loader_and_returns_default_on_miss(tmp_path: Path) -> None:
    # write a file for step0/elements.dat; step1 has no file
    (tmp_path / "step0").mkdir()
    (tmp_path / "step0" / "elements.dat").write_text("loaded", encoding="utf-8")

    steps = _make_steps("elements", "screenshot")  # step1 has screenshot, not elements
    sentinel: list[str] = []
    calls: list[Path] = []

    def loader(p: Path) -> list[str] | None:
        calls.append(p)
        try:
            return [p.read_text(encoding="utf-8")]
        except OSError:
            return None

    # failed at step 1: nearest elements is at step 0
    result = _read_artifact(tmp_path, steps, 1, "elements", loader, sentinel)
    assert result == ["loaded"]
    assert len(calls) == 1 and calls[0] == tmp_path / "step0/elements.dat"

    # kind not present anywhere -> default is returned without calling loader
    # artifact present but its file is missing: the loader IS called and returns None, so the
    # default kicks in (step1 has a screenshot artifact pointing at an unwritten file).
    calls.clear()
    result2 = _read_artifact(tmp_path, steps, 1, "screenshot", loader, sentinel)
    assert calls == [tmp_path / "step1/screenshot.dat"]  # loader was called
    assert result2 is sentinel  # ...and returned None, so the default is used

    # kind absent from every step: the default is returned WITHOUT calling the loader at all.
    calls.clear()
    result3 = _read_artifact(tmp_path, steps, 1, "video", loader, sentinel)
    assert calls == []  # no artifact of that kind -> loader never invoked
    assert result3 is sentinel


# --- cross-run assembly (BE-0220 Half 2) ---


def test_assemble_cross_run_gathers_pass_and_fail(tmp_path: Path) -> None:
    passing = _write_run(tmp_path / "pass", ok=True)
    failing = _write_run(tmp_path / "fail", ok=False, reason="一致なし: {'id': 'home.titel'}")
    ctx = triage.assemble_cross_run([passing], [failing], scenario="s", scenario_hash="abc")
    assert ctx is not None
    assert ctx.scenario == "s"
    assert ctx.scenario_hash == "abc"
    assert len(ctx.passing) == 1 and ctx.passing[0].ok is True
    assert len(ctx.failing) == 1 and ctx.failing[0].ok is False
    assert ctx.failing[0].failed_step is not None and ctx.failing[0].failed_step.action == "tap"
    assert ctx.target_id == "home.titel"  # from the failing run's scenario.yaml
    assert "name: s" in ctx.scenario_yaml


def test_assemble_cross_run_none_without_failing_evidence(tmp_path: Path) -> None:
    passing = _write_run(tmp_path / "pass", ok=True)
    # nothing to contrast against a pass -> nothing to diagnose
    assert triage.assemble_cross_run([passing], [], scenario="s") is None


def test_assemble_cross_run_none_without_passing_evidence(tmp_path: Path) -> None:
    failing = _write_run(tmp_path / "fail", ok=False, reason="x")
    # the guard is symmetric: with no pass to contrast against a fail, the prompt's "some runs
    # pass, some fail" premise no longer holds, so there is no intermittency to diagnose
    assert triage.assemble_cross_run([], [failing], scenario="s") is None


def test_assemble_cross_run_passing_evidence_is_run_end(tmp_path: Path) -> None:
    passing = _write_run(tmp_path / "pass", ok=True)
    failing = _write_run(tmp_path / "fail", ok=False, reason="x")
    ctx = triage.assemble_cross_run([passing], [failing], scenario="s")
    assert ctx is not None
    # a passing run has no failed step; its evidence is the element tree captured at the run's end
    assert ctx.passing[0].failed_step is None
    assert any(e["identifier"] == "home.title" for e in ctx.passing[0].elements)


def test_assemble_cross_run_skips_runs_missing_the_scenario(tmp_path: Path) -> None:
    passing = _write_run(tmp_path / "pass", ok=True)
    failing = _write_run(tmp_path / "fail", ok=False, reason="x")
    ctx = triage.assemble_cross_run([passing], [failing], scenario="other")
    assert ctx is None  # neither run holds a scenario named "other"


# --- laxer guard (BE-0023) ---

_LAXER_YAML = """- name: login
  steps:
    - tap: {id: submit}
    - wait:
        for: {id: home.title}
        timeout: 5
    - assert:
        - value: {sel: {id: home.title}, equals: Welcome}
        - exists: {id: avatar}
"""


def test_flag_laxer_clean_fix_is_not_flagged() -> None:
    # renaming an id token touches no assertion, wait, or selector uniqueness
    fix = Fix("renameId", "rename", "submit", "submitButton")
    assert triage.flag_laxer(_LAXER_YAML, fix) == []


def test_flag_laxer_raising_a_timeout_is_not_flagged() -> None:
    # lengthening a wait does not change what is asserted (BE-0023: raiseTimeout is allowed)
    fix = Fix("raiseTimeout", "raise timeout", "timeout: 5", "timeout: 30")
    assert triage.flag_laxer(_LAXER_YAML, fix) == []


def test_flag_laxer_lowering_a_timeout_is_flagged() -> None:
    fix = Fix("raiseTimeout", "raise timeout", "timeout: 5", "timeout: 1")
    warnings = triage.flag_laxer(_LAXER_YAML, fix)
    assert any("timeout" in w.lower() for w in warnings)


def test_flag_laxer_dropping_a_wait_is_flagged() -> None:
    fix = Fix(
        "raiseTimeout",
        "drop wait",
        "    - wait:\n        for: {id: home.title}\n        timeout: 5\n",
        "",
    )
    warnings = triage.flag_laxer(_LAXER_YAML, fix)
    assert any("wait" in w.lower() for w in warnings)


def test_flag_laxer_removing_an_assertion_is_flagged() -> None:
    fix = Fix("addIndex", "drop assert", "        - exists: {id: avatar}\n", "")
    warnings = triage.flag_laxer(_LAXER_YAML, fix)
    assert any("assert" in w.lower() for w in warnings)


def test_flag_laxer_loosening_a_value_match_is_flagged() -> None:
    # equals -> contains widens what passes: a weaker check
    fix = Fix("addIndex", "loosen", "equals: Welcome", "contains: Wel")
    warnings = triage.flag_laxer(_LAXER_YAML, fix)
    assert any(
        "value" in w.lower() or "label" in w.lower() or "match" in w.lower() for w in warnings
    )


def test_flag_laxer_widening_a_selector_is_flagged() -> None:
    # dropping the id from an assertion's selector widens it past uniqueness
    fix = Fix("addIndex", "widen", "sel: {id: home.title}", "sel: {label: Welcome}")
    warnings = triage.flag_laxer(_LAXER_YAML, fix)
    assert any(
        "selector" in w.lower() or "uniqueness" in w.lower() or "id" in w.lower() for w in warnings
    )


def test_flag_laxer_no_fix_is_empty() -> None:
    assert triage.flag_laxer(_LAXER_YAML, None) == []


def test_flag_laxer_unparseable_patch_is_reported() -> None:
    # a fix that corrupts the YAML can't be verified; the reviewer should be told, not misled
    fix = Fix("addIndex", "break", "steps:", "steps: : :")
    warnings = triage.flag_laxer(_LAXER_YAML, fix)
    assert warnings and any("parse" in w.lower() or "analyze" in w.lower() for w in warnings)


def test_result_payload_surfaces_laxer_warnings() -> None:
    # result_payload derives the guard from the context's scenario source + the proposed fix
    ctx = TriageContext(
        scenario="login",
        failure="x",
        failed_step=None,
        failed_expectations=[],
        elements=[],
        scenario_yaml=_LAXER_YAML,
        target_id=None,
    )
    tri = triage.Triage(
        "s", "timing", ["look"], Fix("raiseTimeout", "t", "timeout: 5", "timeout: 1")
    )
    p = triage.result_payload(ctx, tri)
    assert any("timeout" in w.lower() for w in p["laxer"])


def test_result_payload_no_laxer_key_is_empty() -> None:
    ctx = TriageContext(
        scenario="s",
        failure="f",
        failed_step=None,
        failed_expectations=[],
        elements=[],
        scenario_yaml="",
        target_id=None,
    )
    p = triage.result_payload(ctx, triage.Triage("s", "unknown", ["look"]))
    assert p["laxer"] == []


# --- cross-run surface (BE-0220 Half 2) ---

_CROSS_YAML = """- name: login
  steps:
    - tap: {id: home.titel}
    - assert:
        - exists: {id: avatar}
"""


def _cross_context() -> triage.CrossRunTriageContext:
    failing = triage.RunEvidence(
        run_id="rF",
        ok=False,
        failure="一致なし: {'id': 'home.titel'}",
        failed_step=FailedStep(0, "tap", "一致なし"),
        failed_expectations=[],
        elements=[
            {
                "identifier": "home.title",
                "label": "Home",
                "traits": ["button"],
                "value": None,
                "frame": [0, 0, 1, 1],
            }
        ],
    )
    passing = triage.RunEvidence(
        run_id="rP",
        ok=True,
        failure="",
        failed_step=None,
        failed_expectations=[],
        elements=[
            {
                "identifier": "home.title",
                "label": "Home",
                "traits": ["button"],
                "value": None,
                "frame": [0, 0, 1, 1],
            }
        ],
    )
    return triage.CrossRunTriageContext(
        scenario="login",
        scenario_hash="abc",
        scenario_yaml=_CROSS_YAML,
        target_id="home.titel",
        passing=[passing],
        failing=[failing],
    )


def _write_flaky_run(
    run_dir: Path, *, ok: bool, reason: str = "", scenario_hash: str | None = "sha-abc"
) -> Path:
    """A complete run of the `login` scenario under `run_dir` (manifest + scenario + elements)."""
    (run_dir / "00-login" / "step0").mkdir(parents=True)
    manifest = {
        "runId": run_dir.name,
        "ok": ok,
        "provenance": {"scenarioHash": scenario_hash},
        "scenarios": [
            {
                "scenario": "login",
                "ok": ok,
                "steps": [
                    {
                        "index": 0,
                        "action": "tap",
                        "ok": ok,
                        "reason": reason,
                        "artifacts": [
                            {
                                "name": "00-login/step0/elements.json",
                                "kind": "elements",
                                "provider": "driver",
                            }
                        ],
                    }
                ],
                "expect_results": [],
                "failure": None if ok else f"step0 tap: {reason}",
            }
        ],
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "scenario.yaml").write_text(
        "- name: login\n  steps:\n    - tap: {id: home.titel}\n", encoding="utf-8"
    )
    (run_dir / "00-login" / "step0" / "elements.json").write_text(
        json.dumps(
            [
                {
                    "identifier": "home.title",
                    "label": "Home",
                    "traits": ["button"],
                    "value": None,
                    "frame": [0, 0, 1, 1],
                }
            ]
        ),
        encoding="utf-8",
    )
    return run_dir


def test_render_cross_run_shows_counts_diagnosis_and_diff() -> None:
    ctx = _cross_context()
    fix = Fix("renameId", "rename id `home.titel` -> `home.title`", "home.titel", "home.title")
    tri = triage.Triage("intermittent selector", "selector-ambiguity", ["promote to id"], fix)
    out = triage.render_cross_run(ctx, tri)
    assert "flaky triage · login" in out
    assert "1 passing" in out and "1 failing" in out
    assert "abc" in out  # the content fingerprint
    assert "[selector-ambiguity]" in out
    # the fix is shown as a reviewable proposal diff, not silently applied
    assert "-    - tap: {id: home.titel}" in out
    assert "+    - tap: {id: home.title}" in out


def test_render_cross_run_flags_laxer_fix() -> None:
    ctx = _cross_context()
    # dropping the assertion weakens the test; the render must surface the laxer warning
    fix = Fix("addIndex", "drop assert", "        - exists: {id: avatar}\n", "")
    tri = triage.Triage("s", "unknown", [], fix)
    out = triage.render_cross_run(ctx, tri)
    assert "laxer" in out.lower() and "assert" in out.lower()


def test_cross_run_payload_shape() -> None:
    ctx = _cross_context()
    fix = Fix("raiseTimeout", "raise", "timeout: 5", "timeout: 30")
    tri = triage.Triage("s", "timing", ["wait longer"], fix)
    p = triage.cross_run_payload(ctx, tri)
    assert p["scenario"] == "login" and p["scenarioHash"] == "abc"
    assert p["category"] == "timing"
    assert [ev["runId"] for ev in p["failing"]] == ["rF"]
    assert [ev["runId"] for ev in p["passing"]] == ["rP"]
    assert p["fix"]["kind"] == "raiseTimeout"
    assert p["laxer"] == []  # no wait in this scenario, so the fix is a no-op and not laxer


def test_split_flaky_runs_classifies_by_scenario_verdict(tmp_path: Path) -> None:
    from bajutsu.cli.commands.triage import _split_flaky_runs

    hist = tmp_path / "hist"
    _write_flaky_run(hist / "r1", ok=True)
    _write_flaky_run(hist / "r2", ok=False, reason="一致なし: {'id': 'home.titel'}")
    _write_flaky_run(hist / "r3", ok=True)
    name, pass_dirs, fail_dirs, scenario_hash = _split_flaky_runs(hist, "login")
    assert name == "login"
    assert scenario_hash == "sha-abc"
    assert sorted(d.name for d in pass_dirs) == ["r1", "r3"]
    assert [d.name for d in fail_dirs] == ["r2"]


def test_split_flaky_runs_excludes_other_fingerprint(tmp_path: Path) -> None:
    # `--flaky` contrasts runs at ONE content fingerprint. A run recorded after the
    # scenario was edited (different scenarioHash) is a different test, not flaky evidence,
    # so it must be dropped rather than fed to the model as a contradictory contrast.
    from bajutsu.cli.commands.triage import _split_flaky_runs

    hist = tmp_path / "hist"
    _write_flaky_run(hist / "r1", ok=True)  # reference fingerprint sha-abc
    _write_flaky_run(hist / "r2", ok=False, reason="一致なし: {'id': 'home.titel'}")
    _write_flaky_run(hist / "r3", ok=False, scenario_hash="sha-edited")
    name, pass_dirs, fail_dirs, scenario_hash = _split_flaky_runs(hist, "login")
    assert name == "login" and scenario_hash == "sha-abc"
    assert [d.name for d in pass_dirs] == ["r1"]
    assert [d.name for d in fail_dirs] == ["r2"]  # r3 (different fingerprint) excluded


def test_split_flaky_runs_reference_hash_from_first_stamped_run(tmp_path: Path) -> None:
    # The reference fingerprint must come from the first run that actually HAS one, not the
    # literal first match. If the first match predates provenance stamping (no scenarioHash),
    # locking `scenario_hash = None` would disable the guard for every later run, letting two
    # genuinely different fingerprints mix — the very bug the fingerprint filter prevents.
    from bajutsu.cli.commands.triage import _split_flaky_runs

    hist = tmp_path / "hist"
    _write_flaky_run(hist / "r1", ok=True, scenario_hash=None)  # pre-provenance: no stamp
    _write_flaky_run(hist / "r2", ok=False, scenario_hash="sha-x")  # first stamped -> reference
    _write_flaky_run(hist / "r3", ok=False, scenario_hash="sha-y")  # different fingerprint
    name, pass_dirs, fail_dirs, scenario_hash = _split_flaky_runs(hist, "login")
    assert name == "login" and scenario_hash == "sha-x"
    assert [d.name for d in pass_dirs] == ["r1"]  # unstamped run kept (grace)
    assert [d.name for d in fail_dirs] == ["r2"]  # r3 (sha-y) dropped as a different definition


def test_split_flaky_runs_no_match_returns_none(tmp_path: Path) -> None:
    from bajutsu.cli.commands.triage import _split_flaky_runs

    hist = tmp_path / "hist"
    _write_flaky_run(hist / "r1", ok=True)
    name, pass_dirs, fail_dirs, _ = _split_flaky_runs(hist, "nope")
    assert name is None and pass_dirs == [] and fail_dirs == []


def test_cli_flaky_requires_scenario(tmp_path: Path) -> None:
    r = runner.invoke(app, ["triage", "--flaky", "--history", str(tmp_path), "--ai"])
    assert r.exit_code == 2 and "--scenario" in r.output


def test_cli_flaky_requires_history() -> None:
    r = runner.invoke(app, ["triage", "--flaky", "--scenario", "login", "--ai"])
    assert r.exit_code == 2 and "--history" in r.output


def test_cli_flaky_requires_ai() -> None:
    r = runner.invoke(app, ["triage", "--flaky", "--scenario", "login", "--history", "x"])
    assert r.exit_code == 2 and "--ai" in r.output


def _fake_cross_run_agent(fix: Fix | None) -> type:
    class _FakeAgent:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def triage_flaky(self, context: triage.CrossRunTriageContext) -> triage.Triage:
            assert context.scenario == "login"
            return triage.Triage("flaky selector", "selector-ambiguity", ["promote to id"], fix)

    return _FakeAgent


def _stub_ai_cli(monkeypatch: pytest.MonkeyPatch, fix: Fix | None) -> None:
    monkeypatch.setattr(
        "bajutsu.agents.claude_triage.ClaudeCrossRunTriageAgent", _fake_cross_run_agent(fix)
    )
    monkeypatch.setattr("bajutsu.cli.commands.triage._require_ai_credential", lambda eff: None)
    monkeypatch.setattr("bajutsu.cli.commands.triage._install_usage_ledger", lambda eff, cmd: None)
    monkeypatch.setattr("bajutsu.cli.commands.triage._warn_onscreen_secrets", lambda eff: None)


def test_cli_flaky_end_to_end(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hist = tmp_path / "hist"
    _write_flaky_run(hist / "r1", ok=True)
    _write_flaky_run(hist / "r2", ok=False, reason="一致なし: {'id': 'home.titel'}")
    _stub_ai_cli(monkeypatch, Fix("renameId", "rename id", "home.titel", "home.title"))
    r = runner.invoke(
        app, ["triage", "--flaky", "--scenario", "login", "--history", str(hist), "--ai"]
    )
    assert r.exit_code == 0, r.output
    assert "flaky triage · login" in r.output
    assert "[selector-ambiguity]" in r.output
    assert "home.titel" in r.output  # the reviewable proposal diff


def test_cli_flaky_json_writes_payload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hist = tmp_path / "hist"
    _write_flaky_run(hist / "r1", ok=True)
    _write_flaky_run(hist / "r2", ok=False, reason="一致なし: {'id': 'home.titel'}")
    _stub_ai_cli(monkeypatch, Fix("renameId", "rename id", "home.titel", "home.title"))
    out = tmp_path / "flaky.json"
    r = runner.invoke(
        app,
        [
            "triage",
            "--flaky",
            "--scenario",
            "login",
            "--history",
            str(hist),
            "--ai",
            "--json",
            str(out),
        ],
    )
    assert r.exit_code == 0, r.output
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["scenario"] == "login" and payload["category"] == "selector-ambiguity"
    assert payload["fix"]["kind"] == "renameId"
    assert "laxer" in payload


def test_cli_flaky_no_failing_run_is_advisory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hist = tmp_path / "hist"
    _write_flaky_run(hist / "r1", ok=True)  # all green — nothing flaky to contrast
    _stub_ai_cli(monkeypatch, None)
    r = runner.invoke(
        app, ["triage", "--flaky", "--scenario", "login", "--history", str(hist), "--ai"]
    )
    assert r.exit_code == 0
    assert "nothing to diagnose" in r.output.lower() or "no failing" in r.output.lower()


def test_cli_flaky_no_passing_run_names_the_missing_side(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hist = tmp_path / "hist"
    _write_flaky_run(hist / "r1", ok=False, reason="x")  # all red — no pass to contrast
    _stub_ai_cli(monkeypatch, None)
    r = runner.invoke(
        app, ["triage", "--flaky", "--scenario", "login", "--history", str(hist), "--ai"]
    )
    assert r.exit_code == 0
    # the advisory must name the side that is actually missing, not the opposite
    assert "no passing" in r.output.lower()

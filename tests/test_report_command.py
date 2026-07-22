"""Tests for `bajutsu report` and the re-render path (BE-0068).

Re-rendering is a pure function of the stored run dir: it reproduces the report a fresh `run`
baked (same data + template), reflects template changes on old runs, and never recomputes a
verdict. These run device-free against a baked fixture run dir.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bajutsu.assertions import AssertionResult
from bajutsu.cli import app
from bajutsu.orchestrator import RunResult, StepOutcome
from bajutsu.report import rerender_html, write_report
from bajutsu.scenario import dump_scenario_file, load_scenarios

runner = CliRunner()

SCENARIO = "- name: smoke\n  steps:\n    - tap: { id: home.start }\n  expect:\n    - exists: { id: home.title }\n"


def _bake(run_dir: Path) -> None:
    """Bake a run dir the way the pipeline does: results + manifest + scenario.yaml + report.html."""
    scenarios = load_scenarios(SCENARIO)
    from bajutsu.report import scenario_render_inputs

    definitions, sources = scenario_render_inputs(scenarios)
    results = [
        RunResult(
            scenario="smoke",
            ok=True,
            steps=[
                StepOutcome(
                    index=0,
                    action="tap home.start",
                    assertion_results=[
                        AssertionResult(ok=True, kind="exists", detail="home.title")
                    ],
                )
            ],
            expect_results=[AssertionResult(ok=True, kind="exists", detail="home.title")],
            backend="xcuitest",
        )
    ]
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "scenario.yaml").write_text(dump_scenario_file(scenarios), encoding="utf-8")
    write_report(run_dir, run_dir.name, results, definitions, sources, source_name="smoke.yaml")


def test_rerender_equals_the_original_bake(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "r1"
    _bake(run_dir)
    baked = (run_dir / "report.html").read_text(encoding="utf-8")
    # re-rendering from the stored model reproduces the baked report byte-for-byte (pure function)
    assert rerender_html(run_dir) == baked


def test_report_command_rewrites_report_html(tmp_path: Path) -> None:
    run_dir = tmp_path / "runs" / "r1"
    _bake(run_dir)
    baked = (run_dir / "report.html").read_text(encoding="utf-8")
    (run_dir / "report.html").write_text("STALE", encoding="utf-8")  # simulate an old/edited bake
    result = runner.invoke(app, ["report", "r1", "--runs", str(tmp_path / "runs")])
    assert result.exit_code == 0
    assert (run_dir / "report.html").read_text(encoding="utf-8") == baked  # refreshed from data


def test_report_all_rebakes_every_run(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    for rid in ("r1", "r2"):
        _bake(runs / rid)
        (runs / rid / "report.html").write_text("STALE", encoding="utf-8")
    result = runner.invoke(app, ["report", "--all", "--runs", str(runs)])
    assert result.exit_code == 0
    assert all(
        (runs / rid / "report.html").read_text(encoding="utf-8") != "STALE" for rid in ("r1", "r2")
    )


def test_report_missing_run_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report", "nope", "--runs", str(tmp_path)])
    assert result.exit_code == 2 and "run not found" in result.output


def test_report_requires_run_or_all(tmp_path: Path) -> None:
    result = runner.invoke(app, ["report"])
    assert result.exit_code == 2 and "not both" in result.output


def test_report_handles_a_run_missing_scenario_yaml(tmp_path: Path) -> None:
    # a manifest but no scenario.yaml (a legacy / damaged run): report cleanly, don't traceback
    run_dir = tmp_path / "runs" / "r1"
    _bake(run_dir)
    (run_dir / "scenario.yaml").unlink()
    result = runner.invoke(app, ["report", "r1", "--runs", str(tmp_path / "runs")])
    assert result.exit_code == 2
    assert "could not re-render" in result.output


def test_report_all_continues_past_a_broken_run(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _bake(runs / "ok")
    _bake(runs / "broken")
    (runs / "broken" / "scenario.yaml").unlink()
    (runs / "ok" / "report.html").write_text("STALE", encoding="utf-8")
    result = runner.invoke(app, ["report", "--all", "--runs", str(runs)])
    assert result.exit_code == 1  # one failed
    assert (runs / "ok" / "report.html").read_text(encoding="utf-8") != "STALE"  # good one rebaked

"""`bajutsu stats` CLI (BE-0102) — text / JSON / HTML over a runs directory."""

from __future__ import annotations

import json
from pathlib import Path

from typer.testing import CliRunner

from bajutsu.cli import app

runner = CliRunner()


def _write_run(runs_dir: Path, run_id: str, *, ok: bool, scenario_hash: str | None = None) -> None:
    """Write a minimal `manifest.json` under `runs_dir/run_id/`, as the runner would."""
    run = runs_dir / run_id
    run.mkdir(parents=True)
    manifest: dict[str, object] = {
        "runId": run_id,
        "ok": ok,
        "backend": "fake",
        "scenarios": [{"scenario": "s", "ok": ok, "duration_s": 1.0}],
    }
    if scenario_hash is not None:
        manifest["provenance"] = {"scenarioHash": scenario_hash}
    (run / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")


def test_stats_text_output(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_run(runs, "20260101-000000", ok=True)
    _write_run(runs, "20260102-000000", ok=False)
    result = runner.invoke(app, ["stats", "--runs", str(runs)])
    assert result.exit_code == 0
    assert "runs: 2" in result.stdout
    assert "50%" in result.stdout


def test_stats_json_output(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_run(runs, "20260101-000000", ok=True)
    result = runner.invoke(app, ["stats", "--runs", str(runs), "--json"])
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["runs"] == 1
    assert payload["passed_runs"] == 1


def test_stats_html_output(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    _write_run(runs, "20260101-000000", ok=True, scenario_hash="sha256:aaa")
    out = tmp_path / "nested" / "stats.html"
    result = runner.invoke(app, ["stats", "--runs", str(runs), "--html", str(out)])
    assert result.exit_code == 0
    assert out.is_file()
    assert out.read_text(encoding="utf-8").startswith("<!DOCTYPE html>")


def test_stats_missing_runs_dir_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["stats", "--runs", str(tmp_path / "nope")])
    assert result.exit_code == 2

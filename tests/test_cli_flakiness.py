"""`bajutsu flakiness` CLI (BE-0220, Half 1) — rank scenarios by cross-run flakiness.

Two read-only sources feed one ranking: `--history <runs-dir>` mines a directory of run manifests,
and the default reads the serve database (`BAJUTSU_DATABASE_URL`). Neither decides a verdict or gates
CI. Driven against real manifests on disk and a real SQLite database — no mocks.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.serve.server.db import RunRecord, SqlRepository
from bajutsu.serve.server.models import Base

runner = CliRunner()


def _write_run(runs_dir: Path, run_id: str, *, ok: bool, scenario_hash: str = "sha256:a") -> None:
    """Write a minimal `manifest.json` under `runs_dir/run_id/`, as the runner would."""
    run = runs_dir / run_id
    run.mkdir(parents=True)
    (run / "manifest.json").write_text(
        json.dumps(
            {
                "runId": run_id,
                "ok": ok,
                "provenance": {"scenarioHash": scenario_hash},
                "scenarios": [{"scenario": "login", "ok": ok}],
            }
        ),
        encoding="utf-8",
    )


def test_history_text_output(tmp_path: Path) -> None:
    _write_run(tmp_path, "20260101-000000", ok=True)
    _write_run(tmp_path, "20260102-000000", ok=False)

    result = runner.invoke(app, ["flakiness", "--history", str(tmp_path)])

    assert result.exit_code == 0
    assert "login" in result.stdout
    assert "flaky" in result.stdout


def test_history_json_output(tmp_path: Path) -> None:
    _write_run(tmp_path, "20260101-000000", ok=True)
    _write_run(tmp_path, "20260102-000000", ok=False)

    result = runner.invoke(app, ["flakiness", "--history", str(tmp_path), "--json"])

    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    (scenario,) = payload["scenarios"]
    assert scenario["classification"] == "flaky"
    assert scenario["representative_pass_run_id"] == "20260101-000000"
    assert scenario["representative_fail_run_id"] == "20260102-000000"


def test_negative_window_exits_2(tmp_path: Path) -> None:
    _write_run(tmp_path, "20260101-000000", ok=True)

    result = runner.invoke(app, ["flakiness", "--history", str(tmp_path), "--window", "-1"])

    assert result.exit_code == 2
    assert "--window" in result.stdout


def test_positive_window_trims_to_newest_runs(tmp_path: Path) -> None:
    # Oldest fails, the two newest pass — the whole history is flaky, but the newest run alone is not.
    _write_run(tmp_path, "20260101-000000", ok=False)
    _write_run(tmp_path, "20260102-000000", ok=True)
    _write_run(tmp_path, "20260103-000000", ok=True)

    full = runner.invoke(app, ["flakiness", "--history", str(tmp_path), "--json"])
    (whole,) = json.loads(full.stdout)["scenarios"]
    assert whole["runs"] == 3
    assert whole["classification"] == "flaky"

    windowed = runner.invoke(
        app, ["flakiness", "--history", str(tmp_path), "--window", "1", "--json"]
    )
    (newest,) = json.loads(windowed.stdout)["scenarios"]
    assert newest["runs"] == 1
    assert newest["passed"] == 1
    assert newest["classification"] != "flaky"


def test_missing_runs_dir_exits_2(tmp_path: Path) -> None:
    result = runner.invoke(app, ["flakiness", "--history", str(tmp_path / "nope")])
    assert result.exit_code == 2
    assert "not found" in result.stdout


def test_no_database_configured_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)
    result = runner.invoke(app, ["flakiness"])
    assert result.exit_code == 2
    assert "BAJUTSU_DATABASE_URL" in result.stdout


def test_reads_database_when_configured(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    db_path = tmp_path / "serve.sqlite"
    url = f"sqlite:///{db_path}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    repo = SqlRepository(engine)
    repo.ensure_org("default", slug="default", name="Default")
    repo.record_run(
        RunRecord(
            id="20260101-000000",
            org_id="default",
            status="done",
            ok=True,
            summary={"scenarios": ["login"]},
            scenario_hash="sha256:a",
        )
    )
    repo.record_run(
        RunRecord(
            id="20260102-000000",
            org_id="default",
            status="done",
            ok=False,
            summary={"scenarios": ["login"]},
            scenario_hash="sha256:a",
        )
    )
    monkeypatch.setenv("BAJUTSU_DATABASE_URL", url)

    result = runner.invoke(app, ["flakiness", "--json"])

    assert result.exit_code == 0
    (scenario,) = json.loads(result.stdout)["scenarios"]
    assert scenario["classification"] == "flaky"
    assert scenario["name"] == "login"

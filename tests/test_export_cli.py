"""Tests for `bajutsu export` — archive an existing run to a zip (BE-0060)."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

from typer.testing import CliRunner

from bajutsu.cli import app

runner = CliRunner()


def _run_dir(tmp_path: Path, run_id: str = "20260101-120000") -> Path:
    run = tmp_path / "runs" / run_id
    run.mkdir(parents=True)
    (run / "report.html").write_text("<html></html>", encoding="utf-8")
    (run / "manifest.json").write_text("{}", encoding="utf-8")
    return run


def test_export_writes_zip_beside_the_run_by_default(tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    result = runner.invoke(app, ["export", str(run)])
    assert result.exit_code == 0
    out = run.parent / f"{run.name}.zip"
    assert out.is_file()
    with zipfile.ZipFile(io.BytesIO(out.read_bytes())) as zf:
        assert f"{run.name}/report.html" in zf.namelist()


def test_export_resolves_a_run_id_under_runs_root(tmp_path: Path) -> None:
    _run_dir(tmp_path, "r1")
    out = tmp_path / "r1.zip"
    result = runner.invoke(app, ["export", "r1", "--runs", str(tmp_path / "runs"), "-o", str(out)])
    assert result.exit_code == 0 and out.is_file()


def test_export_refuses_to_overwrite_without_force(tmp_path: Path) -> None:
    run = _run_dir(tmp_path)
    out = tmp_path / "out.zip"
    out.write_bytes(b"existing")
    blocked = runner.invoke(app, ["export", str(run), "-o", str(out)])
    assert blocked.exit_code == 2 and "refusing to overwrite" in blocked.output
    assert out.read_bytes() == b"existing"  # untouched
    forced = runner.invoke(app, ["export", str(run), "-o", str(out), "--force"])
    assert forced.exit_code == 0 and out.read_bytes() != b"existing"


def test_export_missing_run_exits_two(tmp_path: Path) -> None:
    result = runner.invoke(app, ["export", str(tmp_path / "nope"), "--runs", str(tmp_path)])
    assert result.exit_code == 2 and "run not found" in result.output


def test_export_treats_a_slashed_value_as_a_path_not_a_run_id(tmp_path: Path) -> None:
    # "runs/r1" (a path, missing here) must not be silently re-rooted under --runs as an id.
    result = runner.invoke(app, ["export", "runs/r1", "--runs", str(tmp_path / "runs")])
    assert result.exit_code == 2 and "run not found: runs/r1" in result.output

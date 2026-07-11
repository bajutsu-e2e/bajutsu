"""BE-0225 unit 5: the `bajutsu project` CLI and `run --project` — a headless view of the same
project hub serve exposes over HTTP. Exercises the real `LocalProjectRegistry` JSON store (no
mocks): `add` / `ls` / `rm` / `use` against a runs dir under `tmp_path`, so the store lands at
`tmp_path/projects.json`.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli.commands.run import _resolve_project_config

runner = CliRunner()


@pytest.fixture(autouse=True)
def _local_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the CLI to the on-disk JSON store — a stray `BAJUTSU_DATABASE_URL` would send it to a DB."""
    monkeypatch.delenv("BAJUTSU_DATABASE_URL", raising=False)


def test_add_then_ls_marks_the_first_project_active(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    add = runner.invoke(
        app, ["project", "add", "checkout", "--config", "shop.config.yaml", "--runs", str(runs)]
    )
    assert add.exit_code == 0, add.output

    ls = runner.invoke(app, ["project", "ls", "--runs", str(runs)])
    assert ls.exit_code == 0, ls.output
    assert "checkout" in ls.output
    # The first project registered becomes active, marked with a leading '*'.
    assert "* checkout" in ls.output


def test_use_switches_the_active_marker(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    for name in ("shop", "docs"):
        runner.invoke(
            app, ["project", "add", name, "--config", f"{name}.yaml", "--runs", str(runs)]
        )
    # The first registered ('shop') is active; switch to 'docs'.
    use = runner.invoke(app, ["project", "use", "docs", "--runs", str(runs)])
    assert use.exit_code == 0, use.output

    ls = runner.invoke(app, ["project", "ls", "--runs", str(runs)])
    assert "* docs" in ls.output
    assert "  shop" in ls.output


def test_rm_removes_the_binding(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runner.invoke(app, ["project", "add", "shop", "--config", "shop.yaml", "--runs", str(runs)])
    rm = runner.invoke(app, ["project", "rm", "shop", "--runs", str(runs)])
    assert rm.exit_code == 0, rm.output

    ls = runner.invoke(app, ["project", "ls", "--runs", str(runs)])
    assert "no projects registered" in ls.output


def test_rm_unknown_project_fails(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runner.invoke(app, ["project", "add", "shop", "--config", "shop.yaml", "--runs", str(runs)])
    rm = runner.invoke(app, ["project", "rm", "ghost", "--runs", str(runs)])
    assert rm.exit_code == 1
    assert "no project named 'ghost'" in rm.output


def test_run_project_resolves_a_registered_config(tmp_path: Path) -> None:
    # The headless trigger: `run --project X` resolves X's stored source back to its `--config` spec.
    runs = tmp_path / "runs"
    add = runner.invoke(
        app, ["project", "add", "shop", "--config", "github:acme/shop@main", "--runs", str(runs)]
    )
    assert add.exit_code == 0, add.output
    assert _resolve_project_config("shop", str(runs)) == "github:acme/shop@main"


def test_run_project_unknown_name_errors(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runner.invoke(app, ["project", "add", "shop", "--config", "shop.yaml", "--runs", str(runs)])
    result = runner.invoke(
        app, ["run", "--target", "x", "--project", "ghost", "--runs-dir", str(runs)]
    )
    assert result.exit_code != 0
    assert "no project named 'ghost'" in result.output


def test_run_project_and_config_are_mutually_exclusive(tmp_path: Path) -> None:
    runs = tmp_path / "runs"
    runner.invoke(app, ["project", "add", "shop", "--config", "shop.yaml", "--runs", str(runs)])
    result = runner.invoke(
        app,
        [
            "run",
            "--target",
            "x",
            "--project",
            "shop",
            "--config",
            "other.yaml",
            "--runs-dir",
            str(runs),
        ],
    )
    assert result.exit_code != 0
    assert "not both" in result.output

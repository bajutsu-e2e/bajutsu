"""Tests for the CLI error/loading paths (the device paths need a Simulator).

The sandbox has no rocketsim/idb on PATH, so backend selection fails cleanly with
exit code 2 — which lets us drive run/doctor right up to the device boundary.
"""

from __future__ import annotations

from pathlib import Path

from typer.testing import CliRunner

from bajutsu.cli import app

runner = CliRunner()

CONFIG = """
defaults: { backend: [rocketsim, idb] }
apps:
  demo: { bundleId: com.example.demo, idNamespaces: [home] }
"""

SCENARIO = """
- name: demo
  steps:
    - tap: { id: home.title }
"""


def _write(tmp_path: Path) -> tuple[Path, Path]:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(CONFIG, encoding="utf-8")
    scn = tmp_path / "s.yaml"
    scn.write_text(SCENARIO, encoding="utf-8")
    return cfg, scn


def test_run_missing_config(tmp_path: Path) -> None:
    r = runner.invoke(app, ["run", "x.yaml", "--app", "demo", "--config", str(tmp_path / "nope.yaml")])
    assert r.exit_code == 2
    assert "config not found" in r.output


def test_run_unknown_app(tmp_path: Path) -> None:
    cfg, scn = _write(tmp_path)
    r = runner.invoke(app, ["run", str(scn), "--app", "ghost", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "unknown app" in r.output


def test_run_missing_scenario(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["run", str(tmp_path / "missing.yaml"), "--app", "demo", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "scenario not found" in r.output


def test_run_no_backend_available(tmp_path: Path) -> None:
    # An unknown backend is never available -> clean exit 2 (independent of PATH).
    cfg, scn = _write(tmp_path)
    r = runner.invoke(app, ["run", str(scn), "--app", "demo", "--backend", "nope",
                            "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_doctor_no_backend_available(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["doctor", "--app", "demo", "--backend", "nope", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_record_no_backend_available(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    out = tmp_path / "rec.yaml"
    r = runner.invoke(app, ["record", str(out), "--app", "demo", "--goal", "open settings",
                            "--backend", "nope", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "no available actuator" in r.output


def test_record_unknown_app(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["record", str(tmp_path / "rec.yaml"), "--app", "ghost",
                            "--goal", "x", "--config", str(cfg)])
    assert r.exit_code == 2


def test_doctor_unknown_app(tmp_path: Path) -> None:
    cfg, _ = _write(tmp_path)
    r = runner.invoke(app, ["doctor", "--app", "ghost", "--config", str(cfg)])
    assert r.exit_code == 2

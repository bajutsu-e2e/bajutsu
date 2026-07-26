"""Tests for `bajutsu doctor --environment-only` (BE-0304).

The flag stops doctor at the runnability gate: it renders the `environment:` section and exits on the
gate's verdict, *without* the screen probe. That probe reads the live screen — on iOS it spins up a
short-lived XCUITest runner — so a CI lane that only wants to prove the environment gate against a
real host must be able to skip it and its unrelated failure modes. These tests pin that the probe is
never reached in this mode, on both the pass and the fail side, and that the exit code follows the
gate. The device seams (the booted-device count, the screen probe) are the one class the project mocks
— an external dependency — so the gate's own logic still runs for real.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.preflight import Check

runner = CliRunner()


def _config(tmp_path: Path) -> Path:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text("targets:\n  demo:\n    package: com.example.app\n", encoding="utf-8")
    return cfg


def _forbid_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the screen probe blow up, so any test that reaches it fails loudly."""

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("--environment-only must not reach the screen probe")

    monkeypatch.setattr("bajutsu.cli.commands.doctor.probe_screen", boom)


def _adb_available(monkeypatch: pytest.MonkeyPatch) -> None:
    """Report the `adb` tool as present so actuator selection resolves regardless of host.

    `select_actuator(["adb"])` gates on `adb` being on PATH — a host without the Android SDK (the
    Linux `check` runner) would otherwise fail selection and exit 2 before the gate under test runs.
    Tool presence is an external dependency the gate's *own* logic doesn't hinge on, so we make it
    deterministic here; the environment gate's content stays real, injected per test below.
    """
    import shutil

    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd, *a, **k: "/usr/bin/adb" if cmd == "adb" else real_which(cmd, *a, **k),
    )


def test_environment_only_passes_without_probing_the_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_probe(monkeypatch)
    _adb_available(monkeypatch)
    monkeypatch.setattr(
        "bajutsu.preflight.doctor_environment_checks",
        lambda *a, **k: [Check("device attached", True, "1 attached")],
    )
    r = runner.invoke(
        app,
        [
            "doctor",
            "--target",
            "demo",
            "--backend",
            "adb",
            "--config",
            str(_config(tmp_path)),
            "--environment-only",
        ],
    )
    assert r.exit_code == 0
    assert "environment:" in r.stdout
    assert "✓ device attached" in r.stdout


def test_environment_only_fails_loudly_on_a_broken_gate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_probe(monkeypatch)
    _adb_available(monkeypatch)
    monkeypatch.setattr(
        "bajutsu.preflight.doctor_environment_checks",
        lambda *a, **k: [Check("device attached", False, "attach a device")],
    )
    r = runner.invoke(
        app,
        [
            "doctor",
            "--target",
            "demo",
            "--backend",
            "adb",
            "--config",
            str(_config(tmp_path)),
            "--environment-only",
        ],
    )
    # The gate failed, so doctor exits 1 with a ✗ in the section — and still never reached the probe.
    assert r.exit_code == 1
    assert "✗ device attached" in r.stdout

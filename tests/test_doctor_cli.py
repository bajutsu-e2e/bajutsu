"""Tests for the `bajutsu doctor` command itself (bajutsu/cli/commands/doctor.py).

`tests/test_doctor.py` covers the score and the shared screen probe; this file covers the *command*
wrapped around them — the part a CI lane actually keys on:

- the **exit-code contract** (BE-0024): a target carrying the wrong field for its backend is a
  config error and exits 2, while a scenario the backend cannot run is a capability failure and
  exits 1 — a verdict raised *after* the environment section, so both problems surface at once.
- the **informational disclosures** doctor prints without touching a device: the per-scenario
  actuator split (BE-0240) and the xcuitest runner tier (BE-0292).
- the **booted-device counter**, which asks `adb` on Android and `simctl` on iOS.

The device seams (tool presence, the booted count, the screen probe) are the one class this project
mocks — an external dependency — so the command's own logic still runs for real.
"""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest
from typer.testing import CliRunner

from bajutsu.cli import app
from bajutsu.cli.commands.doctor import (
    _host_toolchain,
    _tool_version,
    actuator_resolution_summary,
)
from bajutsu.common.capability.preflight import Check
from bajutsu.common.config import Effective, load_config, resolve

runner = CliRunner()


def _config(tmp_path: Path, body: str) -> Path:
    cfg = tmp_path / "bajutsu.config.yaml"
    cfg.write_text(body, encoding="utf-8")
    return cfg


def _tool_available(monkeypatch: pytest.MonkeyPatch, tool: str) -> None:
    """Report *tool* as present on PATH, so actuator selection resolves regardless of host.

    Tool presence is an external dependency none of the logic under test hinges on; the Linux
    `check` runner has no Android SDK, and without this the command would exit 2 at selection
    before reaching the behavior each test is about.
    """
    import shutil

    real_which = shutil.which
    monkeypatch.setattr(
        shutil,
        "which",
        lambda cmd, *a, **k: f"/usr/bin/{tool}" if cmd == tool else real_which(cmd, *a, **k),
    )


def _forbid_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the screen probe blow up: every test here must exit before reaching a device."""

    def boom(*_a: object, **_k: object) -> object:
        raise AssertionError("this path must not reach the screen probe")

    monkeypatch.setattr("bajutsu.cli.commands.doctor.probe_screen", boom)


# --- the exit-code contract (BE-0024) -------------------------------------------------------------


def test_a_target_carrying_the_wrong_field_for_its_backend_exits_2(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An iOS-shaped target run on Android is a config error — fixable without any tool or device.

    It exits 2, distinct from the 1 a genuine environment failure uses, and is surfaced before any
    doomed probe.
    """
    _forbid_probe(monkeypatch)
    _tool_available(monkeypatch, "adb")
    cfg = _config(tmp_path, "targets:\n  demo:\n    bundleId: com.example.app\n")
    r = runner.invoke(app, ["doctor", "--target", "demo", "--backend", "adb", "--config", str(cfg)])
    assert r.exit_code == 2
    assert "environment:" in r.stdout
    assert "✗ target package" in r.stdout


def test_an_unrunnable_scenario_exits_1_and_names_every_reason(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A scenario using a construct the backend cannot perform fails the capability gate.

    `selectOption` is web-only, so an Android run must be told up front rather than performing
    every earlier step on a real device and failing late. The verdict is exit 1, and it lands
    *after* the environment section so one invocation reports both classes of problem.
    """
    _forbid_probe(monkeypatch)
    _tool_available(monkeypatch, "adb")
    monkeypatch.setattr(
        "bajutsu.common.capability.preflight.doctor_environment_checks",
        lambda *a, **k: [Check("device attached", True, "1 attached")],
    )
    scenario = tmp_path / "select.yaml"
    scenario.write_text(
        "- name: theme\n"
        "  steps:\n"
        "    - selectOption: { sel: { id: nav.theme }, option: midnight }\n",
        encoding="utf-8",
    )
    cfg = _config(tmp_path, "targets:\n  demo:\n    package: com.example.app\n")
    r = runner.invoke(
        app,
        [
            "doctor",
            "--target",
            "demo",
            "--backend",
            "adb",
            "--config",
            str(cfg),
            "--scenario",
            str(scenario),
        ],
    )
    assert r.exit_code == 1
    assert "capability preflight:" in r.stdout
    assert "✘ [theme]" in r.stdout
    assert "selectOption" in r.stdout
    # Printed before the environment section; only the exit-1 verdict is deferred until after it,
    # so one invocation reports both classes of problem.
    assert r.stdout.index("capability preflight:") < r.stdout.index("environment:")
    assert "✓ device attached" in r.stdout


def test_a_runnable_scenario_leaves_the_capability_gate_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_probe(monkeypatch)
    _tool_available(monkeypatch, "adb")
    monkeypatch.setattr(
        "bajutsu.common.capability.preflight.doctor_environment_checks",
        lambda *a, **k: [Check("device attached", True, "1 attached")],
    )
    scenario = tmp_path / "tap.yaml"
    scenario.write_text("- name: tap\n  steps:\n    - tap: { id: ok }\n", encoding="utf-8")
    cfg = _config(tmp_path, "targets:\n  demo:\n    package: com.example.app\n")
    r = runner.invoke(
        app,
        [
            "doctor",
            "--target",
            "demo",
            "--backend",
            "adb",
            "--config",
            str(cfg),
            "--scenario",
            str(scenario),
            "--environment-only",
        ],
    )
    assert r.exit_code == 0
    assert "capability preflight:" not in r.stdout


# --- the booted-device counter --------------------------------------------------------------------


def _counted(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Capture what the gate's `booted_count` callable reports, by calling it from a stub gate."""
    seen: list[int] = []

    def checks(_actuator: str, *, booted_count: Callable[[], int], **_k: object) -> list[Check]:
        seen.append(booted_count())
        return [Check("devices", True, "counted")]

    monkeypatch.setattr("bajutsu.common.capability.preflight.doctor_environment_checks", checks)
    return seen


def test_android_counts_attached_adb_devices(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_probe(monkeypatch)
    _tool_available(monkeypatch, "adb")
    seen = _counted(monkeypatch)
    monkeypatch.setattr(
        "bajutsu.common.backend_cli.adb.booted_serials", lambda: ["emulator-5554", "emulator-5556"]
    )
    monkeypatch.setattr(
        "bajutsu.common.backend_cli.simctl.booted_udids",
        lambda: pytest.fail("Android must not ask simctl"),
    )
    cfg = _config(tmp_path, "targets:\n  demo:\n    package: com.example.app\n")
    r = runner.invoke(
        app,
        [
            "doctor",
            "--target",
            "demo",
            "--backend",
            "adb",
            "--config",
            str(cfg),
            "--environment-only",
        ],
    )
    assert r.exit_code == 0
    assert seen == [2]


def test_ios_counts_booted_simulators(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _forbid_probe(monkeypatch)
    seen = _counted(monkeypatch)
    monkeypatch.setattr("bajutsu.cli.commands.doctor.select_actuator", lambda _b: "xcuitest")
    # No `xcuitest:` block resolves to the bundled tier, whose mismatch note probes the host
    # toolchain — an external dependency this test is not about.
    monkeypatch.setattr("bajutsu.cli.commands.doctor._host_toolchain", lambda: (None, None))
    monkeypatch.setattr("bajutsu.common.backend_cli.simctl.booted_udids", lambda: ["UDID-1"])
    monkeypatch.setattr(
        "bajutsu.common.backend_cli.adb.booted_serials", lambda: pytest.fail("iOS must not ask adb")
    )
    cfg = _config(tmp_path, "targets:\n  demo:\n    bundleId: com.example.app\n")
    r = runner.invoke(
        app,
        ["doctor", "--target", "demo", "--config", str(cfg), "--environment-only"],
    )
    assert r.exit_code == 0
    assert seen == [1]


# --- per-scenario actuator resolution (BE-0240) ---------------------------------------------------


def _eff(tmp_path: Path, **target: str) -> Effective:
    fields = "".join(f"    {k}: {v}\n" for k, v in target.items())
    cfg = load_config(f"targets:\n  x:\n    bundleId: com.x\n{fields}")
    return resolve(cfg, "x").rebased(tmp_path)


def test_a_single_actuator_ladder_discloses_nothing(tmp_path: Path) -> None:
    # Nothing to disclose when there is no choice to make — the common case since BE-0290 left iOS
    # with one actuator.
    assert actuator_resolution_summary(_eff(tmp_path, scenarios="e2e"), ["ios"]) == []


def test_a_missing_scenarios_directory_discloses_nothing(tmp_path: Path) -> None:
    # Configured but not present on disk: informational output must not fabricate a survey.
    assert actuator_resolution_summary(_eff(tmp_path, scenarios="absent"), ["fake", "adb"]) == []


def test_an_empty_scenarios_directory_discloses_nothing(tmp_path: Path) -> None:
    (tmp_path / "e2e").mkdir()
    assert actuator_resolution_summary(_eff(tmp_path, scenarios="e2e"), ["fake", "adb"]) == []


def test_a_multi_actuator_ladder_tallies_each_scenario(tmp_path: Path) -> None:
    """With a real choice to make, doctor says how the target's scenarios split across the ladder."""
    scenarios = tmp_path / "e2e"
    scenarios.mkdir()
    (scenarios / "a.yaml").write_text(
        "- name: one\n  steps:\n    - tap: { id: ok }\n"
        "- name: two\n  steps:\n    - tap: { id: go }\n",
        encoding="utf-8",
    )
    (scenarios / "b.yaml").write_text(
        "- name: three\n  steps:\n    - tap: { id: back }\n", encoding="utf-8"
    )
    lines = actuator_resolution_summary(_eff(tmp_path, scenarios="e2e"), ["fake", "adb"])
    assert lines[0] == "actuator resolution (per scenario, BE-0240):"
    # Every scenario is accounted for exactly once, whichever actuator each resolves to.
    assert sum(int(line.split(":")[1].split()[0]) for line in lines[1:]) == 3


def test_the_command_prints_the_actuator_resolution_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _forbid_probe(monkeypatch)
    _tool_available(monkeypatch, "adb")
    scenarios = tmp_path / "e2e"
    scenarios.mkdir()
    (scenarios / "a.yaml").write_text("- name: one\n  steps:\n    - tap: { id: ok }\n", "utf-8")
    cfg = _config(
        tmp_path,
        "targets:\n  demo:\n    package: com.example.app\n    scenarios: e2e\n",
    )
    r = runner.invoke(
        app,
        [
            "doctor",
            "--target",
            "demo",
            "--backend",
            "fake,adb",
            "--config",
            str(cfg),
            "--environment-only",
        ],
    )
    assert r.exit_code == 0
    assert "actuator resolution (per scenario, BE-0240):" in r.stdout
    assert "1 scenario(s)" in r.stdout


# --- the xcuitest runner tier (BE-0292) -----------------------------------------------------------


def test_the_command_prints_the_xcuitest_runner_tier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An xcuitest target discloses which runner it would use — without building or materializing it."""
    _forbid_probe(monkeypatch)
    monkeypatch.setattr("bajutsu.cli.commands.doctor.select_actuator", lambda _b: "xcuitest")
    monkeypatch.setattr(
        "bajutsu.common.capability.preflight.doctor_environment_checks",
        lambda *a, **k: [Check("simulator booted", True, "1 booted")],
    )
    cfg = _config(
        tmp_path,
        "targets:\n"
        "  demo:\n"
        "    bundleId: com.example.app\n"
        "    xcuitest:\n"
        "      testRunner: /nonexistent/Bajutsu.xctestrun\n",
    )
    r = runner.invoke(
        app,
        ["doctor", "--target", "demo", "--config", str(cfg), "--environment-only"],
    )
    assert r.exit_code == 0
    assert "xcuitest runner:" in r.stdout
    assert "/nonexistent/Bajutsu.xctestrun" in r.stdout


# --- host toolchain probing (BE-0292) -------------------------------------------------------------


def _run_returning(monkeypatch: pytest.MonkeyPatch, stdout: str = "", stderr: str = "") -> None:
    def fake(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess([], 0, stdout, stderr)

    monkeypatch.setattr(subprocess, "run", fake)


def test_tool_version_reads_the_dotted_number(monkeypatch: pytest.MonkeyPatch) -> None:
    _run_returning(monkeypatch, stdout="Xcode 16.0\nBuild version 16A242d\n")
    assert _tool_version(["xcodebuild", "-version"]) == "16.0"


def test_tool_version_reads_stderr_too(monkeypatch: pytest.MonkeyPatch) -> None:
    # `xcodebuild -version` can print to stderr, so a version there still counts.
    _run_returning(monkeypatch, stderr="Xcode 26.6\n")
    assert _tool_version(["xcodebuild", "-version"]) == "26.6"


def test_tool_version_is_none_when_the_banner_carries_no_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A stray number in an error banner must not be read as a version, so only a dotted one counts.
    _run_returning(monkeypatch, stdout="error 7: no developer directory\n")
    assert _tool_version(["xcodebuild", "-version"]) is None


@pytest.mark.parametrize(
    "exc", [OSError("no such tool"), subprocess.CalledProcessError(1, ["xcodebuild"])]
)
def test_tool_version_is_none_when_the_tool_fails(
    monkeypatch: pytest.MonkeyPatch, exc: Exception
) -> None:
    # An absent tool (a non-macOS host) and a non-zero exit both mean "unavailable", never a crash:
    # the bundled-runner mismatch note simply doesn't fire.
    def fake(*_a: object, **_k: object) -> subprocess.CompletedProcess[str]:
        raise exc

    monkeypatch.setattr(subprocess, "run", fake)
    assert _tool_version(["xcodebuild", "-version"]) is None


def test_host_toolchain_reports_xcode_and_the_simulator_sdk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[list[str]] = []

    def fake(cmd: list[str], **_k: object) -> subprocess.CompletedProcess[str]:
        seen.append(cmd)
        return subprocess.CompletedProcess(
            cmd, 0, "26.6" if "xcrun" in cmd[0] else "Xcode 16.0", ""
        )

    monkeypatch.setattr(subprocess, "run", fake)
    assert _host_toolchain() == ("16.0", "26.6")
    assert seen == [
        ["xcodebuild", "-version"],
        ["xcrun", "--sdk", "iphonesimulator", "--show-sdk-version"],
    ]

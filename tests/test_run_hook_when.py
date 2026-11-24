"""`bajutsu run` feeds a run-level `when` the scenarios it selected (BE-0497).

The fake backend stands in for the device; each hook appends a word to a file, so the test reads
which entries ran, and `manifest.json` says which the condition skipped.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from run_cli import invoke
from typer.testing import CliRunner, Result

from bajutsu.cli import app
from bajutsu.common.platform_lifecycle import ReadinessResult

SCENARIOS = """\
scenarios:
  - name: fast
    tags: [smoke]
    steps: []
  - name: offline ${row.mode}
    tags: ["${row.mode}"]
    data: [{mode: offline}]
    steps: []
"""


@pytest.fixture(autouse=True)
def _ready_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    # The fake backend never shows a screen, so the launch wait would run to its timeout.
    monkeypatch.setattr(
        "bajutsu.common.runner.launch.await_ready",
        lambda *_a, **_k: ReadinessResult(True, "skipped", 0.0),
    )


def _project(tmp_path: Path, scenarios: str) -> None:
    ran = tmp_path / "ran.txt"
    script = tmp_path / "hook.sh"
    script.write_text(f"#!/bin/sh\necho $1 >> {ran}\n", encoding="utf-8")
    script.chmod(0o755)
    (tmp_path / "bajutsu.config.yaml").write_text(
        "targets:\n"
        "  site:\n"
        "    platform: web\n"
        "    subject: {url: 'http://127.0.0.1:9/'}\n"
        "    hooks:\n"
        f"      beforeRun: [{{name: up, cmd: '{script} up', when: {{tags: [offline]}}}}]\n"
        f"      afterRun: [{{name: down, cmd: '{script} down', when: {{tags: [offline]}}}}]\n",
        encoding="utf-8",
    )
    (tmp_path / "s.yaml").write_text(scenarios, encoding="utf-8")


def _run_site(tmp_path: Path, *flags: str) -> Result:
    return invoke(
        CliRunner(),
        app,
        [
            *("run", "--target", "site", "--scenario", str(tmp_path / "s.yaml")),
            *("--backend", "fake", "--config", str(tmp_path / "bajutsu.config.yaml")),
            *("--runs-dir", str(tmp_path / "runs"), *flags),
        ],
    )


def _invoke(tmp_path: Path, *flags: str, scenarios: str = SCENARIOS) -> Result:
    _project(tmp_path, scenarios)
    return _run_site(tmp_path, *flags)


def _run(tmp_path: Path, *flags: str) -> tuple[list[str], dict[str, str]]:
    result = _invoke(tmp_path, *flags)
    assert result.exit_code == 0, result.output
    runs, ran = tmp_path / "runs", tmp_path / "ran.txt"
    manifest = json.loads(next(runs.glob("*/manifest.json")).read_text(encoding="utf-8"))
    decisions = {
        e["field"]: e["decision"] for e in manifest["provenance"].get("commandExecutions", [])
    }
    return (ran.read_text().split() if ran.exists() else []), decisions


# The tag sits on a data row, so a match proves the set holds expanded scenarios.
@pytest.mark.parametrize("flags", [(), ("--tag", "offline"), ("--exclude", "smoke")])
def test_a_matching_selection_runs_the_entries(tmp_path: Path, flags: tuple[str, ...]) -> None:
    ran, decisions = _run(tmp_path, *flags)
    assert ran == ["up", "down"]
    assert decisions["hooks.beforeRun[up].cmd"] == "ran"


@pytest.mark.parametrize("flags", [("--tag", "smoke"), ("--exclude", "offline")])
def test_a_selection_without_a_match_skips_them(tmp_path: Path, flags: tuple[str, ...]) -> None:
    ran, decisions = _run(tmp_path, *flags)
    assert ran == []
    assert decisions["hooks.beforeRun[up].cmd"] == "skipped"


READER = """\
scenarios:
  - name: fast
    tags: [smoke]
    steps: []
  - name: ${row.mode}
    tags: ["${row.mode}"]
    data: [{mode: offline}, {mode: online}]
    steps: [{tap: {id: "${hooks.up.port}"}}]
"""


@pytest.mark.parametrize("flags", [(), ("--tag", "smoke")])
def test_an_unmatched_reader_fails_to_load_whatever_the_run_selects(
    tmp_path: Path, flags: tuple[str, ...]
) -> None:
    # One data row matches and the other does not; the second is a defect in the file, so a smoke
    # run that selects neither reports it as well.
    result = _invoke(tmp_path, *flags, scenarios=READER)
    assert result.exit_code == 2
    assert "tags are: online" in result.output and "{tags: [offline]}" in result.output
    assert not (tmp_path / "ran.txt").exists()


def _audit(tmp_path: Path, scenarios: str) -> Result:
    _project(tmp_path, scenarios)
    return CliRunner().invoke(
        app,
        [
            *("audit", str(tmp_path / "s.yaml"), "--target", "site"),
            *("--backend", "fake", "--config", str(tmp_path / "bajutsu.config.yaml")),
            *("--repeat", "2"),
        ],
    )


def test_audit_decides_on_the_scenarios_it_replays(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _audit(tmp_path, SCENARIOS)
    assert result.exit_code == 0, result.output
    assert (tmp_path / "ran.txt").read_text().split() == ["up", "down"]


def test_audit_skips_a_condition_nothing_it_replays_meets(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    result = _audit(tmp_path, "scenarios: [{name: fast, tags: [smoke], steps: []}]\n")
    assert result.exit_code == 0, result.output
    assert not (tmp_path / "ran.txt").exists()


def test_audit_rejects_an_unmatched_reader(tmp_path: Path) -> None:
    result = _audit(tmp_path, READER)
    assert result.exit_code == 2
    assert "tags are: online" in result.output


def _two_targets(tmp_path: Path, scenarios: str, *, web_hooks: str = "{}") -> None:
    """`web` and `admin` over one shared scenario file; `admin` declares a conditional hook."""
    (tmp_path / "bajutsu.config.yaml").write_text(
        "targets:\n"
        "  web:\n"
        "    platform: web\n"
        "    subject: {url: 'http://127.0.0.1:9/'}\n"
        f"    hooks: {web_hooks}\n"
        "  admin:\n"
        "    platform: web\n"
        "    subject: {url: 'http://127.0.0.1:9/'}\n"
        "    hooks:\n"
        "      beforeRun: [{name: seed, cmd: 'true', when: {tags: [adm]}}]\n",
        encoding="utf-8",
    )
    (tmp_path / "s.yaml").write_text(scenarios, encoding="utf-8")


def _run_web(tmp_path: Path, *flags: str) -> Result:
    return invoke(
        CliRunner(),
        app,
        [
            *("run", "--target", "web", "--scenario", str(tmp_path / "s.yaml")),
            *("--backend", "fake", "--config", str(tmp_path / "bajutsu.config.yaml")),
            *("--runs-dir", str(tmp_path / "runs"), *flags),
        ],
    )


_SHARED = """\
scenarios:
  - name: fast
    tags: [smoke]
    steps: []
  - name: admin only
    tags: [{tag}]
    steps: [{{tap: {{id: "${{hooks.seed.id}}"}}}}]
"""


def test_a_deselected_scenario_meant_for_another_target_does_not_fail_the_run(
    tmp_path: Path,
) -> None:
    # It names no target, so under `--target admin` it would run; the file holds no defect.
    _two_targets(tmp_path, _SHARED.format(tag="adm"))
    result = _run_web(tmp_path, "--tag", "smoke")
    assert result.exit_code == 0, result.output


def test_a_deselected_scenario_no_target_could_run_still_fails(tmp_path: Path) -> None:
    _two_targets(tmp_path, _SHARED.format(tag="other"))
    result = _run_web(tmp_path, "--tag", "smoke")
    assert result.exit_code == 2
    assert "hook 'seed'" in result.output


def test_selecting_the_other_target_s_scenario_fails(tmp_path: Path) -> None:
    _two_targets(tmp_path, _SHARED.format(tag="adm"))
    result = _run_web(tmp_path, "--tag", "adm")
    assert result.exit_code == 2
    assert "which this scenario does not drive" in result.output


def test_a_reader_in_the_target_s_own_setup_is_checked(tmp_path: Path) -> None:
    _two_targets(
        tmp_path,
        "scenarios: [{name: fast, tags: [smoke], steps: []}]\n",
        web_hooks=(
            "{beforeScenario: [{name: user, cmd: 'true', when: {tags: [db]}}], "
            "setup: [{tap: {id: '${hooks.user.id}'}}]}"
        ),
    )
    result = _run_web(tmp_path)
    assert result.exit_code == 2
    assert "beforeScenario hook 'user'" in result.output


def test_a_token_in_a_scenario_s_own_command_gets_the_command_error(tmp_path: Path) -> None:
    # A command reads the variable, never the token, so the message names the variable whether or
    # not the row's tags match.
    _project(
        tmp_path,
        "scenarios:\n"
        "  - name: free\n"
        "    tags: [free]\n"
        "    steps: []\n"
        "    hooks:\n"
        "      beforeScenario: [{name: sub, cmd: 'true', when: {tags: [premium]}}]\n"
        "      afterScenario: [{cmd: 'revoke ${hooks.sub.id}', when: {tags: [premium]}}]\n",
    )
    result = _run_site(tmp_path)
    assert result.exit_code == 2
    assert "BAJUTSU_HOOK__sub__id" in result.output

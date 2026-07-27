"""`bajutsu impact` CLI (BE-0321) — the scenario steps a source change is likely to affect.

Read-only and advisory, like `coverage`: it never runs a scenario and never gates CI (exit 0 whatever
the affected set); only a missing config / scenarios dir, an unreadable scenario, or a git / diff read
failure exits 2. Driven against a real `git` repository and real diffs on disk — no mocks.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from typer.testing import CliRunner

from bajutsu.cli import app

runner = CliRunner()

# Strip git's repo-location env vars so a `git -C <tmp>` in these tests targets the tmp repo, not the
# ambient one. They leak in when the suite runs from inside a git operation — e.g. the pre-push hook,
# where GIT_DIR is set — and would otherwise override `-C`. The CLI under test strips them the same way.
_CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _target(tmp_path: Path, scenario: str) -> Path:
    """Write a `demo` target whose scenarios dir holds one scenario file; return the config path."""
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "smoke.yaml").write_text(scenario, encoding="utf-8")
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [login, home]\n",
        encoding="utf-8",
    )
    return config


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=_CLEAN_ENV)


def _init_repo(repo: Path) -> None:
    repo.mkdir(exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "Test")


def test_diff_from_stdin_selects_the_affected_step(tmp_path: Path) -> None:
    config = _target(
        tmp_path, "- name: login\n  steps:\n    - { tap: { id: login.button }, name: tap login }\n"
    )
    diff = (
        "diff --git a/Login.swift b/Login.swift\n--- a/Login.swift\n+++ b/Login.swift\n"
        '@@ -1 +1 @@\n-id = "login.button"\n+id = "login.button2"\n'
    )
    result = runner.invoke(
        app, ["impact", "--target", "demo", "--config", str(config), "--diff", "-"], input=diff
    )
    assert result.exit_code == 0
    assert "login > tap login (step 1)" in result.stdout
    assert "id:login.button" in result.stdout


def test_diff_from_file_json_output(tmp_path: Path) -> None:
    config = _target(
        tmp_path, "- name: login\n  steps:\n    - { tap: { id: login.button }, name: tap login }\n"
    )
    diff_path = tmp_path / "change.diff"
    diff_path.write_text(
        "diff --git a/Login.swift b/Login.swift\n--- a/Login.swift\n+++ b/Login.swift\n"
        '@@ -1 +1 @@\n-id = "login.button"\n+id = "x"\n',
        encoding="utf-8",
    )
    result = runner.invoke(
        app,
        ["impact", "--target", "demo", "--config", str(config), "--diff", str(diff_path), "--json"],
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["complete"] is True
    (affected,) = data["affected"]
    assert affected["step"] == {"scenario": "login", "index": 1, "label": "tap login"}
    assert affected["reasons"] == [{"kind": "id", "value": "login.button"}]


def test_unattributable_change_is_flagged_incomplete_but_exits_0(tmp_path: Path) -> None:
    config = _target(tmp_path, "- name: x\n  steps:\n    - tap: { id: home.start }\n")
    diff = (
        "diff --git a/Logic.swift b/Logic.swift\n--- a/Logic.swift\n+++ b/Logic.swift\n"
        "@@ -1 +1 @@\n-let x = 1\n+let x = 2\n"
    )
    result = runner.invoke(
        app, ["impact", "--target", "demo", "--config", str(config), "--diff", "-"], input=diff
    )
    assert result.exit_code == 0  # read-only: never gates, even when a full run is warranted
    assert "incomplete" in result.stdout and "Logic.swift" in result.stdout


def test_range_diffs_a_real_git_repository(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _init_repo(repo)
    source = repo / "Login.swift"
    source.write_text('let id = "login.button"\n', encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    source.write_text('let id = "login.button2"\n', encoding="utf-8")  # working-tree change

    config = _target(
        tmp_path, "- name: login\n  steps:\n    - { tap: { id: login.button }, name: tap login }\n"
    )
    result = runner.invoke(
        app,
        [
            "impact",
            "--target",
            "demo",
            "--config",
            str(config),
            "--repo",
            str(repo),
            "--range",
            "HEAD",
        ],
    )
    assert result.exit_code == 0
    assert "login > tap login" in result.stdout


def test_untracked_new_file_is_folded_into_the_working_tree_analysis(tmp_path: Path) -> None:
    # `git diff HEAD` misses an untracked file; the command reads it so its referenced ids still match.
    repo = tmp_path / "app"
    _init_repo(repo)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    (repo / "NewScreen.swift").write_text(
        'let id = "login.button"\n', encoding="utf-8"
    )  # untracked

    config = _target(
        tmp_path, "- name: login\n  steps:\n    - { tap: { id: login.button }, name: tap login }\n"
    )
    result = runner.invoke(
        app, ["impact", "--target", "demo", "--config", str(config), "--repo", str(repo)]
    )
    assert result.exit_code == 0
    assert "login > tap login" in result.stdout  # the untracked file's id selected the step


def test_untracked_unmatched_file_makes_the_report_incomplete(tmp_path: Path) -> None:
    repo = tmp_path / "app"
    _init_repo(repo)
    (repo / "seed.txt").write_text("seed\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "initial")
    (repo / "Helper.swift").write_text("let x = compute()\n", encoding="utf-8")  # references no id

    config = _target(tmp_path, "- name: x\n  steps:\n    - tap: { id: home.start }\n")
    result = runner.invoke(
        app, ["impact", "--target", "demo", "--config", str(config), "--repo", str(repo), "--json"]
    )
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert data["complete"] is False and "Helper.swift" in data["unattributable"]


def test_malformed_scenario_exits_2(tmp_path: Path) -> None:
    scn_dir = tmp_path / "scenarios"
    scn_dir.mkdir()
    (scn_dir / "bad.yaml").write_text("- name: x\n  steps: not-a-list\n", encoding="utf-8")
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n"
        f"    scenarios: {scn_dir}\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["impact", "--target", "demo", "--config", str(config), "--diff", "-"], input=""
    )
    assert result.exit_code == 2
    assert "failed to load scenarios" in result.stdout


def test_missing_scenarios_dir_exits_2(tmp_path: Path) -> None:
    config = tmp_path / "bajutsu.config.yaml"
    config.write_text(
        "targets:\n  demo:\n    bundleId: com.example.demo\n    idNamespaces: [home]\n",
        encoding="utf-8",
    )
    result = runner.invoke(
        app, ["impact", "--target", "demo", "--config", str(config), "--diff", "-"], input=""
    )
    assert result.exit_code == 2


def test_unreadable_diff_file_exits_2(tmp_path: Path) -> None:
    config = _target(tmp_path, "- name: x\n  steps:\n    - tap: { id: home.start }\n")
    result = runner.invoke(
        app,
        [
            "impact",
            "--target",
            "demo",
            "--config",
            str(config),
            "--diff",
            str(tmp_path / "nope.diff"),
        ],
    )
    assert result.exit_code == 2
    assert "failed to read diff" in result.stdout


def test_git_failure_exits_2(tmp_path: Path) -> None:
    # A directory that is not a git repository makes `git diff` fail — surfaced as exit 2, not a crash.
    config = _target(tmp_path, "- name: x\n  steps:\n    - tap: { id: home.start }\n")
    not_repo = tmp_path / "plain"
    not_repo.mkdir()
    result = runner.invoke(
        app,
        ["impact", "--target", "demo", "--config", str(config), "--repo", str(not_repo)],
    )
    assert result.exit_code == 2
    assert "git diff failed" in result.stdout

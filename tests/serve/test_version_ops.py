"""Tests for the server-identity operations (BE-0272).

Operations-level: no HTTP, no Simulator. Git behaviour is pinned to a throwaway repo built in
`tmp_path` (not the ambient checkout), so commit / branch / dirty are deterministic.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

import bajutsu
from bajutsu.serve.operations import version as version_ops

# Under a git hook (the pre-push gate), git exports GIT_DIR / GIT_INDEX_FILE into the child env,
# which would redirect these `cwd`-scoped calls at the outer repo. Strip them so the temp repo wins.
_CLEAN_GIT_ENV = {k: v for k, v in os.environ.items() if k not in version_ops._GIT_LOCATION_ENV}


def _git_in(root: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ("git", *args), cwd=root, env=_CLEAN_GIT_ENV, check=True, capture_output=True
    )


def _init_repo(root: Path, branch: str = "work") -> None:
    """A minimal, clean Git checkout at *root* on *branch* with one commit."""
    _git_in(root, "init", "-b", branch)
    _git_in(root, "config", "user.email", "t@example.com")
    _git_in(root, "config", "user.name", "t")
    (root / "f.txt").write_text("hi", encoding="utf-8")
    _git_in(root, "add", "f.txt")
    _git_in(root, "commit", "-m", "init")


def test_server_version_reports_the_package_version() -> None:
    payload, status = version_ops.server_version()
    assert status == 200
    assert payload == {"version": bajutsu.__version__}


def test_server_checkout_reads_a_clean_git_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path, branch="work")
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)
    payload, status = version_ops.server_checkout()
    assert status == 200
    assert payload["branch"] == "work"
    assert isinstance(payload["commit"], str) and payload["commit"]  # a short SHA
    assert payload["dirty"] is False


def test_server_checkout_flags_a_dirty_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    (tmp_path / "untracked.txt").write_text("edit", encoding="utf-8")  # uncommitted change
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)
    payload, _ = version_ops.server_checkout()
    assert payload["dirty"] is True


def test_server_checkout_reports_no_branch_on_a_detached_head(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _init_repo(tmp_path)
    sha = _git_in(tmp_path, "rev-parse", "HEAD").stdout.decode().strip()
    _git_in(tmp_path, "checkout", sha)
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)
    payload, _ = version_ops.server_checkout()
    assert payload["commit"]  # still a commit
    assert payload["branch"] is None  # "HEAD" is not reported as a branch


def test_server_checkout_outside_a_checkout_is_all_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)  # a plain dir, no .git
    payload, status = version_ops.server_checkout()
    assert status == 200
    assert payload == {"commit": None, "branch": None, "dirty": False}

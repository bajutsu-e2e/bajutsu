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
    assert payload["source"] == "git"


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


def test_server_checkout_reports_dirty_none_when_status_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # rev-parse succeeds but `status --porcelain` fails on its own (e.g. a stale index.lock):
    # dirty must be None (unknown), not False — reporting "clean" would hide the failed read.
    def fake_git(*args: str) -> str | None:
        return None if args[0] == "status" else "abc1234" if args[1] == "--short" else "work"

    monkeypatch.setattr(version_ops, "_git", fake_git)
    payload, status = version_ops.server_checkout()
    assert status == 200
    assert payload == {"commit": "abc1234", "branch": "work", "dirty": None, "source": "git"}


def test_server_checkout_outside_a_checkout_is_all_null(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)  # a plain dir, no .git
    monkeypatch.delenv("BAJUTSU_BUILD_COMMIT", raising=False)  # no build-arg fallback either
    payload, status = version_ops.server_checkout()
    assert status == 200
    assert payload == {"commit": None, "branch": None, "dirty": False, "source": None}


def test_server_checkout_falls_back_to_the_build_arg_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No .git in the anchor (a self-hosted Docker image), but the build embedded a commit (BE-0277):
    # report it as the commit with source "build-arg" and no branch / dirty concept.
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)
    monkeypatch.setenv("BAJUTSU_BUILD_COMMIT", "deadbeef")
    payload, status = version_ops.server_checkout()
    assert status == 200
    assert payload == {
        "commit": "deadbeef",
        "branch": None,
        "dirty": None,
        "source": "build-arg",
    }


def test_server_checkout_ignores_a_blank_build_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # An unset build arg embeds as empty (ENV BAJUTSU_BUILD_COMMIT=""): whitespace-only is no commit.
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)
    monkeypatch.setenv("BAJUTSU_BUILD_COMMIT", "  ")
    payload, _ = version_ops.server_checkout()
    assert payload == {"commit": None, "branch": None, "dirty": False, "source": None}


def test_git_detection_wins_over_the_build_arg(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A live checkout stays the primary source even when a build arg is also set: the build arg only
    # ever fills the gap Git detection leaves (BE-0277's "Git detection stays first").
    _init_repo(tmp_path, branch="work")
    monkeypatch.setattr(version_ops, "_REPO_ANCHOR", tmp_path)
    monkeypatch.setenv("BAJUTSU_BUILD_COMMIT", "deadbeef")
    payload, _ = version_ops.server_checkout()
    assert payload["source"] == "git"
    assert payload["commit"] != "deadbeef"
    assert payload["branch"] == "work"

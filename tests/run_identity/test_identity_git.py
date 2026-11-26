"""The Git identity reads behind `appSource` and `toolSource` (BE-0506), on throwaway repos."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bajutsu.common.run_identity import git as identity_git
from bajutsu.common.run_identity.git import GIT_LOCATION_ENV, source_identity, tool_source


def _git_in(root: Path, *args: str) -> str:
    env = {k: v for k, v in os.environ.items() if k not in GIT_LOCATION_ENV}
    return subprocess.run(
        ("git", *args), cwd=root, env=env, check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(root: Path, tracked: str = "f.txt") -> str:
    root.mkdir(parents=True, exist_ok=True)
    _git_in(root, "init", "-b", "work")
    _git_in(root, "config", "user.email", "t@example.com")
    _git_in(root, "config", "user.name", "t")
    (root / tracked).parent.mkdir(parents=True, exist_ok=True)
    (root / tracked).write_text("hi", encoding="utf-8")
    _git_in(root, "add", tracked)
    _git_in(root, "commit", "-m", "init")
    return _git_in(root, "rev-parse", "HEAD")


@pytest.fixture(autouse=True)
def _no_build_commit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_BUILD_COMMIT", raising=False)


def test_source_identity_reads_the_full_commit_of_a_clean_tree(tmp_path: Path) -> None:
    head = _repo(tmp_path)
    assert source_identity(tmp_path, trusted=True) == {"commit": head, "uncommitted": False}


def test_source_identity_flags_uncommitted_changes(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / "new.txt").write_text("x", encoding="utf-8")
    assert source_identity(tmp_path, trusted=True)["uncommitted"] is True


@pytest.mark.parametrize("sub", [".", "missing"])
def test_source_identity_outside_a_repository_is_a_gap(tmp_path: Path, sub: str) -> None:
    assert source_identity(tmp_path / sub, trusted=True) == {
        "commit": None,
        "uncommitted": None,
        "unavailable": {"commit": "not-a-git-repo", "uncommitted": "not-a-git-repo"},
    }


def test_source_identity_without_git_is_a_gap(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(identity_git, "run_git", lambda *_a, **_k: None)
    assert source_identity(tmp_path, trusted=True)["unavailable"] == {
        "commit": "git-unavailable",
        "uncommitted": "git-unavailable",
    }


@pytest.mark.parametrize(
    ("status", "reason"),
    [(None, "git-unavailable"), ((128, ""), "status-read-failed")],
)
def test_a_failed_status_read_keeps_the_commit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: tuple[int, str] | None,
    reason: str,
) -> None:
    head = _repo(tmp_path)
    real = identity_git.run_git

    def fake(cwd: Path, *args: str, **kw: float) -> subprocess.CompletedProcess[str] | None:
        if args[0] != "status":
            return real(cwd, *args, **kw)
        return None if status is None else subprocess.CompletedProcess(args, status[0], status[1])

    monkeypatch.setattr(identity_git, "run_git", fake)
    assert source_identity(tmp_path, trusted=True) == {
        "commit": head,
        "uncommitted": None,
        "unavailable": {"uncommitted": reason},
    }


def test_git_reads_do_not_run_the_repository_fsmonitor(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    _repo(repo)
    marker = tmp_path / "ran"
    hook = tmp_path / "hook.sh"
    hook.write_text(f"#!/bin/sh\ntouch '{marker}'\n", encoding="utf-8")
    hook.chmod(0o755)
    _git_in(repo, "config", "core.fsmonitor", str(hook))
    source_identity(repo, trusted=True)
    assert not marker.exists()


def test_tool_source_reads_a_checkout_that_tracks_the_package(tmp_path: Path) -> None:
    head = _repo(tmp_path, tracked="pkg/__init__.py")
    assert tool_source(tmp_path / "pkg") == {
        "commit": head,
        "uncommitted": False,
        "source": "checkout",
    }


def test_a_package_inside_an_unrelated_repository_has_no_tool_source(tmp_path: Path) -> None:
    _repo(tmp_path)
    package = tmp_path / ".venv" / "site-packages" / "pkg"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    assert tool_source(package) is None


def test_tool_source_falls_back_to_the_build_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("BAJUTSU_BUILD_COMMIT", "abc123")
    assert tool_source(tmp_path) == {"commit": "abc123", "source": "build-arg"}


def test_a_checkout_wins_over_the_build_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    head = _repo(tmp_path, tracked="pkg/__init__.py")
    monkeypatch.setenv("BAJUTSU_BUILD_COMMIT", "abc123")
    stamp = tool_source(tmp_path / "pkg")
    assert stamp is not None
    assert (stamp["commit"], stamp["source"]) == (head, "checkout")


def test_tool_source_without_a_commit_yet_is_omitted(tmp_path: Path) -> None:
    _git_in(tmp_path, "init")
    (tmp_path / "__init__.py").write_text("", encoding="utf-8")
    _git_in(tmp_path, "add", "__init__.py")
    assert tool_source(tmp_path) is None


def test_an_ambient_git_dir_does_not_redirect_the_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A run started from a git hook inherits GIT_DIR.
    head = _repo(tmp_path / "app")
    _repo(tmp_path / "other", tracked="g.txt")
    monkeypatch.setenv("GIT_DIR", str(tmp_path / "other" / ".git"))
    assert source_identity(tmp_path / "app", trusted=True)["commit"] == head


def test_an_ignored_directory_inside_a_repository_is_not_that_repository(tmp_path: Path) -> None:
    _repo(tmp_path)
    (tmp_path / ".gitignore").write_text("vendor/\n", encoding="utf-8")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "src.swift").write_text("x", encoding="utf-8")
    assert source_identity(tmp_path / "vendor", trusted=True)["commit"] is None


def test_a_tracked_subdirectory_reads_its_repository(tmp_path: Path) -> None:
    head = _repo(tmp_path, tracked="apps/shop/main.swift")
    assert source_identity(tmp_path / "apps" / "shop", trusted=True)["commit"] == head


def test_a_setting_cannot_hide_untracked_files(tmp_path: Path) -> None:
    _repo(tmp_path)
    _git_in(tmp_path, "config", "status.showUntrackedFiles", "no")
    (tmp_path / "new.txt").write_text("x", encoding="utf-8")
    assert source_identity(tmp_path, trusted=True)["uncommitted"] is True


def test_an_untrusted_tree_never_has_status_run_on_it(tmp_path: Path) -> None:
    # An uploaded bundle can carry a `.git` whose clean filter `status` would execute.
    repo, marker = tmp_path / "repo", tmp_path / "ran"
    head = _repo(repo)
    _git_in(repo, "config", "filter.mark.clean", f"touch '{marker}'; cat")
    (repo / ".gitattributes").write_text("* filter=mark\n", encoding="utf-8")
    os.utime(repo / "f.txt", (1, 1))
    assert source_identity(repo, trusted=False) == {
        "commit": head,
        "uncommitted": None,
        "unavailable": {"uncommitted": "status-read-failed"},
    }
    assert not marker.exists()

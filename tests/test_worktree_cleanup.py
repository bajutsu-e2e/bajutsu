"""Tests for scripts/worktree_cleanup.sh — the guard behind the `cleanup` skill.

The incident this script exists for (BE-XXXX): a branch created off ``origin/main`` that has not
committed anything yet *is* ``origin/main``, so ``git branch --merged origin/main`` lists it and
``git branch -d`` deletes it. The old skill read that as "work is finished" when it actually meant
"work has not started", and removed a live session's worktree. The first test below is that exact
shape; the rest pin the other guards and the path→branch mapping that made the confirmation prompt
describe the wrong topic.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "worktree_cleanup.sh"


def _clean_env(**overrides: str) -> dict[str, str]:
    """The ambient environment with git's own variables stripped.

    Run from a git hook (the tracked pre-push runs `make check`), git exports GIT_DIR and
    GIT_INDEX_FILE, which would point every command below at the real repository instead of the
    throwaway one built for the test.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | overrides


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
        env=_clean_env(),
    ).stdout


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A throwaway clone with an origin, carrying its own copy of the script under test.

    The script resolves the repository from its own location, so a copy inside the fixture is what
    points it at this repo instead of the real checkout.
    """
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(origin)], check=True, capture_output=True
    )

    work = tmp_path / "repo"
    subprocess.run(
        ["git", "clone", str(origin), str(work)], check=True, capture_output=True, env=_clean_env()
    )
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "T")
    (work / "README.md").write_text("hello\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "-u", "origin", "main")

    (work / "scripts").mkdir()
    shutil.copy(SCRIPT, work / "scripts" / SCRIPT.name)
    return work


def _fake_gh(tmp_path: Path, merged_count: int) -> dict[str, str]:
    """PATH with a `gh` that reports this many merged pull requests for any branch."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\necho {merged_count}\n")
    gh.chmod(0o755)
    return _clean_env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _age(path: Path) -> None:
    """Backdate the worktree's files so the recent-activity guard is not what fires."""
    old = time.time() - 86_400
    for p in path.rglob("*"):
        if ".git" not in p.parts:
            os.utime(p, (old, old), follow_symlinks=False)


def _fake_gh_raw(tmp_path: Path, body: str) -> dict[str, str]:
    """PATH with a `gh` running this script body — for answers that are not a plain count."""
    bin_dir = tmp_path / "fakebin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(f"#!/bin/sh\n{body}\n")
    gh.chmod(0o755)
    return _clean_env(PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _run(
    repo: Path, *args: str, env: dict[str, str] | None = None, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(repo / "scripts" / SCRIPT.name), *args],
        capture_output=True,
        text=True,
        env=env if env is not None else _clean_env(),
        cwd=str(cwd) if cwd is not None else None,
    )


def test_refuses_a_branch_that_never_delivered_work(repo: Path, tmp_path: Path) -> None:
    """The incident: a fresh branch sitting exactly at origin/main, with a live session on it."""
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/implement-be-0390", str(tree), "origin/main")
    _age(tree)

    assert _git(repo, "branch", "--merged", "origin/main").count("claude/implement-be-0390") == 1, (
        "precondition: git itself considers this branch merged"
    )

    result = _run(repo, "--remove", str(tree), env=_fake_gh(tmp_path, 0))

    assert result.returncode == 1
    assert "no merged pull request" in result.stderr
    assert tree.exists()
    assert "claude/implement-be-0390" in _git(repo, "branch", "--list")


def test_removes_a_branch_whose_pull_request_merged(repo: Path, tmp_path: Path) -> None:
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/done", str(tree), "origin/main")
    _age(tree)

    result = _run(repo, "--remove", str(tree), env=_fake_gh(tmp_path, 1))

    assert result.returncode == 0, result.stderr
    assert not tree.exists()
    assert "claude/done" not in _git(repo, "branch", "--list")


def test_refuses_uncommitted_work(repo: Path, tmp_path: Path) -> None:
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/dirty", str(tree), "origin/main")
    _age(tree)
    (tree / "scratch.txt").write_text("in progress\n")
    _age(tree)

    result = _run(repo, "--remove", str(tree), env=_fake_gh(tmp_path, 1))

    assert result.returncode == 1
    assert "uncommitted or untracked" in result.stderr
    assert tree.exists()


def test_refuses_commits_that_are_not_on_origin_main(repo: Path, tmp_path: Path) -> None:
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/ahead", str(tree), "origin/main")
    (tree / "new.md").write_text("work\n")
    _git(tree, "add", "new.md")
    _git(tree, "commit", "-m", "work")
    _age(tree)

    result = _run(repo, "--remove", str(tree), env=_fake_gh(tmp_path, 1))

    assert result.returncode == 1
    assert "not on origin/main" in result.stderr
    assert tree.exists()


def test_refuses_recently_touched_worktree(repo: Path, tmp_path: Path) -> None:
    """A live session can hold a clean tree between edits, so mtime is the signal that it is there."""
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/live", str(tree), "origin/main")

    result = _run(repo, "--remove", str(tree), env=_fake_gh(tmp_path, 1))

    assert result.returncode == 1
    assert "still using it" in result.stderr
    assert tree.exists()


def test_refuses_a_locked_worktree(repo: Path, tmp_path: Path) -> None:
    """Guard 2: a host's lock alone must refuse, even when every other guard would pass."""
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/locked", str(tree), "origin/main")
    _age(tree)
    _git(repo, "worktree", "lock", "--reason", "session 4711", str(tree))

    result = _run(repo, "--remove", str(tree), env=_fake_gh(tmp_path, 1))

    assert result.returncode == 1
    assert "worktree is locked (session 4711)" in result.stderr
    assert tree.exists()


def test_refuses_the_main_checkout(repo: Path, tmp_path: Path) -> None:
    result = _run(repo, "--remove", str(repo), env=_fake_gh(tmp_path, 1))

    assert result.returncode == 1
    assert "main checkout" in result.stderr


def test_refuses_the_worktree_the_caller_is_standing_in(repo: Path, tmp_path: Path) -> None:
    """Guard 1 follows the caller, not the copy of the script that happens to be invoked.

    An absolute-path invocation runs the main checkout's copy, so resolving "self" after the
    script's own `cd` would name that checkout and leave the session's worktree unprotected.
    """
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/self", str(tree), "origin/main")
    _age(tree)

    result = _run(repo, "--remove", str(tree), env=_fake_gh(tmp_path, 1), cwd=tree)

    assert result.returncode == 1
    assert "cleanup is running in" in result.stderr
    assert tree.exists()


def test_refuses_an_unusable_staleness_window(repo: Path, tmp_path: Path) -> None:
    """A window `find -mmin` cannot parse would silently delete guard 5 rather than widen it."""
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/typo", str(tree), "origin/main")
    _age(tree)
    env = _fake_gh(tmp_path, 1) | {"BAJUTSU_CLEANUP_STALE_MINUTES": "180m"}

    result = _run(repo, "--remove", str(tree), env=env)

    assert result.returncode == 1
    assert "whole number of minutes" in result.stderr
    assert tree.exists()


def test_refuses_when_gh_answers_with_something_other_than_a_count(
    repo: Path, tmp_path: Path
) -> None:
    """Fail closed: only a positive count is a delivery, so noise-then-failure must refuse too."""
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/noisy", str(tree), "origin/main")
    _age(tree)
    # A wrapper or shim that writes to stdout before failing: the status is caught, but its output
    # is already in the answer.
    env = _fake_gh_raw(tmp_path, 'echo "gh: please re-authenticate"\nexit 1')

    result = _run(repo, "--remove", str(tree), env=env)

    assert result.returncode == 1
    assert "could not query pull requests" in result.stderr
    assert tree.exists()


def test_refuses_when_gh_cannot_answer(repo: Path, tmp_path: Path) -> None:
    """Fail closed: without a way to confirm delivery, the guard refuses rather than assumes."""
    tree = repo / "wt"
    _git(repo, "worktree", "add", "-b", "claude/unknown", str(tree), "origin/main")
    _age(tree)
    # A PATH carrying everything the script needs except gh — the shape of a machine where gh is
    # simply not installed.
    without_gh = tmp_path / "nogh"
    without_gh.mkdir()
    for tool in ("bash", "git", "find", "head", "dirname"):
        resolved = shutil.which(tool)
        assert resolved, f"test precondition: {tool} must be on PATH"
        (without_gh / tool).symlink_to(resolved)

    result = _run(repo, "--remove", str(tree), env=_clean_env(PATH=str(without_gh)))

    assert result.returncode == 1
    assert "gh is unavailable" in result.stderr
    assert tree.exists()


def test_audit_reports_the_branch_not_the_directory_name(repo: Path, tmp_path: Path) -> None:
    """A worktree directory is often named after a different topic than the branch it holds.

    Reading the branch off the path is what let a confirmation prompt name work that had long since
    merged while the worktree actually held an untouched branch.
    """
    tree = repo / "be-0369-implementation"
    _git(repo, "worktree", "add", "-b", "claude/implement-be-0390", str(tree), "origin/main")
    _age(tree)

    result = _run(repo, env=_fake_gh(tmp_path, 0))

    assert result.returncode == 0, result.stderr
    assert "branch: claude/implement-be-0390" in result.stdout
    assert "verdict: KEEP" in result.stdout

"""Tests for scripts/install-no-verify-guard.sh and the `git()` function it installs.

The installed function's whole body lives inside a quoted heredoc, so shellcheck (which lints
this script via the `SHELL_SCRIPTS` Makefile list) never sees it, and a push blocked by mistake
looks identical to a push that never happened — the failure mode is a push that silently
*succeeds*, which no one investigates. These tests drive the real installer and the real function
it writes, against temporary repositories with a real `origin` remote, so a regression in either
one shows up as a wrong exit code or a commit that did (or did not) reach `origin`.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "install-no-verify-guard.sh"
MARKER_BEGIN = "# >>> bajutsu no-verify guard >>>"


def _clean_env(**overrides: str) -> dict[str, str]:
    """The ambient environment with git's own variables stripped.

    Run from the pre-push hook (which runs `make check`, hence this test), git exports GIT_DIR
    and GIT_INDEX_FILE into everything it runs, which would point every command below at the real
    repository instead of the throwaway one built for the test.
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


def _install(rc_file: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        env=_clean_env(BAJUTSU_GUARD_RC_FILE=str(rc_file)),
        check=False,
    )


@pytest.fixture
def rc_file(tmp_path: Path) -> Path:
    """A fresh, not-yet-existing rc file, installed into once by every test that needs it."""
    rc = tmp_path / "rc"
    result = _install(rc)
    assert result.returncode == 0, result.stderr
    assert MARKER_BEGIN in rc.read_text(encoding="utf-8")
    return rc


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A clone with a real `origin`, so a push either does or does not reach it — no mocking."""
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
    return work


def _origin_subject(repo: Path) -> str | None:
    """The subject of `origin`'s `main`, or ``None`` if nothing has ever reached it.

    `origin` starts as an empty bare repository (``git init --bare`` creates no branch until the
    first push), so a plain `git log main` there fails until a push actually lands.
    """
    origin = repo.parent / "origin.git"
    result = subprocess.run(
        ["git", "-C", str(origin), "log", "-1", "--format=%s", "main"],
        capture_output=True,
        text=True,
        env=_clean_env(),
        check=False,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def _mark_guarded(repo: Path) -> None:
    """Add the marker the installed function looks for at the repo's toplevel."""
    hooks_dir = repo / ".githooks"
    hooks_dir.mkdir(exist_ok=True)
    (hooks_dir / "no-verify-guard-marker").write_text("marker\n", encoding="utf-8")


def _run_guarded_git(repo: Path, rc_file: Path, *git_args: str) -> subprocess.CompletedProcess[str]:
    """Run ``git <git_args>`` from inside ``repo``, in a shell carrying the installed function."""
    script = f'source "{rc_file}"\ncd "{repo}" || exit 99\ngit {" ".join(git_args)}\n'
    return subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, env=_clean_env(), check=False
    )


def test_no_verify_is_refused_in_a_guarded_repo(repo: Path, rc_file: Path) -> None:
    _mark_guarded(repo)

    result = _run_guarded_git(repo, rc_file, "push", "origin", "main", "--no-verify")

    assert result.returncode == 1
    assert "forbidden in this repository" in result.stderr
    assert _origin_subject(repo) is None, "the commit must never have reached origin"


def test_no_verify_passes_through_outside_a_guarded_repo(repo: Path, rc_file: Path) -> None:
    """No `.githooks/no-verify-guard-marker`: this is someone's unrelated repository."""
    result = _run_guarded_git(repo, rc_file, "push", "origin", "main", "--no-verify")

    assert result.returncode == 0, result.stderr
    assert _origin_subject(repo) == "init"


def test_an_ordinary_push_is_not_blocked_in_a_guarded_repo(repo: Path, rc_file: Path) -> None:
    _mark_guarded(repo)

    result = _run_guarded_git(repo, rc_file, "push", "origin", "main")

    assert result.returncode == 0, result.stderr
    assert _origin_subject(repo) == "init"


def test_a_global_option_before_push_does_not_hide_no_verify(repo: Path, rc_file: Path) -> None:
    """`git -c user.email=x push --no-verify`: the subcommand is not $1, but the guard must still see it."""
    _mark_guarded(repo)

    result = _run_guarded_git(
        repo, rc_file, "-c", "user.email=x@example.com", "push", "--no-verify", "origin", "main"
    )

    assert result.returncode == 1
    assert "forbidden in this repository" in result.stderr


def test_installing_twice_does_not_duplicate_the_block(tmp_path: Path) -> None:
    rc = tmp_path / "rc"
    first = _install(rc)
    second = _install(rc)

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    assert "already installed" in second.stdout
    assert rc.read_text(encoding="utf-8").count(MARKER_BEGIN) == 1

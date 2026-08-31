"""Tests for scripts/check_worktree_config.sh — the guard behind issue #1803.

The incident: `core.bare = true` and a `core.worktree` pointing at one session's worktree were
present in the *shared* `.git/config` with `extensions.worktreeConfig` enabled, which strips git's
built-in exception confining those two to the main working tree. Every worktree then resolved to
that one directory — `git add` reported success without changing anything, `git commit --amend`
dropped a file, and `git checkout -- <path>` wrote into a neighbouring session's tree — while
`--git-dir` still answered locally, so nothing looked wrong.

The two tests that carry the most weight here are the negative ones: the same settings in the
*per-worktree* config, where they belong, must not fail, and neither must a shared value while the
extension is off, which is git's own documented exception. A guard that fired on either would be
turned off within a day.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_worktree_config.sh"


def _clean_env(**overrides: str) -> dict[str, str]:
    """The ambient environment with git's own variables stripped.

    Run from a git hook (the tracked pre-push runs `make check`), git exports GIT_DIR and friends,
    which would point the setup commands below at the real repository instead of the throwaway one.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | overrides


def _checkout(root: Path) -> Path:
    """A throwaway checkout carrying its own copy of the script under test.

    The script resolves the repository from its own location (`dirname $0/..`), so the copy inside
    the fixture is what aims it at this tree rather than at the real checkout.
    """
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", str(root)], check=True, capture_output=True, env=_clean_env()
    )
    (root / "scripts").mkdir()
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)
    return root


def _shared_config(root: Path) -> Path:
    return root / ".git" / "config"


def _set_shared(root: Path, key: str, value: str) -> None:
    """Write straight to the shared config file.

    `--file` rather than a plain `git config`, because `core.bare = true` makes every discovering
    git command in that repository fail — including the one that would have set it.
    """
    subprocess.run(
        ["git", "config", "--file", str(_shared_config(root)), key, value],
        check=True,
        capture_output=True,
        env=_clean_env(),
    )


def _run(root: Path, **env_overrides: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(root / "scripts" / SCRIPT.name)],
        capture_output=True,
        text=True,
        # The exit code is the verdict under test, so a non-zero one must reach the assertion
        # rather than raise.
        check=False,
        env=_clean_env(**env_overrides),
    )


def test_a_clean_checkout_passes(tmp_path: Path) -> None:
    """Neither setting present: the guard has nothing to say and must stay out of the way."""
    root = _checkout(tmp_path / "repo")

    assert _run(root).returncode == 0


def test_a_shared_core_worktree_fails(tmp_path: Path) -> None:
    """The setting git-worktree(1) says should never be shared, in the shared config.

    The path deliberately does not exist, which is the shape the incident left behind: a worktree
    that has since been removed. git resolves `core.worktree` during repository setup, before it
    runs the command asked of it, so in this state *every* git call — a plain `git config --file`
    read included — dies with "Invalid path". A guard that read that as "no checkout here" would
    pass the one repository it exists to fail.
    """
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", "true")
    _set_shared(root, "core.worktree", "/somewhere/else")

    result = _run(root)

    assert result.returncode == 1
    assert "core.worktree = /somewhere/else" in result.stderr
    # The message has to name the file to edit and the command that edits it, or it just says
    # "something is wrong" to someone who has never met this failure.
    assert str(_shared_config(root)) in result.stderr
    assert "git config --unset core.worktree" in result.stderr


def test_a_shared_bare_true_fails(tmp_path: Path) -> None:
    """`core.bare = true` — the half that surfaces once a shared `core.worktree` is cleared."""
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", "true")
    _set_shared(root, "core.bare", "true")

    result = _run(root)

    assert result.returncode == 1
    assert "core.bare = true" in result.stderr
    assert "git config --worktree core.bare false" in result.stderr
    # Nothing to unset here, so that line must not be offered.
    assert "git config --unset core.worktree" not in result.stderr


def test_a_shared_bare_false_passes(tmp_path: Path) -> None:
    """What an ordinary clone carries. git-worktree(1) objects only to the `true` value."""
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", "true")
    _set_shared(root, "core.bare", "false")

    assert _run(root).returncode == 0


def test_the_settings_pass_in_the_per_worktree_config(tmp_path: Path) -> None:
    """Where both settings belong, so a correctly repaired repository must not be flagged.

    This is what the `--file` read buys: a plain `git config --get` folds the per-worktree file in
    and would report every properly configured worktree as broken.
    """
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", "true")
    subprocess.run(
        ["git", "config", "--worktree", "core.bare", "false"],
        cwd=root,
        check=True,
        capture_output=True,
        env=_clean_env(),
    )
    subprocess.run(
        ["git", "config", "--worktree", "core.worktree", str(root)],
        cwd=root,
        check=True,
        capture_output=True,
        env=_clean_env(),
    )

    assert _run(root).returncode == 0


def test_a_shared_setting_passes_while_the_extension_is_off(tmp_path: Path) -> None:
    """git's own exception: without the extension, a shared value binds the main worktree only.

    Linked worktrees ignore it, so there is no cross-worktree misdirection to warn about and firing
    here would fail repositories that are behaving exactly as git documents.
    """
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "core.worktree", "/somewhere/else")
    _set_shared(root, "core.bare", "true")

    assert _run(root).returncode == 0


def test_an_inherited_git_dir_never_decides_the_verdict(tmp_path: Path) -> None:
    """A checkout is judged by where the script sits, not by the repository a hook exported.

    `GIT_DIR` overrides discovery from `cwd`, and the pre-push hook exports it into the whole gate —
    absolute in a linked worktree. Read, it would clear a broken checkout because some *other*
    repository is clean, which is the exact class of misdirection this guard exists to catch.
    """
    broken = _checkout(tmp_path / "broken")
    _set_shared(broken, "extensions.worktreeConfig", "true")
    _set_shared(broken, "core.worktree", "/somewhere/else")
    clean = _checkout(tmp_path / "clean")

    assert _run(broken, GIT_DIR=str(clean / ".git")).returncode == 1
    assert _run(clean, GIT_DIR=str(broken / ".git")).returncode == 0


def test_a_directory_that_is_no_checkout_passes(tmp_path: Path) -> None:
    """A source export has no config to be wrong; the gate's other steps report that far better."""
    root = tmp_path / "export"
    (root / "scripts").mkdir(parents=True)
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)

    # GIT_CEILING_DIRECTORIES so discovery cannot walk up into the real checkout this test runs in.
    result = _run(root, GIT_CEILING_DIRECTORIES=str(tmp_path))

    assert result.returncode == 0


@pytest.mark.parametrize("value", ["yes", "on", "1"])
def test_the_extension_is_read_as_a_boolean(tmp_path: Path, value: str) -> None:
    """git accepts every spelling of true, so a string comparison would miss most of them."""
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", value)
    _set_shared(root, "core.worktree", "/somewhere/else")

    assert _run(root).returncode == 1

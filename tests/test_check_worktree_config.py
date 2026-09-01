"""Tests for scripts/check_worktree_config.sh — the guard behind issue #1803.

The incident: `core.bare = true` and a `core.worktree` pointing at one session's worktree were
present in the *shared* `.git/config` with `extensions.worktreeConfig` enabled, which strips git's
built-in exception confining those two to the main working tree. Every worktree then resolved to
that one directory — `git add` reported success without changing anything, `git commit --amend`
dropped a file, and `git checkout -- <path>` wrote into a neighbouring session's tree — while
`--git-dir` still answered locally, so nothing looked wrong.

Three groups of tests carry the weight here, and each guards a different way this script can betray
its own purpose:

- The **negative** tests. Both settings in the *per-worktree* config, where they belong, must not
  fail, and neither must a shared value while the extension is off, which is git's own documented
  exception. `make hooks` runs this guard first for `check`, `setup`, and `worktree` alike, so a
  false positive would brick the entire gate and the guard would be deleted within a day.
- The **linked worktree** tests. CLAUDE.md mandates worktrees for concurrent sessions and the
  incident happened in one, so the path where `--git-common-dir` resolves the *main* checkout's
  config is the guard's whole reason for existing.
- The **loud-failure** tests. A guard against a silent misconfiguration is worthless if it reports a
  clean bill of health on a repository it could not read, so every unreadable state must exit 1
  rather than 0.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "check_worktree_config.sh"
MAKEFILE = Path(__file__).resolve().parents[1] / "Makefile"


def _clean_env(**overrides: str) -> dict[str, str]:
    """The ambient environment with git's own variables stripped.

    Run from a git hook (the tracked pre-push runs `make check`), git exports GIT_DIR and friends,
    which would point the setup commands below at the real repository instead of the throwaway one.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")} | overrides


def _git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        env=_clean_env(),
    ).stdout


def _install_script(root: Path) -> None:
    """Put a copy of the script under test at *root*`/scripts/`.

    The script resolves the repository from its own location (`dirname $0/..`), so the copy inside
    the fixture is what aims it at this tree rather than at the real checkout.
    """
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy2(SCRIPT, root / "scripts" / SCRIPT.name)


def _checkout(root: Path) -> Path:
    """A throwaway checkout carrying its own copy of the script under test."""
    root.mkdir(parents=True, exist_ok=True)
    _git("init", "-q", str(root))
    _install_script(root)
    return root


def _linked_worktree(tmp_path: Path) -> tuple[Path, Path]:
    """A main checkout with the script committed, plus a real linked worktree of it.

    `git worktree add` needs a commit to branch from, and the identity comes from `-c` rather than a
    config write so the gate never depends on a global git identity being present.
    """
    main = tmp_path / "main"
    main.mkdir(parents=True)
    _git("init", "-q", str(main))
    _install_script(main)
    _git("add", "-A", cwd=main)
    _git(
        "-c", "user.email=t@example.invalid", "-c", "user.name=t", "commit", "-qm", "init", cwd=main
    )

    linked = tmp_path / "linked"
    _git("worktree", "add", "-q", str(linked), "-b", "topic", cwd=main)
    return main, linked


def _shared_config(root: Path) -> Path:
    return root / ".git" / "config"


def _set_shared(root: Path, key: str, value: str) -> None:
    """Write straight to *root*'s shared config file.

    `--file` alone is not enough to make this robust. It dodges `core.bare = true`, but not
    `core.worktree`: git resolves that one during repository setup, before it honours `--file`, so
    once a stale value is in place every later write dies with "Invalid path" no matter which file
    it targets. `GIT_WORK_TREE` overrides it, the same way the script under test does, which is what
    lets a test build the combined incident state in any order.
    """
    subprocess.run(
        ["git", "config", "--file", str(_shared_config(root)), key, value],
        check=True,
        capture_output=True,
        env=_clean_env(GIT_WORK_TREE=str(root)),
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


# --- the guard stays out of the way -------------------------------------------------------------


def test_a_clean_checkout_passes(tmp_path: Path) -> None:
    """Neither setting present: the guard has nothing to say and must stay silent."""
    root = _checkout(tmp_path / "repo")

    result = _run(root)

    assert result.returncode == 0
    assert result.stderr == ""


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
    # The extension must be enabled before git accepts `--worktree` at all.
    _set_shared(root, "extensions.worktreeConfig", "true")
    _git("config", "--worktree", "core.bare", "false", cwd=root)
    _git("config", "--worktree", "core.worktree", str(root), cwd=root)

    assert _run(root).returncode == 0


def test_a_shared_setting_passes_while_the_extension_is_off(tmp_path: Path) -> None:
    """git's own exception: without the extension, a shared value binds the main worktree only.

    Linked worktrees ignore it, so there is no cross-worktree misdirection to warn about, and firing
    here would fail repositories behaving exactly as git documents.
    """
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "core.worktree", "/somewhere/else")
    _set_shared(root, "core.bare", "true")

    assert _run(root).returncode == 0


def test_a_directory_that_is_no_checkout_passes(tmp_path: Path) -> None:
    """A source export has no config to be wrong about, and must exit the *quiet* way.

    The empty stderr is the whole assertion: exit 0 alone would equally describe the loud
    unreadable-repository branch below, and the two must stay distinguishable.
    """
    root = tmp_path / "export"
    _install_script(root)

    # GIT_CEILING_DIRECTORIES so discovery cannot walk up into the real checkout this test runs in.
    result = _run(root, GIT_CEILING_DIRECTORIES=str(tmp_path))

    assert result.returncode == 0
    assert result.stderr == ""


# --- the guard fires on the incident ------------------------------------------------------------


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
    # The message has to name the file to edit, or it just says "something is wrong" to someone who
    # has never met this failure.
    assert str(_shared_config(root)) in result.stderr


def test_a_core_worktree_pointing_at_a_real_other_tree_fails(tmp_path: Path) -> None:
    """The insidious half: the path exists, so git succeeds while reading the wrong tree.

    No "Invalid path" rescues the reader here — every command simply operates on somebody else's
    directory and reports success, which is how the incident went unnoticed. A materially different
    runtime state from the stale-path case above, so it gets its own test.
    """
    root = _checkout(tmp_path / "repo")
    other = tmp_path / "other-tree"
    other.mkdir()
    _set_shared(root, "extensions.worktreeConfig", "true")
    _set_shared(root, "core.worktree", str(other))

    result = _run(root)

    assert result.returncode == 1
    assert f"core.worktree = {other}" in result.stderr


@pytest.mark.parametrize("value", ["true", "yes", "on", "1"])
def test_a_shared_bare_true_fails(tmp_path: Path, value: str) -> None:
    """`core.bare` — the half that surfaces once a shared `core.worktree` is cleared.

    Parametrized over git's spellings of true, which is what the `--type=bool` read buys: a string
    comparison would wave `yes`, `on`, and `1` straight through. The report normalizes them all to
    `true` rather than echoing the file's wording.
    """
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", "true")
    _set_shared(root, "core.bare", value)

    result = _run(root)

    assert result.returncode == 1
    assert "core.bare = true" in result.stderr
    # Nothing to unset for core.worktree here, so that remedy line must not be offered.
    assert "git config --unset core.worktree" not in result.stderr


@pytest.mark.parametrize("value", ["yes", "on", "1"])
def test_the_extension_is_read_as_a_boolean(tmp_path: Path, value: str) -> None:
    """git accepts every spelling of true for the extension too, so all of them must arm the guard."""
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", value)
    _set_shared(root, "core.worktree", "/somewhere/else")

    assert _run(root).returncode == 1


def test_the_incident_state_reports_both_offenders_and_a_remedy_that_works(tmp_path: Path) -> None:
    """Both settings poisoned at once — the actual shape of issue #1803 — end to end.

    The round-trip is the point: a remedy that does not clear the guard is worse than none, because
    the reader follows it, stays red, and has no idea why. An earlier draft of this script printed
    `git config --worktree core.bare false`, which writes the per-worktree file and leaves the
    shared value exactly where the guard reads it — permanently red, and every *other* worktree
    still misdirected.
    """
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", "true")
    _set_shared(root, "core.bare", "true")
    _set_shared(root, "core.worktree", "/gone/session-worktree")

    result = _run(root)

    assert result.returncode == 1
    assert "core.worktree = /gone/session-worktree" in result.stderr
    assert "core.bare = true" in result.stderr

    for key in ("core.worktree", "core.bare"):
        assert f"GIT_WORK_TREE=. git config --unset {key}" in result.stderr
        # The prefix is what makes the command survive the stale path; run it exactly as printed.
        subprocess.run(
            ["git", "config", "--unset", key],
            cwd=root,
            check=True,
            capture_output=True,
            env=_clean_env(GIT_WORK_TREE="."),
        )

    assert _run(root).returncode == 0


# --- linked worktrees, where the incident happened ------------------------------------------------


def test_a_linked_worktree_is_judged_by_the_shared_config(tmp_path: Path) -> None:
    """From a linked worktree, the file read must be the MAIN checkout's `.git/config`.

    This is what `--git-common-dir` buys over `--git-dir`. In a linked worktree `--git-dir` names
    `.git/worktrees/<name>/`, which holds no `config` at all, so a guard built on it would find
    nothing to read and wave the repository through — silent, on precisely the arrangement CLAUDE.md
    mandates for concurrent sessions and the one the incident occurred in.
    """
    main, linked = _linked_worktree(tmp_path)
    _set_shared(main, "extensions.worktreeConfig", "true")
    _set_shared(main, "core.worktree", "/gone/session-worktree")

    result = _run(linked)

    assert result.returncode == 1
    assert "core.worktree = /gone/session-worktree" in result.stderr
    assert str(_shared_config(main)) in result.stderr


def test_a_healthy_linked_worktree_passes(tmp_path: Path) -> None:
    """The negative twin: a correctly configured pair must not block either checkout's gate."""
    main, linked = _linked_worktree(tmp_path)
    _set_shared(main, "extensions.worktreeConfig", "true")
    _git("config", "--worktree", "core.bare", "false", cwd=linked)
    _git("config", "--worktree", "core.worktree", str(linked), cwd=linked)

    assert _run(linked).returncode == 0
    assert _run(main).returncode == 0


# --- an unreadable repository is never a clean bill of health -------------------------------------


def test_an_unreadable_repository_is_reported_not_waved_through(tmp_path: Path) -> None:
    """Any git failure other than "there is no checkout here" must be loud.

    Waved through, the guard would report all clear on a repository it could not read — the one
    outcome worse than a false positive, since it is the silence the guard exists to end.
    """
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "core.repositoryformatversion", "1")
    _set_shared(root, "extensions.bogusThing", "true")

    result = _run(root)

    assert result.returncode == 1
    assert "cannot read the repository" in result.stderr
    # git's own words must reach the human, or the message is unactionable.
    assert "bogusthing" in result.stderr.lower()


def test_a_pruned_worktree_admin_directory_is_reported_not_waved_through(tmp_path: Path) -> None:
    """A linked worktree whose admin directory was pruned out from under it.

    git says "not a git repository" here, so a guard classifying on that wording would exit 0 — on a
    repository carrying the poisoned setting. It is the incident's own residue: `core.worktree`
    points at a worktree that was removed, and removing a worktree is exactly what prunes the admin
    directory. Hence the filesystem check for `.git` rather than a match on git's prose.
    """
    main, linked = _linked_worktree(tmp_path)
    _set_shared(main, "extensions.worktreeConfig", "true")
    _set_shared(main, "core.worktree", "/gone/session-worktree")
    shutil.rmtree(main / ".git" / "worktrees")

    result = _run(linked)

    assert result.returncode == 1
    assert "cannot read the repository" in result.stderr


def test_a_warning_on_gits_stderr_does_not_wave_the_repository_through(tmp_path: Path) -> None:
    """git may warn and still exit 0; the warning must not be read as part of the answer.

    Merged into the captured value, a warning is prepended to the common directory, the shared
    config path becomes one that cannot exist, and the guard passes for want of a file to check.
    """
    root = _checkout(tmp_path / "repo")
    _set_shared(root, "extensions.worktreeConfig", "true")
    _set_shared(root, "core.worktree", "/gone/session-worktree")

    real_git = shutil.which("git", path=os.environ["PATH"])
    assert real_git is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    shim = bin_dir / "git"
    shim.write_text(
        f'#!/bin/sh\necho "warning: simulated noise on stderr" >&2\nexec {real_git} "$@"\n',
        encoding="utf-8",
    )
    shim.chmod(0o755)

    result = _run(root, PATH=f"{bin_dir}{os.pathsep}{os.environ['PATH']}")

    assert result.returncode == 1
    assert "core.worktree = /gone/session-worktree" in result.stderr


# --- an inherited location never decides the verdict ----------------------------------------------


@pytest.mark.parametrize("variable", ["GIT_DIR", "GIT_COMMON_DIR"])
def test_an_inherited_location_never_decides_the_verdict(tmp_path: Path, variable: str) -> None:
    """A checkout is judged by where the script sits, not by the repository a hook exported.

    Both variables override discovery from `cwd`, and `GIT_COMMON_DIR` names the very file this
    guard reads. Read, either one would clear a broken checkout because some *other* repository is
    clean — the exact class of misdirection the guard exists to catch. The pre-push hook exports
    `GIT_DIR` into the whole gate, absolute in a linked worktree.
    """
    broken = _checkout(tmp_path / "broken")
    _set_shared(broken, "extensions.worktreeConfig", "true")
    _set_shared(broken, "core.worktree", "/somewhere/else")
    clean = _checkout(tmp_path / "clean")

    assert _run(broken, **{variable: str(clean / ".git")}).returncode == 1
    assert _run(clean, **{variable: str(broken / ".git")}).returncode == 0


# --- the wiring ------------------------------------------------------------------------------------


def test_the_hooks_target_runs_the_guard() -> None:
    """`make hooks` is what puts the guard in front of `check`, `setup`, and `worktree` alike.

    Without this, dropping the one Makefile line disables the guard everywhere with a green suite.
    """
    recipe = MAKEFILE.read_text(encoding="utf-8").split("\nhooks:\n", 1)[1].split("\n\n", 1)[0]

    assert "./scripts/check_worktree_config.sh" in recipe


def test_the_guard_is_linted_as_a_shell_script() -> None:
    """The script must be in SHELL_SCRIPTS, or `make lint-sh` silently skips it."""
    assert (
        "scripts/check_worktree_config.sh"
        in MAKEFILE.read_text(encoding="utf-8").split("SHELL_SCRIPTS :=", 1)[1].split("\n", 1)[0]
    )


def test_the_pre_push_hook_scrubs_gits_location_variables() -> None:
    """The gate must not inherit a repository location from the hook that runs it (issue #1803)."""
    hook = (Path(__file__).resolve().parents[1] / ".githooks" / "pre-push").read_text(
        encoding="utf-8"
    )

    unset_line = next(line for line in hook.splitlines() if line.startswith("unset "))
    for variable in (
        "GIT_DIR",
        "GIT_WORK_TREE",
        "GIT_INDEX_FILE",
        "GIT_OBJECT_DIRECTORY",
        "GIT_COMMON_DIR",
    ):
        assert variable in unset_line


if __name__ == "__main__":  # pragma: no cover - convenience for a single-file run
    sys.exit(pytest.main([__file__]))

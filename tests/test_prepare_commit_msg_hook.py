"""Tests for the `Refs: BE-NNNN` trailer in .githooks/prepare-commit-msg.

The hook reads the roadmap id out of the branch name so a commit records which item it belongs to,
which `git blame` and `git log -L` otherwise cannot recover: merges carry the pull-request number,
so an ordinary commit has nothing to follow. The branch-name match is the part that can regress
quietly — too loose and "describe-1234" stamps a wrong id, too tight and `be0378` stops working —
so these tests drive the real hook script against a temporary repository.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / ".githooks" / "prepare-commit-msg"

pytestmark = pytest.mark.skipif(
    shutil.which("git") is None or not HOOK.exists(), reason="needs git and the tracked hook"
)


def _git_env() -> dict[str, str]:
    """The environment minus git's own variables, so git answers for `cwd`'s repo.

    A git hook exports GIT_DIR and GIT_INDEX_FILE into everything it runs, so under the pre-push
    gate these tests would otherwise drive the real repository instead of their temporary one.
    """
    return {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}


def _repo(tmp_path: Path, branch: str) -> Path:
    """A repository on ``branch``, with one file staged for the hook's own gitleaks scan."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run = ["git", "-c", "user.email=t@example.com", "-c", "user.name=T"]
    subprocess.run([*run, "init", "-q", "-b", branch, "."], cwd=repo, env=_git_env(), check=True)
    (repo / "f.txt").write_text("content\n", encoding="utf-8")
    subprocess.run([*run, "add", "f.txt"], cwd=repo, env=_git_env(), check=True)
    return repo


def _run_hook(repo: Path, message: str, source: str = "message") -> str:
    """Run the hook over ``message`` and return the file as the hook left it.

    The hook's exit status is ignored: its second job is a gitleaks scan, which is absent on some
    machines and irrelevant to the trailer this asserts on.
    """
    msg_file = repo / "COMMIT_EDITMSG"
    msg_file.write_text(message, encoding="utf-8")
    subprocess.run(
        ["bash", str(HOOK), str(msg_file), source],
        cwd=repo,
        env=_git_env(),
        capture_output=True,
        check=False,
    )
    return msg_file.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("branch", "expected"),
    [
        ("claude/be-0305-implementation-509797", "Refs: BE-0305"),
        ("claude/be0378-wedge-measurements", "Refs: BE-0378"),
        ("user/BE-0042-topic", "Refs: BE-0042"),
    ],
)
def test_a_roadmap_branch_stamps_its_id(tmp_path: Path, branch: str, expected: str) -> None:
    """Every shape the branch convention produces yields the item's canonical id."""
    repo = _repo(tmp_path, branch)

    assert expected in _run_hook(repo, "feat(run): do a thing\n")


@pytest.mark.parametrize(
    "branch",
    [
        "claude/yaml-resolver-leak",
        "claude/describe-1234-thing",  # "be" inside a word is not an id
        "claude/probe-9999",
        "claude/be-042",  # three digits is not a BE id
        "claude/be-12345",  # nor five
        "main",
    ],
)
def test_a_branch_without_an_id_is_left_alone(tmp_path: Path, branch: str) -> None:
    """A wrong id is worse than none, so the match refuses anything but a real one."""
    repo = _repo(tmp_path, branch)

    assert "Refs:" not in _run_hook(repo, "fix(x): no id here\n")


def test_the_trailer_is_not_added_twice(tmp_path: Path) -> None:
    """An amend re-runs the hook, and a second trailer would be noise in every amended commit."""
    repo = _repo(tmp_path, "claude/be-0305-topic")

    once = _run_hook(repo, "feat(run): do a thing\n")
    twice = _run_hook(repo, once)

    assert twice.count("Refs: BE-0305") == 1


def test_a_hand_written_ref_is_kept(tmp_path: Path) -> None:
    """An author who named a different item meant it; the hook must not overwrite the choice."""
    repo = _repo(tmp_path, "claude/be-0305-topic")

    result = _run_hook(repo, "docs: keep this\n\nRefs: BE-9999\n")

    assert "Refs: BE-9999" in result
    assert "BE-0305" not in result


def test_an_existing_trailer_block_is_extended(tmp_path: Path) -> None:
    """The id joins the trailer block rather than landing in the middle of the body."""
    repo = _repo(tmp_path, "claude/be-0305-topic")

    result = _run_hook(repo, "fix(run): thing\n\nBody.\n\nCo-Authored-By: A <a@example.com>\n")

    lines = [line for line in result.splitlines() if line.strip()]
    assert lines[-2:] == ["Co-Authored-By: A <a@example.com>", "Refs: BE-0305"]


@pytest.mark.parametrize("source", ["merge", "squash"])
def test_a_generated_message_is_left_alone(tmp_path: Path, source: str) -> None:
    """A merge or squash message is about the commits it joins, not this branch's item."""
    repo = _repo(tmp_path, "claude/be-0305-topic")

    assert "Refs:" not in _run_hook(repo, "Merge branch 'side'\n", source)


def test_an_empty_message_gets_no_trailer(tmp_path: Path) -> None:
    """A trailer on the first line would read as the subject, which commit-msg then rejects."""
    repo = _repo(tmp_path, "claude/be-0305-topic")

    result = _run_hook(repo, "\n# Please enter the commit message for your changes.\n")

    assert "Refs:" not in result

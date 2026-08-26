"""Tests for the scoped agent-skill audit (scripts/audit_skills.py).

The audit's scope is the whole point: APM's `claude` target governs `.claude/` entire, which is
also where Claude Code parks one full checkout per concurrent session, so the content scan reached
into other sessions' `.venv` and `node_modules` and reddened the local gate over files CI never
sees (issue #1775). These tests build throwaway git checkouts and pin the two halves of the
narrowing — that an ignored path never reaches the mirror, and that everything git does see does.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "audit_skills.py"
_spec = importlib.util.spec_from_file_location("audit_skills", _MODULE_PATH)
assert _spec and _spec.loader
audit_skills = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = audit_skills
_spec.loader.exec_module(audit_skills)


def _checkout(root: Path) -> Path:
    """A git checkout shaped like this repository's audited paths, worktree noise included."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "apm.yml").write_text("name: test\n", encoding="utf-8")
    (root / ".apm" / "skills" / "demo").mkdir(parents=True)
    (root / ".apm" / "skills" / "demo" / "SKILL.md").write_text("source\n", encoding="utf-8")
    (root / ".claude" / "skills" / "demo").mkdir(parents=True)
    (root / ".claude" / "skills" / "demo" / "SKILL.md").write_text("deployed\n", encoding="utf-8")
    (root / ".gitignore").write_text(".claude/worktrees/\n", encoding="utf-8")

    # The failure this script exists for: another session's checkout, carrying a vendored file no
    # ignore rule in this repository authored and no CI checkout contains.
    vendored = root / ".claude" / "worktrees" / "other-session" / ".venv" / "lib"
    vendored.mkdir(parents=True)
    (vendored / "vendored.py").write_text("x = 1\n", encoding="utf-8")

    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    return root


def test_ignored_worktrees_stay_out_of_the_audit(tmp_path: Path) -> None:
    """A concurrent session's checkout is invisible to the enumeration that feeds the mirror."""
    root = _checkout(tmp_path)

    files = audit_skills.git_visible_files(root)

    assert files is not None
    assert not [rel for rel in files if rel.startswith(".claude/worktrees/")]


def test_repository_content_is_audited(tmp_path: Path) -> None:
    """Both the skill source and its deployment reach the mirror, so drift still has to fail."""
    root = _checkout(tmp_path)

    files = audit_skills.git_visible_files(root)

    assert files is not None
    assert ".apm/skills/demo/SKILL.md" in files
    assert ".claude/skills/demo/SKILL.md" in files
    assert "apm.yml" in files


def test_uncommitted_skill_is_audited(tmp_path: Path) -> None:
    """A skill edited but not yet committed is the work in hand — the gate must still see it."""
    root = _checkout(tmp_path)
    (root / ".claude" / "skills" / "fresh").mkdir(parents=True)
    (root / ".claude" / "skills" / "fresh" / "SKILL.md").write_text("new\n", encoding="utf-8")

    files = audit_skills.git_visible_files(root)

    assert files is not None
    assert ".claude/skills/fresh/SKILL.md" in files


def test_outside_a_git_checkout_reports_no_scope(tmp_path: Path) -> None:
    """Without git there is no ignore list to read, which the caller handles by auditing in place."""
    assert audit_skills.git_visible_files(tmp_path) is None


def test_mirror_reproduces_the_relative_layout(tmp_path: Path) -> None:
    """APM reads paths, not a flat file set, so the copy has to preserve each one."""
    root = _checkout(tmp_path / "repo")
    dest = tmp_path / "mirror"
    dest.mkdir()

    copied = audit_skills.build_mirror(root, [".claude/skills/demo/SKILL.md", "apm.yml"], dest)

    assert copied == 2
    assert (dest / ".claude" / "skills" / "demo" / "SKILL.md").read_text(encoding="utf-8") == (
        "deployed\n"
    )
    assert (dest / "apm.yml").exists()


@pytest.mark.parametrize("rel", ["gone.md", "link.md"])
def test_mirror_skips_what_an_in_place_audit_would_not_read(tmp_path: Path, rel: str) -> None:
    """A deleted tracked file and a symlink are both absent from what APM would scan in place."""
    root = tmp_path / "repo"
    root.mkdir()
    (root / "target.md").write_text("real\n", encoding="utf-8")
    (root / "link.md").symlink_to(root / "target.md")
    dest = tmp_path / "mirror"
    dest.mkdir()

    assert audit_skills.build_mirror(root, [rel], dest) == 0
    assert not (dest / rel).exists()

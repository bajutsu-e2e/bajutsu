"""Tests for the scoped agent-skill audit (scripts/audit_skills.py).

The audit's scope is the whole point: APM's `claude` target governs `.claude/` entire, which is
also where Claude Code parks one full checkout per concurrent session, so the content scan reached
into other sessions' `.venv` and `node_modules` and reddened the local gate over files CI never
sees (issue #1775). These tests build throwaway git checkouts and pin the two halves of the
narrowing — that an ignored path never reaches the mirror, and that everything git does see does —
then pin the wiring around them: that the audit really runs in the mirror rather than in place,
that it refuses to pass having audited nothing, and that a broken git stops the gate instead of
posing as a missing checkout.
"""

from __future__ import annotations

import importlib.util
import json
import os
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


# A fake `apm`, written out and put first on PATH. The mirror is a TemporaryDirectory `main`
# deletes on return, so the stub has to capture the tree while it still stands.
_STUB_APM = '''#!{python}
"""Record where `apm` was run and what it could see there, then exit with a chosen code."""

import json
import pathlib
import sys

cwd = pathlib.Path.cwd()
payload = {{
    "cwd": str(cwd),
    "files": sorted(p.relative_to(cwd).as_posix() for p in cwd.rglob("*") if p.is_file()),
    "argv": sys.argv[1:],
}}
pathlib.Path({record!r}).write_text(json.dumps(payload), encoding="utf-8")
sys.exit({exit_code})
'''


def _stub_apm(monkeypatch: pytest.MonkeyPatch, bin_dir: Path, record: Path, exit_code: int) -> None:
    """Install the stub as the `apm` the script resolves, recording its run into *record*."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "apm"
    stub.write_text(
        _STUB_APM.format(python=sys.executable, record=str(record), exit_code=exit_code),
        encoding="utf-8",
    )
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")


def _checkout(root: Path) -> Path:
    """A git checkout shaped like this repository's audited paths, worktree noise included."""
    root.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    (root / "apm.yml").write_text("name: test\n", encoding="utf-8")
    (root / "apm.lock.yaml").write_text("packages: []\n", encoding="utf-8")
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


def test_the_audit_runs_in_the_mirror_and_carries_apms_verdict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """APM has to run *in* the mirror, and its verdict has to become the gate's."""
    root = _checkout(tmp_path / "repo")
    record = tmp_path / "record.json"
    _stub_apm(monkeypatch, tmp_path / "bin", record, exit_code=3)

    assert audit_skills.main(["--root", str(root)]) == 3

    seen = json.loads(record.read_text(encoding="utf-8"))
    assert Path(seen["cwd"]).resolve() != root.resolve()
    assert ".claude/worktrees/other-session/.venv/lib/vendored.py" not in seen["files"]
    assert ".claude/skills/demo/SKILL.md" in seen["files"]


@pytest.mark.parametrize("manifest", ["apm.yml", "apm.lock.yaml"])
def test_an_audit_a_manifest_never_reached_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manifest: str
) -> None:
    """Either manifest missing makes APM exit 0 having audited nothing — a pass, on nothing."""
    root = _checkout(tmp_path / "repo")
    # Tracked but deleted from the working tree: `--cached` still lists it, the mirror skips it.
    (root / manifest).unlink()
    record = tmp_path / "record.json"
    _stub_apm(monkeypatch, tmp_path / "bin", record, exit_code=0)

    assert audit_skills.main(["--root", str(root)]) == 1
    assert not record.exists()


def test_a_source_export_missing_a_manifest_is_refused_too(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The in-place branch a source export takes needs the same guard the mirror gets."""
    root = tmp_path / "export"
    root.mkdir()
    record = tmp_path / "record.json"
    _stub_apm(monkeypatch, tmp_path / "bin", record, exit_code=0)

    assert audit_skills.main(["--root", str(root)]) == 1
    assert not record.exists()


def test_a_broken_git_is_not_read_as_a_missing_checkout(tmp_path: Path) -> None:
    """A corrupt index is git failing, not the absence of a repository."""
    root = _checkout(tmp_path / "repo")
    (root / ".git" / "index").write_bytes(b"garbage")

    with pytest.raises(subprocess.CalledProcessError):
        audit_skills.git_visible_files(root)


def test_a_broken_git_stops_the_gate_instead_of_auditing_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Auditing in place there would scan the worktrees this script exists to keep out."""
    root = _checkout(tmp_path / "repo")
    (root / ".git" / "index").write_bytes(b"garbage")
    record = tmp_path / "record.json"
    _stub_apm(monkeypatch, tmp_path / "bin", record, exit_code=0)

    assert audit_skills.main(["--root", str(root)]) != 0
    assert not record.exists()
    assert "fatal:" in capsys.readouterr().err

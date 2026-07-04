"""Tests for restrictive run-artifact permissions (BE-0131)."""

from __future__ import annotations

import stat
from pathlib import Path

from bajutsu.artifact_perms import ARTIFACT_FILE_MODE, RUN_DIR_MODE, make_run_dir, restrict_file


def _mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_make_run_dir_is_owner_only(tmp_path: Path) -> None:
    run_dir = make_run_dir(tmp_path / "runs" / "run1")
    assert run_dir.is_dir()
    assert _mode(run_dir) == RUN_DIR_MODE == 0o700


def test_make_run_dir_re_restricts_an_existing_dir(tmp_path: Path) -> None:
    # A run dir first created world-readable by an earlier write (e.g. a step-dir mkdir with the
    # ambient umask) must be tightened, not left as-is — the guarantee is unconditional.
    existing = tmp_path / "runs" / "run1"
    existing.mkdir(parents=True)
    existing.chmod(0o755)
    make_run_dir(existing)
    assert _mode(existing) == 0o700


def test_restrict_file_is_owner_only(tmp_path: Path) -> None:
    f = tmp_path / "network.json"
    f.write_text("[]", encoding="utf-8")
    f.chmod(0o644)
    restrict_file(f)
    assert _mode(f) == ARTIFACT_FILE_MODE == 0o600


def test_restrict_file_is_a_no_op_when_absent(tmp_path: Path) -> None:
    # Some drivers (the fake/headless path) record a screenshot without writing bytes; there is
    # nothing to protect then, so restricting a missing file must not raise.
    missing = tmp_path / "after.png"
    restrict_file(missing)
    assert not missing.exists()

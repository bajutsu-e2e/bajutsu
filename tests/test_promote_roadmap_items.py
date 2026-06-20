"""Tests for scripts/promote_roadmap_items.py.

The script reconciles each roadmap item's subdirectory with its Status — moving a shipped item
(Status: Implemented) from proposals/ to implemented/ and regenerating the index. These tests pin
the pure detection (misfiled_items / read_status / expected_category) over temporary trees, the
git-mv side effect over a throwaway git repo, and finally assert the committed roadmaps/ tree is
already filed correctly (the same invariant the gate enforces).
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "promote_roadmap_items.py"
_spec = importlib.util.spec_from_file_location("promote_roadmap_items", _MODULE_PATH)
assert _spec and _spec.loader
pri = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = pri
_spec.loader.exec_module(pri)


def _write_item(roadmap: Path, category: str, name: str, status: str) -> Path:
    """Create a minimal BE item directory (both language files) with the given Status."""
    item = roadmap / category / name
    item.mkdir(parents=True)
    (item / f"{name}.md").write_text(
        f"# {name} — demo\n\n* Status: **{status}**\n", encoding="utf-8"
    )
    (item / f"{name}-ja.md").write_text(
        f"# {name} — demo\n\n* 状態: **{status}**\n", encoding="utf-8"
    )
    return item


# --- pure detection --------------------------------------------------------------


def test_expected_category_only_implemented_ships() -> None:
    assert pri.expected_category("Implemented") == "implemented"
    for status in ("Proposal", "Proposal (deferred)", "Accepted, in progress"):
        assert pri.expected_category(status) == "proposals"


def test_read_status_strips_emphasis(tmp_path: Path) -> None:
    item = _write_item(tmp_path, "proposals", "BE-9001-foo", "Proposal (deferred)")
    assert pri.read_status(item) == "Proposal (deferred)"


def test_read_status_none_when_absent(tmp_path: Path) -> None:
    item = tmp_path / "proposals" / "BE-9002-bar"
    item.mkdir(parents=True)
    (item / "BE-9002-bar.md").write_text(
        "# BE-9002-bar — demo\n\nno status line\n", encoding="utf-8"
    )
    assert pri.read_status(item) is None
    # ...and None when the English file is missing entirely.
    assert pri.read_status(tmp_path / "proposals" / "BE-9003-missing") is None


def test_misfiled_flags_shipped_under_proposals(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-foo", "Implemented")
    _write_item(roadmap, "implemented", "BE-9002-bar", "Implemented")  # correctly filed
    _write_item(roadmap, "proposals", "BE-9003-baz", "Accepted, in progress")  # correctly filed
    assert pri.misfiled_items(roadmap) == [
        pri.Misfiled(
            name="BE-9001-foo", status="Implemented", current="proposals", expected="implemented"
        )
    ]


def test_misfiled_flags_unshipped_under_implemented(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "implemented", "BE-9004-qux", "Proposal")
    assert pri.misfiled_items(roadmap) == [
        pri.Misfiled(
            name="BE-9004-qux", status="Proposal", current="implemented", expected="proposals"
        )
    ]


def test_misfiled_skips_items_without_status(tmp_path: Path) -> None:
    roadmap = tmp_path / "roadmaps"
    item = roadmap / "proposals" / "BE-9005-nostatus"
    item.mkdir(parents=True)
    (item / "BE-9005-nostatus.md").write_text("# BE-9005 — demo\n\nno status\n", encoding="utf-8")
    assert pri.misfiled_items(roadmap) == []


# --- main / --check --------------------------------------------------------------


def test_check_exits_1_when_misfiled(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-foo", "Implemented")
    monkeypatch.setattr(pri, "ROADMAP", roadmap)
    assert pri.main(["--check"]) == 1


def test_check_exits_0_when_clean(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "implemented", "BE-9002-bar", "Implemented")
    monkeypatch.setattr(pri, "ROADMAP", roadmap)
    assert pri.main(["--check"]) == 0


def test_main_noop_on_clean_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-foo", "Proposal")
    monkeypatch.setattr(pri, "ROADMAP", roadmap)
    calls: list[str] = []
    monkeypatch.setattr(pri, "git_mv", lambda _src, _dst: calls.append("mv"))
    monkeypatch.setattr(pri, "regenerate_index", lambda: calls.append("index"))
    assert pri.main([]) == 0
    assert calls == []


# --- promote side effects --------------------------------------------------------


def test_promote_computes_moves_and_reindexes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-foo", "Implemented")
    moved: list[tuple[Path, Path]] = []
    reindexed: list[bool] = []
    monkeypatch.setattr(pri, "git_mv", lambda src, dst: moved.append((src, dst)))
    monkeypatch.setattr(pri, "regenerate_index", lambda: reindexed.append(True))
    result = pri.promote(roadmap)
    assert [m.name for m in result] == ["BE-9001-foo"]
    assert moved == [
        (roadmap / "proposals" / "BE-9001-foo", roadmap / "implemented" / "BE-9001-foo")
    ]
    assert reindexed == [True]


def test_promote_refuses_to_clobber(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-foo", "Implemented")
    _write_item(roadmap, "implemented", "BE-9001-foo", "Implemented")  # same id already there
    monkeypatch.setattr(pri, "git_mv", lambda _src, _dst: None)
    monkeypatch.setattr(pri, "regenerate_index", lambda: None)
    with pytest.raises(SystemExit, match="already exists"):
        pri.promote(roadmap)


def test_promote_git_mv_moves_directory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end over a throwaway git repo: the directory physically moves to implemented/."""
    # A git hook (e.g. pre-push running `make check`) exports GIT_DIR / GIT_INDEX_FILE into this
    # process; left set, they would redirect the nested git calls below at the outer repo. Clear
    # them so init/add/mv operate on the throwaway repo discovered from cwd.
    for var in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(var, raising=False)
    roadmap = tmp_path / "roadmaps"
    _write_item(roadmap, "proposals", "BE-9001-foo", "Implemented")
    monkeypatch.chdir(tmp_path)
    for args in (["init"], ["add", "-A"]):
        subprocess.run(["git", *args], cwd=tmp_path, check=True, capture_output=True)
    monkeypatch.setattr(pri, "regenerate_index", lambda: None)  # no index markers in the fixture
    pri.promote(roadmap)
    assert (roadmap / "implemented" / "BE-9001-foo" / "BE-9001-foo.md").is_file()
    assert not (roadmap / "proposals" / "BE-9001-foo").exists()


# --- the gate --------------------------------------------------------------------


def test_committed_items_filed_by_status() -> None:
    """The gate: every committed item sits in the subdirectory its Status calls for."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    misfiled = pri.misfiled_items(roadmap)
    assert misfiled == [], (
        f"roadmap items misfiled by Status (run `make roadmap-promote`): {misfiled}"
    )

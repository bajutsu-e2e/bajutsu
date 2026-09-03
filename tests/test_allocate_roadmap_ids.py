"""Tests for scripts/allocate_roadmap_ids.py.

The script allocates permanent BE IDs to ``BE-XXXX`` placeholders when a roadmap PR merges to
``main``. These tests pin the pure pieces (the used-id floor, the ids-on-a-ref lookup) over
temporary trees, the git-mv side effects over throwaway git repos, and finally assert the committed
roadmaps/ tree carries no duplicate id.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "allocate_roadmap_ids.py"
_spec = importlib.util.spec_from_file_location("allocate_roadmap_ids", _MODULE_PATH)
assert _spec and _spec.loader
ari = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = ari
_spec.loader.exec_module(ari)


def _make_item(roadmap: Path, name: str, body: str = "") -> Path:
    """Create a minimal BE item (both language files) that self-references its id token."""
    be_id = "-".join(name.split("-")[:2])  # "BE-0054-foo" -> "BE-0054" (or "BE-XXXX")
    item = roadmap / name
    item.mkdir(parents=True)
    (item / f"{name}.md").write_text(
        f"# {be_id} — demo\n\n* Proposal: [{be_id}]({name}.md)\n{body}", encoding="utf-8"
    )
    (item / f"{name}-ja.md").write_text(f"# {be_id} — demo\n", encoding="utf-8")
    return item


def _git_init(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Make tmp_path a throwaway git repo with everything staged, isolated from the outer repo.

    A git hook (pre-push running `make check`) exports GIT_DIR / GIT_INDEX_FILE into this process;
    left set they redirect the nested git calls at the outer repo. Clear them, then init + add. A
    throwaway identity (and no commit signing) is set locally so a test can commit even on a fresh
    CI runner with no global git config — without it `git commit` exits 128 ("who are you?").
    """
    for var in [k for k in os.environ if k.startswith("GIT_")]:
        monkeypatch.delenv(var, raising=False)
    monkeypatch.chdir(tmp_path)
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, capture_output=True)
    config = (
        ("user.email", "test@example.com"),
        ("user.name", "test"),
        ("commit.gpgsign", "false"),
    )
    for key, value in config:
        subprocess.run(["git", "config", key, value], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True, capture_output=True)


# --- used-id floor ---------------------------------------------------------------


def test_used_ids_folds_tree_and_main(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ari, "working_tree_ids", lambda: {1})
    monkeypatch.setattr(ari, "ids_on_git_ref", lambda ref: {2} if ref == "origin/main" else set())
    assert ari.used_ids() == {1, 2}


# --- ids_on_git_ref over a real ref ----------------------------------------------


def test_ids_on_git_ref_reads_ref_and_degrades(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "BE-0007-alpha")
    _make_item(roadmap, "BE-0003-beta")
    _git_init(tmp_path, monkeypatch)
    subprocess.run(["git", "commit", "-m", "seed", "-q"], cwd=tmp_path, check=True)
    assert ari.ids_on_git_ref("HEAD") == {7, 3}
    # An unavailable ref degrades to empty, so a shallow/remoteless checkout still runs.
    assert ari.ids_on_git_ref("no-such-ref") == set()


# --- allocate (end-to-end over a throwaway git repo) -----------------------------


def test_allocate_renames_placeholder_end_to_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "BE-XXXX-demo-feature")  # the placeholder to allocate
    _make_item(roadmap, "BE-0003-existing")  # floor from the working tree
    _git_init(tmp_path, monkeypatch)
    # origin/main is unavailable (no remote) -> {}; the next free id above the working-tree max (3).

    assert ari.main() == 0

    allocated = roadmap / "BE-0004-demo-feature" / "BE-0004-demo-feature.md"
    assert allocated.is_file()
    assert not (roadmap / "BE-XXXX-demo-feature").exists()
    assert "BE-0004" in allocated.read_text(encoding="utf-8")
    assert "BE-XXXX" not in allocated.read_text(encoding="utf-8")


def test_allocate_renames_placeholder_with_nested_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An item that carries a nested subdirectory (e.g. an investigation moved under its BE item)
    is renamed and rewritten too, not just its two top-level canonical files."""
    roadmap = tmp_path / "roadmaps"
    item = _make_item(roadmap, "BE-XXXX-with-notes")
    nested = item / "investigations" / "topic"
    nested.mkdir(parents=True)
    (nested / "README.md").write_text("notes for BE-XXXX-with-notes\n", encoding="utf-8")
    # A nested file whose own *name* carries the token exercises the rename step, not just the
    # text-rewrite step — reverting `f.with_name(renamed)` to the old `new_dir / renamed` would
    # flatten this file into the item's root instead of renaming it in place.
    (nested / "BE-XXXX-notes.md").write_text("see BE-XXXX-with-notes\n", encoding="utf-8")
    _git_init(tmp_path, monkeypatch)

    assert ari.main() == 0

    allocated_dir = roadmap / "BE-0001-with-notes"
    nested_readme = allocated_dir / "investigations" / "topic" / "README.md"
    assert nested_readme.is_file()
    assert nested_readme.read_text(encoding="utf-8") == "notes for BE-0001-with-notes\n"
    nested_named = allocated_dir / "investigations" / "topic" / "BE-0001-notes.md"
    assert nested_named.is_file()
    assert nested_named.read_text(encoding="utf-8") == "see BE-0001-with-notes\n"


def test_allocate_renames_nested_directory_carrying_the_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A nested *directory* (not just a file) whose own name carries the token is renamed too —
    otherwise it would keep the placeholder in its path forever, and the dangling-reference lint
    would never catch it (a directory named ``BE-XXXX-*`` is exempted as a placeholder's own
    self-reference)."""
    roadmap = tmp_path / "roadmaps"
    item = _make_item(roadmap, "BE-XXXX-with-notes")
    (item / "investigations" / "BE-XXXX-notes").mkdir(parents=True)
    (item / "investigations" / "BE-XXXX-notes" / "README.md").write_text(
        "notes\n", encoding="utf-8"
    )
    _git_init(tmp_path, monkeypatch)

    assert ari.main() == 0

    allocated = roadmap / "BE-0001-with-notes" / "investigations" / "BE-0001-notes" / "README.md"
    assert allocated.is_file()


def test_allocate_skips_binary_file_text_rewrite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A binary artifact under a nested directory (e.g. a screenshot) is renamed like any other
    file but is not decoded as UTF-8 for the token rewrite — it carries no id text to replace, and
    decoding it would crash the allocator."""
    roadmap = tmp_path / "roadmaps"
    item = _make_item(roadmap, "BE-XXXX-with-notes")
    nested = item / "investigations" / "topic"
    nested.mkdir(parents=True)
    (nested / "shot.png").write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00\x01")
    _git_init(tmp_path, monkeypatch)

    assert ari.main() == 0

    allocated_dir = roadmap / "BE-0001-with-notes"
    assert (allocated_dir / "investigations" / "topic" / "shot.png").is_file()


def test_placeholder_dirs_orders_by_slug(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Every item lives directly under roadmaps/ (BE-0159), so allocation order is a pure function of
    # the directory name (the slug) — the flat scan yields the placeholders sorted by name.
    roadmap = tmp_path / "roadmaps"
    monkeypatch.setattr(ari, "ROADMAP", roadmap)
    _make_item(roadmap, "BE-XXXX-zzz-last-by-slug")
    _make_item(roadmap, "BE-XXXX-aaa-first-by-slug")

    names = [d.name for d in ari.placeholder_dirs()]

    assert names == ["BE-XXXX-aaa-first-by-slug", "BE-XXXX-zzz-last-by-slug"]


def test_allocate_noop_without_placeholders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    roadmap = tmp_path / "roadmaps"
    _make_item(roadmap, "BE-0003-existing")
    monkeypatch.chdir(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(ari, "git_mv", lambda _s, _d: calls.append("mv"))
    assert ari.main() == 0
    assert calls == []


# --- the gate --------------------------------------------------------------------


def test_committed_tree_has_no_duplicate_be_ids() -> None:
    """No two committed items share a BE number — the invariant allocate protects."""
    roadmap = Path(__file__).resolve().parent.parent / "roadmaps"
    seen: dict[int, str] = {}
    duplicates: list[tuple[int, str, str]] = []
    for d in ari.iter_item_dirs(roadmap):
        if not (m := ari.numbered_match(d.name)):
            continue
        be_id = int(m.group(1))
        if be_id in seen:
            duplicates.append((be_id, seen[be_id], d.name))
        else:
            seen[be_id] = d.name
    assert duplicates == [], f"duplicate BE IDs in roadmaps/: {duplicates}"

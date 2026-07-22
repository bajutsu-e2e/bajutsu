"""Tests for the merge-time renumber guard (BE-0089, BE-0149).

The `roadmap-id` workflow's bypass token can push past `main`'s branch protection, so the legitimate
commit is always the same narrow shape: a `BE-XXXX` → `BE-NNNN` rename, all under `roadmaps/`. The
guard caps the blast radius of that token by failing if the staged commit touches anything outside
`roadmaps/`, and (BE-0149) also runs the roadmap format check over the renumbered tree so a
placeholder that drifted out of shape can't land red on `main`.
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_renumber_diff import _format_problems, disallowed_paths


def test_all_roadmap_paths_are_allowed() -> None:
    paths = [
        "roadmaps/proposals/BE-0090-foo/BE-0090-foo.md",
        "roadmaps/proposals/BE-0090-foo/BE-0090-foo-ja.md",
        "roadmaps/README.md",
        "roadmaps/README-ja.md",
    ]
    assert disallowed_paths(paths) == []


def test_paths_outside_roadmaps_are_reported() -> None:
    paths = [
        "roadmaps/README.md",
        "scripts/allocate_roadmap_ids.py",
        "bajutsu/runner/pool.py",
    ]
    assert disallowed_paths(paths) == ["scripts/allocate_roadmap_ids.py", "bajutsu/runner/pool.py"]


def test_empty_diff_is_allowed() -> None:
    assert disallowed_paths([]) == []


def test_roadmaps_prefix_is_not_a_substring_match() -> None:
    # A top-level path that merely starts with the letters "roadmaps" (no slash) is not inside the
    # roadmaps/ tree, so it must be flagged.
    assert disallowed_paths(["roadmaps-notes.md"]) == ["roadmaps-notes.md"]


def test_format_check_passes_on_the_committed_tree() -> None:
    # The guard's format arm runs the shared stdlib checker over the real roadmaps/ tree; the
    # committed tree is conformant, so it returns no problems. This also proves the lazy sibling
    # import resolves.
    assert _format_problems() == []


def test_format_check_catches_a_malformed_renumbered_item(tmp_path: Path) -> None:
    # The failure path this guard exists for: a renumbered item that drifted out of shape (as
    # BE-0137/BE-0138 did) must be caught here, before the push, not just on a conformant tree.
    item = tmp_path / "proposals" / "BE-0042-a-thing"
    item.mkdir(parents=True)
    (item / "BE-0042-a-thing.md").write_text("not a valid roadmap item\n", encoding="utf-8")
    (item / "BE-0042-a-thing-ja.md").write_text("not a valid roadmap item\n", encoding="utf-8")
    assert _format_problems(tmp_path) != []

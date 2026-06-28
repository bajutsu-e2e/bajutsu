"""Tests for the merge-time renumber guard (BE-0089).

The `roadmap-id` workflow's bypass token can push past `main`'s branch protection, so the legitimate
commit is always the same narrow shape: a `BE-XXXX` → `BE-NNNN` rename plus the regenerated index,
all under `roadmaps/`. The guard caps the blast radius of that token by failing if the staged commit
touches anything outside `roadmaps/`.
"""

from __future__ import annotations

from scripts.check_renumber_diff import disallowed_paths


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

"""Tests for the shared BE-item id-shape predicate (BE-0149).

One home for the ``BE-NNNN`` / ``BE-XXXX`` directory-name shape that four scripts and the format
check used to hardcode independently. These pin the three predicates the callers rely on.
"""

from __future__ import annotations

from scripts.roadmap_ids import (
    PLACEHOLDER,
    is_item_dir,
    is_numbered_dir,
    is_placeholder_dir,
    numbered_match,
)


def test_numbered_dir_is_recognised() -> None:
    assert is_numbered_dir("BE-0149-roadmap-placeholder-format-guardrail")
    assert is_item_dir("BE-0149-roadmap-placeholder-format-guardrail")
    assert not is_placeholder_dir("BE-0149-roadmap-placeholder-format-guardrail")


def test_placeholder_dir_is_recognised() -> None:
    assert is_placeholder_dir(f"{PLACEHOLDER}-some-slug")
    assert is_item_dir(f"{PLACEHOLDER}-some-slug")
    assert not is_numbered_dir(f"{PLACEHOLDER}-some-slug")


def test_numbered_match_exposes_id_and_slug() -> None:
    m = numbered_match("BE-0042-do-a-thing")
    assert m is not None
    assert m.group(1) == "0042"
    assert m.group(2) == "do-a-thing"


def test_non_item_names_are_rejected() -> None:
    # A bare id with no slug, too few/many digits, the placeholder token alone, and plain files are
    # all non-items — the shape requires ``BE-NNNN-<slug>`` or ``BE-XXXX-<slug>``.
    for name in ("README.md", "BE-42-short", "BE-00423-toolong", "notes", "BE-XXXX", "BE-0042"):
        assert not is_item_dir(name), name
        assert numbered_match(name) is None, name

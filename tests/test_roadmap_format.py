"""Gate wrapper for the roadmap (BE) item format check.

The checking logic lives in ``scripts/check_roadmap_format.py`` — a stdlib-only module so the
merge-time allocator (``roadmap-id.yml``) can self-validate before pushing to ``main`` without the
test toolchain (BE-0149). Here we just assert it finds nothing on the committed tree, so ``make
check`` fails on any drift, including a malformed ``BE-XXXX`` placeholder.
"""

from __future__ import annotations

from pathlib import Path

from scripts.check_roadmap_format import format_problems, unresolved_be_xxxx_references

ROADMAP = Path(__file__).resolve().parent.parent / "roadmaps"


def test_no_unresolved_be_xxxx_references() -> None:
    problems = unresolved_be_xxxx_references(ROADMAP)
    assert not problems, (
        "unresolved BE-XXXX reference(s) — replace with the allocated BE-NNNN id:\n"
        + "\n".join(problems)
    )


def test_every_be_item_matches_the_canonical_format() -> None:
    problems = format_problems(ROADMAP)
    assert not problems, "roadmap item format violations:\n" + "\n".join(problems)

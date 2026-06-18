"""Tests for the roadmap-index merge driver (scripts/merge-roadmap-index.py).

When two branches each add a roadmap item, both regenerate the same generated index tables and
collide textually. The driver resolves it by three-way merging the table rows keyed by BE id —
each row is wholly determined by its own item, so a union of base/ours/theirs rows (deletions
removed, sorted by id) is the correct merged table, computed from the index files alone. These
tests pin that row merge: independent adds, deletes, and modifications (BE-0043).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_MODULE_PATH = Path(__file__).resolve().parent.parent / "scripts" / "merge-roadmap-index.py"
_spec = importlib.util.spec_from_file_location("merge_roadmap_index", _MODULE_PATH)
assert _spec and _spec.loader
mri = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = mri
_spec.loader.exec_module(mri)


_HEADER = (
    "## Roadmap\n\nprose\n\n<!-- GENERATED:sec -->\n"
    "| ID | Item | Status |\n|---|---|---|\n{rows}<!-- /GENERATED:sec -->\n\ntail prose\n"
)


def _page(*ids: str) -> str:
    rows = "".join(f"| [{i}](x/{i}.md) | {i} item | Proposal |\n" for i in ids)
    return _HEADER.format(rows=rows)


def _ids(text: str) -> list[str]:
    return [
        line.split("](")[0].split("[")[-1] for line in text.splitlines() if line.startswith("| [BE")
    ]


def test_independent_adds_are_unioned_and_sorted() -> None:
    out = mri.merge(_page("BE-0001"), _page("BE-0001", "BE-0009"), _page("BE-0001", "BE-0005"))
    assert _ids(out) == ["BE-0001", "BE-0005", "BE-0009"]


def test_prose_and_table_scaffolding_preserved() -> None:
    out = mri.merge(_page("BE-0001"), _page("BE-0001", "BE-0009"), _page("BE-0001"))
    assert "## Roadmap" in out and "tail prose" in out
    assert "| ID | Item | Status |" in out and "|---|---|---|" in out


def test_their_delete_is_honored() -> None:
    out = mri.merge(_page("BE-0001", "BE-0002"), _page("BE-0001", "BE-0002"), _page("BE-0002"))
    assert _ids(out) == ["BE-0002"]


def test_our_delete_is_honored() -> None:
    out = mri.merge(_page("BE-0001", "BE-0002"), _page("BE-0002"), _page("BE-0001", "BE-0002"))
    assert _ids(out) == ["BE-0002"]


def test_their_modification_wins_when_we_did_not_touch_it() -> None:
    base = _page("BE-0001")
    theirs = _HEADER.format(rows="| [BE-0001](x/BE-0001.md) | BE-0001 item | Implemented |\n")
    out = mri.merge(base, base, theirs)
    assert "Implemented" in out and _ids(out) == ["BE-0001"]


def test_clean_input_round_trips() -> None:
    # No divergence in the rows: the merged output keeps exactly those rows.
    page = _page("BE-0001", "BE-0002")
    assert _ids(mri.merge(page, page, page)) == ["BE-0001", "BE-0002"]

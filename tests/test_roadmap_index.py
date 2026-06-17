"""Tests for the roadmap index generator (scripts/build_roadmap_index.py).

The generated index is the BE-0043 conflict-resistance mechanism; ``make roadmap-index-check``
guards the real ``docs/roadmap/README*.md`` against drift on every gate run. These tests pin
the generator's logic (status -> label mapping, section detection, the origin column) on
synthetic input so a regression surfaces here rather than as a confusing index diff.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).parent.parent / "scripts" / "build_roadmap_index.py"
_spec = importlib.util.spec_from_file_location("build_roadmap_index", _SCRIPT)
assert _spec and _spec.loader
bri = importlib.util.module_from_spec(_spec)
# Register before exec so @dataclass can resolve the module via sys.modules.
sys.modules[_spec.name] = bri
_spec.loader.exec_module(bri)


def entry(
    id_num: int,
    track: str,
    topic: str,
    label: str = "Implemented",
    origin: str | None = None,
) -> object:
    token = f"BE-{id_num:04d}"
    return bri.Entry(
        id_num=id_num,
        token=token,
        link=f"{token}-x/{token}-x.md",
        title=f"Item {id_num}",
        track=track,
        topic=topic,
        label=label,
        origin=origin,
    )


def test_render_table_three_columns() -> None:
    rows = [entry(2, "Accepted", "T"), entry(1, "Accepted", "T", label="In progress")]
    # render_table filters and is fed already-sorted entries by regenerate; pass sorted here.
    rows.sort(key=lambda e: e.id_num)
    lines = bri.render_table(rows, "en", "Accepted", "T")
    assert lines[0] == "| ID | Item | Status |"
    assert lines[1] == "|---|---|---|"
    assert lines[2] == "| [BE-0001](BE-0001-x/BE-0001-x.md) | Item 1 | In progress |"
    assert lines[3] == "| [BE-0002](BE-0002-x/BE-0002-x.md) | Item 2 | Implemented |"


def test_render_table_origin_column() -> None:
    rows = [entry(5, "Accepted", "C", origin="MagicPod")]
    lines = bri.render_table(rows, "en", "Accepted", "C")
    assert lines[0] == "| ID | Item | Status | Origin |"
    assert lines[-1].endswith("| MagicPod |")


def test_render_table_mixed_origin_is_an_error() -> None:
    rows = [entry(1, "Accepted", "C", origin="Both"), entry(2, "Accepted", "C")]
    with pytest.raises(SystemExit):
        bri.render_table(rows, "en", "Accepted", "C")


def test_regenerate_fills_tables_by_section() -> None:
    index = (
        "## Accepted\n\n### Foo\n\n"
        "| ID | Item | Status |\n|---|---|---|\n| stale row |\n\n"
        "## Proposals\n\n### Bar\n\n"
        "| ID | Item | Status |\n|---|---|---|\n| stale row |\n"
    )
    entries = [
        entry(1, "Accepted", "Foo"),
        entry(2, "Proposals", "Bar", label="Proposal"),
    ]
    out = bri.regenerate(index, "en", entries)
    assert "| [BE-0001](BE-0001-x/BE-0001-x.md) | Item 1 | Implemented |" in out
    assert "| [BE-0002](BE-0002-x/BE-0002-x.md) | Item 2 | Proposal |" in out
    assert "stale row" not in out
    # Prose headings are preserved untouched.
    assert "### Foo" in out and "### Bar" in out


def test_regenerate_errors_when_item_has_no_section() -> None:
    index = "## Accepted\n\n### Foo\n\n| ID | Item | Status |\n|---|---|---|\n| x |\n"
    entries = [entry(1, "Accepted", "Missing")]
    with pytest.raises(SystemExit):
        bri.regenerate(index, "en", entries)

"""How `repl` prints an element tree — the id-first table an operator reads ids out of (BE-0423)."""

from __future__ import annotations

import json
import unicodedata

from bajutsu.common.drivers import base

# The columns, in the order the question is asked: the stable selector first, then the two fields
# that tell apart elements sharing one, then what the element holds and where it sits. These are
# the normalized names a scenario selector matches against, not any backend's own vocabulary.
_COLUMNS = ("id", "label", "traits", "value", "frame")


def render_table(elements: list[base.Element]) -> str:
    """The element tree as an aligned `id` / `label` / `traits` / `value` / `frame` table.

    Cells are never truncated. A shell whose whole purpose is to show what the tree really holds
    would defeat itself by hiding the tail of a long label behind an ellipsis, so a wide row is
    left wide.
    """
    rows = [_cells(el) for el in elements]
    widths = [
        max(_display_width(cell) for cell in column) for column in zip(_COLUMNS, *rows, strict=True)
    ]
    header = [_line(_COLUMNS, widths), _line(tuple("-" * w for w in widths), widths)]
    return "\n".join(header + [_line(row, widths) for row in rows])


def render_json(elements: list[base.Element]) -> str:
    """The tree verbatim, as the backend normalized it — for piping, diffing, or reading a frame exactly."""
    return json.dumps(elements, indent=2, ensure_ascii=False)


def _cells(el: base.Element) -> tuple[str, ...]:
    """One element's cells, in `_COLUMNS` order."""
    return (
        _cell(el["identifier"]),
        _cell(el["label"]),
        _cell(",".join(el["traits"])),
        _cell(el["value"]),
        ",".join(f"{v:g}" for v in el["frame"]),
    )


def _cell(value: str | None) -> str:
    """An absent or empty field reads as `-`; a newline or tab is escaped so it can never split a row."""
    if not value:
        return "-"
    return value.replace("\n", "\\n").replace("\t", "\\t")


def _display_width(text: str) -> int:
    """The terminal columns *text* occupies. A fullwidth or wide character (most CJK text) counts as
    two, so a Japanese label lines its column up with the rest of the table the way it actually
    renders, not the way `len()` counts its characters.
    """
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _line(cells: tuple[str, ...], widths: list[int]) -> str:
    """One padded row; the last column is not padded, so no line carries trailing blanks."""
    # Two spaces between columns: a readable gutter, narrow enough that a wide tree still fits.
    padded = (c + " " * (w - _display_width(c)) for c, w in zip(cells, widths, strict=True))
    return "  ".join(padded).rstrip()

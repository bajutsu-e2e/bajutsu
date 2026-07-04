"""Mechanically fix the narrow shape of roadmap-template drift PR #568 fixed by hand (BE-0149).

``scripts/check_stale_roadmap_prs.py`` re-checks open roadmap PRs against the current template and,
on drift, hands the offending file's text here. This fixes exactly the two shapes the template has
actually drifted by so far (a retired metadata field, a newly-required section): drop any metadata
row whose field is not in the canonical order, then fill in any missing ``## `` section — in its
canonical position, seeded with ``TBD`` (the living-checklist skeleton for ``Progress``).

Anything else — a wrong header link, a malformed id, headings out of canonical order, an extra
non-canonical heading — is left untouched; a human fixes those, the same way PR #568's *other*
review-time signals (item 1) still catch them. This keeps the fixer's blast radius exactly as narrow
as the fix it replaces: two mechanical edits, nothing that requires judgment.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from check_roadmap_format import (
    HEADER_KEYS,
    HEADINGS_EN,
    HEADINGS_JA,
    META_BLOCK_RE,
    META_ROW_RE,
    ORDER_EN,
    ORDER_JA,
    heading_positions,
    is_subsequence,
)
from new_roadmap_item import PROGRESS_BODY_EN, PROGRESS_BODY_JA


def _drop_unknown_fields(text: str, order: list[str]) -> str:
    """Remove any metadata row whose field is not in ``order`` (e.g. the retired ``Track``)."""
    block_match = META_BLOCK_RE.search(text)
    if not block_match:
        return text
    kept: list[str] = []
    changed = False
    for line in block_match.group(1).splitlines():
        row = META_ROW_RE.match(line)
        if row and row.group(1).strip() not in HEADER_KEYS and row.group(1).strip() not in order:
            changed = True
            continue
        kept.append(line)
    if not changed:
        return text
    start, end = block_match.span(1)
    return text[:start] + "\n".join(kept) + text[end:]


def _rebuild_sections(text: str, headings_canon: list[str], body_for: dict[str, str]) -> str:
    """Fill in any missing ``## `` section, in canonical position, seeded from ``body_for``.

    Only acts when the file's current headings are already an ordered subsequence of
    ``headings_canon`` — i.e. the sole defect is one or more *missing* sections, not a reordering or
    a stray non-canonical heading. Anything wider than that is left for a human (item 1 still catches
    it at review time; this fixer targets only the shape PR #568 fixed by hand).
    """
    positions = heading_positions(text)
    current = [name for name, _ in positions]
    if not is_subsequence(current, headings_canon):
        return text
    missing = [h for h in headings_canon if h not in current]
    if not missing or not current:
        # No headings at all is a shape wider than "some missing" — there is no existing section to
        # anchor the rebuild on, so leave it for a human rather than fabricate the whole document.
        return text

    lines = text.splitlines(keepends=True)
    # Each present heading's own text span: from its "## " line up to the next heading's line (or
    # EOF for the last one) — copied verbatim so its body is never touched. Built from the same
    # fence-aware `positions` used for `current`, so the two never disagree about what's a heading.
    spans: dict[str, tuple[int, int]] = {}
    for pos, (name, i) in enumerate(positions):
        end = positions[pos + 1][1] if pos + 1 < len(positions) else len(lines)
        spans[name] = (i, end)

    preamble = "".join(lines[: positions[0][1]])
    rebuilt: list[str] = []
    for name in headings_canon:
        if name in spans:
            rebuilt.append("".join(lines[slice(*spans[name])]))
        else:
            rebuilt.append(f"## {name}\n\n{body_for[name]}\n\n")
    return preamble + "".join(rebuilt).rstrip("\n") + "\n"


def fix_unknown_fields_and_missing_sections(text: str, *, lang: str) -> str:
    """Apply both mechanical fixes for one language file; a no-op if neither shape is present."""
    order = ORDER_JA if lang == "ja" else ORDER_EN
    headings_canon = HEADINGS_JA if lang == "ja" else HEADINGS_EN
    progress_name = "進捗" if lang == "ja" else "Progress"
    progress_body = PROGRESS_BODY_JA if lang == "ja" else PROGRESS_BODY_EN
    body_for = {name: progress_body if name == progress_name else "TBD" for name in headings_canon}

    text = _drop_unknown_fields(text, order)
    return _rebuild_sections(text, headings_canon, body_for)

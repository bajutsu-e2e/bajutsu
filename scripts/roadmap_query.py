#!/usr/bin/env python3
"""Filter roadmap (BE) items by ``Status`` and print one table for an AI session (BE-0162).

``roadmaps/README.md`` is the full index — 700+ lines across four status buckets — so a session
that only needs, say, the open proposals otherwise reads hundreds of irrelevant rows into context.
This projects each item's own metadata (the authoritative source the index generator reads, not the
rendered tables) into just the rows for one status, with the file path to open next::

    python scripts/roadmap_query.py --status Proposal

The query is pure and offline: it reads files under ``roadmaps/`` only — no ``gh``, no network, no
LLM — reusing the metadata parsing ``build_roadmap_index`` already owns rather than adding a second
parser. Placeholders (``BE-XXXX``) are read like any numbered item, so an in-flight proposal shows
up with its placeholder id.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Import the shared metadata parsing and id/tree helpers whether this runs as ``python3 scripts/…``
# (scripts/ already on the path) or is loaded under its bare name by a test — add scripts/ so the
# sibling imports resolve either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_roadmap_index import STATUS_TO_BUCKET, metadata_fields
from roadmap_ids import PLACEHOLDER, iter_item_dirs, numbered_match

ROADMAP = Path("roadmaps")

# The canonical statuses, in lifecycle order (most-progressed first) — the same set the index buckets
# derive from. A CLI argument is matched case-insensitively against these.
VALID_STATUSES: tuple[str, ...] = tuple(STATUS_TO_BUCKET)

# The item's H1 title after the em dash. Accepts only the two valid id shapes — a numbered
# ``BE-NNNN`` or the ``BE-XXXX`` placeholder — so an in-flight item's title still reads while a
# malformed heading fails loudly rather than being silently accepted.
_TITLE_RE = re.compile(r"^# BE-(?:\d{4}|XXXX) — (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Row:
    """One filtered item: its id, title, Topic, and the relative path to open next."""

    id: str
    title: str
    topic: str
    path: str


def resolve_status(raw: str) -> str:
    """Return the canonical status matching ``raw`` case-insensitively.

    Raises:
        ValueError: if ``raw`` is not one of the known statuses; the message names the valid
            values so the caller sees them instead of an empty result.
    """
    for status in VALID_STATUSES:
        if status.casefold() == raw.casefold():
            return status
    valid = ", ".join(VALID_STATUSES)
    raise ValueError(f"unknown status {raw!r}; valid values: {valid}")


def _item_id(dir_name: str) -> str:
    """The id shown for an item directory — its ``BE-NNNN`` number, or the ``BE-XXXX`` placeholder."""
    match = numbered_match(dir_name)
    return f"BE-{match.group(1)}" if match else PLACEHOLDER


def _title(text: str) -> str:
    """The item's title (the H1 text after the em dash); raises if the heading is absent."""
    match = _TITLE_RE.search(text)
    if not match:
        raise ValueError("no '# BE-… — <title>' heading found")
    return match.group(1).strip()


def iter_rows(roadmap: Path, status: str) -> list[Row]:
    """Return the rows for items whose (English) ``Status`` is ``status``, sorted by Topic then id.

    Args:
        roadmap: the ``roadmaps/`` tree to scan.
        status: the status to filter by, matched via :func:`resolve_status`.

    Raises:
        ValueError: if ``status`` is unknown (validated before any file is read).
    """
    canonical = resolve_status(status)
    rows = [
        Row(
            id=_item_id(d.name),
            title=_title(text),
            topic=fields["Topic"],
            path=f"{roadmap.name}/{d.name}/{d.name}.md",
        )
        for d in iter_item_dirs(roadmap)
        if (text := (d / f"{d.name}.md").read_text(encoding="utf-8"))
        and (fields := metadata_fields(text)).get("Status") == canonical
    ]
    return sorted(rows, key=lambda row: (row.topic, row.id))


def render_table(rows: list[Row]) -> str:
    """Render the filtered rows as a Markdown table (header + delimiter + one row per item)."""
    header = "| ID | Item | Topic | Path |"
    delimiter = "|---|---|---|---|"
    body = [f"| {row.id} | {row.title} | {row.topic} | {row.path} |" for row in rows]
    return "\n".join([header, delimiter, *body])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Filter roadmap (BE) items by Status.")
    parser.add_argument(
        "--status",
        required=True,
        help=f"the Status to filter by (case-insensitive); one of: {', '.join(VALID_STATUSES)}",
    )
    parser.add_argument(
        "--roadmap",
        type=Path,
        default=ROADMAP,
        help="the roadmaps/ directory to scan (default: roadmaps)",
    )
    args = parser.parse_args(argv)
    try:
        rows = iter_rows(args.roadmap, args.status)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(render_table(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

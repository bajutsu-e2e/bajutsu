#!/usr/bin/env python3
"""Filter roadmap (BE) items and print one table for an AI session (BE-0162).

The [roadmap dashboard](https://bajutsu-e2e.github.io/bajutsu/api/roadmap.html) lists every item
across all five status buckets, which is more than a session that only needs, say, the open
proposals wants to read into context. This projects each item's own metadata (the authoritative
source the dashboard itself reads) into just the rows the question needs, with the file path to
open next::

    python scripts/roadmap_query.py --status Proposal
    python scripts/roadmap_query.py --grep scroll
    python scripts/roadmap_query.py --status Implemented --topic driver
    python scripts/roadmap_query.py --id BE-0349

``--grep`` answers "is there already an item about X" without a grep over the ~127k lines of item
prose: it matches the id, title, Topic, and the ``## Introduction`` excerpt the dashboard already
derives. The filters compose, and at least one is required — an unfiltered dump of ~380 items is
exactly the context cost this query exists to avoid. The table gains a Status column for the
searches that can span statuses; a ``--status`` query keeps the original four columns.

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
from build_roadmap_index import STATUS_TO_BUCKET, extract_summary, metadata_fields
from roadmap_ids import PLACEHOLDER, iter_item_dirs, numbered_match

ROADMAP = Path("roadmaps")

# The canonical statuses, in lifecycle order (most-progressed first) — the same set the dashboard's
# buckets derive from. A CLI argument is matched case-insensitively against these.
VALID_STATUSES: tuple[str, ...] = tuple(STATUS_TO_BUCKET)

# The item's H1 title after the em dash. Accepts only the two valid id shapes — a numbered
# ``BE-NNNN`` or the ``BE-XXXX`` placeholder — so an in-flight item's title still reads while a
# malformed heading fails loudly rather than being silently accepted.
_TITLE_RE = re.compile(r"^# BE-(?:\d{4}|XXXX) — (.+)$", re.MULTILINE)


@dataclass(frozen=True)
class Row:
    """One filtered item: its id, title, Topic, and the relative path to open next.

    ``status`` carries the item's own ``Status`` for the searches that span several of them
    (``--grep`` / ``--topic`` / ``--id``); a ``--status`` query already knows it, so the table
    leaves the column out there and the field stays empty.
    """

    id: str
    title: str
    topic: str
    path: str
    status: str = ""


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


def resolve_item_id(raw: str) -> str:
    """Return the canonical ``BE-NNNN`` (or the placeholder) for an id written any usual way.

    Accepts ``BE-0349`` / ``be-0349`` / ``0349`` / ``349``, and ``BE-XXXX`` / ``XXXX`` for an
    in-flight item, so a caller can paste whichever form a branch name or a PR title gave them.

    Raises:
        ValueError: if ``raw`` is neither a placeholder nor a number.
    """
    text = raw.strip().upper().removeprefix("BE-")
    if text == "XXXX":
        return PLACEHOLDER
    if not text.isdigit():
        raise ValueError(f"unknown item id {raw!r}; expected BE-NNNN, NNNN, or {PLACEHOLDER}")
    return f"BE-{int(text):04d}"


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


def iter_rows(
    roadmap: Path,
    status: str | None = None,
    *,
    grep: str | None = None,
    topic: str | None = None,
    item_id: str | None = None,
) -> list[Row]:
    """Return the rows for the items matching every filter given, sorted by Topic then id.

    A filter left as ``None`` does not constrain the scan, so passing none of them returns the
    whole roadmap. The filters compose: ``status="Proposal", topic="driver"`` is the intersection.

    Args:
        roadmap: the ``roadmaps/`` tree to scan.
        status: the status to filter by, matched via :func:`resolve_status`.
        grep: a word matched case-insensitively against the item's id, title, Topic, and the
            ``## Introduction`` excerpt :func:`build_roadmap_index.extract_summary` already
            derives — the same text the dashboard shows, so "is there an item about X" is one
            query rather than a grep over 127k lines of item prose.
        topic: matched case-insensitively as a substring of the item's ``Topic``.
        item_id: a single item's id, normalized via :func:`resolve_item_id`.

    Raises:
        ValueError: if ``status`` or ``item_id`` is unparseable (validated before any file is
            read), or if a matched item is malformed — a missing ``Topic`` or an unparseable
            heading. The message names the offending file so a CLI failure is actionable, rather
            than a bare ``KeyError`` with no clue which item is at fault.
    """
    canonical = resolve_status(status) if status is not None else None
    wanted_id = resolve_item_id(item_id) if item_id is not None else None
    needle = grep.casefold() if grep is not None else None
    topic_needle = topic.casefold() if topic is not None else None
    rows: list[Row] = []
    for d in iter_item_dirs(roadmap):
        # The id is in the directory name, so this filter costs no file read.
        if wanted_id is not None and _item_id(d.name) != wanted_id:
            continue
        item = d / f"{d.name}.md"
        try:
            text = item.read_text(encoding="utf-8")
        except OSError as exc:
            raise ValueError(f"{item}: cannot read item file: {exc}") from exc
        fields = metadata_fields(text)
        if canonical is not None and fields.get("Status") != canonical:
            continue
        if "Topic" not in fields:
            raise ValueError(f"{item}: metadata is missing a 'Topic' field")
        if topic_needle is not None and topic_needle not in fields["Topic"].casefold():
            continue
        try:
            title = _title(text)
        except ValueError as exc:
            raise ValueError(f"{item}: {exc}") from exc
        row = Row(
            id=_item_id(d.name),
            title=title,
            topic=fields["Topic"],
            path=f"{roadmap.name}/{d.name}/{d.name}.md",
            status=fields.get("Status", ""),
        )
        if needle is not None and needle not in _haystack(row, text):
            continue
        rows.append(row)
    return sorted(rows, key=lambda row: (row.topic, row.id))


def _haystack(row: Row, text: str) -> str:
    """The searchable text for one item: what a reader would skim before opening the file."""
    return " ".join([row.id, row.title, row.topic, extract_summary(text)]).casefold()


def render_table(rows: list[Row], *, with_status: bool = False) -> str:
    """Render the filtered rows as a Markdown table (header + delimiter + one row per item).

    ``with_status`` adds a Status column, for a query that can span several statuses. A
    ``--status`` query leaves it off: the answer is the same for every row there, and the
    four-column shape is what the ``roadmap-filter`` skill documents.
    """
    cells: list[tuple[str, ...]]
    if with_status:
        header = "| ID | Item | Status | Topic | Path |"
        delimiter = "|---|---|---|---|---|"
        cells = [(r.id, r.title, r.status, r.topic, r.path) for r in rows]
    else:
        header = "| ID | Item | Topic | Path |"
        delimiter = "|---|---|---|---|"
        cells = [(r.id, r.title, r.topic, r.path) for r in rows]
    # An item title or Topic carrying a pipe would shift every column after it.
    body = ["| " + " | ".join(c.replace("|", r"\|") for c in row) + " |" for row in cells]
    return "\n".join([header, delimiter, *body])


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        description="Filter roadmap (BE) items by Status, topic, id, or keyword."
    )
    parser.add_argument(
        "--status",
        help=f"the Status to filter by (case-insensitive); one of: {', '.join(VALID_STATUSES)}",
    )
    parser.add_argument(
        "--grep",
        help="a word matched against each item's id, title, Topic, and Introduction excerpt",
    )
    parser.add_argument("--topic", help="a substring of the item's Topic (case-insensitive)")
    parser.add_argument("--id", dest="item_id", help="one item, as BE-NNNN / NNNN / BE-XXXX")
    parser.add_argument(
        "--roadmap",
        type=Path,
        default=ROADMAP,
        help="the roadmaps/ directory to scan (default: roadmaps)",
    )
    args = parser.parse_args(argv)
    # Refuse an unfiltered scan rather than dumping all ~380 items: the whole point of this query
    # is to keep a session from reading more of the roadmap than its question needs.
    if args.status is None and args.grep is None and args.topic is None and args.item_id is None:
        print(
            "give at least one filter: --status, --grep, --topic, or --id",
            file=sys.stderr,
        )
        return 1
    try:
        rows = iter_rows(
            args.roadmap,
            args.status,
            grep=args.grep,
            topic=args.topic,
            item_id=args.item_id,
        )
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    print(render_table(rows, with_status=args.status is None))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

#!/usr/bin/env python3
"""Generate the roadmap index tables from the per-item ``BE-NNNN`` files.

The roadmap index pages (``docs/roadmap/README.md`` and ``README-ja.md``) used to be
hand-edited: every roadmap PR appended a row to the same topic table, so independent items
collided textually and the index became the single largest merge-conflict source (see
``docs/roadmap/BE-0043-conflict-resistant-file-flow/``). Each ``BE-NNNN/*.md`` file already
carries the metadata a row needs (``Status`` / ``Track`` / ``Topic``), so the tables are
derivable — this script regenerates them and ``make roadmap-index-check`` fails the gate on
drift. A roadmap PR then touches only its own item directory; the index never conflicts.

How it works: each index page is hand-maintained prose *except* the tables under
``## Accepted`` / ``## Proposals``. Every such table corresponds to one ``(track, topic)``
pair — the track is the enclosing ``##`` heading, the topic the enclosing ``###`` heading,
and those headings match each item's ``Track`` / ``Topic`` metadata exactly. The script walks
the file, and for each table block it finds it re-renders the rows for the items in that
section (sorted by ID). Adding an item to an existing topic needs no index edit at all; only a
brand-new topic needs a new ``###`` heading + an (initially placeholder) table by hand.

Run ``scripts/build_roadmap_index.py`` to rewrite the indexes, or ``--check`` to verify they
are up to date (used by the gate and CI).
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROADMAP = Path("docs/roadmap")
NUMBERED_DIR_RE = re.compile(r"^BE-(\d{4})-")
ID_TOKEN_RE = re.compile(r"^(BE-\d{4})")
H1_TITLE_RE = re.compile(r"^#\s+BE-\d{4}\s+—\s+(.*\S)\s*$", re.MULTILINE)

# Per-language field labels in the item metadata blocks and the index page name.
LANGS = {
    "en": {
        "index": "README.md",
        "suffix": "",
        "status": "Status",
        "topic": "Topic",
        "origin": "Origin",
        "sections": {"Accepted": "Accepted", "Proposals": "Proposals"},
        "headers": {
            3: ("| ID | Item | Status |", "|---|---|---|"),
            4: ("| ID | Item | Status | Origin |", "|---|---|---|---|"),
        },
        # Item Status string -> (track, short label shown in the index).
        "status_map": {
            "Implemented": ("Accepted", "Implemented"),
            "Accepted, in progress": ("Accepted", "In progress"),
            "Proposal": ("Proposals", "Proposal"),
            "Proposal (deferred)": ("Proposals", "Deferred"),
        },
    },
    "ja": {
        "index": "README-ja.md",
        "suffix": "-ja",
        "status": "状態",
        "topic": "トピック",
        "origin": "由来",
        "sections": {"可決済み": "Accepted", "提案": "Proposals"},
        "headers": {
            3: ("| ID | 項目 | 状態 |", "|---|---|---|"),
            4: ("| ID | 項目 | 状態 | 由来 |", "|---|---|---|---|"),
        },
        "status_map": {
            "実装済み": ("Accepted", "実装済み"),
            "可決・実装中": ("Accepted", "実装中"),
            "提案": ("Proposals", "提案"),
            "提案（保留）": ("Proposals", "保留"),
        },
    },
}


@dataclass(frozen=True)
class Entry:
    """One item's row data for a single language."""

    id_num: int
    token: str
    link: str
    title: str
    track: str
    topic: str
    label: str
    origin: str | None


def _field(text: str, label: str) -> str | None:
    m = re.search(rf"^\*\s+{re.escape(label)}:\s+(.*\S)\s*$", text, re.MULTILINE)
    return m.group(1).strip() if m else None


def _status_value(raw: str) -> str:
    # Status is bold (``**Implemented**``); strip the emphasis markers.
    return raw.strip().strip("*").strip()


def parse_entry(item_dir: Path, lang: str) -> Entry | None:
    """Read one item's metadata for ``lang``; ``None`` if that language file is missing."""
    cfg = LANGS[lang]
    path = item_dir / f"{item_dir.name}{cfg['suffix']}.md"
    if not path.exists():
        return None
    text = path.read_text(encoding="utf-8")

    token_m = ID_TOKEN_RE.match(item_dir.name)
    title_m = H1_TITLE_RE.search(text)
    status_raw = _field(text, cfg["status"])
    topic = _field(text, cfg["topic"])
    if not (token_m and title_m and status_raw and topic):
        raise SystemExit(f"{path}: missing id / title / {cfg['status']} / {cfg['topic']}")

    status = _status_value(status_raw)
    if status not in cfg["status_map"]:
        raise SystemExit(f"{path}: unknown {cfg['status']} {status!r}")
    track, label = cfg["status_map"][status]

    token = token_m.group(1)
    return Entry(
        id_num=int(NUMBERED_DIR_RE.match(item_dir.name).group(1)),  # type: ignore[union-attr]
        token=token,
        link=f"{item_dir.name}/{item_dir.name}{cfg['suffix']}.md",
        title=title_m.group(1),
        track=track,
        topic=topic,
        label=label,
        origin=_field(text, cfg["origin"]),
    )


def collect_entries(lang: str) -> list[Entry]:
    dirs = sorted(d for d in ROADMAP.iterdir() if d.is_dir() and NUMBERED_DIR_RE.match(d.name))
    entries = [e for d in dirs if (e := parse_entry(d, lang)) is not None]
    return sorted(entries, key=lambda e: e.id_num)


def render_table(entries: list[Entry], lang: str, track: str, topic: str) -> list[str]:
    """The table lines (header + separator + rows) for one ``(track, topic)`` section."""
    rows = [e for e in entries if e.track == track and e.topic == topic]
    if not rows:
        return []
    has_origin = [e.origin is not None for e in rows]
    if any(has_origin) and not all(has_origin):
        raise SystemExit(f"{track}/{topic}: some items set Origin and some do not")
    cols = 4 if all(has_origin) else 3
    header, sep = LANGS[lang]["headers"][cols]
    lines = [header, sep]
    for e in rows:
        cells = [f"[{e.token}]({e.link})", e.title, e.label]
        if cols == 4:
            cells.append(e.origin or "")
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def regenerate(text: str, lang: str, entries: list[Entry]) -> str:
    """Rewrite every table under ``## Accepted`` / ``## Proposals`` from ``entries``."""
    cfg = LANGS[lang]
    lines = text.splitlines()
    out: list[str] = []
    track: str | None = None
    topic: str | None = None
    covered: set[tuple[str, str]] = set()

    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("## "):
            track = cfg["sections"].get(line[3:].strip())
            topic = None
        elif line.startswith("### "):
            topic = line[4:].strip()

        if track and topic and line.lstrip().startswith("|"):
            # Replace this whole table block (consecutive table rows) with fresh output.
            j = i
            while j < len(lines) and lines[j].lstrip().startswith("|"):
                j += 1
            out.extend(render_table(entries, lang, track, topic))
            covered.add((track, topic))
            i = j
            continue

        out.append(line)
        i += 1

    missing = [e for e in entries if (e.track, e.topic) not in covered]
    if missing:
        detail = ", ".join(f"{e.token} ({e.track}/{e.topic})" for e in missing)
        raise SystemExit(f"{cfg['index']}: no index table for: {detail}")

    return "\n".join(out) + ("\n" if text.endswith("\n") else "")


def build(check: bool) -> int:
    drift = False
    for lang, cfg in LANGS.items():
        index = ROADMAP / cfg["index"]
        current = index.read_text(encoding="utf-8")
        generated = regenerate(current, lang, collect_entries(lang))
        if generated == current:
            continue
        if check:
            drift = True
            diff = difflib.unified_diff(
                current.splitlines(keepends=True),
                generated.splitlines(keepends=True),
                fromfile=f"{cfg['index']} (committed)",
                tofile=f"{cfg['index']} (generated)",
            )
            sys.stderr.write("".join(diff))
        else:
            index.write_text(generated, encoding="utf-8")
            print(f"Regenerated {cfg['index']}")

    if check and drift:
        sys.stderr.write(
            "\nRoadmap index is out of date. Run `make roadmap-index` and commit the result.\n"
        )
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify the committed indexes are up to date (no writes); exit 1 on drift",
    )
    args = parser.parse_args()
    return build(check=args.check)


if __name__ == "__main__":
    sys.exit(main())

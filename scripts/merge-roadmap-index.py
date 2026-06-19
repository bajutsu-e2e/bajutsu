#!/usr/bin/env python3
"""Git merge driver for the generated roadmap index pages (BE-0043).

``roadmaps/README.md`` and ``README-ja.md`` carry marker-delimited tables that are
GENERATED from each ``BE-NNNN`` item's metadata (``scripts/build_roadmap_index.py``). Two
branches that each add a roadmap item regenerate the same table and collide textually, even
though the items are independent.

Rather than line-merge the tables (which conflicts) or regenerate them from disk (which is
wrong mid-merge — the other side's new ``BE-*/`` directory is not on disk yet when git invokes
this driver, so its row would be dropped), this resolves the index **from the index files
themselves**: a three-way merge of the table rows keyed by their BE ID. Each row is wholly
determined by its own item, so a union of the rows — base deletions removed, sorted by ID — is
the correct merged table, needing nothing but the three index versions git already hands us.

The prose outside the ``<!-- GENERATED:key -->`` markers, and each table's header/separator,
are taken from our side. On the rare same-ID/different-content add (the item file itself also
conflicts and is resolved by hand), ours wins here and the post-resolution
``make roadmap-index`` / CI ``--check`` reconciles it.

Usage (wired as the ``roadmap-index`` merge driver by ``make hooks`` / ``make setup``)::

    merge-roadmap-index.py %O %A %B   # base, ours, theirs; result written back to ours (%A)
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

BEGIN_RE = re.compile(r"^<!-- GENERATED:([\w-]+) -->\s*$")
END_RE = re.compile(r"^<!-- /GENERATED:([\w-]+) -->\s*$")
ROW_RE = re.compile(r"^\|\s*\[?(BE-\d{4})\b")  # a data row starts with its linked BE id


def _regions(text: str) -> dict[str, list[str]]:
    """Map each GENERATED:key region to its list of data-row lines (header/separator excluded)."""
    rows: dict[str, list[str]] = {}
    key: str | None = None
    for line in text.splitlines():
        if key is None:
            m = BEGIN_RE.match(line)
            if m:
                key = m.group(1)
                rows[key] = []
            continue
        if END_RE.match(line):
            key = None
            continue
        if ROW_RE.match(line):
            rows[key].append(line)
    return rows


def _row_id(line: str) -> str:
    m = ROW_RE.match(line)
    return m.group(1) if m else line


def _merge_rows(base: list[str], ours: list[str], theirs: list[str]) -> list[str]:
    """Three-way merge a table's rows keyed by BE id, sorted by id."""
    b = {_row_id(r): r for r in base}
    o = {_row_id(r): r for r in ours}
    t = {_row_id(r): r for r in theirs}
    out: dict[str, str] = {}
    for rid in set(o) | set(t):
        ro, rt, rb = o.get(rid), t.get(rid), b.get(rid)
        if ro is not None and rt is not None:
            # Present on both sides: take the side that changed it (else either; ours on clash).
            if ro == rt or rt == rb:
                out[rid] = ro
            elif ro == rb:
                out[rid] = rt
            else:
                out[rid] = ro
        # Present on only one side: keep it if that side *added* it (absent from base);
        # if it was in base, the other side deleted it, so drop it.
        elif rb is None:
            out[rid] = ro if ro is not None else rt
    return [out[rid] for rid in sorted(out)]


def merge(base: str, ours: str, theirs: str) -> str:
    """Rebuild *ours* with each generated table's rows three-way merged from base/ours/theirs."""
    base_rows, ours_rows, theirs_rows = _regions(base), _regions(ours), _regions(theirs)
    out: list[str] = []
    key: str | None = None
    emitted = False  # whether the merged rows for the current region were written yet
    for line in ours.splitlines():
        if key is None:
            out.append(line)
            m = BEGIN_RE.match(line)
            if m:
                key, emitted = m.group(1), False
            continue
        if END_RE.match(line):
            key = None
            out.append(line)
            continue
        if ROW_RE.match(line):
            if not emitted:
                out.extend(
                    _merge_rows(
                        base_rows.get(key, []),
                        ours_rows.get(key, []),
                        theirs_rows.get(key, []),
                    )
                )
                emitted = True
            continue  # drop ours' original rows; merged rows replace them
        out.append(line)  # header / separator / blank lines inside the region
    trailing = "\n" if ours.endswith("\n") else ""
    return "\n".join(out) + trailing


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: merge-roadmap-index.py %O %A %B", file=sys.stderr)
        return 2
    base_p, ours_p, theirs_p = (Path(a) for a in argv)
    merged = merge(
        base_p.read_text(encoding="utf-8"),
        ours_p.read_text(encoding="utf-8"),
        theirs_p.read_text(encoding="utf-8"),
    )
    ours_p.write_text(merged, encoding="utf-8")  # git takes the resolved file from %A
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

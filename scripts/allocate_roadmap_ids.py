#!/usr/bin/env python3
"""Allocate real BE IDs to placeholder roadmap items.

The ``ideation`` skill drafts new roadmap items with the literal placeholder
ID ``BE-XXXX`` so authors never guess a number — IDs are permanent and monotonic, and
picking by hand races between concurrent branches. This script, run by the ``roadmap-id``
workflow on a pull request, allocates the next free IDs deterministically and rewrites the
directories, files, and index tables.

For each ``docs/roadmap/BE-XXXX-<slug>/`` placeholder (sorted by slug, so the order is
stable across runs and machines) it:

1. allocates the next ID — ``max existing BE-NNNN + 1``, incrementing per item;
2. ``git mv``\\ s the directory and its files, replacing ``BE-XXXX`` with ``BE-NNNN``;
3. rewrites ``BE-XXXX`` -> ``BE-NNNN`` inside those files;
4. regenerates the index tables in ``README.md`` / ``README-ja.md`` from the now-numbered
   item metadata via :mod:`build_roadmap_index` — the index is a generated artifact (BE-0043),
   so newly-allocated items land in their topic table automatically and authors never
   hand-edit a row.

It is a no-op when no placeholders are present. Limitation: a new item must not
cross-reference another new item by ``BE-XXXX`` — the in-file rewrite is per item, so it
would resolve to the wrong number.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import build_roadmap_index

ROADMAP = Path("docs/roadmap")
PLACEHOLDER = "BE-XXXX"
NUMBERED_DIR_RE = re.compile(r"^BE-(\d{4})-")


def existing_max_id() -> int:
    """Highest already-allocated BE number, or 0 if there are none."""
    ids = [
        int(m.group(1))
        for d in ROADMAP.iterdir()
        if d.is_dir() and (m := NUMBERED_DIR_RE.match(d.name))
    ]
    return max(ids, default=0)


def placeholder_dirs() -> list[Path]:
    """Placeholder item directories, sorted by name for deterministic allocation."""
    return sorted(
        d for d in ROADMAP.iterdir() if d.is_dir() and d.name.startswith(f"{PLACEHOLDER}-")
    )


def git_mv(src: Path, dst: Path) -> None:
    subprocess.run(["git", "mv", str(src), str(dst)], check=True)


def allocate() -> list[tuple[str, str]]:
    """Rename placeholder items and return the (slug, new-id-token) allocations."""
    next_id = existing_max_id() + 1
    allocations: list[tuple[str, str]] = []

    for src_dir in placeholder_dirs():
        slug = src_dir.name[len(PLACEHOLDER) + 1 :]
        new_token = f"BE-{next_id:04d}"
        new_dir = ROADMAP / f"{new_token}-{slug}"

        git_mv(src_dir, new_dir)
        for f in sorted(new_dir.iterdir()):
            renamed = f.name.replace(PLACEHOLDER, new_token)
            if renamed != f.name:
                git_mv(f, new_dir / renamed)
        for f in sorted(new_dir.iterdir()):
            text = f.read_text(encoding="utf-8")
            f.write_text(text.replace(PLACEHOLDER, new_token), encoding="utf-8")

        allocations.append((slug, new_token))
        print(f"Allocated {new_token} for {slug}")
        next_id += 1

    return allocations


def main() -> int:
    allocations = allocate()
    if not allocations:
        print("No BE-XXXX placeholder items found; nothing to allocate.")
        return 0
    # The index is generated from item metadata, so regenerate rather than patch rows.
    return build_roadmap_index.build(check=False)


if __name__ == "__main__":
    sys.exit(main())

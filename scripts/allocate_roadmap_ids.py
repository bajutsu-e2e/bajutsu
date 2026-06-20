#!/usr/bin/env python3
"""Allocate real BE IDs to placeholder roadmap items.

The ``ideation`` skill drafts new roadmap items with the literal placeholder
ID ``BE-XXXX`` so authors never guess a number — IDs are permanent and monotonic, and
picking by hand races between concurrent branches. This script, run by the ``roadmap-id``
workflow on a pull request, allocates the next free IDs deterministically and rewrites the
directories, files, and index tables.

For each ``roadmaps/proposals/BE-XXXX-<slug>/`` placeholder (sorted by slug, so the order is
stable across runs and machines) it:

1. allocates the next ID — the smallest free number above every ID in the working tree
   **and on ``origin/main``**, incrementing per item (so a branch cut from an older main
   can't reuse a number a parallel branch has since merged);
2. ``git mv``\\ s the directory and its files, replacing ``BE-XXXX`` with ``BE-NNNN``;
3. rewrites ``BE-XXXX`` -> ``BE-NNNN`` inside those files;
4. fixes the index-table rows in ``README.md`` / ``README-ja.md`` — any line that
   references the unique path ``BE-XXXX-<slug>`` gets its ``BE-XXXX`` rewritten, which
   covers both the link text and the path.

It is a no-op when no placeholders are present. Limitation: a new item must not
cross-reference another new item by ``BE-XXXX`` — the in-file rewrite is per item, so it
would resolve to the wrong number.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROADMAP = Path("roadmaps")
# New items are proposals, so placeholders land here; IDs are global across both subdirs.
CATEGORIES = ("implemented", "proposals")
PLACEHOLDER_CATEGORY = "proposals"
PLACEHOLDER = "BE-XXXX"
NUMBERED_DIR_RE = re.compile(r"^BE-(\d{4})-")
PATH_ID_RE = re.compile(r"/BE-(\d{4})-")  # an item's id as it appears inside a git path
INDEX_FILES = ("README.md", "README-ja.md")


def working_tree_ids() -> set[int]:
    """BE numbers present in the working tree, across both categories."""
    return {
        int(m.group(1))
        for category in CATEGORIES
        if (ROADMAP / category).is_dir()
        for d in (ROADMAP / category).iterdir()
        if d.is_dir() and (m := NUMBERED_DIR_RE.match(d.name))
    }


def ids_on_git_ref(ref: str) -> set[int]:
    """BE numbers present under ``roadmaps/`` on a git ref; empty if the ref is unavailable.

    A PR cut from an older ``main`` would otherwise recompute ``max + 1`` from a stale view and
    re-hand-out a number a parallel branch has already merged — the exact race that produced a
    duplicate BE-0045. Folding in ``origin/main`` closes that window. Degrades to empty when the
    ref or git is unavailable (e.g. a shallow checkout), so local runs still work.
    """
    try:
        out = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", ref, "--", str(ROADMAP)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return set()
    return {int(m.group(1)) for line in out.splitlines() if (m := PATH_ID_RE.search(line))}


def used_ids() -> set[int]:
    """Every BE number to steer clear of: the working tree plus ``origin/main`` (best effort)."""
    return working_tree_ids() | ids_on_git_ref("origin/main")


def placeholder_dirs() -> list[Path]:
    """Placeholder item directories, sorted by name for deterministic allocation."""
    proposals = ROADMAP / PLACEHOLDER_CATEGORY
    if not proposals.is_dir():
        return []
    return sorted(
        d for d in proposals.iterdir() if d.is_dir() and d.name.startswith(f"{PLACEHOLDER}-")
    )


def git_mv(src: Path, dst: Path) -> None:
    subprocess.run(["git", "mv", str(src), str(dst)], check=True)


def allocate() -> list[tuple[str, str]]:
    """Rename placeholder items and return the (slug, new-id-token) allocations."""
    used = used_ids()
    next_id = max(used, default=0) + 1
    allocations: list[tuple[str, str]] = []

    for src_dir in placeholder_dirs():
        slug = src_dir.name[len(PLACEHOLDER) + 1 :]
        while next_id in used:  # never hand out a number already taken (defence in depth)
            next_id += 1
        new_token = f"BE-{next_id:04d}"
        new_dir = ROADMAP / PLACEHOLDER_CATEGORY / f"{new_token}-{slug}"
        if new_dir.exists():
            raise SystemExit(f"refusing to allocate {new_token}: {new_dir} already exists")

        git_mv(src_dir, new_dir)
        for f in sorted(new_dir.iterdir()):
            renamed = f.name.replace(PLACEHOLDER, new_token)
            if renamed != f.name:
                git_mv(f, new_dir / renamed)
        for f in sorted(new_dir.iterdir()):
            text = f.read_text(encoding="utf-8")
            f.write_text(text.replace(PLACEHOLDER, new_token), encoding="utf-8")

        used.add(next_id)
        allocations.append((slug, new_token))
        print(f"Allocated {new_token} for {slug}")
        next_id += 1

    return allocations


def rewrite_index_tables(allocations: list[tuple[str, str]]) -> None:
    """Renumber the index-table rows, keyed by the slug-qualified path (unique per item)."""
    for name in INDEX_FILES:
        index = ROADMAP / name
        lines = index.read_text(encoding="utf-8").splitlines(keepends=True)
        for i, line in enumerate(lines):
            for slug, new_token in allocations:
                if f"{PLACEHOLDER}-{slug}" in line:
                    lines[i] = lines[i].replace(PLACEHOLDER, new_token)
        index.write_text("".join(lines), encoding="utf-8")


def main() -> int:
    allocations = allocate()
    if not allocations:
        print("No BE-XXXX placeholder items found; nothing to allocate.")
        return 0
    rewrite_index_tables(allocations)
    return 0


if __name__ == "__main__":
    sys.exit(main())

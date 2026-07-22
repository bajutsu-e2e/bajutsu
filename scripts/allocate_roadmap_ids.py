#!/usr/bin/env python3
"""Allocate permanent BE IDs for roadmap items.

The ``ideation`` skill drafts new roadmap items with the literal placeholder ID ``BE-XXXX`` so
authors never guess a number — IDs are permanent and monotonic, and picking by hand races between
concurrent branches. This script, run by the ``roadmap-id`` workflow after a roadmap PR merges to
``main``, turns each placeholder into the next free ``BE-NNNN``. For each ``BE-XXXX-<slug>``
placeholder directly under ``roadmaps/`` (BE-0159 flattened the status folders away) — sorted by
slug, so the order is stable across runs and machines — it:

1. allocates the next ID — the smallest free number above every ID already taken (see ``used_ids``),
   incrementing per item;
2. ``git mv``\\ s the directory and its files, replacing ``BE-XXXX`` with ``BE-NNNN``;
3. rewrites ``BE-XXXX`` -> ``BE-NNNN`` inside those files.

Allocation runs on ``main`` in merge order, so the ``BE-NNNN`` sequence is contiguous by
construction and a number is never burned by a rejected proposal (BE-0089). It is a no-op when there
is no placeholder. Limitation: a new item must not cross-reference another *new* item by ``BE-XXXX``
(the rewrite is per item).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Import the shared id-shape predicate whether this file is run as ``python3 scripts/…`` (scripts/
# already on the path) or loaded under its bare name by a test — add scripts/ so the sibling import
# resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from roadmap_ids import PLACEHOLDER, is_placeholder_dir, iter_item_dirs, numbered_match

ROADMAP = Path("roadmaps")
PATH_ITEM_RE = re.compile(r"/BE-(\d{4})-[^/]+/")  # id of an item's directory in a path


def working_tree_ids() -> set[int]:
    """BE numbers present in the working tree."""
    return {int(m.group(1)) for d in iter_item_dirs(ROADMAP) if (m := numbered_match(d.name))}


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
    return {int(m.group(1)) for line in out.splitlines() if (m := PATH_ITEM_RE.search(line))}


def used_ids() -> set[int]:
    """Every BE number to steer clear of: the working tree and ``origin/main`` (best effort)."""
    return working_tree_ids() | ids_on_git_ref("origin/main")


def placeholder_dirs() -> list[Path]:
    """Placeholder item directories, sorted by directory name (the slug) for deterministic allocation.

    ``iter_item_dirs`` yields the flat tree sorted by name, so allocation order is a stable
    function of the slug across runs and machines (BE-0159).
    """
    return sorted(
        (d for d in iter_item_dirs(ROADMAP) if is_placeholder_dir(d.name)),
        key=lambda d: d.name,
    )


def git_mv(src: Path, dst: Path) -> None:
    subprocess.run(["git", "mv", str(src), str(dst)], check=True)


def rename_item(item_dir: Path, old_token: str, new_token: str) -> Path:
    """``git mv`` an item's directory and files from ``old_token`` to ``new_token`` and rewrite
    the token inside the item's own files; return the new directory.

    ``old_token`` is the ``BE-XXXX`` placeholder. Rewriting every occurrence inside the item's *own*
    files is safe because those files refer to the item by its id.
    """
    slug = item_dir.name[len(old_token) + 1 :]
    new_dir = item_dir.parent / f"{new_token}-{slug}"
    if new_dir.exists():
        raise SystemExit(f"refusing to assign {new_token}: {new_dir} already exists")
    git_mv(item_dir, new_dir)
    for f in sorted(new_dir.iterdir()):
        renamed = f.name.replace(old_token, new_token)
        if renamed != f.name:
            git_mv(f, new_dir / renamed)
    for f in sorted(new_dir.iterdir()):
        text = f.read_text(encoding="utf-8")
        f.write_text(text.replace(old_token, new_token), encoding="utf-8")
    return new_dir


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
        rename_item(src_dir, PLACEHOLDER, new_token)
        used.add(next_id)
        allocations.append((slug, new_token))
        print(f"Allocated {new_token} for {slug}")
        next_id += 1

    return allocations


def main() -> int:
    if not allocate():
        print("No BE-XXXX placeholder items found; nothing to allocate.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

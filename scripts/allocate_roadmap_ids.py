#!/usr/bin/env python3
"""Allocate — and repair — permanent BE IDs for roadmap items.

The ``ideation`` skill drafts new roadmap items with the literal placeholder
ID ``BE-XXXX`` so authors never guess a number — IDs are permanent and monotonic, and
picking by hand races between concurrent branches. This script has two jobs:

**Allocate** (default), run by the ``roadmap-id`` workflow on a pull request. For each
``roadmaps/proposals/BE-XXXX-<slug>/`` placeholder (sorted by slug, so the order is stable
across runs and machines) it:

1. allocates the next ID — the smallest free number above every ID already taken (see
   ``used_ids``), incrementing per item;
2. ``git mv``\\ s the directory and its files, replacing ``BE-XXXX`` with ``BE-NNNN``;
3. rewrites ``BE-XXXX`` -> ``BE-NNNN`` inside those files;
4. fixes the index-table rows in ``README.md`` / ``README-ja.md`` — any line that
   references the unique path ``BE-XXXX-<slug>`` gets its ``BE-XXXX`` rewritten, which
   covers both the link text and the path.

**Repair** (``--repair``), run by the ``roadmap-id-repair`` workflow when a roadmap PR merges to
``main`` and on a schedule, against every open PR that also updates the roadmap. Allocation avoids
the IDs in the working tree, on ``origin/main``, and on other open PRs (passed in via
``ROADMAP_RESERVED_IDS``); the workflow also claims each allocated ID atomically as a
``refs/be-claims/*`` git ref, so two branches allocating in the same window cannot both take a
number. Repair is the backstop for whatever still slips past that — a hand-typed concrete ID, or a
branch that predates the machinery: for an item the branch *introduces* (a slug not yet on ``main``)
whose ``BE-NNNN`` is already taken, it allocates the next free ID, rewriting the directory, files,
self-references, the slug-qualified cross-references in other items, and the index. Authority — who
keeps a contested number — is ``origin/main`` first (a merged item always wins), else the **lowest
open-PR number** holding it (passed in via ``ROADMAP_LOWER_PR_IDS``); this branch moves only when it
is not the authority. An item whose slug is already on ``main`` is one the branch inherited (a stale
view ``main`` may have renumbered); that is a rebase, never a renumber here.

Both modes are a no-op when there is nothing to do. Limitations: a new item must not
cross-reference another *new* item by ``BE-XXXX`` (the allocate rewrite is per item); and
repair rewrites only references pinned to the renumbered item by its slug — a bare ``BE-NNNN``
prose mention elsewhere is now ambiguous with ``main``'s item and is left for human review.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from collections.abc import Set as AbstractSet
from pathlib import Path

ROADMAP = Path("roadmaps")
# New items are proposals, so placeholders land here; IDs are global across all status folders.
CATEGORIES = ("implemented", "in-progress", "proposals", "deferred")
PLACEHOLDER_CATEGORY = "proposals"
PLACEHOLDER = "BE-XXXX"
NUMBERED_DIR_RE = re.compile(r"^BE-(\d{4})-(.+)$")
PATH_ITEM_RE = re.compile(r"/BE-(\d{4})-([^/]+)/")  # id + slug of an item's directory in a path
INDEX_FILES = ("README.md", "README-ja.md")
# IDs other open PRs have already allocated but not yet merged. The script can't see GitHub,
# so the workflow lists them (see scripts/open_pr_be_ids.sh) and passes them in here.
RESERVED_IDS_ENV = "ROADMAP_RESERVED_IDS"
# IDs held by open PRs with a *lower* PR number than the one being repaired. When a number is
# contested only between open PRs (none on main yet), the lowest PR number is the authority and
# keeps it; a branch renumbers only when a lower-numbered PR also holds its id. The workflow knows
# every open PR's number and passes the lower-numbered holders' ids here.
LOWER_PR_IDS_ENV = "ROADMAP_LOWER_PR_IDS"
BUILD_INDEX = Path(__file__).resolve().parent / "build_roadmap_index.py"


def working_tree_ids() -> set[int]:
    """BE numbers present in the working tree, across all status folders."""
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
    return set(ids_to_slugs_on_git_ref(ref))


def ids_to_slugs_on_git_ref(ref: str) -> dict[int, str]:
    """``{id: slug}`` for the items under ``roadmaps/`` on a git ref; empty if unavailable.

    Repair needs the slug, not just the number: a working-tree ``BE-NNNN`` only collides if the
    ``BE-NNNN`` on ``main`` is a *different* item, and slug is what tells them apart. Degrades to
    an empty mapping when the ref or git is unavailable, same as the id-only view.
    """
    try:
        out = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", ref, "--", str(ROADMAP)],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except (subprocess.CalledProcessError, FileNotFoundError):
        return {}
    return {
        int(m.group(1)): m.group(2) for line in out.splitlines() if (m := PATH_ITEM_RE.search(line))
    }


def reserved_ids_from_env() -> set[int]:
    """BE numbers other open PRs have already allocated, passed in by the workflow.

    The script can't see GitHub; the roadmap-id / roadmap-id-repair workflows list open PRs and
    export their roadmap BE IDs here (any non-digit separator works, ``BE-`` prefixes welcome),
    so a number a parallel branch has allocated but not yet merged is still avoided — the
    BE-0054 double-allocation. Unset/empty (local runs) degrades to no reservations, like the
    ``origin/main`` lookup.
    """
    raw = os.environ.get(RESERVED_IDS_ENV, "")
    return {int(m.group()) for m in re.finditer(r"\d+", raw)}


def lower_pr_ids_from_env() -> set[int]:
    """BE numbers held by open PRs with a lower PR number than the one being repaired.

    Repair's authority rule when a number is contested only between open PRs (none on ``main``): the
    lowest PR number keeps it. The workflow knows every open PR's number and exports the ids of the
    lower-numbered holders here, so a branch can tell whether it is the authority for its id or must
    move off it. Unset/empty (local runs) degrades to "no lower claimant", like the reservations.
    """
    raw = os.environ.get(LOWER_PR_IDS_ENV, "")
    return {int(m.group()) for m in re.finditer(r"\d+", raw)}


def used_ids() -> set[int]:
    """Every BE number to steer clear of: the working tree, ``origin/main``, and the IDs other
    open PRs have already allocated (each best effort)."""
    return working_tree_ids() | ids_on_git_ref("origin/main") | reserved_ids_from_env()


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


def regenerate_index() -> None:
    """Rebuild the index tables from each item's metadata (repair changes an item's id/path)."""
    subprocess.run([sys.executable, str(BUILD_INDEX)], check=True)


def rename_item(item_dir: Path, old_token: str, new_token: str) -> Path:
    """``git mv`` an item's directory and files from ``old_token`` to ``new_token`` and rewrite
    the token inside the item's own files; return the new directory.

    Shared by allocate (``old_token`` is the ``BE-XXXX`` placeholder) and repair (``old_token``
    is the colliding concrete id). Rewriting every occurrence inside the item's *own* files is
    safe because those files refer to the item by its id; a reference to a *different* item that
    happens to share the old number is the documented edge.
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


def colliding_items(
    roadmap: Path, main_ids: dict[int, str], lower_pr_ids: AbstractSet[int] = frozenset()
) -> list[tuple[int, str, Path]]:
    """Items this branch *introduces* whose BE number a more authoritative holder already owns.

    Returns ``(id, slug, directory)`` for each, sorted by category then name (deterministic). The
    branch's own items are the ones whose slug is absent from ``main``: a slug is unique and
    permanent per item, so a slug not yet on ``main`` marks a new item this branch is adding. Such an
    item collides — and must move — when its number is already taken by a holder that outranks this
    branch: ``main`` (a merged item always wins) or a lower-numbered open PR (``lower_pr_ids``, the
    tiebreaker when neither is merged). An item whose slug *is* on ``main`` is one the branch
    inherited — possibly a stale view ``main`` has since renumbered — which is resolved by rebasing,
    never by renumbering here, so it is left alone.
    """
    main_slugs = set(main_ids.values())
    found: list[tuple[int, str, Path]] = []
    for category in CATEGORIES:
        category_dir = roadmap / category
        if not category_dir.is_dir():
            continue
        for d in sorted(category_dir.iterdir()):
            if not (d.is_dir() and (m := NUMBERED_DIR_RE.match(d.name))):
                continue
            be_id, slug = int(m.group(1)), m.group(2)
            if slug in main_slugs:  # inherited from main -> a rebase, never a renumber here
                continue
            if be_id in main_ids or be_id in lower_pr_ids:
                found.append((be_id, slug, d))
    return found


def rewrite_cross_references(roadmap: Path, old_token: str, slug: str, new_token: str) -> None:
    """Renumber references to the moved item across every roadmap markdown file.

    Keyed per line by the slug-qualified directory token ``old_token-slug``: a line that names it
    is unambiguously about this item, so every ``old_token`` on that line — the link path *and*
    its display text — is rewritten. A bare ``old_token`` on a line that does *not* name the slug
    is now ambiguous with ``main``'s item and is left untouched.
    """
    qualified = f"{old_token}-{slug}"
    for f in sorted(roadmap.rglob("*.md")):
        lines = f.read_text(encoding="utf-8").splitlines(keepends=True)
        changed = False
        for i, line in enumerate(lines):
            if qualified in line:
                lines[i] = line.replace(old_token, new_token)
                changed = True
        if changed:
            f.write_text("".join(lines), encoding="utf-8")


def repair() -> list[tuple[str, str]]:
    """Renumber working-tree items whose id ``origin/main`` — or a lower-numbered open PR — holds.

    Returns the ``(old-token, new-token)`` remaps so the workflow can rewrite the PR title.
    """
    main_ids = ids_to_slugs_on_git_ref("origin/main")
    collisions = colliding_items(ROADMAP, main_ids, lower_pr_ids_from_env())
    if not collisions:
        return []

    used = used_ids()
    next_id = max(used, default=0) + 1
    remaps: list[tuple[str, str]] = []

    for be_id, slug, item_dir in collisions:
        while next_id in used:
            next_id += 1
        old_token = f"BE-{be_id:04d}"
        new_token = f"BE-{next_id:04d}"
        rename_item(item_dir, old_token, new_token)
        rewrite_cross_references(ROADMAP, old_token, slug, new_token)
        used.add(next_id)
        remaps.append((old_token, new_token))
        print(f"Renumbered {old_token} -> {new_token} ({slug})")
        next_id += 1

    regenerate_index()
    return remaps


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repair",
        action="store_true",
        help="renumber working-tree items whose id origin/main now gives a different item",
    )
    args = parser.parse_args(argv)

    if args.repair:
        if not repair():
            print("No BE IDs collide with origin/main; nothing to repair.")
        return 0

    allocations = allocate()
    if not allocations:
        print("No BE-XXXX placeholder items found; nothing to allocate.")
        return 0
    rewrite_index_tables(allocations)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

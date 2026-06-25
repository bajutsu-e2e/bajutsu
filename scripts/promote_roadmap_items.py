#!/usr/bin/env python3
"""Move roadmap items so each item's directory matches its ``Status``.

CLAUDE.md / docs: ``Status`` is the single source of truth for which subdirectory an item belongs
in, and each of the four status values has its own folder (BE-0078) — a bijection, so the folder
and the status can never disagree:

    Status: Implemented          -> roadmaps/implemented/
    Status: In progress          -> roadmaps/in-progress/
    Status: Proposal             -> roadmaps/proposals/
    Status: Proposal (deferred)  -> roadmaps/deferred/

This script reconciles the directory with the Status: it ``git mv``\\ s any item filed under the
wrong subdirectory and regenerates the index (a move changes the ``category`` prefix in every
link the index renders to that item, so the tables must be rebuilt). The ``roadmap-promote``
workflow runs it on a pull request and pushes the result back onto the branch; ``make
roadmap-promote`` runs it locally. The same invariant is a gate test
(``tests/test_promote_roadmap_items.py``), so a Status/directory mismatch fails ``make test``
even when the workflow cannot run (e.g. a fork PR, which the bot cannot push to).

Usage::

    python scripts/promote_roadmap_items.py            # move misfiled items + reindex
    python scripts/promote_roadmap_items.py --check     # exit 1 if any item is misfiled

It is a no-op when every item is already filed by its Status. Limitation: the English file's
``Status`` is the trigger; the English and Japanese ``Status`` are assumed consistent (the same
assumption the index generator makes).
"""

from __future__ import annotations

import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

ROADMAP = Path("roadmaps")
IMPLEMENTED = "implemented"
IN_PROGRESS = "in-progress"
PROPOSALS = "proposals"
DEFERRED = "deferred"
CATEGORIES = (IMPLEMENTED, IN_PROGRESS, PROPOSALS, DEFERRED)  # each item lives under exactly one
# Status -> its one folder (BE-0078). An unrecognised status files under proposals/ rather than
# crashing the move; the format gate (test_roadmap_format) is what rejects an unknown status.
STATUS_TO_CATEGORY = {
    "Implemented": IMPLEMENTED,
    "In progress": IN_PROGRESS,
    "Proposal": PROPOSALS,
    "Proposal (deferred)": DEFERRED,
}
NUMBERED_DIR_RE = re.compile(r"^BE-\d{4}-")
# Status lives in the fenced metadata block (``build_roadmap_index`` defines the same fence). Read
# the ``| Status | … |`` row there; fall back to the legacy ``* Status: …`` bullet for unmigrated
# items. Scoping to the fence keeps a body table that happens to mention "Status" out of reach.
META_BLOCK_RE = re.compile(r"<!-- BE-METADATA -->\n(.*?)\n<!-- /BE-METADATA -->", re.DOTALL)
TABLE_STATUS_RE = re.compile(r"^\| Status \| (.+?) \|\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^\* Status: (.+)$", re.MULTILINE)
BUILD_INDEX = Path(__file__).resolve().parent / "build_roadmap_index.py"


@dataclass(frozen=True)
class Misfiled:
    """An item whose current subdirectory disagrees with the one its ``Status`` calls for."""

    name: str  # directory name, e.g. "BE-0053-bedrock-ai-provider"
    status: str
    current: str  # the category it sits in now
    expected: str  # the category its Status calls for


def read_status(item_dir: Path) -> str | None:
    """The item's ``Status``, read from its English file; ``None`` if absent or unreadable.

    The English file is ``<dir>/<dir-name>.md`` (the Japanese mirror carries a ``-ja`` suffix).
    The ``**`` emphasis around the value is stripped, matching ``build_roadmap_index``.
    """
    english = item_dir / f"{item_dir.name}.md"
    try:
        text = english.read_text(encoding="utf-8")
    except OSError:
        return None
    block = META_BLOCK_RE.search(text)
    match = TABLE_STATUS_RE.search(block.group(1)) if block else STATUS_RE.search(text)
    if not match:
        return None
    return match.group(1).replace("**", "").strip()


def expected_category(status: str) -> str:
    """The subdirectory an item with this ``Status`` belongs in (Status is the source of truth)."""
    return STATUS_TO_CATEGORY.get(status, PROPOSALS)


def misfiled_items(roadmap: Path) -> list[Misfiled]:
    """Items whose current subdirectory disagrees with their ``Status``, sorted by name.

    Pure and side-effect free: the gate test and ``--check`` both call this, and ``promote`` uses
    it to decide what to move. Items without a readable Status are skipped — there is nothing to
    reconcile against.
    """
    found: list[Misfiled] = []
    for category in CATEGORIES:
        category_dir = roadmap / category
        if not category_dir.is_dir():
            continue
        for d in sorted(category_dir.iterdir()):
            if not (d.is_dir() and NUMBERED_DIR_RE.match(d.name)):
                continue
            status = read_status(d)
            if status is None:
                continue
            expected = expected_category(status)
            if expected != category:
                found.append(
                    Misfiled(name=d.name, status=status, current=category, expected=expected)
                )
    return found


def git_mv(src: Path, dst: Path) -> None:
    subprocess.run(["git", "mv", str(src), str(dst)], check=True)


def regenerate_index() -> None:
    """Rebuild the index tables so every link path reflects each item's new subdirectory."""
    subprocess.run([sys.executable, str(BUILD_INDEX)], check=True)


def promote(roadmap: Path) -> list[Misfiled]:
    """Move every misfiled item to the subdirectory its ``Status`` calls for; reindex if any moved."""
    moves = misfiled_items(roadmap)
    for item in moves:
        src = roadmap / item.current / item.name
        dst = roadmap / item.expected / item.name
        if dst.exists():  # an id reused across two items — never silently clobber
            raise SystemExit(f"refusing to move {item.name}: {dst} already exists")
        dst.parent.mkdir(parents=True, exist_ok=True)  # git mv needs the target category dir
        git_mv(src, dst)
        print(f"Moved {item.name}: {item.current}/ -> {item.expected}/ (Status: {item.status})")
    if moves:
        # A move changes the moved item's folder, so item-body links into and out of it now point
        # at the wrong folder. Repair them (BE-0069) before reindexing, so a promotion self-heals
        # the cross-links the same way it rebuilds the index — neither is left to hand-editing.
        # Imported here (sibling script under scripts/, added to the path) so the dependency is
        # local to where it's used, not a module-level import after path setup.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from lint_roadmap import fix_links

        if fixed := fix_links(roadmap):
            print(f"Repaired {fixed} cross-link(s) after the move(s)")
        regenerate_index()
    return moves


def main(argv: list[str]) -> int:
    if "--check" in argv:
        misfiled = misfiled_items(ROADMAP)
        for item in misfiled:
            print(
                f"{item.name}: Status {item.status!r} expects {item.expected}/ "
                f"but it is filed under {item.current}/",
                file=sys.stderr,
            )
        if misfiled:
            print(
                "\nRoadmap items are misfiled. Run `make roadmap-promote` "
                "(or `python scripts/promote_roadmap_items.py`) and commit the result.",
                file=sys.stderr,
            )
            return 1
        return 0

    if not promote(ROADMAP):
        print("Every roadmap item is already filed by its Status; nothing to move.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

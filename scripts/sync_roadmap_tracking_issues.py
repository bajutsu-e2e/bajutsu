#!/usr/bin/env python3
"""Keep one GitHub tracking issue open per *open* roadmap item (BE-0109).

An open item is one whose ``Status`` is ``Proposal`` or ``In progress`` — everything not yet
shipped (``Implemented``) or shelved (``Proposal (deferred)``). Each such item gets an issue the
moment it exists, so an issue with **no** assignee is exactly the signal the roadmap lacks: nobody
has picked it up yet. Assigned issues show who is on what; ``label:roadmap-tracking no:assignee`` is
the unclaimed backlog.

GitHub itself is the source of truth for both facts this tracks — who owns an item (the issue's
Assignees) and whether a tracking issue already exists (an open issue with the ``roadmap-tracking``
label carrying the item's ``BE-NNNN`` in its title). Nothing is written back into the repo, so the
sync needs only ``issues: write`` on the default token — no commit to ``main`` and no bypass App.

Lifecycle — a pure function of each item's current ``Status`` (never a PR diff), so the sync is
idempotent and self-healing (matching BE-0043 / BE-0061): running it twice, or against an
already-consistent set, is a no-op.

- open item (``Proposal`` / ``In progress``) with no matching open issue -> create one.
- matching open issue whose item is now ``Implemented`` / ``Proposal (deferred)`` -> close it.

It scans only numbered ``BE-NNNN`` items, so a ``BE-XXXX`` placeholder — which has no permanent
number yet — is skipped naturally; its issue is created on the next run, after ``roadmap-id``
allocates the real number on ``main`` (BE-0089). That allocation commit is itself a ``roadmaps/**``
change on ``main``, which re-triggers the workflow.

Usage::

    python scripts/sync_roadmap_tracking_issues.py            # create/close to match Status
    python scripts/sync_roadmap_tracking_issues.py --check     # report drift, mutate nothing

It calls the network (``gh``), so it never runs inside ``make check``; ``--check`` only reads.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_roadmap_index import TITLE_RE
from promote_roadmap_items import CATEGORIES, read_status
from roadmap_ids import numbered_match

ROADMAP = Path("roadmaps")
# Only these statuses are "open" and get a tracking issue; the other two are shipped / shelved.
OPEN_STATUSES = frozenset({"Proposal", "In progress"})
LABEL = "roadmap-tracking"
LABEL_COLOR = "0e8a16"
LABEL_DESCRIPTION = "Ownership tracker for an open roadmap (BE) item (BE-0109)"
REPO_BLOB_ROOT = "https://github.com/bajutsu-e2e/bajutsu/blob/main"

# The item's BE id as it appears in an issue title, e.g. "[BE-0042] …".
ID_IN_TITLE_RE = re.compile(r"BE-\d{4}")
# The Introduction section body: everything between its heading and the next "## " heading (or EOF).
INTRO_RE = re.compile(r"^## Introduction\s*\n(.*?)(?=^## |\Z)", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class Item:
    """A numbered roadmap item, with the fields a tracking issue needs."""

    be_id: str  # "BE-0042"
    slug: str
    category: str
    status: str
    title: str  # H1 title text after the em dash
    intro: str  # Introduction section body (may be empty)


def _parse_english(item_dir: Path) -> tuple[str, str]:
    """Return (H1 title, Introduction body) from the item's English file; ('', '') if unreadable."""
    english = item_dir / f"{item_dir.name}.md"
    try:
        text = english.read_text(encoding="utf-8")
    except OSError:
        return "", ""
    title_match = TITLE_RE.search(text)
    intro_match = INTRO_RE.search(text)
    title = title_match.group(1).strip() if title_match else ""
    intro = intro_match.group(1).strip() if intro_match else ""
    return title, intro


def scan_items(roadmap: Path) -> list[Item]:
    """Every numbered ``BE-NNNN`` item across the four folders, sorted by id.

    Placeholders (``BE-XXXX``) aren't numbered, so they're skipped — a tracking issue is only ever
    titled with a permanent id. Items without a readable Status are skipped.
    """
    items: list[Item] = []
    for category in CATEGORIES:
        category_dir = roadmap / category
        if not category_dir.is_dir():
            continue
        for d in sorted(category_dir.iterdir()):
            match = numbered_match(d.name) if d.is_dir() else None
            if not match:
                continue
            status = read_status(d)
            if status is None:
                continue
            be_id, slug = f"BE-{match.group(1)}", match.group(2)
            title, intro = _parse_english(d)
            items.append(
                Item(
                    be_id=be_id,
                    slug=slug,
                    category=category,
                    status=status,
                    title=title,
                    intro=intro,
                )
            )
    return sorted(items, key=lambda item: item.be_id)


@dataclass(frozen=True)
class Plan:
    """The lifecycle decision: which items need an issue created, and which ids need closing."""

    to_create: list[Item]
    to_close: list[str]  # BE ids whose open issue should close (item shipped / shelved)


def plan(items: list[Item], existing_open_ids: set[str]) -> Plan:
    """Compute create/close actions from current Status alone — pure and idempotent.

    Args:
        items: every numbered item in the tree.
        existing_open_ids: BE ids that currently have an open tracking issue on GitHub.

    Returns:
        Items that are open but lack an issue (create), and ids whose item is no longer open
        yet still has an open issue (close). Both lists are sorted by id.
    """
    status_by_id = {item.be_id: item.status for item in items}
    to_create = sorted(
        (
            item
            for item in items
            if item.status in OPEN_STATUSES and item.be_id not in existing_open_ids
        ),
        key=lambda item: item.be_id,
    )
    to_close = sorted(
        be_id
        for be_id in existing_open_ids
        if be_id in status_by_id and status_by_id[be_id] not in OPEN_STATUSES
    )
    return Plan(to_create=to_create, to_close=to_close)


def _gh(args: list[str], capture: bool = False) -> str:
    """Run a ``gh`` command; return its stdout when ``capture`` is set."""
    result = subprocess.run(["gh", *args], check=True, text=True, capture_output=capture)
    return result.stdout if capture else ""


def existing_open_issues() -> dict[str, int]:
    """Map each open tracking issue's BE id to its issue number, read from GitHub.

    One ``gh issue list`` for the whole label, so the sync is a single query regardless of item
    count. An issue whose title carries no ``BE-NNNN`` is ignored (it isn't a tracking issue).
    """
    out = _gh(
        [
            "issue",
            "list",
            "--label",
            LABEL,
            "--state",
            "open",
            "--limit",
            "1000",
            "--json",
            "number,title",
        ],
        capture=True,
    )
    by_id: dict[str, int] = {}
    for issue in json.loads(out):
        match = ID_IN_TITLE_RE.search(issue["title"])
        if match:
            by_id[match.group(0)] = issue["number"]
    return by_id


def ensure_label() -> None:
    """Create the ``roadmap-tracking`` label if it's missing (idempotent — ignores 'already exists')."""
    result = subprocess.run(
        [
            "gh",
            "label",
            "create",
            LABEL,
            "--color",
            LABEL_COLOR,
            "--description",
            LABEL_DESCRIPTION,
        ],
        text=True,
        capture_output=True,
    )
    if result.returncode != 0 and "already exists" not in (result.stderr or ""):
        raise subprocess.CalledProcessError(
            result.returncode, result.args, result.stdout, result.stderr
        )


def issue_body(item: Item) -> str:
    """The issue body: a link back to the item's English file and a quote of its Introduction."""
    stem = f"{item.be_id}-{item.slug}"
    href = f"{REPO_BLOB_ROOT}/roadmaps/{item.category}/{stem}/{stem}.md"
    lines = [
        f"Tracking issue for roadmap item **[{item.be_id}]({href})**.",
        "",
        "Self-assign this issue when you pick the item up; leave it unassigned if it's up for grabs. "
        + "Opened and closed automatically from the item's `Status` (BE-0109) — don't close it by hand.",
    ]
    if item.intro:
        lines += ["", *(f"> {line}" if line else ">" for line in item.intro.splitlines())]
    return "\n".join(lines)


def create_issue(item: Item) -> None:
    _gh(
        [
            "issue",
            "create",
            "--title",
            f"[{item.be_id}] {item.title}",
            "--body",
            issue_body(item),
            "--label",
            LABEL,
        ]
    )


def close_issue(number: int) -> None:
    _gh(["issue", "close", str(number)])


def sync(roadmap: Path) -> Plan:
    """Reconcile GitHub's open tracking issues with the tree's open items."""
    items = scan_items(roadmap)
    existing = existing_open_issues()
    actions = plan(items, set(existing))
    if actions.to_create:
        ensure_label()
    for item in actions.to_create:
        create_issue(item)
        print(f"Opened tracking issue for {item.be_id} ({item.status})")
    for be_id in actions.to_close:
        close_issue(existing[be_id])
        print(f"Closed tracking issue for {be_id} (no longer open)")
    if not actions.to_create and not actions.to_close:
        print("Tracking issues already match every item's Status; nothing to do.")
    return actions


def check(roadmap: Path) -> int:
    """Report drift using only reads; exit 1 if any issue is missing or should be closed."""
    items = scan_items(roadmap)
    existing = existing_open_issues()
    actions = plan(items, set(existing))
    for item in actions.to_create:
        print(f"{item.be_id}: open item ({item.status}) has no tracking issue", file=sys.stderr)
    for be_id in actions.to_close:
        print(f"{be_id}: tracking issue #{existing[be_id]} should be closed", file=sys.stderr)
    if actions.to_create or actions.to_close:
        print(
            "\nRoadmap tracking issues drifted from item Status. Run "
            "`python scripts/sync_roadmap_tracking_issues.py`.",
            file=sys.stderr,
        )
        return 1
    return 0


def main(argv: list[str]) -> int:
    if "--check" in argv:
        return check(ROADMAP)
    sync(ROADMAP)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

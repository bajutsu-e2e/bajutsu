#!/usr/bin/env python3
"""Scaffold a new roadmap (BE) item — both language files, in the canonical shape (BE-0069 A).

Authoring a BE item by hand is the most ceremonious, error-prone procedure in the repo: a bilingual
pair in the exact Swift-Evolution format, a complete fenced metadata block, the author's GitHub
handle, and — the classic trap — the *literal* ``BE-XXXX`` placeholder rather than a guessed number
(IDs are permanent and monotonic; allocation is CI's atomic job, BE-0061). This turns that recipe
into one command both a human and an AI invoke identically::

    make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Proposal] [HANDLE=<handle>]

It writes ``roadmaps/proposals/BE-XXXX-<slug>/`` with ``BE-XXXX-<slug>.md`` and its ``-ja.md`` mirror,
each pre-filled with the header link, the metadata block, and the five sections seeded with ``TBD``.
It deliberately does **not** add an index row: the index generator skips ``BE-XXXX`` items, so the
committed tables stay row-free for the placeholder and ``make check`` is green locally; the
``roadmap-id`` workflow allocates the number and regenerates the index on the PR.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _known_topics() -> frozenset[str]:
    """The index's known topics, used to validate ``TOPIC``. Imported here (a sibling script under
    scripts/, added to the path) so the dependency is local to where it's used, not a module-level
    import after a sys.path tweak."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from build_roadmap_index import KNOWN_TOPICS

    return KNOWN_TOPICS


ROADMAP = Path(__file__).resolve().parent.parent / "roadmaps"
PLACEHOLDER = "BE-XXXX"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Status -> its folder (mirrors STATUS_TO_CATEGORY in promote_roadmap_items.py).
# Defined locally to avoid a module-level import after the sys.path tweak that _known_topics()
# applies — keeping every cross-script dependency scoped to the function that needs it.
STATUS_TO_FOLDER = {
    "Proposal": "proposals",
    "In progress": "in-progress",
    "Implemented": "implemented",
    "Proposal (deferred)": "deferred",
}

# Status -> the word shown in each language's metadata block (the pairing test_roadmap_format pins).
STATUS_JA = {
    "Proposal": "提案",
    "In progress": "実装中",
    "Implemented": "実装済み",
    "Proposal (deferred)": "提案（保留）",
}

_SECTIONS_EN = [
    "Introduction",
    "Motivation",
    "Detailed design",
    "Alternatives considered",
    "References",
]
_SECTIONS_JA = ["はじめに", "動機", "詳細設計", "検討した代替案", "参考"]


def _resolve_handle(explicit: str | None) -> str:
    """The author's GitHub handle: ``HANDLE=`` wins, then ``$GITHUB_ACTOR`` (CI), then
    ``git config github.user``. Errors out rather than guessing — the Author must be a real handle."""
    import os

    if explicit:
        return explicit.lstrip("@")
    if actor := os.environ.get("GITHUB_ACTOR"):
        return actor
    result = subprocess.run(
        ["git", "config", "--get", "github.user"], capture_output=True, text=True, check=False
    )
    if handle := result.stdout.strip():
        return handle
    raise SystemExit(
        "could not resolve a GitHub handle for Author. Pass HANDLE=<handle> "
        "(or set `git config github.user <handle>`)."
    )


def _en_body(slug: str, title: str, status: str, topic: str, handle: str) -> str:
    meta = "\n".join(
        [
            "<!-- BE-METADATA -->",
            "| Field | Value |",
            "|---|---|",
            f"| Proposal | [{PLACEHOLDER}]({PLACEHOLDER}-{slug}.md) |",
            f"| Author | [@{handle}](https://github.com/{handle}) |",
            f"| Status | **{status}** |",
            f"| Topic | {topic} |",
            "<!-- /BE-METADATA -->",
        ]
    )
    sections = "\n\n".join(f"## {s}\n\nTBD" for s in _SECTIONS_EN)
    return (
        f"**English** · [日本語]({PLACEHOLDER}-{slug}-ja.md)\n\n"
        f"# {PLACEHOLDER} — {title}\n\n{meta}\n\n{sections}\n"
    )


def _ja_body(slug: str, title: str, status: str, topic: str, handle: str) -> str:
    meta = "\n".join(
        [
            "<!-- BE-METADATA -->",
            "| 項目 | 値 |",
            "|---|---|",
            f"| 提案 | [{PLACEHOLDER}]({PLACEHOLDER}-{slug}-ja.md) |",
            f"| 提案者 | [@{handle}](https://github.com/{handle}) |",
            f"| 状態 | **{STATUS_JA[status]}** |",
            f"| トピック | {topic} |",
            "<!-- /BE-METADATA -->",
        ]
    )
    sections = "\n\n".join(f"## {s}\n\nTBD" for s in _SECTIONS_JA)
    return (
        f"[English]({PLACEHOLDER}-{slug}.md) · **日本語**\n\n"
        f"# {PLACEHOLDER} — {title}\n\n{meta}\n\n{sections}\n"
    )


def scaffold(roadmap: Path, slug: str, title: str, *, topic: str, status: str, handle: str) -> Path:
    """Create the placeholder item directory and both language files; return the directory."""
    if not title.strip():
        raise SystemExit("TITLE must not be empty")
    if not SLUG_RE.match(slug):
        raise SystemExit(f"SLUG must be kebab-case (lower-case words joined by '-'): {slug!r}")
    known_topics = _known_topics()
    if topic not in known_topics:
        topics = "\n  ".join(sorted(known_topics))
        raise SystemExit(f"unknown TOPIC {topic!r}. Known topics:\n  {topics}")
    if status not in STATUS_JA:
        raise SystemExit(f"unknown STATUS {status!r}. One of: {', '.join(STATUS_JA)}")
    handle = handle.lstrip("@")  # accept either "octocat" or "@octocat"

    # Use the folder that matches Status (Status is the source of truth per BE-0078), so the item
    # is filed correctly from creation and the promote gate never has to move it.
    folder = STATUS_TO_FOLDER[status]
    item_dir = roadmap / folder / f"{PLACEHOLDER}-{slug}"
    if item_dir.exists():
        raise SystemExit(f"{item_dir} already exists")
    item_dir.mkdir(parents=True)
    (item_dir / f"{PLACEHOLDER}-{slug}.md").write_text(
        _en_body(slug, title, status, topic, handle), encoding="utf-8"
    )
    (item_dir / f"{PLACEHOLDER}-{slug}-ja.md").write_text(
        _ja_body(slug, title, status, topic, handle), encoding="utf-8"
    )
    return item_dir


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scaffold a new roadmap (BE) item.")
    parser.add_argument(
        "--slug", required=True, help="kebab-case slug, e.g. web-cross-browser-engines"
    )
    parser.add_argument(
        "--title", required=True, help="the item's title (the H1 text after the id)"
    )
    parser.add_argument(
        "--topic",
        default="Miscellaneous / on hold",
        help="a known index topic (see the error list)",
    )
    parser.add_argument("--status", default="Proposal", help="Proposal (default) / In progress / …")
    parser.add_argument("--handle", default=None, help="GitHub handle for Author (else git config)")
    args = parser.parse_args(argv)

    handle = _resolve_handle(args.handle)
    item_dir = scaffold(
        ROADMAP, args.slug, args.title, topic=args.topic, status=args.status, handle=handle
    )
    rel = item_dir.relative_to(ROADMAP.parent)
    print(f"Created {rel}/ (placeholder {PLACEHOLDER}; CI allocates the real id on your PR).")
    print(
        "Next: fill the TBD sections, localize the Japanese トピック / title / prose, and open a PR"
    )
    print(f"      with the literal {PLACEHOLDER} in the title prefix.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

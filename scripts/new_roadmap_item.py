#!/usr/bin/env python3
"""Scaffold a new roadmap (BE) item — both language files, in the canonical shape (BE-0069 A).

Authoring a BE item by hand is the most ceremonious, error-prone procedure in the repo: a bilingual
pair in the exact Swift-Evolution format, a complete fenced metadata block, the author's GitHub
handle, and — the classic trap — the *literal* ``BE-XXXX`` placeholder rather than a guessed number
(IDs are permanent and monotonic; allocation is CI's atomic job, BE-0061). This turns that recipe
into one command both a human and an AI invoke identically::

    make new-roadmap-item SLUG=<slug> TITLE="<title>" [TOPIC="<topic>"] [STATUS=Proposal] [HANDLE=<handle>]

It writes ``roadmaps/BE-XXXX-<slug>/`` with ``BE-XXXX-<slug>.md`` and its ``-ja.md`` mirror,
each pre-filled with the header link, the metadata block, and the six sections seeded with ``TBD`` —
``Progress`` (BE-0100) seeded with its living-checklist skeleton rather than a bare ``TBD``.
It deliberately does **not** allocate a real id up front: the placeholder stays ``BE-XXXX`` through
authoring and review, and the ``roadmap-id`` workflow renames it to the next free ``BE-NNNN`` on
``main`` after the PR merges (BE-0089).
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


def _put_scripts_on_path() -> None:
    """Add scripts/ (this file's own directory) to the path so a sibling script can be imported.

    Shared by every lazy import from build_roadmap_index below, so the dependency stays local to
    where it's used — not a module-level import after a sys.path tweak — without duplicating the
    path tweak itself. Each caller still imports its own names directly, so mypy resolves their
    real types instead of the ``Any`` a module-object return would carry.
    """
    sys.path.insert(0, str(Path(__file__).resolve().parent))


def _known_topics() -> frozenset[str]:
    """The index's known topics, used to validate ``TOPIC``."""
    _put_scripts_on_path()
    from build_roadmap_index import KNOWN_TOPICS

    return KNOWN_TOPICS


def _tracking_issue_url(be_id: str) -> str:
    """The item's BE-0109 tracking-issue search URL (BE-0139)."""
    _put_scripts_on_path()
    from build_roadmap_index import tracking_issue_url

    return tracking_issue_url(be_id)


ROADMAP = Path(__file__).resolve().parent.parent / "roadmaps"
PLACEHOLDER = "BE-XXXX"
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

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
    "Progress",
    "References",
]
_SECTIONS_JA = ["はじめに", "動機", "詳細設計", "検討した代替案", "進捗", "参考"]

# The Progress section (BE-0100) is the lone section seeded with content rather than ``TBD``: a
# living-checklist skeleton — author guidance, then one placeholder box to enumerate the MECE work
# breakdown once the item is scoped. Public (not ``_``-prefixed): scripts/fix_roadmap_drift.py
# (BE-0149) reuses the exact same skeleton when mechanically inserting a missing Progress section.
PROGRESS_BODY_EN = (
    "> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in\n"
    "> *Detailed design* (one box per unit of work); the log records what changed and when\n"
    "> (oldest first), linking the PRs.\n\n"
    "- [ ] TBD — enumerate the work breakdown (MECE) here once scoped."
)
PROGRESS_BODY_JA = (
    "> 開発の進行に合わせて常に最新の状態に保ってください。チェックリストは *詳細設計* の MECE な\n"
    "> 作業分解（作業の単位ごとに 1 つ）に対応し、ログには変更内容と時期（古い順）を PR へのリンクと\n"
    "> ともに記録します。\n\n"
    "- [ ] TBD — スコープが固まり次第、作業分解（MECE）をここに列挙します。"
)


def _render_sections(names: list[str], *, progress: str, progress_body: str) -> str:
    """Join the H2 sections, seeding ``Progress`` with its skeleton and the rest with ``TBD``."""
    return "\n\n".join(
        f"## {name}\n\n{progress_body if name == progress else 'TBD'}" for name in names
    )


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
            f"| Tracking issue | [Search]({_tracking_issue_url(PLACEHOLDER)}) |",
            f"| Topic | {topic} |",
            "<!-- /BE-METADATA -->",
        ]
    )
    sections = _render_sections(_SECTIONS_EN, progress="Progress", progress_body=PROGRESS_BODY_EN)
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
            f"| トラッキング Issue | [検索]({_tracking_issue_url(PLACEHOLDER)}) |",
            f"| トピック | {topic} |",
            "<!-- /BE-METADATA -->",
        ]
    )
    sections = _render_sections(_SECTIONS_JA, progress="進捗", progress_body=PROGRESS_BODY_JA)
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

    # Scaffold directly under roadmaps/ — the flat layout BE-0159 is migrating to. Status decides the
    # index bucket, not a folder, so a new item's path is permanent from creation.
    item_dir = roadmap / f"{PLACEHOLDER}-{slug}"
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
    print(
        "      with a plain scoped title and NO [BE-…] prefix — the id is allocated on main"
        " after the merge (BE-0089), and scripts/lint_pr.py rejects a prefixed BE-creation title."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

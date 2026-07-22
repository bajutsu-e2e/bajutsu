#!/usr/bin/env python3
"""Deterministic format check for roadmap (BE) item files — the body counterpart to the metadata
checks in ``tests/test_roadmap_index.py`` (BE-0074).

Every item is a pair of files, ``BE-NNNN-<slug>.md`` and ``BE-NNNN-<slug>-ja.md``, that share one
fixed shape: a bilingual header link, a ``# BE-NNNN — …`` title, a fenced metadata block, and the
six Swift-Evolution sections (``Progress`` added in BE-0100). This pins that shape so it can't drift
unnoticed — it walks the real tree, collects every deviation, and reports the full list (a gate, not
a formatter: it reports, it does not rewrite).

The logic lives here, in a **stdlib-only** module, rather than inside the test, for two reasons
(BE-0149): the merge-time allocator (``roadmap-id.yml``) reaches it through
``check_renumber_diff`` to self-validate before pushing to ``main``, and it must stay free of the
third-party test toolchain that runs alongside that job's bypass token (BE-0089);
``tests/test_roadmap_format.py`` is a thin wrapper that asserts these functions return no problems.

An unallocated ``BE-XXXX-<slug>`` placeholder is checked the same way a numbered item is — closing
the gap that let a malformed placeholder pass review and reach ``main`` untouched (BE-0149). The
only shape checks that flex for a placeholder are the id-bearing ones (the title's id, the header
link), which accept the literal ``BE-XXXX`` self-reference; heading set/order, the ``Track`` ban,
and the required ``Progress`` section apply identically.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Import the shared id-shape predicate whether this file is run as ``python3 scripts/…`` (scripts/
# already on the path) or loaded under its bare name by a test — add scripts/ so the sibling import
# resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_roadmap_index import tracking_issue_url
from roadmap_ids import PLACEHOLDER, is_placeholder_dir, iter_item_dirs, numbered_match

ROADMAP = Path(__file__).resolve().parent.parent / "roadmaps"

# Canonical metadata field order, per language. Required fields are always present; the optional
# ones (Implementing PR, Related, Superseded by, Origin) may be absent but, when present, keep their
# slot. Related / Superseded by record cross-item links (BE-0100); Track was retired in BE-0078 —
# the dashboard bucket is now derived from Status, the lone hand-set lifecycle field. Tracking issue
# (BE-0139) is required and mechanical — a pure function of the id — sitting right after Status.
ORDER_EN = [
    "Proposal",
    "Author",
    "Status",
    "Tracking issue",
    "Implementing PR",
    "Topic",
    "Related",
    "Superseded by",
    "Origin",
]
ORDER_JA = [
    "提案",
    "提案者",
    "状態",
    "トラッキング Issue",
    "実装 PR",
    "トピック",
    "関連",
    "無効化",
    "由来",
]
REQUIRED_EN = {"Proposal", "Author", "Status", "Tracking issue", "Topic"}
REQUIRED_JA = {"提案", "提案者", "状態", "トラッキング Issue", "トピック"}

# The block opens with a ``| Field | Value |`` header (``| 項目 | 値 |`` in Japanese) and its
# delimiter, so it renders as a real table; both are skipped when reading fields.
HEADER_EN = "| Field | Value |"
HEADER_JA = "| 項目 | 値 |"
HEADER_KEYS = {"Field", "項目"}
DELIMITER = "|---|---|"

# Status: a fixed set, paired across languages.
STATUS_PAIR = {
    "Implemented": "実装済み",
    "In progress": "実装中",
    "Proposal": "提案",
    "Proposal (deferred)": "提案（保留）",
}

HEADINGS_EN = [
    "Introduction",
    "Motivation",
    "Detailed design",
    "Alternatives considered",
    "Progress",
    "References",
]
HEADINGS_JA = ["はじめに", "動機", "詳細設計", "検討した代替案", "進捗", "参考"]

# A ``BE-XXXX`` reference that should have been resolved to a real ``BE-NNNN`` id. CI allocates the
# number on ``main`` after merge (BE-0089) by renaming the placeholder directory and rewriting the
# item's *own* files — it does not touch cross-references living in *other* files. Two shapes are
# stale leftovers: ``BE-XXXX`` inside a markdown link target ``](…BE-XXXX…)`` (a dangling link, since
# no file keeps that name post-allocation) and ``BE-XXXX-<concrete-slug>`` used as a path/dir
# reference. The naming *pattern* ``BE-XXXX-<slug>`` (literal ``<slug>``), bare ``BE-XXXX`` prose, and
# the ``[BE-XXXX]`` title-prefix example are all legitimate and stay. An unallocated placeholder
# item lives in a ``BE-XXXX-<slug>/`` directory and its own files self-reference ``BE-XXXX`` (header
# link, ``Proposal`` metadata) — expected until CI numbers it, so those files are exempted below.
DANGLING_BE_XXXX_RE = re.compile(r"\]\([^)]*BE-XXXX(?!-<)|BE-XXXX-(?!<)")

TITLE_RE = re.compile(r"^# BE-\d{4} — .+$")
PLACEHOLDER_TITLE_RE = re.compile(rf"^# {re.escape(PLACEHOLDER)} — .+$")
META_BLOCK_RE = re.compile(r"<!-- BE-METADATA -->\n(.*?)\n<!-- /BE-METADATA -->", re.DOTALL)
META_ROW_RE = re.compile(r"^\| (.+?) \| (.+?) \|\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^\*\*(.+)\*\*$")


@dataclass(frozen=True)
class _Item:
    """One BE item directory, identified for the format check."""

    id: str  # "BE-NNNN", or the literal "BE-XXXX" for a still-unallocated placeholder
    slug: str
    dir: Path
    is_placeholder: bool  # whether id-bearing checks must accept the BE-XXXX self-reference


def _items(roadmap: Path) -> list[_Item]:
    """Every BE item under ``roadmaps/`` — numbered and unallocated placeholders alike (BE-0159).

    A placeholder carries the literal ``BE-XXXX`` token as its id, so the id-bearing checks know to
    accept its self-reference (BE-0149).
    """
    items: list[_Item] = []
    for d in iter_item_dirs(roadmap):
        if m := numbered_match(d.name):
            items.append(_Item(f"BE-{m.group(1)}", m.group(2), d, is_placeholder=False))
        elif is_placeholder_dir(d.name):
            items.append(_Item(PLACEHOLDER, d.name[len(PLACEHOLDER) + 1 :], d, is_placeholder=True))
    return items


def is_subsequence(present: list[str], order: list[str]) -> bool:
    """True if ``present`` appears in ``order``'s relative order (every field known and in place)."""
    it = iter(order)
    return all(field in it for field in present)


def _check_file(
    be_id: str, slug: str, text: str, *, lang: str, is_placeholder: bool
) -> tuple[list[str], str | None]:
    """Return (problems, raw Status) for one language file."""
    problems: list[str] = []
    suffix = "-ja" if lang == "ja" else ""
    lines = text.splitlines()

    expected_header = (
        f"[English]({be_id}-{slug}.md) · **日本語**"
        if lang == "ja"
        else f"**English** · [日本語]({be_id}-{slug}-ja.md)"
    )
    if not lines or lines[0] != expected_header:
        problems.append(f"first line must be the bilingual header link {expected_header!r}")

    # A placeholder's title carries the literal ``BE-XXXX`` self-reference; a numbered item's a
    # 4-digit id. Everything else about the title is identical.
    title_re = PLACEHOLDER_TITLE_RE if is_placeholder else TITLE_RE
    if not any(title_re.match(line) for line in lines):
        want = PLACEHOLDER if is_placeholder else "BE-NNNN"
        problems.append(f"missing a '# {want} — <title>' H1 (em dash, U+2014)")

    order = ORDER_JA if lang == "ja" else ORDER_EN
    required = REQUIRED_JA if lang == "ja" else REQUIRED_EN
    block = META_BLOCK_RE.search(text)
    status_raw: str | None = None
    if not block:
        problems.append(
            "metadata block must be fenced by <!-- BE-METADATA --> … <!-- /BE-METADATA -->"
        )
    else:
        expected_header = HEADER_JA if lang == "ja" else HEADER_EN
        block_lines = [line for line in block.group(1).splitlines() if line.strip()]
        if block_lines[:2] != [expected_header, DELIMITER]:
            problems.append(
                f"metadata block must open with the header {expected_header!r} and its "
                f"{DELIMITER!r} delimiter"
            )
        rows = [
            (key.strip(), value.strip())
            for key, value in META_ROW_RE.findall(block.group(1))
            if key.strip() not in HEADER_KEYS
        ]
        fields = [key for key, _ in rows]
        values = dict(rows)
        unknown = [f for f in fields if f not in order]
        if unknown:
            problems.append(f"unknown metadata field(s): {', '.join(unknown)}")
        missing = required - set(fields)
        if missing:
            problems.append(f"missing required metadata field(s): {', '.join(sorted(missing))}")
        known = [f for f in fields if f in order]
        if known != sorted(known, key=order.index) or not is_subsequence(known, order):
            problems.append(f"metadata fields out of canonical order: {fields}")
        status_field = "状態" if lang == "ja" else "Status"
        if status_field in values and (m := STATUS_RE.match(values[status_field])):
            status_raw = m.group(1)

        # Tracking issue is purely mechanical (a function of the id), so its one failure mode is a
        # stale value copy-pasted from another item; pin it to exactly the URL this id predicts.
        # Uses be_id as-is, so a placeholder's own BE-XXXX self-reference works the same way.
        tracking_field = "トラッキング Issue" if lang == "ja" else "Tracking issue"
        if tracking_field in values:
            label = "検索" if lang == "ja" else "Search"
            expected = f"[{label}]({tracking_issue_url(be_id)})"
            if values[tracking_field] != expected:
                problems.append(
                    f"{tracking_field} must be exactly {expected!r} (a search URL derived from "
                    f"the item's own id), got {values[tracking_field]!r}"
                )

    # Headings that are real headings, not lines inside a fenced code block (e.g. a skeleton).
    headings = headings_outside_code(text)
    expected_headings = HEADINGS_JA if lang == "ja" else HEADINGS_EN
    if headings != expected_headings:
        problems.append(f"H2 headings must be exactly {expected_headings} in order, got {headings}")

    return [f"{be_id}{suffix}: {p}" for p in problems], status_raw


def heading_positions(text: str) -> list[tuple[str, int]]:
    """``(name, line_index)`` for every ``## `` heading that is a real heading, not a line inside a
    fenced code block (e.g. a skeleton). ``line_index`` is into ``text.splitlines()`` — the fixer
    (BE-0149) reuses this so its span-finding walk agrees with the same fence-aware scan, rather
    than re-deriving heading positions with a second, fence-naive pass that could disagree with it.
    """
    positions: list[tuple[str, int]] = []
    in_code = False
    for i, line in enumerate(text.splitlines()):
        if line.startswith("```"):
            in_code = not in_code
            continue
        if not in_code and line.startswith("## "):
            positions.append((line[3:].strip(), i))
    return positions


def headings_outside_code(text: str) -> list[str]:
    """``## `` headings that are real headings, not lines inside a fenced code block."""
    return [name for name, _ in heading_positions(text)]


def unresolved_be_xxxx_references(roadmap: Path = ROADMAP) -> list[str]:
    """Every unresolved ``BE-XXXX`` link or path reference in roadmap markdown, as ``path:line``.

    Guards the cross-reference gap in the merge-time allocator (BE-0089): when a placeholder item is
    numbered, links to it from *other* items keep pointing at the old ``BE-XXXX-<slug>`` path. Walks
    every ``.md`` under ``roadmaps/`` (the index pages included); a placeholder item's own files
    legitimately self-reference ``BE-XXXX`` until CI numbers it, so they are exempted.
    """
    problems: list[str] = []
    for path in sorted(roadmap.rglob("*.md")):
        # Match the containing directory chain only (not the filename), so a stray BE-XXXX-*.md file
        # living outside a placeholder directory is still checked.
        if any(part.startswith("BE-XXXX-") for part in path.parent.parts):
            continue
        for lineno, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if DANGLING_BE_XXXX_RE.search(line):
                rel = path.relative_to(roadmap.parent)
                problems.append(f"{rel}:{lineno}: {line.strip()}")
    return problems


def format_problems(roadmap: Path = ROADMAP) -> list[str]:
    """Every canonical-format violation across all BE items, numbered and placeholder alike.

    Empty when the tree is clean. Non-empty entries name the offending item and the deviation, so a
    caller (the gate test, or the merge-time allocator's self-check) can fail loudly with the list.
    """
    items = _items(roadmap)
    if not items:
        return ["no roadmap items found"]
    problems: list[str] = []

    for item in items:
        en_path = item.dir / f"{item.id}-{item.slug}.md"
        ja_path = item.dir / f"{item.id}-{item.slug}-ja.md"
        if not en_path.is_file() or not ja_path.is_file():
            problems.append(f"{item.id}: both {en_path.name} and {ja_path.name} must exist")
            continue

        en_problems, en_status = _check_file(
            item.id,
            item.slug,
            en_path.read_text("utf-8"),
            lang="en",
            is_placeholder=item.is_placeholder,
        )
        ja_problems, ja_status = _check_file(
            item.id,
            item.slug,
            ja_path.read_text("utf-8"),
            lang="ja",
            is_placeholder=item.is_placeholder,
        )
        problems += en_problems + ja_problems

        if en_status is not None and en_status not in STATUS_PAIR:
            problems.append(f"{item.id}: unknown English Status {en_status!r}")
        elif en_status is not None and ja_status != STATUS_PAIR[en_status]:
            problems.append(
                f"{item.id}: Status disagrees across languages — "
                f"en {en_status!r} expects ja {STATUS_PAIR[en_status]!r}, got {ja_status!r}"
            )

    return problems


def all_problems(roadmap: Path = ROADMAP) -> list[str]:
    """Every format violation and unresolved ``BE-XXXX`` reference — the full gate check.

    The one entry point both ``main`` here and the merge-time allocator's self-check
    (``check_renumber_diff``) call, so the two checks are composed in exactly one place.
    """
    return format_problems(roadmap) + unresolved_be_xxxx_references(roadmap)


def main(argv: list[str]) -> int:
    """Check the roadmap tree; print every violation and exit non-zero if any."""
    roadmap = Path(argv[0]) if argv else ROADMAP
    problems = all_problems(roadmap)
    if problems:
        print("roadmap item format violations:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("check-roadmap-format: every BE item matches the canonical format.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

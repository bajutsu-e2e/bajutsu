"""Deterministic format check for roadmap (BE) item files — the body counterpart to the index
check in ``tests/test_roadmap_index.py`` (BE-0074).

Every item is a pair of files, ``BE-NNNN-<slug>.md`` and ``BE-NNNN-<slug>-ja.md``, that share one
fixed shape: a bilingual header link, a ``# BE-NNNN — …`` title, a fenced metadata block, and the
six Swift-Evolution sections (``Progress`` added in BE-0100). This test pins that shape so it can't
drift unnoticed — it walks the real tree, collects every deviation, and fails with the full list (a
gate, not a formatter: it reports, it does not rewrite). The metadata parser the index relies on
lives in
``scripts/build_roadmap_index.py``; here we check structure (which fields, in what order, with what
headings), not the rendered index.
"""

from __future__ import annotations

import re
from pathlib import Path

ROADMAP = Path(__file__).resolve().parent.parent / "roadmaps"
CATEGORIES = ("implemented", "in-progress", "proposals", "deferred")
NUMBERED_DIR_RE = re.compile(r"^BE-(\d{4})-(.+)$")

# Canonical metadata field order, per language. Required fields are always present; the optional
# ones (Implementing PR, Related, Superseded by, Origin) may be absent but, when present, keep their
# slot. Related / Superseded by record cross-item links (BE-0100); Track was retired in BE-0078 —
# the index bucket is now derived from Status, the lone hand-set lifecycle field.
ORDER_EN = [
    "Proposal",
    "Author",
    "Status",
    "Implementing PR",
    "Topic",
    "Related",
    "Superseded by",
    "Origin",
]
ORDER_JA = ["提案", "提案者", "状態", "実装 PR", "トピック", "関連", "無効化", "由来"]
REQUIRED_EN = {"Proposal", "Author", "Status", "Topic"}
REQUIRED_JA = {"提案", "提案者", "状態", "トピック"}

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
META_BLOCK_RE = re.compile(r"<!-- BE-METADATA -->\n(.*?)\n<!-- /BE-METADATA -->", re.DOTALL)
META_ROW_RE = re.compile(r"^\| (.+?) \| (.+?) \|\s*$", re.MULTILINE)
STATUS_RE = re.compile(r"^\*\*(.+)\*\*$")


def _items() -> list[tuple[str, str, Path]]:
    """``(id, slug, dir)`` for every BE item, across both categories."""
    return [
        (f"BE-{m.group(1)}", m.group(2), d)
        for category in CATEGORIES
        if (ROADMAP / category).is_dir()
        for d in sorted((ROADMAP / category).iterdir())
        if d.is_dir() and (m := NUMBERED_DIR_RE.match(d.name))
    ]


def _is_subsequence(present: list[str], order: list[str]) -> bool:
    """True if ``present`` appears in ``order``'s relative order (every field known and in place)."""
    it = iter(order)
    return all(field in it for field in present)


def _check_file(be_id: str, slug: str, text: str, *, lang: str) -> tuple[list[str], str | None]:
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

    if not any(TITLE_RE.match(line) for line in lines):
        problems.append("missing a '# BE-NNNN — <title>' H1 (em dash, U+2014)")

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
        fields = [
            key.strip()
            for key, _ in META_ROW_RE.findall(block.group(1))
            if key.strip() not in HEADER_KEYS
        ]
        values = {
            key.strip(): value.strip()
            for key, value in META_ROW_RE.findall(block.group(1))
            if key.strip() not in HEADER_KEYS
        }
        unknown = [f for f in fields if f not in order]
        if unknown:
            problems.append(f"unknown metadata field(s): {', '.join(unknown)}")
        missing = required - set(fields)
        if missing:
            problems.append(f"missing required metadata field(s): {', '.join(sorted(missing))}")
        known = [f for f in fields if f in order]
        if known != sorted(known, key=order.index) or not _is_subsequence(known, order):
            problems.append(f"metadata fields out of canonical order: {fields}")
        status_field = "状態" if lang == "ja" else "Status"
        if status_field in values and (m := STATUS_RE.match(values[status_field])):
            status_raw = m.group(1)

    # Headings that are real headings, not lines inside a fenced code block (e.g. a skeleton).
    headings = _headings_outside_code(text)
    expected_headings = HEADINGS_JA if lang == "ja" else HEADINGS_EN
    if headings != expected_headings:
        problems.append(f"H2 headings must be exactly {expected_headings} in order, got {headings}")

    return [f"{be_id}{suffix}: {p}" for p in problems], status_raw


def _headings_outside_code(text: str) -> list[str]:
    """``## `` headings that are real headings, not lines inside a fenced code block."""
    headings: list[str] = []
    in_code = False
    for line in text.splitlines():
        if line.startswith("```"):
            in_code = not in_code
            continue
        if not in_code and line.startswith("## "):
            headings.append(line[3:].strip())
    return headings


def test_no_unresolved_be_xxxx_references() -> None:
    """No roadmap markdown may carry an unresolved ``BE-XXXX`` link or path reference.

    Guards the cross-reference gap in the merge-time allocator (BE-0089): when a placeholder item is
    numbered, links to it from *other* items keep pointing at the old ``BE-XXXX-<slug>`` path. This
    walks every ``.md`` under ``roadmaps/`` (the index pages included) and fails with the full list of
    offending ``path:line`` locations, so a dangling reference can't slip onto ``main``.
    """
    problems: list[str] = []
    for path in sorted(ROADMAP.rglob("*.md")):
        # A placeholder item's own files legitimately self-reference BE-XXXX until CI numbers it.
        # Match the containing directory chain only (not the filename), so a stray BE-XXXX-*.md
        # file living outside a placeholder directory is still checked.
        if any(part.startswith("BE-XXXX-") for part in path.parent.parts):
            continue
        for lineno, line in enumerate(path.read_text("utf-8").splitlines(), start=1):
            if DANGLING_BE_XXXX_RE.search(line):
                rel = path.relative_to(ROADMAP.parent)
                problems.append(f"{rel}:{lineno}: {line.strip()}")

    assert not problems, (
        "unresolved BE-XXXX reference(s) — replace with the allocated BE-NNNN id:\n"
        + "\n".join(problems)
    )


def test_every_be_item_matches_the_canonical_format() -> None:
    items = _items()
    assert items, "no roadmap items found"
    problems: list[str] = []

    for be_id, slug, d in items:
        en_path = d / f"{be_id}-{slug}.md"
        ja_path = d / f"{be_id}-{slug}-ja.md"
        if not en_path.is_file() or not ja_path.is_file():
            problems.append(f"{be_id}: both {en_path.name} and {ja_path.name} must exist")
            continue

        en_problems, en_status = _check_file(be_id, slug, en_path.read_text("utf-8"), lang="en")
        ja_problems, ja_status = _check_file(be_id, slug, ja_path.read_text("utf-8"), lang="ja")
        problems += en_problems + ja_problems

        if en_status is not None and en_status not in STATUS_PAIR:
            problems.append(f"{be_id}: unknown English Status {en_status!r}")
        elif en_status is not None and ja_status != STATUS_PAIR[en_status]:
            problems.append(
                f"{be_id}: Status disagrees across languages — "
                f"en {en_status!r} expects ja {STATUS_PAIR[en_status]!r}, got {ja_status!r}"
            )

    assert not problems, "roadmap item format violations:\n" + "\n".join(problems)

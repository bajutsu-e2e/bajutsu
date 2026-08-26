#!/usr/bin/env python3
"""Shared roadmap-item metadata: parsing, classification, and loading.

Every ``roadmaps/<category>/BE-NNNN-<slug>/`` item carries its own metadata (``Status`` / ``Topic``
/ ``Origin``) plus its H1 title. This module reads that metadata into a plain in-memory model — used
by the live roadmap dashboard (``scripts/build_roadmap_dashboard.py``, published to GitHub Pages) and
by a handful of other roadmap tools (topic-label sync, tracking-issue sync, the status query, new-item
scaffolding, format checking) that each need one slice of the same fields.

Every item's bucket is **derived from its ``Status``** (BE-0078), not a hand-set ``Track`` field:
Implemented / In progress / Proposals / Deferred / Rejected, most-progressed first. The roadmap's
index pages (``roadmaps/README.md`` / ``README-ja.md``) used to carry a generated table per bucket;
that table is retired in favor of the dashboard, which already lists every item — Implemented
included — grouped by Topic with filterable status chips, so there is nothing left for this module
to write back to the index pages. It is purely a read side now.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path

# Import the shared id-shape predicate whether this file is run as ``python3 scripts/…`` (scripts/
# already on the path) or loaded under its bare name by a test — add scripts/ so the sibling import
# resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from roadmap_ids import iter_item_dirs, numbered_match

ROADMAP = Path("roadmaps")
TITLE_RE = re.compile(r"^# BE-\d{4} — (.+)$", re.MULTILINE)

# Canonical metadata: a ``| Field | Value |`` table fenced by these markers, mirroring the roadmap
# item's own conventions. Fencing keeps the parser off same-shaped tables in the body.
META_BLOCK_RE = re.compile(r"<!-- BE-METADATA -->\n(.*?)\n<!-- /BE-METADATA -->", re.DOTALL)
# A data row inside that block. The header (``| Field …``) is excluded by key; the dash delimiter
# (``|---|``) never matches because there is no space after its leading pipe.
META_ROW_RE = re.compile(r"^\| (.+?) \| (.+?) \|\s*$", re.MULTILINE)
META_HEADER_KEYS = frozenset({"Field", "項目"})
# Legacy form (unmigrated items): ``* Field: value`` bullet lines. Read when no fence is present.
FIELD_RE = re.compile(r"^\* ([^:]+): (.+)$", re.MULTILINE)

# The GitHub search that finds an item's BE-0109 tracking issue from its id alone (BE-0139). The
# issue title is always ``[BE-NNNN] …`` and carries the ``roadmap-tracking`` label, so the id is
# enough to locate it without its issue number. No ``is:open`` filter, so it matches whether the
# issue is still open or was closed after the item shipped. Purely a function of the id — no ``gh``
# call, token, or network at build or authoring time — and the literal ``BE-XXXX`` placeholder flows
# through it unchanged until CI allocates the real id (BE-0089 rewrites it with the rest of the file).
_TRACKING_ISSUE_SEARCH = (
    "https://github.com/bajutsu-e2e/bajutsu/issues"
    '?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"{id}"'
)


def tracking_issue_url(be_id: str) -> str:
    """The GitHub issue-search URL for an item's tracking issue, built from its id alone (BE-0139)."""
    return _TRACKING_ISSUE_SEARCH.format(id=be_id)


_ID_TOKEN_RE = re.compile(r"BE-(\d{4})")


def relation_ids(value: str | None, *, self_id: str) -> tuple[str, ...]:
    """Every distinct item id a relation field names, in first-seen order, minus the item itself.

    A relation value is author-written Markdown carrying one or more links, so scanning it for id
    tokens reads it without depending on the link syntax the author chose. A ``BE-XXXX`` placeholder
    carries no number and so matches nothing, which is what the callers want: an unnumbered item has
    nowhere stable to link to.
    """
    found: dict[str, None] = {}
    for number in _ID_TOKEN_RE.findall(value or ""):
        be_id = f"BE-{number}"
        if be_id != self_id:
            found.setdefault(be_id, None)
    return tuple(found)


@dataclass(frozen=True)
class Lang:
    """Per-language metadata field names: which file suffix and which field labels to read."""

    code: str
    suffix: str  # filename suffix before ".md" ("" for English, "-ja" for Japanese)
    field_status: str
    field_topic: str
    field_origin: str


LANGS: tuple[Lang, ...] = (
    Lang(code="en", suffix="", field_status="Status", field_topic="Topic", field_origin="Origin"),
    Lang(code="ja", suffix="-ja", field_status="状態", field_topic="トピック", field_origin="由来"),
)


# Status -> the classification bucket (Implemented / In progress / Proposals / Deferred / Rejected).
# Derived from Status, not a hand-set Track field (BE-0078): the lone lifecycle field decides an
# item's bucket, so the two can never disagree. Order is most-progressed first; the dashboard
# (``scripts/build_roadmap_dashboard.py``, which imports ``BUCKETS`` directly) classifies every item
# this way, Implemented included.
STATUS_TO_BUCKET = {
    "Implemented": "Implemented",
    "In progress": "In progress",
    "Proposal": "Proposals",
    "Deferred": "Deferred",
    "Rejected": "Rejected",
}
BUCKETS: tuple[tuple[str, str], ...] = (
    ("Implemented", "implemented"),
    ("In progress", "in-progress"),
    ("Proposals", "proposals"),
    ("Deferred", "deferred"),
    ("Rejected", "rejected"),
)
# Every topic an item's ``Topic`` field may name, with its key fragment and whether it carries an
# Origin column. Topics are feature-focused: they name what a group of items *does*, never a
# delivery phase (the old M1-M4 milestones were dissolved into these) or a competitor (the MagicPod /
# Autify / Maestro research buckets were redistributed here by feature). Platform work is one topic
# across every OS, with the backend-agnostic core split out as its own architecture topic.
TOPICS: tuple[tuple[str, str, bool], ...] = (
    ("Platform support", "platform", False),
    ("Driver & backend architecture", "driver-architecture", False),
    ("Device-cloud execution", "device-cloud", False),
    ("Scenario authoring features", "scenario-authoring", False),
    ("Verification & coverage", "verification", False),
    ("Authoring experience", "authoring", False),
    # Path-only topic: a real key so the `bajutsu/record*` PATH_TOPIC rules can label record changes
    # distinctly from the serve authoring UI. It carries no roadmap items (record features live under
    # the authoring-experience topic), so it renders no dashboard section — it exists only to keep
    # `record` a valid label key for the path rules.
    ("Record (action capture)", "record", False),
    ("Autonomous crawl", "crawl", False),
    ("codegen coverage", "codegen", False),
    ("Self-healing triage", "self-healing", False),
    ("doctor / onboarding", "doctor", False),
    ("Integration & automation", "mcp", False),
    ("Integration with external services", "external-integration", False),
    ("AI provider configuration", "ai-provider", False),
    ("AI usage and cost observability", "ai-usage", False),
    ("Surfacing CLI features in the serve Web UI", "serve-cli-features", False),
    ("Hosting the web UI", "hosting", False),
    ("Configuration sourcing", "config-sourcing", False),
    ("Security hardening", "security", False),
    ("Dogfood fixtures (demo apps)", "dogfood", True),
    ("Dogfood fixtures (web UI)", "dogfood-web-ui", True),
    ("Contributor workflow", "developer-experience", False),
    # Path-only infra topic: a real topic key so PATH_TOPIC_* rules for `.github/`, `.githooks/`,
    # `scripts/`, `Makefile` can label CI/build changes without co-opting the contributor-workflow
    # items' `topic:developer-experience`. It carries no roadmap items (every dev item's Topic is the
    # contributor-workflow line above), so it renders no dashboard section — it exists only to keep
    # `contribution` a valid label key for the path rules.
    ("CI / build infrastructure", "contribution", False),
    ("Codebase quality & technical debt", "quality-debt", False),
    ("Miscellaneous / on hold", "misc", False),
)
KNOWN_TOPICS = frozenset(topic for topic, _key, _origin in TOPICS)
TOPIC_KEY_BY_NAME = {topic: key for topic, key, _origin in TOPICS}


def bucket(status: str) -> str:
    """The classification bucket an item with this (English) ``Status`` belongs in (BE-0078)."""
    try:
        return STATUS_TO_BUCKET[status]
    except KeyError as e:
        raise ValueError(f"unknown status {status!r}") from e


@dataclass(frozen=True)
class Entry:
    """A single item's data, already resolved for one language."""

    id: str
    slug: str
    title: str
    status: str  # raw status, before display mapping
    origin: str | None


@dataclass(frozen=True)
class Item:
    """A roadmap item: its identity, bucket + Topic, and per-language render fields.

    ``created`` / ``updated`` are UTC ISO-8601 timestamps derived from the item's Git history
    (BE-0311), populated only when :func:`load_items` is called ``with_dates=True`` — ``None``
    otherwise, so the tools that don't need them pay no ``git`` cost.

    ``related`` / ``origin_refs`` / ``superseded_by`` are the ids the item's three relation fields
    name, already filtered to items the loaded tree actually holds, so every id here resolves.

    ``summary`` is the first paragraph of the English ``## Introduction`` section, as plain text
    (:func:`extract_summary`) — empty when the file has no such section.
    """

    id: str
    slug: str
    bucket: str  # derived from the English Status (BE-0078)
    topic: str  # canonical (English) topic
    by_lang: dict[str, Entry]
    created: str | None = None
    updated: str | None = None
    related: tuple[str, ...] = ()
    origin_refs: tuple[str, ...] = ()
    superseded_by: tuple[str, ...] = ()
    summary: str = ""


_INTRO_RE = re.compile(r"^## Introduction\n\n(.+?)(?:\n\n|\Z)", re.DOTALL | re.MULTILINE)
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]+\)")
_MD_CODE_RE = re.compile(r"`([^`]+)`")
_MD_BOLD_RE = re.compile(r"\*\*(.+?)\*\*|__(.+?)__")
# An underscore or asterisk pair only counts as emphasis when it is *not* intraword — i.e. neither
# delimiter sits directly between two word characters — so a snake_case identifier like ``wait_for``
# (a single underscore, both neighbours word characters) can never match either alternative.
_MD_ITALIC_RE = re.compile(r"(?<!\w)\*(\S(?:.*?\S)?)\*(?!\w)|(?<!\w)_(\S(?:.*?\S)?)_(?!\w)")
_CODE_PLACEHOLDER_RE = re.compile(r"\x00(\d+)\x00")
_WHITESPACE_RE = re.compile(r"\s+")


def to_plain_text(markdown: str, *, max_len: int = 220) -> str:
    """One paragraph of markdown as a plain-text one-liner, truncated on a word boundary.

    An inline code span is stashed behind a placeholder *before* any other substitution runs —
    including link resolution — and restored verbatim at the end, so its content (an identifier
    like ``` `__init__` ``` or ``` `wait_for` ```, or even literal text that happens to look like a
    markdown link) round-trips untouched rather than being mutated by a rule meant for the
    surrounding prose. A markdown link outside a code span keeps its visible text; bold/italic
    markers are stripped.

    Args:
        markdown: one paragraph, which may span several source lines.
        max_len: the length past which the result is cut back to the last whole word and an
            ellipsis appended.
    """
    paragraph = _WHITESPACE_RE.sub(" ", markdown).strip()
    code_spans: list[str] = []

    def _stash(m: re.Match[str]) -> str:
        code_spans.append(m.group(1))
        return f"\x00{len(code_spans) - 1}\x00"

    paragraph = _MD_CODE_RE.sub(_stash, paragraph)
    paragraph = _MD_LINK_RE.sub(r"\1", paragraph)
    paragraph = _MD_BOLD_RE.sub(lambda m: m.group(1) or m.group(2), paragraph)
    paragraph = _MD_ITALIC_RE.sub(lambda m: m.group(1) or m.group(2), paragraph)
    paragraph = _CODE_PLACEHOLDER_RE.sub(lambda m: code_spans[int(m.group(1))], paragraph)
    if len(paragraph) <= max_len:
        return paragraph
    return paragraph[:max_len].rsplit(" ", 1)[0].rstrip(",;:") + "…"


def extract_summary(text: str, *, max_len: int = 220) -> str:
    """The first paragraph of an item's ``## Introduction`` section, as a plain-text one-liner.

    Used as a short at-a-glance overview (the dashboard relationship map's hover card, BE-0335)
    rather than as parsed metadata: every item's file already opens with one, so this reads it
    instead of asking an author to maintain a second, shorter description that could drift from the
    real one. Returns an empty string when the file has no ``## Introduction`` section (the
    legacy-format fixtures used in tests), so a caller can treat "no summary" as ordinary rather
    than an error. :func:`to_plain_text` does the markdown flattening, which ``repo_map`` reuses
    for the one-line summary it derives from a ``docs/`` page.
    """
    match = _INTRO_RE.search(text)
    if not match:
        return ""
    return to_plain_text(match.group(1), max_len=max_len)


def parse_metadata(text: str) -> tuple[str, dict[str, str]]:
    """Return (H1 title, metadata fields) from a BE item file's body.

    Spec. The metadata block is a ``| Field | Value |`` table fenced by the markers
    ``<!-- BE-METADATA -->`` … ``<!-- /BE-METADATA -->``. Each data row is one ``field -> value``
    (header and dash-delimiter rows excluded), with ``**`` emphasis stripped from the value.
    Fencing the block keeps the parser off same-shaped tables elsewhere in the body. A file without
    the markers is read by the legacy ``* Field: value`` bullet rule, so an unmigrated item still
    parses. The title is the text after the em dash in the ``# BE-NNNN — …`` heading.
    """
    title_match = TITLE_RE.search(text)
    if not title_match:
        raise ValueError("no '# BE-NNNN — <title>' heading found")
    return title_match.group(1).strip(), metadata_fields(text)


def metadata_fields(text: str) -> dict[str, str]:
    """Parse just the ``field -> value`` metadata, independent of the (numbered) title.

    Split from :func:`parse_metadata` so a ``BE-XXXX`` placeholder — whose title carries no number
    and so trips ``TITLE_RE`` — can still be read for its ``Status`` / ``Topic``. Prefers the fenced
    ``BE-METADATA`` table; falls back to the legacy ``* Field: value`` bullets for unmigrated items.
    """
    block = META_BLOCK_RE.search(text)
    if block:
        return {
            key.strip(): value.replace("**", "").strip()
            for key, value in META_ROW_RE.findall(block.group(1))
            if key.strip() not in META_HEADER_KEYS
        }
    return {key.strip(): value.replace("**", "").strip() for key, value in FIELD_RE.findall(text)}


def duplicate_ids(roadmap: Path) -> dict[str, list[str]]:
    """Map every BE id used by more than one item directory to those directories' paths.

    IDs are permanent and unique. A duplicate — e.g. two branches racing for ``max + 1`` — must
    fail the build rather than silently loading two items for one id. Empty when all unique.
    """
    by_id: dict[str, list[str]] = {}
    for d in iter_item_dirs(roadmap):
        if match := numbered_match(d.name):
            by_id.setdefault(f"BE-{match.group(1)}", []).append(d.name)
    return {be_id: paths for be_id, paths in by_id.items() if len(paths) > 1}


def _git_dates(paths: Iterable[Path]) -> tuple[str | None, str | None]:
    """The (created, updated) UTC ISO-8601 timestamps across an item's files' Git history (BE-0311).

    Deriving these from ``git log`` rather than a hand-set metadata field avoids the drift a date
    pair would otherwise accrue on every future edit. Each path is walked separately with
    ``--follow`` (which accepts one path only) so history survives the ``BE-XXXX`` → ``BE-NNNN``
    rename CI does at id allocation; the two files' commit dates are then combined — ``created`` is
    the oldest, ``updated`` the newest — so a Japanese-only fix still moves ``updated``. Every
    timestamp is normalised to UTC so lexical min/max and the dashboard's string sort are
    chronological. Returns ``(None, None)`` when Git yields nothing — a shallow clone, an
    uncommitted file, or no ``git`` on PATH — rather than inventing a date.
    """
    stamps: list[str] = []
    for path in paths:
        try:
            proc = subprocess.run(
                ["git", "log", "--follow", "--format=%aI", "--", str(path)],
                capture_output=True,
                text=True,
                check=False,
            )
        except FileNotFoundError:
            return None, None
        if proc.returncode != 0:
            continue
        for raw in proc.stdout.splitlines():
            line = raw.strip()
            if line:
                stamps.append(datetime.fromisoformat(line).astimezone(UTC).isoformat())
    if not stamps:
        return None, None
    return min(stamps), max(stamps)


def load_items(roadmap: Path, *, with_dates: bool = False) -> list[Item]:
    """Read every BE item directory into an Item with per-language render fields.

    Items live under one flat ``roadmaps/BE-NNNN-<slug>/`` directory (BE-0159), so the link a
    consumer renders to an item is just its directory name. Refuses a tree with duplicate ids, so a
    number reused across two items fails the build. A ``BE-XXXX`` placeholder is skipped: it is not
    numbered yet, so it has nowhere stable to link to.

    Args:
        with_dates: When true, derive each item's ``created`` / ``updated`` from its files' Git
            history (BE-0311). Off by default so the tools that don't render dates skip the per-item
            ``git log`` calls entirely.
    """
    if dupes := duplicate_ids(roadmap):
        detail = "; ".join(f"{be_id}: {', '.join(paths)}" for be_id, paths in sorted(dupes.items()))
        raise ValueError(f"duplicate BE IDs (each id must be unique and permanent): {detail}")
    items: list[Item] = []
    for d in iter_item_dirs(roadmap):
        match = numbered_match(d.name)
        if not match:
            continue
        item_id, slug = f"BE-{match.group(1)}", match.group(2)

        by_lang: dict[str, Entry] = {}
        paths: list[Path] = []
        item_bucket = topic = summary = ""
        related: tuple[str, ...] = ()
        origin_refs: tuple[str, ...] = ()
        superseded_by: tuple[str, ...] = ()
        for lang in LANGS:
            path = d / f"{item_id}-{slug}{lang.suffix}.md"
            paths.append(path)
            text = path.read_text(encoding="utf-8")
            title, fields = parse_metadata(text)
            by_lang[lang.code] = Entry(
                id=item_id,
                slug=slug,
                title=title,
                status=fields[lang.field_status],
                origin=fields.get(lang.field_origin),
            )
            if lang.code == "en":
                item_bucket = bucket(fields[lang.field_status])
                topic = fields[lang.field_topic]
                # The three relation fields are read from the English file alone, exactly as
                # ``Status`` and ``Topic`` already are, so a Japanese-only wording fix can never
                # change what the relations say. The summary is read the same way, for the same
                # reason (BE-0335's relationship-map hover card).
                related = relation_ids(fields.get("Related"), self_id=item_id)
                origin_refs = relation_ids(fields.get("Origin"), self_id=item_id)
                superseded_by = relation_ids(fields.get("Superseded by"), self_id=item_id)
                summary = extract_summary(text)
        if topic not in KNOWN_TOPICS:
            raise ValueError(
                f"{item_id}: unknown Topic {topic!r}; add it to TOPICS (with a key) so it "
                "maps to a section"
            )
        created, updated = _git_dates(paths) if with_dates else (None, None)
        items.append(
            Item(
                id=item_id,
                slug=slug,
                bucket=item_bucket,
                topic=topic,
                by_lang=by_lang,
                created=created,
                updated=updated,
                related=related,
                origin_refs=origin_refs,
                superseded_by=superseded_by,
                summary=summary,
            )
        )
    # A relation is only usable once every item is loaded, since an id can only be checked against
    # the full set. Dropping an id no directory backs keeps a typo in one file from producing a
    # relation with nothing on the other end.
    known = {item.id for item in items}

    def resolved(refs: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(ref for ref in refs if ref in known)

    return [
        replace(
            item,
            related=resolved(item.related),
            origin_refs=resolved(item.origin_refs),
            superseded_by=resolved(item.superseded_by),
        )
        for item in items
    ]

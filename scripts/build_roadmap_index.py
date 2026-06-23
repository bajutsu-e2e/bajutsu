#!/usr/bin/env python3
"""Generate the roadmap index tables in README.md / README-ja.md from per-item metadata.

Every ``roadmaps/<implemented|in-progress|proposals|deferred>/BE-NNNN-<slug>/`` item already
carries the metadata an index row needs (``Status`` / ``Topic`` / ``Origin``) plus its H1 title.
This reads metadata and regenerates the **marker-delimited** table bodies in both index pages, so a
roadmap PR only ever touches its own directory — the shared index never needs a hand-edit,
removing the single largest merge-conflict source (BE-0043). The hand-written section prose
outside the markers is preserved untouched.

Usage::

    python scripts/build_roadmap_index.py            # rewrite the tables in place
    python scripts/build_roadmap_index.py --check     # exit 1 if any table is out of date

The generated regions are bounded by ``<!-- GENERATED:<key> -->`` /
``<!-- /GENERATED:<key> -->`` markers; the section keys below map each table to the items it
lists (by their English ``Status`` bucket + ``Topic``) and to whether it carries an Origin column.
``Status`` is the single source of truth: it decides both an item's folder (BE-0078) and its index
bucket, so the two can never disagree.
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROADMAP = Path("roadmaps")
# Each item lives under one folder named for its Status bucket; it prefixes the item's links.
CATEGORIES = ("implemented", "in-progress", "proposals", "deferred")
NUMBERED_DIR_RE = re.compile(r"^BE-(\d{4})-(.+)$")
TITLE_RE = re.compile(r"^# BE-\d{4} — (.+)$", re.MULTILINE)
# Canonical metadata: a ``| Field | Value |`` table fenced by these markers, mirroring the index's
# ``<!-- GENERATED:* -->`` regions. Fencing keeps the parser off same-shaped tables in the body.
META_BLOCK_RE = re.compile(r"<!-- BE-METADATA -->\n(.*?)\n<!-- /BE-METADATA -->", re.DOTALL)
# A data row inside that block. The header (``| Field …``) is excluded by key; the dash delimiter
# (``|---|``) never matches because there is no space after its leading pipe.
META_ROW_RE = re.compile(r"^\| (.+?) \| (.+?) \|\s*$", re.MULTILINE)
META_HEADER_KEYS = frozenset({"Field", "項目"})
# Legacy form (unmigrated items): ``* Field: value`` bullet lines. Read when no fence is present.
FIELD_RE = re.compile(r"^\* ([^:]+): (.+)$", re.MULTILINE)

# The four index buckets, keyed by an item's raw English Status. ``Status`` is the source of truth
# (BE-0078): this map decides an item's index bucket exactly as ``promote_roadmap_items`` decides its
# folder, so a row's bucket and its directory always agree.
STATUS_BUCKET = {
    "Implemented": "Implemented",
    "In progress": "In progress",
    "Proposal": "Proposals",
    "Proposal (deferred)": "Deferred",
}


@dataclass(frozen=True)
class Lang:
    """Per-language rendering config: which file to read and how to label things."""

    code: str
    suffix: str  # filename suffix before ".md" ("" for English, "-ja" for Japanese)
    index_file: str
    field_status: str
    field_topic: str
    field_origin: str
    status_display: dict[str, str]
    headers: tuple[str, str, str, str]  # column labels: id, item, status, origin


LANGS: tuple[Lang, ...] = (
    Lang(
        code="en",
        suffix="",
        index_file="README.md",
        field_status="Status",
        field_topic="Topic",
        field_origin="Origin",
        status_display={
            "Implemented": "Implemented",
            "In progress": "In progress",
            "Proposal": "Proposal",
            "Proposal (deferred)": "Deferred",
        },
        headers=("ID", "Item", "Status", "Origin"),
    ),
    Lang(
        code="ja",
        suffix="-ja",
        index_file="README-ja.md",
        field_status="状態",
        field_topic="トピック",
        field_origin="由来",
        status_display={
            "実装済み": "実装済み",
            "実装中": "実装中",
            "提案": "提案",
            "提案（保留）": "保留",
        },
        headers=("ID", "項目", "状態", "由来"),
    ),
)
LANG_BY_CODE = {lang.code: lang for lang in LANGS}


@dataclass(frozen=True)
class Section:
    """One generated table, keyed by the English Status bucket + Topic of the items it lists."""

    key: str
    bucket: str
    topic: str
    has_origin: bool


# Ordered to match the layout of the two index pages. The (bucket, topic) pair selects an item into
# exactly one section; adding an item to an existing topic needs no edit here. A topic that spans
# buckets (e.g. an Implemented slice and an In-progress one) has one section per bucket.
SECTIONS: tuple[Section, ...] = (
    # --- Implemented ---------------------------------------------------------
    Section("implemented-milestones", "Implemented", "Milestones (M1–M4)", False),
    Section(
        "implemented-platform-landed", "Implemented", "Platform expansion (landed slices)", False
    ),
    Section(
        "implemented-authoring", "Implemented", "Authoring experience (record / GUI editor)", False
    ),
    Section("implemented-self-healing", "Implemented", "Self-healing triage (M4)", False),
    Section(
        "implemented-competitive",
        "Implemented",
        "Candidates from competitive research (MagicPod / Autify)",
        True,
    ),
    Section(
        "implemented-competitive-maestro",
        "Implemented",
        "Candidates from competitive research (Maestro)",
        True,
    ),
    Section("implemented-mcp", "Implemented", "Integration & automation (MCP)", False),
    Section(
        "implemented-dev-infra",
        "Implemented",
        "Development infrastructure (contributor workflow)",
        False,
    ),
    Section("implemented-dogfood", "Implemented", "Dogfood fixtures (demo apps)", True),
    Section("implemented-dogfood-web-ui", "Implemented", "Dogfood fixtures (web UI)", True),
    Section("implemented-ai-provider", "Implemented", "AI provider configuration", False),
    Section(
        "implemented-hosting", "Implemented", "Hosting the web UI (cloud / self-hosted)", False
    ),
    Section("implemented-codegen", "Implemented", "codegen coverage", False),
    Section("implemented-crawl", "Implemented", "Crawl performance / scale-out", False),
    Section("implemented-misc", "Implemented", "Miscellaneous", False),
    # --- In progress ---------------------------------------------------------
    Section(
        "in-progress-platform-landed", "In progress", "Platform expansion (landed slices)", False
    ),
    Section(
        "in-progress-authoring", "In progress", "Authoring experience (record / GUI editor)", False
    ),
    Section(
        "in-progress-competitive",
        "In progress",
        "Candidates from competitive research (MagicPod / Autify)",
        True,
    ),
    Section(
        "in-progress-competitive-maestro",
        "In progress",
        "Candidates from competitive research (Maestro)",
        True,
    ),
    # --- Proposals -----------------------------------------------------------
    Section("proposals-on-device", "Proposals", "On-device validation (M1 close-out)", False),
    Section(
        "proposals-platform", "Proposals", "Platform expansion (Android / Web / Flutter)", False
    ),
    Section(
        "proposals-authoring", "Proposals", "Authoring experience (record / GUI editor)", False
    ),
    Section("proposals-hosting", "Proposals", "Hosting the web UI (cloud / self-hosted)", False),
    Section("proposals-config-sourcing", "Proposals", "Configuration sourcing", False),
    Section("proposals-backend", "Proposals", "Backend expansion (iOS actuators)", False),
    Section("proposals-doctor", "Proposals", "doctor / onboarding", False),
    Section("proposals-codegen", "Proposals", "codegen coverage", False),
    Section(
        "proposals-dev-infra",
        "Proposals",
        "Development infrastructure (contributor workflow)",
        False,
    ),
    Section(
        "proposals-competitive",
        "Proposals",
        "Candidates from competitive research (MagicPod / Autify)",
        True,
    ),
    Section(
        "proposals-competitive-maestro",
        "Proposals",
        "Candidates from competitive research (Maestro)",
        True,
    ),
    # --- Deferred ------------------------------------------------------------
    Section("deferred-misc", "Deferred", "Miscellaneous", False),
    Section(
        "deferred-competitive",
        "Deferred",
        "Candidates from competitive research (MagicPod / Autify)",
        True,
    ),
)


@dataclass(frozen=True)
class Entry:
    """A single table row's data, already resolved for one language."""

    id: str
    slug: str
    category: str  # subdirectory the item lives in (one of CATEGORIES)
    title: str
    status: str  # raw status, before display mapping
    origin: str | None


@dataclass(frozen=True)
class Item:
    """A roadmap item: its identity, English Status bucket / Topic, and per-language render fields."""

    id: str
    slug: str
    bucket: str  # canonical (English) Status bucket
    topic: str  # canonical (English) topic
    by_lang: dict[str, Entry]


def parse_metadata(text: str) -> tuple[str, dict[str, str]]:
    """Return (H1 title, metadata fields) from a BE item file's body.

    Spec. The metadata block is a ``| Field | Value |`` table fenced by the markers
    ``<!-- BE-METADATA -->`` … ``<!-- /BE-METADATA -->``. Each data row is one ``field -> value``
    (header and dash-delimiter rows excluded), with ``**`` emphasis stripped from the value.
    Fencing the block — like the index's ``<!-- GENERATED:* -->`` regions — keeps the parser off
    same-shaped tables elsewhere in the body. A file without the markers is read by the legacy
    ``* Field: value`` bullet rule, so an unmigrated item still parses. The title is the text after
    the em dash in the ``# BE-NNNN — …`` heading.
    """
    title_match = TITLE_RE.search(text)
    if not title_match:
        raise ValueError("no '# BE-NNNN — <title>' heading found")
    block = META_BLOCK_RE.search(text)
    if block:
        fields = {
            key.strip(): value.replace("**", "").strip()
            for key, value in META_ROW_RE.findall(block.group(1))
            if key.strip() not in META_HEADER_KEYS
        }
    else:  # legacy bullet form, until the item is migrated to the fenced table
        fields = {
            key.strip(): value.replace("**", "").strip() for key, value in FIELD_RE.findall(text)
        }
    return title_match.group(1).strip(), fields


def status_display(raw: str, lang_code: str) -> str:
    """Map a raw metadata Status to the word shown in the index table for that language."""
    lang = LANG_BY_CODE[lang_code]
    try:
        return lang.status_display[raw]
    except KeyError as e:
        raise ValueError(f"unknown {lang_code} status {raw!r}") from e


def render_row(entry: Entry, lang_code: str, has_origin: bool) -> str:
    """Render one Markdown table row for an item in the given language."""
    lang = LANG_BY_CODE[lang_code]
    href = f"{entry.category}/{entry.id}-{entry.slug}/{entry.id}-{entry.slug}{lang.suffix}.md"
    cells = [f"[{entry.id}]({href})", entry.title, status_display(entry.status, lang_code)]
    if has_origin:
        cells.append(entry.origin or "")
    return "| " + " | ".join(cells) + " |"


def render_table(items: list[Item], lang_code: str, has_origin: bool) -> str:
    """Render a full Markdown table (header + delimiter + rows) for one section.

    The whole table sits inside the markers — a GitHub-flavored-Markdown table breaks if an
    HTML comment splits its header from its body, so the markers must wrap the entire table.
    """
    lang = LANG_BY_CODE[lang_code]
    width = 4 if has_origin else 3
    header = "| " + " | ".join(lang.headers[:width]) + " |"
    delimiter = "|" + "|".join(["---"] * width) + "|"
    rows = [
        render_row(item.by_lang[lang_code], lang_code, has_origin)
        for item in sorted(items, key=lambda it: it.id)
    ]
    return "\n".join([header, delimiter, *rows])


def replace_region(text: str, key: str, body: str) -> str:
    """Replace the lines between a region's markers with ``body``, keeping the markers."""
    begin = f"<!-- GENERATED:{key} -->"
    end = f"<!-- /GENERATED:{key} -->"
    pattern = re.compile(rf"({re.escape(begin)}\n).*?(\n{re.escape(end)})", re.DOTALL)
    if not pattern.search(text):
        raise ValueError(f"generated region {key!r} is absent from the document")
    return pattern.sub(lambda m: f"{m.group(1)}{body}{m.group(2)}", text)


def duplicate_ids(roadmap: Path) -> dict[str, list[str]]:
    """Map every BE id used by more than one item directory to those directories' paths.

    IDs are permanent and unique. A duplicate — e.g. two branches racing for ``max + 1`` — must
    fail the build rather than silently render two index rows for one id. Empty when all unique.
    """
    by_id: dict[str, list[str]] = {}
    for category in CATEGORIES:
        category_dir = roadmap / category
        if not category_dir.is_dir():
            continue
        for d in sorted(category_dir.iterdir()):
            if d.is_dir() and (match := NUMBERED_DIR_RE.match(d.name)):
                by_id.setdefault(f"BE-{match.group(1)}", []).append(f"{category}/{d.name}")
    return {be_id: paths for be_id, paths in by_id.items() if len(paths) > 1}


def load_items(roadmap: Path) -> list[Item]:
    """Read every BE item directory into an Item with per-language render fields.

    Items live under ``roadmaps/<category>/BE-NNNN-<slug>/`` — the category (the directory the
    item was filed in by its Status) prefixes every link the index renders to it, and the English
    Status decides the item's index bucket. Refuses a tree with duplicate ids, so a number reused
    across two items fails the build.
    """
    if dupes := duplicate_ids(roadmap):
        detail = "; ".join(f"{be_id}: {', '.join(paths)}" for be_id, paths in sorted(dupes.items()))
        raise ValueError(f"duplicate BE IDs (each id must be unique and permanent): {detail}")
    items: list[Item] = []
    for category in CATEGORIES:
        category_dir = roadmap / category
        if not category_dir.is_dir():
            continue
        for d in sorted(category_dir.iterdir()):
            if not d.is_dir():
                continue
            match = NUMBERED_DIR_RE.match(d.name)
            if not match:
                continue
            item_id, slug = f"BE-{match.group(1)}", match.group(2)

            by_lang: dict[str, Entry] = {}
            bucket = topic = ""
            for lang in LANGS:
                path = d / f"{item_id}-{slug}{lang.suffix}.md"
                title, fields = parse_metadata(path.read_text(encoding="utf-8"))
                by_lang[lang.code] = Entry(
                    id=item_id,
                    slug=slug,
                    category=category,
                    title=title,
                    status=fields[lang.field_status],
                    origin=fields.get(lang.field_origin),
                )
                if lang.code == "en":
                    raw_status = fields[lang.field_status]
                    if raw_status not in STATUS_BUCKET:
                        raise ValueError(f"{item_id}: unknown Status {raw_status!r}")
                    bucket = STATUS_BUCKET[raw_status]
                    topic = fields[lang.field_topic]

            items.append(Item(id=item_id, slug=slug, bucket=bucket, topic=topic, by_lang=by_lang))
    return items


def assign_sections(items: list[Item]) -> dict[str, list[Item]]:
    """Group items by section key; raise if an item matches no section."""
    by_key: dict[str, list[Item]] = {s.key: [] for s in SECTIONS}
    index = {(s.bucket, s.topic): s for s in SECTIONS}
    for item in items:
        section = index.get((item.bucket, item.topic))
        if section is None:
            raise ValueError(
                f"{item.id}: no section for bucket={item.bucket!r} topic={item.topic!r}; "
                "add a Section (and markers) for it"
            )
        by_key[section.key].append(item)
    return by_key


def render_index(items: list[Item], lang_code: str) -> dict[str, str]:
    """Build the marker body (a full table) for every section, in the given language."""
    by_key = assign_sections(items)
    return {
        section.key: render_table(by_key[section.key], lang_code, section.has_origin)
        for section in SECTIONS
    }


def build_index_text(items: list[Item], current: str, lang_code: str) -> str:
    """Apply every section's regenerated body to one index page's current text."""
    bodies = render_index(items, lang_code)
    for section in SECTIONS:
        current = replace_region(current, section.key, bodies[section.key])
    return current


def stale_files(roadmap: Path) -> list[str]:
    """Return the index filenames whose committed content differs from generated output."""
    items = load_items(roadmap)
    stale: list[str] = []
    for lang in LANGS:
        path = roadmap / lang.index_file
        current = path.read_text(encoding="utf-8")
        if build_index_text(items, current, lang.code) != current:
            stale.append(lang.index_file)
    return stale


def _diff(current: str, updated: str, name: str) -> str:
    return "".join(
        difflib.unified_diff(
            current.splitlines(keepends=True),
            updated.splitlines(keepends=True),
            fromfile=f"{name} (committed)",
            tofile=f"{name} (generated)",
        )
    )


def main(argv: list[str]) -> int:
    check = "--check" in argv
    try:
        items = load_items(ROADMAP)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    drift: list[str] = []
    for lang in LANGS:
        path = ROADMAP / lang.index_file
        current = path.read_text(encoding="utf-8")
        updated = build_index_text(items, current, lang.code)
        if updated == current:
            continue
        if check:
            drift.append(lang.index_file)
            sys.stdout.write(_diff(current, updated, lang.index_file))
        else:
            path.write_text(updated, encoding="utf-8")
            print(f"Updated {lang.index_file}")
    if check and drift:
        print(
            "\nRoadmap index is out of date. Run `python scripts/build_roadmap_index.py` "
            "and commit the result.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

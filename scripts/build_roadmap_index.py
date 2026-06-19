#!/usr/bin/env python3
"""Generate the roadmap index tables in README.md / README-ja.md from per-item metadata.

Every ``roadmaps/<implemented|proposals>/BE-NNNN-<slug>/`` item already carries the metadata an
index row needs (``Status`` / ``Track`` / ``Topic`` / ``Origin``) plus its H1 title. This reads
metadata and regenerates the **marker-delimited** table bodies in both index pages, so a
roadmap PR only ever touches its own directory — the shared index never needs a hand-edit,
removing the single largest merge-conflict source (BE-0043). The hand-written section prose
outside the markers is preserved untouched.

Usage::

    python scripts/build_roadmap_index.py            # rewrite the tables in place
    python scripts/build_roadmap_index.py --check     # exit 1 if any table is out of date

The generated regions are bounded by ``<!-- GENERATED:<key> -->`` /
``<!-- /GENERATED:<key> -->`` markers; the section keys below map each table to the items it
lists (by their English ``Track`` + ``Topic``) and to whether it carries an Origin column.
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROADMAP = Path("roadmaps")
CATEGORIES = ("implemented", "proposals")  # each item lives under one; it prefixes its links
NUMBERED_DIR_RE = re.compile(r"^BE-(\d{4})-(.+)$")
TITLE_RE = re.compile(r"^# BE-\d{4} — (.+)$", re.MULTILINE)
FIELD_RE = re.compile(r"^\* ([^:]+): (.+)$", re.MULTILINE)
BRACKET_RE = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class Lang:
    """Per-language rendering config: which file to read and how to label things."""

    code: str
    suffix: str  # filename suffix before ".md" ("" for English, "-ja" for Japanese)
    index_file: str
    field_status: str
    field_track: str
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
        field_track="Track",
        field_topic="Topic",
        field_origin="Origin",
        status_display={
            "Implemented": "Implemented",
            "Accepted, in progress": "In progress",
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
        field_track="トラック",
        field_topic="トピック",
        field_origin="由来",
        status_display={
            "実装済み": "実装済み",
            "可決・実装中": "実装中",
            "提案": "提案",
            "提案（保留）": "保留",
        },
        headers=("ID", "項目", "状態", "由来"),
    ),
)
LANG_BY_CODE = {lang.code: lang for lang in LANGS}


@dataclass(frozen=True)
class Section:
    """One generated table, keyed by the English Track + Topic of the items it lists."""

    key: str
    track: str
    topic: str
    has_origin: bool


# Ordered to match the layout of the two index pages. The (track, topic) pair selects an
# item into exactly one section; adding an item to an existing topic needs no edit here.
SECTIONS: tuple[Section, ...] = (
    Section("accepted-milestones", "Accepted", "Milestones (M1–M4)", False),
    Section("accepted-platform-landed", "Accepted", "Platform expansion (landed slices)", False),
    Section("accepted-authoring", "Accepted", "Authoring experience (record / GUI editor)", False),
    Section("accepted-self-healing", "Accepted", "Self-healing triage (M4)", False),
    Section(
        "accepted-competitive",
        "Accepted",
        "Candidates from competitive research (MagicPod / Autify)",
        True,
    ),
    Section("accepted-mcp", "Accepted", "Integration & automation (MCP)", False),
    Section(
        "accepted-dev-infra",
        "Accepted",
        "Development infrastructure (contributor workflow)",
        False,
    ),
    Section("accepted-dogfood", "Accepted", "Dogfood fixtures (demo apps)", True),
    Section("proposals-on-device", "Proposals", "On-device validation (M1 close-out)", False),
    Section(
        "proposals-platform", "Proposals", "Platform expansion (Android / Web / Flutter)", False
    ),
    Section(
        "proposals-authoring", "Proposals", "Authoring experience (record / GUI editor)", False
    ),
    Section("proposals-hosting", "Proposals", "Hosting the web UI (cloud / self-hosted)", False),
    Section("proposals-mcp", "Proposals", "Integration & automation (MCP)", False),
    Section("proposals-backend", "Proposals", "Backend expansion (iOS actuators)", False),
    Section("proposals-doctor", "Proposals", "doctor / onboarding", False),
    Section("proposals-codegen", "Proposals", "codegen coverage", False),
    Section("proposals-misc", "Proposals", "Miscellaneous / on hold", False),
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
)


@dataclass(frozen=True)
class Entry:
    """A single table row's data, already resolved for one language."""

    id: str
    slug: str
    category: str  # subdirectory the item lives in ("implemented" / "proposals")
    title: str
    status: str  # raw status, before display mapping
    origin: str | None


@dataclass(frozen=True)
class Item:
    """A roadmap item: its identity, English Track/Topic, and per-language render fields."""

    id: str
    slug: str
    track: str  # canonical (English) track
    topic: str  # canonical (English) topic
    by_lang: dict[str, Entry]


def parse_metadata(text: str) -> tuple[str, dict[str, str]]:
    """Return (H1 title, metadata fields) from a BE item file's body.

    Fields are the ``* Key: value`` lines of the metadata block, with ``**`` emphasis stripped
    from the value. The title is the text after the em dash in the ``# BE-NNNN — …`` heading.
    """
    title_match = TITLE_RE.search(text)
    if not title_match:
        raise ValueError("no '# BE-NNNN — <title>' heading found")
    fields = {key.strip(): value.replace("**", "").strip() for key, value in FIELD_RE.findall(text)}
    return title_match.group(1).strip(), fields


def track_label(value: str) -> str:
    """Extract the human label from a Track value like ``[Accepted](../README.md#accepted)``."""
    match = BRACKET_RE.search(value)
    return match.group(1) if match else value.strip()


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


def load_items(roadmap: Path) -> list[Item]:
    """Read every BE item directory into an Item with per-language render fields.

    Items live under ``roadmaps/<category>/BE-NNNN-<slug>/`` — the category (the directory the
    item was filed in by its Status) prefixes every link the index renders to it.
    """
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
            track = topic = ""
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
                    track = track_label(fields[lang.field_track])
                    topic = fields[lang.field_topic]

            items.append(Item(id=item_id, slug=slug, track=track, topic=topic, by_lang=by_lang))
    return items


def assign_sections(items: list[Item]) -> dict[str, list[Item]]:
    """Group items by section key; raise if an item matches no section."""
    by_key: dict[str, list[Item]] = {s.key: [] for s in SECTIONS}
    index = {(s.track, s.topic): s for s in SECTIONS}
    for item in items:
        section = index.get((item.track, item.topic))
        if section is None:
            raise ValueError(
                f"{item.id}: no section for track={item.track!r} topic={item.topic!r}; "
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
    items = load_items(ROADMAP)
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

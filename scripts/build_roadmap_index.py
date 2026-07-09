#!/usr/bin/env python3
"""Generate the roadmap index tables in README.md / README-ja.md from per-item metadata.

Every ``roadmaps/<category>/BE-NNNN-<slug>/`` item already carries the metadata an index row needs
(``Status`` / ``Topic`` / ``Origin``) plus its H1 title. This reads metadata and regenerates the
**marker-delimited** table bodies in both index pages, so a roadmap PR only ever touches its own
directory — the shared index never needs a hand-edit, removing the single largest merge-conflict
source (BE-0043). The hand-written section prose outside the markers is preserved untouched.

The index has four top-level buckets, ordered most-progressed first — Implemented / In progress /
Proposals / Deferred — and each item's bucket is **derived from its ``Status``** (BE-0078), not a
hand-set ``Track`` field. Inside a bucket, ``Topic`` is the secondary grouping; a topic that has
items in more than one bucket appears once per bucket, each as its own marked section.

Usage::

    python scripts/build_roadmap_index.py            # rewrite the tables in place
    python scripts/build_roadmap_index.py --check     # exit 1 if any table is out of date

The generated regions are bounded by ``<!-- GENERATED:<key> -->`` /
``<!-- /GENERATED:<key> -->`` markers; a section's key is ``<bucket-key>-<topic-key>`` (see
``BUCKETS`` / ``TOPICS``), and only ``(bucket, topic)`` pairs that actually have an item get a
section — so adding the first item of a topic to a bucket needs a new marker pair in the page.
"""

from __future__ import annotations

import difflib
import re
import sys
from dataclasses import dataclass
from pathlib import Path

# Import the shared id-shape predicate whether this file is run as ``python3 scripts/…`` (scripts/
# already on the path) or loaded under its bare name by a test — add scripts/ so the sibling import
# resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from roadmap_ids import iter_item_dirs, numbered_match

ROADMAP = Path("roadmaps")
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


# Status -> the index bucket (a top-level page heading) it lands in. Derived from Status, not a
# hand-set Track field (BE-0078): the lone lifecycle field decides both an item's folder and its
# index bucket, so the two can never disagree.
STATUS_TO_BUCKET = {
    "Implemented": "Implemented",
    "In progress": "In progress",
    "Proposal": "Proposals",
    "Proposal (deferred)": "Deferred",
}
# Buckets in page order (most-progressed first), each with the key fragment its sections' markers
# use. A section key is ``<bucket-key>-<topic-key>``.
BUCKETS: tuple[tuple[str, str], ...] = (
    ("Implemented", "implemented"),
    ("In progress", "in-progress"),
    ("Proposals", "proposals"),
    ("Deferred", "deferred"),
)
# Every topic, in the order it appears inside a bucket, with its marker key fragment and whether it
# carries an Origin column. A topic with items in more than one bucket is rendered once per bucket.
TOPICS: tuple[tuple[str, str, bool], ...] = (
    ("Milestones (M1–M4)", "milestones", False),
    ("Platform expansion (landed slices)", "platform-landed", False),
    ("Platform expansion (Android / Web / Flutter)", "platform", False),
    ("Authoring experience (record / GUI editor)", "authoring", False),
    ("Record (action capture)", "record", False),
    ("Surfacing CLI features in the serve Web UI", "serve-cli-features", False),
    ("Self-healing triage (M4)", "self-healing", False),
    ("Candidates from competitive research (MagicPod / Autify)", "competitive", True),
    ("Candidates from competitive research (Maestro)", "competitive-maestro", True),
    ("Integration & automation (MCP)", "mcp", False),
    ("Integration with external services", "external-integration", False),
    ("Backend expansion (iOS actuators)", "backend", False),
    ("doctor / onboarding", "doctor", False),
    ("Development infrastructure (contributor workflow)", "developer-experience", False),
    # Path-only infra topic: a real topic key so PATH_TOPIC_* rules for `.github/`, `.githooks/`,
    # `scripts/`, `Makefile` can label CI/build changes without co-opting the contributor-workflow
    # items' `topic:developer-experience`. It carries no roadmap items (every dev item's Topic is the
    # contributor-workflow line above), so it renders no index/dashboard section — it exists only to
    # keep `dev-infra` a valid label key for the path rules.
    ("CI / build infrastructure", "dev-infra", False),
    ("Codebase quality & technical debt", "quality-debt", False),
    ("Dogfood fixtures (demo apps)", "dogfood", True),
    ("Dogfood fixtures (web UI)", "dogfood-web-ui", True),
    ("AI provider configuration", "ai-provider", False),
    ("AI usage and cost observability", "ai-usage", False),
    ("Hosting the web UI (cloud / self-hosted)", "hosting", False),
    ("Security hardening", "security", False),
    ("Configuration sourcing", "config-sourcing", False),
    ("codegen coverage", "codegen", False),
    ("Crawl performance / scale-out", "crawl", False),
    ("On-device validation (M1 close-out)", "on-device", False),
    ("Miscellaneous / on hold", "misc", False),
)
KNOWN_TOPICS = frozenset(topic for topic, _key, _origin in TOPICS)
TOPIC_KEY_BY_NAME = {topic: key for topic, key, _origin in TOPICS}
BUCKET_KEY_BY_NAME = dict(BUCKETS)


@dataclass(frozen=True)
class Section:
    """One generated table: the items of a single Topic that sit in one bucket."""

    key: str
    bucket: str
    topic: str
    has_origin: bool


def bucket(status: str) -> str:
    """The index bucket an item with this (English) ``Status`` belongs in (BE-0078)."""
    try:
        return STATUS_TO_BUCKET[status]
    except KeyError as e:
        raise ValueError(f"unknown status {status!r}") from e


def sections_for(items: list[Item]) -> list[Section]:
    """The sections present in this tree, in page order (bucket-major, topic-minor).

    Only ``(bucket, topic)`` pairs that actually have an item get a section — so the first item of
    a topic to reach a bucket needs a new marker pair in the page (``replace_region`` will say so),
    and an emptied section drops out rather than rendering a header with no rows.
    """
    present = {(item.bucket, item.topic) for item in items}
    return [
        Section(f"{bucket_key}-{topic_key}", bucket_name, topic, has_origin)
        for bucket_name, bucket_key in BUCKETS
        for topic, topic_key, has_origin in TOPICS
        if (bucket_name, topic) in present
    ]


@dataclass(frozen=True)
class Entry:
    """A single table row's data, already resolved for one language."""

    id: str
    slug: str
    title: str
    status: str  # raw status, before display mapping
    origin: str | None


@dataclass(frozen=True)
class Item:
    """A roadmap item: its identity, index bucket + Topic, and per-language render fields."""

    id: str
    slug: str
    bucket: str  # index bucket, derived from the English Status (BE-0078)
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
    href = f"{entry.id}-{entry.slug}/{entry.id}-{entry.slug}{lang.suffix}.md"
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
    for d in iter_item_dirs(roadmap):
        if match := numbered_match(d.name):
            by_id.setdefault(f"BE-{match.group(1)}", []).append(d.name)
    return {be_id: paths for be_id, paths in by_id.items() if len(paths) > 1}


def load_items(roadmap: Path) -> list[Item]:
    """Read every BE item directory into an Item with per-language render fields.

    Items live under one flat ``roadmaps/BE-NNNN-<slug>/`` directory (BE-0159), so the link the index
    renders to an item is just its directory name. Refuses a tree with duplicate ids, so a number
    reused across two items fails the build.
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
        item_bucket = topic = ""
        for lang in LANGS:
            path = d / f"{item_id}-{slug}{lang.suffix}.md"
            title, fields = parse_metadata(path.read_text(encoding="utf-8"))
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
        if topic not in KNOWN_TOPICS:
            raise ValueError(
                f"{item_id}: unknown Topic {topic!r}; add it to TOPICS (with a key) so it "
                "maps to a section"
            )
        items.append(Item(id=item_id, slug=slug, bucket=item_bucket, topic=topic, by_lang=by_lang))
    return items


def assign_sections(items: list[Item]) -> dict[str, list[Item]]:
    """Group items by section key (bucket + topic)."""
    sections = sections_for(items)
    by_key: dict[str, list[Item]] = {s.key: [] for s in sections}
    index = {(s.bucket, s.topic): s for s in sections}
    for item in items:
        by_key[index[(item.bucket, item.topic)].key].append(item)
    return by_key


def render_index(items: list[Item], lang_code: str) -> dict[str, str]:
    """Build the marker body (a full table) for every section, in the given language."""
    by_key = assign_sections(items)
    return {
        section.key: render_table(by_key[section.key], lang_code, section.has_origin)
        for section in sections_for(items)
    }


def _marker_keys(text: str) -> set[str]:
    """Return the set of section keys that have an opening ``<!-- GENERATED:<key> -->`` marker.

    Only matches keys made of word-chars and hyphens (the format ``<bucket>-<topic>``), so
    wildcard references like ``GENERATED:*`` in prose are ignored.
    """
    return set(re.findall(r"<!-- GENERATED:([\w-]+) -->", text))


def _paired_marker_keys(text: str) -> set[str]:
    """Return the keys that have **both** an opening and a closing ``GENERATED`` marker.

    A lone opening marker is not a usable section: ``replace_region`` needs the closing marker too
    and would crash on it. So the guard counts a section as present only when the pair is intact.
    """
    closing = set(re.findall(r"<!-- /GENERATED:([\w-]+) -->", text))
    return _marker_keys(text) & closing


def build_index_text(items: list[Item], current: str, lang_code: str) -> str:
    """Apply every section's regenerated body to one index page's current text.

    Sections that still have items get a regenerated table; sections whose markers exist in the
    page but no longer have any items get their body cleared (an empty region between markers).
    """
    bodies = render_index(items, lang_code)  # keyed in section order
    for key, body in bodies.items():
        current = replace_region(current, key, body)
    for stale_key in _marker_keys(current) - bodies.keys():
        current = replace_region(current, stale_key, "")
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


def required_section_keys(roadmap: Path) -> dict[str, str]:
    """Map every ``<bucket>-<topic>`` section key an item needs to the directory that first needs it.

    Scans **every** item directory — placeholders (``BE-XXXX``) included, unlike ``load_items``,
    which the index render skips. This is what closes the gap that let the missing-section failure
    reach ``main``: a placeholder that introduces a topic into a bucket needs that section to exist
    *before* the ``roadmap-id`` automation numbers it and the reindex tries to fill the region.
    """
    required: dict[str, str] = {}
    for d in iter_item_dirs(roadmap):
        fields = metadata_fields((d / f"{d.name}.md").read_text(encoding="utf-8"))
        topic = fields["Topic"]
        if topic not in TOPIC_KEY_BY_NAME:
            raise ValueError(
                f"{d.name}: unknown Topic {topic!r}; add it to TOPICS (with a key) so it "
                "maps to a section"
            )
        key = f"{BUCKET_KEY_BY_NAME[bucket(fields['Status'])]}-{TOPIC_KEY_BY_NAME[topic]}"
        required.setdefault(key, d.name)
    return required


def missing_section_markers(roadmap: Path) -> list[str]:
    """Report every section an item needs but an index page lacks the ``GENERATED`` markers for.

    Empty when both index pages carry a marker pair for each item's ``(bucket, topic)`` — the
    invariant the render silently assumes. Non-empty entries name the page, the missing key, and
    the item, so the fix (add the heading + marker pair) is unambiguous.
    """
    required = required_section_keys(roadmap)
    marker_sets = {
        lang.index_file: _paired_marker_keys(
            (roadmap / lang.index_file).read_text(encoding="utf-8")
        )
        for lang in LANGS
    }
    return [
        f"{index_file}: no '<!-- GENERATED:{key} -->' section for {item_dir} "
        "(add the heading + marker pair for this Topic under its bucket)"
        for key, item_dir in sorted(required.items())
        for index_file, keys in marker_sets.items()
        if key not in keys
    ]


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
        missing = missing_section_markers(ROADMAP)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    if missing:
        print("\n".join(missing), file=sys.stderr)
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

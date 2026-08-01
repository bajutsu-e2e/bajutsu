#!/usr/bin/env python3
"""Generate the roadmap status dashboard page for the docs site (BE-XXXX).

The roadmap's source of truth is the per-item metadata under ``roadmaps/<category>/BE-NNNN-<slug>/``
— read through the shared loader in ``build_roadmap_index.py``. This renders that live metadata as a
single self-contained HTML dashboard, ``docs/api/roadmap.md``, that the existing MkDocs site
publishes to GitHub Pages: cards grouped by category (Topic), each card carrying its own status
(Implemented / In progress / Proposal / Deferred) and linking to its item on GitHub. Each category
shows a progress figure — the share of its items that are Implemented — and a stacked bar of its
full status composition, and fully-implemented categories are grouped separately under Completed.
This dashboard is the only place any item's status is browsable — ``roadmaps/README.md`` /
``README-ja.md`` carry no generated status tables of their own.

Like the generated API reference (``site/``), the page is a **build artifact, never committed**: it
is regenerated from the live tree on every docs build, so it can never drift from the roadmap and is
never coupled to the CI BE-id-allocation machinery. ``make docs`` / ``make docs-serve`` regenerate
it first; the ``docs`` workflow does the same before publishing.

Usage::

    python scripts/build_roadmap_dashboard.py  # write docs/api/roadmap.md
    python scripts/build_roadmap_dashboard.py --out PATH  # write elsewhere (tests)
    python scripts/build_roadmap_dashboard.py --emit-script  # print the embedded filter JS (lint-js)

Only facts the metadata carries are shown. The per-category progress percentage is derived purely
from the Status field (Implemented items / total items in the category), so it has a source of truth;
no per-item completion figure is invented — that lives in no item's metadata.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
import posixpath
import re
import sys
from pathlib import Path
from typing import Any

_SCRIPTS = Path(__file__).resolve().parent
_INDEX_MODULE = _SCRIPTS / "build_roadmap_index.py"
_spec = importlib.util.spec_from_file_location("build_roadmap_index", _INDEX_MODULE)
assert _spec and _spec.loader
bri = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = bri  # let dataclasses resolve annotations during exec
_spec.loader.exec_module(bri)

ROOT = _SCRIPTS.parent
DEFAULT_OUT = ROOT / "docs" / "api" / "roadmap.md"
# Matches ``repo_url`` in mkdocs.yml; cards link to each item's English file on the default branch.
REPO_BLOB = "https://github.com/bajutsu-e2e/bajutsu/blob/main"

# Bucket -> the accent colour its cards carry. Greens read as shipped, amber as in flight, indigo as
# proposed, grey as parked — the same lifecycle ordering the index uses (most-progressed first).
BUCKET_COLOR: dict[str, str] = {
    "Implemented": "#3B6D11",
    "In progress": "#BA7517",
    "Proposals": "#534AB7",
    "Deferred": "#5F5E5A",
}
# The singular status word shown on each card's badge (the bucket name is the plural index heading).
BUCKET_LABEL: dict[str, str] = {
    "Implemented": "Implemented",
    "In progress": "In progress",
    "Proposals": "Proposal",
    "Deferred": "Deferred",
}


def _item_dir_name(en: Any) -> str:
    """An item's flat ``roadmaps/`` directory name (``BE-NNNN-slug``, BE-0159)."""
    return f"{en.id}-{en.slug}"


def _item_href(item: Any) -> str:
    """The GitHub URL of an item's English markdown file (flat ``roadmaps/`` path, BE-0159)."""
    name = _item_dir_name(item.by_lang["en"])
    return f"{REPO_BLOB}/roadmaps/{name}/{name}.md"


def _search_terms(item: Any) -> str:
    """The lower-cased id/title/topic/status every view filters an item on (BE-0219)."""
    en = item.by_lang["en"]
    return f"{en.id} {en.title} {item.topic} {item.bucket}".lower()


def _search_text(item: Any) -> str:
    """The escaped form of :func:`_search_terms`, for the ``data-search`` a card and row carry.

    Emitted as ``data-search`` on both, so the filter script reads one ready-made string instead of
    scraping the markup — and the card and its table row always match on the same tokens. The graph
    view carries the same string inside its JSON payload, unescaped, so all three filter alike.
    """
    return html.escape(_search_terms(item))


def _issue_pill(item_id: str) -> str:
    """The additive "Issue" pill linking to an item's BE-0109 tracking-issue search (BE-0139).

    Shared verbatim by the card and its table row (BE-0311) so the two can't silently drift apart —
    the link is a search that can legitimately return zero results (a "born implemented" item never
    opened a tracking issue), hence "Issue" rather than a promise of a guaranteed issue.
    """
    url = html.escape(bri.tracking_issue_url(item_id))
    return (
        f'<a class="be-issue" href="{url}" title="Search GitHub for this item&#39;s '
        'tracking issue (may have no results)">Issue</a>'
    )


# An ``Origin`` field is free text, sometimes a markdown link to another item written relative to
# *its own* directory (e.g. ``[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md)``
# — correct from inside ``roadmaps/<this-item>/``, meaningless once embedded verbatim in this page,
# which lives under ``docs/api/``). Resolving it into an absolute GitHub URL at render time, rather
# than reproducing the raw relative text, keeps the link real without ever landing a stray
# ``roadmaps/**``-shaped path in the generated file for ``lint-roadmap`` to flag as broken.
_ORIGIN_LINK_RE = re.compile(r"\[(?P<text>[^\]]+)\]\((?P<path>[^)\s]+)\)")


def _render_origin(origin: str, item_dir: str) -> str:
    """Render an ``Origin`` field as safe HTML, resolving any embedded item-relative link.

    ``Origin`` is free-form author text: a link target is usually item-relative (see above), but an
    absolute one (e.g. an issue URL) is left verbatim rather than run through ``posixpath.normpath``,
    which would mangle it into a nonsensical ``roadmaps/<item>/https:/...`` path.
    """
    parts: list[str] = []
    pos = 0
    for m in _ORIGIN_LINK_RE.finditer(origin):
        parts.append(html.escape(origin[pos : m.start()]))
        path = m.group("path")
        if "://" in path or path.startswith("/"):
            href = html.escape(path)
        else:
            resolved = posixpath.normpath(f"roadmaps/{item_dir}/{path}")
            href = html.escape(f"{REPO_BLOB}/{resolved}")
        parts.append(f'<a href="{href}">{html.escape(m.group("text"))}</a>')
        pos = m.end()
    parts.append(html.escape(origin[pos:]))
    return "".join(parts)


def _card(item: Any) -> str:
    en = item.by_lang["en"]
    color = BUCKET_COLOR[item.bucket]
    label = BUCKET_LABEL[item.bucket]
    origin = (
        f'<span class="be-origin">{_render_origin(en.origin, _item_dir_name(en))}</span>'
        if en.origin
        else ""
    )
    # The card's primary click target stays the proposal file (the whole main link); the Issue pill is
    # an additive second link, built from the id alone. The two are sibling <a>s under a <div> rather
    # than one nested in the other, since nested anchors are invalid HTML.
    return (
        f'<div class="be-card" data-status="{html.escape(item.bucket)}" '
        f'data-topic="{html.escape(item.topic)}" data-search="{_search_text(item)}" '
        f'style="border-left-color:{color}">'
        f'<a class="be-card-main" href="{_item_href(item)}">'
        '<span class="be-card-top">'
        f'<span class="be-id" style="color:{color}">{html.escape(en.id)}</span>'
        f'<span class="be-badge" style="color:{color};border-color:{color}">{html.escape(label)}</span>'
        "</span>"
        f'<span class="be-title">{html.escape(en.title)}</span>'
        f"{origin}"
        "</a>"
        f"{_issue_pill(en.id)}"
        "</div>"
    )


def _progress_bar(counts: dict[str, int], total: int) -> str:
    """A stacked bar of a category's status composition (one coloured segment per non-zero bucket)."""
    segments = "".join(
        f'<span style="width:{100 * counts[name] / total:.2f}%;'
        f'background:{BUCKET_COLOR[name]}" title="{counts[name]} {html.escape(name)}"></span>'
        for name, _key in bri.BUCKETS
        if counts[name]
    )
    return f'<div class="be-bar">{segments}</div>'


def _topic_progress(cat_items: list[Any]) -> tuple[dict[str, int], int, int]:
    """A topic's per-bucket counts, item total, and implemented-share percentage — one derivation.

    Shared by the card sections and the table view's progress strip so both show the same figure
    (BE-0311); the percentage is purely a function of Status, so it always has a source of truth.
    """
    counts = {name: sum(1 for it in cat_items if it.bucket == name) for name, _key in bri.BUCKETS}
    total = len(cat_items)
    return counts, total, round(100 * counts["Implemented"] / total)


def _date_cell(iso: str | None) -> str:
    """A Created/Updated cell (BE-0311): the day for a reader, the full UTC ISO for the sort.

    ``data-sort`` holds the UTC timestamp (or "" when unknown) so the client sort is a plain,
    correct string comparison; the visible text is just the ``YYYY-MM-DD`` day.
    """
    if not iso:
        return '<td class="be-date" data-sort="">—</td>'
    return f'<td class="be-date" data-sort="{html.escape(iso)}">{html.escape(iso[:10])}</td>'


def _row(item: Any) -> str:
    """One table row mirroring the item's card: same status/topic attributes, plus the two dates.

    ``data-search`` carries the same id/title/topic/status text the card exposes, so the search box
    and status chips filter rows with no separate matching logic (BE-0311).
    """
    en = item.by_lang["en"]
    color = BUCKET_COLOR[item.bucket]
    label = BUCKET_LABEL[item.bucket]
    return (
        f'<tr class="be-row" data-status="{html.escape(item.bucket)}" '
        f'data-topic="{html.escape(item.topic)}" data-search="{_search_text(item)}">'
        # id column sorts on the zero-padded number ("0311"), so the string compare is numeric.
        f'<td data-sort="{html.escape(en.id[3:])}">{html.escape(en.id)}</td>'
        f'<td class="be-row-title"><a href="{_item_href(item)}">{html.escape(en.title)}</a></td>'
        f"<td>{html.escape(item.topic)}</td>"
        f'<td><span class="be-badge" style="color:{color};border-color:{color}">'
        f"{html.escape(label)}</span></td>"
        f"{_date_cell(item.created)}"
        f"{_date_cell(item.updated)}"
        # The same additive tracking-issue pill the card carries; a trailing, non-sortable column,
        # so it lines up after the six sortable ones without shifting their th/td indices.
        f"<td>{_issue_pill(en.id)}</td>"
        "</tr>"
    )


# The six sortable columns, in render order: (data-sort-key, header label).
_TABLE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("id", "ID"),
    ("title", "Title"),
    ("topic", "Topic"),
    ("status", "Status"),
    ("created", "Created"),
    ("updated", "Updated"),
)


def _table(items: list[Any]) -> str:
    """The flat sortable table (BE-0311): one row per item in id order, six sortable columns.

    A trailing, unsortable "Issue" column follows the six (BE-0139 parity with the card's pill);
    appending it after every sortable ``th`` keeps their 0-based indices — which the sort script
    reads off ``th[data-sort-key]``'s position — unchanged.
    """
    heads = "".join(
        f'<th data-sort-key="{key}" aria-sort="none" role="columnheader" tabindex="0">'
        f"{html.escape(label)}</th>"
        for key, label in _TABLE_COLUMNS
    )
    heads += "<th>Issue</th>"
    rows = "".join(_row(it) for it in sorted(items, key=lambda it: it.id))
    return f'<table class="be-table"><thead><tr>{heads}</tr></thead><tbody>{rows}</tbody></table>'


def _progress_strip(by_topic: dict[str, list[Any]]) -> str:
    """A compact per-topic progress list above the table, keeping Cards view's progress figure.

    Reuses :func:`_progress_bar` and the same implemented-share percentage the card sections show,
    so the table view doesn't drop the per-topic progress a reader relies on (BE-0311).
    """
    entries = ""
    for topic, _key, _origin in bri.TOPICS:
        cat_items = by_topic[topic]
        if not cat_items:
            continue
        counts, total, pct = _topic_progress(cat_items)
        entries += (
            '<div class="be-strip-row">'
            f'<span class="be-strip-name">{html.escape(topic)}</span>'
            f'<span class="be-strip-pct">{pct}%</span>'
            f"{_progress_bar(counts, total)}"
            "</div>"
        )
    return f'<div class="be-strip">{entries}</div>'


# One edge per unordered pair of items, whatever relations the two declare between them: an item and
# the item it grew out of are usually listed as Related as well, and drawing both would put two lines
# between the same two nodes. The most specific relation wins, so the surviving edge is the one that
# says the most — a replacement outranks a derivation, which outranks a plain association.
_EDGE_PRECEDENCE: tuple[str, ...] = ("superseded", "origin", "related")


def _edges(items: list[Any]) -> list[dict[str, str]]:
    """Every relation between two loaded items, deduped to one edge per pair.

    ``Related`` is mutual — the repository's reciprocal-links rule expects both items to declare it —
    so its two directions collapse into a single edge whose endpoints are stored in id order.
    ``Origin`` and ``Superseded by`` stay directed, since "grew out of" and "was replaced by" each
    read one way only; ``source`` and ``target`` record which way.
    """
    best: dict[tuple[str, str], dict[str, str]] = {}

    def offer(source: str, target: str, kind: str) -> None:
        key = (source, target) if source < target else (target, source)
        current = best.get(key)
        if current is None or _EDGE_PRECEDENCE.index(kind) < _EDGE_PRECEDENCE.index(
            current["kind"]
        ):
            best[key] = {"source": source, "target": target, "kind": kind}

    for item in items:
        for ref in item.superseded_by:
            offer(item.id, ref, "superseded")
        for ref in item.origin_refs:
            offer(ref, item.id, "origin")
        for ref in item.related:
            first, second = sorted((item.id, ref))
            offer(first, second, "related")
    return [best[key] for key in sorted(best)]


def graph_data(items: list[Any]) -> dict[str, Any]:
    """The map's data: the items taking part in a relation, and the relations between them.

    A node is drawn for every item on some edge, which is *participation* in a relation rather than
    *declaration* of one: ``Origin`` is one-directional, and ``Related``'s expected reciprocity is a
    convention rather than a checked rule, so an item named only by another still belongs in the
    picture. An item on no edge has nothing to draw and would only crowd it, so it is left out and
    counted instead: ``total`` and ``unlinked`` let the page state that omission outright rather than
    let the view imply a completeness it does not have (BE-0094's honesty rule). Each node carries the
    same lower-cased search string its card does, so the search box and status chips narrow the map on
    exactly the tokens they narrow the other two views on.
    """
    edges = _edges(items)
    linked = {end for edge in edges for end in (edge["source"], edge["target"])}
    nodes = [
        {
            "id": item.by_lang["en"].id,
            "title": item.by_lang["en"].title,
            "topic": item.topic,
            "status": item.bucket,
            "href": _item_href(item),
            "search": _search_terms(item),
            "summary": item.summary,
        }
        for item in sorted(items, key=lambda it: it.id)
        if item.id in linked
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "total": len(items),
        "unlinked": len(items) - len(nodes),
    }


# The map's geometry, in the SVG's own units. One row per topic, its items along it at STEP apart;
# LABEL_GUTTER is the strip on the left the row names are right-aligned into.
LABEL_GUTTER = 272.0
RULE_INSET = 12.0  # the rule starts this far right of the gutter's edge
STEP = 48.0
ROW_STEP = 62.0
TOP_MARGIN = 44.0
BOTTOM_MARGIN = 40.0
RIGHT_MARGIN = 28.0
LABEL_DROP = 17.0  # an id label sits this far below its dot
ARC_RISE = 26.0  # how far a same-row connector bulges above the row


def map_layout(items: list[Any]) -> dict[str, Any]:
    """Place the drawn items on a transit map: one row per topic, items along it in id order.

    A pure function of the metadata — topic decides the row, id decides the slot — so the same
    roadmap always yields the same coordinates and a test can assert them, which no force simulation
    could offer. Each row is packed with its own items rather than sharing one identifier axis with
    every other row: one axis is the more informative arrangement, since a column would then mean the
    same moment in every row, but roughly 200 items on it spans some 14,000 units, nearly all of it
    the whitespace of rows holding a handful of items each.

    Returns the rows (name, y, and the rule's extent), the placed nodes, the connectors, and the
    drawing's size — everything the view needs to render without measuring anything.
    """
    data = graph_data(items)
    by_topic: dict[str, list[dict[str, Any]]] = {}
    for node in data["nodes"]:
        by_topic.setdefault(node["topic"], []).append(node)

    # Rows follow the index's canonical topic order, so the map reads in the same order as the cards.
    ordered = [topic for topic, _key, _origin in bri.TOPICS if topic in by_topic]
    widest = max((len(by_topic[topic]) for topic in ordered), default=0)
    plot_left = LABEL_GUTTER + RULE_INSET + STEP / 2
    width = plot_left + max(widest - 1, 0) * STEP + STEP / 2 + RIGHT_MARGIN
    rule_right = width - RIGHT_MARGIN

    rows: list[dict[str, Any]] = []
    placed: dict[str, dict[str, Any]] = {}
    for index, topic in enumerate(ordered):
        y = TOP_MARGIN + index * ROW_STEP
        rows.append({"topic": topic, "y": y, "x1": LABEL_GUTTER + RULE_INSET, "x2": rule_right})
        for slot, node in enumerate(sorted(by_topic[topic], key=lambda n: n["id"])):
            placed[node["id"]] = {**node, "x": plot_left + slot * STEP, "y": y}

    height = TOP_MARGIN + max(len(rows) - 1, 0) * ROW_STEP + BOTTOM_MARGIN
    connectors = [
        {**edge, "d": _connector(placed[edge["source"]], placed[edge["target"]])}
        for edge in data["edges"]
    ]
    return {
        "rows": rows,
        "nodes": [placed[node["id"]] for node in data["nodes"]],
        "connectors": connectors,
        "width": width,
        "height": height,
        "total": data["total"],
        "unlinked": data["unlinked"],
    }


def _connector(a: dict[str, Any], b: dict[str, Any]) -> str:
    """The path joining two placed items, shaped by whether they share a row.

    Two items on one row are joined by an arc bulging above it; two on different rows by a curve that
    leaves one row horizontally and arrives at the other horizontally. Both read as a single line
    following the rows, where a straight segment would cut across them.
    """
    if a["y"] == b["y"]:
        mid = (a["x"] + b["x"]) / 2
        return (
            f"M{a['x']:.1f},{a['y']:.1f} Q{mid:.1f},{a['y'] - ARC_RISE:.1f} "
            f"{b['x']:.1f},{b['y']:.1f}"
        )
    reach = abs(b["x"] - a["x"]) * 0.4 or STEP * 0.4
    lead = reach if b["x"] >= a["x"] else -reach
    return (
        f"M{a['x']:.1f},{a['y']:.1f} C{a['x'] + lead:.1f},{a['y']:.1f} "
        f"{b['x'] - lead:.1f},{b['y']:.1f} {b['x']:.1f},{b['y']:.1f}"
    )


def marker_end(kind: str) -> str:
    """The ``marker-end`` attribute a connector carries, empty for the undirected ``related`` kind."""
    return "" if kind == "related" else ' marker-end="url(#be-map-arrow)"'


def _map_node(node: dict[str, Any]) -> str:
    """One stop on the map: a hit target, a status dot, an id label, and a real link around them.

    The id label is too small to carry a title, so the item's name rides along in ``data-caption``
    for the readout and in ``<title>`` for a tooltip; naming it once here keeps the three spellings
    of it from drifting apart. ``data-title`` and ``data-summary`` carry the same title plus the
    item's Introduction excerpt (:func:`build_roadmap_index.extract_summary`) for the hover/focus
    card, which shows more than the single-line readout can.
    """
    name = html.escape(f"{node['id']} — {node['title']}")
    return (
        f'<a class="be-map-node" href="{node["href"]}" data-id="{html.escape(node["id"])}" '
        f'data-status="{html.escape(node["status"])}" data-search="{html.escape(node["search"])}" '
        f'data-caption="{name}" data-title="{html.escape(node["title"])}" '
        f'data-summary="{html.escape(node["summary"])}" '
        f'data-status-label="{html.escape(BUCKET_LABEL[node["status"]])}" '
        f'aria-label="{html.escape(f"{node['id']}: {node['title']}")}">'
        f"<title>{name}</title>"
        f'<circle class="be-map-hit" cx="{node["x"]:.1f}" cy="{node["y"]:.1f}" r="18"/>'
        f'<circle class="be-map-dot" cx="{node["x"]:.1f}" cy="{node["y"]:.1f}" r="6" '
        f'style="fill:{BUCKET_COLOR[node["status"]]}"/>'
        f'<text class="be-map-label" x="{node["x"]:.1f}" y="{node["y"] + LABEL_DROP:.1f}" '
        f'text-anchor="middle">{html.escape(node["id"])}</text>'
        "</a>"
    )


def _map_view(items: list[Any]) -> str:
    """The map view: its caption, legend, the finished drawing, and the readout a pointer fills.

    The drawing ships as complete SVG rather than as data a script lays out, so the view needs no
    client-side layout at all; the script only shows, hides, and highlights what the build rendered.
    """
    layout = map_layout(items)
    drawn = len(layout["nodes"])
    caption = (
        f"{drawn} of {layout['total']} roadmap items take part in at least one Related, Origin, or "
        f"Superseded by relationship, and each is drawn here as a stop on its topic's line; the "
        f"remaining {layout['unlinked']} take part in none and are not drawn. Items run left to "
        "right in id order within a row, so a column means nothing across rows."
    )
    legend = "".join(
        f'<span class="be-legend-item">'
        f'<span class="be-legend-dot" style="background:{BUCKET_COLOR[name]}"></span>'
        f"{html.escape(BUCKET_LABEL[name])}</span>"
        for name, _key in bri.BUCKETS
    )
    legend += "".join(
        f'<span class="be-legend-item"><span class="be-legend-line be-legend-{kind}"></span>'
        f"{html.escape(label)}</span>"
        for kind, label in (
            ("related", "Related"),
            ("origin", "Origin →"),
            ("superseded", "Superseded by →"),
        )
    )
    rows = "".join(
        f'<g class="be-map-row">'
        f'<line class="be-map-rule" x1="{row["x1"]:.1f}" y1="{row["y"]:.1f}" '
        f'x2="{row["x2"]:.1f}" y2="{row["y"]:.1f}"/>'
        f'<text class="be-map-row-label" x="{LABEL_GUTTER:.1f}" y="{row["y"]:.1f}" '
        f'text-anchor="end" dominant-baseline="middle">{html.escape(row["topic"])}</text>'
        "</g>"
        for row in layout["rows"]
    )
    # Origin and Superseded by are directed, so they carry an arrowhead; Related is undirected and
    # stays a bare line. The marker is defined once and referenced by every directed connector.
    marker = (
        '<defs><marker id="be-map-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="6" '
        'markerHeight="6" orient="auto"><path class="be-map-arrow" d="M0,0 L8,4 L0,8 Z"/>'
        "</marker></defs>"
    )
    edges = "".join(
        f'<path class="be-map-edge be-kind-{edge["kind"]}" d="{edge["d"]}" '
        f'data-a="{html.escape(edge["source"])}" data-b="{html.escape(edge["target"])}"'
        f"{marker_end(edge['kind'])}/>"
        for edge in layout["connectors"]
    )
    nodes = "".join(_map_node(node) for node in layout["nodes"])
    readout = "Point at an item to read its title. Selecting one opens its record on GitHub."
    # Zoom scales the already-finished SVG by pixel size (no re-layout); Full size breaks the map out
    # of the page's prose-width column into the whole browser viewport. Both are pure view controls —
    # neither touches the deterministic, build-time layout above.
    toolbar = (
        '<div class="be-map-toolbar">'
        '<div class="be-map-zoom" role="group" aria-label="Zoom the map">'
        '<button type="button" class="be-map-zoom-btn" data-zoom="out" '
        'aria-label="Zoom out">−</button>'
        '<button type="button" class="be-map-zoom-btn" data-zoom="reset" '
        'aria-label="Reset zoom">Reset</button>'
        '<button type="button" class="be-map-zoom-btn" data-zoom="in" '
        'aria-label="Zoom in">+</button>'
        "</div>"
        '<button type="button" class="be-map-expand" aria-pressed="false" '
        'aria-label="View the map at full browser size">Full size</button>'
        "</div>"
    )
    # A hover/focus card, positioned next to whatever node the reader is pointing at (BE-0335): the
    # id, title, and Introduction excerpt every node's data-* attributes already carry. Decorative
    # (aria-hidden) — the live-region readout below stays the accessible source of the same fact.
    card = (
        '<div class="be-map-card" aria-hidden="true">'
        '<div class="be-map-card-top"><span class="be-map-card-id"></span>'
        '<span class="be-map-card-status"></span></div>'
        '<div class="be-map-card-title"></div>'
        '<div class="be-map-card-summary"></div>'
        "</div>"
    )
    return (
        '<div class="be-map-view is-hidden">'
        f'<p class="be-graph-caption">{html.escape(caption)}</p>'
        f'<div class="be-legend">{legend}</div>'
        f"{toolbar}"
        '<div class="be-map-scroll">'
        f'<svg class="be-map" viewBox="0 0 {layout["width"]:.0f} {layout["height"]:.0f}" '
        f'style="min-width:{layout["width"]:.0f}px" preserveAspectRatio="xMidYMid meet" '
        'role="group" aria-label="Map of roadmap items, grouped by topic and joined by relation">'
        f'{marker}<g class="be-map-rows">{rows}</g>'
        f'<g class="be-map-edges">{edges}</g><g class="be-map-nodes">{nodes}</g></svg>'
        "</div>"
        f"{card}"
        f'<p class="be-map-readout" role="status" data-default="{html.escape(readout)}">'
        f"{html.escape(readout)}</p>"
        "</div>"
    )


def render_html(items: list[Any]) -> str:
    """Render the dashboard body: filters, Cards/Table toggle, card sections, table, empty region.

    Both views render the same items (BE-0311): the card view is category-major (by Topic), each
    card carrying its own status (colour + badge) and each category a progress figure derived purely
    from the Status field — the share of its items Implemented — beside a stacked bar of the full
    composition; the table view lays every item out as one sortable row. The toggle shows one and
    hides the other; the search box and status filter narrow both alike.
    """
    by_bucket: dict[str, list[Any]] = {name: [] for name, _key in bri.BUCKETS}
    by_topic: dict[str, list[Any]] = {topic: [] for topic, _key, _origin in bri.TOPICS}
    for item in items:
        by_bucket[item.bucket].append(item)
        by_topic[item.topic].append(item)

    # Each status is an independent on/off checkbox (all checked = everything shown). The chip is a
    # label around a real <input type="checkbox">, so clicking toggles it natively; a small script
    # (below) reacts to the change. Without JavaScript the boxes stay checked and inert and every card
    # stays visible, so the page is still fully readable — progressive enhancement.
    chips = "".join(
        f'<label class="be-stat be-filter is-active" style="border-color:{BUCKET_COLOR[name]}">'
        f'<input type="checkbox" class="be-check" data-filter="{html.escape(name)}" '
        f'checked style="accent-color:{BUCKET_COLOR[name]}">'
        f'<b style="color:{BUCKET_COLOR[name]}">{len(by_bucket[name])}</b> {html.escape(name)}'
        "</label>"
        for name, _key in bri.BUCKETS
    )
    # A free-text search sits on its own row above the status chips. It matches an item's id, title,
    # topic, and status (all readable off each card) and composes with the chips (AND). Inert without
    # JavaScript, like the chips, so the no-JS page is unchanged (progressive enhancement).
    search = (
        '<input type="search" class="be-search" '
        'placeholder="Search id, title, topic, status…" aria-label="Search roadmap items">'
    )
    filters = (
        f'<div class="be-filters" role="group" aria-label="Filter roadmap items">'
        f'<div class="be-search-row">{search}</div>'
        f'<div class="be-chips">{chips}</div>'
        "</div>"
    )

    # Split categories into those with work left and those fully implemented; the 100% ones move to a
    # separate "Completed" group so the main view is the work still in flight.
    ongoing: list[str] = []
    completed: list[str] = []
    for topic, _key, _origin in bri.TOPICS:
        cat_items = by_topic[topic]
        if not cat_items:
            continue
        counts, total, pct = _topic_progress(cat_items)
        implemented = counts["Implemented"]
        cards = "".join(_card(it) for it in sorted(cat_items, key=lambda it: it.id))
        section = (
            '<section class="be-cat">'
            '<div class="be-cat-head" role="button" tabindex="0" aria-expanded="true">'
            '<div class="be-cat-title">'
            '<span class="be-chev" aria-hidden="true"></span>'
            f"<h3>{html.escape(topic)}</h3>"
            "</div>"
            '<div class="be-prog">'
            f'<span class="be-pct">{pct}%</span>'
            f'<span class="be-prog-detail">{implemented}/{total} implemented</span>'
            "</div>"
            f"{_progress_bar(counts, total)}"
            "</div>"
            f'<div class="be-cards">{cards}</div>'
            "</section>"
        )
        (completed if pct == 100 else ongoing).append(section)

    groups = ""
    if ongoing:
        groups += (
            '<div class="be-group" data-group="ongoing">'
            '<h2 class="be-group-head">In progress</h2>'
            f"{''.join(ongoing)}</div>"
        )
    if completed:
        groups += (
            '<div class="be-group" data-group="completed">'
            f'<h2 class="be-group-head">Completed <span class="be-count">{len(completed)}</span></h2>'
            f"{''.join(completed)}</div>"
        )

    # A live region the filter script fills when the current filters leave the grid empty. It stays in
    # the DOM at all times (empty = collapsed via `:empty`, so no layout cost and no-JS shows nothing);
    # the script only ever mutates its text, never its presence — the reliable pattern for an
    # `aria-live` status region to announce. The message text is set via textContent, never as markup.
    # A Cards/Table/Map toggle beside the filters (BE-0311, extended with the relationship map).
    # Every view reads the same rendered items — the toggle only shows one sibling container and
    # hides the rest; nothing is recomputed. Cards is the default and the only view without
    # JavaScript (the table and map containers ship hidden). The Map button keeps the stored
    # value `graph`, so a reader who chose this view before it was renamed still lands on it.
    views = (("cards", "Cards"), ("table", "Table"), ("graph", "Map"))
    buttons = "".join(
        f'<button type="button" class="be-view-btn{" is-active" if key == "cards" else ""}" '
        f'data-view="{key}" aria-pressed="{"true" if key == "cards" else "false"}">{label}</button>'
        for key, label in views
    )
    toggle = f'<div class="be-viewtoggle" role="group" aria-label="Choose layout">{buttons}</div>'
    cards_view = f'<div class="be-cards-view">{groups}</div>'
    table_view = (
        f'<div class="be-table-view is-hidden">{_progress_strip(by_topic)}{_table(items)}</div>'
    )
    empty = '<div class="be-empty" role="status"></div>'
    return (
        f'<div class="be-dash">{filters}{toggle}{cards_view}{table_view}'
        f"{_map_view(items)}{empty}</div>"
    )


_STYLE = """
<style>
.be-dash{font-size:14px}
.be-filters{margin:.5rem 0 1.5rem}
.be-search-row{margin-bottom:.6rem}
.be-chips{display:flex;flex-wrap:wrap;align-items:center;gap:.6rem}
.be-search{width:100%;box-sizing:border-box;max-width:420px;font:inherit;font-size:13px;
  padding:.3rem .6rem;
  border:1px solid rgba(128,128,128,.35);border-radius:8px;background:transparent;color:inherit}
.be-search:focus{border-color:currentColor}
.be-empty{color:#888;font-size:13px;margin:1rem 0}
.be-empty:empty{margin:0}
.be-stat{border:1px solid;border-radius:8px;padding:.25rem .7rem;font-size:13px}
.be-stat b{font-weight:600}
.be-filter{display:inline-flex;align-items:center;gap:.45rem;cursor:pointer;user-select:none;opacity:.5}
.be-filter.is-active{opacity:1;background:rgba(128,128,128,.1)}
.be-check{width:15px;height:15px;margin:0;cursor:pointer;flex:none}
.be-group.is-hidden,.be-cat.is-hidden,.be-card.is-hidden,.be-row.is-hidden,
  .be-cards-view.is-hidden,.be-table-view.is-hidden,.be-map-view.is-hidden{display:none}
.be-group-head{font-size:12px;font-weight:600;text-transform:uppercase;letter-spacing:.04em;
  color:#888;border-bottom:1px solid rgba(128,128,128,.2);padding-bottom:.3rem;margin:1.6rem 0 .6rem}
.be-cat{margin:1.6rem 0}
.be-cat-head{margin:0 0 .8rem;cursor:pointer}
.be-cat-title{display:flex;align-items:center;gap:.5rem}
.be-cat-title>h3{margin:.2rem 0;font-size:17px}
.be-chev{width:0;height:0;border-left:5px solid transparent;border-right:5px solid transparent;
  border-top:6px solid currentColor;opacity:.55;transition:transform .15s}
.be-cat.is-collapsed .be-chev{transform:rotate(-90deg)}
.be-cat.is-collapsed .be-cards{display:none}
.be-prog{display:flex;align-items:baseline;gap:.5rem;margin:.1rem 0 .4rem}
.be-pct{font-size:15px;font-weight:600}
.be-prog-detail{font-size:12px;color:#888}
.be-bar{display:flex;height:7px;border-radius:4px;overflow:hidden;background:rgba(128,128,128,.15);max-width:520px}
.be-bar>span{display:block;height:100%}
.be-cards{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:.55rem}
.be-card{display:flex;flex-direction:column;gap:.3rem;border:1px solid rgba(128,128,128,.25);
  border-left:3px solid;border-radius:8px;padding:.5rem .65rem}
.be-card:hover{background:rgba(128,128,128,.08)}
.be-card-main{display:flex;flex-direction:column;gap:.3rem;text-decoration:none;color:inherit}
.be-card-top{display:flex;align-items:center;justify-content:space-between;gap:.4rem}
.be-id{font-size:12px;font-weight:600}
.be-badge{font-size:10px;border:1px solid;border-radius:4px;padding:0 .35rem;white-space:nowrap}
.be-title{font-size:13px;line-height:1.35}
.be-origin{font-size:11px;color:#888}
.be-issue{align-self:flex-start;font-size:10px;font-weight:600;color:#666;text-decoration:none;
  border:1px solid rgba(128,128,128,.55);border-radius:4px;padding:0 .35rem;white-space:nowrap}
.be-issue:hover{color:inherit;border-color:currentColor}
.be-viewtoggle{display:inline-flex;margin:0 0 1.2rem;border:1px solid rgba(128,128,128,.35);
  border-radius:8px;overflow:hidden}
.be-view-btn{font:inherit;font-size:13px;padding:.3rem .9rem;border:0;background:transparent;
  color:inherit;cursor:pointer}
.be-view-btn+.be-view-btn{border-left:1px solid rgba(128,128,128,.35)}
.be-view-btn.is-active{background:rgba(128,128,128,.18);font-weight:600}
.be-strip{display:flex;flex-direction:column;gap:.35rem;margin:0 0 1.2rem}
.be-strip-row{display:grid;grid-template-columns:minmax(0,1fr) auto minmax(120px,180px);
  align-items:center;gap:.6rem;font-size:12px}
.be-strip-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.be-strip-pct{color:#888;font-variant-numeric:tabular-nums}
.be-table-view{overflow-x:auto}
.be-table{width:100%;border-collapse:collapse;font-size:13px}
.be-table th,.be-table td{text-align:left;padding:.4rem .6rem;
  border-bottom:1px solid rgba(128,128,128,.2);vertical-align:top}
.be-table th{user-select:none;white-space:nowrap;font-size:12px}
.be-table th[data-sort-key]{cursor:pointer}
.be-table th[data-sort-key]:hover{background:rgba(128,128,128,.1)}
.be-table th[aria-sort="ascending"]::after{content:" ▲";font-size:9px}
.be-table th[aria-sort="descending"]::after{content:" ▼";font-size:9px}
.be-table tbody tr:hover{background:rgba(128,128,128,.08)}
.be-row-title a{color:inherit;font-weight:600;text-decoration:underline;
  text-decoration-color:rgba(128,128,128,.5)}
.be-row-title a:hover{text-decoration-color:currentColor}
.be-date{white-space:nowrap;font-variant-numeric:tabular-nums;color:#888}
.be-graph-caption{font-size:13px;margin:0 0 .6rem;max-width:80ch}
.be-legend{display:flex;flex-wrap:wrap;gap:.7rem;font-size:12px;color:#888;margin-bottom:.7rem}
.be-legend-item{display:inline-flex;align-items:center;gap:.35rem}
.be-legend-dot{width:9px;height:9px;border-radius:50%}
.be-legend-line{width:18px;height:0;border-top:2px solid currentColor;opacity:.7}
.be-legend-origin{border-top-style:dashed}
.be-legend-superseded{border-top-style:dotted}
.be-map-toolbar{display:flex;align-items:center;justify-content:space-between;gap:.6rem;
  flex-wrap:wrap;margin:0 0 .5rem}
.be-map-zoom{display:inline-flex;border:1px solid rgba(128,128,128,.35);border-radius:8px;
  overflow:hidden}
.be-map-zoom-btn,.be-map-expand{font:inherit;font-size:12.5px;padding:.28rem .7rem;border:0;
  background:transparent;color:inherit;cursor:pointer}
.be-map-zoom-btn+.be-map-zoom-btn{border-left:1px solid rgba(128,128,128,.35)}
.be-map-zoom-btn:hover,.be-map-expand:hover{background:rgba(128,128,128,.15)}
.be-map-expand{border:1px solid rgba(128,128,128,.35);border-radius:8px}
.be-map-expand[aria-pressed="true"]{background:rgba(128,128,128,.18);font-weight:600}
/* The drawing is rendered at a fixed natural size, so the box scrolls rather than letting the SVG
   scale below the size its labels were drawn for; Zoom (above) and Full size (below) are the reader's
   way past that natural size when it reads too small. */
.be-map-scroll{position:relative;overflow:auto;border:1px solid rgba(128,128,128,.25);
  border-radius:8px;padding:.5rem;background:rgba(128,128,128,.04)}
.be-map{display:block;width:100%;height:auto}
/* Full size breaks the view out of the page's prose-width column to fill the browser viewport —
   still an ordinary in-page element (no Fullscreen API), so it needs no permission and Escape or the
   same button closes it. */
.be-map-view.is-expanded{position:fixed;inset:0;z-index:1000;display:flex;flex-direction:column;
  padding:1rem 1.2rem;overflow:auto;background:var(--md-default-bg-color,Canvas)}
.be-map-view.is-expanded .be-map-scroll{flex:1 1 auto;min-height:0}
html.be-map-lock-scroll,html.be-map-lock-scroll body{overflow:hidden}
.be-map-card{position:fixed;z-index:1100;display:none;max-width:280px;padding:.55rem .7rem;
  border:1px solid rgba(128,128,128,.35);border-radius:8px;
  background:var(--md-default-bg-color,Canvas);box-shadow:0 4px 18px rgba(0,0,0,.22);
  font-size:12.5px;line-height:1.4;pointer-events:none}
.be-map-card.is-visible{display:block}
.be-map-card-top{display:flex;align-items:center;gap:.4rem;margin-bottom:.15rem}
.be-map-card-id{font-weight:700;font-size:11px}
.be-map-card-status{font-size:10px;opacity:.7}
.be-map-card-title{font-weight:600;margin-bottom:.25rem}
.be-map-card-summary{opacity:.85}
.be-map-card-summary:empty{display:none}
.be-map-rule{stroke:currentColor;stroke-opacity:.18;stroke-width:1}
.be-map-row-label{fill:currentColor;opacity:.72;font-size:12px}
.be-map-edge{fill:none;stroke:currentColor;stroke-opacity:.16;stroke-width:1}
.be-map-edge.be-kind-origin{stroke-dasharray:5 3;stroke-opacity:.42}
.be-map-edge.be-kind-superseded{stroke-dasharray:1.5 3;stroke-opacity:.46}
.be-map-arrow{fill:currentColor;fill-opacity:.55}
.be-map-node{cursor:pointer}
.be-map-hit{fill:transparent}
.be-map-dot{stroke:rgba(128,128,128,.45);stroke-width:1}
.be-map-label{fill:currentColor;opacity:.62;font-size:9px;font-variant-numeric:tabular-nums;
  pointer-events:none}
.be-map-node:hover .be-map-dot,.be-map-node:focus-visible .be-map-dot{stroke:currentColor;
  stroke-width:2}
.be-map-node:hover .be-map-label,.be-map-node:focus-visible .be-map-label{opacity:1}
.be-map-node:focus{outline:none}
/* Pointing at an item dims the rest: the container carries the dimming, so one class toggle
   restyles every element and the item's own neighbourhood opts back out of it. */
.be-map.is-picking .be-map-node:not(.is-lit){opacity:.22}
.be-map.is-picking .be-map-edge:not(.is-lit){opacity:.06}
.be-map-edge.is-lit{stroke-opacity:.9;stroke-width:2}
.be-map-node.is-out,.be-map-edge.is-out{display:none}
.be-map-readout{font-size:12.5px;color:#888;margin:.7rem 0 0;min-height:1.5em}
</style>
"""

# Progressive enhancement. Two composing filters: each status is an independent on/off toggle (all on
# by default) and a free-text query over each card's id/title/topic/status. A card shows only while its
# status is on AND it matches the query (empty query matches everything); a category (or group) left
# with no visible card is hidden. With no query and every status on, categories collapse to a compact
# overview (just the heading and its progress bar); turning a status off, or typing a query, expands the
# categories that still have a match so results are visible without a click. Whenever the filters leave
# nothing visible, a live-region line explains why rather than leaving the grid silently blank — the
# query matched nothing, its matches are hidden by the status chips, or (no query) every chip is off or
# the on chips have no items. The collapsed state is applied by JS, never baked into the markup, so with scripting
# off every status is on, every category open, the empty-state region empty, and the page fully
# readable. Each heading also toggles its own category. Nothing fetches or computes; the filters only
# show and hide already-rendered markup. Two more affordances share this same script (BE-0311): the
# Cards/Table toggle shows one already-rendered view and hides the other, persisting the choice in
# localStorage; and clicking a table column header reorders the already-rendered <tbody> rows in
# place (ascending/descending on repeat clicks) without touching which rows the filters above show.
_SCRIPT = """
<script>
(function(){
  var search=document.querySelector('.be-search');
  var checks=document.querySelectorAll('.be-check');
  var cards=document.querySelectorAll('.be-card');
  var rows=document.querySelectorAll('.be-row');
  var cats=document.querySelectorAll('.be-cat');
  var groups=document.querySelectorAll('.be-group');
  var empty=document.querySelector('.be-empty');
  var on={};
  checks.forEach(function(c){ on[c.getAttribute('data-filter')]=c.checked; });
  // Each card and row carries its searchable text (id + title + topic + status, lower-cased) in
  // data-search, so the filter reads it ready-made instead of scraping markup. Cached once here.
  var cardHay=[]; cards.forEach(function(c){ cardHay.push(c.getAttribute('data-search')||''); });
  var rowHay=[]; rows.forEach(function(r){ rowHay.push(r.getAttribute('data-search')||''); });
  function terms(){
    return (search?search.value:'').toLowerCase().split(/\\s+/).filter(Boolean);
  }
  function setCollapsed(cat, collapsed){
    cat.classList.toggle('is-collapsed', collapsed);
    var head=cat.querySelector('.be-cat-head');
    if(head) head.setAttribute('aria-expanded', String(!collapsed));
  }
  // Cards and rows are the same items in two layouts, so one predicate drives both: a status chip
  // and the query. Counts and the empty-state message come from the cards (the canonical set), so a
  // row never double-counts; the rows just mirror each card's visibility.
  function apply(){
    var allOn=Object.keys(on).every(function(s){ return on[s]; });
    var q=terms(), hasQuery=q.length>0, matched=0, shown=0;
    function shows(hay, status){
      return on[status] && q.every(function(t){ return hay.indexOf(t)>=0; });
    }
    cards.forEach(function(c, i){
      var match=q.every(function(t){ return cardHay[i].indexOf(t)>=0; });
      if(match) matched++;
      var visible=on[c.getAttribute('data-status')] && match;
      if(visible) shown++;
      c.classList.toggle('is-hidden', !visible);
    });
    rows.forEach(function(r, i){
      r.classList.toggle('is-hidden', !shows(rowHay[i], r.getAttribute('data-status')));
    });
    cats.forEach(function(cat){
      var hasMatch=!!cat.querySelector('.be-card:not(.is-hidden)');
      cat.classList.toggle('is-hidden', !hasMatch);
      setCollapsed(cat, (allOn && !hasQuery) ? true : !hasMatch);
    });
    groups.forEach(function(g){
      g.classList.toggle('is-hidden', !g.querySelector('.be-cat:not(.is-hidden)'));
    });
    checks.forEach(function(c){
      c.closest('.be-filter').classList.toggle('is-active', c.checked);
    });
    applyGraphFilter();
    if(empty){
      // Whenever the current filters leave nothing visible, say why — so the grid is never silently
      // blank, whether search or the chips (or both) emptied it. The query cases are match-count
      // driven; the no-query case is chip-state driven (every chip off vs. the on chips just having no
      // items), so the wording can't contradict a chip the reader still sees checked. '' ⇒ collapses.
      var qText=search ? ('\\u201C'+search.value.trim()+'\\u201D') : '';
      var allOff=Object.keys(on).every(function(s){ return !on[s]; });
      var msg='';
      if(hasQuery && matched===0){ msg='No items match '+qText; }
      else if(hasQuery && shown===0){
        msg=matched+(matched===1?' item matches ':' items match ')+qText
          +', but the status filter above is hiding '+(matched===1?'it':'them');
      }
      else if(shown===0 && allOff){
        msg='Every status is turned off — switch a status filter above back on to see items';
      }
      else if(shown===0){
        msg='No items in the selected statuses — turn on another status filter above to see more';
      }
      empty.textContent=msg;
    }
  }
  checks.forEach(function(c){
    c.addEventListener('change', function(){ on[c.getAttribute('data-filter')]=c.checked; apply(); });
  });
  if(search) search.addEventListener('input', apply);
  cats.forEach(function(cat){
    var head=cat.querySelector('.be-cat-head');
    function toggle(){ setCollapsed(cat, !cat.classList.contains('is-collapsed')); }
    head.addEventListener('click', toggle);
    head.addEventListener('keydown', function(e){
      if(e.key==='Enter'||e.key===' '){ e.preventDefault(); toggle(); }
    });
  });

  // Table sort: clicking (or Enter/Space on) a header reorders the tbody rows by that column,
  // toggling ascending/descending on repeat clicks and marking the active column with aria-sort.
  // It only reorders already-rendered rows — hidden ones keep their is-hidden class — so it never
  // changes which rows the filter above is showing. Columns with a data-sort attribute compare on
  // it (id numeric via zero-padding, dates as UTC ISO); the rest compare on the cell's text.
  var table=document.querySelector('.be-table');
  var tbody=table?table.querySelector('tbody'):null;
  var ths=table?table.querySelectorAll('th[data-sort-key]'):[];
  var sortIdx=null, sortDir=1;
  function cellVal(row, idx){
    var td=row.children[idx];
    if(!td) return '';
    var s=td.getAttribute('data-sort');
    return (s!==null?s:(td.textContent||'')).trim().toLowerCase();
  }
  ths.forEach(function(th, idx){
    function sortBy(){
      sortDir=(sortIdx===idx)?-sortDir:1;
      sortIdx=idx;
      var arr=Array.prototype.slice.call(tbody.children);
      arr.sort(function(a, b){
        var va=cellVal(a, idx), vb=cellVal(b, idx);
        if(va<vb) return -sortDir;
        if(va>vb) return sortDir;
        return 0;
      });
      arr.forEach(function(r){ tbody.appendChild(r); });
      ths.forEach(function(h){ h.setAttribute('aria-sort', 'none'); });
      th.setAttribute('aria-sort', sortDir>0?'ascending':'descending');
    }
    th.addEventListener('click', sortBy);
    th.addEventListener('keydown', function(e){
      if(e.key==='Enter'||e.key===' '){ e.preventDefault(); sortBy(); }
    });
  });

  // Relationship map. The drawing arrives finished from the build — rows, connectors, and node
  // positions are all computed in Python — so nothing here lays anything out. The script only
  // narrows the map with the shared filters and highlights what a pointer rests on.
  var mapSvg=document.querySelector('.be-map');
  var mapNodes=Array.prototype.slice.call(document.querySelectorAll('.be-map-node'));
  var mapEdges=Array.prototype.slice.call(document.querySelectorAll('.be-map-edge'));
  var readout=document.querySelector('.be-map-readout');
  var readoutDefault=readout?(readout.getAttribute('data-default')||''):'';
  var mapCard=document.querySelector('.be-map-card');
  var mapCardId=mapCard?mapCard.querySelector('.be-map-card-id'):null;
  var mapCardStatus=mapCard?mapCard.querySelector('.be-map-card-status'):null;
  var mapCardTitle=mapCard?mapCard.querySelector('.be-map-card-title'):null;
  var mapCardSummary=mapCard?mapCard.querySelector('.be-map-card-summary'):null;

  function applyGraphFilter(){
    if(!mapSvg) return;
    var q=terms(), live={};
    mapNodes.forEach(function(node){
      var show=on[node.getAttribute('data-status')]&&q.every(function(t){
        return (node.getAttribute('data-search')||'').indexOf(t)>=0;
      });
      live[node.getAttribute('data-id')]=show;
      node.classList.toggle('is-out', !show);
    });
    mapEdges.forEach(function(edge){
      var both=live[edge.getAttribute('data-a')]&&live[edge.getAttribute('data-b')];
      edge.classList.toggle('is-out', !both);
    });
  }

  // Naming the item under the pointer is what makes a dot readable: the id label is too small to
  // carry a title, and a filtered-out connector must not light up something the reader cannot see.
  function pickNode(node){
    var id=node.getAttribute('data-id'), lit={};
    lit[id]=true;
    mapEdges.forEach(function(edge){
      var a=edge.getAttribute('data-a'), b=edge.getAttribute('data-b');
      var touches=(a===id||b===id)&&!edge.classList.contains('is-out');
      edge.classList.toggle('is-lit', touches);
      if(touches){ lit[a]=true; lit[b]=true; }
    });
    mapNodes.forEach(function(other){
      other.classList.toggle('is-lit', !!lit[other.getAttribute('data-id')]);
    });
    mapSvg.classList.add('is-picking');
    if(readout){
      readout.textContent=node.getAttribute('data-caption')+' \u00b7 '
        +node.getAttribute('data-status-label');
    }
    showCard(node);
  }

  function releaseNode(){
    if(!mapSvg) return;
    mapSvg.classList.remove('is-picking');
    mapEdges.forEach(function(edge){ edge.classList.remove('is-lit'); });
    mapNodes.forEach(function(node){ node.classList.remove('is-lit'); });
    if(readout) readout.textContent=readoutDefault;
    hideCard();
  }

  // The hover/focus card: id, title, and Introduction excerpt for whichever node the reader is
  // pointing at (BE-0335). Positioned from the node's own screen rect \u2014 via getBoundingClientRect,
  // so it works the same on mouse hover and keyboard focus \u2014 and clamped inside the viewport rather
  // than letting it run off the edge near a border node.
  function showCard(node){
    if(!mapCard) return;
    if(mapCardId) mapCardId.textContent=node.getAttribute('data-id')||'';
    if(mapCardStatus) mapCardStatus.textContent=node.getAttribute('data-status-label')||'';
    if(mapCardTitle) mapCardTitle.textContent=node.getAttribute('data-title')||'';
    if(mapCardSummary) mapCardSummary.textContent=node.getAttribute('data-summary')||'';
    mapCard.classList.add('is-visible');
    var rect=node.getBoundingClientRect();
    var cardRect=mapCard.getBoundingClientRect();
    var vw=document.documentElement.clientWidth, vh=document.documentElement.clientHeight;
    var left=rect.left+rect.width/2-cardRect.width/2;
    var top=rect.bottom+10;
    left=Math.max(8, Math.min(left, vw-cardRect.width-8));
    if(top+cardRect.height>vh-8) top=rect.top-cardRect.height-10;
    top=Math.max(8, top);
    mapCard.style.left=left+'px';
    mapCard.style.top=top+'px';
  }

  function hideCard(){
    if(mapCard) mapCard.classList.remove('is-visible');
  }

  // Zoom scales the finished SVG by explicit pixel size, relative to however it is rendered right
  // now (its default fill-the-container width, or a size an earlier click already set) \u2014 so each
  // click is a step from what the reader sees, not from an abstract baseline. Reset clears the
  // inline size and returns to the default responsive sizing.
  var ZOOM_FACTOR=1.25, ZOOM_MIN_PX=200, ZOOM_MAX_PX=8000;
  var zoomBtns=document.querySelectorAll('.be-map-zoom-btn');
  function mapWidthPx(){
    return mapSvg.style.width ? parseFloat(mapSvg.style.width)
      : mapSvg.getBoundingClientRect().width;
  }
  function setMapWidth(px){
    var vb=mapSvg.viewBox.baseVal;
    if(!vb || !vb.width) return;
    px=Math.max(ZOOM_MIN_PX, Math.min(ZOOM_MAX_PX, px));
    mapSvg.style.width=px+'px';
    mapSvg.style.height=(px*vb.height/vb.width)+'px';
  }
  zoomBtns.forEach(function(btn){
    btn.addEventListener('click', function(){
      var action=btn.getAttribute('data-zoom');
      if(!mapSvg) return;
      if(action==='in') setMapWidth(mapWidthPx()*ZOOM_FACTOR);
      else if(action==='out') setMapWidth(mapWidthPx()/ZOOM_FACTOR);
      else { mapSvg.style.width=''; mapSvg.style.height=''; }
    });
  });

  // Full size breaks the map out of the page's prose-width column to fill the browser viewport \u2014 an
  // in-page overlay (not the Fullscreen API), so no permission prompt and Escape or the same button
  // closes it just as reliably as it opened.
  var mapView=document.querySelector('.be-map-view');
  var expandBtn=document.querySelector('.be-map-expand');
  function setExpanded(on){
    if(!mapView) return;
    mapView.classList.toggle('is-expanded', on);
    document.documentElement.classList.toggle('be-map-lock-scroll', on);
    if(expandBtn){
      expandBtn.setAttribute('aria-pressed', String(on));
      expandBtn.textContent=on?'Exit full size':'Full size';
    }
  }
  if(expandBtn){
    expandBtn.addEventListener('click', function(){
      setExpanded(!mapView.classList.contains('is-expanded'));
    });
  }
  document.addEventListener('keydown', function(e){
    if(e.key==='Escape' && mapView && mapView.classList.contains('is-expanded')) setExpanded(false);
  });

  mapNodes.forEach(function(node){
    node.addEventListener('mouseenter', function(){ pickNode(node); });
    node.addEventListener('focus', function(){ pickNode(node); });
    node.addEventListener('mouseleave', releaseNode);
    node.addEventListener('blur', releaseNode);
  });

  // Cards/Table/Map toggle, persisted in localStorage so the choice survives visits. Defaults to
  // Cards (the no-JS view) when the key is absent, unknown, or storage is unavailable; the
  // try/catch keeps a locked-down browser from breaking the toggle. The map's stored value is
  // still `graph`, so a reader who chose it before the view was renamed still lands on it.
  var viewBtns=document.querySelectorAll('.be-view-btn');
  var VIEWS={cards:document.querySelector('.be-cards-view'),
             table:document.querySelector('.be-table-view'),
             graph:document.querySelector('.be-map-view')};
  var VIEW_KEY='bajutsu-roadmap-view';
  function setView(v){
    if(!VIEWS[v]) v='cards';
    // Switching away from Map must also leave full size: its overlay covers the whole viewport
    // (including this toggle), so nothing else would ever call setExpanded(false) to release the
    // scroll lock it set on <html> — a reader who reaches another view while still expanded (e.g.
    // by keyboard, since the hidden buttons stay focusable behind the overlay) would otherwise be
    // left unable to scroll the page at all.
    if(v!=='graph') setExpanded(false);
    Object.keys(VIEWS).forEach(function(name){
      if(VIEWS[name]) VIEWS[name].classList.toggle('is-hidden', name!==v);
    });
    viewBtns.forEach(function(b){
      var active=b.getAttribute('data-view')===v;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-pressed', String(active));
    });
    try{ localStorage.setItem(VIEW_KEY, v); }catch(e){}
  }
  viewBtns.forEach(function(b){
    b.addEventListener('click', function(){ setView(b.getAttribute('data-view')); });
  });
  var savedView='cards';
  try{ var stored=localStorage.getItem(VIEW_KEY); if(VIEWS[stored]) savedView=stored; }catch(e){}
  setView(savedView);

  apply();
})();
</script>
"""

_INTRO = (
    "# Roadmap status\n\n"
    '!!! warning "Ownership tracking lives in GitHub Issues, not on this page"\n'
    "    Every open item (status `Proposal` or `In progress`) has a matching GitHub issue, and "
    "that issue's **Assignees — not this dashboard or any file in the repo — are the single "
    "source of truth** for who, if anyone, is working on it. Browse issues labeled "
    "[`roadmap-tracking`]"
    "(https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+is%3Aopen+label%3Aroadmap-tracking): "
    "`no:assignee` for the unclaimed backlog, `assignee:<user>` for one person's plate. See "
    "[BE-0109](https://github.com/bajutsu-e2e/bajutsu/blob/main/roadmaps/"
    "BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) for how the sync works.\n\n"
    "Live view of every roadmap (BE) item, grouped by category — each category showing the share of "
    "its items already implemented, and each card its own status. Regenerated from item metadata on "
    "every docs build, so it always reflects the committed roadmap. Fully-implemented categories are "
    "grouped separately under Completed. Categories start collapsed to a progress overview — click a "
    "heading to expand it, toggle the status chips on and off, or type in the search box to narrow the "
    "cards by id, title, topic, or status. Switch between the card grid and a sortable table with the "
    "Cards / Table toggle — the table lists every item as a row with sortable Created and Updated "
    "columns, and the search and status filters narrow both views alike. A third view, Map, draws "
    "the relationships the items themselves record as a transit map: one line per topic, its items "
    "as stops along it in id order, and a curve joining every pair that stands in a Related, "
    "Origin, or Superseded by relation. Point at a stop to light up what it connects to and see a "
    "card with its title and a short excerpt from its Introduction; the Zoom and Full size controls "
    "above the map scale it up and expand it to fill the browser window. Each card links to its "
    "full proposal on GitHub. This dashboard is the only status view — for what a roadmap item is "
    "and how to add one, see [`roadmaps/README.md`]"
    "(https://github.com/bajutsu-e2e/bajutsu/blob/main/roadmaps/README.md) (both languages).\n\n"
)


def build_page(items: list[Any]) -> str:
    """The complete ``roadmap.md`` content: intro prose, the dashboard HTML, styles, and filter JS."""
    return f"{_INTRO}{render_html(items)}\n{_STYLE}{_SCRIPT}"


def filter_script() -> str:
    """The dashboard's client-side filter JS, without its ``<script>`` tags — for ``node --check``.

    The script lives inline in this module rather than under ``bajutsu/templates/`` where
    ``make lint-js``'s glob would catch it, so lint-js emits this (``--emit-script``) to a temp file
    and syntax-checks it there.
    """
    return _SCRIPT.replace("<script>", "").replace("</script>", "").strip() + "\n"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    # Two alternative modes: write the page (--out), or print just the filter JS (--emit-script).
    # A mutually exclusive group makes passing both fail loudly instead of silently ignoring --out.
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path for the page")
    mode.add_argument(
        "--emit-script",
        action="store_true",
        help="write only the embedded filter JS (no <script> tags) to stdout, for lint-js",
    )
    args = parser.parse_args(argv)
    if args.emit_script:
        sys.stdout.write(filter_script())
        return 0
    try:
        items = bri.load_items(bri.ROADMAP, with_dates=True)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_page(items), encoding="utf-8")
    print(f"wrote {args.out} ({len(items)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

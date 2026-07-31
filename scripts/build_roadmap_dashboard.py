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
import json
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
    """The relationship graph's payload: the items taking part in a relation, and the edges between.

    A node is drawn for every item on some edge, which is *participation* in a relation rather than
    *declaration* of one: ``Origin`` is one-directional, and ``Related``'s expected reciprocity is a
    convention rather than a checked rule, so an item named only by another still belongs in the
    picture. An item on no edge has nothing to draw and would only crowd it, so it is left out and
    counted instead: ``total`` and ``unlinked`` let the page state that omission outright rather than
    let the view imply a completeness it does not have (BE-0094's honesty rule). Each
    node carries the same lower-cased search string its card does, so the search box and status chips
    narrow the graph on exactly the tokens they narrow the other two views on.
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
        }
        for item in sorted(items, key=lambda it: it.id)
        if item.id in linked
    ]
    return {
        "nodes": nodes,
        "edges": edges,
        "colors": dict(BUCKET_COLOR),
        "total": len(items),
        "unlinked": len(items) - len(nodes),
    }


def _graph_view(items: list[Any]) -> str:
    """The graph view: its caption, legend, the empty canvas, and the data the script lays out.

    The nodes and edges ship as one JSON payload rather than as attributes spread over the existing
    card markup, because an edge belongs to a *pair* of items and has no natural home on either one's
    card. The canvas starts empty: the script fills it the first time a reader opens this view, so a
    reader who stays in Cards never pays for the layout.
    """
    data = graph_data(items)
    drawn = len(data["nodes"])
    caption = (
        f"{drawn} of {data['total']} roadmap items take part in at least one Related, Origin, or "
        f"Superseded by relationship, and each is drawn here as a node; the remaining "
        f"{data['unlinked']} take part in none and are not drawn."
    )
    hint = (
        "Drag a node to move it, drag the background to pan, scroll to zoom, and hover a node to "
        "isolate its neighbors. Every node links to its item on GitHub."
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
    # A script element holds raw text, so only a literal "</script" could close it early; escaping
    # every "<" as a JSON unicode escape rules that out and leaves the payload valid JSON.
    payload = json.dumps(data, separators=(",", ":"), ensure_ascii=False).replace("<", "\\u003c")
    return (
        '<div class="be-graph-view is-hidden">'
        f'<p class="be-graph-caption">{html.escape(caption)}</p>'
        f'<div class="be-legend">{legend}</div>'
        f'<p class="be-graph-hint">{html.escape(hint)}</p>'
        '<div class="be-graph-canvas" role="group" '
        'aria-label="Relationship graph of roadmap items">'
        '<svg class="be-graph"></svg></div>'
        f'<script type="application/json" class="be-graph-data">{payload}</script>'
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
    # A Cards/Table/Graph toggle beside the filters (BE-0311, extended with the relationship graph).
    # Every view reads the same rendered items — the toggle only shows one sibling container and
    # hides the rest; nothing is recomputed. Cards is the default and the only view without
    # JavaScript (the table and graph containers ship hidden).
    views = (("cards", "Cards"), ("table", "Table"), ("graph", "Graph"))
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
        f"{_graph_view(items)}{empty}</div>"
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
  .be-cards-view.is-hidden,.be-table-view.is-hidden,.be-graph-view.is-hidden{display:none}
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
.be-graph-caption{font-size:13px;margin:0 0 .6rem}
.be-graph-hint{font-size:12px;color:#888;margin:.5rem 0 .6rem}
.be-legend{display:flex;flex-wrap:wrap;gap:.7rem;font-size:12px;color:#888}
.be-legend-item{display:inline-flex;align-items:center;gap:.35rem}
.be-legend-dot{width:9px;height:9px;border-radius:50%}
.be-legend-line{width:18px;height:0;border-top:2px solid currentColor;opacity:.7}
.be-legend-origin{border-top-style:dashed}
.be-legend-superseded{border-top-style:dotted}
.be-graph-canvas{border:1px solid rgba(128,128,128,.25);border-radius:8px;overflow:hidden;
  background:rgba(128,128,128,.04);touch-action:none}
.be-graph{display:block;width:100%;height:70vh;min-height:420px;cursor:grab}
.be-graph.is-panning{cursor:grabbing}
/* The dim/lit pair is what makes a hover readable: dimming happens on the container, so one class
   toggle re-styles every edge and node at once, and the hovered node's own neighborhood opts back
   out of it. */
.be-graph-edge{stroke:currentColor;stroke-opacity:.28;stroke-width:1.1}
.be-graph-edge.be-kind-origin{stroke-dasharray:5 3;stroke-opacity:.5}
.be-graph-edge.be-kind-superseded{stroke-dasharray:1.5 3;stroke-opacity:.55}
.be-node{cursor:grab}
.be-node circle{stroke:rgba(128,128,128,.55);stroke-width:1;transition:r .1s}
/* Two hundred labels at once are unreadable, so only the hubs carry one at rest; every other node
   names itself on hover, on focus, and through its <title> tooltip. */
.be-node text{font-size:9px;fill:currentColor;opacity:0;pointer-events:none;text-anchor:middle}
.be-node.be-hub text{opacity:.8}
.be-node.is-lit text,.be-node:hover text,.be-node:focus text{opacity:1}
.be-node:focus{outline:none}
.be-node:focus circle,.be-node:hover circle{stroke:currentColor;stroke-width:2}
.be-graph.is-dimmed .be-graph-edge,.be-graph.is-dimmed .be-node{opacity:.12}
.be-graph.is-dimmed .be-graph-edge.is-lit,.be-graph.is-dimmed .be-node.is-lit{opacity:1}
.be-graph .be-graph-edge.is-lit{stroke-opacity:.85;stroke-width:1.7}
/* A marker paints with its own fill, not the referencing line's stroke, so match the edge opacity
   rather than let an arrowhead read darker than the line it ends. */
.be-graph-arrow{fill:currentColor;fill-opacity:.55}
.be-graph .be-node.is-hidden,.be-graph .be-graph-edge.is-hidden{display:none}
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

  // Relationship graph. The nodes and edges arrive as one JSON payload; everything below — the
  // layout, the SVG, and the pointer handling — is written for this page rather than taken from a
  // library, because the docs site ships no bundler and depends on no third-party script. Starting
  // positions come from each node's index along a golden-angle spiral rather than from a random
  // number generator, so the same roadmap always draws the same picture.
  var SVG_NS='http://www.w3.org/2000/svg';
  var graphSvg=document.querySelector('.be-graph');
  var graphRoot=null, graphBuilt=false;
  var graphNodes=[], graphEdges=[], graphIndex={}, graphNeighbors={}, litEls=[];
  var BOX_W=1200, BOX_H=820;
  var view={x:0, y:0, k:1};

  function graphPayload(){
    var el=document.querySelector('.be-graph-data');
    if(!el) return null;
    try{ return JSON.parse(el.textContent||''); }catch(e){ return null; }
  }

  // Fruchterman-Reingold: repulsion between every pair, attraction along every edge, a weak pull to
  // the center so a disconnected component cannot drift off the canvas, and a step limit that cools
  // to zero. A few hundred nodes settle in well under a second, so it runs to completion once and
  // then stops rather than animating.
  function layoutGraph(nodes, edges, w, h){
    var n=nodes.length;
    if(!n) return;
    var cx=w/2, cy=h/2, spread=Math.min(w,h)*0.46;
    var GOLDEN=2.399963229728653;
    var i, j;
    for(i=0;i<n;i++){
      var radius=Math.sqrt((i+0.5)/n)*spread, angle=i*GOLDEN;
      nodes[i].x=cx+radius*Math.cos(angle);
      nodes[i].y=cy+radius*Math.sin(angle);
    }
    var STEPS=400, k=Math.sqrt(w*h/n)*0.95, temp=Math.min(w,h)*0.1, cool=temp/(STEPS+1);
    // Repulsion is cut off beyond a few ideal edge lengths. The roadmap's relations form many small
    // components alongside one large one, and nothing but the centring pull holds two components
    // together — without a cutoff they push each other outward indefinitely, and fitting the result
    // into the box would then squash the large component into an unreadable blob.
    var cutoff2=(k*7)*(k*7);
    var dx=new Float64Array(n), dy=new Float64Array(n);
    for(var step=0; step<STEPS; step++){
      dx.fill(0); dy.fill(0);
      for(i=0;i<n;i++){
        for(j=i+1;j<n;j++){
          var ux=nodes[i].x-nodes[j].x, uy=nodes[i].y-nodes[j].y;
          var d2=ux*ux+uy*uy;
          if(d2>cutoff2) continue;
          if(d2<0.01) d2=0.01;
          var rep=k*k/d2;
          dx[i]+=ux*rep; dy[i]+=uy*rep; dx[j]-=ux*rep; dy[j]-=uy*rep;
        }
      }
      for(var e=0;e<edges.length;e++){
        var s=edges[e].s, t=edges[e].t;
        var ex=nodes[s].x-nodes[t].x, ey=nodes[s].y-nodes[t].y;
        var att=Math.sqrt(ex*ex+ey*ey)/k;
        dx[s]-=ex*att; dy[s]-=ey*att; dx[t]+=ex*att; dy[t]+=ey*att;
      }
      for(i=0;i<n;i++){
        dx[i]+=(cx-nodes[i].x)*0.05; dy[i]+=(cy-nodes[i].y)*0.05;
        var len=Math.sqrt(dx[i]*dx[i]+dy[i]*dy[i]);
        if(len<0.0001) continue;
        var lim=Math.min(len, temp)/len;
        nodes[i].x+=dx[i]*lim; nodes[i].y+=dy[i]*lim;
      }
      temp-=cool;
    }
    fitToBox(nodes, w, h);
  }

  // A sparse graph settles far wider than the box it started in — repulsion keeps pushing where too
  // few edges pull back — so the settled layout is scaled and centred into the viewBox as a last
  // step. Scaling uniformly after the fact keeps every distance proportional while guaranteeing the
  // whole picture is on screen, which no amount of force tuning can promise for an arbitrary graph.
  function fitToBox(nodes, w, h){
    var pad=42, minX=Infinity, minY=Infinity, maxX=-Infinity, maxY=-Infinity;
    nodes.forEach(function(nd){
      if(nd.x<minX) minX=nd.x;
      if(nd.x>maxX) maxX=nd.x;
      if(nd.y<minY) minY=nd.y;
      if(nd.y>maxY) maxY=nd.y;
    });
    var sx=(w-2*pad)/Math.max(1, maxX-minX), sy=(h-2*pad)/Math.max(1, maxY-minY);
    var s=Math.min(sx, sy);
    var ox=(w-(maxX-minX)*s)/2-minX*s, oy=(h-(maxY-minY)*s)/2-minY*s;
    nodes.forEach(function(nd){ nd.x=nd.x*s+ox; nd.y=nd.y*s+oy; });
  }

  // Client coordinates into the coordinate system of the given SVG element, so pointer handling
  // stays correct under the viewBox, the current zoom, and any page scaling.
  function toLocal(el, ev){
    var m=el.getScreenCTM();
    if(!m||!graphSvg.createSVGPoint) return null;
    var pt=graphSvg.createSVGPoint();
    pt.x=ev.clientX; pt.y=ev.clientY;
    var p=pt.matrixTransform(m.inverse());
    return {x:p.x, y:p.y};
  }

  function applyViewTransform(){
    graphRoot.setAttribute('transform',
      'translate('+view.x.toFixed(2)+','+view.y.toFixed(2)+') scale('+view.k.toFixed(4)+')');
  }

  function placeGraph(){
    graphEdges.forEach(function(ed){
      var a=graphNodes[ed.s], b=graphNodes[ed.t], x2=b.x, y2=b.y;
      // A directed edge stops short of its target: its arrowhead sits at the line's end, and a line
      // run to the node's centre would bury the head under the circle.
      if(ed.kind!=='related'){
        var vx=b.x-a.x, vy=b.y-a.y, len=Math.sqrt(vx*vx+vy*vy);
        if(len>0.001){
          var back=Math.min(len-0.5, b.r+7);
          x2=b.x-vx/len*back; y2=b.y-vy/len*back;
        }
      }
      ed.el.setAttribute('x1', a.x.toFixed(1)); ed.el.setAttribute('y1', a.y.toFixed(1));
      ed.el.setAttribute('x2', x2.toFixed(1)); ed.el.setAttribute('y2', y2.toFixed(1));
    });
    graphNodes.forEach(function(nd){
      nd.el.setAttribute('transform',
        'translate('+nd.x.toFixed(1)+','+nd.y.toFixed(1)+')');
    });
  }

  function clearLit(){
    litEls.forEach(function(el){ el.classList.remove('is-lit'); });
    litEls=[];
    graphSvg.classList.remove('is-dimmed');
  }

  // Hovering or focusing a node dims the rest of the picture: the container carries the dimming, so
  // one class toggle restyles every element, and the node's own neighborhood opts back out of it.
  function lightNode(nd){
    clearLit();
    graphSvg.classList.add('is-dimmed');
    function light(el){ el.classList.add('is-lit'); litEls.push(el); }
    light(nd.el);
    Object.keys(graphNeighbors[nd.id]||{}).forEach(function(id){
      var other=graphNodes[graphIndex[id]];
      if(other) light(other.el);
    });
    graphEdges.forEach(function(ed){
      if(graphNodes[ed.s]===nd||graphNodes[ed.t]===nd) light(ed.el);
    });
  }

  function applyGraphFilter(){
    if(!graphBuilt) return;
    var q=terms(), visible={};
    graphNodes.forEach(function(nd){
      var show=on[nd.status] && q.every(function(t){ return nd.search.indexOf(t)>=0; });
      visible[nd.id]=show;
      nd.el.classList.toggle('is-hidden', !show);
    });
    graphEdges.forEach(function(ed){
      var show=visible[graphNodes[ed.s].id]&&visible[graphNodes[ed.t].id];
      ed.el.classList.toggle('is-hidden', !show);
    });
  }

  var drag=null;

  function wireNode(nd){
    var el=nd.el;
    el.addEventListener('pointerenter', function(){ if(!drag) lightNode(nd); });
    el.addEventListener('pointerleave', function(){ if(!drag) clearLit(); });
    el.addEventListener('focus', function(){ lightNode(nd); });
    el.addEventListener('blur', function(){ clearLit(); });
    el.addEventListener('pointerdown', function(ev){
      if(ev.pointerType==='mouse'&&ev.button!==0) return;
      var p=toLocal(graphRoot, ev);
      if(!p) return;
      drag={node:nd, dx:nd.x-p.x, dy:nd.y-p.y, moved:false};
      // Keep the background's pan handler from also claiming this press.
      ev.stopPropagation();
      ev.preventDefault();
      el.setPointerCapture(ev.pointerId);
    });
    el.addEventListener('pointermove', function(ev){
      if(!drag||drag.node!==nd) return;
      var p=toLocal(graphRoot, ev);
      if(!p) return;
      nd.x=p.x+drag.dx; nd.y=p.y+drag.dy;
      drag.moved=true;
      placeGraph();
    });
    function endDrag(ev){
      if(!drag||drag.node!==nd) return;
      // A drag that moved the node must not also navigate, so the click that follows the release is
      // swallowed once. A press that never moved stays an ordinary link click.
      if(drag.moved) el.addEventListener('click', swallow, {once:true});
      drag=null;
      if(el.hasPointerCapture&&el.hasPointerCapture(ev.pointerId)) el.releasePointerCapture(ev.pointerId);
    }
    function swallow(ev){ ev.preventDefault(); }
    el.addEventListener('pointerup', endDrag);
    el.addEventListener('pointercancel', endDrag);
  }

  function wireCanvas(){
    var pan=null;
    graphSvg.addEventListener('pointerdown', function(ev){
      if(ev.pointerType==='mouse'&&ev.button!==0) return;
      var p=toLocal(graphSvg, ev);
      if(!p) return;
      pan={x:p.x, y:p.y};
      graphSvg.classList.add('is-panning');
      graphSvg.setPointerCapture(ev.pointerId);
    });
    graphSvg.addEventListener('pointermove', function(ev){
      if(!pan) return;
      var p=toLocal(graphSvg, ev);
      if(!p) return;
      view.x+=p.x-pan.x; view.y+=p.y-pan.y;
      pan={x:p.x, y:p.y};
      applyViewTransform();
    });
    function endPan(ev){
      if(!pan) return;
      pan=null;
      graphSvg.classList.remove('is-panning');
      if(graphSvg.hasPointerCapture&&graphSvg.hasPointerCapture(ev.pointerId)){
        graphSvg.releasePointerCapture(ev.pointerId);
      }
    }
    graphSvg.addEventListener('pointerup', endPan);
    graphSvg.addEventListener('pointercancel', endPan);
    graphSvg.addEventListener('wheel', function(ev){
      var p=toLocal(graphSvg, ev);
      if(!p) return;
      ev.preventDefault();
      var f=ev.deltaY<0?1.15:1/1.15, next=view.k*f;
      if(next<0.25||next>6) return;
      // Zoom about the pointer: the point under it keeps its place while everything scales around it.
      view.x=p.x-(p.x-view.x)*f; view.y=p.y-(p.y-view.y)*f; view.k=next;
      applyViewTransform();
    }, {passive:false});
  }

  function renderGraph(colors){
    graphSvg.setAttribute('viewBox', '0 0 '+BOX_W+' '+BOX_H);
    graphSvg.setAttribute('preserveAspectRatio', 'xMidYMid meet');
    graphRoot=document.createElementNS(SVG_NS, 'g');
    var edgeLayer=document.createElementNS(SVG_NS, 'g');
    var nodeLayer=document.createElementNS(SVG_NS, 'g');
    // Origin and Superseded by are directed relations, and the legend labels them with an arrow, so
    // the drawing has to carry one — a dash pattern alone says the two ends differ without saying
    // which is which.
    var defs=document.createElementNS(SVG_NS, 'defs');
    var marker=document.createElementNS(SVG_NS, 'marker');
    marker.setAttribute('id', 'be-graph-arrow');
    marker.setAttribute('viewBox', '0 0 8 8');
    marker.setAttribute('refX', '7'); marker.setAttribute('refY', '4');
    marker.setAttribute('markerWidth', '6'); marker.setAttribute('markerHeight', '6');
    marker.setAttribute('orient', 'auto');
    var head=document.createElementNS(SVG_NS, 'path');
    head.setAttribute('class', 'be-graph-arrow');
    head.setAttribute('d', 'M0,0 L8,4 L0,8 Z');
    marker.appendChild(head);
    defs.appendChild(marker);
    graphEdges.forEach(function(ed){
      var line=document.createElementNS(SVG_NS, 'line');
      line.setAttribute('class', 'be-graph-edge be-kind-'+ed.kind);
      if(ed.kind!=='related') line.setAttribute('marker-end', 'url(#be-graph-arrow)');
      ed.el=line;
      edgeLayer.appendChild(line);
    });
    graphNodes.forEach(function(nd){
      // A real <a> rather than a shape with a click handler, so a node is reachable by keyboard and
      // openable in a new tab like every other link on the page.
      var a=document.createElementNS(SVG_NS, 'a');
      a.setAttribute('class', nd.deg>=6?'be-node be-hub':'be-node');
      a.setAttribute('href', nd.href);
      a.setAttribute('aria-label', nd.id+': '+nd.title);
      var r=4+Math.min(6, Math.sqrt(nd.deg)*1.6);
      nd.r=r;  // placeGraph stops a directed edge this far short, so its arrowhead stays visible
      var circle=document.createElementNS(SVG_NS, 'circle');
      circle.setAttribute('r', r.toFixed(2));
      circle.setAttribute('fill', colors[nd.status]||'#888');
      var label=document.createElementNS(SVG_NS, 'text');
      label.setAttribute('dy', (-r-3).toFixed(1));
      label.textContent=nd.id;
      var tip=document.createElementNS(SVG_NS, 'title');
      tip.textContent=nd.id+' — '+nd.title+' ('+nd.status+')';
      a.appendChild(tip); a.appendChild(circle); a.appendChild(label);
      nd.el=a;
      nodeLayer.appendChild(a);
    });
    graphRoot.appendChild(defs);
    graphRoot.appendChild(edgeLayer);
    graphRoot.appendChild(nodeLayer);
    graphSvg.appendChild(graphRoot);
    applyViewTransform();
    placeGraph();
    graphNodes.forEach(wireNode);
    wireCanvas();
  }

  function buildGraph(){
    if(graphBuilt||!graphSvg) return;
    var data=graphPayload();
    if(!data||!data.nodes||!data.nodes.length) return;
    graphNodes=data.nodes.map(function(nd){
      return {id:nd.id, title:nd.title, status:nd.status, href:nd.href, search:nd.search,
              x:0, y:0, deg:0};
    });
    graphNodes.forEach(function(nd, i){ graphIndex[nd.id]=i; graphNeighbors[nd.id]={}; });
    graphEdges=[];
    (data.edges||[]).forEach(function(ed){
      var s=graphIndex[ed.source], t=graphIndex[ed.target];
      if(s===undefined||t===undefined) return;
      graphEdges.push({s:s, t:t, kind:ed.kind});
      graphNodes[s].deg++; graphNodes[t].deg++;
      graphNeighbors[ed.source][ed.target]=1;
      graphNeighbors[ed.target][ed.source]=1;
    });
    graphBuilt=true;
    layoutGraph(graphNodes, graphEdges, BOX_W, BOX_H);
    renderGraph(data.colors||{});
    applyGraphFilter();
  }

  // Cards/Table/Graph toggle, persisted in localStorage so the choice survives visits. Defaults to
  // Cards (the no-JS view) when the key is absent, unknown, or storage is unavailable; the
  // try/catch keeps a locked-down browser from breaking the toggle.
  var viewBtns=document.querySelectorAll('.be-view-btn');
  var VIEWS={cards:document.querySelector('.be-cards-view'),
             table:document.querySelector('.be-table-view'),
             graph:document.querySelector('.be-graph-view')};
  var VIEW_KEY='bajutsu-roadmap-view';
  function setView(v){
    if(!VIEWS[v]) v='cards';
    Object.keys(VIEWS).forEach(function(name){
      if(VIEWS[name]) VIEWS[name].classList.toggle('is-hidden', name!==v);
    });
    viewBtns.forEach(function(b){
      var active=b.getAttribute('data-view')===v;
      b.classList.toggle('is-active', active);
      b.setAttribute('aria-pressed', String(active));
    });
    // The graph is laid out the first time it is shown, never on page load: the work is wasted on a
    // reader who stays in Cards, and an SVG in a hidden container has no measurable size anyway.
    if(v==='graph') buildGraph();
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
    "columns, and the search and status filters narrow both views alike. A third view, Graph, draws "
    "the relationships the items themselves record: one node per item standing in a Related, "
    "Origin, or Superseded by relation to another, one edge per relationship, and a force-directed "
    "layout you can drag, pan, and zoom. Each card links to its "
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

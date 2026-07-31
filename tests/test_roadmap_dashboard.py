"""Tests for the roadmap dashboard generator (scripts/build_roadmap_dashboard.py).

The generator renders the live BE metadata as a self-contained HTML page the docs site publishes
(BE-XXXX). Unlike the index, the page is a build artifact (never committed), so there is no drift
check to pin; these tests pin the rendering instead — that every committed item is rendered, that
buckets and links are well formed, that titles are escaped, and that the BE-XXXX placeholder is
excluded just as the index excludes it.
"""

from __future__ import annotations

import html
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = _ROOT / "scripts" / "build_roadmap_dashboard.py"
_spec = importlib.util.spec_from_file_location("build_roadmap_dashboard", _MODULE_PATH)
assert _spec and _spec.loader
brd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = brd
_spec.loader.exec_module(brd)

_ITEMS = brd.bri.load_items(_ROOT / "roadmaps")
_PAGE = brd.build_page(_ITEMS)


def _sample_item(title: str = "T", **over: Any) -> Any:
    """A synthetic item for render tests — independent of Git history and the committed tree.

    Reuses the loaded model's real classes; ``over`` sets extra Item fields (e.g. ``created`` /
    ``updated``) so each test declares only what it exercises.
    """
    entry_cls = type(_ITEMS[0].by_lang["en"])
    item_cls = type(_ITEMS[0])
    return item_cls(
        id="BE-9999",
        slug="x",
        bucket="Proposals",
        topic=_ITEMS[0].topic,
        by_lang={
            "en": entry_cls(id="BE-9999", slug="x", title=title, status="Proposal", origin=None)
        },
        **over,
    )


def test_every_committed_item_is_rendered() -> None:
    """Each real item contributes exactly one status-tagged card linking to its file on GitHub."""
    assert _PAGE.count('class="be-card"') == len(_ITEMS)
    for item in _ITEMS:
        en = item.by_lang["en"]
        assert f">{en.id}</span>" in _PAGE, f"{en.id} missing from the dashboard"
        assert f"/roadmaps/{en.id}-{en.slug}/{en.id}-{en.slug}.md" in _PAGE


def test_each_card_links_to_its_tracking_issue_search() -> None:
    """Each card carries an additive "Issue" pill linking to its id's tracking-issue search (BE-0139).

    The pill is a second link beside the proposal one, built from the id alone (no network), so the
    per-item count matches the card count and each url is the one the id predicts. Table rows
    (BE-0311) carry the same pill, so this is scoped to the cards view to keep the count pinned to
    cards alone.
    """
    cards_view = _PAGE.split('class="be-table-view', 1)[0]
    assert cards_view.count('class="be-issue"') == len(_ITEMS)
    for item in _ITEMS:
        en = item.by_lang["en"]
        url = html.escape(brd.bri.tracking_issue_url(en.id))
        assert f'<a class="be-issue" href="{url}"' in cards_view, f"{en.id} issue link missing"


def test_every_nonempty_category_renders_a_section() -> None:
    """Every Topic that has items gets its own category section heading."""
    present = {item.topic for item in _ITEMS}
    assert _PAGE.count('class="be-cat"') == len(present)
    for topic in present:
        assert f"<h3>{html.escape(topic)}</h3>" in _PAGE


def test_per_category_progress_is_implemented_share() -> None:
    """Each category's percentage equals round(100 * implemented / total) over its own items."""
    by_topic: dict[str, list[object]] = {}
    for item in _ITEMS:
        by_topic.setdefault(item.topic, []).append(item)
    for topic, items in by_topic.items():
        implemented = sum(1 for it in items if it.bucket == "Implemented")  # type: ignore[attr-defined]
        pct = round(100 * implemented / len(items))
        assert f'<span class="be-pct">{pct}%</span>' in _PAGE, topic
        assert f">{implemented}/{len(items)} implemented<" in _PAGE


def test_status_filter_toggles_present() -> None:
    """Each bucket is an independent checkbox (checked by default); each card carries its status.

    There is no aggregate "all" control — every status is its own checkbox.
    """
    assert 'data-filter="all"' not in _PAGE
    assert _PAGE.count('type="checkbox"') == len(brd.bri.BUCKETS)
    for name, _key in brd.bri.BUCKETS:
        assert f'data-filter="{name}" checked' in _PAGE
    for item in _ITEMS:
        assert f'data-status="{item.bucket}"' in _PAGE


def test_search_box_is_rendered_and_wired() -> None:
    """A free-text search input sits in the filter row and the filter script listens to its input.

    This is BE-0219's machine-checkable outcome: the input exists (progressive enhancement, one per
    page), and the script wires the ``input`` event so typing narrows the cards.
    """
    assert _PAGE.count('type="search"') == 1
    assert 'class="be-search"' in _PAGE
    assert 'aria-label="Search roadmap items"' in _PAGE
    # Wired to the input event — matched loosely so a harmless reformat of the script (quote style,
    # spacing) doesn't break the test, only the actual wiring does.
    assert re.search(r"""search\.addEventListener\(\s*['"]input['"]\s*,\s*apply\s*\)""", _PAGE)
    # The always-present live region the script fills when the filters leave nothing visible.
    assert 'class="be-empty" role="status"' in _PAGE
    # Its empty-state reasons, so the grid never goes silently blank: the query matches nothing, its
    # matches are hidden by the status chips, or — with no query — every chip is off or the on chips
    # have no items. Pinned to the user-facing phrases (not the JS literal's quoting) a reader sees.
    assert "No items match " in _PAGE
    assert "but the status filter above is hiding " in _PAGE
    assert "item matches " in _PAGE and "items match " in _PAGE
    assert "Every status is turned off" in _PAGE
    assert "No items in the selected statuses" in _PAGE


def test_search_box_sits_in_its_own_row_above_the_chips() -> None:
    """The search input and the status chips live in separate rows, not one shared filter line.

    Guards the layout: the search box is wrapped in ``.be-search-row`` and the chips in
    ``.be-chips``, both inside ``.be-filters`` — so a later refactor can't silently re-merge them
    back onto a single row. Pinned structurally: the search row opens (and its input closes) before
    the chip container begins.
    """
    assert 'class="be-filters"' in _PAGE
    search_row = _PAGE.index('class="be-search-row"')
    chip_row = _PAGE.index('class="be-chips"')
    assert search_row < chip_row, "search row must render before the chip container"
    # The search input belongs to the search row, not the chip container.
    assert _PAGE.index('class="be-search"') < chip_row


def test_every_card_carries_its_topic() -> None:
    """Each card exposes its Topic as ``data-topic`` so search can match it without scraping markup."""
    for item in _ITEMS:
        assert f'data-topic="{html.escape(item.topic)}"' in _PAGE
    # Table rows (BE-0311) also carry data-topic, so count only within the cards view — the portion
    # before the table container — to keep this pinned to the cards.
    cards_view = _PAGE.split('class="be-table-view', 1)[0]
    assert cards_view.count("data-topic=") == len(_ITEMS)


def test_fully_implemented_categories_are_separated() -> None:
    """A category whose items are all Implemented lands in the Completed group, others in In progress."""
    by_topic: dict[str, list[object]] = {}
    for item in _ITEMS:
        by_topic.setdefault(item.topic, []).append(item)
    completed = {t for t, its in by_topic.items() if all(i.bucket == "Implemented" for i in its)}  # type: ignore[attr-defined]
    ongoing = set(by_topic) - completed
    # Both group headings appear only when their group has members.
    assert ('data-group="completed"' in _PAGE) == bool(completed)
    assert ('data-group="ongoing"' in _PAGE) == bool(ongoing)
    # The Completed group's section count matches the number of all-Implemented categories.
    completed_block = _PAGE.split('data-group="completed"', 1)[-1] if completed else ""
    assert completed_block.count('class="be-cat"') == len(completed)


def test_categories_are_collapsible() -> None:
    """Each category header is a keyboard-operable toggle, open by default."""
    present = {item.topic for item in _ITEMS}
    assert _PAGE.count('class="be-cat-head"') == len(present)
    assert _PAGE.count('aria-expanded="true"') == len(present)
    assert 'role="button"' in _PAGE


def test_placeholder_is_excluded() -> None:
    """A BE-XXXX placeholder item is not numbered yet, so it never appears on the dashboard."""
    assert "BE-XXXX" not in _PAGE


def test_emit_script_is_the_tagless_filter_js() -> None:
    """``filter_script`` / ``--emit-script`` yield the embedded JS with no ``<script>`` tags.

    ``make lint-js`` emits this and runs ``node --check`` on it, so the gate syntax-checks the
    dashboard's inline filter script (which lives in a Python string, outside lint-js's template
    glob). The test pins that the emitted text is the script body and carries no markup, so a stray
    tag can't slip into what ``node --check`` parses.
    """
    js = brd.filter_script()
    assert "<script>" not in js and "</script>" not in js
    # It is the real filter script, wrapped as an IIFE — pinned by its actual content (the wrapper
    # and a selector/API it must use to work), not by re-deriving filter_script's own transformation.
    assert js.lstrip().startswith("(function()")
    assert js.rstrip().endswith("})();")
    assert "addEventListener" in js
    assert ".be-check" in js and "querySelectorAll" in js


def test_table_view_renders_one_row_per_item() -> None:
    """The table view (BE-0311) is one ``<tr>`` per item under six sortable column headers."""
    assert _PAGE.count('class="be-row"') == len(_ITEMS)
    for key, _label in brd._TABLE_COLUMNS:
        assert f'data-sort-key="{key}"' in _PAGE
    # Exactly one header row: six columns, no more (a second table would double the count).
    assert _PAGE.count("data-sort-key=") == len(brd._TABLE_COLUMNS) == 6


def test_table_rows_mirror_card_status_and_topic() -> None:
    """Each row carries the same status/topic attributes as its card, so one filter drives both."""
    table_view = _PAGE.split('class="be-table-view', 1)[-1]
    for item in _ITEMS:
        row = (
            f'<tr class="be-row" data-status="{item.bucket}" data-topic="{html.escape(item.topic)}"'
        )
        assert row in table_view, f"{item.by_lang['en'].id} row missing or mis-tagged"
    assert table_view.count('class="be-row"') == len(_ITEMS)


def test_table_rows_link_to_their_tracking_issue_search() -> None:
    """Each row carries the same additive "Issue" pill the card does (BE-0139 parity, BE-0311).

    A trailing, unsortable column after the six sortable ones, so it doesn't shift their indices.
    """
    table_view = _PAGE.split('class="be-table-view', 1)[-1]
    assert table_view.count('class="be-issue"') == len(_ITEMS)
    for item in _ITEMS:
        en = item.by_lang["en"]
        url = html.escape(brd.bri.tracking_issue_url(en.id))
        assert f'<a class="be-issue" href="{url}"' in table_view, (
            f"{en.id} table issue link missing"
        )
    assert "<th>Issue</th>" in table_view


def test_view_toggle_and_both_containers_present() -> None:
    """A Cards/Table toggle sits beside the filters, with a container for each view (BE-0311)."""
    assert 'class="be-viewtoggle"' in _PAGE
    assert 'data-view="cards"' in _PAGE and 'data-view="table"' in _PAGE
    assert 'class="be-cards-view"' in _PAGE
    # The table view ships hidden so the no-JS page shows only Cards, exactly as it does today.
    assert 'class="be-table-view is-hidden"' in _PAGE


def test_table_headers_are_sortable_and_wired() -> None:
    """Every header is sortable (``aria-sort``) and the script wires a click handler over them.

    Machine-checkable outcome for the sort (BE-0311): each ``<th>`` carries ``aria-sort`` and the
    filter script selects ``th[data-sort-key]`` and listens for a click, so a header press reorders
    the rows. Matched loosely so a harmless reformat of the script doesn't break the test.
    """
    assert _PAGE.count('aria-sort="none"') >= len(brd._TABLE_COLUMNS)
    assert "th[data-sort-key]" in _PAGE
    assert re.search(r"""addEventListener\(\s*['"]click['"]\s*,\s*sortBy\s*\)""", _PAGE)


def test_date_columns_render_iso_dates() -> None:
    """The Created/Updated cells show the ``YYYY-MM-DD`` day and sort on the full UTC ISO stamp.

    Pinned with a synthetic item carrying known dates, so it holds regardless of the checkout's Git
    depth (a shallow ``make test`` clone can't derive real per-item dates); the real page's dates
    come from ``git log`` only in the full-history docs build.
    """
    sample = _sample_item(created="2026-01-02T03:04:05+00:00", updated="2026-07-08T09:10:11+00:00")
    out = brd.render_html([sample])
    assert 'data-sort="2026-01-02T03:04:05+00:00">2026-01-02<' in out
    assert 'data-sort="2026-07-08T09:10:11+00:00">2026-07-08<' in out


def test_missing_dates_render_a_placeholder() -> None:
    """An item with no derivable dates (shallow clone, uncommitted) renders a ``—`` empty cell."""
    out = brd.render_html([_sample_item()])
    assert '<td class="be-date" data-sort="">—</td>' in out


def test_html_is_escaped() -> None:
    """Titles flow through html.escape, so a stray angle bracket can't break the markup."""
    out = brd.render_html([_sample_item(title="a <script> & b")])
    assert "a &lt;script&gt; &amp; b" in out
    assert "<script>" not in out


def _card_for_origin(origin: str) -> str:
    """Render a single synthetic card carrying the given ``Origin`` field value."""
    entry_cls = type(_ITEMS[0].by_lang["en"])
    item_cls = type(_ITEMS[0])
    sample = item_cls(
        id="BE-9999",
        slug="x",
        bucket="Proposals",
        topic=_ITEMS[0].topic,
        by_lang={
            "en": entry_cls(id="BE-9999", slug="x", title="t", status="Proposal", origin=origin)
        },
    )
    return brd._card(sample)


def test_origin_item_link_resolves_to_an_absolute_github_url() -> None:
    """An ``Origin`` markdown link, written relative to *its own* item directory (e.g.
    ``[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md)``), must not survive
    verbatim into the generated page: that relative path only resolves from inside
    ``roadmaps/<that other item>/``, not from this page's own location. It must instead render as a
    real anchor pointing at the item's absolute GitHub URL — the same convention ``_item_href`` uses
    for the card's own link — so no stray ``roadmaps/**``-shaped relative path ever lands in the
    generated file for ``lint-roadmap`` to flag as broken.
    """
    card = _card_for_origin(
        "[BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md)"
    )
    expected_href = (
        "https://github.com/bajutsu-e2e/bajutsu/blob/main/"
        "roadmaps/BE-0014-record-demarcation/BE-0014-record-demarcation.md"
    )
    assert f'<span class="be-origin"><a href="{expected_href}">BE-0014</a></span>' in card
    assert "../BE-0014" not in card


def test_origin_prose_around_a_link_is_preserved_and_escaped() -> None:
    """Prose surrounding an ``Origin`` link (e.g. "Review of ...") survives, html-escaped."""
    card = _card_for_origin(
        "Review of [BE-0180](../BE-0180-crawl-history-viewer/BE-0180-crawl-history-viewer.md) <x>"
    )
    assert "Review of <a href=" in card
    assert ">BE-0180</a> &lt;x&gt;</span>" in card


def test_origin_plain_text_is_escaped_with_no_markup() -> None:
    """An ``Origin`` with no markdown link (most items) renders as plain escaped text."""
    card = _card_for_origin("MagicPod & <competitors>")
    assert '<span class="be-origin">MagicPod &amp; &lt;competitors&gt;</span>' in card


def test_origin_absolute_link_is_left_verbatim() -> None:
    """An ``Origin`` link to an absolute URL (e.g. an issue) must not be treated as item-relative.

    Running it through the same ``posixpath.normpath(f"roadmaps/{item_dir}/...")`` resolution as an
    item-relative target would mangle ``https://`` into ``https:/`` and prefix it with
    ``roadmaps/<item>/``, producing a broken href.
    """
    card = _card_for_origin("[#123](https://github.com/bajutsu-e2e/bajutsu/issues/123)")
    assert (
        '<span class="be-origin"><a href="https://github.com/bajutsu-e2e/bajutsu/issues/123">'
        "#123</a></span>" in card
    )


def test_graph_payload_holds_only_items_taking_part_in_a_relation() -> None:
    """The graph draws the linked items and counts the rest, so the view claims no completeness.

    The relationship graph's node set is exactly the items that appear on some edge — participation,
    not declaration, since an item named only by another still belongs in the picture; every other
    item is reported through ``total`` / ``unlinked`` instead of drawn as an isolated dot.
    """
    data = brd.graph_data(_ITEMS)
    linked = {end for edge in data["edges"] for end in (edge["source"], edge["target"])}
    assert {node["id"] for node in data["nodes"]} == linked
    assert data["total"] == len(_ITEMS)
    assert data["unlinked"] == len(_ITEMS) - len(data["nodes"])
    assert [node["id"] for node in data["nodes"]] == sorted(node["id"] for node in data["nodes"])


def test_graph_edges_are_well_formed() -> None:
    """Every edge names two rendered nodes, one known kind, and no pair appears twice."""
    data = brd.graph_data(_ITEMS)
    ids = {node["id"] for node in data["nodes"]}
    seen: set[tuple[str, str]] = set()
    for edge in data["edges"]:
        assert edge["source"] in ids and edge["target"] in ids
        assert edge["source"] != edge["target"]
        assert edge["kind"] in brd._EDGE_PRECEDENCE
        pair = tuple(sorted((edge["source"], edge["target"])))
        assert pair not in seen, f"{pair} drawn twice"
        seen.add(pair)


def _linked_item(be_id: str, **relations: tuple[str, ...]) -> Any:
    """A synthetic item with a chosen id and relations — for the edge rules, free of the real tree."""
    entry_cls = type(_ITEMS[0].by_lang["en"])
    item_cls = type(_ITEMS[0])
    return item_cls(
        id=be_id,
        slug="x",
        bucket="Proposals",
        topic=_ITEMS[0].topic,
        by_lang={"en": entry_cls(id=be_id, slug="x", title="T", status="Proposal", origin=None)},
        **relations,
    )


def test_a_mutual_related_pair_becomes_one_undirected_edge() -> None:
    """``Related`` is declared by both items, so its two directions collapse into a single edge."""
    edges = brd._edges(
        [
            _linked_item("BE-9999", related=("BE-9998",)),
            _linked_item("BE-9998", related=("BE-9999",)),
        ]
    )
    assert edges == [{"source": "BE-9998", "target": "BE-9999", "kind": "related"}]


def test_a_directed_relation_outranks_related_between_the_same_pair() -> None:
    """Two items that are both Related and Origin get the edge that says more, drawn once."""
    edges = brd._edges([_linked_item("BE-9999", related=("BE-9998",), origin_refs=("BE-9998",))])
    assert edges == [{"source": "BE-9998", "target": "BE-9999", "kind": "origin"}]


def test_graph_payload_is_valid_json_and_cannot_close_its_script_element() -> None:
    """The payload parses as JSON and carries no raw ``<``, which could end the script early."""
    payload = _PAGE.split('class="be-graph-data">', 1)[1].split("</script>", 1)[0]
    assert "<" not in payload
    data = json.loads(payload)
    assert len(data["nodes"]) == len(brd.graph_data(_ITEMS)["nodes"])
    assert set(data["colors"]) == set(brd.BUCKET_COLOR)


def test_graph_nodes_carry_the_same_search_string_their_cards_do() -> None:
    """One filter predicate serves all three views, so a node matches exactly when its card does."""
    by_id = {item.by_lang["en"].id: item for item in _ITEMS}
    for node in brd.graph_data(_ITEMS)["nodes"]:
        item = by_id[node["id"]]
        assert node["search"] == brd._search_terms(item)
        assert node["status"] == item.bucket
        assert node["href"] == brd._item_href(item)


def test_view_toggle_offers_cards_table_and_graph() -> None:
    """Three views share one toggle; Cards is the pressed default, so no-JS readers keep it."""
    for key, label in (("cards", "Cards"), ("table", "Table"), ("graph", "Graph")):
        assert f'data-view="{key}"' in _PAGE, key
        assert f">{label}</button>" in _PAGE
    assert 'data-view="cards" aria-pressed="true"' in _PAGE
    assert 'data-view="graph" aria-pressed="false"' in _PAGE
    # Every view but Cards ships hidden, so scripting off leaves the page exactly as it was.
    assert 'class="be-graph-view is-hidden"' in _PAGE
    assert 'class="be-table-view is-hidden"' in _PAGE


def test_graph_view_is_wired_and_states_what_it_leaves_out() -> None:
    """The canvas, its caption, and the script's entry points are all present on the page."""
    data = brd.graph_data(_ITEMS)
    assert f"{len(data['nodes'])} of {data['total']} roadmap items" in _PAGE
    assert f"the remaining {data['unlinked']} take part in none and are not drawn" in _PAGE
    assert '<svg class="be-graph">' in _PAGE
    # Laid out on first sight rather than on page load, and narrowed by the shared filters.
    assert re.search(r"if\(v==='graph'\)\s*buildGraph\(\);", _PAGE)
    assert "applyGraphFilter();" in _PAGE
    # The layout is reproducible: no random seeding anywhere in the script.
    assert "Math.random" not in _PAGE


def test_an_item_named_only_by_another_is_drawn_while_declaring_nothing() -> None:
    """Being drawn means taking part in a relation, which is wider than declaring one.

    ``Origin`` is one-directional and ``Related``'s reciprocity is a convention rather than a checked
    rule, so an item named only by another is drawn without declaring anything itself. Pinned on a
    synthetic pair, since only the code guarantees this — whether the live roadmap happens to contain
    such an item today is an accident of its content.
    """
    derived = _linked_item("BE-9999", origin_refs=("BE-9998",))
    named = _linked_item("BE-9998")
    assert not (named.related or named.origin_refs or named.superseded_by)
    drawn = {node["id"] for node in brd.graph_data([derived, named])["nodes"]}
    assert drawn == {"BE-9998", "BE-9999"}


def test_the_caption_claims_participation_rather_than_declaration() -> None:
    """The caption must not tell a reader the drawn items declared anything (BE-0094's honesty rule).

    Every declarer is drawn, so the caption's claim is checked as the non-strict containment the code
    guarantees; the wording assertions are what keep it from drifting back to "declare".
    """
    declaring = {
        item.by_lang["en"].id
        for item in _ITEMS
        if item.related or item.origin_refs or item.superseded_by
    }
    drawn = {node["id"] for node in brd.graph_data(_ITEMS)["nodes"]}
    assert declaring <= drawn
    assert "declare at least one" not in _PAGE
    assert "take part in at least one Related, Origin, or Superseded by relationship" in _PAGE


def test_directed_edges_are_drawn_with_an_arrowhead() -> None:
    """The legend labels Origin and Superseded by with an arrow, so the drawing must render one.

    A dash pattern alone says the two ends of an edge differ without saying which is which, and the
    direction ``_edges`` stores in ``source`` / ``target`` would be invisible.
    """
    assert "Origin \u2192" in _PAGE and "Superseded by \u2192" in _PAGE
    assert "'be-graph-arrow'" in _PAGE
    # Only the directed kinds carry the marker; Related is undirected and must stay a bare line.
    assert re.search(
        r"if\(ed\.kind!=='related'\)\s*line\.setAttribute\('marker-end', 'url\(#be-graph-arrow\)'\);",
        _PAGE,
    )

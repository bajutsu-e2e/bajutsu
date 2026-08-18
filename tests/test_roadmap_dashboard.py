"""Tests for the roadmap dashboard generator (scripts/build_roadmap_dashboard.py).

The generator renders the live BE metadata as a self-contained HTML page the docs site publishes
(BE-XXXX). Unlike the index, the page is a build artifact (never committed), so there is no drift
check to pin; these tests pin the rendering instead — that every committed item is rendered, that
buckets and links are well formed, that titles are escaped, and that the BE-XXXX placeholder is
excluded just as the index excludes it.
"""

from __future__ import annotations

import dataclasses
import html
import importlib.util
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


def _bucketed(bucket: str) -> Any:
    """A synthetic item in one bucket — the lone field the progress derivation reads."""
    return dataclasses.replace(_sample_item(), bucket=bucket)


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
    """Each category's percentage is its implemented share of the items it still owes.

    Rejected items are outside that denominator (BE-0366) — they are never coming back, so they are
    not outstanding work.
    """
    by_topic: dict[str, list[object]] = {}
    for item in _ITEMS:
        by_topic.setdefault(item.topic, []).append(item)
    for topic, items in by_topic.items():
        implemented = sum(1 for it in items if it.bucket == "Implemented")  # type: ignore[attr-defined]
        outstanding = sum(1 for it in items if it.bucket != "Rejected")  # type: ignore[attr-defined]
        pct = round(100 * implemented / outstanding) if outstanding else 100
        assert f'<span class="be-pct">{pct}%</span>' in _PAGE, topic
        assert f">{implemented}/{outstanding} implemented<" in _PAGE


def test_rejected_items_leave_the_progress_denominator() -> None:
    """One Rejected item alongside one Implemented reads 100%, not 50% (BE-0366).

    A Deferred item in the same position still counts, since a parked item remains a live question
    the topic has yet to answer.
    """
    implemented = _bucketed("Implemented")
    rejected = _bucketed("Rejected")
    deferred = _bucketed("Deferred")

    counts, total, pct = brd._topic_progress([implemented, rejected])
    assert (counts["Rejected"], total, pct) == (1, 1, 100)
    # The bar shares that denominator, so it draws no Rejected segment either — otherwise its
    # segments would sum past 100%. The segment's title attribute carries the bucket name.
    assert "Rejected" not in brd._progress_bar(counts, total)

    counts, total, pct = brd._topic_progress([implemented, deferred])
    assert (counts["Deferred"], total, pct) == (1, 2, 50)


def test_an_all_rejected_topic_reads_complete_rather_than_dividing_by_zero() -> None:
    """A topic with nothing outstanding has no share to compute, so it reads 100% (BE-0366)."""
    counts, total, pct = brd._topic_progress([_bucketed("Rejected")])
    assert (counts["Rejected"], total, pct) == (1, 0, 100)
    # The bar draws no segment either, rather than dividing its widths by an empty denominator.
    assert brd._progress_bar(counts, total) == '<div class="be-bar"></div>'


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
    """A category with no outstanding work lands in the Completed group, others in In progress.

    "No outstanding work" means every item is Implemented or Rejected: a Rejected item is outside
    the progress denominator (BE-0366), so it cannot hold a category back at 99%.
    """
    by_topic: dict[str, list[object]] = {}
    for item in _ITEMS:
        by_topic.setdefault(item.topic, []).append(item)
    completed = {
        t
        for t, its in by_topic.items()
        if all(i.bucket in ("Implemented", "Rejected") for i in its)  # type: ignore[attr-defined]
    }
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


def test_graph_nodes_carry_the_same_search_string_their_cards_do() -> None:
    """One filter predicate serves all three views, so a node matches exactly when its card does."""
    by_id = {item.by_lang["en"].id: item for item in _ITEMS}
    for node in brd.graph_data(_ITEMS)["nodes"]:
        item = by_id[node["id"]]
        assert node["search"] == brd._search_terms(item)
        assert node["status"] == item.bucket
        assert node["href"] == brd._item_href(item)


def test_graph_nodes_carry_the_items_summary() -> None:
    """Each node's ``summary`` is the item's own (BE-0335's hover card reads it verbatim)."""
    by_id = {item.by_lang["en"].id: item for item in _ITEMS}
    for node in brd.graph_data(_ITEMS)["nodes"]:
        assert node["summary"] == by_id[node["id"]].summary


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


def test_every_drawn_item_is_placed_exactly_once_on_its_topic_row() -> None:
    """The layout is a total, injective placement: one position per drawn item, on its topic's row."""
    layout = brd.map_layout(_ITEMS)
    rows = {row["topic"]: row["y"] for row in layout["rows"]}
    drawn = {node["id"] for node in brd.graph_data(_ITEMS)["nodes"]}
    placed = [node["id"] for node in layout["nodes"]]
    assert sorted(placed) == sorted(drawn)
    assert len(placed) == len(set(placed))
    for node in layout["nodes"]:
        assert node["y"] == rows[node["topic"]], node["id"]


def test_each_row_runs_left_to_right_in_id_order() -> None:
    """Position is predictable because it is derived: within a row, x ascends with the identifier."""
    layout = brd.map_layout(_ITEMS)
    by_row: dict[float, list[Any]] = {}
    for node in layout["nodes"]:
        by_row.setdefault(node["y"], []).append(node)
    for nodes in by_row.values():
        ordered = sorted(nodes, key=lambda n: n["x"])
        assert [n["id"] for n in ordered] == sorted(n["id"] for n in nodes)


def test_rows_are_emitted_only_for_topics_that_have_a_drawn_item() -> None:
    """A topic nothing is drawn for gets no row, so the map carries no empty lines."""
    layout = brd.map_layout(_ITEMS)
    drawn_topics = {node["topic"] for node in layout["nodes"]}
    assert [row["topic"] for row in layout["rows"]] == [
        topic for topic, _key, _origin in brd.bri.TOPICS if topic in drawn_topics
    ]


def test_the_drawing_fits_the_reported_size() -> None:
    """Width and height bound every row and node, so the viewBox never clips the map."""
    layout = brd.map_layout(_ITEMS)
    for row in layout["rows"]:
        assert 0 < row["x1"] < row["x2"] <= layout["width"]
        assert 0 < row["y"] < layout["height"]
    for node in layout["nodes"]:
        assert 0 < node["x"] < layout["width"]
        assert 0 < node["y"] < layout["height"]


def test_the_layout_is_a_pure_function_of_the_metadata() -> None:
    """The same items always yield the same coordinates — what no force simulation could promise."""
    assert brd.map_layout(_ITEMS) == brd.map_layout(list(reversed(_ITEMS)))


def test_a_connector_follows_the_rows_rather_than_cutting_across_them() -> None:
    """Same-row relations arc above the row; cross-row ones leave and arrive horizontally."""
    layout = brd.map_layout(_ITEMS)
    at = {node["id"]: node for node in layout["nodes"]}
    same = next(c for c in layout["connectors"] if at[c["source"]]["y"] == at[c["target"]]["y"])
    across = next(c for c in layout["connectors"] if at[c["source"]]["y"] != at[c["target"]]["y"])
    assert same["d"].count("Q") == 1, "a same-row connector is a single arc"
    assert across["d"].count("C") == 1, "a cross-row connector is a single curve"
    # The cross-row curve's first control point shares its start's y, so it leaves horizontally.
    control = across["d"].split("C")[1].split()[0]
    assert control.split(",")[1] == f"{at[across['source']]['y']:.1f}"


def test_every_connector_ends_on_two_drawn_items() -> None:
    """A path with an endpoint nothing is drawn for would run off into blank space."""
    layout = brd.map_layout(_ITEMS)
    drawn = {node["id"] for node in layout["nodes"]}
    for connector in layout["connectors"]:
        assert connector["source"] in drawn and connector["target"] in drawn


def test_view_toggle_offers_cards_table_and_map() -> None:
    """Three views share one toggle; Cards is the pressed default, so no-JS readers keep it.

    The map's stored value stays ``graph`` even though the button now reads Map, so a reader who
    chose the view before it was renamed still lands on it.
    """
    for key, label in (("cards", "Cards"), ("table", "Table"), ("graph", "Map")):
        assert f'data-view="{key}"' in _PAGE, key
        assert f">{label}</button>" in _PAGE
    assert 'data-view="cards" aria-pressed="true"' in _PAGE
    assert 'class="be-map-view is-hidden"' in _PAGE
    assert 'class="be-table-view is-hidden"' in _PAGE


def test_the_map_ships_finished_rather_than_laid_out_in_the_browser() -> None:
    """Every row, connector, and node is rendered by the build; the script lays out nothing."""
    layout = brd.map_layout(_ITEMS)
    assert _PAGE.count('class="be-map-row"') == len(layout["rows"])
    assert _PAGE.count('class="be-map-edge ') == len(layout["connectors"])
    assert _PAGE.count('class="be-map-node"') == len(layout["nodes"])
    assert f'viewBox="0 0 {layout["width"]:.0f} {layout["height"]:.0f}"' in _PAGE
    # No simulation, and no randomness that would make the drawing differ between builds.
    assert "Math.random" not in _PAGE
    assert "Float64Array" not in _PAGE


def test_directed_connectors_are_drawn_with_an_arrowhead() -> None:
    """The legend labels Origin and Superseded by with an arrow, so the drawing must render one."""
    layout = brd.map_layout(_ITEMS)
    directed = sum(1 for c in layout["connectors"] if c["kind"] != "related")
    assert directed, "expected the roadmap to declare a directed relation"
    assert _PAGE.count('marker-end="url(#be-map-arrow)"') == directed
    assert 'id="be-map-arrow"' in _PAGE
    assert brd.marker_end("related") == ""
    assert brd.marker_end("origin") == ' marker-end="url(#be-map-arrow)"'


def test_pointing_at_an_item_names_it_in_a_live_region() -> None:
    """A dot is too small to carry a title, so the readout names whatever the pointer rests on."""
    assert 'class="be-map-readout" role="status"' in _PAGE
    assert "Point at an item to read its title" in _PAGE
    for node in brd.map_layout(_ITEMS)["nodes"][:5]:
        assert html.escape(f"{node['id']} — {node['title']}") in _PAGE


def test_the_live_region_readout_carries_the_summary_too() -> None:
    """The visual hover card is decorative (aria-hidden); the readout is its accessible fallback, so
    it must speak the same Introduction excerpt the card shows, not just the id/title/status.
    """
    script = brd.filter_script()
    pick_node = script[script.index("function pickNode") : script.index("function releaseNode")]
    assert "data-summary" in pick_node
    assert "readout.textContent" in pick_node and "summary" in pick_node


def test_map_node_href_is_escaped_like_its_other_attributes() -> None:
    """A node's ``href`` is escaped consistently with its other ``data-*`` attributes.

    Today's ``_item_href()`` output only ever holds URL-safe characters, but the map node was the
    one attribute on the element left unescaped — inconsistent with the rest, and one slug away from
    a malformed attribute if that ever changes.
    """
    for node in brd.map_layout(_ITEMS)["nodes"][:5]:
        assert f'href="{html.escape(node["href"])}"' in _PAGE


def test_each_node_carries_its_title_and_summary_for_the_hover_card() -> None:
    """The hover/focus card (BE-0335) reads a node's title and Introduction excerpt off its own <a>."""
    for node in brd.map_layout(_ITEMS)["nodes"][:5]:
        assert f'data-title="{html.escape(node["title"])}"' in _PAGE
        assert f'data-summary="{html.escape(node["summary"])}"' in _PAGE


def test_the_map_offers_zoom_and_full_size_controls() -> None:
    """A reader can enlarge the map beyond its natural size, in place or across the whole browser."""
    assert 'class="be-map-toolbar"' in _PAGE
    for action in ("out", "reset", "in"):
        assert f'data-zoom="{action}"' in _PAGE
    assert 'class="be-map-expand"' in _PAGE
    assert 'aria-pressed="false"' in _PAGE.split('class="be-map-expand"', 1)[1][:120]


def test_zoom_overrides_the_svgs_natural_size_floor() -> None:
    """Zooming must move the same floor it reads, or a zoom-out below natural size silently does nothing.

    Regression test: the svg carries an inline ``min-width`` (BE-0337's natural-size floor). Setting
    only ``style.width`` while that min-width stays at its original value clamps the *rendered* size
    below it, desyncing it from the tracked ``style.width`` that later zoom clicks step from —
    ``setMapWidth`` must move ``min-width`` in lockstep, and ``resetMapWidth`` must restore the floor
    to the natural size rather than dropping it outright.
    """
    script = brd.filter_script()
    set_width = script[
        script.index("function setMapWidth") : script.index("function resetMapWidth")
    ]
    assert "mapSvg.style.minWidth=px" in set_width
    reset_width = script[script.index("function resetMapWidth") : script.index("zoomBtns.forEach")]
    assert "mapSvg.style.minWidth=" in reset_width
    assert "vb.width" in reset_width


def test_the_map_ships_a_hover_card_container() -> None:
    """The card's four descriptive fields (id, status, title, summary) sit in an aria-hidden span —
    the live-region readout is their accessible source — while the related-chip row sits outside
    it, since those are real navigation rather than decoration. A second, wholly decorative card
    previews whichever chip the pointer rests on.
    """
    map_view = _PAGE.split('class="be-map-view', 1)[1]
    assert 'class="be-map-card">' in map_view
    assert 'class="be-map-card-info" aria-hidden="true"' in map_view
    for field in ("id", "status", "title", "summary"):
        assert f'class="be-map-card-{field}"' in map_view
    assert 'class="be-map-card-related"' in map_view
    assert 'class="be-map-chip-card" aria-hidden="true"' in map_view


def test_the_map_node_carries_no_native_tooltip() -> None:
    """A native ``<title>`` tooltip would duplicate, and visually collide with, the hover card."""
    for node in brd.map_layout(_ITEMS)["nodes"][:5]:
        name = html.escape(f"{node['id']} — {node['title']}")
        assert f"<title>{name}</title>" not in _PAGE


def test_each_node_carries_its_related_ids_for_the_hover_cards_links() -> None:
    """The card's related-links list is built from ``data-related`` rather than re-deriving the
    graph in JS, so every drawn node must carry the ids of everything it shares an edge with.
    """
    data = brd.graph_data(_ITEMS)
    neighbors: dict[str, set[str]] = {}
    for edge in data["edges"]:
        neighbors.setdefault(edge["source"], set()).add(edge["target"])
        neighbors.setdefault(edge["target"], set()).add(edge["source"])
    for node in data["nodes"]:
        assert set(node["related"]) == neighbors[node["id"]]
        assert node["related"] == sorted(node["related"])
    for node in brd.map_layout(_ITEMS)["nodes"][:5]:
        assert f'data-related="{html.escape(" ".join(node["related"]))}"' in _PAGE

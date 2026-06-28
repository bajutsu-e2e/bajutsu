"""Tests for the self-contained crawl screen-map HTML report (bajutsu/crawl_report.py, BE-0038).

The report is a pure, deterministic function of a `ScreenMap` (the crawl's already-captured model):
it lays the screens out in BFS depth columns, draws the transitions as a static inline SVG, and
links each screen to its screenshot — no device, no model, no external asset, no JavaScript.
"""

from __future__ import annotations

from bajutsu.crawl import Alert, Crash, Edge, Node, ScreenMap
from bajutsu.crawl_report import layout, render_html, write_html


def _node(fp: str, ids: tuple[str, ...] = (), actions: tuple[str, ...] = ()) -> Node:
    return Node(fingerprint=fp, kind="screen", ids=ids, actions=actions)


def _map(*, nodes: list[Node], edges: list[Edge]) -> ScreenMap:
    return ScreenMap(nodes={n.fingerprint: n for n in nodes}, edges=edges)


def test_layout_places_screens_in_bfs_depth_columns() -> None:
    # a -> b -> c : depth 0, 1, 2, so each box sits in a further-right column.
    sm = _map(
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[Edge("a", "tap next", "b"), Edge("b", "tap next", "c")],
    )
    boxes = {bx.fp: bx for bx in layout(sm).boxes}
    assert boxes["a"].x < boxes["b"].x < boxes["c"].x  # deeper screens are further right
    assert boxes["a"].y == boxes["b"].y == boxes["c"].y  # one per layer -> same row


def test_layout_siblings_share_a_column_in_separate_rows() -> None:
    # a -> b and a -> c : b and c are both depth 1, stacked in the same column.
    sm = _map(
        nodes=[_node("a"), _node("b"), _node("c")],
        edges=[Edge("a", "tap b", "b"), Edge("a", "tap c", "c")],
    )
    boxes = {bx.fp: bx for bx in layout(sm).boxes}
    assert boxes["b"].x == boxes["c"].x  # same depth -> same column
    assert boxes["b"].y != boxes["c"].y  # different rows


def test_layout_pure_cycle_falls_back_to_a_root() -> None:
    # a -> b -> a : every screen has an incoming edge, so there is no natural root; the layout must
    # still place both (fall back to the first fingerprint as depth 0) rather than dropping them.
    sm = _map(nodes=[_node("a"), _node("b")], edges=[Edge("a", "go", "b"), Edge("b", "back", "a")])
    boxes = {bx.fp: bx for bx in layout(sm).boxes}
    assert set(boxes) == {"a", "b"}  # both placed
    assert boxes["a"].x < boxes["b"].x  # "a" (first fingerprint) is the fallback root at depth 0


def test_layout_unreachable_screen_gets_a_column_zero_slot() -> None:
    # "b" has no transition touching it at all — it must still be placed (column 0), not dropped.
    sm = _map(nodes=[_node("a"), _node("b")], edges=[Edge("a", "tap self", "a")])
    boxes = {bx.fp: bx for bx in layout(sm).boxes}
    assert set(boxes) == {"a", "b"}


def test_render_html_draws_a_self_loop_edge() -> None:
    # A transition back to the same screen (src == dst) is drawn as a self-loop, not skipped.
    sm = _map(nodes=[_node("a")], edges=[Edge("a", "tap refresh", "a")])
    lay = layout(sm)
    assert len(lay.edges) == 1  # the self-loop is routed
    assert "1 transition" in render_html(sm) and "<path" in render_html(sm)


def test_render_html_marks_an_alert_transition() -> None:
    sm = _map(nodes=[_node("a"), _node("b")], edges=[Edge("a", "tap allow", "b", alert=("Allow",))])
    [edge] = layout(sm).edges
    assert edge.alert is True
    assert "🛡️" in render_html(sm)  # the alert marker is drawn


def test_render_html_is_self_contained() -> None:
    sm = _map(nodes=[_node("abc1234", ids=("home.title",))], edges=[])
    html = render_html(sm, run_id="20260101-000000")
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "<style>" in html  # inline CSS
    assert "<script" not in html  # no JavaScript
    assert 'src="http' not in html and 'href="http' not in html  # no external asset


def test_render_html_shows_screens_edges_and_summary() -> None:
    sm = _map(
        nodes=[_node("abc1234", ids=("home.title",)), _node("def5678")],
        edges=[Edge("abc1234", "tap home.start", "def5678")],
    )
    html = render_html(sm)
    assert "abc1234"[:7] in html  # the short fingerprint label
    assert "home.title" in html  # the screen's id
    assert "2 screens" in html and "1 transition" in html  # the summary counts
    assert "<svg" in html and "<path" in html  # the transition is drawn as an SVG edge


def test_render_html_lists_crashes_and_alerts() -> None:
    sm = ScreenMap(
        nodes={"a": _node("a")},
        edges=[],
        crashes=[Crash(("tap home.start", "tap risky"))],
        alerts=[Alert(("tap home.start",), ("Allow",))],
    )
    html = render_html(sm)
    assert "tap risky" in html  # the crashing action path
    assert "Allow" in html  # the dismissed alert button
    assert "1 crash" in html


def test_render_html_links_screenshot_when_present() -> None:
    sm = _map(nodes=[_node("abc1234")], edges=[])
    html = render_html(sm, have_screens=frozenset({"abc1234"}))
    assert 'src="screens/abc1234.png"' in html  # relative link, resolves next to the report
    # a screen with no captured screenshot must not emit a broken <img>
    assert 'src="screens/' not in render_html(sm)


def test_render_html_escapes_ids() -> None:
    sm = _map(nodes=[_node("a", ids=("<svg>.x",))], edges=[])
    html = render_html(sm)
    assert "<svg>.x" not in html  # escaped, not injected verbatim
    assert "&lt;svg&gt;.x" in html


def test_render_html_empty_map_is_valid() -> None:
    html = render_html(ScreenMap())
    assert html.lstrip().startswith("<!DOCTYPE html>")
    assert "0 screens" in html


def test_write_html_writes_report_next_to_screens(tmp_path) -> None:  # type: ignore[no-untyped-def]
    out_dir = tmp_path / "20260101-000000"
    (out_dir / "screens").mkdir(parents=True)
    (out_dir / "screens" / "abc1234.png").write_bytes(b"\x89PNG")  # a captured screenshot
    sm = _map(nodes=[_node("abc1234")], edges=[])

    report = write_html(out_dir, sm)

    assert report == out_dir / "screenmap.html"
    body = report.read_text(encoding="utf-8")
    assert body.lstrip().startswith("<!DOCTYPE html>")
    assert 'src="screens/abc1234.png"' in body  # the on-disk screenshot is linked

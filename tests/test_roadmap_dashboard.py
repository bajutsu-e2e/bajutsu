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
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_MODULE_PATH = _ROOT / "scripts" / "build_roadmap_dashboard.py"
_spec = importlib.util.spec_from_file_location("build_roadmap_dashboard", _MODULE_PATH)
assert _spec and _spec.loader
brd = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = brd
_spec.loader.exec_module(brd)

_ITEMS = brd.bri.load_items(_ROOT / "roadmaps")
_PAGE = brd.build_page(_ITEMS)


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
    per-item count matches the card count and each url is the one the id predicts.
    """
    assert _PAGE.count('class="be-issue"') == len(_ITEMS)
    for item in _ITEMS:
        en = item.by_lang["en"]
        url = html.escape(brd.bri.tracking_issue_url(en.id))
        assert f'<a class="be-issue" href="{url}"' in _PAGE, f"{en.id} issue link missing"


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


def test_html_is_escaped() -> None:
    """Titles flow through html.escape, so a stray angle bracket can't break the markup."""
    entry_cls = type(_ITEMS[0].by_lang["en"])
    item_cls = type(_ITEMS[0])
    sample = item_cls(
        id="BE-9999",
        slug="x",
        bucket="Proposals",
        topic=_ITEMS[0].topic,
        by_lang={
            "en": entry_cls(
                id="BE-9999",
                slug="x",
                title="a <script> & b",
                status="Proposal",
                origin=None,
            )
        },
    )
    out = brd.render_html([sample])
    assert "a &lt;script&gt; &amp; b" in out
    assert "<script>" not in out

#!/usr/bin/env python3
"""Generate the roadmap status dashboard page for the docs site (BE-XXXX).

The roadmap's source of truth is the per-item metadata under ``roadmaps/<category>/BE-NNNN-<slug>/``
— the same metadata the index generator (``build_roadmap_index.py``) reads. This renders that live
metadata as a single self-contained HTML dashboard, ``docs/api/roadmap.md``, that the existing
MkDocs site publishes to GitHub Pages: cards grouped by category (Topic), each card carrying its own
status (Implemented / In progress / Proposal / Deferred) and linking to its item on GitHub. Each
category shows a progress figure — the share of its items that are Implemented — and a stacked bar
of its full status composition.

Like the generated API reference (``site/``), the page is a **build artifact, never committed**: it
is regenerated from the live tree on every docs build, so it can never drift from the roadmap and is
never coupled to the CI BE-id-allocation machinery. ``make docs`` / ``make docs-serve`` regenerate
it first; the ``docs`` workflow does the same before publishing.

Usage::

    python scripts/build_roadmap_dashboard.py            # write docs/api/roadmap.md
    python scripts/build_roadmap_dashboard.py --out PATH  # write elsewhere (tests)

Only facts the metadata carries are shown. The per-category progress percentage is derived purely
from the Status field (Implemented items / total items in the category), so it has a source of truth;
no per-item completion figure is invented — that lives in no item's metadata.
"""

from __future__ import annotations

import argparse
import html
import importlib.util
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


def _item_href(item: Any) -> str:
    """The GitHub URL of an item's English markdown file."""
    en = item.by_lang["en"]
    name = f"{en.id}-{en.slug}"
    return f"{REPO_BLOB}/roadmaps/{en.category}/{name}/{name}.md"


def _card(item: Any) -> str:
    en = item.by_lang["en"]
    color = BUCKET_COLOR[item.bucket]
    label = BUCKET_LABEL[item.bucket]
    origin = f'<span class="be-origin">{html.escape(en.origin)}</span>' if en.origin else ""
    return (
        f'<a class="be-card" data-status="{html.escape(item.bucket)}" '
        f'style="border-left-color:{color}" href="{_item_href(item)}">'
        '<span class="be-card-top">'
        f'<span class="be-id" style="color:{color}">{html.escape(en.id)}</span>'
        f'<span class="be-badge" style="color:{color};border-color:{color}">{html.escape(label)}</span>'
        "</span>"
        f'<span class="be-title">{html.escape(en.title)}</span>'
        f"{origin}"
        "</a>"
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


def render_html(items: list[Any]) -> str:
    """Render the dashboard body: a status filter, then one section per category with its progress.

    Sections are category-major (by Topic); each item card carries its own status (colour + badge),
    and each category shows a progress figure derived purely from the Status field — the share of its
    items that are Implemented — beside a stacked bar of the full status composition.
    """
    by_bucket: dict[str, list[Any]] = {name: [] for name, _key in bri.BUCKETS}
    by_topic: dict[str, list[Any]] = {topic: [] for topic, _key, _origin in bri.TOPICS}
    for item in items:
        by_bucket[item.bucket].append(item)
        by_topic[item.topic].append(item)

    # The summary doubles as a status filter: each chip is a button a small script (below) wires to
    # show only cards of that status. Without JavaScript the buttons are inert and every card stays
    # visible, so the page is still fully readable — filtering is progressive enhancement.
    all_chip = (
        '<button type="button" class="be-stat be-filter is-active" data-filter="all">'
        f"<b>{len(items)}</b> All</button>"
    )
    bucket_chips = "".join(
        f'<button type="button" class="be-stat be-filter" data-filter="{html.escape(name)}" '
        f'style="border-color:{BUCKET_COLOR[name]}">'
        f'<b style="color:{BUCKET_COLOR[name]}">{len(by_bucket[name])}</b> {html.escape(name)}'
        "</button>"
        for name, _key in bri.BUCKETS
    )
    summary_cells = all_chip + bucket_chips

    sections: list[str] = []
    for topic, _key, _origin in bri.TOPICS:
        cat_items = by_topic[topic]
        if not cat_items:
            continue
        counts = {name: sum(1 for it in cat_items if it.bucket == name) for name, _k in bri.BUCKETS}
        total = len(cat_items)
        implemented = counts["Implemented"]
        pct = round(100 * implemented / total)
        cards = "".join(_card(it) for it in sorted(cat_items, key=lambda it: it.id))
        sections.append(
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

    return (
        '<div class="be-dash">'
        f'<div class="be-summary" role="group" aria-label="Filter by status">{summary_cells}</div>'
        f"{''.join(sections)}"
        "</div>"
    )


_STYLE = """
<style>
.be-dash{font-size:14px}
.be-summary{display:flex;flex-wrap:wrap;gap:.6rem;margin:.5rem 0 1.5rem}
.be-stat{border:1px solid;border-radius:8px;padding:.25rem .7rem;font-size:13px}
.be-stat b{font-weight:600}
.be-filter{cursor:pointer;background:none;font:inherit;color:inherit}
.be-filter.is-active{background:rgba(128,128,128,.14);font-weight:600}
.be-cat.is-hidden,.be-card.is-hidden{display:none}
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
  border-left:3px solid;border-radius:8px;padding:.5rem .65rem;text-decoration:none;color:inherit}
.be-card:hover{background:rgba(128,128,128,.08)}
.be-card-top{display:flex;align-items:center;justify-content:space-between;gap:.4rem}
.be-id{font-size:12px;font-weight:600}
.be-badge{font-size:10px;border:1px solid;border-radius:4px;padding:0 .35rem;white-space:nowrap}
.be-title{font-size:13px;line-height:1.35}
.be-origin{font-size:11px;color:#888}
</style>
"""

# Progressive enhancement. On load the script collapses every category to a compact overview (just
# the heading and its progress bar); the collapsed state is applied by JS, never baked into the
# markup, so with scripting off every category stays open and the page is fully readable. Status
# filter: a chip hides cards of other statuses, hides any emptied category, and expands the ones with
# matches so the results show; "All" returns to the collapsed overview. Each heading also toggles its
# own category. Nothing fetches or computes — it only shows and hides already-rendered markup.
_SCRIPT = """
<script>
(function(){
  var filters=document.querySelectorAll('.be-filter');
  var cards=document.querySelectorAll('.be-card');
  var cats=document.querySelectorAll('.be-cat');
  function setCollapsed(cat, collapsed){
    cat.classList.toggle('is-collapsed', collapsed);
    var head=cat.querySelector('.be-cat-head');
    if(head) head.setAttribute('aria-expanded', String(!collapsed));
  }
  function apply(value){
    cards.forEach(function(c){
      c.classList.toggle('is-hidden', value!=='all' && c.getAttribute('data-status')!==value);
    });
    cats.forEach(function(cat){
      var hasMatch=!!cat.querySelector('.be-card:not(.is-hidden)');
      cat.classList.toggle('is-hidden', !hasMatch);
      setCollapsed(cat, value==='all' ? true : !hasMatch);
    });
    filters.forEach(function(f){
      f.classList.toggle('is-active', f.getAttribute('data-filter')===value);
    });
  }
  filters.forEach(function(f){
    f.addEventListener('click', function(){ apply(f.getAttribute('data-filter')); });
  });
  cats.forEach(function(cat){
    var head=cat.querySelector('.be-cat-head');
    function toggle(){ setCollapsed(cat, !cat.classList.contains('is-collapsed')); }
    head.addEventListener('click', toggle);
    head.addEventListener('keydown', function(e){
      if(e.key==='Enter'||e.key===' '){ e.preventDefault(); toggle(); }
    });
    setCollapsed(cat, true);
  });
})();
</script>
"""

_INTRO = (
    "# Roadmap status\n\n"
    "Live view of every roadmap (BE) item, grouped by category — each category showing the share of "
    "its items already implemented, and each card its own status. Regenerated from item metadata on "
    "every docs build, so it always reflects the committed roadmap. Categories start collapsed to a "
    "progress overview — click a heading to expand it, or use the chips to filter by status. Each "
    "card links to its full proposal on GitHub. The full index with both languages "
    "lives in [`roadmaps/README.md`](https://github.com/bajutsu-e2e/bajutsu/blob/main/roadmaps/README.md).\n\n"
)


def build_page(items: list[Any]) -> str:
    """The complete ``roadmap.md`` content: intro prose, the dashboard HTML, styles, and filter JS."""
    return f"{_INTRO}{render_html(items)}\n{_STYLE}{_SCRIPT}"


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output path for the page")
    args = parser.parse_args(argv)
    try:
        items = bri.load_items(bri.ROADMAP)
    except ValueError as exc:
        print(exc, file=sys.stderr)
        return 1
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(build_page(items), encoding="utf-8")
    print(f"wrote {args.out} ({len(items)} items)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

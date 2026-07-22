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
    """The GitHub URL of an item's English markdown file (flat ``roadmaps/`` path, BE-0159)."""
    en = item.by_lang["en"]
    name = f"{en.id}-{en.slug}"
    return f"{REPO_BLOB}/roadmaps/{name}/{name}.md"


def _card(item: Any) -> str:
    en = item.by_lang["en"]
    color = BUCKET_COLOR[item.bucket]
    label = BUCKET_LABEL[item.bucket]
    origin = f'<span class="be-origin">{html.escape(en.origin)}</span>' if en.origin else ""
    # The card's primary click target stays the proposal file (the whole main link); the Issue pill is
    # an additive second link to the item's BE-0109 tracking issue, built from its id alone. The two
    # are sibling <a>s under a <div> rather than one nested in the other, since nested anchors are
    # invalid HTML. The link is a search that can legitimately return zero results (a "born
    # implemented" item never opened a tracking issue), so it is labelled "Issue" and titled as a
    # search, not a guaranteed issue (BE-0139).
    issue_url = html.escape(bri.tracking_issue_url(en.id))
    return (
        f'<div class="be-card" data-status="{html.escape(item.bucket)}" '
        f'data-topic="{html.escape(item.topic)}" '
        f'style="border-left-color:{color}">'
        f'<a class="be-card-main" href="{_item_href(item)}">'
        '<span class="be-card-top">'
        f'<span class="be-id" style="color:{color}">{html.escape(en.id)}</span>'
        f'<span class="be-badge" style="color:{color};border-color:{color}">{html.escape(label)}</span>'
        "</span>"
        f'<span class="be-title">{html.escape(en.title)}</span>'
        f"{origin}"
        "</a>"
        f'<a class="be-issue" href="{issue_url}" title="Search GitHub for this item&#39;s '
        'tracking issue (may have no results)">Issue</a>'
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


def render_html(items: list[Any]) -> str:
    """Render the dashboard body: search box, status filter, category sections, empty-state region.

    Sections are category-major (by Topic); each item card carries its own status (colour + badge),
    and each category shows a progress figure derived purely from the Status field — the share of its
    items that are Implemented — beside a stacked bar of the full status composition.
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
        counts = {name: sum(1 for it in cat_items if it.bucket == name) for name, _k in bri.BUCKETS}
        total = len(cat_items)
        implemented = counts["Implemented"]
        pct = round(100 * implemented / total)
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
    empty = '<div class="be-empty" role="status"></div>'
    return f'<div class="be-dash">{filters}{groups}{empty}</div>'


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
.be-group.is-hidden,.be-cat.is-hidden,.be-card.is-hidden{display:none}
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
.be-issue{align-self:flex-start;font-size:10px;color:#888;text-decoration:none;
  border:1px solid rgba(128,128,128,.3);border-radius:4px;padding:0 .35rem}
.be-issue:hover{color:inherit;border-color:currentColor}
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
# readable. Each heading also toggles its own category. Nothing fetches or computes — it only shows and
# hides already-rendered markup.
_SCRIPT = """
<script>
(function(){
  var search=document.querySelector('.be-search');
  var checks=document.querySelectorAll('.be-check');
  var cards=document.querySelectorAll('.be-card');
  var cats=document.querySelectorAll('.be-cat');
  var groups=document.querySelectorAll('.be-group');
  var empty=document.querySelector('.be-empty');
  var on={};
  checks.forEach(function(c){ on[c.getAttribute('data-filter')]=c.checked; });
  // The searchable text of each card, lower-cased once: id + title + topic + status.
  var hay=[];
  cards.forEach(function(c){
    var id=c.querySelector('.be-id'), title=c.querySelector('.be-title');
    hay.push([
      id?id.textContent:'', title?title.textContent:'',
      c.getAttribute('data-topic')||'', c.getAttribute('data-status')||''
    ].join(' ').toLowerCase());
  });
  function terms(){
    return (search?search.value:'').toLowerCase().split(/\\s+/).filter(Boolean);
  }
  function setCollapsed(cat, collapsed){
    cat.classList.toggle('is-collapsed', collapsed);
    var head=cat.querySelector('.be-cat-head');
    if(head) head.setAttribute('aria-expanded', String(!collapsed));
  }
  function apply(){
    var allOn=Object.keys(on).every(function(s){ return on[s]; });
    var q=terms(), hasQuery=q.length>0, matched=0, shown=0;
    cards.forEach(function(c, i){
      var match=q.every(function(t){ return hay[i].indexOf(t)>=0; });
      if(match) matched++;
      var visible=on[c.getAttribute('data-status')] && match;
      if(visible) shown++;
      c.classList.toggle('is-hidden', !visible);
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
    "[BE-0109](https://github.com/bajutsu-e2e/bajutsu/blob/main/roadmaps/implemented/"
    "BE-0109-roadmap-tracking-issues/BE-0109-roadmap-tracking-issues.md) for how the sync works.\n\n"
    "Live view of every roadmap (BE) item, grouped by category — each category showing the share of "
    "its items already implemented, and each card its own status. Regenerated from item metadata on "
    "every docs build, so it always reflects the committed roadmap. Fully-implemented categories are "
    "grouped separately under Completed. Categories start collapsed to a progress overview — click a "
    "heading to expand it, toggle the status chips on and off, or type in the search box to narrow the "
    "cards by id, title, topic, or status. Each card links to its "
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

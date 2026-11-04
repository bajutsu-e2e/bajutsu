**English** · [日本語](BE-0219-roadmap-dashboard-search-ja.md)

# BE-0219 — Text search on the roadmap status dashboard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0219](BE-0219-roadmap-dashboard-search.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0219") |
| Implementing PR | [#888](https://github.com/bajutsu-e2e/bajutsu/pull/888), [#918](https://github.com/bajutsu-e2e/bajutsu/pull/918) |
| Topic | Contributor workflow |
| Related | [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a free-text search box to the roadmap status dashboard, the GitHub Pages page shipped
by [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md). A reader types
a keyword and the cards narrow at once to the matching items. Search matches an item's **id, title,
topic, and status**, and it composes with the existing status-chip filter.

Like the rest of the page, search is a purely client-side enhancement over markup that is already
rendered. It emits no new data and adds no build-time or runtime dependency.

## Motivation

The dashboard already lets a reader filter by status (the four toggle chips) and collapse categories.
To find a *specific* item, such as "where's the one about tracking issues?" or "did we ever propose
X?", the reader has only the browser's Ctrl+F or a visual scan of the cards.

As the roadmap grows past ~200 items across many topics, that search becomes the slowest part of using
the page. Ctrl+F highlights raw text anywhere on the page, including the intro prose and collapsed
sections. It does not *filter to the matching cards*, and it cannot combine with the status filter.

A first-class search box turns "find the BE item I half-remember" into one keystroke-driven
interaction, which is the dashboard's main day-to-day job.

This is a documentation and contributor-workflow improvement. It stays clear of every prime directive.
The dashboard is a static docs-site artifact, so no LLM is involved, and the change touches no
`run`/CI verdict path and no determinism surface. Nothing in it is app-specific, because the search is
generic over whatever items the metadata produces.

## Detailed design

[`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) generates the
dashboard. `render_html` emits one `.be-card` per item, and each card already carries `data-status`,
its id, title, and origin. The cards are grouped into `.be-cat` sections whose `<h3>` is the Topic. A
`<script>` block progressively enhances the status chips. The search box slots into exactly that
structure.

The work is MECE across these units:

1. **Search input in the markup.** Render a labelled `<input type="search">` with a placeholder and an
   `aria-label`. It sits on its own row (`.be-search-row`) above the status chips, and both rows live
   inside the `.be-filters` container (the chips sit in a `.be-chips` row). The input is inert without
   JavaScript, so the no-JS page is unchanged. This is progressive enhancement, matching the existing
   filter.

2. **Card-level searchable metadata.** The matcher should not scrape rendered text, so each `.be-card`
   gets a `data-topic` attribute. The id, title, and status are already present as `data-status` and
   rendered text. The four searchable fields (id, title, topic, status) are then readable directly off
   each card's attributes and text. Matching is a case-insensitive substring match. Whitespace-separated
   words are AND terms, so all terms must match somewhere across the fields.

3. **Filter logic that composes with status.** Extend the existing `apply()` in `_SCRIPT` so a card is
   visible only when it passes **both** the status chips **and** the current query. An empty query
   matches everything. Reuse the existing category and group hiding: a category left with no visible
   card hides, and a group left with no visible category hides. While a query is active, matching
   categories **expand**, overriding the "all-status-on ⇒ collapse to overview" default. Results are
   then visible without a click.

4. **Empty-state message.** When a non-empty query matches zero cards, show a small "No items match
   *query*" line instead of a blank page. The line clears when the query is emptied or broadened.

5. **Styling.** Add the input's CSS to `_STYLE`. It stays consistent with the chip styling, takes one
   line, and adds no new assets. Make sure it reads well in both the light and dark MkDocs themes.

6. **Tests.** Extend [`tests/test_roadmap_dashboard.py`](../../tests/test_roadmap_dashboard.py) to
   assert three things: the rendered page contains the search input, every card carries `data-topic`,
   and the filter script wires the input's `input` event. These assertions are the machine-checkable
   outcome. They need no browser and keep the gate fast and LLM-free.

What the page *contains* does not change, because the item set and its fields stay as they are. Only
how a reader narrows the page changes.

`docs/architecture.md` and `docs/ja/` need no update, because they describe the dashboard's purpose,
not its filter affordances. If a sentence there enumerates the filters, update it in the same PR
(BE-0113).

## Alternatives considered

- **Rely on the browser's Ctrl+F.** It needs zero code, but it highlights instead of filtering. It
  spills into the intro prose and collapsed sections, and it cannot combine with the status chips.
  Those are exactly the gaps this item closes.
- **Server-side or indexed search (for example, MkDocs' built-in search plugin).** The site already
  has site-wide search, but that search finds *pages*, not *cards within the dashboard*. It also
  returns the reader to the top of the page instead of filtering the grid. A card-level filter is the
  right granularity, and it keeps the dashboard self-contained: one generated page, no plugin coupling.
- **Full-text search over each item's prose (Introduction, Motivation, and so on).** Rejected for this
  item. The dashboard renders only metadata, not the proposal bodies. Matching prose would force the
  generator to embed every item's full text into the page, a large and redundant payload for marginal
  gain. Scoping to id, title, topic, and status keeps the page lean. Prose search already lives in the
  repo (grep and GitHub search) and in the `/roadmap-filter` AI skill (BE-0162).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Search input rendered into the filter row (progressive enhancement).
- [x] `data-topic` on each card; four searchable fields readable off the card.
- [x] Filter logic composing search with the status chips (AND); matching categories expand.
- [x] Empty-state ("no items match") message.
- [x] Styling for both light and dark themes.
- [x] Tests in `tests/test_roadmap_dashboard.py`.

Log:

- Implemented all six units in `scripts/build_roadmap_dashboard.py` and its test — the search input,
  per-card `data-topic`, the query-aware `apply()` (AND with the status chips, matching categories
  expand), the empty-state line, the CSS, and the assertions. Verified the filter end to end in a
  browser (id/title/topic/status matching, chip composition, empty state, and reset to the collapsed
  overview) beyond the deterministic tests.
- Review follow-ups (PR #888): number-agreed the chip-hidden empty-state message, kept the native
  focus ring on the search input, and generalised the empty-state into an always-present `aria-live`
  region that explains *any* empty grid — the query matched nothing, its matches are chip-hidden, or
  (no query) the chips alone hid everything — so the page never goes silently blank.
- Layout follow-up (PR #918): moved the search box onto its own row (`.be-search-row`) above the
  status chips instead of sharing one row with them, wrapping both in a `.be-filters` container (the
  chips now sit in `.be-chips`). Detailed design §1 above is updated to match.

## References

- [BE-0094 — Generated roadmap status dashboard](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md)
  — the dashboard this item extends.
- [BE-0139 — Link the roadmap dashboard and items to their tracking issue](../BE-0139-roadmap-dashboard-issue-links/BE-0139-roadmap-dashboard-issue-links.md)
- [BE-0162 — Roadmap status-filter skill for AI sessions](../BE-0162-roadmap-status-filter-skill/BE-0162-roadmap-status-filter-skill.md)
  — the AI-side counterpart (status filtering for Claude sessions).
- [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) — the generator the search box lands in.

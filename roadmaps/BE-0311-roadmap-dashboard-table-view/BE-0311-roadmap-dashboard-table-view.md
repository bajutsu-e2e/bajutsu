**English** · [日本語](BE-0311-roadmap-dashboard-table-view-ja.md)

# BE-0311 — Sortable table view for the roadmap dashboard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0311](BE-0311-roadmap-dashboard-table-view.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0311") |
| Implementing PR | [#1306](https://github.com/bajutsu-e2e/bajutsu/pull/1306) |
| Topic | Contributor workflow |
| Related | [BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md), [BE-0219](../BE-0219-roadmap-dashboard-search/BE-0219-roadmap-dashboard-search.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a table view to the roadmap status dashboard, as an alternative to its card grid. The
dashboard is the GitHub Pages page shipped by
[BE-0094](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md) and extended with
search by [BE-0219](../BE-0219-roadmap-dashboard-search/BE-0219-roadmap-dashboard-search.md).

A reader toggles between Cards and Table. The table renders every item as one row with six sortable
columns: ID, Title, Topic, Status, Created, and Updated. Clicking a column header sorts the whole
table by that column, ascending or descending.

The chosen view persists across visits through the browser's `localStorage`. The existing search box
and status-filter chips keep working unchanged in both views.

Created and Updated are two columns that the dashboard has never shown before. The build derives each
date from the item's Git history: the earliest and latest commit across both language files of the
item. No new hand-set field is added. The dashboard then need not trust every future edit to keep two
dates in sync with the truth.

## Motivation

The card grid groups items by Topic and shows each item's status as a badge. That layout suits
browsing one category at a time. It cannot answer a different, common question: which items changed
most recently, or how the whole roadmap looks sorted by id regardless of topic. A card grid has no
stable column to sort by. Sorting becomes meaningful once every item is a row in one flat table, no
longer boxed into the section of its topic.

Two contributor workflows want that flat, sortable table:

- Reviewing what changed recently means sorting by Updated and scanning the top of the list. Today a
  reviewer opens each category by hand and compares dates that do not exist on the page.
- Reviewing how long a proposal has sat untouched means sorting by Created.

The existing card layout serves neither workflow. The browser's Ctrl+F and the search box of the
dashboard do not help either, because both narrow *which* cards show and never *what order* they show
in.

The change is a documentation and contributor-workflow improvement, and it stays clear of every prime
directive ([`CLAUDE.md`](../../CLAUDE.md)):

- The dashboard is a static docs-site artifact, so no large language model (LLM) sits near it.
- No `run`/CI verdict path exists here.
- No determinism surface is touched.
- The table is generic over whatever items the metadata produces, so nothing about it is
  app-specific.

## Detailed design

[`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) generates the
dashboard. The function `render_html` emits one `.be-card` per item inside `.be-cat` topic sections.
Each section carries a progress bar derived from `Status` alone. A `<script>` block progressively
enhances the search box and status chips (BE-0219).

The table view slots into that structure and does not replace it. The work is mutually exclusive,
collectively exhaustive (MECE) across these units:

1. **Git-derived Created / Updated dates.** Extend the `Item` of
   [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) with two ISO-8601 dates.
   The build reads the dates by running `git log --follow --format=%aI -- <path>` once per language
   file: the English file and its `-ja.md` mirror. The build then combines the two results. `created`
   is the oldest line that either command reports, which is the first commit of each file. `updated`
   is the newest line, which is the most recent commit of either file.

   Running the two files separately, each with `--follow`, is deliberate. `--follow` accepts one path.
   It correctly walks the history of a file through the rename that every item undergoes when CI
   allocates its real id (`BE-0311-<slug>` → `BE-NNNN-<slug>`). Combining the results of both files
   keeps `updated` correct when one file changes after the other, for example in a Japanese-only
   wording fix.

   The dashboard has one firm rule about the data it shows (BE-0094's "Honesty of the data"): never
   invent a number with no source in the metadata. The rule holds for `Origin`, which the dashboard
   renders verbatim when an item sets it. The rule also holds for the per-topic progress percentage,
   which is derived from `Status` alone. A hand-set `Created`/`Updated` pair would not break the rule
   on day one. But `Origin` is written once at authoring time and rarely revisited, whereas a date
   pair needs a correct edit on every future change to the item. Such a field is the kind most likely
   to drift from the truth without notice. Deriving both dates from `git log` avoids adding the field.

   The derivation adds one prerequisite. The checkout step of
   [`.github/workflows/docs.yml`](../../.github/workflows/docs.yml) must fetch full history
   (`fetch-depth: 0`) instead of its current shallow clone. On a single-commit checkout, `git log`
   would report every item as created and updated on the same day. The trade-off is a small,
   recurring increase in checkout time on every docs build from then on. The increase is not a
   one-time cost.

   Two `git log --follow` subprocesses per item, roughly 600 across the current roadmap, add a second
   recurring cost. Each subprocess is a local read over an already fetched history, with no network
   round-trip, so the cost is expected to stay negligible next to the checkout itself. If the cost
   ever measurably slows the build, the cheaper alternative is one repository-wide
   `git log --name-status -M` walk that resolves the dates of every item in a single pass.
2. **View toggle.** Render a two-way control (`Cards` / `Table`) beside the existing `.be-filters`
   row. Both views read from the same rendered item data. The toggle shows one of two sibling
   containers (`.be-cards-view` / `.be-table-view`) and hides the other. Nothing is fetched or
   recomputed when a reader switches.
3. **Table markup.** Render one `<table>` per item set and one `<tr>` per item. The table has six
   `<th>` columns: ID, Title, Topic, Status, Created, and Updated. Each `<th>` carries a
   `data-sort-key` and a click handler. Reuse the existing per-item fields of each card (id, title,
   topic, status) and add the two new date columns.

   A row carries the same `data-status` / `data-topic` attributes as its corresponding card. The
   search box and status chips (BE-0219) then filter table rows as they filter cards, with no
   separate matching logic.

   The per-topic progress bar that a reader relies on in Cards view is not dropped. A compact strip
   above the table lists every topic with its existing stacked bar and percentage. The strip reuses
   the `_progress_bar` computation that the card sections already use.
4. **Client-side sort.** Extend the existing filter script (`_SCRIPT`) with a sort step. Clicking a
   `<th>` re-orders the `<tr>` elements of the table by that column:
   - String comparison for Title, Topic, and Status.
   - Id-numeric comparison for ID.
   - Chronological comparison for Created and Updated.

   Repeated clicks on the same column toggle ascending and descending. An `aria-sort` attribute marks
   the active column and direction. Sorting reorders the already-rendered rows that have passed
   through the status/search filter. Sorting never changes which rows are visible.
5. **Persisted view choice.** On toggling Cards/Table, write the choice to `localStorage` under a
   dedicated key. On page load, read that key and show the matching view before first render. Fall
   back to Cards, the current default, when the key is absent or `localStorage` is unavailable.

   Like the rest of the interactivity of the dashboard, the choice is progressive enhancement. Without
   JavaScript, the page shows the Cards view unconditionally, as it does today.
6. **Styling.** Add the table, toggle, and progress-strip CSS to `_STYLE`. The CSS matches the
   existing chip and card styling and stays legible in both the light and dark MkDocs themes.
7. **Tests.** Extend [`tests/test_roadmap_dashboard.py`](../../tests/test_roadmap_dashboard.py) to
   assert four outcomes:
   - Every item renders one table row with its six columns.
   - Each date column holds a value in ISO-8601 form.
   - The view toggle and its two containers are present.
   - The sort script wires a click handler to every `<th>`.

   These outcomes are machine-checkable. They need no browser and keep the gate fast and LLM-free.

The page *contains* the same items and fields as before, apart from the two new date columns. The
change adds a second way to lay the same items out. `docs/architecture.md` and `docs/ja/` need no
update, because they describe the purpose of the dashboard and not its view affordances. If a
sentence there enumerates the views of the dashboard, update the sentence in the same PR
([BE-0113](../BE-0113-design-doc-realignment/BE-0113-design-doc-realignment.md)).

## Alternatives considered

- **Replace the card grid outright instead of adding a toggle.** Rejected. The per-topic progress bar
  and the browse-by-category workflow would either disappear or need reinventing inside a table row.
  The sorting use case that this item targets gains nothing from that loss. A toggle keeps both
  workflows at full strength.
- **Hand-maintained `Created` / `Updated` metadata fields on each item, updated by the `ideation` and
  `implement-be` skills.** Rejected, for two reasons. The fields need a one-time backfill across
  roughly 300 existing items. And `Origin` is a fact set once at authoring time that rarely goes
  stale, whereas every future edit gives a hand-set date pair a chance to drift from the real history
  of the file. Deriving both dates from `git log` at build time removes the drift risk entirely. The
  cost is a small, recurring increase in the checkout time of the docs workflow on every future
  build, because the checkout fetches full history instead of a shallow clone.
- **Sort within each topic's card section instead of a flat table.** Rejected. The two motivating
  workflows, "what changed most recently" and "how old is this proposal", are both roadmap-wide
  questions. Confining the sort to one topic at a time would still require opening every section by
  hand to compare across sections. A flat table answers both in one sort.
- **A server-side / build-time sort (bake one fixed sort order into the generated page).** Rejected.
  A reader wants ID order sometimes and Updated order at other times, and a static page can bake in only one order. Client-side sort over already-rendered rows costs nothing at build time and serves every
  order a reader might want.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Git-derived `created` / `updated` dates on `Item`, and `docs.yml` checkout switched to
      `fetch-depth: 0`.
- [x] View toggle (`Cards` / `Table`) rendered beside the existing filter row.
- [x] Table markup: one row per item, six sortable columns, per-topic progress strip retained above
      the table.
- [x] Client-side sort wired to each column header, composing with the existing search/status
      filters.
- [x] Persisted view choice via `localStorage`, defaulting to Cards.
- [x] Styling for both light and dark themes.
- [x] Tests in `tests/test_roadmap_dashboard.py`.

## References

- [BE-0094 — Generated roadmap status dashboard](../BE-0094-roadmap-status-dashboard/BE-0094-roadmap-status-dashboard.md)
  — the dashboard this item extends.
- [BE-0219 — Text search on the roadmap status dashboard](../BE-0219-roadmap-dashboard-search/BE-0219-roadmap-dashboard-search.md)
  — the search/status filter this item's table view composes with.
- [BE-0139 — Link the roadmap dashboard and items to their tracking issue](../BE-0139-roadmap-dashboard-issue-links/BE-0139-roadmap-dashboard-issue-links.md)
- [`scripts/build_roadmap_dashboard.py`](../../scripts/build_roadmap_dashboard.py) — the generator the
  table view lands in.
- [`scripts/build_roadmap_index.py`](../../scripts/build_roadmap_index.py) — the shared `Item` model
  the Created/Updated dates extend.
- [`.github/workflows/docs.yml`](../../.github/workflows/docs.yml) — the docs build whose checkout
  needs full Git history for the date derivation.

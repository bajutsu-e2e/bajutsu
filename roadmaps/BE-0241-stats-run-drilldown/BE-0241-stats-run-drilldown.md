**English** · [日本語](BE-0241-stats-run-drilldown-ja.md)

# BE-0241 — Drill down from the Stats dashboard to the runs behind it

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0241](BE-0241-stats-run-drilldown.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0241") |
| Implementing PR | [#979](https://github.com/bajutsu-e2e/bajutsu/pull/979) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

The [`/stats`](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md) dashboard (BE-0102)
shows the trend of the whole suite: pass rate by day and backend, plus the scenarios, step actions,
and assertion kinds that fail most. Every figure is a bare aggregate. A cell reading "day
2026-07-10, idb, 4 failed" or "`LoginScenario > tap` fails 12×" carries no link back to *which* runs
produced it.

Today, finding those runs means leaving the page. The reader opens the flat, unfiltered run-history
list in the `serve` single-page application (SPA), `#panel-history`, and eyeballs it for entries that
look like a match.

This item makes every axis of the Stats dashboard clickable: date, backend, scenario, and
step or assertion hotspot. A click lands on exactly the matching runs. From there, the reader
reaches the existing per-run report (BE-0068).

## Motivation

- **BE-0102 is read-only by design and stays that way.** This item adds navigation to data that
  aggregation already computes. It adds no new judgment and no new stored artifact.
- **The report viewer already exists.** BE-0068's `/runs/<id>/report.html` renders live from the
  stored manifest.
- **The run-history list already exists.** `loadHistory()` in `bajutsu/templates/serve.panels.js`
  populates `bajutsu/templates/serve.html.j2#panel-history`, and each row already opens its report
  on click.
- **Showing a concrete scenario needs nothing built from scratch.** Two gaps remain:
  1. The Stats aggregator discards the run ids behind a scenario, step, or assertion hotspot once it
     has tallied them.
  2. The history list has no filter, so there is nothing to jump to even when the ids are known.
- **Today's flow is manual and error-prone.** A user reading "12 failures for `tap` in
  `LoginScenario`" has to guess which 12 of the unfiltered, unsorted history entries are relevant,
  among potentially hundreds of runs.
  - For a per-day or per-backend cell, this is merely tedious, because `by_run` already carries `day`
    and `backend` per point.
  - For a scenario, step, or assertion hotspot, it is currently impossible without re-reading raw
    manifests by hand.

## Detailed design

The design threads existing data through to the existing UI. It needs no new stored state, no new
endpoint, and no new report viewer.

1. **Retain run ids through the hotspot aggregators** (`bajutsu/stats.py`).
   - `_failing_scenarios`, `_failing_steps`, and `_failing_assertions` loop over each run's manifest.
     They tally `Counter`s keyed by scenario, `scenario > action`, or assertion kind.
   - Today they discard the manifest's `runId` once counted.
   - Add a `run_ids: tuple[str, ...]` field to `Hotspot`, sorted for determinism.
   - Each aggregator collects the contributing run ids per key alongside the existing reason tally.
     This is the same shape `_hotspots()` already reduces.
   - `by_day` and `by_backend` need no change. `Stats.by_run` already carries `run_id`, `day`, and
     `backend` per point. A date or backend click is answered by a plain client-side filter over
     data the page already has.
2. **Render the cells as deep links, server-side** (`bajutsu/templates/stats.html.j2`).
   - `stats.html.j2` is a pure server-rendered Jinja page today, with no `<script>` tag. This item
     keeps it that way.
   - The relevant cells become plain `<a href="/?tab=history&runs=id1,id2,…">` links. They are each
     `by_day` row, each `by_backend` row, and each `failing_scenarios` / `failing_steps` /
     `failing_assertions` row. The template builds the query string directly.
   - Day and backend rows derive the ids by filtering `stats.by_run` in the template. Hotspot rows
     use the new `Hotspot.run_ids`.
   - The Stats page gets no inline script and makes no follow-up API call. All client-side behavior
     lives on the SPA side (step 3). This matches BE-0102's "one aggregation pass, one page" shape.
3. **Land on the existing history list, filtered.**
   - `/stats` and the main `serve` SPA are separate routes today. A click therefore navigates to the
     SPA with the target ids in the URL, for example `/?tab=history&runs=id1,id2,…`.
   - On load, `serve.js` / `serve.panels.js` reads that query and switches to the History tab. It
     then applies a new client-side filter to the render of `loadHistory()`, so only the matching
     rows show. The list is unfiltered today, so this filter is the one new piece of history-list
     behavior this item adds.
   - A "filtered: <label> (N runs) · clear" affordance above the list drops back to the full history.
   - Clicking a filtered row behaves exactly as today. It opens that run's `/runs/<id>/report.html`
     (BE-0068), unchanged.
4. **Step and assertion granularity stops at the run.**
   - Per the scoping discussion, clicking a step or assertion hotspot filters history down to the
     runs where that step or assertion failed. It then opens the existing full-run report.
   - It does not anchor or highlight the specific step inside `report.html`.
   - That finer-grained jump is a natural follow-up and outside this item's scope.

## Alternatives considered

- **A dedicated drilldown list or modal built into the Stats page itself.** Rejected. It would
  duplicate the rendering and the click-through-to-report behavior of the run-history list in the
  `serve` SPA. That works against this repo's reuse-over-new-surface norm, the argument BE-0102
  itself made when it declined to fold into `report.html` or `audit`. Navigating to the existing
  history list, filtered, gets the same outcome with one rendering path instead of two.
- **A new `/api/runs?matching=…` endpoint computed on demand.** Rejected.
  - The aggregator already computes every hotspot's contributing run ids. It does so in the same
    pass that produces the counts `/stats` renders today.
  - A second endpoint would recompute or separately cache data the page already has.
  - It would also turn a single self-contained render into a page plus a follow-up fetch.
  - Serializing the ids into the existing `/stats` HTML keeps BE-0102's "one aggregation pass, one
    page" shape.
- **Anchor or highlight the specific step inside `report.html` on drilldown.** Deferred rather than
  rejected. The feature is useful, but `report.html` has no per-step anchors or ids to target today.
  It is separable follow-up work, not a blocker for the run-level drilldown this item delivers.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `Hotspot.run_ids` and thread run ids through `_failing_scenarios` / `_failing_steps` /
      `_failing_assertions` in `bajutsu/stats.py`.
- [x] Render the day / backend / hotspot rows in `stats.html.j2` as server-side
      `<a href="/?tab=history&runs=…">` deep links (no new inline script on the Stats page).
- [x] Add the `?tab=history&runs=…` deep-link handling and the history-list filter (with a clear
      affordance) to `serve.js` / `serve.panels.js`.

### Log

- Implemented in [#979](https://github.com/bajutsu-e2e/bajutsu/pull/979): threaded run ids through the
  hotspot aggregators via a shared `_HotspotTally`, added the `drill()` deep-link macro to
  `stats.html.j2` (day / backend rows filter `stats.by_run` in-template; hotspot rows use
  `Hotspot.run_ids`), and added the `?tab=history&runs=…&label=…` boot handler plus the history-list
  filter + clear affordance to `serve.panels.js` / `serve.author.js`.

## References

- [BE-0102 — Aggregate run-stats dashboard](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md) — the dashboard this item makes interactive.
- [BE-0068 — Regenerable reports](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) — the per-run report viewer every drilldown lands on.
- [BE-0049 — Determinism/flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — the `(scenarioHash, name)` identity `stats.py` already reuses.
- [BE-0239 — Deletable runs and reports in the serve Web UI](../BE-0239-deletable-runs-serve/BE-0239-deletable-runs-serve.md) — an adjacent proposal (still `Status: Proposal`, not yet in progress) touching the same run-history list this item filters.

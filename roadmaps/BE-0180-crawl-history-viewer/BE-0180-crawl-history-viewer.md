**English** · [日本語](BE-0180-crawl-history-viewer-ja.md)

# BE-0180 — Crawl history viewer in the Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0180](BE-0180-crawl-history-viewer.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0180") |
| Implementing PR | [#750](https://github.com/bajutsu-e2e/bajutsu/pull/750) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

This item adds a **history list of past crawls** to the Web UI's Crawl tab.

`bajutsu crawl` already writes a live-streamed `runs/<id>/screenmap.json`. On completion, it also writes a self-contained `runs/<id>/screenmap.html` ([BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)). While a crawl runs, the Crawl tab renders that JSON as an interactive graph, which is zoomable, pannable, and draggable ([BE-0072](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md), [BE-0095](../BE-0095-interactive-crawl-graph/BE-0095-interactive-crawl-graph.md)).

What is missing is a way back to that graph once the crawl has finished and the browser tab has moved on or closed. Today the only path to a past crawl's graph is opening `screenmap.html` from the run directory on disk. This item closes the gap. The Crawl tab lists past crawl runs and reopens the same interactive graph, read-only, for any of them.

## Motivation

The Web UI already has a run history: the Replay tab's History list, backed by `list_runs()` ([`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)). That list reads each run directory's `manifest.json` and summarizes it as a pass/fail scenario report.

A `bajutsu crawl` run writes no `manifest.json`. Its artifact is `screenmap.json`, a screen-and-transition map with no notion of pass or fail. Crawl runs therefore never appear in that list. A viewer who wants to revisit a completed crawl has no in-UI path to it. The viewer must know the run id and find `screenmap.html` on the filesystem, which defeats the point of driving crawls from a browser.

The same gap affects the two artifacts that a crawl produces alongside the map:

- One `crashes/crash-NNN.yaml` per faithfully replayable crash ([`crawl_repro.py`](../../bajutsu/crawl_repro.py)).
- One `flows/flow-NNN.yaml` per faithfully reachable screen ([`crawl_flows.py`](../../bajutsu/crawl_flows.py)).

Both are directly runnable by `bajutsu run`. Today nothing in the Web UI links to either, and finding them means browsing the run directory by hand.

## Detailed design

**1. A crawl-specific run listing.** Add `list_crawl_runs(runs_dir)` next to `list_runs()` in [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py). `list_runs()` requires `manifest.json`. The new function scans each run directory for a `screenmap.json` instead, the one file every crawl run writes regardless of outcome. It does not depend on whether a `manifest.json` also exists.

For each match, the function reads the JSON once and summarizes node, edge, and crash counts. These are the fields the Crawl tab already computes client-side from the fetched map, in `renderGraph`. The function also records whether `crashes/` and `flows/` hold any files. The list sorts newest-first by run id, mirroring `list_runs()`.

**2. A read endpoint.** Expose the listing as `/api/crawl/runs`, alongside the existing `/api/runs`, in [`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py). The crash and flow files need no new artifact-serving path. `/runs/<id>/...` already serves static run-directory content, which is how `screenmap.json` and screenshots reach the browser today. A `crashes/crash-001.yaml` link is just a path under that existing mount.

**3. A history list in the Crawl tab.** Add a dedicated list to the Crawl view in [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) and [`serve.html.j2`](../../bajutsu/templates/serve.html.j2), populated from `/api/crawl/runs`. The list stays separate from the Replay tab's `#history`. A crawl run's summary (screens, transitions, crashes) has no pass/fail shape to share with a scenario report.

Selecting an entry calls the existing `loadGraph(runId)`. That function already fetches `/runs/<runId>/screenmap.json` and renders it with `renderGraph` and `renderPlan`. The rendering path itself does not change.

**4. Read-only framing.** A selected historical run must not be confused with the live crawl form. While a historical entry is selected, the UI disables (or hides) these controls:

- the Start/Stop controls,
- the target/simulator pickers,
- the max-screens/max-steps budget fields.

The UI also labels the graph clearly as a past run, for example with the run id and a "past crawl" badge in the status line. Selecting **Start crawl** again returns to the live form and clears the historical selection. Today's `crawlDone` likewise leaves the tab ready for another run.

**5. Crash/flow links.** Next to the reopened graph, list the `crashes/*.yaml` and `flows/*.yaml` file names for the selected run, from the counts already gathered in step 1. Each name is a plain link into the existing `/runs/<id>/...` static mount, and opening one shows the raw scenario YAML. No new import path into a target's `scenarios/` directory or into the Author tab is part of this item (see Alternatives).

## Alternatives considered

- **Fold crawl runs into the Replay tab's existing History list.** Rejected. `list_runs()` and its list item are shaped around a pass/fail scenario report (`ok`, `passed`/`total`, `scenarios`), and a crawl run has none of those. Reusing the list would force one of two outcomes: a crawl entry fakes that shape, or every consumer of `list_runs()` branches on run kind. A dedicated list in the Crawl tab keeps each history list honest about what it summarizes.
- **Resume a crawl from a loaded historical screen map.** Rejected for this item. Resuming means the crawl engine ([`bajutsu/crawl.py`](../../bajutsu/crawl.py)) accepts an existing map as a starting point and picks exploration back up from its frontier. That is a change to the deterministic crawl engine itself, not just the report viewer. This proposal stays a read-only viewer. Resume is a distinct, larger idea.
- **One-click import of a crash/flow scenario into the target's `scenarios/` directory (and straight into the Author tab).** Rejected for this item, to keep the change to the viewer's own surface. A plain link to the existing static file already lets a viewer read and copy a scenario. A guided import is a natural follow-up once the history list exists, not a prerequisite for it.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `list_crawl_runs()` helper (scan by `screenmap.json`, summarize counts, detect
      `crashes/`/`flows/`)
- [x] `/api/crawl/runs` read endpoint
- [x] History list in the Crawl tab, wired to the existing `loadGraph()`
- [x] Read-only framing (disable live controls, label the selected run as past)
- [x] Crash/flow file links for the selected run

**Log**

- [#750](https://github.com/bajutsu-e2e/bajutsu/pull/750) — `list_crawl_runs()` scans a runs dir for `screenmap.json` and summarizes each crawl's
  screen/transition/crash counts plus the names of its `crashes/*.yaml` and `flows/*.yaml` files;
  exposed read-only at `/api/crawl/runs` (both the stdlib handler and the FastAPI app). The Crawl tab
  gained a Form/History sub-tab pair: selecting a past run reopens its screen map through the existing
  `loadGraph()` with a "past crawl" badge, disables the live form while it's shown, and links the
  run's crash/flow scenario files into the existing `/runs/<id>/...` static mount.

## References

- [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py) — `list_runs()`, the pattern
  this item mirrors for crawl runs.
- [`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py) — where
  `/api/runs` lives today, alongside the proposed `/api/crawl/runs`.
- [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) — `loadGraph`,
  `renderGraph`, `renderPlan`, and the Crawl tab's existing controls.
- [`bajutsu/crawl_repro.py`](../../bajutsu/crawl_repro.py) and
  [`bajutsu/crawl_flows.py`](../../bajutsu/crawl_flows.py) — the crash/flow scenario
  writers whose output this item links to.
- [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)
  — the crawl engine and screen map this item's viewer reopens, unchanged by this proposal.
- [BE-0095](../BE-0095-interactive-crawl-graph/BE-0095-interactive-crawl-graph.md) — the
  interactive graph rendering (drag/realign) this item reuses as-is for historical runs.

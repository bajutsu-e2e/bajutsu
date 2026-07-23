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

Add a **history list of past crawls** to the Web UI's Crawl tab. `bajutsu crawl` already
writes a live-streamed `runs/<id>/screenmap.json` plus, on completion, a self-contained
`runs/<id>/screenmap.html` ([BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)),
and the Crawl tab already renders that JSON as an interactive graph — zoomable, pannable,
draggable ([BE-0072](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md),
[BE-0095](../BE-0095-interactive-crawl-graph/BE-0095-interactive-crawl-graph.md)) — while a
crawl is running. What is missing is a way back to that graph once the crawl has finished
and the browser tab has moved on or closed: today the only path to a past crawl's graph is
opening `screenmap.html` from the run directory on disk. This item closes that gap by
listing past crawl runs in the Crawl tab and reopening the same interactive graph,
read-only, for any of them.

## Motivation

The Web UI already has a run history: the Replay tab's History list, backed by
`list_runs()` ([`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)). That list
reads each run directory's `manifest.json` and summarizes it as a pass/fail scenario
report. A `bajutsu crawl` run writes no `manifest.json` — its artifact is `screenmap.json`,
a screen-and-transition map with no notion of pass or fail — so crawl runs never appear in
that list. A viewer who wants to revisit a completed crawl has no in-UI path to it; they
have to know the run id and find `screenmap.html` on the filesystem, which defeats the
point of driving crawls from a browser in the first place.

The same gap affects the two artifacts a crawl produces alongside the map: one
`crashes/crash-NNN.yaml` per faithfully replayable crash
([`crawl_repro.py`](../../bajutsu/crawl_repro.py)) and one `flows/flow-NNN.yaml` per
faithfully reachable screen ([`crawl_flows.py`](../../bajutsu/crawl_flows.py)), both
directly runnable by `bajutsu run`. Neither is linked from anywhere in the Web UI today;
finding them means browsing the run directory by hand.

## Detailed design

**1. A crawl-specific run listing.** Add `list_crawl_runs(runs_dir)` next to `list_runs()`
in [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py). Unlike `list_runs()`, which
requires `manifest.json`, this scans for a `screenmap.json` in each run directory — the
one file every crawl run writes regardless of outcome — and is otherwise independent of
whether a `manifest.json` also happens to exist. For each match, read the JSON once and
summarize node/edge/crash counts (the same fields the Crawl tab already computes
client-side from the fetched map, in `renderGraph`), and record whether `crashes/` and
`flows/` hold any files. Sort newest-first by run id, mirroring `list_runs()`.

**2. A read endpoint.** Expose the listing as `/api/crawl/runs` (alongside the existing
`/api/runs`, in [`bajutsu/serve/operations/reads.py`](../../bajutsu/serve/operations/reads.py)).
No new artifact-serving path is needed for the crash/flow files themselves: `/runs/<id>/...`
already serves static run-directory content (it is how `screenmap.json` and screenshots
reach the browser today), so a `crashes/crash-001.yaml` link is just a path under that
existing mount.

**3. A history list in the Crawl tab.** Add a dedicated list to the Crawl view in
[`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) /
[`serve.html.j2`](../../bajutsu/templates/serve.html.j2), populated from
`/api/crawl/runs` — separate from the Replay tab's `#history`, since a crawl run's summary
(screens / transitions / crashes) has no pass/fail shape to share with a scenario report.
Selecting an entry calls the existing `loadGraph(runId)`, which already fetches
`/runs/<runId>/screenmap.json` and renders it with `renderGraph` + `renderPlan` — no change
to the rendering path itself.

**4. Read-only framing.** A selected historical run must not be confused with the live
crawl form. Disable (or hide) the Start/Stop controls, the target/simulator pickers, and
the max-screens/max-steps budget fields while a historical entry is selected, and clearly
label the graph as showing a past run (e.g. the run id and a "past crawl" badge in the
status line) rather than a live one. Selecting **Start crawl** again returns to the live
form and clears the historical selection, the same way today's `crawlDone` leaves the tab
ready for another run.

**5. Crash/flow links.** Next to the reopened graph, list the `crashes/*.yaml` and
`flows/*.yaml` file names for the selected run (from the counts already gathered in step 1),
each as a plain link into the existing `/runs/<id>/...` static mount — opening one shows the
raw scenario YAML. No new import path into a target's `scenarios/` directory or into the
Author tab is part of this item (see Alternatives).

## Alternatives considered

- **Fold crawl runs into the Replay tab's existing History list.** Rejected: `list_runs()`
  and its list item are shaped around a pass/fail scenario report (`ok`, `passed`/`total`,
  `scenarios`), and a crawl run has none of those. Reusing the same list either forces a
  crawl entry to fake that shape or forces every consumer of `list_runs()` to branch on run
  kind. A dedicated list in the Crawl tab keeps each history list honest about what it
  summarizes.
- **Resume a crawl from a loaded historical screen map.** Rejected for this item: resuming
  means the crawl engine ([`bajutsu/crawl.py`](../../bajutsu/crawl.py)) accepting an
  existing map as a starting point and picking exploration back up from its frontier,
  which is a change to the deterministic crawl engine itself, not just the report viewer.
  This proposal stays a read-only viewer; resume is a distinct, larger idea.
- **One-click import of a crash/flow scenario into the target's `scenarios/` directory
  (and straight into the Author tab).** Rejected for this item, to keep the change to the
  viewer's own surface: a plain link to the existing static file already lets a viewer read
  and copy a scenario. A guided import is a natural follow-up once the history list exists,
  not a prerequisite for it.

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

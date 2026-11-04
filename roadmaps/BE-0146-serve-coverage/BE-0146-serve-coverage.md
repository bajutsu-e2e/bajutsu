**English** · [日本語](BE-0146-serve-coverage-ja.md)

# BE-0146 — E2E coverage map in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0146](BE-0146-serve-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0146") |
| Implementing PR | [#702](https://github.com/bajutsu-e2e/bajutsu/pull/702), [#1784](https://github.com/bajutsu-e2e/bajutsu/pull/1784) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Surface the E2E coverage map ([BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md))
in the `serve` Web UI. The browser shows how much of an app's surface the scenario suite exercises:

- id namespaces covered versus declared
- the gap list
- off-namespace ids

With a run set, it also shows endpoints observed versus asserted, and observed ids versus declared
namespaces. It shows the screens a crawl discovered that those runs reached. The view is read-only,
AI-free, and never a gate.

## Motivation

[BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) ships `bajutsu coverage`. It is a
read-only, deterministic aggregation that answers "what do our E2E tests actually cover?" It reports:

- per-namespace id coverage, measured against the app's declared `idNamespaces`
- the gap list: declared namespaces no scenario touches
- off-namespace ids
- with `--runs`, endpoints observed versus asserted (`bajutsu/coverage.py`)

Teams routinely ask this question, and UI-only competitors cannot answer it. But the aggregation
lives on the CLI, while the place a team looks at its suite and its runs is the browser. The
Replay and History views already list runs and embed reports. Surfacing the coverage map there makes
"is this screen or namespace tested?" visible next to the runs it is derived from.

## Detailed design

Tier-1, read-only; the UI merely shells out to the existing aggregation.

- **A "Coverage" view**, posting to `POST /api/coverage` (`{target, runs?, crawl?}`). The endpoint
  runs the aggregation and returns per-namespace id coverage, the gap list, and off-namespace ids.
  When a run set is selected, it also returns the endpoints-observed-versus-asserted dimension (the
  union of `network.json` across those runs) and the observed-id dimension.
- **A linkable `GET /coverage` page**, carrying its inputs in the query string
  (`?target=&runs=&crawl=`), so a reader can open and link a coverage map directly. The three other
  analytics views (`/stats`, `/flakiness`, `/usage`) each have such a page. The coverage map needs a
  target, which is why its own page arrived later.
- **Read-only, deterministic, AI-free.** Every figure is a deterministic count over declared
  namespaces and captured artifacts. There is no model and no judgement call, and the view is never a
  gate. A team may still track the number in CI as informational; this UI leaves that unchanged.
- **Dimensions, in slices.** The id-namespace dimension is the first slice, because its denominator
  is fully defined and on disk. The endpoint and observed-id dimensions fold in when a run set is
  chosen. The screens-visited dimension folds in when a crawl
  ([BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md))
  supplies the discovered denominator its `screenmap.json` holds, the same input the CLI's `--crawl`
  takes.
- **A run belongs to the target it ran.** The run picker offers runs of the selected target's own
  scenarios, matched on the scenario names a run's summary records. The Author picker scopes on the
  same compatibility key
  ([BE-0262](../BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker.md)). A
  run of another target's scenarios carries evidence this map cannot place.

  The crawl picker is **not** scoped the same way, because a crawl records no target to scope on.
  `screenmap.json` and the history entry built from it carry screens, transitions, crashes, and the
  frontier, and nothing naming the app crawled. A crawl of another app measures as every screen
  unvisited. The picker says so in its hint rather than implying a scope it cannot enforce.
- **A placeholder is not an id.** The suite loader expands components (`params.*`) and data rows
  (`row.*`) before building the map. A `${vars.*}` or `${secrets.*}` token survives that expansion,
  because the run binds it later. The token names no element, so the map excludes it. Counting its
  text would invent a `${vars` namespace among the off-namespace ids.
- **An empty denominator is an empty state.** Each dimension's `coverage` is 1.0 when it has nothing
  to measure. A target declaring no namespaces would otherwise draw a full bar over "0/0" and claim
  complete coverage. Every dimension states that it has nothing to measure instead.
- **App-agnostic.** The denominator (`idNamespaces`), the runs, and the crawl all come from config
  and the run history, not hard-coded knowledge.

## Alternatives considered

* **Leave coverage CLI-only.** Rejected: coverage is a reporting view, and the browser is where the
  suite and its runs are already reviewed. A terminal table is the wrong home for a map meant to be
  scanned.
* **Compute coverage live in the browser from raw artifacts.** Rejected: the deterministic
  aggregation already exists server-side. Re-implementing it in JS would risk drift and duplicate
  the exact count.
* **Gate CI on a coverage threshold from the UI.** Out of scope and against the grain. Coverage is
  informational; a team may track it in CI itself, but the UI never turns it into a verdict.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add the `POST /api/coverage` endpoint (`{target, runs?}`) that runs the aggregation and
      returns per-namespace coverage, the gap list, and off-namespace ids
- [x] Add the "Coverage" view surfacing those results in the browser
- [x] Fold in the endpoints-observed-vs-asserted dimension when a run set is selected
- [x] Add the linkable `GET /coverage` page the other analytics views each have
- [x] Scope the run picker to the selected target's own scenarios
- [x] Fold in the screens-visited dimension from a selected crawl's `screenmap.json`
- [x] Exclude run-bound `${vars.*}` / `${secrets.*}` placeholders from the id map
- [x] Render a dimension with an empty denominator as an empty state, not a full bar

* [#702](https://github.com/bajutsu-e2e/bajutsu/pull/702) — Added `POST /api/coverage` (shared with the stdlib and FastAPI shells) reusing the
  BE-0050 aggregation and its self-contained HTML report, plus a "Coverage" tab that renders it. The
  device-free scenario loader moved to `bajutsu/scenario` and the run-evidence readers were shared
  into `bajutsu/analysis/coverage.py` (with a run-id filter) so the CLI and serve read the same way.
  Also folds in the observed-id dimension alongside endpoints for a selected run set.
* [#1784](https://github.com/bajutsu-e2e/bajutsu/pull/1784) — Finished the view against the three
  analytics dashboards it had fallen behind: added the linkable `GET /coverage` page, scoped the run
  picker to the selected target's own scenarios, and wired the screens-visited dimension to a crawl
  picker. Two figures were also wrong rather than missing — a `${vars.*}` placeholder counted as a
  referenced id, and a 0/0 dimension drew a full bar claiming complete coverage — so the aggregation
  now drops run-bound placeholders and every dimension renders an empty denominator as an empty state.
  The screen-map node walk and the visited-screen fingerprinting moved into
  `bajutsu/analysis/coverage.py`, so the CLI's `--crawl` and the serve view read one crawl the same
  way.

## References

* `bajutsu/analysis/coverage.py`, `bajutsu/cli/commands/coverage.py` — the aggregation this surfaces;
  `bajutsu/serve/operations/coverage.py` — the two serve entry points onto it.
* [BE-0050 — E2E coverage map](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md)
  — the feature this is the Web UI surface of;
  [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)
  — the crawl whose `screenmap.json` is the screens-visited denominator;
  [BE-0048 — Behavioral / protocol assertions](../BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md)
  — the "declared endpoints" half of the endpoint dimension.
* [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this extends and the small-screen layout it inherits.
* [evidence.md](../../docs/evidence.md), [configuration.md](../../docs/configuration.md) — the
  captured artifacts and declared namespaces the map aggregates; [CLAUDE.md](../../CLAUDE.md),
  [DESIGN §2](../../DESIGN.md) — every figure is a deterministic count, never a verdict.

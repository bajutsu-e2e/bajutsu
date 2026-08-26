**English** · [日本語](BE-0146-serve-coverage-ja.md)

# BE-0146 — E2E coverage map in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0146](BE-0146-serve-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0146") |
| Implementing PR | [#702](https://github.com/bajutsu-e2e/bajutsu/pull/702) |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Surface the E2E coverage map ([BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md))
in the `serve` Web UI: show, in the browser, how much of an app's surface the scenario suite
exercises — id namespaces covered vs declared, the gap list, off-namespace ids, and (with a run
set) endpoints observed vs asserted, observed ids vs declared namespaces, and the screens a crawl
discovered that those runs reached. Read-only, AI-free, never a gate.

## Motivation

[BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) ships
`bajutsu coverage`: a read-only, deterministic aggregation that answers "what do our E2E tests
actually cover?" — per-namespace id coverage measured against the app's declared `idNamespaces`, the gap
list (declared namespaces no scenario touches), off-namespace ids, and, with `--runs`, endpoints
observed vs asserted (`bajutsu/coverage.py`). It is a question teams routinely ask and UI-only
competitors cannot answer. But it lives on the CLI, while the place a team looks at its suite and
its runs is the browser — the Replay / History views already list runs and embed reports.
Surfacing the coverage map there turns "is this screen / namespace tested?" into something visible
next to the runs it is derived from.

## Detailed design

Tier-1, read-only; the UI only shells out to the existing aggregation.

- **A "Coverage" view**, posting to `POST /api/coverage` (`{target, runs?, crawl?}`). It runs the
  aggregation and returns per-namespace id coverage, the gap list, off-namespace ids, and — when a
  run set is selected — the endpoints-observed-vs-asserted dimension (the union of `network.json`
  across those runs) alongside the observed-id dimension.
- **A linkable `GET /coverage` page**, carrying its inputs in the query string
  (`?target=&runs=&crawl=`), so a reader can open and link a coverage map directly. The three other
  analytics views (`/stats`, `/flakiness`, `/usage`) each have such a page; the coverage map needs a
  target, which is why its own arrived later.
- **Read-only, deterministic, AI-free.** Every figure is a deterministic count over declared
  namespaces and captured artifacts; no model, no judgement call, never a gate (a team may still
  track the number in CI as informational — unchanged by this UI).
- **Dimensions, in slices.** The id-namespace dimension is the first slice (its denominator is fully
  defined and on disk); the endpoint and observed-id dimensions fold in when a run set is chosen; the
  screens-visited dimension folds in when a crawl
  ([BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md))
  supplies the discovered denominator its `screenmap.json` holds, the same input the CLI's `--crawl`
  takes.
- **A run belongs to the target it ran.** The run picker offers only runs of the selected target's
  own scenarios, matched on the scenario names a run's summary records — the same compatibility key
  the Author picker scopes on
  ([BE-0262](../BE-0262-serve-author-live-step-picker/BE-0262-serve-author-live-step-picker.md)). A run of
  another target's scenarios carries evidence this map cannot place.
- **A placeholder is not an id.** The suite loader expands components (`params.*`) and data rows
  (`row.*`) before building the map; the run binds a `${vars.*}` or `${secrets.*}` token surviving
  that expansion, so the token names no element and the map excludes it rather than counting its text
  and inventing a `${vars` namespace among the off-namespace ids.
- **An empty denominator is an empty state.** Each dimension's `coverage` is 1.0 when it has nothing
  to measure, so a target declaring no namespaces would otherwise draw a full bar over "0/0" and
  claim complete coverage. Every dimension states that it has nothing to measure instead.
- **App-agnostic.** The denominator (`idNamespaces`), the runs, and the crawl all come from config
  and the run history, not hard-coded knowledge.

## Alternatives considered

* **Leave coverage CLI-only.** Rejected: coverage is a reporting view, and the browser is where the
  suite and its runs are already reviewed; a terminal table is the wrong home for a map meant to be
  scanned.
* **Compute coverage live in the browser from raw artifacts.** Rejected: the deterministic
  aggregation already exists server-side; re-implementing it in JS would risk drift and duplicate
  the exact count.
* **Gate CI on a coverage threshold from the UI.** Out of scope and against the grain — coverage is
  informational; a team may track it in CI themselves, but the UI never turns it into a verdict.

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
* PR pending — Finished the view against the three
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

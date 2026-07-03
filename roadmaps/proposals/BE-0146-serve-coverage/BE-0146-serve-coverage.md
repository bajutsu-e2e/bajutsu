**English** · [日本語](BE-0146-serve-coverage-ja.md)

# BE-0146 — E2E coverage map in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0146](BE-0146-serve-coverage.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Surfacing CLI features in the serve Web UI |
<!-- /BE-METADATA -->

## Introduction

Surface the E2E coverage map ([BE-0050](../../implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md))
in the `serve` Web UI: show, in the browser, how much of an app's surface the scenario suite
exercises — id namespaces covered vs declared, the gap list, off-namespace ids, and (with a run
set) endpoints observed vs asserted. Read-only, AI-free, never a gate.

## Motivation

[BE-0050](../../implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md) ships
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

- **A "Coverage" view**, posting to `POST /api/coverage` (`{target, runs?}`). It runs the
  aggregation and returns per-namespace id coverage, the gap list, off-namespace ids, and — when a
  run set is selected — the endpoints-observed-vs-asserted dimension (the union of `network.json`
  across those runs).
- **Read-only, deterministic, AI-free.** Every figure is a deterministic count over declared
  namespaces and captured artifacts; no model, no judgement call, never a gate (a team may still
  track the number in CI as informational — unchanged by this UI).
- **Dimensions, in slices.** The id-namespace dimension is the first slice (its denominator is fully
  defined and on disk); the endpoint dimension folds in when a run set is chosen. The
  screens-visited dimension stays deferred behind a crawl-discovered denominator
  ([BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)),
  same as on the CLI.
- **App-agnostic.** The denominator (`idNamespaces`) and the runs come from config and the runs
  directory, not hard-coded knowledge.

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

- [ ] Add the `POST /api/coverage` endpoint (`{target, runs?}`) that runs the aggregation and
      returns per-namespace coverage, the gap list, and off-namespace ids
- [ ] Add the "Coverage" view surfacing those results in the browser
- [ ] Fold in the endpoints-observed-vs-asserted dimension when a run set is selected

No PR has landed yet.

## References

* `bajutsu/coverage.py`, `bajutsu/cli/commands/coverage.py` — the aggregation this surfaces.
* [BE-0050 — E2E coverage map](../../implemented/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md)
  — the feature this is the Web UI surface of;
  [BE-0038 — Autonomous crawl exploration](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)
  — the deferred screens-visited denominator;
  [BE-0048 — Behavioral / protocol assertions](../../implemented/BE-0048-behavioral-protocol-assertions/BE-0048-behavioral-protocol-assertions.md)
  — the "declared endpoints" half of the endpoint dimension.
* [BE-0011 — Local web UI (`bajutsu serve`)](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  [BE-0072 — Responsive serve Web UI](../../implemented/BE-0072-responsive-web-ui/BE-0072-responsive-web-ui.md)
  — the UI this extends and the small-screen layout it inherits.
* [evidence.md](../../../docs/evidence.md), [configuration.md](../../../docs/configuration.md) — the
  captured artifacts and declared namespaces the map aggregates; [CLAUDE.md](../../../CLAUDE.md),
  [DESIGN §2](../../../DESIGN.md) — every figure is a deterministic count, never a verdict.

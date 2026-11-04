**English** · [日本語](BE-0226-cross-project-metrics-dashboard-ja.md)

# BE-0226 — Cross-project metrics comparison dashboard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0226](BE-0226-cross-project-metrics-dashboard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0226") |
| Implementing PR | [#936](https://github.com/bajutsu-e2e/bajutsu/pull/936), [#940](https://github.com/bajutsu-e2e/bajutsu/pull/940), [#942](https://github.com/bajutsu-e2e/bajutsu/pull/942) |
| Topic | Authoring experience |
| Related | [BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md) |
| Superseded by | [BE-0404](../BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md) |
<!-- /BE-METADATA -->

## Introduction

The aggregate run-stats dashboard ([BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md))
answers "how is *this* config doing?" It aggregates the run history of the single active config.
The aggregates are pass-rate over time, run durations, the most-failing scenarios and steps, and a
flakiness class.

Once `serve` becomes a hub that registers several configs as projects (the sibling **config project
hub** proposal), a *comparative* question follows: "how do my projects compare against each other,
and which one needs attention?"

This proposal adds a **cross-project comparison view**. One dashboard ranks and charts the
registered projects side by side, showing pass-rate, flaky-rate, and run duration per project. A
team that maintains several configs can then see at a glance which project is regressing, which is
flaky, and which is slow. Nobody has to open each project's single-config dashboard in turn.

## Motivation

BE-0102 is deliberately single-config. Every metric it computes is scoped to the currently bound
config's run history, and no surface puts two configs next to each other. For a single app, that is
exactly right.

The whole point of the config project hub, however, is that a team holds *several* projects at once.
A per-project dashboard alone forces a serial, one-at-a-time reading. To find the worst-behaving
project, you would switch to each in turn, read its numbers, and hold the comparison in your head.

A comparison view removes that burden. The hub records runs with a `project_id`, because the config
project hub item wires the foreign key that BE-0015 left dangling. The run history then becomes
partitionable *by project*. The aggregations BE-0102 already computes can run once per project and be
laid out together.

The value is the ranking and the trend lines across projects. Two examples: "project *checkout*
dropped from 98% to 71% pass-rate this week", and "project *search* is the flakiest". No
single-config dashboard can show either.

This item is the analytics complement to
[BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md).
BE-0220 mines cross-run history to *rank and propose fixes for* individual flaky scenarios. It works
within a run history to find flaky scenarios. This item works *across projects* to compare their
aggregate health.

The two share the underlying run store, and they are complementary rather than overlapping. BE-0220
is scenario-level and prescriptive. This item is project-level and comparative.

This stays within the prime directives, exactly as BE-0102 does. It is a **read-only aggregation**
over stored run data. The verdicts it charts were already decided deterministically by `run`, and no
LLM enters the computation. It reuses each project's config through the hub, so it stays
app-agnostic: it compares whatever projects are registered and encodes nothing about a particular
app.

## Detailed design

The work is MECE across three units: the aggregation, the API, and the UI. It depends on the config
project hub having landed the per-project run history that this item reads.

### 1. Cross-project aggregation (reuse BE-0102's computation)

Reuse BE-0102's existing per-config aggregation rather than reimplementing it. For one config's run
history, BE-0102 computes these values:

- pass-rate over time
- run durations
- the most-failing scenarios and steps
- a flakiness class

Factor that computation so it takes a run set as input. Then run it **once per registered project**
over that project's `project_id`-scoped runs. Assemble the per-project results into a comparison
model. For each project, the model holds four values:

- its latest pass-rate
- its flaky-rate
- its median and 95th-percentile duration
- a short trend series

BE-0102's flakiness output is a per-scenario *classification*. Each scenario is labelled flaky or
not over the window. The comparison needs one scalar per project to rank on. The **flaky-rate** is
that roll-up: the share of the project's scenarios that BE-0102 classifies as flaky over the window
(flaky-classified scenarios ÷ total scenarios).

The flaky-rate is a plain count over BE-0102's existing labels. It adds no new flakiness heuristic,
which keeps "reuse BE-0102's computation" literal. It answers the ranking question ("which project
has the most flaky surface?") directly.

The aggregation is pure and deterministic. It sits in the Python core, out of any Simulator path, and
is testable on the Linux gate against fixture run data.

### 2. API

- `GET /api/metrics/projects` — the comparison model. It returns one row per registered project, with
  its headline metrics (pass-rate, flaky-rate, duration percentiles) and a trend series over a
  requested window. It is org-scoped and resolves to `default` locally, exactly as the hub's
  endpoints do.

This endpoint sits alongside BE-0102's single-config metrics endpoint and does not replace it. A
caller who wants one project's detail still uses the BE-0102 view.

### 3. UI

A **comparison dashboard** view in serve has two parts:

- A sortable table of projects. Sorting by pass-rate, flaky-rate, or duration surfaces the worst
  offender.
- Small multiples: one pass-rate trend sparkline per project over the window. Regressions and
  flakiness stand out across the whole set at a glance.

Selecting a project row deep-links into that project's existing BE-0102 single-config dashboard,
rebound via the hub's project switcher. The comparison view is the entry point, and BE-0102 is the
drill-down.

## Alternatives considered

- **Extend BE-0102 in place to take a project filter.** Rejected as the primary framing. BE-0102 is
  the *single-config* detail view and stays useful as-is, because it is the drill-down target here.
  The comparison is a distinct surface: a ranking and small-multiples layout across projects. It is
  not a filtered variant of one config's dashboard. It is therefore its own item that *reuses*
  BE-0102's computation rather than growing inside it.
- **Fold cross-project comparison into the config project hub item.** Rejected, to keep each item
  shippable on its own. The hub is the registry plus the per-project run plumbing, which is valuable
  without any comparison view. The comparison dashboard is the analytics layer on top. Separating
  them lets the hub land first, and this item then builds on the `project_id`-scoped history the hub
  produces.
- **Merge with [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md).**
  Rejected. BE-0220 is scenario-level and prescriptive (rank flaky scenarios, propose fixes), and its
  fix-proposal path is AI-assisted. This item is project-level and comparative, and it is a pure
  read-only aggregation with no AI. The two share the run store and are linked `Related`, but their
  outputs and audiences differ.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1 — Cross-project aggregation: factor BE-0102's per-config computation to take a run set, run it per project, roll its per-scenario flakiness classification up into a per-project flaky-rate scalar, assemble the comparison model.
- [x] 2 — API: `GET /api/metrics/projects`, org-scoped, alongside BE-0102's single-config endpoint.
- [x] 3 — UI: the sortable comparison table + per-project trend sparklines, deep-linking into BE-0102's single-config dashboard.

**Log**

- [#936](https://github.com/bajutsu-e2e/bajutsu/pull/936) — Unit 1. `stats.project_metrics` rolls
  `aggregate_runs` up into a per-project headline (pass-rate, flaky-rate as the share of
  flaky-classified scenarios, median/p95 per-run duration, daily pass-rate trend);
  `serve.operations.project_comparison.compare_projects` iterates the `ProjectRegistry` and reads
  each project's runs via the `run_set_manifests` seam. Pure, read-only, off the run/CI path.
- [#940](https://github.com/bajutsu-e2e/bajutsu/pull/940) — Unit 2. `project_metrics_view` exposes
  `compare_projects` as JSON at `GET /api/metrics/projects` over both transports (stdlib handler +
  FastAPI), org-scoped and returning an empty list when no hub is wired. A read `GET`, so no RBAC
  gate; window parity with BE-0102's fixed `_STATS_RUN_LIMIT`. Sits alongside the single-config
  `/stats`, not replacing it.
- [#942](https://github.com/bajutsu-e2e/bajutsu/pull/942) — Unit 3. A **Metrics** tab in serve renders the comparison model client-side: a
  sortable table (pass-rate, flaky-rate, p50/p95 duration) with a per-project pass-rate sparkline,
  reusing the `/stats` SVG-polyline trend shape. Clicking a row rebinds the project through the hub
  switcher (`switchProject(..., {goStats:true})`) and opens its BE-0102 single-config dashboard, so
  the comparison is the entry point and the per-project view is the drill-down. The tab tracks the
  switcher's `>1` visibility, so a single-config serve never shows it. This flips the item to
  Implemented.

## References

`bajutsu/serve/`, [reporting](../../docs/reporting.md), [architecture](../../docs/architecture.md);
[BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md) (the single-config
aggregation this reuses and drills down into),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the `projects` /
`runs.project_id` schema the per-project partitioning relies on),
[BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)
(the scenario-level flaky-mining complement), and the sibling **config project hub** proposal,
which records the `project_id`-scoped run history this dashboard aggregates.

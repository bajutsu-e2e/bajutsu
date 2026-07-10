**English** · [日本語](BE-XXXX-cross-project-metrics-dashboard-ja.md)

# BE-XXXX — Cross-project metrics comparison dashboard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-cross-project-metrics-dashboard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Authoring experience (record / GUI editor) |
| Related | [BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md) |
<!-- /BE-METADATA -->

## Introduction

The aggregate run-stats dashboard ([BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md))
answers "how is *this* config doing?" — pass-rate over time, run durations, the most-failing
scenarios and steps, and a flakiness class, all aggregated over the run history of the single
active config. Once `serve` becomes a hub that registers several configs as projects (the
sibling **config project hub** proposal), the natural next question is a *comparative* one:
"how do my projects compare against each other, and which one needs attention?"

This proposal adds a **cross-project comparison view**: one dashboard that ranks and charts the
registered projects side by side — pass-rate, flaky-rate, and run duration per project — so a
team maintaining several configs can see at a glance which project is regressing, which is
flaky, and which is slow, without opening each project's single-config dashboard in turn.

## Motivation

BE-0102 is deliberately single-config: every metric it computes is scoped to the currently
bound config's run history, and there is no surface that puts two configs next to each other.
For a single app that is exactly right. But the whole point of the config project hub is that a
team holds *several* projects at once, and a per-project dashboard alone forces a serial,
one-at-a-time reading: to find the worst-behaving project you would switch to each in turn,
read its numbers, and hold the comparison in your head.

A comparison view removes that. The moment the hub records runs with a `project_id` (the config
project hub item wires the foreign key BE-0015 left dangling), the run history becomes
partitionable *by project*, and the same aggregations BE-0102 already computes can be run once
per project and laid out together. The value is the ranking and the trend lines across
projects — "project *checkout* dropped from 98% to 71% pass-rate this week; project *search* is
the flakiest" — which no single-config dashboard can show.

This is the analytics complement to [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md),
which mines cross-run history to *rank and propose fixes for* individual flaky scenarios. BE-0220
works within a run history to find flaky scenarios; this item works *across projects* to compare
their aggregate health. They share the underlying run store and are complementary, not
overlapping — one is scenario-level and prescriptive, the other is project-level and comparative.

This stays within the prime directives, exactly as BE-0102 does. It is a **read-only aggregation**
over stored run data: the verdicts it charts were already decided deterministically by `run`; no
LLM enters the computation. It reuses each project's config through the hub, so it stays
app-agnostic — it compares whatever projects are registered and encodes nothing about a
particular app.

## Detailed design

The work is MECE across three units: the aggregation, the API, and the UI. It depends on the
config project hub having landed the per-project run history it reads.

### 1. Cross-project aggregation (reuse BE-0102's computation)

Reuse BE-0102's existing per-config aggregation rather than reimplementing it. BE-0102 computes,
for one config's run history, pass-rate over time, run durations, most-failing
scenarios/steps, and a flakiness class. Factor that computation so it takes a run set as input,
then run it **once per registered project** over that project's `project_id`-scoped runs, and
assemble the per-project results into a comparison model: for each project, its latest pass-rate,
its flaky-rate, its median/95th-percentile duration, and a short trend series.

BE-0102's flakiness output is a per-scenario *classification* (each scenario is labelled flaky or
not over the window), so the comparison needs one scalar per project to rank on. The **flaky-rate**
is that roll-up: the share of the project's scenarios BE-0102 classifies as flaky over the window
(flaky-classified scenarios ÷ total scenarios). This is a plain count over BE-0102's existing
labels — it adds no new flakiness heuristic, keeping "reuse BE-0102's computation" literal — and
answers the ranking question ("which project has the most flaky surface?") directly.

The aggregation is pure and deterministic, sits in the Python core (out of any Simulator path),
and is testable on the Linux gate against fixture run data.

### 2. API

- `GET /api/metrics/projects` — the comparison model: one row per registered project with its
  headline metrics (pass-rate, flaky-rate, duration percentiles) and a trend series over a
  requested window. Org-scoped, resolving to `default` locally, exactly as the hub's endpoints do.

This sits alongside — not replacing — BE-0102's single-config metrics endpoint; a caller who
wants one project's detail still uses the BE-0102 view.

### 3. UI

A **comparison dashboard** view in serve: a sortable table of projects (sort by pass-rate,
flaky-rate, or duration to surface the worst offender) plus small multiples — one trend
sparkline per project for pass-rate over the window — so regressions and flakiness stand out
across the whole set at a glance. Selecting a project row deep-links into that project's existing
BE-0102 single-config dashboard (rebound via the hub's project switcher), so the comparison view
is the entry point and BE-0102 is the drill-down.

## Alternatives considered

- **Extend BE-0102 in place to take a project filter.** Rejected as the primary framing: BE-0102
  is the *single-config* detail view and stays useful as-is (it is the drill-down target here).
  The comparison is a distinct surface — a ranking and small-multiples layout across projects —
  not a filtered variant of one config's dashboard, so it is its own item that *reuses* BE-0102's
  computation rather than growing inside it.
- **Fold cross-project comparison into the config project hub item.** Rejected to keep each item
  shippable on its own: the hub is the registry + per-project run plumbing (valuable without any
  comparison view), and the comparison dashboard is the analytics layer on top. Separating them
  lets the hub land first and this build on the `project_id`-scoped history it produces.
- **Merge with [BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md).**
  Rejected: BE-0220 is scenario-level and prescriptive (rank flaky scenarios, propose fixes) and
  its fix-proposal path is AI-assisted; this item is project-level, comparative, and a pure
  read-only aggregation with no AI. They share the run store and are linked `Related`, but their
  outputs and audiences differ.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1 — Cross-project aggregation: factor BE-0102's per-config computation to take a run set, run it per project, roll its per-scenario flakiness classification up into a per-project flaky-rate scalar, assemble the comparison model.
- [ ] 2 — API: `GET /api/metrics/projects`, org-scoped, alongside BE-0102's single-config endpoint.
- [ ] 3 — UI: the sortable comparison table + per-project trend sparklines, deep-linking into BE-0102's single-config dashboard.

## References

`bajutsu/serve/`, [reporting](../../docs/reporting.md), [architecture](../../docs/architecture.md);
[BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md) (the single-config
aggregation this reuses and drills down into),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the `projects` /
`runs.project_id` schema the per-project partitioning relies on),
[BE-0220](../BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)
(the scenario-level flaky-mining complement), and the sibling **config project hub** proposal,
which records the `project_id`-scoped run history this dashboard aggregates.

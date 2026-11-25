**English** · [日本語](BE-0195-ai-usage-cost-dashboard-ja.md)

# BE-0195 — Visualize AI token usage and cost in the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0195](BE-0195-ai-usage-cost-dashboard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0195") |
| Implementing PR | [#786](https://github.com/bajutsu-e2e/bajutsu/pull/786) |
| Topic | AI usage and cost observability |
<!-- /BE-METADATA -->

## Introduction

This item adds a read-only **AI usage dashboard** to the serve Web UI. The page reads the attributed
usage ledger (defined by the `ai-usage-cost-ledger` item) and shows where Bajutsu's AI tokens and
dollars go. It breaks usage down by provider, model, command, and scenario, and trends it over time.

The item is the visualization half of the *AI usage and cost observability* topic. The recording half
produces the data, and this item makes the data legible enough to act on.

## Motivation

The ledger is a growing JSONL file: durable and complete, but not something a team reads by hand. The
point of recording attributed usage is to *see* it. A team can then notice that one scenario dominates
the crawl budget, that a model is disproportionately expensive for the value it returns, or that
switching a command's provider would cut cost without hurting results. A raw log answers none of those
questions at a glance, and a dashboard does.

Bajutsu already has the pattern. BE-0102 shipped a read-only run-stats dashboard in serve (aggregate
pass/fail over test-run history), and BE-0169 added a `/metrics` observability endpoint. An AI-usage
dashboard applies the same shape to a new data source. It is also the one AI-usage surface that does
not exist at all: `bajutsu/usage.py` prints only to CLI stdout, and `serve.js` shows nothing about
tokens or cost.

The dashboard stays observability. It *reports* cost so a human can make a better provider or model
decision. It decides nothing at runtime, and it puts no LLM on the `run` / CI verdict path.

## Detailed design

The work comprises the following mutually exclusive, collectively exhaustive units:

1. **A read API for aggregated usage.** A serve endpoint (e.g. `GET /api/usage`) reads the ledger. It
   returns totals and aggregates grouped by the ledger's dimensions (provider, model, command,
   scenario) over a requested time range. Aggregation happens server-side, so the browser receives
   compact summaries instead of the raw event stream. The endpoint follows the existing serve API
   conventions used by the run-stats dashboard (BE-0102).

2. **A usage dashboard view.** A new view in `bajutsu/templates/serve.js` renders into a retained pane,
   consistent with the desktop tiler's layout rules. It shows:
   - headline totals (tokens and cost for the period);
   - a breakdown table or chart by provider and by model;
   - a by-command and by-scenario breakdown;
   - a time trend.

   A subscription provider with `cost = null` appears with its tokens and an explicit "no per-token
   price" marker, never a fabricated dollar figure.

3. **A provider/model comparison view.** This view serves the optimization goal directly. It compares
   cost-per-call and cost-per-scenario across providers and models, so a team can read off which
   provider and model is most efficient for a given kind of task. The view presents recorded data and
   nothing more. A human (or, later, deterministic config) makes the choice it informs, never the
   dashboard.

4. **Empty/absent-ledger handling.** When no ledger exists yet (AI paths never ran, or persistence is
   disabled), the view renders a clear empty state that explains how to enable usage recording, instead
   of raising an error. The doctor/readiness panels degrade the same way.

5. **Tests.** Unit tests cover the aggregation endpoint (grouping, time-range filtering, the
   `cost = null` path, the empty-ledger case) against a fixture ledger. They are pure and
   Simulator-free, so they run in the standard gate. The JS view is verified the way other `serve.js`
   views are.

## Alternatives considered

- **Reuse BE-0102's run-stats dashboard instead of a new view.** Rejected: run-stats aggregates test
  pass/fail history, a different data source that answers a different question. Folding AI cost into it
  would overload one page, whereas a sibling view under the same serve dashboard pattern is cleaner.
- **Expose usage only through BE-0169's `/metrics` and let users bring their own Grafana.** This is
  reasonable for teams already running Prometheus, and worth doing as a small follow-up. Rejected as
  the primary surface, because it requires external tooling. It also gives up the built-in, zero-setup
  "open serve and see your AI spend" experience that the run-stats dashboard set the precedent for. The
  two can coexist: `/metrics` for scraping, this dashboard for the human view.
- **A CLI subcommand (`bajutsu usage`) instead of a Web UI view.** This is cheaper, but the parent
  topic's motivation is a *visualization* surface, and serve is where Bajutsu already presents
  aggregate history. A CLI reporter could follow later over the same ledger and read API.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Read API for aggregated usage (the `/usage` HTML endpoint)
- [x] Usage dashboard view in `serve.js`
- [x] Provider/model comparison view
- [x] Empty/absent-ledger handling
- [x] Tests (aggregation endpoint + view)

**Log**

- [#786](https://github.com/bajutsu-e2e/bajutsu/pull/786) — Add the AI usage/cost dashboard: `usage_stats.aggregate_usage` + `usage.html.j2`
  render the ledger server-side; a `/usage` endpoint (both serve backends) and a new **Usage** tab
  in `serve.js` display it, mirroring the run-stats dashboard (BE-0102). Following the existing
  dashboard convention, `/usage` returns a self-contained HTML page rather than the `GET /api/usage`
  JSON the proposal sketched.

## References

- `ai-usage-cost-ledger` — the sibling item that records the attributed usage/cost ledger this
  dashboard reads. This item depends on that format.
- [BE-0102](../BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md) (Run-stats dashboard) — the read-only serve dashboard pattern this follows.
- [BE-0169](../BE-0169-serve-metrics-observability/BE-0169-serve-metrics-observability.md) (Serve metrics and observability endpoint) — the complementary `/metrics` surface.
- `bajutsu/templates/serve.js` — the serve Web UI where the view is added.

**English** · [日本語](BE-0102-run-stats-dashboard-ja.md)

# BE-0102 — Aggregate run-stats dashboard

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0102](BE-0102-run-stats-dashboard.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0102") |
| Implementing PR | [#652](https://github.com/bajutsu-e2e/bajutsu/pull/652), [#654](https://github.com/bajutsu-e2e/bajutsu/pull/654) |
| Topic | Authoring experience |
<!-- /BE-METADATA -->

## Introduction

A read-only dashboard that aggregates **many** runs into one view: pass-rate over time, run and
per-scenario durations, the scenarios and steps that fail most often, and (folded in from
[BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md))
each scenario's flakiness classification. Today every run is reported richly on its own
(`report.html`), but nothing renders the trend *across* runs. The dashboard is a deterministic
aggregation over data the runner already writes — `manifest.json` per run, plus the serve run
records — with no LLM and no effect on any verdict, in the same family as the coverage map
([BE-0050](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md)) and the
flakiness audit (BE-0049).

## Motivation

A single run answers "did *this* run pass, and where did it fail." A team running E2E continuously
has the next question — "how is the suite trending?" — and Bajutsu has no answer surface for it.
The raw material already exists and is lossless:

- Every run writes `manifest.json` ([reporting.md](../../docs/reporting.md)) with the top-level
  verdict, each scenario's `ok`/`failure`, each step's `duration_s`, and (BE-0049) a `provenance`
  block stamping `scenarioHash` / `toolVersion` / `gitRevision`. So a directory of `runs/`
  *already* encodes pass-rate, duration, and failure history — it has just never been aggregated.
- The serve backend records each finished run into its `runs` table (`status`, `ok`, `created_at`,
  `summary` JSONB) under BE-0015 7c-4, so a hosted instance accumulates the same history in a query
  -friendly store.

What is missing is the layer that turns that pile into a picture: a pass-rate trend line, a ranking
of the slowest and the flakiest scenarios, and a list of the steps/assertions that fail most. This
is the operational complement to the two analytical reports Bajutsu already ships — coverage
(BE-0050) answers *"what surface do we test?"* and the audit (BE-0049) answers *"is a given scenario
reproducible?"*; this item answers *"how is the whole suite doing over time?"* It is also the
natural companion to the per-run `report.html` and to the serve run list, which today shows
individual runs but no aggregate.

**Why it does not strain the prime directives.** Like coverage and audit, every figure is a
deterministic count or aggregation over already-captured artifacts — there is no model and no
judgement call, the dashboard never changes a verdict, and it is never part of the CI gate (a team
may *track* a number from it as informational, exactly as BE-0050 allows). It is app-agnostic: it
reads `manifest.json` / run records, with no per-app hard-coding. The grouping key is the BE-0049
`scenarioHash`, so the trend it draws separates *true* flakiness (verdict flips while the content
hash is unchanged) from a scenario that was simply edited — it consumes that provenance rather than
re-deriving it.

## Detailed design

Proposal altitude; the binding constraint is that the dashboard is purely observational. The work
is MECE across five units:

- **1. The aggregator (deterministic core).** A pure function `aggregate_runs(runs) -> Stats` that
  walks a set of run manifests and computes the metric set below. It groups scenarios by
  `(scenarioHash, name)` — the BE-0049 identity — so the same scenario's history is one series even
  as unrelated scenarios come and go, and a content edit starts a new series rather than corrupting
  the old one. It reuses `report.load` (`results_from_manifest`) to read manifests losslessly, and
  reuses the BE-0049 flakiness classifier rather than re-implementing one. No device, no network —
  testable on the Linux gate with `FakeDriver`-produced manifests.
- **2. The metric set (what is computed).** A MECE set over the manifest fields that already exist:
  - **Pass-rate over time** — runs and per-scenario pass/fail bucketed by run (and by day), with
    optional grouping by `backend` / target / tag.
  - **Duration / performance** — distribution of total run duration and per-scenario duration
    (from `steps[].duration_s`), a ranking of the slowest scenarios, and each scenario's duration
    trend so a regression is visible.
  - **Failure hotspots** — the scenarios, steps, and assertions that fail most often, ranked by
    frequency, with the recurring `failure` reasons surfaced.
  - **Flakiness** — each scenario's BE-0049 classification (`flaky` / `deterministic` / `unproven`)
    folded in, so the "is it reproducible" axis sits beside the "how often does it pass" axis.
  - **Volume** — run count over time, broken down by backend / target, as the denominator the rates
    are read against.
- **3. The CLI surface (primary).** `bajutsu stats`, mirroring `bajutsu coverage`
  (BE-0050): `--runs <dir>` selects the run set, `--html` emits a **self-contained** HTML dashboard
  (inline styles; the chart rendering kept minimal and dependency-free, in the BE-0050 spirit), and
  the default text/JSON output makes the same numbers scriptable and CI-publishable. This is the
  load-bearing slice — it runs entirely on the Linux gate, needs no serve and no database, and a
  team can publish the HTML from CI exactly as it would a coverage report.
- **4. The serve surface (follow-on).** A **Stats** tab in `bajutsu serve` that calls the *same*
  aggregator over the run history and renders it live, so the hosted/local UI gains the trend view
  next to its existing per-run report list. It reads through the existing serve seams — the DB
  `Repository` run records when a database is wired (BE-0015 7c-4), the `ArtifactStore` `manifest.json`
  files otherwise — so it follows the established *"DB if present, else the artifact store"* fallback
  and needs no new persistence. (Local serve and the stdlib path keep working with no database.)
- **5. Scope guards (what it is *not*).** It never re-runs an assertion or recomputes a verdict — it
  only aggregates recorded outcomes (an older manifest missing a newer field renders as "not
  captured", per the BE-0068 re-render discipline). It introduces no LLM. It is not a CI gate; any
  threshold a team sets on its numbers is their own informational check, outside Bajutsu's verdict.

**Open scope choices (flagged for review, not yet fixed).** These were the questions a scoping pass
would settle; the recommendation is in brackets and the alternatives are real:

- *Surface ordering* — ship the CLI `stats --html` first and add the serve tab as a follow-on slice
  [recommended: yes, CLI-core then serve], versus building only one of the two.
- *Data source* — primary on a local `runs/` directory of manifests, with the serve DB as the
  longitudinal source where present [recommended: both, DB-if-present-else-runs/], versus committing
  to a single source.
- *Charting* — keep it dependency-free and minimal in the BE-0050 coverage style (inline SVG / CSS,
  no JS chart library) [recommended], versus pulling a charting dependency for richer interactivity.

## Alternatives considered

- **Extend the per-run `report.html` instead of a new surface.** Rejected: the per-run report is
  deliberately *one run* (BE-0068 makes it a pure re-render of one run dir). Cross-run aggregation is
  a different altitude with a different input (a *set* of runs) and belongs in its own command/tab,
  the way coverage (BE-0050) and audit (BE-0049) are their own surfaces.
- **Fold it into `bajutsu audit`.** Rejected as the home, kept as a dependency: audit (BE-0049)
  *judges reproducibility* of a scenario (repeat-and-diff + a flaky/deterministic classification).
  This item *visualizes operational trend* (pass-rate, duration, failure hotspots, volume) across
  the suite. They share the `scenarioHash` grouping and this dashboard *consumes* the audit's
  classification, but the deliverables are distinct — merging them would overload one command.
- **Have an LLM summarize the trend.** Rejected: non-deterministic and unnecessary — every figure is
  an exact count/aggregation over the manifests, and an LLM in a reporting path that teams might
  glance at before trusting CI is exactly the coupling the prime directives keep out.
- **A committed, generated dashboard page (like the roadmap dashboard, BE-0094).** Rejected as the
  model: that page derives from in-repo metadata, but run history is runtime data that lives outside
  the repo, so the dashboard must be generated *from a run set on demand* (CLI / serve), not baked
  into the docs site.
- **Do nothing (status quo).** Acceptable, but the suite's trend stays invisible despite the data
  already being on disk; a team has to script `manifest.json` parsing by hand to answer "how are we
  trending," which is the gap this closes cheaply given the artifacts already exist.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1. The aggregator (`aggregate_runs`) over a run set, grouping by the BE-0049 `scenarioHash`,
  reusing the BE-0049 flakiness classifier (`audit.longitudinal`) and reading manifests directly (the
  aggregator needs only a handful of fields, so it consumes the manifest mappings like the audit does
  rather than reconstructing full `RunResult`s through `report.load`).
- [x] 2. The metric set — pass-rate over time, duration / performance, failure hotspots, flakiness,
  volume.
- [x] 3. The CLI surface — `bajutsu stats` with `--runs` / `--html` (self-contained) and text/JSON
  output.
- [x] 4. The serve **Stats** tab reusing the aggregator through the existing serve seams: the run-id
  list comes from the system of record when a database is wired (org-scoped), else the artifact store,
  and each run's full `manifest.json` is read from the artifact store either way (the DB `summary`
  holds only the compact history-list shape).
- [x] 5. Scope guards — read-only, no verdict change, no LLM, not a CI gate.

**Log**

- [#652](https://github.com/bajutsu-e2e/bajutsu/pull/652) — Shipped the CLI-first slice: the deterministic aggregator `bajutsu/stats.py`
  (`aggregate_runs` + text/JSON/HTML renderers, flakiness reused from the BE-0049 longitudinal audit)
  and the `bajutsu stats --runs/--json/--html` command, with `tests/test_stats.py` /
  `tests/test_cli_stats.py` on the Linux gate and bilingual `cli.md` docs. The serve Stats tab
  (unit 4) is deferred to a follow-on.
- [#654](https://github.com/bajutsu-e2e/bajutsu/pull/654) — Added the serve **Stats** tab (unit 4): a new `stats_html` serve operation reusing
  the aggregator over the org's run history through the existing seams (DB-else-artifact for the id
  list, ArtifactStore for the manifests), served at `GET /stats` on both the stdlib and FastAPI
  backends and rendered in the SPA's new Stats tab. Covered by `tests/serve/test_stats_tab.py`.

## References

[`docs/reporting.md`](../../docs/reporting.md) (`manifest.json` / `report.load` /
`results_from_manifest`), `bajutsu/report/`, `bajutsu/serve/helpers.py` (`list_runs`),
[BE-0049 — Determinism / flakiness audit](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
(the `scenarioHash` provenance and flakiness classification this dashboard consumes),
[BE-0050 — E2E coverage map](../BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md)
(the sibling self-contained HTML report this mirrors),
[BE-0068 — Regenerable reports](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md)
(the "re-present recorded outcomes, never re-run" discipline),
[BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
(7c-4 — the DB-backed run records the serve tab reads, with the artifact-store fallback),
[DESIGN §2 / §10](../../DESIGN.md)

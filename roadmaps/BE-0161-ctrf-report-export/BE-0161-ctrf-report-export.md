**English** · [日本語](BE-0161-ctrf-report-export-ja.md)

# BE-0161 — Export run results in Common Test Report Format (CTRF)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0161](BE-0161-ctrf-report-export.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0161") |
| Implementing PR | [#653](https://github.com/bajutsu-e2e/bajutsu/pull/653) |
| Topic | Integration with external services |
<!-- /BE-METADATA -->

## Introduction

This item emits a run's results as a [Common Test Report Format (CTRF)](https://ctrf.io/) document.
The file is `ctrf.json`, written into the run directory alongside the existing `manifest.json`,
`junit.xml`, and `report.html`.

CTRF is an open-standard JSON schema for test reports (`reportFormat: "CTRF"`, and a `results`
object holding `tool` / `summary` / `tests`). Any framework can produce the same shape. Consumers
across a growing ecosystem read it without per-tool adapters: GitHub Actions PR-comment reporters,
dashboards, and flaky-test analytics.

The exporter is a pure, deterministic projection of the run data Bajutsu already computes. It takes
the same input as the existing `junit_xml()` and adds a new output format beside it. It involves no
LLM and has no effect on the verdict.

## Motivation

Bajutsu already writes `junit.xml` for CI integration (BE-0003), and that remains the lingua franca
of CI test-result ingestion. JUnit XML is a lowest-common-denominator format, though. It carries a
test's name, class, time, and a failure blob, and little else.

Bajutsu knows much more about a run:

- per-step outcomes
- the backend/engine and device the scenario ran on
- screenshots, video, and network logs as first-class artifacts
- visual-diff evidence
- cross-browser matrix cells
- scenario provenance

JUnit has no home for any of this, so it is flattened into free text or dropped.

CTRF exists to carry that richer, structured shape, and a growing ecosystem consumes it:

1. **PR-comment and summary reporters.** The `ctrf-io` GitHub Actions (`github-test-reporter` and
   friends) turn a `ctrf.json` into a rich PR comment and job summary. The output includes a failure
   table with messages, flaky-test call-outs, and historical trends. It comes from *one* standard
   file, whichever tool produced it. Today a Bajutsu user who wants that must hand-write a
   JUnit-to-comment step.
2. **Cross-tool dashboards and analytics.** CTRF normalizes across frameworks. A team that runs
   Bajutsu for E2E next to unit tests in another framework can feed both into one CTRF-based
   dashboard. Bajutsu's structured extras (device, engine, steps, attachments) survive the trip.
3. **A structured artifact that keeps Bajutsu's detail.** CTRF's `tests[].steps`, `attachments`,
   `browser`/`device`, and `extra` extension points let the exported file preserve nearly everything
   in `manifest.json`. JUnit loses that detail.

The reframing that keeps this cheap and safe is that Bajutsu adopts no new internal model and
changes nothing a run computes. `manifest.json` is already the canonical, versioned run model
([BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md)). Investigation shows that
it is a **superset** of CTRF. Every required CTRF field has a direct source. Bajutsu's surplus maps
onto CTRF's first-class optional fields (`steps`, `attachments`, `browser`, `device`,
`environment`) or its `extra` escape hatches.

The exporter projects existing data and sits next to `junit_xml()`. It adds no
new bookkeeping. It serializes results that are already decided after the verdict, so by
construction it sits outside the determinism-first contract.

## Detailed design

### Field mapping (manifest → CTRF)

The exporter reads the same in-memory result model the reporter already builds. That model is
`RunResult` / `StepOutcome` / `AssertionResult` / `Artifact`, defined in
`bajutsu/orchestrator/types.py`, `bajutsu/assertions.py`, and `bajutsu/evidence.py`. From it the
exporter produces a CTRF document:

| CTRF field | Source | Notes |
|---|---|---|
| `reportFormat` / `specVersion` | constants | `"CTRF"` and the targeted spec version |
| `generatedBy` / `timestamp` | `"bajutsu"` / now | document metadata |
| `results.tool.{name,version}` | `"bajutsu"` / `provenance.toolVersion` | |
| `results.summary.tests` | count of `scenarios` (matrix: cells) | |
| `results.summary.{passed,failed}` | `ok` tally | |
| `results.summary.{skipped,pending,other}` | `0` | Bajutsu has no such states today |
| `results.summary.duration` | Σ `duration_s × 1000` | milliseconds |
| `results.summary.{start,stop}` | run start + duration | see the timestamp note below |
| `tests[].name` | `RunResult.scenario` | matrix cell: `scenario` + engine suffix |
| `tests[].status` | `ok` → `passed` / `failed` | the only two states Bajutsu emits |
| `tests[].duration` | `duration_s × 1000` | milliseconds |
| `tests[].message` / `trace` | `RunResult.failure` / step + assertion reasons | |
| `tests[].steps[]` | `StepOutcome` → `{name: action, status}` | rich fields → `step.extra` (see below) |
| `tests[].browser` / `device` | `engine` / `device_name` + `device_runtime` | |
| `tests[].attachments[]` | `Artifact` (`name`/`kind`/`provider`) | `contentType` from a `kind → MIME` map |
| `results.environment.{commit,osPlatform,…}` | `provenance.gitRevision`, host info | optional block |
| surplus (matrix cells, `expect_results`, `expect_alerts`, `skipped_captures`, `sid`, visual diff) | `extra` | CTRF is "fully extendable" |

Investigation confirms that every *required* CTRF field has a direct source: `reportFormat`,
`specVersion`, `results.{tool,summary,tests}`, and each test's `name`/`status`/`duration`. The
mapping loses no information.

### Two shape mismatches to reconcile (neither is a blocker)

1. **Absolute timestamps.** CTRF wants `start`/`stop` as **milliseconds since the Unix epoch**, per
   run and per test. Bajutsu stores `runId` (a `YYYYmmdd-HHMMSS` wall-clock string), relative
   `duration_s`, and each `StepOutcome.started_at` offset. It stores no absolute per-scenario start.
   The exporter derives `summary.start` from `runId` and per-test times from cumulative offsets.
   This is exact for a serial run and approximate for parallel or matrix execution.

   A small follow-up can record an absolute epoch start on the run and per scenario, for full
   fidelity. This proposal treats that follow-up as an *optional* enhancement that bumps
   `manifest.json`'s schema version, not as a prerequisite. `duration` is exact either way, and CTRF
   and most consumers key on it.
2. **CTRF steps are minimal.** A CTRF `step` allows `{name, status, extra}` alone, with no duration
   or reason at the top level. Bajutsu's `StepOutcome` is richer: duration, reason, per-step
   assertion results, and artifacts. Those go into `step.extra`, so nothing is lost. Consumers that
   render name and status see a clean list, and Bajutsu-aware tooling can read the extras.

`Artifact.kind` (`video` / `screenshot` / `deviceLog` / `elements` / `network` / …) maps to a MIME
`contentType` (`video/mp4`, `image/png`, `text/plain`, `application/json`, …) via a small table.
Unknown kinds get a safe `application/octet-stream` default. Attachment `path` stays run-directory
relative, matching how `manifest.json` records artifacts.

### Where it hooks in

CTRF generation lives beside the existing JUnit path. A `ctrf_json()` builder sits in
`bajutsu/report/`, next to `junit_xml()` and `manifest_dict()` in `bajutsu/report/manifest.py`. The
`run` pipeline calls it from the report-assembly point that already writes `manifest.json` / `junit.xml` /
`report.html`: `_assemble_report()` in `bajutsu/runner/pipeline.py`, via `write_report()` in
`bajutsu/report/html.py`.

BE-0068 made reports regenerable from stored run data. `bajutsu report <run>` re-emits
`ctrf.json` for past runs, the same way it re-emits HTML and JUnit. The exporter reads the persisted
model and never reads live device state.

Matrix runs ([BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md), if
present) emit one CTRF `test` per cell. The engine appears in the test name and in `browser`,
mirroring how `junit_xml()` already encodes the engine in the JUnit `classname`.

### Determinism and the prime directives

- **No LLM, ever.** CTRF is a mechanical serialization of `manifest.json`. Nothing here touches the
  Tier-2 gate or the verdict.
- **Post-verdict, side-effect-free.** `run` writes the file after it has decided pass/fail and built
  the result model. Writing the file cannot move the verdict or the exit code.
- **App-agnostic.** The exporter is driver- and target-independent. It reads the shared result
  model, so iOS (idb), web (Playwright), and future backends export identically, with no per-app
  branching.
- **Redaction inherited.** CTRF is projected from the already-scrubbed manifest
  ([BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) /
  [BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md)), so no
  raw secret reaches the exported file.

### Work breakdown (MECE)

1. A `kind → MIME` mapping for artifacts, with an `octet-stream` default.
2. A `ctrf_json()` builder projecting the result model into a CTRF document (summary, tests, steps,
   attachments, environment, and `extra` for surplus). It covers both single-engine and matrix runs.
3. Wiring the builder into report assembly so `run` writes `ctrf.json`, and into `bajutsu report` so
   past runs regenerate it.
4. Tests: a serial run and a matrix run produce a `ctrf.json` that validates against the CTRF schema
   and round-trips the mapped fields (status counts, durations, step lists, attachment MIME types).
5. Docs (bilingual): document `ctrf.json` as a run artifact, with a short "consume it in CI" note.
6. *(Optional follow-up)* record an absolute epoch start on the run and scenario, to make
   `start`/`stop` exact under parallel execution. This bumps the `manifest.json` schema version.

## Alternatives considered

- **Do nothing — JUnit XML is enough.** JUnit covers the pass/fail gate but strips the structured
  detail (steps, device/engine, artifacts, visual diffs) that is Bajutsu's distinguishing output.
  The CTRF consumer ecosystem (PR comments, dashboards) also cannot read `manifest.json`. CTRF is the
  bridge between "our rich model" and "standard consumers".
- **Convert `manifest.json` → CTRF with an external CLI tool at CI time.** This pushes a bespoke,
  drift-prone transform into every user's CI config. A first-class `ctrf.json` beside `junit.xml`
  makes the integration a one-line consumer step. The transform belongs in the tool that owns the
  model.
- **Replace `manifest.json` with CTRF as the canonical model.** Rejected. `manifest.json` is
  Bajutsu-native and versioned (BE-0068). It carries Bajutsu-specific structure with no CTRF home
  (matrix cells, skipped captures, visual evidence), and it drives `report.html`. CTRF is an *export
  target*, not the internal contract, which is the relationship JUnit already has.
- **Emit CTRF and drop JUnit.** Rejected. JUnit remains the widest-supported CI format, and CTRF is
  additive, for the consumers JUnit cannot feed.
- **Block on exact absolute timestamps first.** Not required. `duration` is already exact, and CTRF
  and its consumers key on it. `start`/`stop` derive acceptably from `runId`. The absolute-epoch
  refinement is an optional follow-up, not a gate on shipping the exporter.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `kind → MIME` mapping for artifacts.
- [x] `ctrf_json()` builder (summary / tests / steps / attachments / environment / `extra`; serial + matrix).
- [x] Wire into report assembly (`run`) and `bajutsu report` regeneration.
- [x] Tests: schema-valid `ctrf.json` for a serial and a matrix run; field round-trip.
- [x] Bilingual docs: `ctrf.json` as a run artifact + a "consume it in CI" note.
- [ ] *(Optional)* absolute epoch start on run/scenario for exact `start`/`stop`; `manifest.json` schema bump.

**Log**

- 2026-07-04 — Shipped the exporter:
  - A `kind → MIME` map and a `ctrf_json()` projection in `bajutsu/report/ctrf.py`.
  - Wiring into `write_html_and_junit`, so both `run` and `bajutsu report` emit `ctrf.json`.
    Provenance is threaded through `RenderModel` for the regeneration path.
  - `ctrf.json` added to the secret-value scrub.
  - Tests that validate a serial and a matrix run against the vendored official CTRF schema and
    round-trip the mapped fields.
  - Bilingual docs.

  The optional absolute-epoch refinement is deferred.

## References

- [CTRF — Common Test Report Format](https://ctrf.io/) and its [JSON schema](https://github.com/ctrf-io/ctrf) — the standard this item targets.
- [BE-0068 — Regenerable reports](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) — `manifest.json` as the canonical, versioned run model the exporter projects from, and the `bajutsu report` regeneration path CTRF reuses.
- [BE-0060 — Download / export a run report as a zip](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) — the run-directory artifact bundle `ctrf.json` joins.
- [BE-0099 — Webhook notifications for run results](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) — the sibling post-verdict, format-neutral projection of `manifest.json`; the same "project, don't recompute" pattern.
- [BE-0003 — M3: codegen, traces, network, CI](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) — where JUnit XML for CI landed; CTRF is the richer companion format.
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) — the redaction the exported file inherits from the manifest.

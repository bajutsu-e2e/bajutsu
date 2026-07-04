**English** · [日本語](BE-0161-ctrf-report-export-ja.md)

# BE-0161 — Export run results in Common Test Report Format (CTRF)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0161](BE-0161-ctrf-report-export.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0161") |
| Implementing PR | _pending_ |
| Topic | Integration with external services |
<!-- /BE-METADATA -->

## Introduction

Emit a run's results as a [Common Test Report Format (CTRF)](https://ctrf.io/) document —
`ctrf.json`, written into the run directory alongside the existing `manifest.json`, `junit.xml`,
and `report.html`. CTRF is an open-standard JSON schema for test reports (`reportFormat: "CTRF"`,
a `results` object holding `tool` / `summary` / `tests`), designed so that any framework can produce
the same shape and consumers across a growing ecosystem — GitHub Actions PR-comment reporters,
dashboards, flaky-test analytics — read it without per-tool adapters. The exporter is a pure,
deterministic projection of the run data Bajutsu already computes: same input as the existing
`junit_xml()`, a new output format beside it. No LLM, no effect on the verdict.

## Motivation

Bajutsu already writes `junit.xml` for CI integration (BE-0003), and that remains the lingua franca
of CI test-result ingestion. But JUnit XML is a lowest-common-denominator format: it carries a
test's name, class, time, and a failure blob, and little else. Everything richer that Bajutsu knows
about a run — per-step outcomes, the backend/engine and device the scenario ran on, screenshots and
video and network logs as first-class artifacts, visual-diff evidence, cross-browser matrix cells,
scenario provenance — has no home in JUnit and is flattened into free text or dropped.

CTRF exists to carry exactly that richer, structured shape, and a growing ecosystem consumes it:

1. **PR-comment and summary reporters.** The `ctrf-io` GitHub Actions (`github-test-reporter` and
   friends) turn a `ctrf.json` into a rich PR comment / job summary — a failure table with messages,
   flaky-test call-outs, historical trends — from *one* standard file, regardless of which tool
   produced it. Today a Bajutsu user wanting that has to hand-write a JUnit-to-comment step.
2. **Cross-tool dashboards and analytics.** Because CTRF normalizes across frameworks, a team running
   Bajutsu for E2E next to unit tests in another framework can feed both into one CTRF-based
   dashboard. Bajutsu's structured extras (device, engine, steps, attachments) survive the trip.
3. **A structured artifact that keeps Bajutsu's detail.** CTRF's `tests[].steps`, `attachments`,
   `browser`/`device`, and the `extra` extension points let the exported file preserve nearly
   everything in `manifest.json` — unlike JUnit, where it is lost.

The reframing that keeps this cheap and safe: Bajutsu does not adopt a new internal model or change
what a run computes. `manifest.json` is already the canonical, versioned run model
([BE-0068](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md)), and investigation shows
it is a **superset** of CTRF — every required CTRF field has a direct source, and Bajutsu's surplus
maps onto CTRF's first-class optional fields (`steps`, `attachments`, `browser`, `device`,
`environment`) or its `extra` escape hatches. So the exporter is a projection of existing data, added
next to `junit_xml()`, not new bookkeeping. Being a post-verdict serialization of already-decided
results, it sits entirely outside the determinism-first contract by construction.

## Detailed design

### Field mapping (manifest → CTRF)

The exporter reads the same in-memory result model the reporter already builds (`RunResult` /
`StepOutcome` / `AssertionResult` / `Artifact` in `bajutsu/orchestrator/types.py`,
`bajutsu/assertions.py`, `bajutsu/evidence.py`) and produces a CTRF document:

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

Investigation confirms every *required* CTRF field — `reportFormat`, `specVersion`,
`results.{tool,summary,tests}`, and each test's `name`/`status`/`duration` — has a direct source, so
the mapping loses no information.

### Two shape mismatches to reconcile (neither is a blocker)

1. **Absolute timestamps.** CTRF wants `start`/`stop` as **milliseconds since the Unix epoch**, per
   run and per test. Bajutsu currently stores `runId` (a `YYYYmmdd-HHMMSS` wall-clock string) plus
   relative `duration_s` and each `StepOutcome.started_at` offset — but no absolute per-scenario
   start. The exporter derives `summary.start` from `runId` and per-test times from cumulative
   offsets, which is exact for a serial run and approximate for parallel/matrix execution. For full
   fidelity, a small follow-up can record an absolute epoch start on the run and per scenario; this
   proposal treats that as an *optional* enhancement bumping `manifest.json`'s schema version, not a
   prerequisite. `duration` (the field CTRF and most consumers key on) is exact either way.
2. **CTRF steps are minimal.** A CTRF `step` allows only `{name, status, extra}` — no duration or
   reason at the top level. Bajutsu's `StepOutcome` is richer (duration, reason, per-step assertion
   results, artifacts). Those go into `step.extra`, so nothing is lost; consumers that only render
   name/status see a clean list, and Bajutsu-aware tooling can read the extras.

`Artifact.kind` (`video` / `screenshot` / `deviceLog` / `elements` / `network` / …) maps to a MIME
`contentType` (`video/mp4`, `image/png`, `text/plain`, `application/json`, …) via a small table, with
a safe `application/octet-stream` default for unknown kinds. Attachment `path` stays run-directory
relative, matching how `manifest.json` records artifacts.

### Where it hooks in

CTRF generation lives beside the existing JUnit path: a `ctrf_json()` builder in `bajutsu/report/`
(next to `junit_xml()` and `manifest_dict()` in `bajutsu/report/manifest.py`), called from the same
report-assembly point that already writes `manifest.json` / `junit.xml` / `report.html`
(`_assemble_report()` in `bajutsu/runner/pipeline.py`, via `write_report()` in
`bajutsu/report/html.py`). Because BE-0068 made reports regenerable from stored run data,
`bajutsu report <run>` re-emits `ctrf.json` for past runs the same way it re-emits HTML/JUnit — the
exporter reads only the persisted model, never live device state.

Matrix runs ([BE-0076](../BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md), if present)
emit one CTRF `test` per cell, with the engine in the test name and in `browser`, mirroring how
`junit_xml()` already encodes the engine in the JUnit `classname`.

### Determinism and the prime directives

- **No LLM, ever.** CTRF is a mechanical serialization of `manifest.json`; nothing here touches the
  Tier-2 gate or the verdict.
- **Post-verdict, side-effect-free.** The file is written after `run` has decided pass/fail and built
  the result model — writing it cannot move the verdict or the exit code.
- **App-agnostic.** The exporter is driver- and target-independent; it reads the shared result model,
  so iOS (idb), web (Playwright), and future backends export identically with no per-app branching.
- **Redaction inherited.** CTRF is projected from the already-scrubbed manifest
  ([BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) /
  [BE-0153](../BE-0153-encode-aware-secret-redaction/BE-0153-encode-aware-secret-redaction.md)), so no raw
  secret reaches the exported file.

### Work breakdown (MECE)

1. A `kind → MIME` mapping for artifacts, with an `octet-stream` default.
2. A `ctrf_json()` builder projecting the result model into a CTRF document (summary, tests, steps,
   attachments, environment, `extra` for surplus), covering both single-engine and matrix runs.
3. Wiring the builder into report assembly so `run` writes `ctrf.json`, and into `bajutsu report` so
   past runs regenerate it.
4. Tests: a serial run and a matrix run produce a `ctrf.json` that validates against the CTRF schema
   and round-trips the mapped fields (status counts, durations, step lists, attachment MIME types).
5. Docs (bilingual): document `ctrf.json` as a run artifact and a short "consume it in CI" note.
6. *(Optional follow-up)* record an absolute epoch start on the run/scenario to make `start`/`stop`
   exact under parallel execution; a `manifest.json` schema-version bump.

## Alternatives considered

- **Do nothing — JUnit XML is enough.** JUnit covers the pass/fail gate but strips the structured
  detail (steps, device/engine, artifacts, visual diffs) that is Bajutsu's distinguishing output, and
  the CTRF consumer ecosystem (PR comments, dashboards) can't read `manifest.json`. CTRF is the
  bridge between "our rich model" and "standard consumers".
- **Convert `manifest.json` → CTRF with an external CLI tool at CI time.** Pushes a bespoke,
  drift-prone transform into every user's CI config; a first-class `ctrf.json` beside `junit.xml`
  makes the integration a one-line consumer step. The transform belongs in the tool that owns the
  model.
- **Replace `manifest.json` with CTRF as the canonical model.** Rejected. `manifest.json` is
  Bajutsu-native and versioned (BE-0068), carries Bajutsu-specific structure with no CTRF home
  (matrix cells, skipped captures, visual evidence), and drives `report.html`. CTRF is an *export
  target*, not the internal contract — the same relationship JUnit already has.
- **Emit CTRF only, drop JUnit.** No — JUnit remains the widest-supported CI format; CTRF is
  additive, for the consumers JUnit can't feed.
- **Block on exact absolute timestamps first.** Not required: `duration` (what CTRF and its consumers
  key on) is already exact, and `start`/`stop` derive acceptably from `runId`. The absolute-epoch
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

- 2026-07-04 — Shipped the exporter: a `kind → MIME` map and a `ctrf_json()` projection in
  `bajutsu/report/ctrf.py`, wired into `write_html_and_junit` so both `run` and `bajutsu report`
  emit `ctrf.json` (provenance threaded through `RenderModel` for the regeneration path), with
  `ctrf.json` added to the secret-value scrub. Tests validate a serial and a matrix run against the
  vendored official CTRF schema and round-trip the mapped fields; bilingual docs added. The optional
  absolute-epoch refinement is deferred.

## References

- [CTRF — Common Test Report Format](https://ctrf.io/) and its [JSON schema](https://github.com/ctrf-io/ctrf) — the standard this item targets.
- [BE-0068 — Regenerable reports](../BE-0068-regenerable-reports/BE-0068-regenerable-reports.md) — `manifest.json` as the canonical, versioned run model the exporter projects from, and the `bajutsu report` regeneration path CTRF reuses.
- [BE-0060 — Download / export a run report as a zip](../BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md) — the run-directory artifact bundle `ctrf.json` joins.
- [BE-0099 — Webhook notifications for run results](../BE-0099-webhook-run-notifications/BE-0099-webhook-run-notifications.md) — the sibling post-verdict, format-neutral projection of `manifest.json`; the same "project, don't recompute" pattern.
- [BE-0003 — M3: codegen, traces, network, CI](../BE-0003-m3-codegen-traces-network-ci/BE-0003-m3-codegen-traces-network-ci.md) — where JUnit XML for CI landed; CTRF is the richer companion format.
- [BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md) — the redaction the exported file inherits from the manifest.

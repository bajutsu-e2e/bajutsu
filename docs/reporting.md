**English** · [日本語](ja/reporting.md)

# Reporting (manifest.json / JUnit / CTRF / HTML)

> One run executes one or more scenarios (`list[RunResult]`). Bajutsu writes their results in four
> formats. `manifest.json` is the **single source of truth** for the report and for CI (continuous integration).
>
> Implementation: `bajutsu/report/` (a package, split by stage: `format` → `manifest` / `richtext` → `rows` / `panels` → `html`).

Related: [the run results in run-loop](run-loop.md#run-results-data-structures) · [evidence](evidence.md)

---

## Output layout

```
runs/<runId>/
├── manifest.json     # the step → outcome correlation (single source of truth)
├── junit.xml         # CI integration (1 scenario = 1 testcase)
├── ctrf.json         # Common Test Report Format (richer CI consumers: PR comments, dashboards)
├── report.html       # self-contained HTML (no external assets)
└── <stepId>/         # per-step evidence (when using FileSink)
    ├── after.png     # screenshot
    ├── elements.json # query() dump
    ├── segment.mp4   # video (interval)
    └── device.log    # deviceLog (interval)
```

The CLI assigns `runId` as `YYYYMMDD-HHMMSS` (`cli/commands/run.py`). `stepId` is `step.name` or `step<i>`.

## manifest.json

`RunResult` and its parts are all dataclasses, so `asdict()` drops the step / expect results
verbatim.

```json
{
  "runId": "20260605-101530",
  "ok": true,
  "backend": "xcuitest",
  "scenarios": [
    {
      "scenario": "onboard, log in, and increment the counter",
      "ok": true,
      "backend": "xcuitest",
      "steps": [
        {
          "index": 5, "action": "tap", "ok": true, "reason": "",
          "duration_s": 0.12,
          "assertion_results": [],
          "artifacts": [{ "name": "after.png", "kind": "screenshot", "provider": "driver" }]
        }
      ],
      "expect_results": [
        { "ok": true, "kind": "value", "detail": "value equals='2': id='counter.value'", "reason": "" }
      ],
      "failure": null
    }
  ]
}
```

- `ok` (top): true if every scenario is ok.
- `backend`: the actuator that drove the run (`xcuitest`, or `fake` in tests). One actuator is fixed
  per run, so the top-level value is normally a single name; each scenario also carries its own
  `backend` ([drivers](drivers.md#backend-selection-and-the-actuator)).
- `steps[].duration_s`: each step's timing (the `actionLog`-equivalent information).
- `steps[].artifacts`: the provenance of evidence captured for that step
  ([evidence](evidence.md#artifact-provenance-provider)).
- `failure`: a summary on failure (e.g. `"step 3 (tap): no match: {...}"`). null on success.
- `provenance` (top, optional): a run-identity stamp ([BE-0049](../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md))
  — `scenarioHash` (a `sha256:` fingerprint of the executed `scenario.yaml`), `toolVersion`
  (`bajutsu.__version__`), `gitRevision` (the commit, present only when the run is inside a git
  checkout), and — when the config came from a Git source ([BE-0063](../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source.md)) —
  `configSource` (`{ host, owner, repo, ref, sha }`, the exact commit a branch-based run executed).
  It groups accumulated runs by identity, so a verdict that flips while the fingerprint is
  unchanged is **true flakiness** rather than an edited scenario. Pure metadata — it never enters
  `ok`. (`schemaVersion` is `3` or higher once this block can appear — it is `4` today.)
- `idb` (top, optional, legacy): older manifests may carry an `idb_companion` / client version
  block (BE-0005). It was retired with the idb backend (BE-0290) and is no longer written; an old
  manifest that still has it loads fine, since an unknown top-level key is ignored.
- `matrix` (top, optional): the cross-browser engine × scenario grid, present only on a
  `bajutsu run --browsers` run ([BE-0076](../roadmaps/BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)).
  `scenarios` stays the flat result list, each entry tagged with its `engine`; `matrix` is
  `{ engines, scenarios, cells: { "<scenario>": { "<engine>": { ok, sid, failure } } } }` — a pure
  aggregation of those per-engine verdicts (the report renders it as a grid). `ok` is all-must-pass
  across every engine × scenario. Omitted for a single-engine / iOS run. (`schemaVersion` is `4`
  once this block can appear.)

## junit.xml

For CI integration. **one scenario = one `<testcase>`.** A failing scenario gets a `<failure>`, whose
`text` lists each step / expect's ok/FAIL and reason. On a `--browsers` matrix run the engine is
keyed into the case (`classname="bajutsu.<engine>"`), so CI sees `chromium.login` and `webkit.login`
as distinct cases (BE-0076); a single-engine run keeps `classname="bajutsu"`.

```xml
<testsuite name="bajutsu" tests="2" failures="1">
  <testcase name="..." classname="bajutsu"/>
  <testcase name="..." classname="bajutsu">
    <failure message="step 1 (tap): ...">step 0 tap: ok
step 1 tap: FAIL no match: {...}</failure>
  </testcase>
</testsuite>
```

## ctrf.json

The [Common Test Report Format (CTRF)](https://ctrf.io/) export ([BE-0161](../roadmaps/BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md)):
an open-standard JSON test report that a growing ecosystem reads without per-tool adapters — the
`ctrf-io` GitHub Actions (PR-comment / job-summary reporters), cross-tool dashboards, and
flaky-test analytics. Where JUnit XML strips a run down to name / time / a failure blob, CTRF carries
Bajutsu's structured detail (per-step outcomes, the engine and device, artifacts as first-class
attachments) that JUnit has no place for. It is a **pure projection of `manifest.json`** — the same
data, a new shape beside `junit.xml` — so it adds no bookkeeping, and, written after the verdict,
it cannot change that verdict (no LLM, no effect on pass/fail).

The document is `{ reportFormat: "CTRF", specVersion, generatedBy, timestamp, results }`, where
`results` holds `tool` / `summary` / `tests` (+ optional `environment` / `extra`):

```json
{
  "reportFormat": "CTRF",
  "specVersion": "0.0.0",
  "generatedBy": "bajutsu",
  "results": {
    "tool": { "name": "bajutsu", "version": "…" },
    "summary": { "tests": 2, "passed": 1, "failed": 1, "skipped": 0, "pending": 0, "other": 0,
                 "start": 1717581300000, "stop": 1717581302300, "duration": 2300 },
    "tests": [
      { "name": "login", "status": "passed", "duration": 1500,
        "steps": [{ "name": "tap", "status": "passed", "extra": { "duration": 500 } }],
        "browser": "chromium", "device": "iPhone 15 (iOS 17.2)",
        "attachments": [{ "name": "00-login/scenario.mp4", "contentType": "video/mp4", "path": "00-login/scenario.mp4" }] }
    ]
  }
}
```

- `summary.duration` and each `tests[].duration` are milliseconds (Σ / per-scenario `duration_s`),
  the field CTRF consumers key on, and exact. `summary.start` and the document `timestamp` derive
  from the `YYYYMMDD-HHMMSS` runId, parsed as UTC (the runId is stamped in UTC); `stop = start +
  duration`. Absolute per-test start/stop are deferred (they need an absolute per-scenario epoch, an
  optional follow-up) so they are omitted rather than approximated. The export carries no live host
  state, so `bajutsu report` regenerates the same run's `ctrf.json` byte-for-byte.
- `tests[].status` is `passed` / `failed` — the only two states a Bajutsu run emits; the other CTRF
  counts stay `0`.
- A CTRF `step` allows only `{ name, status, extra }`, so a step's richer data (duration, reason,
  per-step assertions, artifacts) lands in `step.extra` — a consumer that renders just name/status
  sees a clean list, and Bajutsu-aware tooling can read the extras.
- Attachment `contentType` comes from an artifact-`kind` → MIME map (`video`→`video/mp4`,
  `screenshot`→`image/png`, `deviceLog`→`text/plain`, `elements`/`network`/`appTrace`→`application/json`),
  defaulting to `application/octet-stream`; `path` stays run-directory relative like the manifest.
- On a `--browsers` matrix run each engine × scenario cell is one CTRF test — the engine in the test
  `name` and the `browser` field (mirroring JUnit's `classname`) — and the engine × scenario grid is
  carried under `results.extra.matrix`. Bajutsu's other surplus (`sid`, `expect` results, alerts,
  `skipped_captures`) lives under the per-test `extra`.
- Since CTRF is projected from the already-redacted manifest, it inherits the same secret scrubbing
  ([BE-0047](../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)); no raw secret reaches it.

### Consuming ctrf.json in CI

`ctrf.json` sits beside `junit.xml`, so wiring it into a CI job is a single consumer step. For
example, the `ctrf-io/github-test-reporter` action turns it into a PR comment / job summary:

```yaml
- uses: ctrf-io/github-test-reporter@v1
  with:
    report-path: runs/*/ctrf.json
  if: always()
```

## report.html

A self-contained HTML for humans (inline CSS, no external assets). The header shows the run id and
overall PASS/FAIL, the **scenario file name** under the run id (`source_name`), and the **file-level
`description`** when present. Each scenario row's summary shows the **scenario name** and, when set,
the **scenario-level `description`** beside it, so a run surfaces scenario name + file name +
descriptions throughout. Bajutsu merges the scenario definition and its execution into one **Steps tab**. It has labelled sections (preconditions / **steps** /
**expectations**), each a table. The **steps** table: `#` / `result` (a PASS/FAIL pill in its own
column) / `action` (a colored badge) / `detail` (the target description) / `at` / `view` (screenshot +
an **in-report element-tree viewer**: the captured elements open in an in-page overlay, no new tab) /
`reason`. In the detail, identifiers (`#home.title`) and literal constants (`“text”`,
numbers) are rendered as subtly-styled inline tokens — visually distinct from the solid
action/assert badges, so variables and constants are distinguishable at a glance. An `assert` step's
checks become a **nested table**, one row per assertion split into `kind` / `target` / `comparison`
cells (instead of a hard-to-read `a; b; c` line). Steps that never ran (execution stops at the first
failure) still appear, marked as skipped. **Observed network exchanges are interleaved into the
steps** in time order (each placed by its offset from the scenario start): a row with the HTTP method
as a neutral badge, the status in the `result` column, and the exchange's settings (method / endpoint
/ status / duration / headers) as a **nested table** in the detail cell. The scenario's `network.filter.domains` (by URL host) filters which requests appear; the Network tab still lists them all.
The **preconditions** table is collapsible (key / value).
The **expectations** table uses parallel columns `result` / `kind` (badge) / `target` (the checked
selector, e.g. `#counter.value`) / `comparison` (e.g. `== “2”`) / `reason`, with the same id/constant
tokens. A **Rich / YAML toggle** switches the same tab between this structured view and the raw
scenario YAML.

A `visual` expectation renders an **interactive baseline-vs-actual comparator** beneath its row,
with four modes: **Swipe** (drag a divider to wipe between the two), **Onion** (a slider cross-fades
actual over baseline), **Blend** (`mix-blend-mode: difference` — identical pixels go black, changed pixels are highlighted),
and **Diff** (the machine's precomputed pixel diff, with the assertion's `exclude`
regions masked — present only when the check failed). A `diff <pct>%` badge accompanies it, or a
`no baseline yet` badge on a first run (when only the actual screenshot exists). When the check did
not pass, an **Approve as baseline** button promotes the captured screenshot into the baselines dir;
it `POST`s `/api/approve` and so works only when the report is opened through `bajutsu serve` (it is
hidden for a report opened from disk). The CLI twin is [`bajutsu approve`](cli.md#approve).

Failing rows have a red background. Clicking a step seeks the recording to that step **without
auto-playing** (a paused video stays paused; a playing one keeps playing). Clicking a step's
screenshot opens a full-size lightbox; **← / →** (or the on-screen arrows) then walk through every
screenshot in the run, across scenario boundaries, with a caption showing the scenario, step, and
position. The run's actuator backend is shown as a `driver: <backend>` chip in the header and a small
badge on each scenario row.
Device Log / App Trace remain separate tabs.

## Write API

```python
def write_report(run_dir, run_id, results, definitions=None, sources=None, source_name=None, description=None, provenance=None) -> Path  # all 4 formats; definitions = per-scenario dict, sources = raw YAML, source_name = scenario file name, description = file-level description; provenance = run-identity stamp (BE-0049)
def write_html_and_junit(run_dir, run_id, results, definitions=None, sources=None, source_name=None, description=None, provenance=None) -> None  # the regenerable half (report.html + junit.xml + ctrf.json), leaving manifest.json untouched — used by re-render; provenance feeds the CTRF tool/environment fields
def manifest_dict(run_id, results, *, source_name=None, provenance=None) -> dict  # the versioned render model (schemaVersion); the manifest source (for tests / inspection)
def run_provenance(scenario_yaml, *, git_revision, config_source=None) -> dict  # the run-identity stamp: scenarioHash + toolVersion + optional gitRevision (BE-0049) + optional configSource (BE-0063)
def ctrf_json(run_id, results, *, provenance=None) -> dict  # the CTRF projection of the result model (BE-0161); provenance feeds tool.version / environment.commit
def junit_xml(results) -> str
def html_report(run_id, results, run_dir=None, definitions=None, sources=None, source_name=None, description=None) -> str
def scenario_render_inputs(scenarios) -> tuple[list[dict], list[str]]  # (definitions, sources); shared by the bake and the re-render
```

`runner.run_and_report` calls this `write_report` and returns `(results, manifest_path)` to the CLI
([run-loop](run-loop.md#runner-the-run-pipeline)). The CLI exits 0 if every scenario passes, 1 on
failure.

## Regenerating a report (BE-0068)

The report is a **pure rendering of data stored in the run dir**, so a finished run can be
re-rendered offline with the current template — without re-executing it. `manifest.json` is the
**versioned** (`schemaVersion`), lossless render model; `report.load` is its inverse —
`results_from_manifest()` reconstructs the `RunResult`s, and `load_run(run_dir)` recovers the whole
render model (outcomes from `manifest.json`, the scenario plan from `scenario.yaml`). `bajutsu
report <run>` ([cli](cli.md#report)) rewrites `report.html` + `junit.xml` + `ctrf.json` from it. Re-rendering only
re-presents recorded outcomes — it never re-runs an assertion or changes a verdict — and an older
run renders with any newer-only section shown as "not captured" rather than invented.

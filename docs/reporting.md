**English** · [日本語](ja/reporting.md)

# Reporting (manifest.json / JUnit / HTML)

> One run executes one or more scenarios (`list[RunResult]`). Their results are written in three
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
├── report.html       # self-contained HTML (no external assets)
└── <stepId>/         # per-step evidence (when using FileSink)
    ├── after.png     # screenshot
    ├── elements.json # query() dump
    ├── segment.mp4   # video (interval)
    └── device.log    # deviceLog (interval)
```

The CLI assigns `runId` as `YYYYMMDD-HHMMSS` (`cli.py`). `stepId` is `step.name` or `step<i>`.

## manifest.json

`RunResult` and its parts are all dataclasses, so `asdict()` drops the step / expect results
verbatim.

```json
{
  "runId": "20260605-101530",
  "ok": true,
  "backend": "idb",
  "scenarios": [
    {
      "scenario": "onboard, log in, and increment the counter",
      "ok": true,
      "backend": "idb",
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
- `backend`: the actuator that drove the run (`idb`, or `fake` in tests). One actuator is fixed
  per run, so the top-level value is normally a single name; each scenario also carries its own
  `backend` ([drivers](drivers.md#backend-selection-and-the-actuator)).
- `steps[].duration_s`: each step's timing (the `actionLog`-equivalent information).
- `steps[].artifacts`: the provenance of evidence captured for that step
  ([evidence](evidence.md#artifact-provenance-provider)).
- `failure`: a summary on failure (e.g. `"step 3 (tap): no match: {...}"`). null on success.

## junit.xml

For CI integration. **1 scenario = 1 `<testcase>`.** A failing scenario gets a `<failure>`, whose
`text` lists each step / expect's ok/FAIL and reason.

```xml
<testsuite name="bajutsu" tests="2" failures="1">
  <testcase name="..." classname="bajutsu"/>
  <testcase name="..." classname="bajutsu">
    <failure message="step 1 (tap): ...">step 0 tap: ok
step 1 tap: FAIL no match: {...}</failure>
  </testcase>
</testsuite>
```

## report.html

A self-contained HTML for humans (inline CSS, no external assets). The header shows the run id and
overall PASS/FAIL, the **scenario file name** under the run id (`source_name`), and the **file-level
`description`** when present. Each scenario row's summary shows the **scenario name** and, when set,
the **scenario-level `description`** beside it, so a run surfaces scenario name + file name +
descriptions throughout. The scenario definition and its execution are **merged into one Steps tab**. It has labelled sections (preconditions / **steps** /
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
/ status / duration / headers) as a **nested table** in the detail cell. Which requests appear is
filtered by the scenario's `network.filter.domains` (by URL host); the Network tab still lists them all.
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
def write_report(run_dir, run_id, results, definitions=None, sources=None, source_name=None, description=None) -> Path  # all 3 formats; definitions = per-scenario dict, sources = raw YAML, source_name = scenario file name, description = file-level description
def manifest_dict(run_id, results) -> dict            # the manifest source (for tests / inspection)
def junit_xml(results) -> str
def html_report(run_id, results, run_dir=None, definitions=None, sources=None, source_name=None, description=None) -> str
```

`runner.run_and_report` calls this `write_report` and returns `(results, manifest_path)` to the CLI
([run-loop](run-loop.md#runner-the-run-pipeline)). The CLI exits 0 if every scenario passes, 1 on
failure.

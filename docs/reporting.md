**English** · [日本語](ja/reporting.md)

# Reporting (manifest.json / JUnit / CTRF / HTML)

> One run executes one or more scenarios (`list[RunResult]`). Bajutsu writes their results in four
> formats. `manifest.json` is the **single source of truth** for the report and for continuous
> integration (CI).
>
> Implementation: `bajutsu/common/report/`, a package split by stage:
> `format` → `manifest` / `richtext` → `rows` / `panels` → `html`.

Related: [the run results in run-loop](run-loop.md#run-results-data-structures) · [evidence](evidence.md)

---

## Output layout

```
runs/<runId>/
├── manifest.json     # the step → outcome correlation (single source of truth)
├── junit.xml         # CI integration (1 scenario = 1 testcase)
├── ctrf.json         # Common Test Report Format (richer CI consumers: PR comments, dashboards)
├── report.html       # self-contained HTML (no external assets)
└── <sid>/            # one scenario's evidence (when using FileSink)
    ├── result.json   # this scenario's verdict, written the moment it finishes
    └── <stepId>/     # per-step evidence
        ├── before.png    # screenshot, before the step acts
        ├── after.png     # screenshot, after it acts
        ├── elements.json # query() dump
        ├── segment.mp4   # video (interval)
        └── device.log    # deviceLog (interval)
```

The runner writes `manifest.json`, `junit.xml`, `ctrf.json`, and `report.html` only after every
scenario has finished. A run killed partway through a suite (a crashed host, a CI job timeout)
would otherwise leave no verdict at all. Each `<sid>/result.json` closes that gap: the runner writes
the file as soon as that scenario ends, before the next scenario starts.

The file holds the manifest's `schemaVersion` and, under `scenario`, the same entry `manifest.json`
lists for that scenario under `scenarios`. The runner masks secret values exactly as it does for
the manifest. In a
cross-browser matrix run the file sits under `<engine>/<sid>/`: the parent directory names the
engine, the entry's `engine` is empty, and artifact paths are relative to `<engine>/`.
`manifest.json` remains the single source of truth for a run that completes.

When a scenario's `targets` field
([BE-0428](../roadmaps/BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md))
names two or more entries, each declared target's evidence, the primary's included, nests under a
folder named after the target. The per-step evidence above goes under `<sid>/<target>/<stepId>/…`,
and that target's scenario-wide recordings and `network.json` go directly under
`<sid>/<target>/`. A scenario declaring zero targets or exactly one keeps the flat layout above
unchanged.

The CLI assigns `runId` as `YYYYMMDD-HHMMSS`. `bajutsu/common/run_meta/id.py`
([BE-0200](../roadmaps/BE-0200-run-id-contract/BE-0200-run-id-contract.md)) mints it once, so every
call site shares one format. `sid` is `{NN}-{slug}`: a zero-padded run-order index plus the stem of
the scenario's own source file (`login_flow.yaml` → `login_flow`), or a slug of the scenario's
`name:` field when no source file is known — a scenario built directly rather than loaded from disk
([BE-0417](../roadmaps/BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming.md)).
`stepId` is `step.name` or `step<i>`.

Bajutsu caps both of those slugs at 60 characters
([BE-0420](../roadmaps/BE-0420-scenario-slug-length-cap/BE-0420-scenario-slug-length-cap.md)). A
source-file stem keeps its Unicode letters, so the cap counts characters rather than bytes.
American Standard Code for Information Interchange (ASCII) text takes one byte per character, and
Japanese text can take more. Counting characters keeps the two truncating to the same length.

A scenario whose file name or `name:` field runs past the budget still runs to completion. Its
evidence directory merely keeps the recognizable lead of the name. `Scenario.name` stays untouched,
so `manifest.json` and `report.html` carry the full name. Truncation can make two long names produce
the same slug. In `runs/<runId>/<sid>/`, the run-order index in front of the slug still tells them
apart.

## manifest.json

`RunResult` and its parts are all dataclasses, so `manifest_dict` drops the step / expect results
verbatim, minus `wall_offset_s`. That field is an in-run-only conversion constant with no meaning
once persisted (see below).

Every other timestamp is an absolute wall-clock instant (epoch seconds). It keeps its meaning after
the run that produced it exits
(see [evidence](evidence.md#interval-evidence-video--devicelog--apptrace)).

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
          "artifacts": [
            { "name": "before.png", "kind": "screenshot", "provider": "driver",
              "depicts": "xcuitest:before" },
            { "name": "after.png", "kind": "screenshot", "provider": "driver",
              "depicts": "xcuitest:after" }
          ]
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
  per run, so the top-level value is normally a single name. Each scenario also carries its own
  `backend` ([drivers](drivers.md#backend-selection-and-the-actuator)).
- `steps[].duration_s`: each step's timing. A run measures every elapsed time on the monotonic
  clock. A wall clock can jump backward mid-run, for example after a Network Time Protocol (NTP)
  correction or a manual clock change. Such a jump would corrupt a duration or a timeout. A run
  reads the wall clock once per scenario and uses it for the recorded timestamps alone.
- `steps[].started_at`: the absolute wall-clock instant (epoch seconds) the step began, with no
  video correction folded in. A viewer subtracts `video_anchor_s` (below) at render time to get the
  seconds to seek the recording to. [report.html](#reporthtml) shows that number as the **steps**
  table's `at` column.

  Storing the raw instant, rather than the already-corrected offset, lets a viewer recompute the
  offset from a saved run. A fix to how a run resolves its anchor is one example. Nothing requires a
  re-run of the scenario
  ([BE-0348](../roadmaps/BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md)).
- `video_anchor_s` (per scenario): the absolute wall-clock instant the scenario's video started.
  Where the finished recording's own duration is a wall-clock measure, the anchor derives from
  that duration. Otherwise it comes from the recording's confirmed start signal
  ([evidence](evidence.md#interval-evidence-video--devicelog--apptrace)). Every viewer subtracts it.

  A run recorded before `schemaVersion` 6 carries no anchor. Its `started_at` values are already
  video-relative, so a viewer reads the missing anchor as `0.0` and renders them unchanged.
- `wall_offset_s` (in-run only, not persisted): what the run adds to one of its own monotonic
  instants to reach a wall-clock epoch. The run uses it to carry a network exchange's monotonic
  receive time onto the same anchor `video_anchor_s` uses. It never survives into the manifest, and
  no monotonic instant does either, so a later reader would have nothing left to convert with it.
- `steps[].artifacts`: the provenance of evidence captured for that step
  ([evidence](evidence.md#artifact-provenance-provider)).
- `steps[].actuations`: what the driver actually did to the screen during that step: the coordinate
  a tap sent, the endpoints a swipe travelled, and the channel that carried each gesture. This is
  the `actionLog` evidence kind, inherent in the manifest rather than a file
  ([evidence](evidence.md#actionlog--what-each-step-actually-did-to-the-screen)). A run recorded
  before `schemaVersion` 5 carries none.
  - `expect_actuations` holds the same for the scenario-level `expect` re-check, where the
    system-alert guard can actuate with no step to attribute it to.
  - From `schemaVersion` 7 a record may also carry `substitution`, naming why the element actuated
    is not the one the driver's default rule would have named. An older run carries none, which
    reads the same way as its absence today.
  - From `schemaVersion` 13 a record also carries `at`, the absolute instant the driver sent it, on
    the same footing as `started_at`. A viewer subtracts `video_anchor_s` from it the same way. An
    older run carries no `at`, and a viewer then falls back to the step's `started_at`.
- `steps[].system_alert`: for a `handleSystemAlert` step, the button it tapped (`label`) and the rule
  that chose it (`rule`: `sel`, `label table: <locale>`, or `position: button 2 of 2`). A run under
  a language the label table does not cover still shows what was tapped and why
  ([BE-0445](../roadmaps/BE-0445-system-alert-locale-agnostic-answer/BE-0445-system-alert-locale-agnostic-answer.md)).
  Absent on every other action, and on every run recorded before `schemaVersion` 12.
- `steps[].warnings`: non-fatal notes about how the step ran, shown on the step's report row. Two
  sources write them today:
  - a `totp` or `otp` step whose `minRemaining` window wait saw the wall clock jump
    ([BE-0474](../roadmaps/BE-0474-totp-window-alignment/BE-0474-totp-window-alignment.md))
  - the Android backend's read channel, when it recovers, falls back, or returns during the step

  A warning never changes `ok` or `reason`. The list is empty on a step that has none, and absent on
  every run recorded before `schemaVersion` 16, which a reader treats as no warnings.
- `channel_degraded`: why the Android backend's reads were on the `uiautomator dump` path when the
  scenario ended. With it, a run that passed on that fallback can be found. It never changes `ok`. The
  value is empty while the channel is healthy and on every other backend. Every run recorded before
  `schemaVersion` 18 lacks it, and a reader treats such a run as healthy.
- `channel_notes`: what the same read channel did after the scenario's last step, the notes no step
  was left to carry in its `warnings`. Empty when nothing happened, and absent on every run recorded
  before `schemaVersion` 18.
- `ignored_settings`: each setting the scenario put in effect that a target's route could not apply
  and the target declared under `runsOn.ignoredSettings`, as `{target, key, reason}`
  ([BE-0467](../roadmaps/BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md),
  [configuration](configuration.md#environment-specific-settings-runsonignoredsettings-be-0467)).
  It never changes `ok`. The list is empty on a scenario that ignored nothing, and on one that the
  preflight refused before any launch. Every run recorded before `schemaVersion` 17 lacks it, and a
  reader treats such a run as having ignored nothing.
- `network.json`'s `startedAt` (one file per scenario, not shown in the manifest above): each
  observed exchange's absolute start. It stands on the same footing as `steps[].started_at` and
  derives through the same scenario anchor, so a viewer subtracts `video_anchor_s` from both. See
  [report.html](#reporthtml) for how the two are interleaved into one timeline.
- `failure`: a summary on failure (e.g. `"step 3 (tap): no match: {...}"`). null on success.
- `provenance` (top, optional): a run-identity stamp ([BE-0049](../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)).
  It holds these keys:
  - `scenarioHash`: a `sha256:` fingerprint of the executed `scenario.yaml`
  - `toolVersion`: `bajutsu.__version__`
  - `gitRevision`: the commit, present only when the run is inside a git checkout
  - `configSource`: `{ host, owner, repo, ref, sha }`, the exact commit a branch-based run executed,
    present when the config came from a Git source
    ([BE-0063](../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source.md))
  - `targets` and `toolSource`: which build of which code ran ([below](#build-and-source-identity))

  The stamp groups accumulated runs by identity. A verdict that flips while the fingerprint is
  unchanged is **true flakiness** rather than an edited scenario. The stamp is pure metadata and never
  enters `ok`. (`schemaVersion` is `3` or higher once this block can appear, and `19` today.)

  A `run-set` job also records `workerEnvironment`, the environment of the worker that ran it
  (`local`, `devicefarm`, …). Another run of the same job may land elsewhere. The report lists
  it under *notices* ([BE-0484](../roadmaps/BE-0484-job-group-lease/BE-0484-job-group-lease.md)).
- `target` (top, optional): the target the run ran, so "the Android target passes while the iOS
  target fails" is computable from stored data
  ([BE-0404](../roadmaps/BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md)).
  One run resolves one target, so the key sits beside `backend` rather than on each scenario.
  `serve` mirrors it onto the run row and ranks targets against each other from it. Omitted when the
  run named no target. (`schemaVersion` is `10` or higher once this key can appear.)
- `label` (top, optional): the run-history partition, either the bound config's own name or the
  operator's `run --label` override. The label is opaque metadata: never parsed, never matched
  against config, and never an input to `ok`. It keeps two configs' runs readable apart after a
  `serve` restart. Omitted when the run carried no label. (`schemaVersion` is `10` or higher once
  this key can appear.)
- `idb` (top, optional, legacy): older manifests may carry an `idb_companion` / client version
  block (BE-0005). BE-0290 retired the block with the idb backend, and the manifest no longer
  writes it. An old manifest that still has it loads fine, since an unknown top-level key is
  ignored.
- `matrix` (top, optional): the cross-browser engine × scenario grid, present only on a
  `bajutsu run --browsers` run ([BE-0076](../roadmaps/BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)).
  `scenarios` stays the flat result list, each entry tagged with its `engine`. `matrix` is
  `{ engines, scenarios, cells: { "<scenario>": { "<engine>": { ok, sid, failure } } } }`, a pure
  aggregation of those per-engine verdicts that the report renders as a grid. `ok` is all-must-pass
  across every engine × scenario. A single-engine / iOS run omits the block.

  Nothing enforces a unique `name:` across a multi-file run. A second scenario sharing an earlier
  one's name gets its own row. The row carries a `(2)`, `(3)`, … suffix, so it does not
  overwrite the first's cell. (`schemaVersion` is `4` or higher once this block can appear.)

### Build and source identity

The stamp names the build of each app a run drove
([BE-0506](../roadmaps/BE-0506-run-code-identity/BE-0506-run-code-identity.md)). `gitRevision` keeps
its older meaning: the commit of the directory the run started in. The two keys below appear from
`schemaVersion` 19. A reader treats a run without them as an older run.

`provenance.targets.<name>` holds one entry per target the run drove. The entry has two groups.

| Key | Content |
|---|---|
| `app.hash` | `sha256:<hex>` digest of the binary at `subject.path` |
| `app.hashKind` | `file` for an `.apk`, `tree` for an `.app` bundle |
| `app.version` | `CFBundleShortVersionString` or `versionName` |
| `app.buildNumber` | `CFBundleVersion` or `versionCode`, as a string |
| `appSource.commit` | Full commit of the repository at `subject.sourceRoot` |
| `appSource.uncommitted` | `true` when that working tree holds uncommitted changes |

`provenance.toolSource` describes Bajutsu's own code. It carries `commit` and `source`. A `source` of
`checkout` also carries `uncommitted`. A checkout counts when it tracks Bajutsu's package. A source of
`build-arg` names the `BAJUTSU_BUILD_COMMIT` value of an install with no checkout. With neither, the
stamp omits `toolSource`.

The runner reads these values once per run. The read follows the builds and the `beforeRun` entries,
and precedes the first lease. Every
scenario in a run thus reports the same build. The commit describes the working tree, not the binary.
The two values can disagree, and the stamp keeps both.

A value the runner could not read stays in the stamp as `null`. A sibling `unavailable` mapping names
the reason for each such key. An unreadable value never changes a verdict and never stops a run.

| Reason | Meaning |
|---|---|
| `path-missing` | No binary exists at `subject.path`, or the bundle holds no file |
| `read-error` | The runner could not read the binary |
| `plist-unreadable` | The `Info.plist` did not parse, or lacks the version key |
| `apk-unreadable` | The `.apk` did not open as a zip archive, or has no manifest |
| `manifest-unparsable` | The manifest did not decode, or gives no literal version |
| `not-a-git-repo` | `subject.sourceRoot` holds no file a Git checkout tracks |
| `git-unavailable` | The `git` command is missing or timed out |
| `status-read-failed` | `git status` errored, or the runner did not run it |

The runner runs `git status` for a config the operator chose, and for no other. A `--config-source` of `uploaded`
or `remote` records `status-read-failed` instead. Git can run commands that such a tree configures
during a status read. The commit is still recorded.

The stamp omits a group the target never configured. A target with no `subject.path` has no `app`
group. A target with no `subject.sourceRoot` has no `appSource` group. A web target has neither. A
target whose device provider installs the app also has no `app` group. A real iOS device is the same
case, since its build arrives out of band. The file at `subject.path` is not known to be the binary
under test there.

## junit.xml

`junit.xml` serves CI integration. **One scenario is one `<testcase>`.** A failing scenario gets a
`<failure>`, whose `text` lists each step / expect's ok/FAIL and reason.

- On a `--browsers` matrix run, the engine keys the classname (`classname="bajutsu.<engine>"`),
  so CI sees `chromium.login` and `webkit.login` as distinct cases (BE-0076). A single-engine run
  keeps `classname="bajutsu"`.
- A scenario that ran without a setting its target declared under `runsOn.ignoredSettings` carries a
  `<properties>` block with one `bajutsu.ignoredSettings` property, whose value is a JSON array of
  `{target, key, reason}`
  ([BE-0467](../roadmaps/BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md)).
  The verdict does not change.

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

The [Common Test Report Format (CTRF)](https://ctrf.io/) export
([BE-0161](../roadmaps/BE-0161-ctrf-report-export/BE-0161-ctrf-report-export.md)) is an
open-standard JSON test report. A growing ecosystem reads it without per-tool adapters. The ecosystem includes the
`ctrf-io` GitHub Actions (PR-comment / job-summary reporters), cross-tool dashboards, and
flaky-test analytics.

JUnit XML strips a run down to name / time / a failure blob. CTRF carries Bajutsu's structured
detail. JUnit has no place for per-step outcomes, the engine and device, or artifacts as
first-class attachments. The export is a **pure projection of `manifest.json`**: the same data in a
new shape beside `junit.xml`. It adds no bookkeeping. Written after the verdict, it cannot change
that verdict (no LLM, no effect on pass/fail).

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
  from the `YYYYMMDD-HHMMSS` runId, parsed as UTC (the runId is stamped in UTC).
  `stop = start + duration`. Absolute per-test start/stop are deferred. They need an
  absolute per-scenario epoch (an optional follow-up), so the export omits them rather than
  approximating. The export carries no live host state, so `bajutsu report` regenerates the same
  run's `ctrf.json` byte-for-byte.
- `environment.commit` repeats `gitRevision`. The build identity sits under `environment.extra`,
  because CTRF accepts defined keys there and no others. None of those keys covers a per-target
  build. `extra.targets.<name>` carries the app
  values `hash` and `version`. It also carries the source `commit` and `uncommitted`.
  `extra.toolSource` carries the tool `commit`, `uncommitted`, and `source`. A gap stays
  `null`, with its reason under `unavailable`.
- `tests[].status` is `passed` / `failed` for every run that ran. A not-applicable record is
  `skipped` ([Runs that device conditions form](#runs-that-device-conditions-form)). The other CTRF
  counts stay `0`.
- A CTRF `step` allows only `{ name, status, extra }`, so a step's richer data (duration, reason,
  per-step assertions, artifacts) lands in `step.extra`. A consumer that renders just name/status
  sees a clean list, and Bajutsu-aware tooling can read the extras.
- Attachment `contentType` comes from an artifact-`kind` → MIME map (`screenshot`→`image/png`,
  `deviceLog`→`text/plain`, `elements`/`network`/`appTrace`→`application/json`), defaulting to
  `application/octet-stream`; `path` stays run-directory relative like the manifest. `video` is the
  one exception: its MIME is derived from the artifact's real filename extension instead of a fixed
  kind mapping. A Playwright-recorded web scenario's video is genuine WebM (`video/webm`,
  `scenario.webm`) rather than the `video/mp4` a simctl/adb recording is (see
  [evidence.md](evidence.md)).
- On a `--browsers` matrix run, each engine × scenario cell is one CTRF test. The engine appears in
  the test `name` and the `browser` field (mirroring JUnit's `classname`), and the engine × scenario
  grid lives under `results.extra.matrix`. Bajutsu's other surplus lives under the per-test
  `extra`: `sid`, `expect` results, alerts, `skipped_captures`, and the settings the run went
  without as `ignoredSettings`.
- Since CTRF is projected from the already-redacted manifest, it inherits the same secret scrubbing
  ([BE-0047](../roadmaps/BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)). No raw secret
  reaches it.

### Consuming ctrf.json in CI

`ctrf.json` sits beside `junit.xml`, so wiring it into a CI job takes a single consumer step. For
example, the `ctrf-io/github-test-reporter` action turns the file into a PR comment / job summary:

```yaml
- uses: ctrf-io/github-test-reporter@v1
  with:
    report-path: runs/*/ctrf.json
  if: always()
```

## report.html

`report.html` is a self-contained HTML page for humans, with inline CSS and no external assets.

### Header and scenario names

The header shows the run id and the overall PASS/FAIL. Below it sits the **scenario file name**
(`source_name`), the label of the whole suite. A lone `--scenario` file gives its own name. A
multi-file or whole-directory run gives the containing directory's name instead. The **file-level
`description`** follows when present.

One line per target then shows the build ([Build and source identity](#build-and-source-identity)).
The line holds the app version, the short hash, and the short source commit. An `uncommitted` marker
appears for a source tree with changes. A gap reads `unknown` with its reason.

Each scenario row's summary shows the **scenario name** and the scenario's **own originating file
name** (`source_files`). The file name is distinct from the header's whole-suite label. A
`bajutsu run` that read the scenario off disk sets `source_files`; an offline re-render does not.
When set, the **scenario-level `description`** sits beside the name. A run thus surfaces scenario
name, file name, and description throughout.

### Steps tab

Bajutsu merges the scenario definition and its execution into one **Steps tab**. The tab has
labelled sections (preconditions, **steps**, and **expectations**), each a table.

The **steps** table has these columns:

- `#`
- `result`: a PASS/FAIL pill in its own column
- `action`: a colored badge
- `detail`: the target description
- `at`
- `view`: the screenshot and an **in-report element-tree viewer**. The captured elements open in an
  in-page overlay, with no new tab.
- `reason`

The `#` column also carries the step's own line number in the scenario file when available. For
example, it can read `2:15`. A component expansion that changed the step list's shape drops the
line number instead. A wrong line would mislead more than none.

The `at` cell also shows the step's own elapsed time in parentheses, at the cell's end. For example,
`3.2s (1.1s)`. The parenthetical shows whenever the duration rounds to something other than 0.0s. A slow
`wait` or a sluggish gesture is then visible without opening the recording.

The **preconditions** table is collapsible (key / value). The **expectations** table has these
parallel columns, with the same id/constant tokens:

- `result`
- `kind`: a badge
- `target`: the checked selector, for example `#counter.value`
- `comparison`: for example `== “2”`
- `reason`

In the detail, identifiers (`#home.title`) and literal constants (`“text”`, numbers) appear as
subtly-styled inline tokens. The tokens look distinct from the solid action/assert badges, so
variables and constants differ at a glance.

An `assert` step's checks become a **nested table**. Each assertion gets one row, split into
`kind` / `target` / `comparison` cells, instead of a hard-to-read `a; b; c` line.

Steps that never ran (execution stops at the first failure) still appear, marked as skipped.

A [`group:`](scenarios.md#grouping-steps-group--folded-in-reporthtml) step's own steps fold into one
collapsed section. The section's heading names the group and counts its steps. A passing group stays
collapsed, and a group holding a failing step opens on its own. The header's "expand all" /
"collapse all" buttons open and close every group too, alongside every scenario.

**Observed network exchanges are interleaved into the steps** in time order, each placed by its
offset from the scenario start. An exchange row has the HTTP method as a neutral badge and the status
in the `result` column. The exchange's settings (method / endpoint / status / duration / headers)
appear as a **nested table** in the detail cell. The scenario's `network.filter.domains` (by URL
host) filters which requests appear; the Network tab still lists them all.

### Screenshots and the element-tree viewer

The screenshot shown for a step is the post-action `after.png`
([evidence](evidence.md#interval-evidence-video--devicelog--apptrace)). Every step that acts records
one. A step that fails before acting records none, and shows its `before.png` instead.

Hovering an element in the viewer highlights its frame on the screenshot. That frame comes from
`elements.json`, which a step re-reads after acting, so frame and pixels describe the same moment.
One exception applies. A non-mutating step (`assert`, `wait`) reuses the tree it settled on
(BE-0259). That tree precedes the screenshot rather than following it. The serve editor's element
picker resolves a step to the same image, for the same reason.

In two cases the image and the tree describe *different* screens. One is a
[`web` block](scenarios.md#web-entering-a-webviews-dom)'s native screenshot beside its WebView tree.
The other is a stored run that has lost its `after.png`. In both cases the viewer shows the image and the tree but draws no
frames. The tree button says why
([evidence](evidence.md#which-screen-an-artifact-shows-depicts)).

The serve editor offers no image for such a step at all. Every click there would resolve to an
element that was never at those pixels.

### Source view

A **Rich / YAML toggle** switches the same tab between the structured view above and the raw scenario
YAML. For a freshly baked `bajutsu run` report, the YAML is the scenario's own text, as authored.
Comments and formatting stay intact. The report slices this text straight from the source file
and never re-serializes the parsed model. A literal `totp.secret` still gets masked in place.

Two cases differ:

- A scenario whose component expansion changed its step count keeps the verbatim text, which is
  still accurate as authored. The scenario loses the `#` column's line numbers, because the
  expansion would leave them pointing at the wrong steps.
- A `data`/`dataFile`-driven scenario always shows the structured re-dump and never the verbatim
  text. Every row shares one authored template. Substituting `${row.*}` per row leaves the step
  count unchanged. A verbatim slice would show the same unsubstituted template for every row. It
  would never show what that row actually ran.

An offline re-render (`bajutsu report`, below) falls back to the structured re-dump it always used.
The original file's raw text was never persisted into the run.

### Visual comparison

A `visual` expectation renders an **interactive baseline-vs-actual comparator** beneath its row, with
four modes:

- **Swipe:** drag a divider to wipe between the two.
- **Onion:** a slider cross-fades actual over baseline.
- **Blend:** `mix-blend-mode: difference` turns identical pixels black and highlights changed pixels.
- **Diff:** the machine's precomputed pixel diff, with the assertion's `exclude` regions masked. It is
  present only when the check failed.

A `diff <pct>%` badge accompanies the comparator. On a first run, when only the actual screenshot
exists, a `no baseline yet` badge appears instead.

When the check did not pass, an **Approve as baseline** button appears. It promotes the captured
screenshot into the baselines dir. The button `POST`s `/api/approve`, so it works only when the report is
opened through `bajutsu serve`. A report opened from disk hides the button. The CLI twin is
[`bajutsu approve`](cli.md#approve).

### Playback

Failing rows have a red background. During playback, the report highlights the row whose action is on
screen and scrolls that row into view. A row takes over at its first accepted actuation's `at`, and
never ahead of its own `at` column. The reason is where a step stamps `started_at`. The stamp
comes before the step screenshots, reads the tree, and resolves its target. The screen then changes up
to seconds later. A highlight switched at `started_at` would lead the picture by that whole lead-in.

Some rows carry no actuation instant:

- a step that never actuates, such as `wait` or `assert`
- every step of a run recorded before `schemaVersion` 13

Such a row switches at its `at` column instead. The report reads the playhead on every presented
frame, not merely on the coarse `timeupdate` event. The scrubber marks where inside each step's bar
its action landed.

Clicking a step seeks the recording to that same switch instant **without auto-playing**. A paused
video stays paused, and a playing one keeps playing. The clicked row is then the highlighted one.
Its action appears a moment later. The screen answers a sent gesture within a fraction of a
second.

The row's start button still seeks to the `at` column. That column is `started_at` minus
`video_anchor_s`. The anchor is the recording's measured origin. Where no measurement was possible,
the anchor is the recording's best-known real start. The anchor is not the raw moment the step loop
began. The seek thus lands on what the row shows
([evidence](evidence.md#interval-evidence-video--devicelog--apptrace)).

One visible consequence follows. For a video-capturing Android or web scenario, the derived
value (seconds into the recording) can exceed the scenario's own `duration_s`. The two measure
different things on purpose. The video's timeline starts before the run's own step loop does, while
`duration_s` measures the loop itself. The difference is expected and does not mean either number is
wrong.

Clicking a step's screenshot opens a full-size lightbox. **← / →** (or the on-screen arrows) then
walk through every screenshot in the run, across scenario boundaries. A caption shows the
scenario, step, and position. The run's actuator backend is shown as a `driver: <backend>` chip in the
header and a small badge on each scenario row.

### Multi-target recordings

A multi-target scenario stacks one recording per target that captured video. Each recording
highlights its own target's step while it plays, so two rows can be highlighted at once.

When two or more recordings stack, each gets a color to keep them apart. Its target label, its steps'
highlight, and the target chip on those steps all share it. The colors are amber, blue, pink,
and green in stacking order, repeating from the fifth recording on. A target that recorded no video
has no player of its own; its steps seek the first recording and take that recording's color. Every
recording's frame stays the same blue. A scenario with a single recording keeps the default look.

Each recording carries its own **expand** button, beside its play/pause control. Pressing the
button opens a modal that shows that recording enlarged. The scenario's own **steps** rows clone beside it. The
screenshot/element-tree column stays empty in the clone. A click there would otherwise reopen
the element viewer on top of the modal. Clicking a cloned step seeks the enlarged recording, the same
way a click seeks it in the compact view. Playback highlights the row in progress and scrolls it into
view.

A multi-target scenario has two or more recordings, each with its own tab in the modal. Switching tabs
swaps which recording the modal shows. Every other target's player keeps playing, in sync, in
its ordinary compact form. A backdrop click, the close button, or Escape closes the modal and returns
the recording to its own player.

Device Log / App Trace remain separate tabs.

## Runs that device conditions form

A target may declare device conditions under `runsOn`
([configuration](configuration.md)). A list of models is one such condition, and an OS version
range is another. The conditions split one scenario into *runs*, one per set of coordinates.
`iPhone SE (3rd generation) / iOS 17` names one such set. An open range with no device in it leaves
a *not-applicable* record instead of a run. A not-applicable record neither passes nor fails. Every
report output shows both kinds, as follows.

| Output | Runs | Not-applicable records |
|---|---|---|
| `manifest.json` | each scenario entry carries `coordinates` (the label) and `run_coordinates` (per target and condition field) | `notApplicable`: `{ scenario, target, coordinates, reason }` per record |
| `manifest.json` `runMatrix` | `{ columns, scenarios, cells: { "<scenario>": { "<coordinates>": { status, sid, failure } } } }`, with `status` `passed` or `failed` | a cell with `status: "not-applicable"` and its `reason` |
| `report.html` | a **run matrix** table of scenarios against coordinates, and each scenario card names its coordinates | an `N/A` cell, a count chip in the header, and a list of each record's reason |
| `junit.xml` | each case named `<scenario> [<coordinates>]` | a `<skipped>` case, counted in the suite's `skipped` attribute |
| `ctrf.json` | the coordinates in the test name and in `extra.coordinates`; the grid under `results.extra.runMatrix` | a `skipped` test with `extra.notApplicable: true`, counted in `summary.skipped` |
| webhook payload | a failure names its coordinates | the result line counts the records |
| `serve` run history | each scenario named once, with every run counted in `passed` / `total` | a `notApplicable` count shown as `N n/a` |

A run whose every record is not applicable ran nothing. Its manifest and report read `FAIL`, and
the invocation exits with code 1 and prints `no run executed`. The run matrix puts a scenario with no
condition in the `any device` column.

A run that no condition formed carries none of these fields. Its outputs keep the shape the sections
above describe. `runMatrix` appears from `schemaVersion` 15, and the other fields from 14.

## Write API

```python
def write_report(run_dir, run_id, results, definitions=None, sources=None, source_name=None, description=None, provenance=None, writer=None, target=None, label=None, source_files=None, step_lines=None) -> Path  # all 4 formats; definitions = per-scenario dict, sources = raw YAML, source_name = scenario file name (whole suite), description = file-level description; source_files / step_lines = each scenario's own file name and steps' original line numbers, when a live run recovered them; provenance = run-identity stamp (BE-0049)
def write_html_and_junit(run_dir, run_id, results, definitions=None, sources=None, source_name=None, description=None, provenance=None, writer=None, source_files=None, step_lines=None) -> None  # the regenerable half (report.html + junit.xml + ctrf.json), leaving manifest.json untouched — used by re-render; provenance feeds the CTRF tool/environment fields
def manifest_dict(run_id, results, *, source_name=None, provenance=None, target=None, label=None) -> dict  # the versioned render model (schemaVersion); the manifest source (for tests / inspection)
def run_provenance(scenario_yaml, *, git_revision, config_source=None, identity=None) -> dict  # the run-identity stamp: scenarioHash + toolVersion + optional gitRevision (BE-0049) + optional configSource (BE-0063) + optional targets / toolSource (BE-0506)
def ctrf_json(run_id, results, *, provenance=None) -> dict  # the CTRF projection of the result model (BE-0161); provenance feeds tool.version / environment.commit / environment.extra
def junit_xml(results) -> str
def html_report(run_id, results, run_dir=None, definitions=None, sources=None, source_name=None, description=None, source_files=None, step_lines=None) -> str
def scenario_render_inputs(scenarios, plan_sources=None) -> tuple[list[dict], list[str]]  # (definitions, sources); shared by the bake and the re-render — `sources` is each scenario's own verbatim YAML when `plan_sources` (keyed by declared scenario name) recovers one, else the structured re-dump as before
def scenario_source_meta(scenarios, plan_sources=None) -> tuple[list[str | None], list[list[int] | None]]  # (source_files, step_lines), aligned with `scenario_render_inputs`'s `sources`
```

`runner.run_and_report` calls `write_report` and returns `(results, manifest_path)` to the CLI
([run-loop](run-loop.md#runner-the-run-pipeline)). The CLI exits 0 if every scenario passes, 1 on
failure.

## Regenerating a report (BE-0068)

The report is a **pure rendering of data stored in the run dir**. A finished run can be
re-rendered offline with the current template, without re-executing it. `manifest.json` is the
**versioned** (`schemaVersion`), lossless render model. `report.load` is its inverse:
`results_from_manifest()` reconstructs the `RunResult`s, and `load_run(run_dir)` recovers the whole
render model (outcomes from `manifest.json`, the scenario plan from `scenario.yaml`).

`bajutsu report <run>` ([cli](cli.md#report)) rewrites `report.html`, `junit.xml`, and `ctrf.json`
from that model. Re-rendering only re-presents recorded outcomes. It never re-runs an assertion or
changes a verdict. An older run renders with any newer-only section shown as "not captured"
rather than invented.

## Uploading a run to a server

`scripts/upload_run.py` sends one finished run to a `bajutsu serve`
([BE-0510](../roadmaps/BE-0510-serve-run-import/BE-0510-serve-run-import.md)). It then prints the
address of the run's report. The script uses the Python standard library alone and imports nothing
from Bajutsu. A copy of the file works on any machine that holds a run directory.

Check the screenshots and videos before the upload. The run masked its text when it wrote each file.
Pixels carry no masking, and the server does not scan the run again.

```bash
python3 scripts/upload_run.py runs/<runId> --server https://bajutsu.example.com
```

The first argument is a run directory, or a run id under `--runs-dir` (default `runs`). A successful
upload prints two lines, the report and then the zip of the run:

```text
https://bajutsu.example.com/runs/<runId>/report.html
https://bajutsu.example.com/runs/<runId>/archive.zip
```

A signed-in member of the organization that received the run can open both addresses. People outside
that organization cannot. A script that holds a key alone cannot either.

| Input | Behavior |
|---|---|
| Key | Read from `$BAJUTSU_SERVE_KEY`, or from the file `--key-file` names. No argument takes the key itself. |
| `--server` | `https://`, or `http://` to a loopback address. Any other `http://` address is refused. |
| Redirect | Not followed, and the upload stops. The key stays with the named server. |
| `--dry-run` | Lists the files and sends nothing. |
| Exit status | 0 when the import succeeded, 1 otherwise. It never reflects the run's verdict. |

The server keeps passive file types alone. The script names the dropped files on standard error.
[Self-hosting → Importing a finished run](self-hosting.md#importing-a-finished-run) covers the
server's checks and the credential it accepts.

## Posting evidence to a pull request (BE-0504)

The `post-evidence` composite action posts a finished run to a pull request or an issue. It reads
`manifest.json`, or `<sid>/result.json` when the run left no manifest. It installs no Bajutsu and
calls no Bajutsu command, so any repository that produces a run directory can use it.

The comment names each failed scenario, its first failed step, and the reason. It links the evidence
that the action uploads as GitHub Actions artifacts. A rerun updates the same comment. A hidden
marker on the comment's first line identifies it, so each job keeps one comment.

```yaml
permissions:
  contents: read
  pull-requests: write   # issues: write when the target is an issue
steps:
  - run: uv run bajutsu run --scenario scenarios/smoke.yaml --target web
  - name: Post run evidence
    if: always()
    uses: bajutsu-e2e/bajutsu/.github/actions/post-evidence@<ref>
```

The action uploads three kinds of artifact:

- **The whole run, as one zip.** Unpack it and open `report.html`, which works offline.
- **A screenshot per failed scenario**, for the first three failed scenarios. It is the failing
  step's `after.png` when the step has one, and is uploaded without a zip.
- **A video per failed scenario**, for the same three scenarios, uploaded without a zip.

A non-zipped upload names the artifact after the file. The action thus copies each file under
`<artifact-name>-screenshot-N` or `<artifact-name>-video-N`. We have not confirmed whether a browser
displays such a file or downloads it. Either way, the link delivers the file. A reader needs read access
to the repository, because artifact URLs require sign-in.

### Inputs

| Input | Default | Meaning |
|---|---|---|
| `run-dir` | `runs` | One run directory, or a root that holds several runs. |
| `artifact-name` | workflow and job name | Name of the zip, and prefix of the other artifacts. A matrix job adds its matrix values. |
| `retention-days` | `14` | Days GitHub keeps each uploaded artifact (1 to 90). |
| `compression-level` | `6` | Zlib level of the zip. Use `0` for a run that is mostly video. |
| `log-tail-kb` | `1024` | A `deviceLog` larger than this keeps its last `log-tail-kb` KB in the zip. `0` keeps logs whole. |
| `size-warn-mb` | `500` | Skips the zip upload above this staged size. `0` disables the guard. |
| `upload` | `true` | With `false`, uploads nothing and links `artifact-url`. |
| `artifact-url` | empty | Artifact to link when `upload` is `false`. The `bajutsu-e2e` action outputs one. |
| `issue-number` | the pull request | Without a number the action writes the job summary alone. |
| `github-token` | `github.token` | Token that writes the comment. |
| `comment-key` | workflow and job name | Key of the sticky comment. A matrix job adds its matrix values. |
| `comment-author` | `github-actions[bot]` | Login that owns the sticky comment. Set the App's login for an App token. |
| `max-failures` | `10` | Failed scenarios listed in full. |
| `evidence-base-url` | empty | Base URL that serves the run directory. Screenshots then render inline. |

### Behavior to know

- **The action never changes the job's result.** A broken upload, a read-only token (a pull request
  from a fork), or an API error produces a warning and the job summary. The test step's own exit code
  decides pass or fail. The staging and comment steps also run with `continue-on-error`, so an
  unexpected crash in the script degrades to a warning too.
- **A run stopped partway leaves no manifest.** The comment then says the run did not complete.
- **Truncation does not mask secrets.** The staged copy of a device log over `log-tail-kb` keeps the
  log's end, and its first line records the original size. No upload holds the full log. The staged
  copy keeps whatever text the log already holds.
- **The comment carries manifest text.** The run has already masked that text. Set `upload: false`
  when screenshots or videos must not leave the job.
- **`pull_request_target` is unsupported.** That trigger gives a write token to a job that runs a
  fork's code.
- **An evidence base URL** renders each screenshot in the comment and links `<base>/report.html`.
  The action never uploads there. A signed URL leaves a broken image once it expires.
- **Manifest version.** The renderer reads only fields present since the first `schemaVersion`, so any
  recorded version renders. A manifest with a missing or unrecognized version produces a notice in
  place of scenario details.

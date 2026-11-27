**English** · [日本語](ja/reporting.md)

# Reporting (manifest.json / JUnit / CTRF / `HTML`)

> One run covers one or more scenarios (`list[RunResult]`). Bajutsu writes their results in four
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

The runner writes the four report files after every
scenario has finished, and not before. The four are `manifest.json`, `junit.xml`, `ctrf.json`, and
`report.html`. A run can stop partway through a suite.
The machine may go down, or a CI job may time out. Such a run would otherwise leave no verdict at all. Each
`<sid>/result.json` closes that gap. The runner writes the file as soon as that scenario ends, before
the next scenario starts.

The file holds the manifest's `schemaVersion`. Under `scenario`, it holds the same entry that
`manifest.json` lists for that scenario under `scenarios`. The runner masks secret values the same
way it does for the manifest.

In a cross-browser matrix run, the file sits under `<engine>/<sid>/`. The parent directory names the
engine, and the entry's `engine` is empty. The base of each artifact path is `<engine>/`. `manifest.json` stays
the single source of truth for a run that completes.

A scenario's `targets` field
([BE-0428](../roadmaps/BE-0428-multi-target-scenario-execution/BE-0428-multi-target-scenario-execution.md))
may name two or more entries. Each declared target then gets a folder named after it.
That folder holds the target's evidence, the primary target's included. The per-step evidence above goes under
`<sid>/<target>/<stepId>/…`. That target's scenario-wide recordings and `network.json` go directly
under `<sid>/<target>/`. A scenario declaring zero targets or one target keeps the flat layout above
unchanged.

The Command Line Interface (CLI) assigns `runId` as `YYYYMMDD-HHMMSS`.
`bajutsu/common/run_meta/id.py`
([BE-0200](../roadmaps/BE-0200-run-id-contract/BE-0200-run-id-contract.md)) mints the ID once, so
every call site shares one format.

`sid` is `{NN}-{slug}`. `NN` is a zero-padded run-order index. The slug is the stem of the scenario's
own source file (`login_flow.yaml` → `login_flow`). When the runner knows no source file, the slug comes
from the scenario's `name:` field. A scenario built directly, rather than loaded from disk, is one
such case
([BE-0417](../roadmaps/BE-0417-scenario-result-folder-naming/BE-0417-scenario-result-folder-naming.md)).
`stepId` is `step.name` or `step<i>`.

Bajutsu caps both of those slugs at 60 characters
([BE-0420](../roadmaps/BE-0420-scenario-slug-length-cap/BE-0420-scenario-slug-length-cap.md)). A
source-file stem keeps its Unicode letters, so the cap counts characters rather than bytes.
A half-width letter or digit takes one byte, and a kana or kanji character takes more. Counting
characters keeps both kinds of name truncating to the same length.

A scenario whose file name or `name:` field runs past the budget still runs to completion. Its
evidence directory merely keeps the recognizable lead of the name. `Scenario.name` stays untouched,
so `manifest.json` and `report.html` carry the full name. Truncation can make two long names produce
the same slug. In `runs/<runId>/<sid>/`, the run-order index in front of the slug still tells them
apart.

## manifest.json

`RunResult` and its parts are all dataclasses. `manifest_dict` thus copies the step / expect
results verbatim, minus `wall_offset_s`. That field is an in-run conversion constant with no meaning once
persisted (see below).

Every other recorded time is an absolute wall-clock instant (epoch seconds). Such a value keeps its
meaning after the run that produced it exits
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
- `backend`: the actuator that drove the run (`xcuitest`, or `fake` in tests). Each run fixes one
  actuator, so the top-level value holds a single name in most runs. Each scenario also carries its own
  `backend` ([drivers](drivers.md#backend-selection-and-the-actuator)).
- `steps[].duration_s`: each step's timing. A run measures every elapsed time on the monotonic
  clock. A wall clock can jump backward mid-run. A Network Time Protocol (NTP)
  correction or a manual clock change can cause such a jump. Such a jump would corrupt a duration or a timeout. A run
  reads the wall clock once per scenario and uses it for the recorded times alone.
- `steps[].started_at`: the absolute wall-clock instant (epoch seconds) the step began. No
  video correction applies to it. A viewer subtracts `video_anchor_s` (below) at render time. The result
  is the number of seconds to seek the recording to. [report.html](#reporthtml) shows that number as
  the **steps** table's `at` column.

  The manifest stores the raw instant rather than the already-corrected offset. A viewer can thus
  recompute the offset from a saved run. A fix to how a run resolves its anchor is one example.
  Nothing requires a re-run of the scenario
  ([BE-0348](../roadmaps/BE-0348-absolute-timestamp-recording/BE-0348-absolute-timestamp-recording.md)).
- `video_anchor_s` (per scenario): the absolute wall-clock instant the scenario's video started.
  The finished recording's own duration may be a wall-clock measure. The anchor then derives from
  that duration. Otherwise it comes from the recording's confirmed start signal
  ([evidence](evidence.md#interval-evidence-video--devicelog--apptrace)). Every viewer subtracts it.

  A run recorded before `schemaVersion` 6 carries no anchor. Its `started_at` values are already
  video-relative. A viewer thus reads the missing anchor as `0.0` and renders them unchanged.
- `wall_offset_s` (in-run, never persisted): an offset for the run's own monotonic instants.
  Adding it to such an instant gives a wall-clock epoch. A network exchange has a monotonic receive time. The run
  uses the offset to carry that time onto the same anchor `video_anchor_s` uses. The offset never
  survives into the manifest, and no monotonic instant does either. A later reader would thus have
  nothing left to convert.
- `steps[].artifacts`: the provenance of evidence captured for that step
  ([evidence](evidence.md#artifact-provenance-provider)).
- `steps[].actuations`: what the driver actually did to the screen
  during that step. It records the
  coordinate a tap sent and the endpoints a swipe travelled. It also records the channel that
  carried each gesture.
  This `actionLog` evidence kind is inherent in the manifest rather than a file
  ([evidence](evidence.md#actionlog--what-each-step-actually-did-to-the-screen)). A run recorded
  before `schemaVersion` 5 carries none.
  - `expect_actuations` holds the same for the scenario-level `expect` re-check. There, the
    system-alert guard can actuate with no step to attribute it to.
  - From `schemaVersion` 7, a record may also carry `substitution`. It names why the actuated
    element differs from the default rule's choice. An older run carries
    none, which reads the same way as its absence today.
  - A record from `schemaVersion` 13 on also carries `at`. That key holds the absolute
    send time, on the same footing as `started_at`.
    A viewer subtracts `video_anchor_s` from it the same way. An
    older run carries no `at`, and a viewer then falls back to the step's `started_at`.
- `steps[].system_alert`: appears on a `handleSystemAlert` step. It holds the tapped button
  (`label`) and the choosing rule (`rule`). The rule is `sel`, `label table: <locale>`, or `position: button 2 of 2`.
  A run under a language outside the label table still shows both
  ([BE-0445](../roadmaps/BE-0445-system-alert-locale-agnostic-answer/BE-0445-system-alert-locale-agnostic-answer.md)).
  Absent on every other action, and on every run recorded before `schemaVersion` 12.
- `steps[].warnings`: non-fatal notes about how the step ran, shown on the step's report row. Two
  sources write them today:
  - a `totp` or `otp` step whose `minRemaining` window wait saw the wall clock jump
    ([BE-0474](../roadmaps/BE-0474-totp-window-alignment/BE-0474-totp-window-alignment.md))
  - the Android backend's read channel, when it recovers, falls back, or returns during the step

  A warning never changes `ok` or `reason`. The list is empty on a step that has none. Every run
  recorded before `schemaVersion` 16 lacks the list. A reader treats such a run as having no
  warnings.
- `channel_degraded`: the reason for a fallback of the Android backend's reads. It applies when
  those reads were on the `uiautomator dump` path at the scenario's end. The field lets you find a run that passed on that fallback. It never changes `ok`.
  The value is empty while the channel is healthy and on every other backend. Every run recorded
  before `schemaVersion` 18 lacks it, and a reader treats such a run as healthy.
- `channel_notes`: what the same read channel did after the scenario's last step. No later step could carry these notes in its `warnings`. The list is empty when nothing happened. Every run
  recorded before `schemaVersion` 18 lacks it.
- `ignored_settings`: the settings a target's route could not apply, as `{target, key, reason}`.
  It lists each setting that the scenario put in effect and the target declared under
  `runsOn.ignoredSettings`
  ([BE-0467](../roadmaps/BE-0467-environment-specific-settings/BE-0467-environment-specific-settings.md),
  [configuration](configuration.md#environment-specific-settings-runsonignoredsettings-be-0467)).
  It never changes `ok`. The list is empty on a scenario that ignored nothing. The list is also empty when
  the preflight refused the scenario before any launch. Every run recorded before `schemaVersion` 17
  lacks the list. A reader treats such a run as having ignored nothing.
- `network.json`'s `startedAt`: each observed exchange's absolute start. The run writes one such
  file per scenario, and the manifest above does not show it. The value stands on the same footing as `steps[].started_at`.
  Both derive through the same scenario anchor, so a viewer subtracts `video_anchor_s` from both.
  [report.html](#reporthtml) interleaves the two into one timeline.
- `failure`: a summary of what broke (for example `"step 3 (tap): no match: {...}"`). null on
  success.
- `provenance` (top, optional): a run-identity stamp
  ([BE-0049](../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)).
  It holds these keys:
  - `scenarioHash`: a `sha256:` fingerprint of the `scenario.yaml` that ran
  - `toolVersion`: `bajutsu.__version__`
  - `gitRevision`: the commit, present when the run is inside a Git checkout and absent otherwise
  - `configSource`: `{ host, owner, repo, ref, sha }`, the exact commit a branch-based run used.
    Present when the config came from a Git source
    ([BE-0063](../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source.md))
  - `targets` and `toolSource`: which build of which code ran ([below](#build-and-source-identity))

  The stamp groups accumulated runs by identity. A verdict may flip while the fingerprint stays the
  same. Such a flip is **true flakiness** rather than an edited scenario. The stamp is pure metadata
  and never enters `ok`. The `schemaVersion` is `3` or higher once this block can appear, and `19`
  today.

  A `run-set` job also records `workerEnvironment`. It names the environment of the worker
  that ran the job (`local`, `devicefarm`, …). Another run of the same job may land elsewhere. The report lists
  the value under *notices*
  ([BE-0484](../roadmaps/BE-0484-job-group-lease/BE-0484-job-group-lease.md)).
- `target` (top, optional): the target the run ran. Stored data can thus compare targets. One example is "the Android
  target passes while the iOS target breaks"
  ([BE-0404](../roadmaps/BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md)).
  One run resolves one target, so the key sits beside `backend` rather than on each scenario.
  `serve` mirrors it onto the run row and ranks targets against each other from it. The run omits
  the key when it named no target. The `schemaVersion` is `10` or higher once this key can appear.
- `label` (top, optional): the run-history partition. The value is either the bound config's own
  name or the operator's `run --label` override. The label is opaque metadata. Nothing parses the label
  or matches it against config. The label never feeds into `ok`. It keeps two configs' runs readable apart after a
  `serve` restart. The run omits the key when it carried no label. The `schemaVersion` is `10` or
  higher once this key can appear.
- `idb` (top, optional, legacy): a block of `idb_companion` and client versions. Older
  manifests may still carry it (BE-0005). BE-0290 retired the block with the idb backend. The manifest no
  longer writes it. An old manifest that still has it loads fine, since the loader skips an unknown
  top-level key.
- `matrix` (top, optional): the cross-browser engine × scenario grid. The grid is present on a
  `bajutsu run --browsers` run and on no other
  ([BE-0076](../roadmaps/BE-0076-web-cross-browser-engines/BE-0076-web-cross-browser-engines.md)).
  `scenarios` stays the flat result list, and each entry carries its `engine`. `matrix` has the shape
  `{ engines, scenarios, cells: { "<scenario>": { "<engine>": { ok, sid, failure } } } }`. The block
  is a pure aggregation of those per-engine verdicts, and the report renders it as a grid. `ok` is
  all-must-pass across every engine × scenario. A single-engine / iOS run omits the block.

  Nothing enforces a unique `name:` across a multi-file run. A second scenario sharing an earlier
  one's name gets its own row. The row carries a `(2)`, `(3)`, … suffix, so it does not
  overwrite the first's cell. The `schemaVersion` is `4` or higher once this block can appear.

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

The runner runs `git status` for a config the operator chose, and for no other. A `--config-source`
of `uploaded` or `remote` records `status-read-failed` instead. Git can run commands that such a tree
configures during a status read. The runner still records the commit.

The stamp omits a group the target never configured. A target with no `subject.path` has no `app`
group. A target with no `subject.sourceRoot` has no `appSource` group. A web target has neither. A
target whose device provider installs the app also has no `app` group. A real iOS device is the same
case, since its build arrives out of band. The file at `subject.path` is not known to be the binary
under test there.

## junit.xml

`junit.xml` serves CI integration. **One scenario is one `<testcase>`.** A failing scenario gets a
`<failure>`. Its `text` lists each step / expect's `ok`/`FAIL` and reason.

- On a `--browsers` matrix run, the engine keys the classname (`classname="bajutsu.<engine>"`).
  CI thus sees `chromium.login` and `webkit.login` as distinct cases (BE-0076). A single-engine run
  keeps `classname="bajutsu"`.
- A target can declare settings under `runsOn.ignoredSettings`. A scenario that ran without one
  carries a `<properties>` block. The block has one `bajutsu.ignoredSettings` property. Its value is a JavaScript
  Object Notation (`JSON`) array of `{target, key, reason}`
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
open test report standard. It uses `JSON`. A growing ecosystem reads it without
per-tool adapters. Examples are the `ctrf-io` GitHub Actions
(PR-comment / job-summary reporters). Cross-tool dashboards and flaky-test analytics read it too.

A JUnit file strips a run down to name, time, and a blob of what broke. CTRF carries Bajutsu's
structured detail. JUnit has no place for
per-step outcomes or the engine and device. It has no first-class attachments for artifacts either. The export is a **pure projection of `manifest.json`**. It puts the same data in a new shape
beside `junit.xml`. It adds no bookkeeping. The runner writes it after the verdict. The export thus
cannot change that verdict (no Large Language Model (LLM), no effect on pass/fail).

The document has the shape `{ reportFormat: "CTRF", specVersion, generatedBy, timestamp, results }`.
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

- `summary.duration` and each `tests[].duration` are exact milliseconds
  (Σ / per-scenario `duration_s`). CTRF consumers key on this field. `summary.start` and the document `timestamp` derive
  from the `YYYYMMDD-HHMMSS` runId. The export parses the runId as Coordinated Universal Time (`UTC`),
  because the CLI stamps it in `UTC`. `stop = start + duration`. The export defers absolute per-test
  start/stop. They need an
  absolute per-scenario epoch (an optional follow-up). The export omits them rather than
  approximating. The export carries no live machine state. `bajutsu report` thus regenerates the same run's
  `ctrf.json` byte-for-byte.
- `environment.commit` repeats `gitRevision`. The build identity sits under `environment.extra`.
  CTRF accepts defined keys in `environment` and no others. None of those keys covers a per-target
  build. `extra.targets.<name>` carries the app
  values `hash` and `version`. It also carries the source `commit` and `uncommitted`.
  `extra.toolSource` carries the tool `commit`, `uncommitted`, and `source`. A gap stays
  `null`, with its reason under `unavailable`.
- `tests[].status` is `passed` / `failed` for
  every run that ran. A not-applicable record is
  `skipped` ([Runs that device conditions form](#runs-that-device-conditions-form)). The other CTRF
  counts stay `0`.
- A CTRF `step` allows `{ name, status, extra }` and nothing else. A step's richer data thus lands in
  `step.extra`. That data covers duration, reason, per-step assertions, and artifacts. A consumer that renders
  name/status alone sees a clean list, and Bajutsu-aware tooling can read the extras.
- Attachment `contentType` comes from a map of artifact `kind` to media type. The map gives
  `screenshot`→`image/png` and `deviceLog`→`text/plain`. It gives
  `elements`/`network`/`appTrace`→`application/json`, and defaults to `application/octet-stream`.
  `path` stays run-directory relative like the manifest. `video` is the one exception. Its media
  type comes from the artifact's real filename extension instead of a fixed kind mapping. A
  Playwright-recorded web scenario's video is genuine WebM (`video/webm`, `scenario.webm`). A
  simctl/adb recording is `video/mp4` instead (see [evidence.md](evidence.md)).
- On a `--browsers` matrix run, each engine × scenario cell is one CTRF test. The engine appears in
  the test `name` and the `browser` field (mirroring JUnit's `classname`). The engine × scenario
  grid lives under `results.extra.matrix`. Bajutsu's other surplus lives under the per-test
  `extra`. That surplus is `sid`, `expect` results, alerts, and
  `skipped_captures`. The settings the run went without appear there as `ignoredSettings`.
- CTRF projects from the already-redacted manifest. It thus inherits the same secret scrubbing
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

`report.html` is a self-contained HyperText Markup Language (`HTML`) page for humans. It carries inline
Cascading Style Sheets (CSS) and no external assets.

### Header and scenario names

The header shows the run id and the run's `PASS`/`FAIL` verdict. Below it sits the **scenario file name**
(`source_name`), the label of the whole suite. A lone `--scenario` file gives its own name. A
multi-file or whole-directory run gives the containing directory's name instead. The **file-level
`description`** follows when present.

One line per target then shows the build ([Build and source identity](#build-and-source-identity)).
The line holds the app version, the short hash, and the short source commit. An `uncommitted` marker
appears for a source tree with changes. A gap reads `unknown` with its reason.

Each scenario row's summary shows the **scenario name**. It also shows the scenario's
**own originating file name** (`source_files`). The file name is distinct from the header's whole-suite label. A
`bajutsu run` that read the scenario off disk sets `source_files`; an offline re-render does not.
When set, the **scenario-level `description`** sits beside the name. A run thus surfaces scenario
name, file name, and description throughout.

### Steps tab

Bajutsu merges the scenario definition and its run results into one **Steps tab**. The tab has
labelled sections (preconditions, **steps**, and **expectations**), each a table.

The **steps** table has these columns:

- `#`
- `result`: a `PASS`/`FAIL` pill in its own column
- `action`: a tinted badge
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

In the detail, some values appear as inline tokens with subtle styling. These values are
identifiers (`#home.title`) and literal constants (`“text”`, numbers). The tokens look distinct from the solid action/assert badges.
Variables and constants thus differ at a glance.

An `assert` step's checks become a **nested table**. Each assertion gets one row with
`kind` / `target` / `comparison` cells. This layout replaces a hard-to-read `a; b; c` line.

Steps that never ran still appear, marked as skipped, because a run stops at the first broken step.

A [`group:`](scenarios.md#grouping-steps-group--folded-in-reporthtml) step's own steps fold into one
collapsed section. The section's heading names the group and counts its steps. A passing group stays
collapsed, and a group holding a failing step opens on its own. The header has "expand all" and
"collapse all" buttons. They open and close every group and every scenario.

The report **interleaves observed network exchanges into the steps** in time order. Each exchange
sits at its offset from the scenario start. An exchange row has the request method as a neutral badge
and the status in the `result` column. The detail cell holds the exchange's settings as a
**nested table**. Its rows are method / endpoint / status / duration / headers. The scenario's
`network.filter.domains` filters which requests appear, by hostname. The Network tab still
lists them all.

### Screenshots and the element-tree viewer

The screenshot shown for a step is the post-action `after.png`
([evidence](evidence.md#interval-evidence-video--devicelog--apptrace)). Every step that acts records
one. A step that fails before acting records none, and shows its `before.png` instead.

Hovering an element in the viewer highlights its frame on the screenshot. That frame comes from
`elements.json`, which a step re-reads after acting. Frame and pixels thus describe the same moment.
One exception applies. A non-mutating step (`assert`, `wait`) reuses the tree it settled on
(BE-0259). That tree precedes the screenshot rather than following it. The serve editor's element
picker resolves a step to the same image, for the same reason.

In two cases the image and the tree describe *different* screens. One is a
[`web` block](scenarios.md#web-entering-a-webviews-dom)'s native screenshot beside its WebView tree.
The other is a stored run that has lost its `after.png`. In both cases the viewer shows the image and
the tree but draws no frames. The tree button says why
([evidence](evidence.md#which-screen-an-artifact-shows-depicts)).

The serve editor offers no image for such a step at all. Every click there would resolve to an
element that was never at those pixels.

### Source view

A **Rich / `YAML` toggle** switches the same tab. It shows either the structured view above or
the raw scenario source. A freshly baked `bajutsu run` report shows the
scenario's authored text there. Comments and formatting stay intact. The report slices this text straight from the source
file and never re-serializes the parsed model. A literal `totp.secret` still gets masked in place.

Two cases differ:

- Component expansion may change a scenario's
  step count. Such a scenario keeps the verbatim text,
  which is still accurate as authored. The scenario loses the `#` column's line numbers. The
  expansion would leave them pointing at the wrong steps.
- A `data`/`dataFile`-driven scenario always shows the structured re-dump and never the verbatim
  text. Every row shares one authored template. Substituting `${row.*}` per row leaves the step
  count unchanged. A verbatim slice would show the same unsubstituted template for every row. It
  would never show what that row actually ran.

An offline re-render (`bajutsu report`, below) falls back to the structured re-dump it always used.
The run never persisted the original file's raw text.

### Visual comparison

A `visual` expectation renders an **interactive baseline-vs-actual comparator** beneath its row.
The comparator has four modes:

- **Swipe:** drag a divider to wipe between the two.
- **Onion:** a slider cross-fades actual over baseline.
- **Blend:** `mix-blend-mode: difference` turns identical pixels fully dark and highlights changed
  pixels.
- **Diff:** the machine's precomputed pixel diff, with the assertion's `exclude` regions masked. This
  mode appears when the check did not pass, and not otherwise.

A `diff <pct>%` badge accompanies the comparator. On a first run, the actual screenshot exists
without a baseline. A `no baseline yet` badge then appears instead.

When the check did not pass, an **Approve as baseline** button appears. It promotes the captured
screenshot into the baselines dir. The button `POST`s `/api/approve`. It thus needs the
report to be open through `bajutsu serve`. A report opened from disk hides the button. The CLI twin is
[`bajutsu approve`](cli.md#approve).

### Playback

Failing rows have a red background. During playback, the report highlights the row of the
on-screen action. It also scrolls that row into view. A row takes over at the `at` of its first accepted actuation.
It never takes over ahead of its own `at` column. The reason is where a step stamps `started_at`. The stamp comes
before the step takes screenshots, reads the tree, and resolves its target. The screen then changes
up to seconds later. A highlight switched at `started_at` would lead the picture by that whole
lead-in.

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

One visible consequence follows. Take an Android or web scenario that captures video. The derived
value counts seconds into the recording. It can exceed the scenario's own `duration_s`. The two measure
different spans on purpose. The video's timeline starts before the run's own step loop does.
`duration_s` measures the loop itself. Expect the difference; it does not mean either number is
wrong.

Clicking a step's screenshot opens a full-size lightbox. **← / →** (or the on-screen arrows) then
walk through every screenshot in the run. The walk crosses scenario boundaries. A caption shows the
scenario, step, and position. The header shows the run's actuator backend as a
`driver: <backend>` chip. Each scenario row also shows it as a small
badge.

### Multi-target recordings

A multi-target scenario stacks one recording per target that captured video. Each recording
highlights its own target's step while it plays, so two rows can light up at once.

When two or more recordings stack, each gets its own hue to keep them apart. Its target label shares that hue. So do its steps'
highlight and the target chip on those steps. The hues follow stacking order: amber, blue, pink, and green. They
repeat from the fifth recording on. A target that recorded no
video has no player of its own. Its steps seek the first recording and take that recording's hue.
Every recording's frame stays the same blue. A scenario with a single recording keeps the default
look.

Each recording carries its own **expand** button, beside its play/pause control. Pressing the
button opens a modal that shows that recording enlarged. The scenario's own **steps** rows clone
beside it. The screenshot/element-tree column stays empty in the clone. A click there would otherwise
reopen the element viewer on top of the modal. Clicking a cloned step seeks the enlarged recording,
as a click does in the compact view. Playback highlights the row in progress and
scrolls it into view.

A multi-target scenario has two or more recordings, each with its own tab in the modal. Switching tabs
swaps which recording the modal shows. Every other target's player keeps playing, in sync, in
its ordinary compact form. A backdrop click, the close button, or Escape closes the modal. The
recording then returns to its own player.

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
| webhook payload | a failing run names its coordinates | the result line counts the records |
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
([run-loop](run-loop.md#runner-the-run-pipeline)). The CLI exits 0 if every scenario passes, and 1
otherwise.

## Regenerating a report (BE-0068)

The report is a **pure rendering of data stored in the run dir**. The current template can re-render
a finished run offline without running it again. `manifest.json` is the **versioned**
(`schemaVersion`), lossless render model. `report.load` is its inverse.
`results_from_manifest()` reconstructs the `RunResult`s. `load_run(run_dir)` recovers the whole
render model. It takes outcomes from `manifest.json` and the scenario plan from `scenario.yaml`.

`bajutsu report <run>` ([cli](cli.md#report)) rewrites `report.html`, `junit.xml`, and `ctrf.json`
from that model. Re-rendering merely re-presents recorded outcomes. It never re-runs an assertion or
changes a verdict. An older run renders with any newer section shown as "not captured"
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
| Key | An `editor` API key, or the shared token on a server without OAuth. Read from `$BAJUTSU_SERVE_KEY`, or from the file `--key-file` names. No argument takes the key itself. |
| `--server` | `https://`, or `http://` to a loopback address. Any other `http://` address is refused. |
| Redirect | Not followed, and the upload stops. The key stays with the named server. |
| `--dry-run` | Lists the files and sends nothing. |
| Exit status | 0 when the import succeeded, 1 otherwise. It never reflects the run's verdict. |

The server keeps passive file types alone. The script names the dropped files on standard error.
[Self-hosting → Importing a finished run](self-hosting.md#importing-a-finished-run) covers the
server's checks and the credential it accepts.

## Posting a run's address to a pull request

`scripts/post_run_url.py` tells reviewers where an uploaded run is
([BE-0508](../roadmaps/BE-0508-post-run-url-comment/BE-0508-post-run-url-comment.md)). It writes one
comment on a pull request or an issue. The comment gives the verdict and the counts. It names each
failing scenario with its first failing step and the reason, and it links the run. A rerun edits the
same comment. The script uploads nothing, and like the upload script it uses the Python standard
library alone.

```bash
python3 scripts/post_run_url.py runs/<runId> \
  --url https://bajutsu.example.com/runs/<runId>/report.html \
  --archive-url https://bajutsu.example.com/runs/<runId>/archive.zip \
  --to owner/repo#123
```

The two addresses are the lines that the upload script prints. The first argument is a run directory,
or a run id under `--runs-dir` (default `runs`). The script reads the run's `manifest.json`. For a run that
left no manifest, the script reads each `<sid>/result.json` instead. Before any write, the script prints
the target, the action, and the full text. It then asks for confirmation.

| Input | Behavior |
|---|---|
| `--to` | Where the text goes. The forms are listed below the table. |
| `--url` | Required. Its path must hold `/runs/<runId>/` for this run. |
| `--archive-url` | Optional. Links the zip of the run. A query string is refused. |
| `--marker` | The slot of the sticky comment, `local` by default. |
| `--author` | The comment's owner, for a token that has no user. |
| `--max-failures` | Scenarios listed in full, 10 by default. |
| `--dry-run` | Prints the plan and the text, and writes nothing. |
| `--yes` | Posts without asking. |
| Exit status | 0 for a post or a finished `--dry-run`, 1 when nothing was posted, 2 for a refused argument. |

`--to` takes one of three forms:

- `owner/repo#NUMBER` names a pull request or an issue.
- A pull request or issue address on `https://github.com/` names the same thread.
- `release:owner/repo@TAG` names a release.

Behavior to know:

- **A release has no comments.** The script writes the same text into the release notes instead. The
  text sits between two hidden markers that carry the `--marker` name. A rerun replaces that block and
  keeps the rest of the notes. The script never creates a release, and a missing tag stops the post.
- **The token stays with GitHub.** The script reads `GH_TOKEN`, then `GITHUB_TOKEN`, then
  `gh auth token`. It sends requests to `https://api.github.com` alone. A redirect drops the token.
  A comment needs write access to pull requests or issues. A release needs write access to contents.
- **A bare `#123` is refused.** In a fork workflow, `origin` names the fork. A guessed repository
  would post to the wrong thread.
- **Both addresses refuse user information and queries.** A pasted secret would sit there. The
  thread may be public, so the script never publishes either part. The rule covers `--url` and
  `--archive-url`. A signed zip address with a query string is thus refused.
- **A marker cannot start with `bajutsu_`.** That prefix marks a server API key. A marker takes
  letters and digits, plus dots, hyphens, and underscores.
- **Run text cannot change the comment's markup.** Each scenario name and reason sits in a code span.
  The script cuts each such string at 500 characters. Near the size limit of GitHub, the script drops
  whole scenario blocks. The comment then states the number it left out.
- **The comment embeds no screenshot.** GitHub cannot fetch an image from an address behind a
  sign-in. A reviewer opens the run on the server instead.
- **The exit status never reflects the run's verdict.** A post of a run that did not pass still
  exits 0.

## Posting evidence to a pull request (BE-0504)

The `post-evidence` composite action posts a finished run to a pull request or an issue. It reads
`manifest.json`, or `<sid>/result.json` when the run left no manifest. It installs no Bajutsu and
calls no Bajutsu command. Any repository that produces a run directory can thus use it.

The comment names each failing scenario, the first step that broke in it, and the reason. It links the
evidence that the action uploads as GitHub Actions artifacts. A rerun updates the same comment. A
hidden marker on the comment's first line identifies it, so each job keeps one comment. The comment's header
shows when the action last wrote it, in coordinated universal time.

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
- **A screenshot per failing scenario**, for the first three failing scenarios. The action takes the
  broken step's `after.png` when the step has one. The upload is not zipped.
- **A video per failing scenario**, for the same three scenarios, uploaded without a zip.

A non-zipped upload names the artifact after the file. The action thus copies each file under
`<artifact-name>-screenshot-N` or `<artifact-name>-video-N`. A reader needs read access to the
repository, because artifact links require sign-in.

What a link does depends on the file type and the browser. We checked one run in November 2026:

| File | `Content-Disposition` that GitHub sent | Signed-in reader in Safari on iPhone |
|---|---|---|
| Portable Network Graphics (PNG) screenshot | `inline` | Sees the image |
| MP4 video | `attachment` | Watches the video in the browser |
| WebM video | `attachment` | Chooses between viewing and downloading |

The header values come from the artifact download endpoint of the GitHub API. We did not check
desktop browsers. Re-running a job did not collide with the artifact names of the earlier attempt.
The action thus has no `overwrite` input.

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
| `max-failures` | `10` | Number of failing scenarios listed in full. |
| `evidence-base-url` | empty | Base address that serves the run directory. Screenshots then render inline. |

### Behavior to know

- **The action never changes the job result**. It reports its own problems instead, as a
  warning and in the job summary. Examples are a broken upload and an Application
  Programming Interface (API) error. A token without write access is another example, as on a pull
  request from a fork.

  The test step's own exit code decides pass or fail. The staging and comment steps also run with
  `continue-on-error`, so an unexpected error in the script degrades to a warning too.
- **A run stopped partway leaves no manifest.** The comment then says the run did not complete.
- **Truncation does not mask secrets.** The action stages a copy of a device log over
  `log-tail-kb`. That copy keeps the log's end, and its first line records the original size. No
  upload holds the full log. The staged copy keeps whatever text the log already holds.
- **The comment carries manifest text.** The run has already masked that text. Set `upload: false`
  when screenshots or videos must not leave the job.
- **`pull_request_target` is unsupported**. That trigger gives a write token to a job that runs a
  fork's code.
- **An evidence base address** renders each screenshot in the comment and links `<base>/report.html`.
  The action never uploads there. A signed address leaves a broken image once it expires.
- **Manifest version**. The renderer reads fields present since the first `schemaVersion` and no
  later ones. Any recorded version thus renders. A manifest with a missing or unrecognized version
  produces a notice in place of scenario details.

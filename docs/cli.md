**English** · [日本語](ja/cli.md)

# CLI reference

> Implementation: `bajutsu/cli/` (Typer; one file per command under `cli/commands/`). The entry point is `bajutsu = "bajutsu.cli:app"` in
> `pyproject.toml`. Every command in this CLI (command-line interface) selects one [app](glossary.md#target-app-device) with `--target <name>` and points at config with
> `--config` (default `bajutsu.config.yaml`). App-specific differences live in config
> ([configuration](configuration.md)).

Related: [run-loop](run-loop.md) · [recording](recording.md) · [codegen](codegen.md) · [configuration](configuration.md)

---

## Common

- Every command loads `.env` first (`_bootstrap`, below).
- Missing config / undefined app / no actuator → prints a message and exits with **code 2**.
- `--backend` is a comma-separated list (e.g. `ios`). Empty uses config's `backend`. It checks
  availability in order and the **first usable one is the actuator**
  ([drivers](drivers.md#backend-selection-and-the-actuator)).

## `run`

Runs a scenario **deterministically**; pass/fail is machine-only. The only AI component is the **alert guard** (on by default per scenario), which fires only to clear an OS prompt that blocked a step — see [`alertHandling`](scenarios.md#alerthandling-the-system-alert-guard).

```bash
bajutsu run --target <name> [--scenario <file.yaml>] [options]
```

By default `run` loads **every `*.yaml`** in the app's configured scenarios dir
(`targets.<name>.scenarios`, see [configuration](configuration.md)) — so the config alone is enough
to run. Pass `--scenario <file>` to run a single file instead.

| Option | Default | Description |
|---|---|---|
| `--target` | (required) | the target app (config's `targets.<name>`) |
| `--scenario` | config's `scenarios` dir | run one `*.yaml` instead of the app's whole scenarios dir |
| `--backend` | config | actuator order (comma-separated; first usable wins) |
| `--tag` | "" | comma list; run only scenarios carrying any of these tags |
| `--exclude` | "" | comma list; skip scenarios carrying any of these tags |
| `--udid` | `booted` | the target Simulator (comma list = a device pool for `--workers`) |
| `--erase / --no-erase` | scenario › config › off | override every scenario's `preconditions.erase` (wipe the simulator first); omit and it resolves each scenario's value, then the target's `erase` config, then off ([BE-0177](../roadmaps/BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)). The app is reinstalled fresh either way (config `appPath` + `preconditions.reinstall`) |
| `--alert-handling / --no-alert-handling` | scenario › config › on | override every scenario's `alertHandling` — the vision guard that dismisses system alerts the iOS backend cannot see; omit and it resolves each scenario's value, then the target's `alertHandling` config, then on (uses the configured AI provider — `ANTHROPIC_API_KEY`, or AWS credentials for Bedrock; [recording](recording.md#dismissing-system-alerts-automatically)) |
| `--alert-instruction` | "" | default button instruction, below a scenario's own `alertHandling.instruction` and above the target's `alertHandling` config |
| `--log-predicate` | "" | an NSPredicate narrowing the `deviceLog` stream (e.g. subsystem) |
| `--log-subsystem` | "" | the os_log subsystem for `appTrace` (defaults to the app's `bundleId`) |
| `--network / --no-network` | config › on | collect the app's network exchanges for `request` assertions; omit and it resolves the target's `network` config, then on ([BE-0177](../roadmaps/BE-0177-run-behavior-target-config/BE-0177-run-behavior-target-config.md)). iOS needs BajutsuKit in the app; web observes natively via Playwright, and stubs scenario `mocks` in-process |
| `--workers` | 1 | parallel scenarios over a device pool. On iOS, needs `--udid u1,u2,…` and is capped to that pool size. On web, `--workers N` alone is N parallel browser-context lanes — no `--udid` needed ([BE-0054](../roadmaps/BE-0054-web-backend-completion/BE-0054-web-backend-completion.md)). Each lane carries its own network collector, interval recordings, and (iOS) device control, so network / video / `setLocation` / `push` work the same as a single-device run |
| `--baselines` | `baselines/` beside the scenario | directory of baseline images for `visual` assertions; `baseline: home.png` resolves inside it |
| `--schemas` | `schemas/` beside the scenario | directory of JSON Schema files for `responseSchema` assertions; `schema: items.json` resolves inside it (needs the `schema` extra) |
| `--headed / --no-headed` | app `headless` (headless) | web backend: show the run in a visible, slow-motion Chromium window instead of headless, so you can watch each step (the window opens on the machine running the command). Omit to use the app's `headless` config; iOS ignores it |
| `--progress / --no-progress` | off | stream per-scenario / per-step progress lines to stderr (the `serve` UI consumes these) |
| `--zip` | off | after the run, also write `runs/<id>.zip` — one portable artifact (report + evidence) for CI upload or sharing. Runs **after** the verdict, so it can't affect pass/fail; see [`export`](#export) |
| `--runs-dir` | `runs` | directory to write the run tree into. Lets a caller run from one working directory but persist the run elsewhere — `serve` uses it when the active config is bound from a different tree (a Git checkout or an uploaded bundle) to run from that tree while keeping the run in `serve`'s store ([BE-0073](../roadmaps/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)) |
| `--evidence-store` | "" (also `BAJUTSU_EVIDENCE_STORE`) | after the run, upload the whole run tree to object storage at this URI — `s3://bucket/prefix` (AWS / R2 / MinIO) or `gs://bucket/prefix` (Google Cloud Storage). The remote layout mirrors the local one under the prefix (`<prefix><runId>/…`), so the upload path selects the cloud lifecycle policy (retain main-branch evidence, expire feature-branch evidence). Runs **after** the verdict, so an upload failure is reported as a warning and can't affect pass/fail. Needs the `s3` or `gcs` extra ([BE-0110](../roadmaps/BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)) |
| `--config` | `bajutsu.config.yaml` | the config file |
| `--project` | "" | run a project registered with [`project add`](#project) by name — resolves its stored config source back to a `--config` spec and runs it. The headless trigger a CI/cron step calls, the CLI mirror of `POST /api/projects/<name>/run` ([BE-0225](../roadmaps/BE-0225-config-project-hub/BE-0225-config-project-hub.md)). Mutually exclusive with `--config` |

- Evidence is written to `FileSink(runs/<runId>, udid=..., log_predicate=...)`
  ([evidence](evidence.md#sinks-where-evidence-goes)).
- `runId` is `YYYYMMDD-HHMMSS`.
- Output: `PASS|FAIL  runs/<runId>/manifest.json`. **Exits 0 if every scenario passes, 1 on failure.**
- When the alert guard fires (it is the run's only AI), an `AI usage:` line with the
  token totals it consumed is printed to **stderr** after the result, leaving stdout the single
  machine-readable result line. A run that used no AI prints nothing.

```bash
bajutsu run --target showcase-swiftui --udid <UDID> --backend ios --no-erase            # the app's whole scenarios dir
bajutsu run --scenario demos/showcase/scenarios/smoke.yaml --target showcase-swiftui --no-erase   # one file
```

## `project`

The **config project hub** as a headless CLI — the same store `serve` exposes over HTTP, so a
project registered here is visible in the web hub and vice versa ([BE-0225](../roadmaps/BE-0225-config-project-hub/BE-0225-config-project-hub.md)).
A project is a named binding to a config source; `run --project <name>` (above) resolves and runs
it. The store is the DB when `BAJUTSU_DATABASE_URL` is set, else the on-disk JSON beside the runs
directory (`--runs`, default `runs`). Every command resolves to the single `default` org locally.

```bash
bajutsu project add <name> --config <source>   # register (or rebind) a project; first one becomes active
bajutsu project ls                              # list projects; the active one is marked with a leading '*'
bajutsu project use <name>                      # make a project the active binding
bajutsu project rm <name>                       # deregister a project (its runs are retained on disk)
```

`--config` accepts a local path or a Git spec (`github:owner/repo@ref:path`), the same forms
[`run --config`](#run) takes ([BE-0063](../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source.md)).
The cadence lives in the CI/cron system that calls `run --project`, not in Bajutsu.

## `doctor`

A **runnability gate** + the **convention score** for the current screen (AI-independent;
[configuration](configuration.md#doctor-the-convention-score)).

```bash
bajutsu doctor --target <name> [--udid booted] [--backend ...] [--config ...]
```

- First the env gate (`preflight.py`): the required CLIs for the actuator (`xcrun` / `xcodebuild`
  for XCUITest) and a **booted Simulator**, printed as a ✓/✗ checklist. A missing
  check **exits 1** (fail fast with a fixable hint).
- Then `query()`s via the actuator and renders `score(elements, idNamespaces)`. **Exits 1 when
  the grade is Blocked, 0 otherwise.**

## `audit`

A **static determinism score** for a scenario — the device-free cousin of `doctor`'s convention
score (AI-independent; [selectors](selectors.md); [BE-0049](../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)).
It reads a scenario file (expanding components / data, like `trace --explain`) and reports, per
scenario, how reproducible it is — without running it.

```bash
bajutsu audit <scenario.yaml> [--json]
```

- Grades each selector on the **stability ladder** ([selectors](selectors.md)): a unique `id` /
  `idMatches` is **stable**; `label` / `labelMatches` / `traits` / `value` is **moderate** (auxiliary,
  no id); an `index` (nth-of-many) is **fragile**. Plus it flags **coordinate gestures**
  (`swipe {from,to}`, which a stable id could replace) and **over-loose waits** (`until:
  screenChanged` / `settled`, which wait for no concrete condition).
- Emits a per-scenario `grade` (`Stable` / `Moderate` / `Fragile`), a stability fraction, and the
  located findings — as text, or `--json` for tooling.
- **Advisory and read-only**: it never runs the scenario, never edits it, and **never gates CI** —
  a successful audit **exits 0 even with findings** (only a missing / unreadable scenario file
  exits 2). A finding is something to harden, not a verdict — the opposite of retry-to-pass, which
  hides flakiness.

Two further modes prove determinism by *observation* rather than static grading:

```bash
bajutsu audit <scenario.yaml> --repeat K --target <name>   # run K times, diff the outcomes
bajutsu audit --history <runs-dir>                         # mine past runs for flakiness
```

- `--repeat K` runs the scenario `K` times under identical preconditions and reports anything whose
  outcome varied (`deterministic` vs `flaky`) — a divergence is a finding to fix, never a retry that
  turns red into green.
- `--history <runs-dir>` is the **longitudinal view**: it groups each scenario's accumulated runs by
  the run's scenario **fingerprint** (the `provenance.scenarioHash` each `manifest.json` carries) and
  classifies each scenario (`flaky` / `deterministic` / `unproven`). A verdict that flipped while the
  fingerprint stayed constant is *true* flakiness — not an edited scenario, since editing changes the
  hash and starts a fresh group. Keying by scenario name as well as fingerprint pins *which* scenario
  in a suite flaked. Runs with no fingerprint (pre-provenance) can't be grouped and are reported as
  skipped. Like the other modes it is read-only and **exits 0 even when it finds flakiness** (only a
  missing runs dir exits 2).

## `coverage`

A **static e2e coverage map** for a suite — the read-only cousin of `doctor`'s convention score
(AI-independent; [BE-0050](../roadmaps/BE-0050-e2e-coverage-map/BE-0050-e2e-coverage-map.md)).
Where `doctor` grades the ids one screen *exposes*, this grades the ids a whole *suite* exercises:
it walks every `*.yaml` in the app's configured `scenarios` dir (expanding components / data), groups
the stable ids they reference by namespace, and measures them against the app's declared
`idNamespaces` ([configuration](configuration.md#doctor-the-convention-score)) — without running anything.

```bash
bajutsu coverage --target <name> [--config ...] [--runs <dir>] [--crawl <screenmap>] [--json] [--html <path>]
```

- Reports the **coverage fraction** (declared namespaces the suite references / declared namespaces),
  the **per-namespace ids** that touch each, the **gap list** (declared namespaces no scenario
  references — what is untested), and **off-namespace ids** (referenced ids whose namespace was never
  declared). As text, or `--json` for tooling.
- A referenced id is any `id` / `idMatches` a scenario addresses — across steps, nested control flow,
  `within` scopes, and assertions.
- **`--runs <dir>`** folds in two **run-evidence** dimensions:
  - **Endpoint coverage**: it reads every `network.json` under the runs dir (the union of observed
    exchanges) and measures how many **observed endpoints** (`METHOD path`) the suite's network
    assertions (`request` / `event` / `requestSequence`) cover. It reports the fraction asserted,
    the **unasserted** observed endpoints (traffic the suite never asserts on), and matchers
    **declared but not observed** in any run.
  - **Observed-id coverage**: it reads every per-step `elements.json` under the runs dir and collects
    the stable ids the runs actually **rendered** (each element's `identifier`; null and empty ids
    are dropped),
    grouping them by the declared namespaces — the run-evidence counterpart to the static id map. It
    reports the per-namespace observed ids, the namespaces **observed in no run**, and observed ids
    whose namespace was never declared (**off-namespace**).

  Omit `--runs` for the static id-namespace map only.
- **`--crawl <screenmap>`** (with `--runs`) folds in a **screens-visited** dimension against an
  autonomous crawl's discovered surface ([BE-0038](../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)):
  the **denominator** is the screens the crawl found (`screenmap.json` nodes; pass the file or its
  run dir), the **numerator** is the screens the run set reached — each per-step `elements.json` is
  fingerprinted with the *same* `crawl.fingerprint`, so a visited screen matches a discovered one.
  It reports the fraction reached and the **unvisited** screens the crawl discovered but no run
  touched. Needs `--runs` for the visited evidence; given `--crawl` without it, the dimension is
  skipped with a warning.
- **`--html <path>`** also writes a **self-contained HTML report** of the same figures (inline CSS,
  no JavaScript, no external asset — it opens straight from disk), with a coverage bar per dimension
  and the gap / off-namespace / unvisited lists called out. The endpoint, observed-id, and
  screens-visited sections render only when `--runs` (and, for screens, `--crawl`) supply them. The
  text (or `--json`) output is unchanged; the path is confirmed on stderr.
- **Advisory and read-only**: it never runs a scenario, never edits anything, and **never gates CI** —
  it **exits 0 even with gaps** (only a missing config / scenarios dir or an unreadable scenario exits
  2). A gap is a namespace to cover, not a verdict.

## `stats`

An **aggregate run-stats dashboard** — the read-only trend *across* many runs, complementing the
per-run `report.html` (AI-independent;
[BE-0102](../roadmaps/BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md)). Where `coverage`
answers *"what surface do we test?"* and `audit` answers *"is a scenario reproducible?"*, `stats`
answers *"how is the whole suite doing over time?"* It aggregates the `manifest.json` of every run
under a directory — no device, no AI, no verdict.

```bash
bajutsu stats --runs <dir> [--json] [--html <path>]
```

- **`--runs <dir>`** is the directory of past runs to aggregate; it reads each run's `manifest.json`
  (unreadable / malformed ones are skipped). A missing directory exits 2.
- Reports, as text or `--json` for tooling:
  - **Pass-rate over time** — overall and per day (the day is parsed from the run id's timestamp
    prefix; a custom-labelled run id has no day and buckets under `(no date)`).
  - **Duration / performance** — total wall-clock across the set (each run's duration is summed from
    its scenarios' `duration_s`) and the **slowest scenarios** by average duration.
  - **Failure hotspots** — the scenarios, steps (`scenario > action`), and assertion kinds that fail
    most, each with its most frequent failure reason.
  - **Flakiness** — each scenario's classification (`flaky` / `deterministic` / `unproven`) reused
    from the [BE-0049](../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
    longitudinal audit.
  - **Volume** — run count per backend, the denominator the rates are read against.
- Scenario series are keyed by the BE-0049 `(scenarioHash, name)` identity, so a verdict that flips at
  a constant fingerprint is true flakiness while an edited scenario starts a fresh series. A run
  without a `provenance.scenarioHash` still counts toward the run-level trend but can't join a
  scenario series (reported as skipped).
- **`--html <path>`** also writes a **self-contained HTML dashboard** of the same figures (inline CSS,
  a minimal inline-SVG trend line, no JavaScript, no external asset — it opens straight from disk).
  The text (or `--json`) output is unchanged; the path is confirmed on stderr.
- **Advisory and read-only**: it never re-runs a scenario, never recomputes a verdict, and **never
  gates CI** — an older manifest missing a newer field renders as "not captured", never as a failure.
  Any threshold a team sets on its numbers is their own informational check.

## `flakiness`

A **ranked flaky-scenario surface** over the run history — the actionable counterpart to `audit
--history` (AI-independent;
[BE-0220](../roadmaps/BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)).
Where `audit --history` labels each scenario `flaky` / `deterministic` / `unproven`, `flakiness`
sorts the whole suite flaky-first and scores how badly each one flips, so a team sees its worst
offenders at a glance — no device, no AI, no verdict.

```bash
bajutsu flakiness --history <runs-dir> [--json] [--window N]   # mine a directory of run manifests
bajutsu flakiness [--org <org>] [--json] [--window N]          # read the serve database (BAJUTSU_DATABASE_URL)
```

- **Two sources, one ranking.** `--history <runs-dir>` mines a directory of past runs' `manifest.json`
  (the CI / scripting form); omitting it reads the serve database (`BAJUTSU_DATABASE_URL`), grouping
  straight from the `provenance.scenarioHash` stamped on each run row. A missing runs directory or an
  unconfigured database exits 2.
- Runs are grouped by `provenance.scenarioHash` and each scenario is scored by its **verdict flip
  rate** — `2·min(passed, failed)/runs` (0 when consistent, 1 at a 50/50 split) — reusing the
  [BE-0049](../roadmaps/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)
  classification. The output is ordered flaky-first, then by descending flip rate, then by run count.
- Each entry carries the newest passing and newest failing run ids, so `--json` consumers (and the
  serve panel) can link straight to the representative evidence on both sides.
- **`--window N`** keeps only each scenario's newest `N` runs before scoring (0, the default, uses the
  whole history); **`--org <org>`** picks the tenant whose runs the database read mines.
- A run with no `provenance.scenarioHash` (a pre-provenance run) or no recorded verdict can't be
  grouped and is reported as skipped, exactly as in `audit --history`.
- **Advisory and read-only**: it only reports flakiness already recorded in history — it re-runs
  nothing, recomputes no verdict, and **never gates CI**.

## `export`

Bundles a finished run into a single portable `.zip` — `report.html` together with `manifest.json`,
`junit.xml`, the executed `scenario.yaml`, and **all** of its evidence (screenshots, video,
`network.json`, …) ([BE-0060](../roadmaps/BE-0060-run-report-zip-export/BE-0060-run-report-zip-export.md)).
The whole `runs/<id>/` tree is rooted under a single `<id>/` folder, so `report.html`'s **relative**
links resolve offline — the report works by double-click, no server.

```bash
bajutsu export <run-id | run-dir> [-o out.zip] [--force]
```

- `<run>` is a run id (resolved under `--runs`, default `runs/`) or a path to a run directory.
- Output defaults to `<id>.zip` beside the run dir; `-o/--output` overrides. It **refuses to
  overwrite** an existing file without `--force` (mirroring how `record` never silently overwrites).
- Pure packaging of what the run already wrote: no device, no AI, no effect on any verdict. It
  archives **strictly the run dir** (never `.env`, config, or anything above it) and inherits the
  run's secret-scrubbing. `bajutsu run --zip` is the same archiver, run inline after the verdict;
  `bajutsu serve` offers it as a **Download** button beside the embedded report.

## `trace`

Inspects a finished run as a **text timeline**: per scenario, steps and observed network
exchanges interleaved chronologically, followed by expectations, app-trace intervals, and an evidence
summary. Read-only (reads the saved `manifest.json` / `network.json` / `appTrace.json`, and
`scenario.yaml` for provenance).

- Each step shows the natural-language phrase it was recorded from — its [`from:`](scenarios.md#from-provenance)
  provenance (BE-0044) — inline as `← "<phrase>"`; a run of steps sharing one phrase is labeled once.
  A hand-authored scenario or an older run without provenance simply shows none.

```bash
bajutsu trace [<run-dir>] [--scenario <substr>] [--runs runs]
bajutsu trace --explain <scenario.yaml>     # pre-run dry run (no device)
```

- With no `<run-dir>`, uses the latest run under `runs/`. `--scenario` filters by name substring.
- **Exits 2** if no run is found.
- **`--explain <scenario.yaml>`** is the *pre-run* counterpart: instead of reading a finished run,
  it previews how the scenario's `capturePolicy` would fire (BE-0028). Action-triggered rules are
  counted exactly and their matching steps listed; `event` / `result` rules are reported as
  runtime-conditional; and a heavy capture (`video` / `deviceLog` / `appTrace` / `network`) on a
  broadly-matching rule is flagged with ⚠ so you can tighten the match before a run pays for it.
  Read-only and deterministic — no device, no LLM. (Components and data rows are expanded;
  config `setup` preludes are not included.) **Exits 2** if the scenario file is missing.

## `report`

Re-renders a finished run's `report.html` (and re-emits `junit.xml`) from its **stored data**, with
the **current** template — no device, no LLM, no re-run
([BE-0068](../roadmaps/BE-0068-regenerable-reports/BE-0068-regenerable-reports.md)). So a
template improvement or a rendering-bug fix reaches past runs without re-executing them; the
verdict is read from the stored model, never recomputed.

```bash
bajutsu report <run-id | run-dir>      # re-render one run
bajutsu report --all [--runs runs]     # re-render every run dir (with a manifest.json) under runs/
```

- The render model is `manifest.json` (a versioned, lossless render input — `schemaVersion`) plus
  the executed `scenario.yaml`; the renderer reads only the run dir. An **older** run renders
  without error, with any newer-only section shown as "not captured" rather than invented.
- It re-presents recorded outcomes — it never re-evaluates an assertion or alters a verdict — so it
  sits inside the determinism contract. `serve` uses the **same renderer on view**: it renders
  `report.html` fresh from each run's stored model on every request (falling back to the baked file
  if the model can't be loaded), so upgrading `serve` refreshes every report with no re-bake.
- **Exits 2** if the run (or, with `--all`, the runs root) has no readable `manifest.json`.

## `triage`

Diagnoses the first **failed** scenario in a run and suggests a minimal fix. This is **advisory** — it
never judges pass/fail (the AI boundary). It assembles failure context (the failing step and its
reason, failed expectations, the element tree nearest the failure, the scenario) and runs a
`TriageAgent`. The default is rule-based (`HeuristicTriageAgent`, no API key): it categorizes the
failure (selector / timing / assertion) and, when a target id is absent but a similar id is on
screen, suggests "did you mean …?" (the classic renamed-id self-heal). `--ai` swaps in a
Claude-backed agent (needs `ANTHROPIC_API_KEY`) that reasons over the same context plus the
failure **screenshot** for richer diagnoses.

An agent may also return a **structured fix** the tool can apply: `renameId`, `addIndex`
(disambiguate an ambiguous match), or `raiseTimeout`. `--apply <scenario-file>` prints it as a
**dry-run diff**; `--write` applies it to the source; `--rerun --target <name>` then re-runs the
patched scenario (`--no-erase`) and reports whether it now passes. The boundary holds: a fix
is applied only when the user opts in after reviewing the diff, and a fragment that no longer matches the
source is a safe no-op.

```bash
bajutsu triage [<run-dir>] [--scenario <substr>] [--runs runs] [--ai]
bajutsu triage [<run-dir>] --ai --apply <scenario-file> [--write] \
               [--rerun --target <name> [--backend ios] [--udid <udid>]]
```

- Defaults to the latest run under `runs/`. **Exits 0** when the run has no failed scenario.
- `--rerun` requires `--write` (nothing to verify otherwise) and `--target`.
- With `--ai`, an `AI usage:` line of the tokens the diagnosis consumed is printed to stderr after
  the diagnosis. The rule-based default uses no AI, so it prints nothing.
- `--json <path>` also writes the diagnosis as JSON (category, summary, suggestions, and the
  structured fix); combined with `--apply`, it includes the fix's unified diff and patched text.
  `--json` itself only writes that JSON file and never touches the scenario source — patching the
  source still requires `--write` (as it always has). The `serve` Web UI uses `--json` to surface
  triage in the browser (BE-0147).

### Cross-run flaky triage (`--flaky`)

Where the default `triage` explains *one* failed run, `--flaky` explains a *pattern*: why one
scenario intermittently passes and fails while its content fingerprint is unchanged
([BE-0220](../roadmaps/BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)).
It is the fix-proposal counterpart to [`flakiness`](#flakiness): `flakiness` ranks *which*
scenarios flip, and `--flaky` diagnoses *why* one of them does and proposes a fix. It reads the
evidence of both the passing and the failing runs of the scenario and reasons about the **delta**
between them (a selector that resolved in one run but was ambiguous in another, a wait that beat a
spinner most of the time, a network response that varied).

```bash
bajutsu triage --flaky --scenario <name> --history <runs-dir> --ai [--apply <file> [--write]] [--json <path>]
```

- **Requires `--ai`.** Cross-run diagnosis has no rule-based agent — the delta reasoning is exactly
  the judgement-free investigator work the LLM does — so `--flaky` without `--ai` exits 2. It stays
  advisory: the runs already decided pass/fail; this only explains and proposes, never on the
  verdict path (prime directive 1).
- `--scenario <name>` picks the flaky scenario (matched as a substring of the run's scenario name,
  resolved to one exact name so two scenarios sharing a substring never mix), and `--history
  <runs-dir>` is a directory of past runs (each with a `manifest.json`) to contrast — the same
  directory shape `bajutsu flakiness --history` and `audit --history` read. Both are required; a
  missing one exits 2.
- The report shows the passing / failing run counts, the fingerprint, the intermittency category
  (`selector-ambiguity` / `timing` / `network-variance` / `state-leak` / `unknown`), and the
  proposed fix as a **reviewable diff** against the current scenario — never applied on its own.
- Reuses the same structured fixes and flags as single-run triage: `--apply` / `--write` patch the
  scenario, `--json` writes the machine-readable result, and any edit that would weaken an assertion
  is flagged as **laxer** ([BE-0023](../roadmaps/BE-0023-self-healing-guards/BE-0023-self-healing-guards.md))
  inline in both surfaces. A full YAML rewrite is deferred to a follow-up; the current fixes are the
  targeted structured edits.
- **Exits 0** when the scenario's history has no failing run or no passing run to contrast
  (nothing to diagnose as flaky) — advisory, like the other read-only modes.

## `record`

Explores toward a goal with AI and **writes the recorded scenario** (Tier 1; [recording](recording.md)).
By default it auto-names a `*.yaml` under the app's configured scenarios dir
(`targets.<name>.scenarios`); pass `--out` to write a specific path instead.

```bash
bajutsu record --target <name> --goal "<natural-language goal>" [--out <file.yaml>] [options]
```

| Option | Default | Description |
|---|---|---|
| `--target` | (required) | the target app |
| `--goal` | (required) | the goal to author (natural language) |
| `--out` | auto-named in config's `scenarios` dir | explicit output path (overrides the app's scenarios dir) |
| `--name` | (from the goal) | file name for the auto-named scenario (ignored when `--out` is given) |
| `--udid` | `booted` | the target Simulator |
| `--backend` | config | actuator order |
| `--erase / --no-erase` | `--erase` | erase before launch (the app must be installed) |
| `--alert-handling` | off | clear prompts during authoring (needs an API key) |
| `--headed / --no-headed` | app `headless` | web backend: author against a visible (headed, slow-motion) browser instead of headless; omit to use the app's `headless` config |
| `--alert-instruction` | "" | the press instruction for the above |
| `--language` | config `ai.language` (`auto`) | AI output language for the authored prose (`from:` provenance, reasoning) — `ja` / `en` / `auto`; overrides `ai.language`, `auto` follows the goal ([BE-0188](../roadmaps/BE-0188-configurable-ai-output-language/BE-0188-configurable-ai-output-language.md)) |
| `--config` | `bajutsu.config.yaml` | config |

- Internally `launch_driver` → `record_loop(driver, goal, ClaudeAgent(), ...)` → `dump_scenarios`.
- Output: `recorded <N> steps -> <path>`. **Needs `ANTHROPIC_API_KEY`** (`ClaudeAgent`).
- **A Git `--config` is read-only input** ([BE-0063](../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source.md)):
  `record` reads the config from the fetched checkout, but the authored scenario goes **local** — with
  no `--out` it auto-names under the **current directory** (not the checkout's `scenarios` dir, which
  is the read-only SHA-keyed cache), and an `--out` inside the checkout is refused. Review the file
  and commit it to the repository through normal git.
- An `AI usage:` line with the tokens the authoring (and any alert-guard) AI consumed follows on
  stderr.

## `crawl`

Explores the app **breadth-first** and writes a **screen map** of the reachable screens and the
transitions between them (Tier 1; [BE-0038](../roadmaps/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)). Unlike `record`, which is
*goal-directed* — AI explores toward one natural-language goal and writes one scenario — `crawl`
is *systematic discovery*: it visits the screens it can reach and reports what it found. The
exploration engine is **deterministic** (a screen's identity and the order candidate actions are
tried are pure functions of the element tree); **no AI** is involved, and it is **never a
pass/fail gate**.

```bash
bajutsu crawl --target <name> [--max-screens N] [--max-steps N] [--out <dir>] [options]
```

| Option | Default | Description |
|---|---|---|
| `--target` | (required) | the target app |
| `--max-screens` | `50` | stop after discovering this many distinct screens |
| `--max-steps` | `200` | stop after taking this many actions |
| `--udid` | `booted` | the target Simulator — or a comma list (`A,B,C`) for a parallel pool (see `--workers`) |
| `--workers` | `1` | crawl with this many workers at once, sharing one screen map: across this many simulators on iOS ([BE-0064](../roadmaps/BE-0064-parallel-crawl/BE-0064-parallel-crawl.md), capped to the `--udid` devices) or this many browser processes on web ([BE-0077](../roadmaps/BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md)). `1` = single-worker crawl |
| `--backend` | config | actuator order |
| `--erase / --no-erase` | `--erase` | erase before launch (the app must be installed) |
| `--alert-handling / --no-alert-handling` | `--alert-handling` | dismiss unexpected OS prompts while crawling (so they aren't read as crashes; uses the configured AI provider — `ANTHROPIC_API_KEY`, or AWS credentials for Bedrock) |
| `--headed / --no-headed` | app `headless` | web backend: crawl a visible (headed, slow-motion) browser instead of headless; omit to use the app's `headless` config |
| `--language` | config `ai.language` (`auto`) | AI output language for the guide's streamed reasoning — `ja` / `en` / `auto`; overrides `ai.language`, `auto` stays English for crawl ([BE-0188](../roadmaps/BE-0188-configurable-ai-output-language/BE-0188-configurable-ai-output-language.md)) |
| `--out` | `runs/<timestamp>` | run dir the screen map is written into |
| `--continue` | off | continue a past run (point `--out` at it) — re-explore **every** screen it left with untried operations, not one branch; raise `--max-screens`/`--max-steps` to go deeper, and `--workers`/`--udid` run the continuation in parallel ([BE-0181](../roadmaps/BE-0181-crawl-continuation/BE-0181-crawl-continuation.md)) |
| `--config` | `bajutsu.config.yaml` | config |

- **A Git `--config` is read-only input** ([BE-0063](../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source.md)):
  `crawl` reads the config from the fetched checkout, but the screen map / screenshots go to the local
  `--out` run dir (default `runs/<timestamp>`), never into the read-only SHA-keyed cache; an `--out`
  inside the checkout is refused.
- Traversal is by **deterministic replay**, not in-place backtracking: to revisit a known screen
  the crawl relaunches the app to a clean start and replays the shortest recorded path to it,
  then takes the next untried action — the same way `run` reaches any state.
- **Continue a past crawl** ([BE-0181](../roadmaps/BE-0181-crawl-continuation/BE-0181-crawl-continuation.md)):
  a crawl stops on `--max-screens`/`--max-steps` far more often than on `completed`, so
  `bajutsu crawl --out <existing run> --continue` picks the run's map back up and re-explores
  **every** screen it left with untried operations — the whole remaining frontier — instead of
  re-crawling from the entry screen with a bigger budget. It needs no extra saved state: each such
  screen's recorded path (`paths`) is replayed and its still-untried operations re-derived by the
  **deterministic** guide (`candidate_actions`) and matched against the saved `plan`. Because the
  reconstruction is deterministic, it reconstructs only the deterministically derivable operations;
  **AI-only operations are intentionally not reconstructed** — for an AI-guided run, the richer
  operations the model proposed (a realistic `fill`/`type`, a vision-located `tap_point`) are not
  re-seeded, only the deterministic taps/inputs are. A frontier that reconstructs to nothing is left
  untouched (the saved map keeps its `plan` and stop reason) rather than reported as completed. Raise
  `--max-screens`/`--max-steps` to go deeper, and `--workers`/`--udid` run the continuation across the
  same parallel pool a fresh crawl uses. This is distinct from the single-branch resume the Web UI's
  pruned-branch "tap to resume" fires (which re-explores one named pruned operation); `--continue`
  and that resume are mutually exclusive.
- Disabled controls (`notEnabled`) are reported per screen as `blocked` rather than tapped. To
  enumerate transitions the crawl explores the **combinations** of control states: it tries each
  empty text field (and toggles each switch, and switches each tab of a tab bar) independently,
  and — when several fields are empty —
  also a **compound fill** of them at once. The compound matters because a control can stay
  disabled until *several* fields are valid, and an intermediate single fill is often invisible (a
  masked password exposes no value), so filling one at a time can't reach the all-filled state.
  Crawl is **AI-driven**: it first inspects the screen deterministically, then hands those
  operations to Claude to reason about and **combine**, proposing **realistic inputs** (a valid
  email, a password meeting the rules, all of a form's fields in one fill) to enable controls whose
  precondition isn't obvious, plus operations on id-less elements, narrating its reasoning into the
  run log. The AI also handles a **tab
  bar whose individual tabs the accessibility tree can't address** — the iOS accessibility tree can
  surface a SwiftUI TabView as a single "Tab Bar" group with no per-tab identifiers, so the bar is visible but its tabs can't
  be tapped by selector. When that bar is present (and no tab is already addressable by id), it
  locates the tabs by vision — the same fallback the alert guard uses — and taps each by coordinate,
  still switching tabs before drilling in. (UIKit tab bars, whose tabs the accessibility tree exposes as individual
  elements, are a planned refinement — for now they fall back to the same vision path.) The AI only
  chooses *what to try* — screen identity, transitions, and crashes stay deterministic, so the crawl
  is never a verdict (it never gates CI).
- Output: `<out>/screenmap.json`, a JSON graph of `nodes` (screens — fingerprint, kind, ids,
  candidate actions, plus `blocked` disabled controls), `edges` (transitions), `crashes` (action
  paths that collapsed the app UI), `alerts` (OS prompts the guard dismissed mid-crawl — the
  triggering path + the button tapped), `plan` (the live frontier: still-untried operations per
  screen, refreshed as the crawl advances so a reader can see what it will try next), and
  `stop_reason` (`completed` / `max_screens` / `max_steps`), plus `<out>/screens/<fingerprint>.png`
  — a screenshot captured for each discovered screen (while the crawl is on it). The map is
  rewritten as the crawl advances, so a reader (the **Crawl** tab in `serve`) can draw it live: each
  screen is a label+info node, and screens that are the same UI in different states (a form empty vs
  filled) collapse into one node you can expand in place. Stops at the first of `--max-screens` /
  `--max-steps`.
- On completion it also writes `<out>/screenmap.html` — a **self-contained** report (inline CSS, no
  JavaScript, no external asset) and the offline counterpart to the live **Crawl** tab: the screens
  laid out in BFS depth columns with their screenshots, the transitions drawn as a static inline SVG
  (amber, with a 🛡️ marker, where a step tapped through an OS alert), and the crash / dismissed-alert
  paths listed below. It sits beside `screenmap.json` and `screens/`, so it opens straight from the
  run dir — share or archive it without the web UI. Read-only and model-free, like the JSON.
- On completion it also writes one `<out>/crashes/crash-NNN.yaml` per faithfully reproducible crash
  — a **repro scenario** built from the crash's recorded action path, directly runnable by `run`, so
  a discovered crash becomes a committed Tier 2 regression after human review. The conversion is
  pure, deterministic, and model-free (`tap` / `type` / `fill` map to their steps). A path that taps a
  normalized coordinate (a vision-located control) has no selector to address, so it emits no
  scenario rather than a lossy one.
- **Parallel pool** ([BE-0064](../roadmaps/BE-0064-parallel-crawl/BE-0064-parallel-crawl.md)):
  with `--workers N` over a `--udid A,B,C` pool the crawl runs across N booted simulators at once,
  all sharing the one screen map and frontier — independent branches explore concurrently and the AI
  guide's round-trips overlap across devices, so wall-clock time falls roughly with the device count.
  Only *scheduling* becomes concurrent: which worker reaches a screen first is timing-dependent, so
  for an app with its own non-determinism the recorded paths and discovery order can vary run to run,
  but screen identity, transitions, and crashes stay the same deterministic functions of the element
  tree (the crawl is still never a verdict). A wedged device drops its work and the others carry on.
  Default `--workers 1` is the single-worker crawl, unchanged. On **web** the same model runs N
  **browser processes** instead of simulators
  ([BE-0077](../roadmaps/BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md)): the
  worker count alone sizes the lane set (the web has no devices), each reset is a fresh browser
  context (the clean-state `erase`), and a browser that wedges is torn down and relaunched so one bad
  browser can't sink the crawl.

### How a screen is identified (the fingerprint)

Every screen is reduced to a **fingerprint** — a short, stable identity that lets the crawl tell a
revisit from a genuinely new screen. This reduction is a pure, deterministic function of the element tree (no
AI, no screenshot pixels), which is what keeps the screen map reproducible: the same screen always
hashes to the same value. Each node records both the fingerprint and its `kind`.

- **Identifier fingerprint (`kind: "id"` — the normal case).** The sorted *set* of accessibility
  identifiers present on the screen, hashed. Keying on *which* identifiers are present — not their
  on-screen text — makes the identity stable across locales and data changes: a list showing
  different rows, or a label in another language, is still the same screen. Each identifier is then
  tagged with its **interactive state**, but only when that state departs from the default, so the
  same layout in a different state hashes differently and the crawl explores the combinations:
  - `!` — a **disabled** control (`notEnabled`),
  - `=` — a **filled** text input (a field that now holds a value),
  - `+` — a **selected / toggled** control (a switch on, a tab selected).

  An enabled, empty, unselected screen contributes just the bare identifiers, so a screen with no
  such state hashes exactly as its plain identifier set would. This is why filling a form or
  flipping a switch yields a *new* node: it is a distinct, separately-explorable state, which is how
  the crawl discovers an action that only becomes available once a precondition is met (e.g. a
  submit button enabled after every field is filled).
- **Structural fingerprint (`kind: "structural"` — the fallback).** When a screen carries fewer than
  two accessibility identifiers, the id set is too thin to identify it reliably, so the crawl hashes
  the **actionable elements' traits bucketed by coarse on-screen position** instead. This is less
  stable — layout jitter can shift it, and unrelated screens with a similar control skeleton can
  collide — so it is flagged as `structural` to signal the identity is approximate. Raising
  accessibility-identifier coverage (see `doctor`) moves a screen back onto the stable id scheme.
- **Display grouping is a separate, view-only step.** The fingerprint above is the exact, *per-state*
  identity stored in the map. The **Crawl** tab additionally *groups* nodes that are the same screen
  in a different transient state — an empty vs filled form, a toggle, or an alert/overlay that adds a
  few elements — into one expandable unit, so the graph stays readable. That grouping never changes
  the fingerprints or what the crawl explores; it only collapses the drawing.

### Web backend (`--backend web`)

The crawl runs against a web app the same way it runs against the Simulator — the exploration
engine is platform-neutral, so `bajutsu crawl --target <web-app> --backend web` produces the same
`screenmap.json`, screenshots, and crash list. The web app is identified by `baseUrl` (not
`bundleId`), and because a browser needs no Mac or emulator a web crawl runs on the Linux
`make check` / CI gate. Three things differ from iOS, and all stay deterministic
([BE-0066](../roadmaps/BE-0066-web-crawl/BE-0066-web-crawl.md)):

- **Clean start = re-navigate.** There is no app process to relaunch; returning to a clean state
  is `page.goto(baseUrl)` against a fresh browser context (the `erase` equivalent, ~free), the
  same lifecycle seam `run` uses.
- **Crash detection.** The iOS signal (the accessibility tree collapsing) does not exist on the
  web, so the crawl uses the browser's own deterministic signals instead: an uncaught JS exception
  (`pageerror`), a 4xx/5xx main-frame navigation, or a blank document. Each is a machine fact (an
  event, a status number, an empty element set) — no model judges whether the page "looks broken".
- **Dialogs instead of OS alerts.** The web has no OS prompts; it has JS dialogs (`alert` /
  `confirm` / `beforeunload`). They are auto-handled by a fixed, model-free policy (dismiss) and
  recorded in `alerts`, replacing the iOS vision alert guard. `--alert-handling` and the vision
  path are iOS-only; `--headed` applies (watch the crawl in a visible browser).

## `codegen`

Generates a **native test** from a scenario (AI-independent · structural mapping · [codegen](codegen.md)):
**XCUITest** (Swift, iOS) or **Playwright** (TypeScript, web).

```bash
bajutsu codegen <scenario.yaml> --target <name> [--emit xcuitest | playwright] [-o <out>] [--config ...]
```

| Option | Default | Description |
|---|---|---|
| `--emit` | `xcuitest` | output format: `xcuitest` or `playwright` (others exit with code 2) |
| `-o, --out` | `-` | output file. `-` for stdout |

- Config's `launchEnv` goes into the generated test: `app.launchEnvironment` (XCUITest) or seeded
  `localStorage` (Playwright).
- `--emit playwright` requires the app to be a web target (`targets.<name>.baseUrl`); otherwise it exits
  with code 2.
- On file output: `wrote <N> scenario(s) -> <out>`.

## `approve`

Promotes a run's captured screenshots into `visual` **baselines** — the second half of the
visual-regression loop (run → review → approve → re-run). Reads `manifest.json`, so it needs
**no Simulator** and runs headless in CI; it is the CLI twin of the WebUI's **Approve** button.

```bash
bajutsu approve [<run_dir>] --baselines <dir> [--scenario <id>] [--all] [--runs runs]
```

| Option | Default | Description |
|---|---|---|
| `<run_dir>` | latest under `runs/` | the run to approve from |
| `--baselines` | (required) | where to write the promoted baseline PNGs |
| `--scenario` | "" | only this scenario id (e.g. `00-home`, as in the run dir) |
| `--all` | off | also refresh baselines whose comparison already passed (default: only failing / missing) |
| `--runs` | `runs` | runs root, used when `<run_dir>` is omitted |

- Copies each visual check's captured `visual-actual.png` to `<dir>/<baseline>`. **Exits 0**
  when ≥1 baseline was promoted, **1** when there was nothing to approve.

## `serve`

A local web UI to **author, run, and explore** — a Tier-1 convenience, **not part of the CI
gate**. Four top-level tabs over the CLI: **Record** authors a scenario from a goal
(`python -m bajutsu record ...`), **Replay** runs a scenario and shows its report
(`python -m bajutsu run ...`), **Crawl** explores the app and draws its screen map live
(`python -m bajutsu crawl ...`), and **Stats** shows the aggregate run-stats dashboard across the
run history (`bajutsu stats`). All run against the **active config**, which you open from
the file browser, a Git repository, or an uploaded `.zip` bundle (see "Open config" below).
Each request spawns the CLI per request on a background thread,
streams its output, and serves the produced `runs/<id>/` tree so the report's relative asset
links (and the crawl's `screenmap.json`) resolve. Stdlib only (no web framework); binds
`127.0.0.1`.

```bash
bajutsu serve [--port 8765] [--config bajutsu.config.yaml] [--root .] [--runs runs] [--baselines <dir>]
              [--host 127.0.0.1] [--token <t>] [--max-concurrent-runs 4] [--evidence-store <uri>]
```

- **`--token` (or `$BAJUTSU_SERVE_TOKEN`) — authentication (BE-0051).** With a token set, every
  request must authenticate: API clients send `Authorization: Bearer <token>`; the browser exchanges
  the token once via `POST /api/login` (the UI prompts on a 401), which sets an HttpOnly, SameSite
  session cookie — the token is never put in a URL. **Binding a non-loopback `--host` (e.g. `0.0.0.0`)
  requires a token**, so the server is never exposed unauthenticated (it exits otherwise). With no
  token the server is open, as before — only safe on loopback. Full multi-user auth (OAuth/RBAC) is
  out of scope here ([BE-0015](../roadmaps/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).
- **CSRF + security headers (BE-0051).** When a token is set, a state-changing POST whose `Origin`
  doesn't match the `Host` is rejected (defense-in-depth atop the `SameSite=Strict` session cookie);
  non-browser clients (no `Origin`) are unaffected. Every response carries `X-Content-Type-Options:
  nosniff`, `X-Frame-Options: DENY`, and `Referrer-Policy: no-referrer`.
- `--config` is **optional**. Omit it and open a `config.yml` from the UI's file browser (an
  "Open config" button); the browser is confined to `--root` (default: the current directory).
  `--scenarios <dir>` is available as an override of the selected app's configured dir.
- **From a Git repository ([BE-0063](../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source.md)).**
  `--config` also accepts a Git source (`github:owner/repo@ref:path`), and the "Open config" dialog
  has a **From a Git repository** field for the same spec: serve materializes the repo subtree at the
  ref into its cache, binds that config, and serves from the checkout root — so the config's relative
  `scenarios` / `appPath` / `build` resolve against the fetched tree. This Git binding is the self-hosted payoff
  ([BE-0016](../roadmaps/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) Tier A):
  point serve at the team's test repository instead of hand-syncing files, and switch branches in the
  UI rather than redeploying. The file browser stays confined to `--root`; the checkout is a managed
  content-addressed cache, and a Git-sourced run confines the config's path fields to the checkout
  root ([BE-0063](../roadmaps/BE-0063-git-config-source/BE-0063-git-config-source.md)).
- `--baselines` sets the visual-regression baselines dir (default: a `baselines/` folder under
  the app's scenarios dir); runs launched from the UI use it, and the report's **Approve** button
  promotes the captured screenshot into it via `POST /api/approve`.
- `--themes <dir>` is a **drop-in theme directory**
  ([BE-0191](../roadmaps/BE-0191-pluggable-theme-system-serve-ui/BE-0191-pluggable-theme-system-serve-ui.md)):
  each `*.css` file adds a selectable UI theme. A theme is declarative — a block of the design
  tokens documented in `bajutsu/templates/serve.themes.css`, with a leading
  `/* bajutsu-theme name: … kind: dark|light */` manifest comment; the theme's id is the filename
  stem and its `[data-theme="<id>"]` selector must match it. No JavaScript, so a dropped-in theme
  sits at the same trust level as your scenarios and config. The built-in `midnight` / `daylight`
  pair is always offered; discovered themes extend it. Scanned once at startup (no live reload). An
  optional `ui.default_theme` in the config sets the initial selection (otherwise the OS scheme is
  followed until the user picks one).
- Pick an app (its scenarios populate the dropdown), set backend / udid / erase / `disable
  alert-dismiss`, hit **Run**; the output streams live and the `report.html` embeds on completion.
- The **Crawl** tab picks an app, a pool of simulators (multi-select, like Replay — two or more
  crawl in parallel, sharing one screen map; [BE-0064](../roadmaps/BE-0064-parallel-crawl/BE-0064-parallel-crawl.md)),
  and a budget (max screens / steps), then `POST /api/crawl` spawns the crawl; the returned run id
  lets the UI poll `runs/<id>/screenmap.json` and draw the screen map as it grows (screens laid out
  in breadth-first layers, transitions as arrows). The **Stop** button aborts it, like Replay.
- The **Stats** tab renders the aggregate run-stats dashboard
  ([BE-0102](../roadmaps/BE-0102-run-stats-dashboard/BE-0102-run-stats-dashboard.md)) — the same
  read-only trend as `bajutsu stats`, live over the server's run history: pass-rate over time,
  slowest and flakiest scenarios, failure hotspots, and run volume. It reuses the same deterministic
  aggregator, reading each run's `manifest.json` from the artifact store (the run-id list comes from
  the system of record when a database is wired, org-scoped, else the artifact store) — no device, no
  AI, no verdict. It is served at `GET /stats` and refreshed with the tab's refresh button.
- The **Flaky** tab renders the ranked flaky-scenario surface
  ([BE-0220](../roadmaps/BE-0220-flaky-suggestion-and-cross-run-fix/BE-0220-flaky-suggestion-and-cross-run-fix.md)) —
  the same read-only ranking as `bajutsu flakiness`, live over the server's run history: scenarios
  sorted flaky-first by verdict flip rate, each row linking to the representative passing and failing
  runs' evidence. When a database is wired it groups straight from the provenance stamp on each run
  row (org-scoped); otherwise it builds the same records from each run's `manifest.json`. No device,
  no AI, no verdict. It is served at `GET /flakiness` and refreshed with the tab's refresh button.
- **Upload a bundle ([BE-0073](../roadmaps/BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)).**
  The "Open config" dialog has a third source, **Upload a bundle**, that lets a browser user **bring
  their own suite** to a hosted `serve` with no file-system access to the host. Drop a `.zip` whose
  layout is just a working local checkout — a `bajutsu.config.yaml`, its `scenarios` tree, and the
  built app binary the config's `appPath` names (a `.app` dir, a zipped `.app`, or an `.ipa`).
  `serve` extracts it into a confined sandbox (a sibling of `runs/`, never `--root`) and **binds it
  as the active config**, exactly like the file-browser and Git sources — so the **Replay / Record /
  Crawl** tabs then run from the extracted tree (the config's relative `appPath` / `scenarios` /
  `baselines` resolve against the bundle). Runs land in `serve`'s own store (`--runs-dir`), not the
  sandbox, so they survive in **History**; the upload's file name and the zip's **sha256** are
  recorded into each run's `manifest.json` (`provenance`), so "what did this run execute?" stays
  answerable. Only one bundle is bound at a time — opening another config (any source) removes the
  sandbox. The bundle carries **no secrets**: `${secrets.*}` resolve from the `serve` host's
  environment as on any run ([BE-0032](../roadmaps/BE-0032-secret-variables/BE-0032-secret-variables.md)),
  and an uploaded config's `build` command is **never** executed on the host (the bundle ships a
  prebuilt binary; DESIGN §1). Extraction is hardened against zip-slip (absolute / `..` / symlink
  entries are rejected) and zip-bombs (entry-count, total-uncompressed, and per-entry
  compression-ratio caps); every target's path fields are confined to the bundle at bind; and binding
  a config is an admin-role action behind the same token auth as every other request — bringing an
  arbitrary binary is only exposed on an authenticated, single-Mac `serve`.
- **Compose from artifacts ([BE-0268](../roadmaps/BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)).**
  A combined bundle couples three pieces of very different change cadence — a large binary that
  changes every build, a small scenario tree that changes every edit, and a config that almost never
  changes — so re-shipping the whole zip for a one-line scenario fix pays for the unchanged binary
  again. The "Open config" dialog's fourth source, **Compose from artifacts**, splits the bundle
  into three **independently uploadable, content-addressed** parts — **Config**, **Scenarios** (a
  `.zip` of the scenario tree **or** a single `.yaml`, told apart by content and, for a single file,
  written into the config's `scenarios` directory with its extension normalized to `.yaml`), and
  **Binary** (a zipped `.app`, an `.ipa`, or an Android `.apk`, extracted or written raw by the
  config's `appPath` suffix) — each stored by the sha256 of
  its bytes (`POST /api/artifacts/{config,scenarios,binary}`). Because a part is addressed by its
  content, an **unchanged** part is skipped entirely: the browser hashes the file and asks
  `GET /api/artifacts/exists` before uploading, so the same binary never travels the wire twice.
  **Compose & load** (`POST /api/compose`) then assembles a chosen `(config, scenarios, binary)`
  **triple** into exactly the same confined tree a combined bundle produces — the **config is the
  sole layout authority**, naming via its own relative `scenarios` / `appPath` where each part lands —
  and binds it as the active config, so the **Replay / Record / Crawl** tabs run from it just as with
  a bundle. A triple whose config needs a part you did not supply is **rejected** before any run
  (never a half-built tree). Swapping one leg and composing again runs a new **combination** (binary A
  vs. B against the same scenarios, or several scenario sets against one binary) with **no re-upload**
  of the shared parts. Each run's `manifest.json` records the triple's provenance as a `compositionId`
  plus one `<kind>Sha` per part, so "what did this run execute?" is answerable to the exact bytes of
  each piece. Every artifact upload and the compose step are admin-role actions behind the same token
  auth, with the same zip-slip / zip-bomb hardening as the combined bundle. (The three-drop-zone UI
  and this compose endpoint are BE-0268's UI slice; the split-vs-combined internal unification of the
  combined `POST /api/upload` is a separate follow-up.)
- The **AI provider for authoring** (Record and Crawl) is one global choice in **Settings → AI
  provider**: **Anthropic API** (`ANTHROPIC_API_KEY`), **Amazon Bedrock** (AWS credentials +
  `BAJUTSU_BEDROCK_MODEL`), or the **Anthropic CLI** (`ant`, a browser-based OAuth/SSO sign-in on your
  Pro/Max/Console seat — BE-0163). `serve` applies it to spawned jobs via `BAJUTSU_AI_PROVIDER`, so
  there is no per-tab picker; every AI path (authoring, the alert guard, triage) uses the one provider.
- **Inline scenario validation in the editor ([BE-0138](../roadmaps/BE-0138-serve-lint/BE-0138-serve-lint.md)).**
  The Author tab's YAML editor validates **as you type**, not only on Save: a debounced `POST /api/lint`
  runs the same `bajutsu lint` checks and returns line-anchored diagnostics, shown as a gutter marker
  on each offending line and a clickable problems list (click a finding to jump to its line). YAML
  parse errors carry the exact line and column; schema/validation errors resolve to their line
  best-effort. The scenario JSON Schema (`GET /api/schema`, the same output `bajutsu schema` prints)
  also drives lightweight key completion (Ctrl/⌘+Space) and hover descriptions. Deterministic and
  AI-free — no device or model runs; Save keeps its own validation guard, so the inline layer only
  surfaces failures earlier.
- If the app's built binary (config `appPath`) is missing, the app's `build` command runs first
  (its output streams into the job log); a build failure aborts the run before it spawns. Set
  `targets.<name>.build` to the shell command that produces `appPath` (e.g. `make -C demos/showcase
  swiftui-build`) to build on demand from the UI without a manual build first.
- A **History** list under the controls shows past runs (newest first, with a pass/fail dot and
  scenario summary); click one to reopen its report. `GET /api/runs` backs it.
- The run subprocess inherits the launch environment (the venv `bin` is prepended to `PATH`). Run
  it from the project root so `bajutsu.config.yaml` resolves.
- **Input validation on `/api/run`.** The scenario must be an existing `*.yaml` **inside the
  selected app's scenarios dir** (no arbitrary host paths or `..` traversal), and `backend` / `udid`
  must be known tokens, not free text — so a request can't run an arbitrary file or smuggle
  surprising argv. This hardening is the prerequisite for hosting `serve` beyond loopback
  ([BE-0015](../roadmaps/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) / [BE-0016](../roadmaps/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md));
  it still binds `127.0.0.1` and has no auth, so don't expose it to an untrusted network yet.
- **`--max-concurrent-runs` (default 4)** caps how many run/record jobs may run at once so one
  caller can't monopolize the scarce device (BE-0051); dispatch over the cap returns **429**. Set
  `0` for unlimited.
- **`--evidence-store <uri>` (or `$BAJUTSU_EVIDENCE_STORE`) — upload each run's evidence
  ([BE-0110](../roadmaps/BE-0110-evidence-store-uri/BE-0110-evidence-store-uri.md)).** Point it at
  `s3://bucket/prefix` or `gs://bucket/prefix` and every completed run's tree is uploaded there,
  keyed `<prefix><evidence_prefix><runId>/…` so the path selects the cloud lifecycle policy. Unlike
  the standalone `run --evidence-store` (which uploads directly with the runner's own credentials),
  **`serve` holds the credentials and never hands them to a worker**: the control plane signs a
  presigned PUT URL per file, and the worker uploads over plain HTTP with no cloud SDK or credentials
  of its own. A caller picks the per-run path by passing `evidence_prefix` in the `POST /api/run`
  body (validated as a safe relative segment); the server prepends its own bucket + base prefix, so
  the run id is always in the key and runs never collide. Upload runs after the verdict, so a failure
  only warns. Needs the `s3` or `gcs` extra **on the server** (the worker needs neither). See
  [self-hosting](self-hosting.md) for the topology.
- **Hosting flags (advanced).** `--emit-launchagent` prints a launchd plist to run `serve` as a
  token-authenticated LaunchAgent on a single Mac; `--backend server` (with `--asgi`) switches to
  the hosted FastAPI control plane. Both are covered in [self-hosting](self-hosting.md).

## `mcp`

Starts an **MCP (Model Context Protocol) server** so an agent (Claude Desktop / Code) can run
scenarios and read run evidence. Needs the optional `bajutsu[mcp]` extra (`fastmcp`).

```bash
bajutsu mcp [--config bajutsu.config.yaml] [--runs runs] [--transport stdio]
```

| Option | Default | Description |
|---|---|---|
| `--config` | `bajutsu.config.yaml` | config the tools resolve targets against |
| `--runs` | `runs` | the runs dir exposed as resources |
| `--transport` | `stdio` | `stdio` (a local agent) or `sse` (HTTP) |

- **Tools**: `bajutsu_run` (deterministic run) and `bajutsu_doctor` (convention score) — both mirror
  the CLI, and no AI enters the verdict.
- **Resources**: a finished run's `manifest.json` / `report.html` / `junit.xml` and any nested
  artifact (`bajutsu://runs/<id>/…`), plus `runs/latest`.

## `worker`

Leases queued runs from Redis and executes them — the execution half of the hosted server backend
([BE-0015](../roadmaps/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md);
[self-hosting](self-hosting.md)). Needs the optional `bajutsu[worker]` extra (`redis` / `rq`); not
needed for local use.

```bash
bajutsu worker [--redis-url <url>] [--queue bajutsu]
```

## `lint`

Validates a scenario file against the grammar **without running it** — the same strict validation
`run` applies at load. Exits 0 if valid, non-zero with the error otherwise.

```bash
bajutsu lint <scenario.yaml>
```

## `schema`

Prints the scenario **JSON Schema** to stdout, for editor integration (autocomplete / inline
validation). No options.

```bash
bajutsu schema > bajutsu.schema.json
```

## Environment variables (.env)

`_bootstrap` (`@app.callback`) loads `.env` before every command (implementation: `bajutsu/dotenv.py`).

- `KEY=VALUE` form. Handles `#` comments, blank lines, an `export ` prefix, and quotes.
- **Never overrides an existing environment variable** (a real value always wins). `.env` is a
  fallback.
- The path defaults to `.env`, changeable via `BAJUTSU_DOTENV`. It is `.gitignore`d.
- Main use: `ANTHROPIC_API_KEY` (for `record`, `crawl`, and the alert guard, which runs by default).
- AI provider (BE-0053): set `BAJUTSU_AI_PROVIDER=bedrock` to reach Claude through **Amazon Bedrock**
  instead of the Anthropic API — authenticated by the standard AWS credential chain (env vars /
  shared profile / instance or role), so **no `ANTHROPIC_API_KEY`** is needed on that path. Set
  `BAJUTSU_BEDROCK_MODEL` to a provider-prefixed model id (e.g. `global.anthropic.claude-opus-4-6-v1`;
  the bare Anthropic id is not a valid Bedrock id) and `AWS_REGION` for the region. Anthropic is the
  default. The same selection drives `record`, `crawl`, `triage`, and the alert guard.
- Anthropic CLI (BE-0163): set `BAJUTSU_AI_PROVIDER=ant` (or `ai.provider: ant`) to reach Claude
  through the official `ant` CLI — run `ant auth login` (a browser-based OAuth/SSO sign-in) so a
  Claude Pro/Max/Console seat is billed instead of an API key; `ANTHROPIC_PROFILE` selects a named CLI
  profile. **No `ANTHROPIC_API_KEY`** is needed, and every AI path keeps full vision.

```bash
# .env — Anthropic (default)
ANTHROPIC_API_KEY=sk-ant-...

# .env — Amazon Bedrock instead (authenticated by your AWS credentials)
BAJUTSU_AI_PROVIDER=bedrock
BAJUTSU_BEDROCK_MODEL=global.anthropic.claude-opus-4-6-v1
AWS_REGION=us-east-1

# .env — Anthropic CLI instead (no API key; run `ant auth login` first)
BAJUTSU_AI_PROVIDER=ant
# ANTHROPIC_PROFILE=work   # optional: select a named ant CLI profile
```

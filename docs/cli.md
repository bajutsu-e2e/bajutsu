**English** · [日本語](ja/cli.md)

# CLI reference

> Implementation: `bajutsu/cli.py` (Typer). The entry point is `bajutsu = "bajutsu.cli:app"` in
> `pyproject.toml`. Every command in this CLI (command-line interface) selects one app with `--app <name>` and points at config with
> `--config` (default `bajutsu.config.yaml`). App-specific differences live in config
> ([configuration](configuration.md)).

Related: [run-loop](run-loop.md) · [recording](recording.md) · [codegen](codegen.md) · [configuration](configuration.md)

---

## Common

- Every command loads `.env` first (`_bootstrap`, below).
- Missing config / undefined app / no actuator → prints a message and exits with **code 2**.
- `--backend` is a comma-separated list (e.g. `idb`). Empty uses config's `backend`. It checks
  availability in order and the **first usable one is the actuator**
  ([drivers](drivers.md#backend-selection-and-the-actuator)).

## `run`

Runs a scenario **deterministically**; pass/fail is machine-only. The only AI component is the **alert guard** (on by default per scenario), which fires only to clear an OS prompt that blocked a step — see [`dismissAlerts`](scenarios.md#dismissalerts-the-system-alert-guard).

```bash
bajutsu run --app <name> [--scenario <file.yaml>] [options]
```

By default `run` loads **every `*.yaml`** in the app's configured scenarios dir
(`apps.<name>.scenarios`, see [configuration](configuration.md)) — so the config alone is enough
to run. Pass `--scenario <file>` to run a single file instead.

| Option | Default | Description |
|---|---|---|
| `--app` | (required) | the target app (config's `apps.<name>`) |
| `--scenario` | config's `scenarios` dir | run one `*.yaml` instead of the app's whole scenarios dir |
| `--backend` | config | actuator order (comma-separated; first usable wins) |
| `--tag` | "" | comma list; run only scenarios carrying any of these tags |
| `--exclude` | "" | comma list; skip scenarios carrying any of these tags |
| `--udid` | `booted` | the target Simulator (comma list = a device pool for `--workers`) |
| `--erase / --no-erase` | per-scenario | override every scenario's `preconditions.erase` (wipe the simulator first); omit to let each scenario decide. The app is reinstalled fresh either way (config `appPath` + `preconditions.reinstall`) |
| `--dismiss-alerts / --no-dismiss-alerts` | per-scenario (on) | override every scenario's `dismissAlerts` — the vision guard that dismisses system alerts idb can't see; omit to let each scenario decide (needs an API key; [recording](recording.md#dismissing-system-alerts-automatically)) |
| `--alert-instruction` | "" | default button instruction (a scenario's own `dismissAlerts.instruction` wins) |
| `--log-predicate` | "" | an NSPredicate narrowing the `deviceLog` stream (e.g. subsystem) |
| `--log-subsystem` | "" | the os_log subsystem for `appTrace` (defaults to the app's `bundleId`) |
| `--network / --no-network` | `--network` | collect the app's network exchanges for `request` assertions (needs BajutsuKit in the app) |
| `--workers` | 1 | parallel scenarios over a device pool; needs `--udid u1,u2,…` (capped to the pool size). Each device carries its own network collector, interval recordings, and device control, so network / video / `setLocation` / `push` work the same as a single-device run |
| `--baselines` | `baselines/` beside the scenario | directory of baseline images for `visual` assertions; `baseline: home.png` resolves inside it |
| `--config` | `bajutsu.config.yaml` | the config file |

- Evidence is written to `FileSink(runs/<runId>, udid=..., log_predicate=...)`
  ([evidence](evidence.md#sinks-where-evidence-goes)).
- `runId` is `YYYYMMDD-HHMMSS`.
- Output: `PASS|FAIL  runs/<runId>/manifest.json`. **Exits 0 if every scenario passes, 1 on failure.**
- When the alert guard actually fires (it is the run's only AI), an `AI usage:` line with the
  token totals it consumed is printed to **stderr** after the result, leaving stdout the single
  machine-readable result line. A run that used no AI prints nothing.

```bash
bajutsu run --app sample --udid <UDID> --backend idb --no-erase            # the app's whole scenarios dir
bajutsu run --scenario demos/features/app/scenarios/smoke.yaml --app sample --no-erase   # one file
```

## `doctor`

A **runnability gate** + the **convention score** for the current screen (AI-independent;
[configuration](configuration.md#doctor-the-convention-score)).

```bash
bajutsu doctor --app <name> [--udid booted] [--backend ...] [--config ...]
```

- First the env gate (`preflight.py`): the required CLIs for the actuator (`xcrun`; `idb` /
  `idb_companion` for idb) and a **booted Simulator**, printed as a ✓/✗ checklist. A missing
  check **exits 1** (fail fast with a fixable hint).
- Then `query()`s via the actuator and renders `score(elements, idNamespaces)`. **Exits 1 when
  the grade is Blocked, 0 otherwise.**

## `trace`

Inspects a finished run as a **text timeline**: per scenario, steps and observed network
exchanges interleaved chronologically, followed by expectations, app-trace intervals, and an evidence
summary. Read-only (reads the saved `manifest.json` / `network.json` / `appTrace.json`).

```bash
bajutsu trace [<run-dir>] [--scenario <substr>] [--runs runs]
```

- With no `<run-dir>`, uses the latest run under `runs/`. `--scenario` filters by name substring.
- **Exits 2** if no run is found.

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
**dry-run diff**; `--write` applies it to the source; `--rerun --app <name>` then re-runs the
patched scenario (`--no-erase`) and reports whether it now passes. The boundary holds: a fix
is applied only when the user opts in after reviewing the diff, and a fragment that no longer matches the
source is a safe no-op.

```bash
bajutsu triage [<run-dir>] [--scenario <substr>] [--runs runs] [--ai]
bajutsu triage [<run-dir>] --ai --apply <scenario-file> [--write] \
               [--rerun --app <name> [--backend idb] [--udid <udid>]]
```

- Defaults to the latest run under `runs/`. **Exits 0** when the run has no failed scenario.
- `--rerun` requires `--write` (nothing to verify otherwise) and `--app`.
- With `--ai`, an `AI usage:` line of the tokens the diagnosis consumed is printed to stderr after
  the diagnosis. The rule-based default uses no AI, so it prints nothing.

## `record`

Explores toward a goal with AI and **writes the recorded scenario** (Tier 1; [recording](recording.md)).
By default it auto-names a `*.yaml` under the app's configured scenarios dir
(`apps.<name>.scenarios`); pass `--out` to write a specific path instead.

```bash
bajutsu record --app <name> --goal "<natural-language goal>" [--out <file.yaml>] [options]
```

| Option | Default | Description |
|---|---|---|
| `--app` | (required) | the target app |
| `--goal` | (required) | the goal to author (natural language) |
| `--out` | auto-named in config's `scenarios` dir | explicit output path (overrides the app's scenarios dir) |
| `--name` | (from the goal) | file name for the auto-named scenario (ignored when `--out` is given) |
| `--udid` | `booted` | the target Simulator |
| `--backend` | config | actuator order |
| `--erase / --no-erase` | `--erase` | erase before launch (the app must be installed) |
| `--dismiss-alerts` | off | clear prompts during authoring (needs an API key) |
| `--alert-instruction` | "" | the press instruction for the above |
| `--config` | `bajutsu.config.yaml` | config |

- Internally `launch_driver` → `record_loop(driver, goal, ClaudeAgent(), ...)` → `dump_scenarios`.
- Output: `recorded <N> steps -> <path>`. **Needs `ANTHROPIC_API_KEY`** (`ClaudeAgent`).
- An `AI usage:` line with the tokens the authoring (and any alert-guard) AI consumed follows on
  stderr. The `claude-code` agent bills no API tokens here, so it shows nothing.

## `crawl`

Explores the app **breadth-first** and writes a **screen map** of the reachable screens and the
transitions between them (Tier 1; [BE-0038](roadmap/README.md)). Unlike `record`, which is
*goal-directed* — AI explores toward one natural-language goal and writes one scenario — `crawl`
is *systematic discovery*: it visits the screens it can reach and reports what it found. The
exploration engine is **deterministic** (a screen's identity and the order candidate actions are
tried are pure functions of the element tree); **no AI** is involved, and it is **never a
pass/fail gate**.

```bash
bajutsu crawl --app <name> [--max-screens N] [--max-steps N] [--out <dir>] [options]
```

| Option | Default | Description |
|---|---|---|
| `--app` | (required) | the target app |
| `--max-screens` | `50` | stop after discovering this many distinct screens |
| `--max-steps` | `200` | stop after taking this many actions |
| `--guide` | `ai` | exploration guide: `ai` (default; Claude proposes operations and realistic inputs) or `off` (deterministic, no AI) |
| `--agent` | `api` | AI backend for `--guide ai`: `api` (Anthropic API, pay-per-token; needs `ANTHROPIC_API_KEY`) or `claude-code` (the Claude Code CLI, drawing on your subscription — text-only, like `record --agent claude-code`) |
| `--udid` | `booted` | the target Simulator |
| `--backend` | config | actuator order |
| `--erase / --no-erase` | `--erase` | erase before launch (the app must be installed) |
| `--dismiss-alerts / --no-dismiss-alerts` | `--dismiss-alerts` | dismiss unexpected OS prompts while crawling (so they aren't read as crashes; needs an API key) |
| `--out` | `runs/<timestamp>` | run dir the screen map is written into |
| `--config` | `bajutsu.config.yaml` | config |

- Traversal is by **deterministic replay**, not in-place backtracking: to revisit a known screen
  the crawl relaunches the app to a clean start and replays the shortest recorded path to it,
  then takes the next untried action — the same way `run` reaches any state.
- Disabled controls (`notEnabled`) are reported per screen as `blocked` rather than tapped. To
  enumerate transitions the crawl explores the **combinations** of control states: it tries each
  empty text field (and toggles each switch, and switches each tab of a tab bar) independently,
  and — when several fields are empty —
  also a **compound fill** of them at once. The compound matters because a control can stay
  disabled until *several* fields are valid, and an intermediate single fill is often invisible (a
  masked password exposes no value), so filling one at a time can't reach the all-filled state.
  `--guide off` types a deterministic placeholder; **`--guide ai`** runs a pipeline — it first
  inspects the screen deterministically, then hands those operations to Claude to reason about and
  **combine**, proposing **realistic inputs** (a valid email, a password meeting the rules, all of
  a form's fields in one fill) to enable controls whose precondition isn't obvious, plus operations
  on id-less elements, narrating its reasoning into the run log. `--guide ai` also handles a **tab
  bar whose individual tabs the accessibility tree can't address** — idb surfaces a SwiftUI TabView
  as a single "Tab Bar" group with no per-tab identifiers, so the bar is visible but its tabs can't
  be tapped by selector. When that bar is present (and no tab is already addressable by id), it
  locates the tabs by vision — the same fallback the alert guard uses — and taps each by coordinate,
  still switching tabs before drilling in. (UIKit tab bars, whose tabs idb exposes as individual
  elements, are a planned refinement — for now they fall back to the same vision path.) The AI only
  chooses *what to try* — screen identity, transitions and crashes stay deterministic, so the crawl
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

### How a screen is identified (the fingerprint)

Every screen is reduced to a **fingerprint** — a short, stable identity that lets the crawl tell a
revisit from a genuinely new screen. This is a pure, deterministic function of the element tree (no
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

## `codegen`

Generates a **native XCUITest** from a scenario (AI-independent · structural mapping · [codegen](codegen.md)).

```bash
bajutsu codegen <scenario.yaml> --app <name> [--emit xcuitest] [-o <out.swift>] [--config ...]
```

| Option | Default | Description |
|---|---|---|
| `--emit` | `xcuitest` | output format (currently `xcuitest` only; others exit with code 2) |
| `-o, --out` | `-` | output file. `-` for stdout |

- Config's `launchEnv` goes into the generated test's `app.launchEnvironment`.
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
gate**. Three top-level tabs over the CLI: **Record** authors a scenario from a goal
(`python -m bajutsu record ...`), **Replay** runs a scenario and shows its report
(`python -m bajutsu run ...`), and **Crawl** explores the app and draws its screen map live
(`python -m bajutsu crawl ...`). Each request spawns the CLI per request on a background thread,
streams its output, and serves the produced `runs/<id>/` tree so the report's relative asset
links (and the crawl's `screenmap.json`) resolve. Stdlib only (no web framework); binds
`127.0.0.1`.

```bash
bajutsu serve [--port 8765] [--config bajutsu.config.yaml] [--root .] [--runs runs] [--baselines <dir>]
```

- `--config` is **optional**. Omit it and open a `config.yml` from the UI's file browser (an
  "Open config" button); the browser is confined to `--root` (default: the current directory).
  `--scenarios <dir>` is available as an override of the selected app's configured dir.
- `--baselines` sets the visual-regression baselines dir (default: a `baselines/` folder under
  the app's scenarios dir); runs launched from the UI use it, and the report's **Approve** button
  promotes the captured screenshot into it via `POST /api/approve`.
- Pick an app (its scenarios populate the dropdown), set backend / udid / erase / `disable
  alert-dismiss`, hit **Run**; the output streams live and the `report.html` embeds on completion.
- The **Crawl** tab picks an app, device, and budget (max screens / steps), then `POST /api/crawl`
  spawns the crawl; the returned run id lets the UI poll `runs/<id>/screenmap.json` and draw the
  screen map as it grows (screens laid out in breadth-first layers, transitions as arrows). The
  **Stop** button aborts it, like Replay.
- If the app's built binary (config `appPath`) is missing, the app's `build` command runs first
  (its output streams into the job log); a build failure aborts the run before it spawns. Set
  `apps.<name>.build` to the shell command that produces `appPath` (e.g. `make -C demos/features
  sample-build`) to build on demand from the UI without a manual build first.
- A **History** list under the controls shows past runs (newest first, with a pass/fail dot and
  scenario summary); click one to reopen its report. `GET /api/runs` backs it.
- The run subprocess inherits the launch environment (the venv `bin` is prepended to `PATH` so
  the `idb` client resolves). Run it from the project root so `bajutsu.config.yaml` resolves.

## Environment variables (.env)

`_bootstrap` (`@app.callback`) loads `.env` before every command (implementation: `bajutsu/dotenv.py`).

- `KEY=VALUE` form. Handles `#` comments, blank lines, an `export ` prefix, and quotes.
- **Never overrides an existing environment variable** (a real value always wins). `.env` is a
  fallback.
- The path defaults to `.env`, changeable via `BAJUTSU_DOTENV`. It is `.gitignore`d.
- Main use: `ANTHROPIC_API_KEY` (for `record` and the alert guard, which runs by default).

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
```

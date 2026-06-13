**English** · [日本語](ja/cli.md)

# CLI reference

> Implementation: `bajutsu/cli.py` (Typer). The entry point is `bajutsu = "bajutsu.cli:app"` in
> `pyproject.toml`. Every command selects one app with `--app <name>` and points at config with
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

Runs a scenario **deterministically**; pass/fail is machine-only. The sole AI is the **alert guard** (on by default per scenario), which fires only to clear an OS prompt that blocked a step — see [`dismissAlerts`](scenarios.md#dismissalerts-the-system-alert-guard).

```bash
bajutsu run <scenario.yaml> --app <name> [options]
```

| Option | Default | Description |
|---|---|---|
| `--app` | (required) | the target app (config's `apps.<name>`) |
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
| `--config` | `bajutsu.config.yaml` | the config file |

- Evidence is written to `FileSink(runs/<runId>, udid=..., log_predicate=...)`
  ([evidence](evidence.md#sinks-where-evidence-goes)).
- `runId` is `YYYYMMDD-HHMMSS`.
- Output: `PASS|FAIL  runs/<runId>/manifest.json`. **Exits 0 if every scenario passes, 1 on failure.**

```bash
bajutsu run demos/features/app/scenarios/smoke.yaml --app sample --udid <UDID> --backend idb --no-erase
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

Inspects a finished run as a **text timeline** — per scenario, steps and observed network
exchanges interleaved chronologically, then expectations, app-trace intervals, and an evidence
summary. Read-only (reads the saved `manifest.json` / `network.json` / `appTrace.json`).

```bash
bajutsu trace [<run-dir>] [--scenario <substr>] [--runs runs]
```

- With no `<run-dir>`, uses the latest run under `runs/`. `--scenario` filters by name substring.
- **Exits 2** if no run is found.

## `triage`

Diagnoses the first **failed** scenario in a run and suggests a minimal fix — **advisory**, it
never judges pass/fail (the AI boundary). Assembles the failure context (the failing step + its
reason, failed expectations, the element tree nearest the failure, the scenario) and runs a
`TriageAgent`. The default is rule-based (`HeuristicTriageAgent`, no API key): it categorizes the
failure (selector / timing / assertion) and, when a target id is absent but a similar id is on
screen, suggests "did you mean …?" (the classic renamed-id self-heal). `--ai` swaps in a
Claude-backed agent (needs `ANTHROPIC_API_KEY`) that reasons over the same context plus the
failure **screenshot** for richer diagnoses.

An agent may also return a **structured fix** the tool can apply — `renameId`, `addIndex`
(disambiguate an ambiguous match), or `raiseTimeout`. `--apply <scenario-file>` prints it as a
**dry-run diff**; `--write` applies it to the source; `--rerun --app <name>` then re-runs the
patched scenario (`--no-erase`) and reports whether it now passes. The boundary holds: a fix
lands only when you opt in after reviewing the diff, and a fragment that no longer matches the
source is a safe no-op.

```bash
bajutsu triage [<run-dir>] [--scenario <substr>] [--runs runs] [--ai]
bajutsu triage [<run-dir>] --ai --apply <scenario-file> [--write] \
               [--rerun --app <name> [--backend idb] [--udid <udid>]]
```

- Defaults to the latest run under `runs/`. **Exits 0** when the run has no failed scenario.
- `--rerun` requires `--write` (nothing to verify otherwise) and `--app`.

## `record`

Explores toward a goal with AI and **writes the recorded scenario to OUT** (Tier 1; [recording](recording.md)).

```bash
bajutsu record <out.yaml> --app <name> --goal "<natural-language goal>" [options]
```

| Option | Default | Description |
|---|---|---|
| `--app` | (required) | the target app |
| `--goal` | (required) | the goal to author (natural language) |
| `--udid` | `booted` | the target Simulator |
| `--backend` | config | actuator order |
| `--erase / --no-erase` | `--erase` | erase before launch (the app must be installed) |
| `--dismiss-alerts` | off | clear prompts during authoring (needs an API key) |
| `--alert-instruction` | "" | the press instruction for the above |
| `--config` | `bajutsu.config.yaml` | config |

- Internally `launch_driver` → `record_loop(driver, goal, ClaudeAgent(), ...)` → `dump_scenarios`.
- Output: `recorded <N> steps -> <out>`. **Needs `ANTHROPIC_API_KEY`** (`ClaudeAgent`).

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

## `serve`

A local web UI to **run a scenario and view its report** — a Tier-1 convenience, **not part
of the CI gate**. Lists the scenarios + apps, spawns `python -m bajutsu run ...` per request on
a background thread, streams its output, and serves the produced `runs/<id>/` tree so the
report's relative asset links resolve. Stdlib only (no web framework); binds `127.0.0.1`.

```bash
bajutsu serve [--port 8765] [--scenarios demos/features/app/scenarios] [--config bajutsu.config.yaml] [--runs runs]
```

- Pick a scenario file + app, set backend / udid / erase / `disable alert-dismiss`, hit **Run**;
  the output streams live and the `report.html` embeds on completion.
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

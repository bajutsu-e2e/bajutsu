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
- `--backend` is comma-separated (e.g. `rocketsim,idb`). Empty uses config's `backend`. It checks
  availability in order and the **first usable one is the actuator**
  ([drivers](drivers.md#backend-selection-and-the-actuator)).

## `run`

Runs a scenario **deterministically** (AI-independent unless `--dismiss-alerts`).

```bash
bajutsu run <scenario.yaml> --app <name> [options]
```

| Option | Default | Description |
|---|---|---|
| `--app` | (required) | the target app (config's `apps.<name>`) |
| `--backend` | config | actuator order (comma-separated; first usable wins) |
| `--udid` | `booted` | the target Simulator |
| `--erase / --no-erase` | `--erase` | `simctl erase` before each test; `--no-erase` sets every scenario's `preconditions.erase` to false |
| `--dismiss-alerts` | off | the safety net that visually dismisses system alerts (needs an API key; [recording](recording.md#dismissing-system-alerts-automatically)) |
| `--alert-instruction` | "" | which button to press instead of dismissing |
| `--log-predicate` | "" | an NSPredicate narrowing the `deviceLog` stream (e.g. subsystem) |
| `--workers` | 1 | ⚠️ **unused** (parallel execution is unimplemented; runs serially) |
| `--config` | `bajutsu.config.yaml` | the config file |

- Evidence is written to `FileSink(runs/<runId>, udid=..., log_predicate=...)`
  ([evidence](evidence.md#sinks-where-evidence-goes)).
- `runId` is `YYYYMMDD-HHMMSS`.
- Output: `PASS|FAIL  runs/<runId>/manifest.json`. **Exits 0 if every scenario passes, 1 on failure.**

```bash
bajutsu run sample/scenarios/smoke.yaml --app sample --udid <UDID> --backend idb --no-erase
```

## `doctor`

Reports the **convention score** for the current screen ([configuration](configuration.md#doctor-the-convention-score)).
AI-independent.

```bash
bajutsu doctor --app <name> [--udid booted] [--backend ...] [--config ...]
```

- `query()`s via the actuator and renders `score(elements, idNamespaces)`.
- **Exits 1 when the grade is Blocked, 0 otherwise.**

> ⚠️ Env / connection gates are unimplemented. The score is computed only against "the currently
> displayed screen."

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

## Environment variables (.env)

`_bootstrap` (`@app.callback`) loads `.env` before every command (implementation: `bajutsu/dotenv.py`).

- `KEY=VALUE` form. Handles `#` comments, blank lines, an `export ` prefix, and quotes.
- **Never overrides an existing environment variable** (a real value always wins). `.env` is a
  fallback.
- The path defaults to `.env`, changeable via `BAJUTSU_DOTENV`. It is `.gitignore`d.
- Main use: `ANTHROPIC_API_KEY` (for `record` and `--dismiss-alerts`).

```bash
# .env
ANTHROPIC_API_KEY=sk-ant-...
```

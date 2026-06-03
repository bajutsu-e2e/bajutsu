# SimPilot

> Natural-language-driven E2E testing for iOS Simulators.
> **Status: pre-alpha** — the deterministic core (M1) is implemented and tested; the
> device-facing backends are in progress. The tool cannot drive a real Simulator yet.

SimPilot takes test scenarios written in (or recorded from) natural language and runs
them against an app on the iOS Simulator: it performs taps / typing / swipes / waits and
verifies the result with **machine-checkable assertions**.

The guiding idea is to keep the LLM out of the CI gate:

- **AI is the author and the failure investigator, never the judge.** It helps *write*
  scenarios (explore + record) and *investigate* failures, but a `run` is fully
  deterministic with no AI involved — pass/fail comes only from machine assertions.
- **Two tiers.** Tier 1 = AI live operation (exploration / authoring). Tier 2 = a
  deterministic runner for CI regression.

Design rationale (in Japanese) lives in [`DESIGN.md`](DESIGN.md).

## Core principles

- **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector
  fails immediately instead of "tapping whatever matched first"; each test starts from a
  clean environment.
- **Stable selectors.** Prefer `accessibilityIdentifier` (non-localized, data-derived);
  coordinates are the last resort.
- **Stability ladder.** UI actions are attempted most-stable-first (semantic tap by id →
  coordinate tap → … ), and the chosen backend is the most stable one available.
- **App-agnostic tool.** Per-app differences live entirely in config (`apps.<name>`); the
  tool, drivers, and runner stay unchanged across apps.
- **Evidence as rules.** "Capture on every X" is normalized into reusable rules so the
  second run reproduces the same evidence without AI.

## Architecture

```
Natural-language scenario (YAML)
        │
        ▼
   Orchestrator  ── observe → act → verify (deterministic; no AI)
        │ abstract driver API (tap/type/swipe/wait/query/screenshot)
        ▼
 RocketSim / idb backends   ← unified behind one Driver interface
        │
        ▼
 Environment Manager (simctl)  +  Mock Server (deterministic network)
        │
        ▼
 Evidence/Trace  →  Reporter (manifest.json + JUnit)
```

## Status

Implemented and covered by tests (runs without a Simulator):

- Driver abstraction and **selector resolution** (the determinism core)
- **Scenario schema** (steps, waits, assertions) with strict validation + YAML loading
- **Assertion evaluation** (exists / value / label / count / enabled / disabled / selected)
- **Tier 2 run loop** (act → wait → verify), tested via an in-memory fake driver
- **Reporting** (`manifest.json` + JUnit XML)
- **Config resolution** (team defaults × per-app)

In progress (needs Xcode + a Simulator):

- `env.py` (simctl wrapper), real `rocketsim` / `idb` backends, evidence capture,
  `doctor --app`, and wiring the CLI `run` to real backends.

## Requirements

- macOS with Xcode (for the iOS Simulator) — required to drive a device
- Python 3.13 (managed via [uv](https://github.com/astral-sh/uv))

## Setup

```bash
uv sync --extra dev      # creates .venv (Python 3.13) and installs deps + dev tools
```

## Usage

The CLI surface (commands are being implemented incrementally):

```bash
simpilot run <scenario.yaml> --app <name> [--backend rocketsim[,idb]] [--udid booted]
simpilot record <scenario.yaml> --app <name>   # explore + record (planned)
simpilot doctor --app <name>                   # environment gates + convention score
```

Per-app settings live in `simpilot.config.yaml`:

```yaml
defaults:
  backend: [rocketsim, idb]   # UI-stability order; first available is the actuator
  device: "iPhone 15"
  locale: ja_JP

apps:
  searchsample:
    bundleId: com.example.SearchSample
    deeplinkScheme: searchsample
    launchEnv: { SEARCH_SHOW_SETTINGS: "1" }
    idNamespaces: [settings, search, result]
```

## Development

```bash
uv run pytest -q          # tests
uv run ruff check .       # lint
uv run mypy simpilot      # type check (strict)
```

## Project layout

```
simpilot/
├── drivers/base.py   # Driver protocol + selector resolution (determinism core)
├── drivers/fake.py   # in-memory fake driver for tests
├── scenario.py       # scenario schema + YAML loading
├── assertions.py     # machine-checkable assertion evaluation
├── orchestrator.py   # deterministic Tier 2 run loop
├── report.py         # manifest.json + JUnit
├── config.py         # team defaults × per-app resolution
└── cli.py            # CLI (typer)
```

## Roadmap

- **M1** — deterministic runner: env (simctl) + drivers + scenarios + assertions +
  lightweight evidence + manifest + per-app config + `run` / `doctor`. Done criteria: the
  same scenario passes on both RocketSim and idb, and the target app is switchable via
  config alone.
- **M2** — the AI loop (record / normalization) + evidence rules + video / device logs +
  reporter.
- **M3** — network (mock) + app traces (os_signpost) + redaction + XCUITest codegen + CI.
- **M4** — self-healing triage (summarize failures, propose minimal scenario diffs; human
  review required).

**English** · [日本語](README.ja.md)

# Bajutsu

> Natural-language-driven E2E testing for iOS Simulators.
> **Status: pre-alpha** — the deterministic core, the AI authoring loop (`record`), the
> evidence subsystem, and XCUITest codegen are all implemented and unit-tested. The
> device-facing backend *execution* (idb) is implemented but not yet validated
> against a real Simulator, so end-to-end runs on a device are still unverified.

Bajutsu takes test scenarios written in (or recorded from) natural language and runs
them against an app on the iOS Simulator: it performs taps / typing / swipes / waits and
verifies the result with **machine-checkable assertions**.

> **The name.** *Bajutsu* (馬術) is Japanese for *horsemanship / equestrianism* — the
> art of mastering a horse. Here the unruly horse is the **iOS Simulator**: flaky timing,
> async transitions, and surprise system alerts that buck a test off course. Bajutsu is
> about taming that — riding the Simulator through a scenario with a steady, deterministic
> hand so it goes exactly where you point it, every time.

The guiding idea is to keep the LLM out of the CI gate:

- **AI is the author and the failure investigator, never the judge.** It helps *write*
  scenarios (explore + record) and *investigate* failures, but a `run` is fully
  deterministic with no AI involved — pass/fail comes only from machine assertions.
- **Two tiers.** Tier 1 = AI live operation (exploration / authoring). Tier 2 = a
  deterministic runner for CI regression.

Design rationale (in Japanese) lives in [`DESIGN.md`](DESIGN.md). Implementation-grounded,
per-feature documentation (in Japanese) lives in [`docs/`](docs/README.md).

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
Natural-language goal ──(record, Tier 1 / AI)──▶ Scenario (YAML) ◀──(hand-edited)
                                                       │
                                                       ▼
   Orchestrator  ── observe → act → verify (run, Tier 2; deterministic, no AI)
        │ abstract driver API (tap/type/swipe/wait/query/screenshot)
        ▼
 idb backend   ← unified behind one Driver interface (fake driver for tests)
        │
        ▼
 Environment Manager (simctl)  +  Mock Server (deterministic network; planned)
        │
        ▼
 Evidence/Trace  →  Reporter (manifest.json + JUnit + HTML)
                                                       │
                                                       ▼
                                  codegen ──▶ equivalent XCUITest (Swift)
```

Three entry points share the scenario format: `record` (AI authoring), `run` (deterministic
replay), and `codegen` (emit a native XCUITest). See [`docs/`](docs/README.md) for the per-feature
breakdown.

## Status

Implemented and covered by tests (~150 unit tests, run without a Simulator):

- Driver abstraction and **selector resolution** (the determinism core)
- **Scenario schema** (steps, waits, assertions) with strict validation + YAML round-trip
- **Assertion evaluation** (exists / value / label / count / enabled / disabled / selected)
- **Tier 2 run loop** (act → wait → verify), tested via an in-memory fake driver
- **Evidence subsystem**: instant captures (screenshot / elements), `video` / `deviceLog`
  interval captures (simctl), and `capturePolicy` trigger rules
- **Reporting** (`manifest.json` + JUnit XML + self-contained HTML)
- **Config resolution** (team defaults × per-app) and **backend selection** (stability order)
- **simctl command layer**, **idb output parsers**, and the **doctor** convention score
- **AI authoring loop** (`record`): Agent abstraction + Claude implementation + system-alert guard
- **XCUITest codegen** (structural mapping; no AI at test time)
- The wired CLI: `run` / `doctor` / `record` / `codegen`

Implemented but not yet validated on a real device (needs Xcode + a Simulator):

- The idb backend's subprocess execution. Its output parsers are tested,
  but the external CLI surface and JSON schema are **assumed** and must be confirmed
  against the installed tool; the simctl launch sequencing is best-effort.

Not yet wired: the external `mockServer` command (superseded by scenario `mocks`), and
self-healing triage (M4). See
[`docs/architecture.md`](docs/architecture.md) for the full implemented-vs-unwired table.

## Requirements

- macOS with Xcode (for the iOS Simulator) — required to drive a device
- Python 3.13 (managed via [uv](https://github.com/astral-sh/uv))

## Setup

```bash
uv sync --extra dev      # creates .venv (Python 3.13) and installs deps + dev tools
```

## Usage

The CLI surface (full reference in [`docs/cli.md`](docs/cli.md)):

```bash
bajutsu run    <scenario.yaml> --app <name> [--backend idb] [--udid booted]
bajutsu record <out.yaml>      --app <name> --goal "..."   # explore + record (Tier 1, needs API key)
bajutsu doctor                 --app <name>                # convention score for the current screen
bajutsu codegen <scenario.yaml> --app <name> -o UITests/Foo.swift   # emit a native XCUITest
```

Per-app settings live in `bajutsu.config.yaml` (the repo ships the `sample` app, below):

```yaml
defaults:
  backend: [idb]   # UI-stability order; first available is the actuator
  device: "iPhone 15"
  locale: en_US

apps:
  sample:
    bundleId: com.bajutsu.sample
    deeplinkScheme: bajutsusample
    launchEnv: { SAMPLE_UITEST: "1" }
    idNamespaces: [home, list, counter, settings, onboarding, auth, nav, comp, ctrl, text, lists]
```

## Development

```bash
uv run pytest -q          # tests
uv run ruff check .       # lint
uv run mypy bajutsu      # type check (strict)
```

## Project layout

```
bajutsu/
├── drivers/base.py        # Driver protocol + selector resolution (determinism core)
├── drivers/fake.py        # in-memory fake driver for tests
├── drivers/idb.py         # idb backend (headless, frame-center coordinate tap)
├── scenario.py            # scenario schema + YAML round-trip
├── assertions.py          # machine-checkable assertion evaluation
├── orchestrator.py        # deterministic Tier 2 run loop
├── runner.py              # config + scenarios -> report; device factory
├── report.py              # manifest.json + JUnit + HTML
├── evidence.py            # capture: instant (screenshot / elements) + Sinks
├── intervals.py           # interval capture (video / deviceLog) via simctl
├── config.py              # team defaults × per-app resolution
├── backends.py            # backend selection + driver construction
├── env.py                 # simctl command layer
├── doctor.py              # convention score
├── agent.py               # authoring Agent abstraction (Tier 1)
├── claude_agent.py        # Claude-backed Agent (forced tool use, prompt cache)
├── record.py              # record loop: explore -> emit a scenario
├── alerts.py              # system-alert guard (vision locator)
├── codegen.py             # scenario -> XCUITest (Swift)
├── dotenv.py              # minimal .env loader
├── _yaml.py               # YAML loader (keeps on/off as strings)
└── cli.py                 # CLI (typer)
```

## Roadmap

- **M1 — done (validated on-device).** Deterministic runner: env (simctl) + drivers +
  scenarios + assertions + lightweight evidence + manifest + per-app config + `run` / `doctor`.
  Done criteria met on a real device: the same id-first scenario
  (`sample/scenarios/cross_backend.yaml`) passes deterministically on idb, with the target app
  switchable via config alone. idb resolves id-first selectors directly from the native
  `AXUniqueId` and actuates by frame-center coordinates.
- **M2 — mostly done.** The AI loop (`record`) + `capturePolicy` evidence rules + `video` /
  `deviceLog` + the reporter (JUnit/HTML). *(Done. Idempotent normalization / provenance
  comments are still light.)*
- **M3 — done bar CI.** XCUITest codegen ✅, app traces (`appTrace` / os_signpost) ✅, redaction
  applied to captured evidence ✅, network **observation** (the in-app collector + `request`
  assertions) ✅, and **deterministic mocks** (scenario `mocks` → offline in-protocol stubs) ✅ —
  all validated on-device. Remaining: CI integration.
- **M4 — started (skeleton).** Self-healing triage: `bajutsu triage` assembles a failed run's
  context and diagnoses it (root cause + suggested fixes; advisory, human review required). The
  default agent is rule-based (renamed-id "did you mean", timing / assertion categories); a
  ClaudeAgent-backed triage drops in behind the same `TriageAgent` protocol (next).

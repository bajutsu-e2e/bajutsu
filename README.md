**English** · [日本語](README.ja.md)

<p align="center">
  <img src="assets/icons/logo.png" alt="Bajutsu - logo" width="300" height="300">
</p>

# Bajutsu

> Natural-language-driven E2E (end-to-end) testing built on a **backend-agnostic driver**: one
> scenario format and one deterministic runner, where **a platform is just a backend** behind that
> one interface. Swap the backend and the same scenarios run on a different target — the iOS
> Simulator (idb) today, a web (Playwright) backend landed, Android next.
> **Status: pre-alpha.** The deterministic core, the AI authoring loop (`record` / `crawl`),
> the evidence subsystem, XCUITest codegen, and self-healing triage are all implemented and
> unit-tested (no Simulator needed). The iOS **idb backend** is **validated
> end-to-end on a real Simulator** — scenarios, evidence capture, and the triage self-heal loop
> all run on-device — and the **web (Playwright) backend** has landed a first slice: a
> deterministic `run` against a browser, on the Linux gate ([`demos/web`](demos/web/README.md)).

Bajutsu takes test scenarios written in (or recorded from) natural language, drives your app —
taps / typing / swipes / waits — and verifies the result with **machine-checkable assertions**.
Everything but one seam is platform-neutral: the scenario format, selector resolution, the
deterministic runner, the evidence subsystem, and the reporter never name a platform. That one seam
is the **backend** — the driver that actuates the UI. Point the runner at a different backend and
the same scenario runs on a different target: the **iOS Simulator** (idb) today, a **browser**
(Playwright) now too, with **Android** next. Choosing a platform is choosing a backend, not adopting
a different tool.

> **The name.** *Bajutsu* (馬術) is Japanese for *horsemanship / equestrianism*. The name
> refers to the sources of test instability the tool tames — flaky timing, async transitions,
> and unexpected system alerts on the **iOS Simulator**. Bajutsu drives the target
> deterministically through a scenario so that each run produces the same result — on the
> Simulator, and on every backend behind the same driver.

The central design decision is to keep the LLM (large language model) out of the CI
(continuous integration) gate:

- **AI is the author and the failure investigator, never the judge.** It helps *write*
  scenarios (explore + record) and *investigate* failures, but a `run` is fully
  deterministic with no AI involved — pass/fail comes only from machine assertions.
- **Two tiers.** Tier 1 = AI live operation (exploration / authoring). Tier 2 = a
  deterministic runner for CI regression.

Design rationale (in Japanese) lives in [`DESIGN.md`](DESIGN.md). Implementation-grounded,
per-feature documentation lives in [`docs/`](docs/README.md) — English, with a Japanese mirror
under [`docs/ja/`](docs/ja/README.md).

## Core principles

- **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector
  fails immediately instead of "tapping whatever matched first"; each test starts from a
  clean environment.
- **Stable selectors.** Prefer a non-localized, data-derived id — `accessibilityIdentifier`
  on iOS, `data-testid` on the web — over text; coordinates are the last resort.
- **Stability ladder.** UI actions are attempted most-stable-first (semantic tap by id →
  coordinate tap → … ), and the chosen backend is the most stable one available.
- **A platform is a backend.** The deterministic core names no platform; the one platform-specific
  seam is the **backend** (idb / playwright / …) behind the `Driver` interface. Add or swap a
  backend and the same scenario format, runner, and CLI target a new platform unchanged —
  per-target and per-platform differences live only in config and the chosen backend.
- **Evidence as rules.** "Capture on every X" is normalized into reusable rules so the
  second run reproduces the same evidence without AI.

## Architecture

```mermaid
flowchart TB
    goal(["🗣️ Natural-language goal"])
    hand(["✍️ Hand-edited"])
    scenario[["📄 Scenario (YAML)"]]

    subgraph tier1["Tier 1 · AI — author and failure investigator"]
        record["record / crawl<br/>explore + author"]
        agent["Claude agent<br/>+ system-alert guard"]
        record <--> agent
    end

    subgraph tier2["Tier 2 · Deterministic run — no AI in the CI gate"]
        orch["Orchestrator<br/>observe → act → verify"]
        driver["Backend-agnostic Driver API<br/>tap · type · swipe · wait · query · screenshot"]
        idb["idb backend<br/>📱 iOS Simulator (simctl)"]
        pw["playwright backend<br/>🌐 web browser"]
        orch --> driver
        driver --> idb
        driver --> pw
    end

    verdict{"Pass / Fail<br/>machine assertions only"}
    report["📊 Reporter<br/>manifest.json · JUnit · HTML"]
    codegen["codegen<br/>→ XCUITest (Swift)"]
    triage["triage<br/>root cause + fixes · advisory"]

    goal --> record
    record ==> scenario
    hand ==> scenario
    scenario ==> orch
    scenario -.-> codegen
    orch --> verdict
    orch --> report
    verdict -->|fail| triage
    triage -.->|suggest edits| scenario

    classDef ai fill:#fde68a,stroke:#d97706,color:#1f2937;
    classDef det fill:#bfdbfe,stroke:#2563eb,color:#1f2937;
    class tier1 ai
    class tier2 det
```

The same flow as text:

```
Natural-language goal ──(record / crawl, Tier 1 · AI)──▶ Scenario (YAML) ◀──(hand-edited)
                                                       │
                                                       ▼
   Orchestrator  ── observe → act → verify (run, Tier 2; deterministic, no AI)
        │ backend-agnostic driver API (tap/type/swipe/wait/query/screenshot)
        ▼
   ┌─ idb backend ───────▶ 📱 iOS Simulator (simctl boots / installs / launches)
   ├─ playwright backend ─▶ 🌐 web browser
   └─ fake driver ────────▶ in-memory (tests, zero-setup demos)
        │
        ▼
 Evidence / Trace  →  Reporter (manifest.json + JUnit + HTML)
                                                       │
                                                       ▼
                                  codegen ──▶ equivalent XCUITest (Swift)
```

Entry points share the scenario format: `record` and `crawl` (AI authoring / exploration),
`run` (deterministic replay), and `codegen` (emit a native XCUITest). See
[`docs/`](docs/README.md) for the per-feature breakdown.

## Status

Implemented and covered by tests (run without a Simulator):

- Driver abstraction and **selector resolution** (the determinism core)
- **Platform-aware backend registry** — `--backend` / `backend:` accept `ios` / `web` / `fake`
  (Android `adb` planned), each expanding to its actuator in stability order
- **Scenario schema**: steps, waits, assertions, reusable components (`use`), control flow
  (`if` / `forEach`), variables (`extract` → `${vars.*}`), parametrization (`data` / `dataFile`),
  network `mocks`, a `network` filter, `capturePolicy` evidence rules, and `redact` — with
  strict validation, YAML round-trip, and a generated JSON Schema
- **Assertion evaluation** (exists / value / label / count / enabled / disabled / selected /
  request / **visual**)
- **Tier 2 run loop** (act → wait → verify), tested via an in-memory fake driver
- **Evidence subsystem**: instant captures (screenshot / elements / actionLog), `video` /
  `deviceLog` interval captures (simctl), network observation + `mocks`, **visual regression**
  (baselines + `approve`), `capturePolicy` trigger rules, and secret **redaction**
- **Reporting** (`manifest.json` + JUnit XML + self-contained interactive HTML)
- **Config resolution** (team defaults × per-target; iOS `bundleId` or web `baseUrl`) and
  **backend selection** (stability order)
- **simctl command layer**, **idb output parsers**, the **Playwright web driver** (first slice),
  and the **doctor** convention score + environment preflight
- **AI authoring**: `record` (goal-directed) and `crawl` (breadth-first screen map) — the Agent
  abstraction with two backends (Anthropic API + Claude Code) + system-alert guard
- **XCUITest codegen** (structural mapping; no AI at test time)
- **Self-healing triage** (root cause + minimal-fix suggestions; advisory, AI optional)
- The wired CLI: `run` / `doctor` / `record` / `crawl` / `codegen` / `trace` /
  `triage` / `approve` / `serve` / `mcp` / `worker` / `lint` / `schema`
- **MCP server** (`bajutsu mcp`): exposes `run` and `doctor` as MCP tools and run evidence
  (manifest / report / JUnit / artifacts) as resources, for Claude Desktop / Code integration
- **Web UI** (`bajutsu serve`): author (`record` / `crawl`), edit, and run scenarios; browse
  reports and every evidence type; approve visual baselines; live job streaming over SSE

Validated on a real Simulator (iPhone 17 Pro, recent iOS):

- The idb backend's subprocess execution — `describe-all` parsing, frame-center
  tap / text / swipe, and the simctl launch sequencing — confirmed against the installed
  `idb` / `idb_companion` by running the `showcase` scenarios, evidence capture, and the
  triage self-heal loop on-device.

Validated in a browser (Linux, no Mac):

- The Playwright backend runs the [`demos/web`](demos/web/README.md) scenarios deterministically
  inside the same gate as CI — proving the core is platform-neutral. Rich-end web capabilities
  (network capture / video / multi-touch / parallel) are still planned ([roadmap](roadmaps/README.md)).

Not yet wired: the external `mockServer` command (superseded by in-scenario `mocks`); the
Android (`adb`) and Flutter backends (planned). See
[`docs/architecture.md`](docs/architecture.md) for the full implemented-vs-unwired table.

## Requirements

- Python 3.13 (managed via [uv](https://github.com/astral-sh/uv)) — the deterministic core and
  the whole gate run anywhere, Linux included
- **For iOS:** macOS with Xcode (the iOS Simulator) plus `idb` / `idb_companion`
- **For web:** any OS with Playwright's Chromium (`playwright install chromium`) — no Mac needed

## Setup

```bash
uv sync --group dev      # creates .venv (Python 3.13) and installs deps + dev tools
```

## Usage

The CLI surface (full reference in [`docs/cli.md`](docs/cli.md)):

```bash
bajutsu run    --target <name> [--scenario file.yaml]        # default: the app's whole scenarios dir
bajutsu record --target <name> --goal "..." [--out file]     # AI explore + record (Tier 1, needs API key/login)
bajutsu crawl  --target <name> [--max-screens N]             # AI breadth-first crawl → screen map (Tier 1)
bajutsu doctor --target <name>                               # environment + convention score for the current screen
bajutsu codegen <scenario.yaml> --target <name> -o UITests/Foo.swift   # emit a native XCUITest
bajutsu approve --baselines <dir> [--scenario s.yaml]     # promote captured screenshots to visual baselines
bajutsu serve  [--port 8765] [--config c.yaml]            # local web UI: author + run + reports (Tier 1)
bajutsu mcp    [--config c.yaml] [--transport stdio]      # MCP server for agent integration (needs `bajutsu[mcp]`)
bajutsu lint   <scenario.yaml>                            # validate a scenario without running it
bajutsu schema                                            # print the JSON Schema for editor integration
```

`trace` (inspect a finished run), `triage` (diagnose a failure), and `worker` (lease queued runs
for the hosted backend) round out the set — see the [CLI reference](docs/cli.md).

> `make serve` (or `scripts/serve.sh`) wraps `bajutsu serve` and installs the idb
> backend's dependencies on demand, so a fresh checkout won't hit
> `no available actuator among ['idb']`. Pass flags via `make serve ARGS="--port 8766"`.

Per-app (and per-platform) settings live in a config file you pass with `--config`; the demos
ship ready-to-run ones (e.g. [`demos/showcase/showcase.config.yaml`](demos/showcase/showcase.config.yaml),
[`demos/web/demo.config.yaml`](demos/web/demo.config.yaml)). An app targets iOS by `bundleId` or
the web by `baseUrl`:

```yaml
defaults:
  backend: [idb]            # stability order; first available backend is the actuator
  device: "iPhone 17 Pro"
  locale: en_US

targets:
  showcase-swiftui:         # iOS app — driven on the Simulator via idb
    bundleId: com.bajutsu.showcase.ios.swiftui
    deeplinkScheme: showcaseswiftui
    launchEnv: { SHOWCASE_UITEST: "1" }
    idNamespaces: [stable, horse, search, log, notice, perm, sys, net]
    scenarios: demos/showcase/scenarios

  web:                      # web app — driven in a browser via Playwright
    baseUrl: "http://127.0.0.1:8787/index.html"
    backend: [web]
    scenarios: demos/web/scenarios
```

## Demos

Runnable demos, all through one entry point — `make -C demos <target>` ([`demos/`](demos/README.md)):

- **[tour](demos/tour/README.md)** — `make -C demos tour`. The whole lifecycle (run → modify →
  diagnose) on a real Simulator, fully deterministic, **no API key**. (It also runs against an
  in-memory fake device with **zero setup**: `uv run python demos/tour/tour.py`.)
- **[features](demos/showcase/README.md)** — `make -C demos features`. The scenario-authoring
  features (tags, parameterized shared steps, secrets) on a real Simulator.
- **[webui](demos/showcase/WEBUI.md)** — `make -C demos webui`. The **Web UI** driving a Simulator
  and collecting every evidence type: screenshots, video, logs, network (observed + mocked),
  visual regression, system-alert handling. The headline demo for iOS developers.
- **[record](demos/showcase/README.md)** — `make -C demos record`. AI authoring with real Claude on
  a booted app, then the modify-and-self-heal (`triage`) loop.
- **[web](demos/web/README.md)** — `make -C demos/web e2e`. The **Playwright backend** running
  scenarios against a static web app — no Mac or Simulator, runs on Linux.

## Development

```bash
make check                # the full gate: format + lint + typecheck + tests (mirrors CI exactly)
uv run pytest -q          # just the tests (no Simulator)
```

See [`CLAUDE.md`](CLAUDE.md) and [`CONTRIBUTING.md`](CONTRIBUTING.md) for the working agreement.

## Project layout

```
bajutsu/
├── drivers/              # Driver protocol + selector resolution (determinism core); fake / idb (iOS) / playwright (web)
├── backends.py           # platform-aware backend registry + driver construction (stability order)
├── scenario/             # scenario schema (models), YAML load/round-trip, expansion, JSON Schema
├── assertions.py         # machine-checkable assertion evaluation
├── interp.py             # ${namespace.key} interpolation over data / vars / secrets
├── orchestrator/         # deterministic Tier 2 run loop (act → wait → verify)
├── runner/               # config + scenarios -> report via a device pool
├── report/               # manifest.json + JUnit + interactive HTML
├── evidence.py           # instant captures (screenshot / elements) + Sinks
├── intervals.py          # interval capture (video / deviceLog) via simctl
├── network.py            # network observation (exchange model + collector)
├── visual.py             # visual-regression image comparison
├── redaction.py          # mask secrets in captured evidence
├── config.py             # team defaults × per-target resolution (iOS bundleId / web baseUrl)
├── env.py                # simctl command layer (iOS environment)
├── preflight.py          # environment runnability gate for doctor / CI
├── doctor.py             # convention score
├── agent.py · agents.py  # authoring Agent abstraction + backend selection (Tier 1)
├── claude_agent.py       # Anthropic-API agent · claude_code_agent.py — Claude Code agent
├── record.py             # record loop: explore -> emit a scenario
├── crawl.py              # autonomous breadth-first crawl -> screen map
├── alerts.py             # system-alert guard (vision locator)
├── codegen.py            # scenario -> XCUITest (Swift)
├── trace.py              # inspect a finished run as a text timeline
├── triage.py             # self-healing triage: diagnose a failed run, propose a fix
├── lint.py               # scenario linter + JSON Schema generation
├── github.py             # GitHub Actions integration for `run`
├── mcp/                  # MCP server (tools + resources for agent integration)
├── serve/                # local web UI (author + run + reports; Tier 1)
├── cli/                  # CLI (typer) — one file per command under cli/commands/
├── dotenv.py             # minimal .env loader
└── _yaml.py              # YAML loader (keeps on/off as strings)
```

## Roadmap

Milestones M1–M4 are complete — the deterministic runner, the AI `record` loop + `capturePolicy`
evidence rules, XCUITest codegen + CI, and self-healing triage — all validated on a real
Simulator (see [Status](#status) above for the implemented surface). Beyond iOS, the **web
(Playwright) backend** has landed its first slice; **Android (`adb`)** and **Flutter** are planned
(see [`docs/multi-platform.md`](docs/multi-platform.md)).

The forward-looking, prioritized backlog (what we want to build next) lives in
[`roadmaps/`](roadmaps/README.md).

## License

Licensed under the [Apache License, Version 2.0](LICENSE). See [`NOTICE`](NOTICE) for attribution.

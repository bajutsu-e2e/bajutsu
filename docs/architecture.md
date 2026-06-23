**English** · [日本語](ja/architecture.md)

# Architecture and module relationships

> Which module does what, where it depends, and **which features described in
> the design ([`DESIGN.md`](../DESIGN.md)) are not yet wired up** in the current code.

Related: [concepts](concepts.md) · the per-feature pages (linked below)

---

## Overview (data flow)

A scenario (authored by AI or by hand) is the shared artifact. `run` replays it deterministically with no AI in the gate. `codegen` and `triage` also consume the scenario.
Tier 1 (AI — yellow) authors and investigates only; Tier 2 (deterministic — blue) decides pass/fail from machine assertions alone.
The whole spine is platform-neutral; the only platform-specific seam is the **backend** the orchestrator drives (idb for iOS, playwright for web, … behind one `Driver` interface), so a new platform is a new backend, not a fork of the core.

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

The [dependency-layer view](#dependencies-layers) below is the same system seen as module layers
rather than data flow.

---

## Module list and roles

The `bajutsu/` package (Python 3.13+, pydantic v2 / typer / anthropic / pyyaml / jinja2).

| Module | Role | Page |
|---|---|---|
| `drivers/base.py` | Driver Protocol + shared types (`Element`/`Selector`/`Point`) + **selector resolution** (the determinism core) | [selectors](selectors.md) / [drivers](drivers.md) |
| `drivers/fake.py` | In-memory `FakeDriver` (for tests without a device) | [drivers](drivers.md#fakedriver) |
| `drivers/idb.py` | idb backend (iOS Simulator; headless, coordinate tap) | [drivers](drivers.md#idb) |
| `drivers/playwright.py` | Playwright web backend (browser; first slice — deterministic run) | [drivers](drivers.md#playwright-web) |
| `scenario/` | Scenario schema (strict pydantic validation) + YAML load / dump (package: `models` / `load` / `expand` / `select` / `serialize`) | [scenarios](scenarios.md) |
| `assertions.py` | Machine assertion evaluation (total function — never raises) | [selectors](selectors.md#assertion-evaluation) |
| `orchestrator/` | The deterministic Tier 2 run loop (act → wait → verify) (package: `loop` / `waits` / `substitution` / `evidence_rules` / `actions`) | [run-loop](run-loop.md) |
| `evidence.py` | Evidence capture (instant / interval) and Sinks | [evidence](evidence.md) |
| `intervals.py` | Interval evidence (video / deviceLog) as simctl child processes | [evidence](evidence.md#interval-evidence-video--devicelog) |
| `report/` | `manifest.json` + JUnit XML + interactive HTML (package: `format` / `manifest` / `rows` / `panels` / `html`) | [reporting](reporting.md) |
| `network.py` | Network collector + in-protocol deterministic mocks | [evidence](evidence.md) |
| `redaction.py` | Redaction of evidence (labels / headers / fields + secret values) | [evidence](evidence.md) |
| `interp.py` | `${ns.key}` interpolation primitive (`params.` / `row.` / `secrets.` / `vars.`) | [scenarios](scenarios.md) |
| `config.py` | Team defaults × per-app resolution (`Effective`) | [configuration](configuration.md) |
| `backends.py` | Backend availability check · actuator selection (platform-aware registry: `ios` / `web` / `fake`) · driver construction | [drivers](drivers.md#backend-selection-and-the-actuator) |
| `env.py` | `simctl` wrapper (erase/boot/launch/openurl/io) | [drivers](drivers.md#environment-management-simctl) |
| `preflight.py` | Runnability gate (required CLIs + a booted Simulator) | [configuration](configuration.md) |
| `runner/` | config + scenarios → report; device pool + launch sequence (package: `pipeline` / `pool` / `launch`) | [run-loop](run-loop.md#runner-the-run-pipeline) |
| `doctor.py` | Convention score (id coverage, etc.) | [configuration](configuration.md#doctor-the-convention-score) |
| `agent.py` · `agents.py` | Authoring Agent abstraction (`Observation`/`Proposal`/`Agent`) + backend selection (`--agent api` / `claude-code`) | [recording](recording.md) |
| `claude_agent.py` | Anthropic API agent (forced tool use · prompt cache) | [recording](recording.md#claude-agents-api-and-claude-code) |
| `claude_code_agent.py` | Claude Code agent (drives the Claude Code CLI) | [recording](recording.md) |
| `record.py` | The record loop (observe → propose → execute → emit) | [recording](recording.md#the-record-loop) |
| `crawl.py` | Autonomous breadth-first crawl → screen map (`crawl_guide` / `crawl_tabs` helpers) | [recording](recording.md) |
| `alerts.py` | System-alert detection / dismissal (vision locator) | [recording](recording.md#dismissing-system-alerts-automatically) |
| `codegen.py` | Scenario → XCUITest (Swift) generation | [codegen](codegen.md) |
| `visual.py` | Visual-regression image comparison (the `visual` assertion) | [evidence](evidence.md) |
| `trace.py` | Text timeline over a saved run (the `trace` command) | [cli](cli.md) |
| `triage.py` | M4 self-heal: rule-based `HeuristicTriageAgent` + structured fixes (`renameId`/`addIndex`/`raiseTimeout`), `--apply`/`--write`/`--rerun` | [cli](cli.md) |
| `claude_triage.py` | Claude-backed `TriageAgent` (`--ai`, failure screenshot) | [cli](cli.md) |
| `github.py` | GitHub helpers (CI, continuous integration) | [ci](ci.md) |
| `serve/` | Local web UI (the `serve` command): author / run / reports | [cli](cli.md) |
| `mcp/` | MCP server: exposes `run`/`doctor` as tools + run evidence as resources | [cli](cli.md) |
| `lint.py` | Scenario linter + JSON Schema generation (`lint` / `schema` commands) | [cli](cli.md) |
| `cli/` | Typer-based CLI; one file per command in `cli/commands/` (`run`/`doctor`/`record`/`crawl`/`codegen`/`trace`/`triage`/`approve`/`serve`/`mcp`/`worker`/`lint`/`schema`) | [cli](cli.md) |
| `dotenv.py` | Minimal `.env` loader (never overrides an existing var) | [cli](cli.md#environment-variables-env) |
| `_yaml.py` | YAML loader that keeps `on`/`off`/`yes`/`no` as strings | [scenarios](scenarios.md#yaml-caveat) |

## Dependencies (layers)

Lower layers are more stable; upper layers depend on lower ones. The core is `drivers/base.py`
(selector resolution), which every execution path depends on.

```
                       cli/             ← user entry (Typer): run / doctor / record / crawl / codegen / trace / triage / approve / serve / mcp / worker / lint / schema
        ┌─────────────┬───┴───────┬───────────────┬───────────┐
     runner/    record.py / crawl.py  codegen.py   trace.py     triage.py / claude_triage.py
        │          (Tier 1 / AI)   (structural)   (timeline)   (self-heal · advisory)
   orchestrator/   agent.py / agents.py / claude_agent.py / claude_code_agent.py / alerts.py   serve/ · github.py (web UI · CI)
        │                 │
   ┌────┼────────┬────────┘
assertions.py  evidence.py ── intervals.py · network.py · visual.py · redaction.py
        │         │
   scenario/    report/      config.py · preflight.py   backends.py   env.py
        │ (interp.py)             │              │            │
        └──────────────┬─────────────┴──────────────┴────────────┘
                       ▼
                drivers/base.py  ←── the determinism core (Element / Selector / resolve_unique)
                       ▲
        ┌──────────────┼───────────────┐
   drivers/fake   drivers/idb    drivers/playwright
```

- `orchestrator/` depends only on `base.Driver` and **is not coupled to any concrete driver**.
  That is why it can be tested with `FakeDriver` without a device, while in production the same
  loop drives idb (iOS) or playwright (web).
- `runner/` provides the factory that launches the app and returns a ready driver,
  decoupling the loop from a real device.
- `scenario/` (the pydantic authoring model) and `drivers/base.py` (the runtime TypedDict)
  are different things. `Selector.as_selector()` converts the former to the latter.

## Test layout

`tests/` holds the **unit-test suite** (`uv run pytest -q`). None require a real Simulator: command
builders are verified as pure functions, and execution paths are tested with `FakeDriver` /
injected runners (`RunFn` · `Spawn` · `Clock`). Real-device E2E against the sample app is
`make -C demos/features e2e` / `make -C demos/features ui-test` ([sample-app](sample-app.md)).

---

## Implementation status

> The design ([`DESIGN.md`](../DESIGN.md)) also includes the future vision. Here we separate
> **what the current code actually runs** from **what is not yet wired up**.

### Implemented (tested; the path works end-to-end in code)

- Selector resolution and ambiguity detection (the determinism core)
- Platform-aware backend registry: `--backend` / `backend:` accept `ios` / `web` / `fake` tokens
  (Android planned), each expanding to its actuator in stability order (`backends.py`)
- The **Playwright web backend** (`drivers/playwright.py`): a first slice — a deterministic `run`
  against a browser, on the Linux gate (`demos/web`); rich-end web capture is planned (BE-0054)
- Scenario schema (strict validation) and YAML round-trip
- Evaluation of the assertion kinds (`exists` / `value` / `label` / `count` / `enabled` / `disabled` / `selected` / `request` / `visual`)
- The Tier 2 run loop (act → wait → verify), verified with `FakeDriver`
- DSL: the `within` selector (geometric scoping), the `relaunch` step (validated on-device),
  reusable `setup` preludes, `locale` applied at launch, and parallel runs (`--workers`) over a
  device pool
- DSL authoring reuse: reusable parameterized components (`use` / `${params.*}`), data-driven
  scenarios (`data` / `dataFile` with `${row.*}`), secret variables (`${secrets.X}` with value
  masking), scenario tags + `--tag` / `--exclude` selection, the `setLocation` / `push` device
  steps, the `doubleTap` action, and file-level + scenario-level `description`
- DSL control flow & data capture: conditional `if` and `forEach` loops (deterministic; the
  condition is a machine assertion), and `extract` (capture an element's value / label / identifier
  into `${vars.*}`)
- DSL device & system actions (iOS): `background`, `clearKeychain`, `clearClipboard`,
  `overrideStatusBar` / `clearStatusBar` (deterministic status bar), and the `http` action for
  test-data setup / webhooks
- Evidence: instant (`screenshot`/`elements`/`actionLog`) + interval (`video`/`deviceLog`/`appTrace`)
  + the network collector (`network.json`) + **visual regression** (`visual` vs. a baseline; the
  `approve` command promotes baselines) + `capturePolicy` firing + **redaction applied** to logs /
  element trees / network exchanges before they are written
- Network observation + **deterministic mocks** (scenario `mocks` → in-protocol stubs, validated
  on-device): `request` assertions, `wait: { until: request }`, and offline stubbed responses
- Reporting (`manifest.json` / `junit.xml` / `report.html`)
- Config resolution (defaults × apps, redact merge) and actuator selection
- The `simctl` command layer · the idb output parser · the `doctor` score + runnability gate
  (`preflight.py`: required CLIs + a booted Simulator)
- The `trace` command (`trace.py`): a text timeline over a saved run (steps + network + appTrace)
- M4 self-healing triage (`triage.py` + `claude_triage.py`): assemble a failed run's context +
  a `TriageAgent` diagnosis (rule-based `HeuristicTriageAgent`, or `--ai` Claude with the failure
  screenshot). An agent can propose a structured fix (`renameId` / `addIndex` / `raiseTimeout`);
  `--apply`/`--write` patches the scenario source (diff-previewed, opt-in) and `--rerun` re-runs it
- The CLI: `run` / `doctor` / `record` / `crawl` / `codegen` / `trace` / `triage` / `approve` / `serve` / `mcp` / `worker` / `lint` / `schema` — with `record` + `crawl` as the Tier 1 AI authoring paths and the alert guard
- AI **crawl** (`crawl.py`): autonomous breadth-first exploration of an app → a screen map (`screenmap.json`)
- The `serve` local web UI (Tier 1): author (`record` / `crawl`), edit, and run scenarios; browse reports and evidence; approve visual baselines; live job streaming — from a browser (not for CI)
- **MCP server** (`bajutsu mcp`): `bajutsu_run` and `bajutsu_doctor` as MCP tools + run evidence as resources, for Claude Desktop / Code integration (optional dependency `fastmcp`)
- **Scenario linter** (`bajutsu lint` / `bajutsu schema`): validate scenarios without running them; JSON Schema output for editor integration
- XCUITest code generation

### Validated on a real Simulator (iPhone 17 Pro, recent iOS)

- The idb backend's subprocess execution — `describe-all` parsing, frame-center tap / text /
  swipe, and the simctl launch sequencing — confirmed against the installed `idb` /
  `idb_companion` by running the sample scenarios, evidence capture, and the triage self-heal
  loop on-device (`make -C demos/features e2e`; the `e2e.yml` CI workflow also exercises the idb smoke path).

### Validated in a browser (Linux, no Mac)

- The Playwright web backend runs the `demos/web` scenarios deterministically inside the same
  `make check` gate as CI (the `web-e2e` job in `ci.yml`), confirming the deterministic core is
  platform-neutral. Rich-end web capture (network / video / multi-touch) is planned (BE-0054); a
  parallel web crawl across N browser processes ([BE-0077](../roadmaps/implemented/BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md)) runs on this same gate.

### Not yet wired (schema/flags exist but have no runtime effect)

| Feature | Status | Location |
|---|---|---|
| `mockServer` (external mock command) | config schema only; the `cmd`/`port` external server is **not implemented** — superseded by scenario `mocks` (declarative in-protocol stubs, implemented) | `config.py` `MockServer` |
| interval evidence (`video` / `deviceLog` / `appTrace`) on the **web** backend | the `capturePolicy` rules parse, but these captures are simctl-based and run on iOS only; the Playwright backend records `screenshot` / `elements` — web rich-end capture (incl. video / network) is planned (BE-0054) | `intervals.py` · `drivers/playwright.py` |

These are also flagged inline on the relevant feature pages.

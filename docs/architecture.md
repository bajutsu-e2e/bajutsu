**English** · [日本語](ja/architecture.md)

# Architecture and module relationships

> Which module does what, where it depends, and — crucially — **which features described in
> the design ([`DESIGN.md`](../DESIGN.md)) are not yet wired up** in the current code.

Related: [concepts](concepts.md) · the per-feature pages (linked below)

---

## Module list and roles

The `bajutsu/` package (Python 3.11+, pydantic v2 / typer / anthropic / pyyaml).

| Module | Role | Page |
|---|---|---|
| `drivers/base.py` | Driver Protocol + shared types (`Element`/`Selector`/`Point`) + **selector resolution** (the determinism core) | [selectors](selectors.md) / [drivers](drivers.md) |
| `drivers/fake.py` | In-memory `FakeDriver` (for tests without a device) | [drivers](drivers.md#fakedriver) |
| `drivers/idb.py` | idb backend (headless, coordinate tap) | [drivers](drivers.md#idb) |
| `scenario.py` | Scenario schema (strict pydantic validation) + YAML load / dump | [scenarios](scenarios.md) |
| `assertions.py` | Machine assertion evaluation (total function — never raises) | [selectors](selectors.md#assertion-evaluation) |
| `orchestrator.py` | The deterministic Tier 2 run loop (act → wait → verify) | [run-loop](run-loop.md) |
| `evidence.py` | Evidence capture (instant / interval) and Sinks | [evidence](evidence.md) |
| `intervals.py` | Interval evidence (video / deviceLog) as simctl child processes | [evidence](evidence.md#interval-evidence-video--devicelog) |
| `report.py` | `manifest.json` + JUnit XML + HTML | [reporting](reporting.md) |
| `config.py` | Team defaults × per-app resolution (`Effective`) | [configuration](configuration.md) |
| `backends.py` | Backend availability check · actuator selection · driver construction | [drivers](drivers.md#backend-selection-and-the-actuator) |
| `env.py` | `simctl` wrapper (erase/boot/launch/openurl/io) | [drivers](drivers.md#environment-management-simctl) |
| `runner.py` | config + scenarios → report. Device factory (launch sequence) | [run-loop](run-loop.md#runner-the-run-pipeline) |
| `doctor.py` | Convention score (id coverage, etc.) | [configuration](configuration.md#doctor-the-convention-score) |
| `agent.py` | Authoring Agent abstraction (`Observation`/`Proposal`/`Agent`) | [recording](recording.md) |
| `claude_agent.py` | Claude implementation (forced tool use · prompt cache) | [recording](recording.md#claudeagent) |
| `record.py` | The record loop (observe → propose → execute → emit) | [recording](recording.md#the-record-loop) |
| `alerts.py` | System-alert detection / dismissal (vision locator) | [recording](recording.md#dismissing-system-alerts-automatically) |
| `codegen.py` | Scenario → XCUITest (Swift) generation | [codegen](codegen.md) |
| `cli.py` | Typer-based CLI (`run`/`record`/`doctor`/`codegen`) | [cli](cli.md) |
| `dotenv.py` | Minimal `.env` loader (never overrides an existing var) | [cli](cli.md#environment-variables-env) |
| `_yaml.py` | YAML loader that keeps `on`/`off`/`yes`/`no` as strings | [scenarios](scenarios.md#yaml-caveat) |

## Dependencies (layers)

Lower layers are more stable; upper layers depend on lower ones. The core is `drivers/base.py`
(selector resolution), which every execution path depends on.

```
                       cli.py            ← user entry (Typer)
        ┌─────────────────┼───────────────────────────┐
     runner.py        record.py                     codegen.py
        │           (Tier 1 / AI)                (structural mapping)
   orchestrator.py   agent.py / claude_agent.py / alerts.py
        │                 │
   ┌────┼────────┬────────┘
assertions.py  evidence.py ── intervals.py
        │         │
   scenario.py  report.py        config.py     backends.py     env.py
        │                            │              │            │
        └──────────────┬─────────────┴──────────────┴────────────┘
                       ▼
                drivers/base.py  ←── the determinism core (Element / Selector / resolve_unique)
                       ▲
        ┌──────────────┴──────────────┐
   drivers/fake                   drivers/idb
```

- `orchestrator.py` depends only on `base.Driver` and **is not coupled to any concrete driver**.
  That is why it can be tested with `FakeDriver` without a device, while in production the same
  loop drives idb.
- `runner.py` provides the factory that "launches the app and returns a ready driver,"
  decoupling the loop from a real device.
- `scenario.py` (the pydantic authoring model) and `drivers/base.py` (the runtime TypedDict)
  are different things. `Selector.as_selector()` converts the former to the latter.

## Test layout

`tests/` holds **150 unit tests** (`uv run pytest -q`). None require a real Simulator: command
builders are verified as pure functions, and execution paths are tested with `FakeDriver` /
injected runners (`RunFn` · `Spawn` · `Clock`). Real-device E2E against the sample app is
`make e2e` / `make ui-test` ([sample-app](sample-app.md)).

---

## Implementation status

> The design ([`DESIGN.md`](../DESIGN.md)) also includes the future vision. Here we separate
> **what the current code actually runs** from **what is not yet wired up**.

### Implemented (tested; the path works end-to-end in code)

- Selector resolution and ambiguity detection (the determinism core)
- Scenario schema (strict validation) and YAML round-trip
- Evaluation of the 7 assertion kinds
- The Tier 2 run loop (act → wait → verify), verified with `FakeDriver`
- DSL: the `within` selector (geometric scoping), the `relaunch` step (validated on-device),
  reusable `setup` preludes, `locale` applied at launch, and parallel runs (`--workers`) over a
  device pool
- Evidence: instant (`screenshot`/`elements`) + interval (`video`/`deviceLog`/`appTrace`) + the
  network collector (`network.json`) + `capturePolicy` firing + **redaction applied** to logs /
  element trees / network exchanges before they are written
- Network observation + **deterministic mocks** (scenario `mocks` → in-protocol stubs, validated
  on-device): `request` assertions, `wait: { until: request }`, and offline stubbed responses
- Reporting (`manifest.json` / `junit.xml` / `report.html`)
- Config resolution (defaults × apps, redact merge) and actuator selection
- The `simctl` command layer · the idb output parser · the `doctor` score + runnability gate
  (`preflight.py`: required CLIs + a booted Simulator)
- The `trace` command (`trace.py`): a text timeline over a saved run (steps + network + appTrace)
- M4 triage skeleton (`triage.py`): assemble a failed run's context + a `TriageAgent` diagnosis;
  default `HeuristicTriageAgent` (rule-based, "did you mean" id self-heal). AI agent: pending
- The CLI `run` / `doctor` / `codegen`, plus `record` (AI authoring) + the alert guard
- XCUITest code generation

### Implemented but not validated on a real device (needs external CLIs)

- The idb backend's subprocess execution. **The output parser is tested, but the external CLI
  surface and JSON schema are "assumed"** and must be confirmed against the installed tool (the note
  at the top of `drivers/idb.py`). The simctl launch sequencing is also best-effort.

### Not yet wired (schema/flags exist but have no runtime effect)

| Feature | Status | Location |
|---|---|---|
| `mockServer` (external mock command) | config schema only; the `cmd`/`port` external server is **not implemented** — superseded by scenario `mocks` (declarative in-protocol stubs, implemented) | `config.py` `MockServer` |

These are also flagged inline on the relevant feature pages.

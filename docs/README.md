**English** · [日本語](ja/README.md)

# Bajutsu documentation

> Implementation-grounded reference for the natural-language-driven iOS E2E testing tool
> (iOS Simulator only). Where [`README.md`](../README.md) is the pitch and [`DESIGN.md`](../DESIGN.md)
> is the design rationale (the *why*), this set of pages explains **what the code actually
> does today**, feature by feature. Forward-looking work (what we want to build next) lives
> in [roadmap.md](roadmap.md).

Bajutsu takes test scenarios written in (or recorded from) natural language, drives an app
on the iOS Simulator (tap / type / swipe / wait), and verifies the result with
**machine-checkable assertions**. The central idea is to **keep AI out of the CI gate**: AI
is the scenario *author* and the failure *investigator*, never the pass/fail *judge*
(see [concepts](concepts.md)).

## The big picture (data flow)

```
Natural-language goal ──(record / Tier 1, AI)──▶ Scenario YAML ◀──(hand-edited)
                                                       │
                                                       ▼
                                  run (Tier 2, no AI; deterministic)
                                                       │
              ┌───────────────────────────────────────┼───────────────────────────────┐
              ▼                                        ▼                                ▼
        Orchestrator                            Driver abstraction              Evidence Sink
   observe → act → verify   ──tap/type/swipe/wait/query──▶  idb / fake
              │                          (simctl boots/launches)                        │
              ▼                                                                         ▼
        Reporter ──────────────▶ runs/<runId>/{manifest.json, junit.xml, report.html}
                                                       │
                                                       ▼
                                  codegen ──▶ equivalent XCUITest (Swift)
```

Which module owns each box, and how they depend on each other, is in [architecture](architecture.md).

## Pages (suggested reading order)

| # | Page | What it covers |
|---|---|---|
| 1 | [concepts](concepts.md) | Design philosophy & core principles (determinism, two tiers, stability ladder, the AI boundary) |
| 2 | [architecture](architecture.md) | Module layout, dependencies, and the **implementation status** (implemented / unwired) |
| 3 | [scenarios](scenarios.md) | Scenario YAML grammar (steps / waits / assertions / capture tokens) = the authoring reference |
| 4 | [selectors](selectors.md) | Selector model and deterministic resolution (0/1/2+ matches); how assertions evaluate = the determinism core |
| 5 | [drivers](drivers.md) | Driver abstraction · idb / fake · capability differences · the simctl environment |
| 6 | [run-loop](run-loop.md) | Orchestrator (observe → act → verify) · waits · retries · run results |
| 7 | [evidence](evidence.md) | Evidence subsystem (instant / interval · capturePolicy · provider · redact) |
| 8 | [reporting](reporting.md) | Reports (manifest.json / JUnit / HTML) and the `runs/` layout |
| 9 | [configuration](configuration.md) | Config layering (defaults × apps) · onboarding a new app · the `doctor` convention score |
| 10 | [recording](recording.md) | AI authoring (Tier 1 `record`) · the Agent abstraction · system-alert handling |
| 11 | [codegen](codegen.md) | Scenario → native XCUITest generation |
| 12 | [cli](cli.md) | Full reference for CLI commands and options |
| 13 | [sample-app](sample-app.md) | The bundled `BajutsuSample` fixture (exercises every primitive) |
| 14 | [ci](ci.md) | Running in CI — the repo's own workflows + the reusable `bajutsu-e2e` action |

## Quick start

```bash
uv sync --extra dev                  # .venv + deps + dev tools
uv run pytest -q                     # ~150 unit tests (no Simulator needed)

# Against the bundled sample (needs a real Simulator)
make sample-build                    # build the fixture app
make e2e                             # run the smoke scenario on the idb backend
```

Minimal CLI:

```bash
bajutsu run    <scenario.yaml> --app <name> [--backend idb] [--udid booted]
bajutsu doctor               --app <name>                # convention score
bajutsu record <out.yaml>    --app <name> --goal "..."   # AI explore + record (needs API key)
bajutsu codegen <scenario.yaml> --app <name> -o UITests/Foo.swift
```

Details in [cli](cli.md).

## How this documentation is written

- **Code is the source of truth.** Statements map to the current implementation (`bajutsu/`),
  with `file.py:line` pointers at key spots.
- **Design vs. implementation gaps are explicit.** Features described in [`DESIGN.md`](../DESIGN.md)
  but not yet wired up (parallel execution, the mock server, `network`/`appTrace` evidence,
  the `trace` command, `relaunch`/`within`, …) are flagged as "not implemented" on each page
  and in the [architecture status table](architecture.md#implementation-status).
- **Languages.** English is primary; a Japanese translation lives under [`ja/`](ja/README.md).
  Code comments / docstrings are in English.

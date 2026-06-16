**English** · [日本語](ja/README.md)

# Bajutsu documentation

> Implementation-grounded reference for the natural-language-driven iOS E2E (end-to-end) testing tool
> (iOS Simulator only). [`README.md`](../README.md) is the introduction and [`DESIGN.md`](../DESIGN.md)
> covers the design rationale; this set of pages explains **what the code actually
> does today**, feature by feature. Planned work is in [the roadmap](roadmap/README.md).

Bajutsu takes test scenarios written in (or recorded from) natural language, drives an app
on the iOS Simulator (tap / type / swipe / wait), and verifies the result with
**machine-checkable assertions**. The central idea is to keep AI out of the CI (continuous
integration) gate: AI is the scenario author and the failure investigator, never the pass/fail
judge (see [concepts](concepts.md)).

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

> **New here? Start with the [Getting started tutorial](getting-started.md)** — a hands-on
> walkthrough (install → unit tests → scenario → device run → report). Then come
> back to the reference pages below.

| # | Page | What it covers |
|---|---|---|
| 1 | [concepts](concepts.md) | Design philosophy & core principles (determinism, two tiers, stability ladder, the AI boundary) |
| 2 | [architecture](architecture.md) | Module layout, dependencies, and the **implementation status** (implemented / unwired) |
| 3 | [scenarios](scenarios.md) | Scenario YAML grammar (steps / waits / assertions / capture tokens) = the authoring reference |
| 4 | [dsl-grammar](dsl-grammar.md) | The **formal grammar** of the scenario DSL (domain-specific language) — EBNF + every validation constraint — the normative spec behind [scenarios](scenarios.md) |
| 5 | [selectors](selectors.md) | Selector model and deterministic resolution (0/1/2+ matches); how assertions evaluate = the determinism core |
| 6 | [drivers](drivers.md) | Driver abstraction · idb / fake · capability differences · the simctl environment |
| 7 | [run-loop](run-loop.md) | Orchestrator (observe → act → verify) · waits · retries · run results |
| 8 | [evidence](evidence.md) | Evidence subsystem (instant / interval · capturePolicy · provider · redact) |
| 9 | [reporting](reporting.md) | Reports (manifest.json / JUnit / HTML) and the `runs/` layout |
| 10 | [configuration](configuration.md) | Config layering (defaults × apps) · onboarding a new app · the `doctor` convention score |
| 11 | [recording](recording.md) | AI authoring (Tier 1 `record`) · the Agent abstraction · system-alert handling |
| 12 | [codegen](codegen.md) | Scenario → native XCUITest generation |
| 13 | [cli](cli.md) | Full reference for CLI commands and options |
| 14 | [sample-app](sample-app.md) | The bundled `BajutsuSample` fixture (exercises every primitive) |
| 15 | [ci](ci.md) | Running in CI — the repo's own workflows + the reusable `bajutsu-e2e` action |
| 16 | [vision](vision.md) | **Forward-looking** — the three axes of growth (reach / scale / authoring) and the constraints all of them respect; the concrete plans now live as items in the [roadmap](roadmap/README.md) |
| 17 | [ai-development](ai-development.md) | Working agreement for AI agents + humans in parallel (the gate, branches, pre-push hook, worktrees) — the long form of [`CLAUDE.md`](../CLAUDE.md) |

## Quick start

```bash
uv sync --extra dev                  # .venv + deps + dev tools
uv run pytest -q                     # 405 unit tests (no Simulator needed)

# Against the bundled sample (needs a real Simulator)
make -C demos/features sample-build                    # build the fixture app
make -C demos/features e2e                             # run the smoke scenario on the idb backend
```

Minimal CLI:

```bash
bajutsu run    --app <name> [--scenario file.yaml]      # default: the app's whole scenarios dir
bajutsu doctor --app <name>                             # convention score
bajutsu record --app <name> --goal "..." [--out file]   # AI explore + record (needs API key)
bajutsu codegen <scenario.yaml> --app <name> -o UITests/Foo.swift
bajutsu serve                                           # local web UI (Tier 1; not for CI)
```

Details in [cli](cli.md).

## How this documentation is written

- **Code is the source of truth.** Statements map to the current implementation (`bajutsu/`),
  with `file.py:line` pointers at key spots.
- **Design vs. implementation gaps are explicit.** Features described in [`DESIGN.md`](../DESIGN.md)
  but not yet wired up (the external `mockServer` command — superseded by scenario `mocks`) are
  flagged as such on each page and in the
  [architecture status table](architecture.md#implementation-status).
- **Languages.** English is primary; a Japanese translation lives under [`ja/`](ja/README.md).
  Code comments / docstrings are in English.

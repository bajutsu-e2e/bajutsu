**English** · [日本語](ja/overview.md)

# Bajutsu documentation

> Implementation-grounded reference for the natural-language-driven end-to-end (E2E) testing tool.
> Its deterministic core is platform-neutral. The one platform-specific seam is the **backend**
> behind a single `Driver` interface, so a new platform is a new backend. The iOS Simulator
> (XCUITest) backend, a web (Playwright) backend, and an Android (adb) backend have all landed.
> Flutter apps run through those same backends unchanged, with no separate Flutter backend.
>
> [`README.md`](../README.md) is the introduction, and [`DESIGN.md`](../DESIGN.md) covers the
> design rationale. This set of pages explains **what the code actually does today**, feature by
> feature. Planned work is in [the roadmap](../roadmaps/README.md).

Bajutsu takes test scenarios written in (or recorded from) natural language, drives an app
(tap / type / swipe / wait), and verifies the result with **machine-checkable assertions**. The
**backend** is the only platform-specific seam, so swapping it runs the same scenarios on the iOS
Simulator (XCUITest) or in a browser (Playwright).

The central idea is to keep AI out of the continuous integration (CI) gate. AI is the scenario
author and the failure investigator, never the pass/fail judge (see [concepts](concepts.md)).

## The big picture of the data flow

![Data-flow diagram: a natural-language goal or hand edit produces a Scenario YAML; Tier 2's Orchestrator runs it deterministically through the backend-agnostic Driver API against XCUITest, adb, or Playwright; the verdict feeds the Reporter and, on failure, triage, which may suggest scenario edits.](assets/diagrams/architecture-data-flow.svg)

[architecture](architecture.md) shows which module owns each box and how the modules depend on
each other, including a dependency-layer view of the same system.

## Pages (suggested reading order)

> **New here? Start with the [Getting started tutorial](getting-started/index.md)**, a hands-on
> walkthrough (install → unit tests → scenario → device run → report). Then return to the
> reference pages below. **On a machine without a Mac** (Linux, Windows, a container), follow the
> [web track](getting-started/web.md) instead. It runs the same loop against a browser with the
> Playwright backend, and needs no Xcode or Simulator.

| # | Page | What it covers |
|---|---|---|
| 1 | [concepts](concepts.md) | Design philosophy & core principles (determinism, two tiers, stability ladder, the AI boundary) |
| 2 | [glossary](glossary.md) | Term-by-term reference for the domain vocabulary; disambiguates the near-synonym clusters (driver / backend / actuator / platform · target / app / device · scenario vs. test · trace vs. triage) |
| 3 | [architecture](architecture.md) | Module layout, dependencies, and the **implementation status** (implemented / unwired) |
| 4 | [developer-guide](developer-guide.md) | A guide to the source tree and how to work in it: file layout, the four contracts, the scenario DSL and selectors, the classes in each layer, and the development and verification routine |
| 5 | [scenarios](scenarios.md) | Scenario YAML grammar (steps / waits / assertions / capture tokens) = the authoring reference |
| 6 | [dsl-grammar](dsl-grammar.md) | The **formal grammar** of the scenario DSL (domain-specific language) — EBNF + every validation constraint — the normative spec behind [scenarios](scenarios.md) |
| 7 | [selectors](selectors.md) | Selector model and deterministic resolution (0/1/2+ matches); how assertions evaluate = the determinism core |
| 8 | [drivers](drivers.md) | Driver abstraction · XCUITest (iOS) / playwright (web) / adb (Android) / fake · capability differences · the simctl environment |
| 9 | [run-loop](run-loop.md) | Orchestrator (observe → act → verify) · waits · retries · run results |
| 10 | [evidence](evidence.md) | Evidence subsystem (instant / interval · capturePolicy · provider · redact) |
| 11 | [reporting](reporting.md) | Reports (manifest.json / JUnit / HTML) and the `runs/` layout |
| 12 | [configuration](configuration.md) | Config layering (defaults × targets) · onboarding a new target · the `doctor` convention score |
| 13 | [recording](recording.md) | AI authoring (Tier 1 `record`) · the Agent abstraction · system-alert handling |
| 14 | [codegen](codegen.md) | Scenario → native XCUITest / Playwright / UI Automator generation |
| 15 | [cli](cli.md) | Full reference for CLI commands and options |
| 16 | [showcase](showcase.md) | The showcase suite — the single iOS fixture (exercises every primitive) |
| 17 | [ci](ci.md) | Running in CI — the repo's own workflows + the reusable `bajutsu-e2e` action |
| 18 | [self-hosting](self-hosting.md) | Run `serve` as a token-authenticated LaunchAgent on a single Mac behind Tailscale (BE-0016 Tier A) |
| 19 | [vision](vision.md) | The three axes of growth (reach / scale / authoring) — how far each has already gotten, and the constraints all of them respect; reach's platform-portability design (selectors, id conventions, phasing) lives in its own section |
| 20 | [ai-development](ai-development.md) | Working agreement for AI agents + humans in parallel (the gate, branches, pre-push hook, worktrees) — the long form of [`CLAUDE.md`](../CLAUDE.md) |
| 21 | [roadmap-workflow](roadmap-workflow.md) | The **ideation → implementation cycle**: the `ideation` skill authors a BE proposal, the `implement-be` skill ships it (placeholder IDs, the Approved → Implemented lifecycle) |
| 22 | [contributor-workflow-tutorial](contributor-workflow-tutorial.md) | **Hands-on** walkthrough of that cycle: one idea from `/ideation` to a merged proposal, then `/implement-be` to a merged PR, with a worked good-vs-bad proposal example and when to use `propose-and-build` |

## Quick start

```bash
uv sync --group dev                  # .venv + deps + dev tools
uv run pytest -q                     # unit tests (no Simulator needed)

# Against the showcase fixture (needs a real Simulator)
make -C demos/showcase swiftui-build                    # build the fixture app
make -C demos/showcase run-swiftui                      # run the scenarios on the iOS (XCUITest) backend
```

Minimal CLI:

```bash
bajutsu run    --target <name> [--scenario file.yaml]      # default: the app's whole scenarios dir
bajutsu doctor --target <name>                             # convention score
bajutsu record --target <name> --goal "..." [--out file]   # AI explore + record (needs API key)
bajutsu codegen <scenario.yaml> --target <name> -o UITests/Foo.swift
bajutsu serve                                           # local web UI (Tier 1; not for CI)
```

[cli](cli.md) lists every command and option.

## How this documentation is written

- **Code is the source of truth.** Each statement maps to the current implementation (`bajutsu/`),
  with `file.py:line` pointers at key spots.
- **Design vs. implementation gaps are explicit.** A feature that [`DESIGN.md`](../DESIGN.md)
  describes but the code does not yet wire up (the external `mockServer` command, superseded by
  scenario `mocks`) is flagged on each page and in the
  [architecture status table](architecture.md#implementation-status).
- **Languages.** English is primary, and a Japanese translation lives under [`ja/`](ja/overview.md).
  Code comments and docstrings are in English.

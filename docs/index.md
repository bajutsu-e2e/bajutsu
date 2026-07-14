---
title: Bajutsu
template: home.html
hide:
  - navigation
  - toc
---

Bajutsu (馬術) takes test scenarios written in — or recorded from — natural language, drives your
app (taps, typing, swipes, waits), and verifies the result with **machine-checkable assertions**.
Everything but one seam is platform-neutral; that seam is the **backend** that actuates the UI, so
the same scenario runs on a different target by swapping it.

## The core stance

- **AI is the author and the failure investigator, never the judge.** AI helps *write* scenarios
  and *investigate* failures, but a `run` is fully deterministic — pass/fail comes only from machine
  assertions, never a model.
- **Two tiers.** Tier 1 is AI live operation (exploration and authoring); Tier 2 is the
  deterministic runner that gates CI.
- **Determinism first.** No fixed `sleep` (condition waits only); an ambiguous selector fails
  immediately instead of tapping whatever matched first.
- **A platform is a backend.** The deterministic core names no platform; add or swap a backend and
  the same scenario format, runner, and CLI target a new platform unchanged.

## Status — pre-alpha

The deterministic core, the AI authoring loop (`record` / `crawl`), the evidence subsystem,
codegen, and self-healing triage are all implemented and unit-tested with no Simulator
needed. The iOS **idb/XCUITest backends** are validated end-to-end on a real Simulator, the
**Android (adb) backend** is validated on an emulator, and the **web (Playwright) backend** runs
a deterministic `run` against a browser on the Linux gate.

## Quickstart

```bash
uv sync --group dev                                       # .venv + deps + dev tools
bajutsu record --target <name> --goal "..." [--out f.yaml]  # Tier 1: AI explore + record
bajutsu run    --target <name> [--scenario f.yaml]          # Tier 2: deterministic pass/fail
```

The [Getting started tutorial](getting-started.md) walks through install → unit tests → scenario →
device run → report. On a machine without a Mac, the [web track](getting-started-web.md) does the
same loop against a browser (Playwright backend) — no Xcode or Simulator.

## Feature highlights

- **Author with AI** — `record` and `crawl` explore the app and write scenarios you can hand-edit.
- **Deterministic runner** — condition waits, a stability ladder, and selectors that fail loudly on
  ambiguity.
- **Evidence subsystem** — screenshots, hierarchy, and network captured on a policy you control.
- **Self-healing triage** — AI investigates a failure and proposes a fix, off the CI path.
- **Codegen** — turn a scenario into an equivalent native test: XCUITest (iOS), Playwright (web),
  or UI Automator (Android).
- **MCP & web UI** — drive Bajutsu from an MCP client or the local `serve` UI.

## Backends & platforms

| Platform | Backend | Status |
|---|---|---|
| iOS Simulator | idb / XCUITest | Validated end-to-end on-device |
| Web | Playwright | Validated end-to-end on the Linux gate |
| Android | adb | Validated end-to-end on an emulator |
| Flutter | (planned) | Next |

## Learn more

- [Documentation overview](overview.md) — the per-feature reference, in suggested reading order.
- [Getting started](getting-started.md) · [Concepts](concepts.md) · [Glossary](glossary.md) · [API reference](api/index.md)
- [Roadmap](../roadmaps/README.md) · [Design rationale (DESIGN.md)](../DESIGN.md) ·
  [GitHub](https://github.com/bajutsu-e2e/bajutsu)

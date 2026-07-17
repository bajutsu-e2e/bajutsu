**English** · [日本語](../ja/getting-started/index.md)

# Getting started

> A hands-on walkthrough. By the end you will have installed Bajutsu, run the
> unit suite, read a scenario, driven a real app, and opened the HTML report. The other pages are
> a reference (what each feature does); this one is a tutorial (do these steps, in order).

Related: [cli](../cli.md) · [scenarios](../scenarios.md) · [run-loop](../run-loop.md) · [reporting](../reporting.md)

---

## Two tracks, one loop

Steps 1–3 and 6 below are the same regardless of what you're driving — install, unit tests, read a
scenario, read the report. Only Steps 4–5 (build/serve the app under test, then run a scenario
against it) differ by **[backend](../glossary.md#driver-backend-actuator-platform)**, because "a platform is a backend" ([concepts](../concepts.md)):

- **[iOS track](ios.md)** — finishes on the iOS Simulator via the idb backend. Needs macOS + Xcode.
- **[Web track](web.md)** — finishes against a browser via the Playwright backend. Runs on any OS
  (Linux, Windows, macOS) — no Xcode, no Simulator.

Pick whichever matches your machine, or do both — the same scenario schema, runner, and CLI drive
either target unchanged.

> **No Claude needed to start.** Only the AI authoring paths reach a model; `run`, `doctor`,
> `lint`, `codegen`, `trace`, and friends run with **zero configuration** — no key, no `.env`, no
> login. Which commands use Claude and which don't is spelled out in
> [What uses Claude](../ai-boundary.md).

> **Status note.** Bajutsu is pre-alpha. The deterministic core, the AI authoring loop, the
> evidence subsystem, and codegen are all implemented and unit-tested; on-device execution is
> validated end-to-end on both the iOS Simulator and an Android emulator, and the web (Playwright)
> backend runs the same deterministic loop in a browser. If a device step misbehaves, fall back to
> the unit tests, which are stable.

---

## Step 1 — Install

```bash
git clone <this repo> && cd bajutsu       # or just cd into your checkout
uv sync --group dev                        # creates .venv (Python 3.13) + deps + dev tools
```

`uv` reads `pyproject.toml` / `uv.lock` and builds an isolated `.venv`. Prefix project commands
with `uv run` (e.g. `uv run bajutsu …`) so they use that environment.

> **Installing from PyPI?** The base package is AI-free: `pip install bajutsu` gets the
> deterministic authoring / running paths with no AI SDK, and `pip install bajutsu[ai]` (or
> `bajutsu[bedrock]`) adds the SDK for the Claude paths. The split is detailed in
> [What uses Claude](../ai-boundary.md#installing-the-claude-paths).

Confirm the CLI (command-line interface) is wired up:

```bash
uv run bajutsu --help
```

You should see the commands `run`, `doctor`, `record`, `crawl`, `codegen`, `trace`, `triage`,
`approve`, `serve`, `mcp`, `worker`, `lint`, and `schema` (full reference: [cli](../cli.md)).

Each track adds one more install step on top of this (the idb backend's tools for iOS, or the
Playwright browser for web) — see [Step 1 on your chosen track](#two-tracks-one-loop).

---

## Step 2 — Run the unit suite

This is the fastest way to confirm everything is healthy — the unit tests exercise the
determinism core, the scenario schema, assertions, and the run loop against an in-memory fake
driver, **without touching a device or a browser**.

```bash
uv run pytest -q          # the test suite
make check                # or: ruff (lint) + mypy (strict types) + pytest, together
```

Green here means the engine works on this machine, regardless of which track you follow next.

---

## Step 3 — Read a scenario

A scenario file is plain YAML: a list of named scenarios (optionally wrapped in a
`{ description, scenarios }` mapping), each with optional `preconditions`, a list of `steps`, and
an `expect` block of **machine-checkable assertions**. The showcase suite's smoke test,
[`demos/showcase/scenarios/smoke.yaml`](../../demos/showcase/scenarios/smoke.yaml), checks that a
fixed catalog of rows renders on launch:

```yaml
- name: stable catalog smoke
  preconditions:
    launchEnv: { SHOWCASE_UITEST: "1" }     # disable animations -> tight condition waits
  steps:
    - wait: { for: { id: [stable.row.1, stable_row_1] }, timeout: 10 }
  expect:
    - count: { sel: { idMatches: ["stable.row.*", "stable_row_*"] }, equals: 5 }
    - exists: { id: [stable.row.1, stable_row_1] }
```

The key points:

- **Steps act; `expect` judges.** A `run` performs the steps, then the assertions in `expect`
  alone decide pass/fail, with no AI and no human judgment. This is the determinism boundary
  ([concepts](../concepts.md)).
- **Selectors are id-first** (`{ id: stable.row.1 }`). They are stable and non-localized. An
  ambiguous selector fails immediately rather than tapping whatever matched first
  ([selectors](../selectors.md)). This scenario's `id` lists two forms
  (`stable.row.1` / `stable_row_1`) so the same file also runs unchanged on Android, whose native
  id syntax can't hold a `.` ([scenarios](../scenarios.md#cross-platform-ids-a-candidate-list-be-0221)).
- **Waits are conditions, not sleeps** (`wait: { for: …, timeout: 10 }`): Bajutsu polls until the
  element exists, up to the timeout.

The full grammar (every step kind, wait, and assertion) is in [scenarios](../scenarios.md). Each
track's smoke test looks slightly different — the web track's is a login-and-counter flow — but
every track shares this exact step/expect grammar.

---

## Step 6 — Read the report

Every run writes a folder `runs/<runId>/` (where `runId` is `YYYYMMDD-HHMMSS`) with three views of
the same result:

```
runs/20260610-120000/
├── manifest.json     # the step -> outcome correlation — the single source of truth
├── junit.xml         # CI integration (1 scenario = 1 testcase)
└── report.html       # self-contained HTML (open it in a browser)
```

Open the HTML report to see each step, its outcome, and the captured evidence (screenshots /
element snapshots) inline:

```bash
open runs/<runId>/report.html      # macOS; use xdg-open on Linux
```

You can also inspect a finished run as a text timeline without leaving the terminal:

```bash
uv run bajutsu trace               # the latest run under runs/
```

Details on each format and the `runs/` layout: [reporting](../reporting.md).

---

## Where to go next

You now have the full loop: install → unit tests → scenario → device run → report. Two more entry
points use the same scenario format:

- **Author with AI.** Let Claude explore toward a goal and write the scenario for you (Tier 1). Put
  `ANTHROPIC_API_KEY=sk-ant-…` in a `.env` file, then run `bajutsu record --target <name> --goal
  "…"` — each track's page shows the exact invocation. In the web UI (`make serve`) the **API key**
  button sets the same key for the running session. The key is write-once: it is shown only as a
  masked preview and never displayed again — to change it, set a new one (there is no read-back).
  It is kept in memory only (not written to disk), so a `.env` is still the way to make it survive
  a restart. How the authoring loop and the system-alert guard work: [recording](../recording.md).
  How to drive each tab of the web UI: [web-ui](../web-ui.md).
- **Emit a native test.** Convert a scenario to a native XCUITest, Playwright, or UI Automator test
  (no Bajutsu runtime / AI at test time):
  ```bash
  uv run bajutsu codegen demos/showcase/scenarios/smoke.yaml --target showcase-swiftui -o UITests/Smoke.swift
  ```
  The structural mapping: [codegen](../codegen.md).

From here, the reference pages cover each piece in depth. Start with [concepts](../concepts.md) for
the rationale, then follow the suggested reading order in the [docs overview](../overview.md). To
onboard your own app (not the showcase/demo), see
[configuration](../configuration.md#onboarding-a-new-target).

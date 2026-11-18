**English** · [日本語](../ja/getting-started/index.md)

# Getting started

> This hands-on walkthrough takes you from installing Bajutsu to running the unit suite, reading a
> scenario, driving a real app, and opening the HTML report. The other pages are a reference that
> says what each feature does. This page is a tutorial: do these steps, in order.

Related: [cli](../cli.md) · [scenarios](../scenarios.md) · [run-loop](../run-loop.md) · [reporting](../reporting.md)

---

## Two tracks, one loop

Steps 1–3 and 6 below are the same whatever you are driving: install, run the unit tests, read a
scenario, and read the report. Only Steps 4–5 differ: build or serve the app under test, then run a
scenario against it. They differ by
**[backend](../glossary.md#driver-backend-actuator-platform)**, because "a platform is a backend"
([concepts](../concepts.md)):

- **[iOS track](ios.md)**: finishes on the iOS Simulator through the XCUITest backend. It needs
  macOS and Xcode.
- **[Web track](web.md)**: finishes against a browser through the Playwright backend. It runs on any
  OS (Linux, Windows, macOS) and needs no Xcode and no Simulator.

Pick the track that matches your machine, or do both. The same scenario schema, runner, and CLI
drive either target unchanged.

> **No Claude needed to start.** Only the AI authoring paths reach a model. `run`, `doctor`,
> `lint`, `codegen`, `trace`, and the other commands run with **zero configuration**: no key, no
> `.env`, no login. [What uses Claude](../ai-boundary.md) spells out which commands use Claude and
> which do not.

> **Status note.** Bajutsu is pre-alpha. The deterministic core, the AI authoring loop, the
> evidence subsystem, and codegen are all implemented and unit-tested. On-device execution is
> validated end-to-end on both the iOS Simulator and an Android emulator. The web (Playwright)
> backend runs the same deterministic loop in a browser. If a device step misbehaves, fall back to
> the unit tests, which are stable.

---

## Step 1 — Install

```bash
git clone <this repo> && cd bajutsu       # or just cd into your checkout
uv sync --group dev                        # creates .venv (Python 3.13) + deps + dev tools
```

`uv` reads `pyproject.toml` / `uv.lock` and builds an isolated `.venv`. Prefix project commands
with `uv run` (for example `uv run bajutsu …`) so they use that environment.

> **Installing from PyPI?** The base package is AI-free. `pip install bajutsu` gets the
> deterministic authoring and running paths with no AI SDK. `pip install bajutsu[ai]` (or
> `bajutsu[bedrock]`) adds the SDK for the Claude paths. [What uses
> Claude](../ai-boundary.md#installing-the-claude-paths) details the split. The base package also
> carries the server stack (FastAPI, uvicorn, SQLAlchemy, Alembic, `cryptography`), so
> `bajutsu serve` starts on one machine with no extra. The `server` and `db` extras add the clients
> for a hosted deployment, and nothing else: boto3 for an S3 bucket and psycopg for PostgreSQL.

Confirm that the command-line interface (CLI) is wired up:

```bash
uv run bajutsu --help
```

The output lists the commands `run`, `doctor`, `record`, `crawl`, `repl`, `codegen`, `trace`,
`triage`, `approve`, `serve`, `mcp`, `worker`, `lint`, `schema`, `audit`, `coverage`, `export`,
`flakiness`, `impact`, `report`, and `stats` (full reference: [cli](../cli.md)).

The web track adds one more install step on top of this one: the Playwright browser. The iOS track
needs only Xcode, which the XCUITest backend drives directly. See
[Step 1 on your chosen track](#two-tracks-one-loop).

---

## Step 2 — Run the unit suite

Running the unit suite is the fastest way to confirm that everything is healthy. The unit tests
exercise the determinism core, the scenario schema, assertions, and the run loop against an
in-memory fake driver, **without touching a device or a browser**.

```bash
uv run pytest -q          # the test suite
make check                # or: ruff (lint) + mypy (strict types) + pytest, together
```

A green result means the engine works on this machine, whichever track you follow next.

---

## Step 3 — Read a scenario

A scenario file is plain YAML: a list of named scenarios, optionally wrapped in a
`{ description, scenarios }` mapping. Each scenario has optional `preconditions`, a list of `steps`,
and an `expect` block of **machine-checkable assertions**. The showcase suite's smoke test,
[`demos/showcase/scenarios/smoke.yaml`](../../demos/showcase/scenarios/smoke.yaml), checks that a
fixed catalog of rows renders on launch:

```yaml
- name: stable catalog smoke
  preconditions:
    subject:
      launch:
        env: { SHOWCASE_UITEST: "1" }     # disable animations -> tight condition waits
  steps:
    - wait: { for: { id: [stable.row.1, stable_row_1] }, timeout: 10 }
  expect:
    - count: { sel: { idMatches: ["stable.row.*", "stable_row_*"] }, equals: 5 }
    - exists: { id: [stable.row.1, stable_row_1] }
```

The scenario shows three key points:

- **Steps act; `expect` judges.** A `run` performs the steps, then the assertions in `expect`
  alone decide pass or fail, with no AI and no human judgment. This is the determinism boundary
  ([concepts](../concepts.md)).
- **Selectors are id-first** (`{ id: stable.row.1 }`). Ids are stable and non-localized. An
  ambiguous selector fails immediately rather than tapping whatever matched first
  ([selectors](../selectors.md)). This scenario's `id` lists two forms
  (`stable.row.1` / `stable_row_1`), so the same file also runs unchanged on Android, whose native
  id syntax cannot hold a `.`
  ([scenarios](../scenarios.md#cross-platform-ids-a-candidate-list-be-0221)).
- **Waits are conditions, not sleeps** (`wait: { for: …, timeout: 10 }`). Bajutsu polls until the
  element exists, up to the timeout.

[scenarios](../scenarios.md) holds the full grammar: every step kind, wait, and assertion. Each
track's smoke test looks slightly different (the web track's is a login-and-counter flow), but
every track shares this exact step/expect grammar.

---

## Step 6 — Read the report

Every run writes a folder `runs/<runId>/`, where `runId` is `YYYYMMDD-HHMMSS`. The folder holds four
views of the same result:

```
runs/20260610-120000/
├── manifest.json     # the step -> outcome correlation — the single source of truth
├── junit.xml         # CI integration (1 scenario = 1 testcase)
├── ctrf.json         # Common Test Report Format (richer CI consumers: PR comments, dashboards)
└── report.html       # self-contained HTML (open it in a browser)
```

Open the HTML report to see each step, its outcome, and the captured evidence (screenshots and
element snapshots) inline:

```bash
open runs/<runId>/report.html      # macOS; use xdg-open on Linux
```

To inspect a finished run as a text timeline without leaving the terminal, run:

```bash
uv run bajutsu trace               # the latest run under runs/
```

[reporting](../reporting.md) details each format and the `runs/` layout.

---

## Where to go next

You now have the full loop: install → unit tests → scenario → device run → report. Two more entry
points use the same scenario format.

- **Author with AI.** Let Claude explore toward a goal and write the scenario for you (Tier 1). Put
  `ANTHROPIC_API_KEY=sk-ant-…` in a `.env` file, then run `bajutsu record --target <name> --goal
  "…"`. Each track's page shows the exact invocation. In the web UI (`make serve`), the **API key**
  button sets the same key for the running session. The key is write-once: the UI shows only a
  masked preview and never displays the key again. To change it, set a new one (there is no
  read-back). The key lives in memory only and is never written to disk, so a `.env` is still the
  way to keep it across a restart. [recording](../recording.md) explains how the authoring loop and
  the system-alert guard work. [web-ui](../web-ui.md) explains how to drive each tab of the web UI.
- **Emit a native test.** Convert a scenario to a native XCUITest, Playwright, or UI Automator test
  that needs neither the Bajutsu runtime nor AI at test time:
  ```bash
  uv run bajutsu codegen demos/showcase/scenarios/smoke.yaml --target showcase-swiftui -o UITests/Smoke.swift
  ```
  [codegen](../codegen.md) describes the structural mapping.

From here, the reference pages cover each piece in depth. Start with [concepts](../concepts.md) for
the rationale, then follow the suggested reading order in the [docs overview](../overview.md). To
onboard your own app rather than the showcase or demo, see
[configuration](../configuration.md#onboarding-a-new-target).

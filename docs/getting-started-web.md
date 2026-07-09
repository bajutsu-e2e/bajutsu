**English** · [日本語](ja/getting-started-web.md)

# Getting started (web track — no Mac required)

> A hands-on walkthrough that finishes on **any operating system**. By the end you will have
> installed Bajutsu, run the unit suite, read a scenario, driven a real web app in a browser with
> the **Playwright** backend, and opened the HTML report — no macOS, no Xcode, no iOS Simulator.
> This is the web twin of the [Getting started tutorial](getting-started.md): the same
> install → scenario → run → report loop, on the same scenario format and the same deterministic
> runner, only the backend differs.

Related: [getting-started (iOS)](getting-started.md) · [cli](cli.md) · [scenarios](scenarios.md) · [drivers](drivers.md) · [reporting](reporting.md)

---

## Why a web track

Bajutsu's headline claim is **"a platform is a backend"**: the deterministic core, the scenario
format, and the reporter are identical regardless of which backend actuates the UI. The
[iOS tutorial](getting-started.md) finishes on a Simulator (the `idb` backend), which needs a Mac
with Xcode. This track finishes the same loop against a browser (the `web` / Playwright backend),
which runs on Linux, Windows, or macOS alike — the exact backend that drives Bajutsu's own web
demo on the Linux gate ([`demos/web`](../demos/web/README.md)).

The vocabulary here follows the [glossary](glossary.md): a **backend** is the token you pass to
`--backend` (here `web`), it resolves to an **actuator** (the Playwright engine that performs the
taps and types), and a **target** is one `targets.<name>` config entry describing the app under
test — for the web, identified by a `baseUrl` rather than an iOS `bundleId`.

> **No Claude needed to finish.** Only the AI authoring paths (`record`, `crawl`) reach a model;
> `run`, `doctor`, `lint`, `codegen`, and `trace` run with **zero configuration** — no key, no
> `.env`, no login. Which commands use Claude and which don't is spelled out in
> [What uses Claude](ai-boundary.md).

> **Status note.** Bajutsu is pre-alpha. The deterministic core, the AI authoring loop, the
> evidence subsystem, and codegen are all implemented and unit-tested. The web (Playwright)
> backend has landed a first slice — a deterministic `run` against a browser, exercised on the
> Linux gate ([BE-0041](../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).

---

## Step 1 — Install

```bash
git clone <this repo> && cd bajutsu       # or just cd into your checkout
uv sync --group dev                        # creates .venv (Python 3.13) + deps + dev tools
uv sync --extra web                        # adds the Playwright python package
uv run playwright install chromium         # downloads the Chromium binary Playwright drives
```

`uv` reads `pyproject.toml` / `uv.lock` and builds an isolated `.venv`. Prefix project commands
with `uv run` (e.g. `uv run bajutsu …`) so they use that environment. The `--extra web` line and
the `playwright install` line are what the web backend adds on top of the base install — the
Chromium binary is a separate download, not a pip dependency.

Confirm the CLI (command-line interface) is wired up:

```bash
uv run bajutsu --help
```

You should see the commands `run`, `doctor`, `record`, `crawl`, `codegen`, `trace`, `triage`,
`approve`, `serve`, `mcp`, `worker`, `lint`, and `schema` (full reference: [cli](cli.md)).

---

## Step 2 — Run the unit suite (no browser)

This is the fastest way to confirm everything is healthy — the unit tests exercise the determinism
core, the scenario schema, assertions, and the run loop against an in-memory fake driver, **without
launching a browser**.

```bash
uv run pytest -q          # the test suite
make check                # or: ruff (lint) + mypy (strict types) + pytest, together
```

Green here means the engine works on this machine. Everything from Step 4 on builds on top of it.

---

## Step 3 — Read a scenario

A scenario is plain YAML: a list of named tests, each with optional `preconditions`, a list of
`steps`, and an `expect` block of **machine-checkable assertions**. Open the web demo's smoke test,
[`demos/web/scenarios/smoke.yaml`](../demos/web/scenarios/smoke.yaml):

```yaml
# End-to-end smoke for the web backend: onboarding -> login -> home -> counter.
scenarios:
  - name: onboard, log in, and increment the counter
    steps:
      - tap: { id: onboarding.start }
      - type: { text: "a@b.com", into: { id: auth.email } }
      - type: { text: "pw", into: { id: auth.password } }
      - tap: { id: auth.submit }
      - wait: { for: { id: home.title }, timeout: 5 }   # condition wait, never a fixed sleep
      - tap: { id: counter.increment }
      - tap: { id: counter.increment }
    expect:
      - exists: { id: home.title }
      - value: { sel: { id: counter.value }, equals: "2" }
```

This is **byte-for-byte the same step/expect schema as the iOS smoke test** — that is the point.
The key ideas:

- **Steps act; `expect` judges.** A `run` performs the steps, then the assertions in `expect`
  alone decide pass/fail, with no AI and no human judgment. This is the determinism boundary
  ([concepts](concepts.md)).
- **Selectors are id-first** (`{ id: home.title }`). On the web these resolve to `data-testid`
  attributes — the browser equivalent of iOS `accessibilityIdentifier` — through the **same**
  selector-resolution core as every other backend ([selectors](selectors.md), [drivers](drivers.md#playwright-web)).
  An ambiguous selector fails immediately rather than tapping whatever matched first.
- **Waits are conditions, not sleeps** (`wait: { for: …, timeout: 5 }`): Bajutsu polls until the
  element exists, up to the timeout.

The full grammar (every step kind, wait, and assertion) is in [scenarios](scenarios.md).

---

## Step 4 — Serve the demo web app

The web demo ships a tiny static app under [`demos/web/app`](../demos/web/README.md) — onboarding →
login → counter, written in vanilla JavaScript with stable `data-testid` ids. Serve it locally so a
browser (and Bajutsu) can reach it:

```bash
make -C demos/web app-serve        # serves demos/web/app on 127.0.0.1:8787 (Ctrl-C to stop)
```

Leave it running and open <http://127.0.0.1:8787/index.html> in your own browser to poke the app by
hand — Get started, sign in with any email/password, and increment the counter. That is exactly the
flow the scenario in Step 3 automates.

---

## Step 5 — Run a scenario in the browser

The one-shot path is the `make` target: it installs the web backend if needed, serves the app in
the background, runs the demo's scenarios through Playwright, and tears the server down — no manual
serve required.

```bash
make -C demos/web e2e
```

Or drive the CLI directly against the app you served in Step 4 (the same run, written out):

```bash
uv run bajutsu run --scenario demos/web/scenarios/smoke.yaml --target web --backend web --config demos/web/demo.config.yaml
```

What the flags mean:

- `--target web` selects `targets.web` from [`demos/web/demo.config.yaml`](../demos/web/demo.config.yaml)
  (its `baseUrl` and scenarios dir). The tool itself is app-agnostic; all per-target differences
  live in config ([configuration](configuration.md)).
- `--backend web` picks the actuator: the Playwright engine driving Chromium. No Xcode, no idb, no
  Simulator is involved.
- Omit `--scenario` to run the target's whole scenarios directory (what `make -C demos/web e2e`
  does).

On success you'll see a line like:

```
PASS  runs/20260610-120000/manifest.json
```

`run` **exits 0 when every scenario passes, 1 on any failure**, and that exit code is the CI
(continuous integration) gate ([run-loop](run-loop.md)). The verdict is purely the scenario's
machine assertions (the counter reads `2`) — never an LLM.

> Hit an environment problem (Chromium not installed, the web extra missing)? Run
> `uv run bajutsu doctor --target web --config demos/web/demo.config.yaml` first — it prints a
> ✓/✗ checklist of what the web backend needs.

---

## Step 6 — Read the report

Every run writes a folder `runs/<runId>/` (where `runId` is `YYYYMMDD-HHMMSS`) with three views of
the same result — **identical formats to the iOS track**:

```
runs/20260610-120000/
├── manifest.json     # the step -> outcome correlation — the single source of truth
├── junit.xml         # CI integration (1 scenario = 1 testcase)
└── report.html       # self-contained HTML (open it in a browser)
```

Open the HTML report to see each step, its outcome, and the captured evidence (screenshots)
inline:

```bash
# Linux
xdg-open runs/<runId>/report.html
# macOS
open runs/<runId>/report.html
```

You can also inspect a finished run as a text timeline without leaving the terminal:

```bash
uv run bajutsu trace               # the latest run under runs/
```

Details on each format and the `runs/` layout: [reporting](reporting.md).

---

## Where to go next

You now have the full loop on any machine: install → unit tests → scenario → browser run → report.
The same scenario format extends further:

- **Author with AI.** Let Claude explore the web app toward a goal and write the scenario for you
  (Tier 1). Put `ANTHROPIC_API_KEY=sk-ant-…` in a `.env` file, then:
  ```bash
  make -C demos/web record GOAL="Get started, then increment the counter three times and confirm it shows 3."
  ```
  With no key and no browser, the offline twin reproduces the same record loop deterministically
  (`make -C demos/web record-offline`). How the authoring loop works: [recording](recording.md).
- **Onboard your own web app.** Point a new `targets.<name>` entry at your app's `baseUrl` and give
  its elements stable `data-testid` ids — see [configuration](configuration.md#onboarding-a-new-target).
- **Have a Mac too?** The [iOS track](getting-started.md) finishes the same loop on a Simulator via
  the idb backend, showing the same scenarios run unchanged on a different backend.

From here, the reference pages cover each piece in depth. Start with [concepts](concepts.md) for the
rationale, then follow the suggested reading order in the [docs overview](overview.md).

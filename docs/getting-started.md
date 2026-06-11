**English** · [日本語](ja/getting-started.md)

# Getting started

> A hands-on, zero-to-green walkthrough. By the end you will have installed Bajutsu, run the
> unit suite, read a scenario, driven the bundled sample app on a Simulator, and opened the
> HTML report. Where the other pages are a *reference* (what each feature does), this one is a
> *tutorial* (do these steps, in order).

Related: [cli](cli.md) · [scenarios](scenarios.md) · [sample-app](sample-app.md) · [run-loop](run-loop.md) · [reporting](reporting.md)

---

## What you'll need

| For… | You need |
|---|---|
| The deterministic core + unit tests | macOS or Linux, Python 3.13 (managed via [uv](https://github.com/astral-sh/uv)) |
| Driving an app on a Simulator | macOS with **Xcode** (the iOS Simulator), [XcodeGen](https://github.com/yonwoo9/XcodeGen) (to build the sample), and the **idb** backend (`brew install facebook/fb/idb-companion`) |
| AI authoring (`record`) / `--dismiss-alerts` | an `ANTHROPIC_API_KEY` |

You can do **Steps 1–3 on any machine** (no Simulator). Steps 4–6 need a Mac with Xcode.

> **Status note.** Bajutsu is pre-alpha. The deterministic core, the AI authoring loop, the
> evidence subsystem, and codegen are all implemented and unit-tested; on-device execution via
> the idb backend works through the `make` targets but is still being hardened. If a device step
> misbehaves, fall back to the unit tests — they are the stable ground.

---

## Step 1 — Install

```bash
git clone <this repo> && cd simpilot      # or just cd into your checkout
uv sync --extra dev                        # creates .venv (Python 3.13) + deps + dev tools
```

`uv` reads `pyproject.toml` / `uv.lock` and builds an isolated `.venv`. Prefix project commands
with `uv run` (e.g. `uv run bajutsu …`) so they use that environment.

Confirm the CLI is wired up:

```bash
uv run bajutsu --help
```

You should see the commands `run`, `doctor`, `record`, `codegen`, `trace`, `triage`, and
`serve` (full reference: [cli](cli.md)).

---

## Step 2 — Run the unit suite (no Simulator)

This is the fastest way to confirm everything is healthy — 324 tests that exercise the
determinism core, the scenario schema, assertions, and the run loop against an in-memory fake
driver, **without touching a Simulator**.

```bash
uv run pytest -q          # the test suite
make check                # or: ruff (lint) + mypy (strict types) + pytest, together
```

Green here means the engine is sound on this machine. Everything from Step 4 on builds on top of
it.

---

## Step 3 — Read a scenario

A scenario is plain YAML: a list of named tests, each with optional `preconditions`, a list of
`steps`, and an `expect` block of **machine-checkable assertions**. Open the bundled smoke test,
[`app/sample/scenarios/smoke.yaml`](../app/sample/scenarios/smoke.yaml):

```yaml
# End-to-end smoke: onboarding -> login -> home -> counter.
- name: onboard, log in, and increment the counter
  preconditions:
    launchEnv: { SAMPLE_UITEST: "1" }     # disable animations -> tight condition waits
  steps:
    - tap:  { id: onboarding.start }
    - type: { text: "a@b.com", into: { id: auth.email } }
    - type: { text: "pw",      into: { id: auth.password } }
    - tap:  { id: auth.submit }
    - wait: { for: { id: home.title }, timeout: 5 }   # condition wait, never a fixed sleep
    - tap:  { id: counter.increment }
    - tap:  { id: counter.increment }
  expect:
    - exists: { id: home.title }
    - value:  { sel: { id: counter.value }, equals: "2" }
```

The shape to internalize:

- **Steps act; `expect` judges.** A `run` performs the steps, then the assertions in `expect`
  alone decide pass/fail — no AI, no human, no "looks right." This is the determinism boundary
  ([concepts](concepts.md)).
- **Selectors are `accessibilityIdentifier`-first** (`{ id: home.title }`). They are stable and
  non-localized. An *ambiguous* selector fails immediately rather than tapping whatever matched
  first ([selectors](selectors.md)).
- **Waits are conditions, not sleeps** (`wait: { for: …, timeout: 5 }`) — Bajutsu polls until the
  element exists, up to the timeout.

The full grammar (every step kind, wait, and assertion) is in [scenarios](scenarios.md); the
identifiers this app exposes are catalogued in [sample-app](sample-app.md).

---

## Step 4 — Build the sample app (needs Xcode)

The repo ships a small SwiftUI fixture, `BajutsuSample`, instrumented for every Bajutsu
primitive. Build it for the Simulator:

```bash
make sample-build         # xcodegen generate -> xcodebuild for the iOS Simulator
```

This produces `BajutsuSample.app` under `app/sample/build/…`. (The `.xcodeproj` and `build/` are
gitignored — `project.yml` is the source of truth.) See [sample-app](sample-app.md) for the launch-env
hooks and the identifier catalog.

---

## Step 5 — Run a scenario on a Simulator

Boot a Simulator and make sure the idb backend is available:

```bash
xcrun simctl boot "iPhone 15"                 # or boot one from Xcode > Open Developer Tool > Simulator
brew install facebook/fb/idb-companion        # the idb backend (one-time)
uv sync --extra idb                           # the idb python client
```

The one-shot path is the `make` target, which installs the freshly built app and runs the smoke
scenario plus a `doctor` check on the booted device:

```bash
make e2e
```

Or drive the CLI directly (the same thing, spelled out):

```bash
uv run bajutsu run app/sample/scenarios/smoke.yaml --app sample --backend idb --udid booted --no-erase
```

What the flags mean:

- `--app sample` selects `apps.sample` from [`bajutsu.config.yaml`](../bajutsu.config.yaml)
  (bundle id, launch env, allowed id namespaces). The tool itself is app-agnostic; all per-app
  differences live in config ([configuration](configuration.md)).
- `--backend idb` picks the actuator; `--udid booted` targets the currently booted Simulator.
- `--no-erase` keeps the already-installed app instead of `simctl erase`-ing first.

On success you'll see a line like:

```
PASS  runs/20260610-120000/manifest.json
```

`run` **exits 0 when every scenario passes, 1 on any failure** — that exit code is the CI gate
([run-loop](run-loop.md)).

> Hit an environment problem (no booted Simulator, idb not installed)? Run
> `uv run bajutsu doctor --app sample` first — it prints a ✓/✗ checklist of the required CLIs and
> a booted device, then scores how well the current screen follows the identifier convention
> ([configuration](configuration.md#doctor-the-convention-score)).

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
open runs/<runId>/report.html      # the latest run's folder
```

You can also inspect a finished run as a text timeline without leaving the terminal:

```bash
uv run bajutsu trace               # the latest run under runs/
```

Details on each format and the `runs/` layout: [reporting](reporting.md).

---

## Where to go next

You now have the full loop: install → unit tests → scenario → device run → report. Two more
entry points share the same scenario format:

- **Author with AI.** Let Claude explore toward a goal and write the scenario for you (Tier 1).
  Put `ANTHROPIC_API_KEY=sk-ant-…` in a `.env` file, then:
  ```bash
  uv run bajutsu record out.yaml --app sample --goal "log in and increment the counter to 3"
  ```
  How the authoring loop and the system-alert guard work: [recording](recording.md).
- **Emit a native XCUITest.** Convert a scenario to Swift (no Bajutsu runtime / AI at test time):
  ```bash
  uv run bajutsu codegen app/sample/scenarios/smoke.yaml --app sample -o UITests/Smoke.swift
  ```
  The structural mapping: [codegen](codegen.md). Run it end-to-end with `make ui-test`.

From here, the reference pages go deep on each piece — start with [concepts](concepts.md) for the
*why*, then follow the suggested reading order in the [docs index](README.md). To onboard your own
app (not the sample), see [configuration](configuration.md#onboarding-a-new-app).

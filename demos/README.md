# Bajutsu demos

**English** · [日本語](README.ja.md)

Runnable demos — the `make -C demos` set below drives a real iOS Simulator, and a separate web
demo drives a browser on Linux (below). Each tells the same core story —
**a scenario is the deterministic hub; a run decides pass/fail with machine assertions only,
never an LLM** — at a different depth. Every iOS demo runs through one entry point,
`make -C demos <target>`:

```bash
make -C demos            # the menu (same as `make -C demos help`)
make -C demos tour       # deterministic (no API key): run -> modify -> diagnose on a Simulator
make -C demos features   # deterministic (no API key): the scenario-feature showcase
make -C demos offline    # both of the deterministic demos at once
make -C demos webui      # the Web UI tour — every evidence type
make -C demos record     # AI authoring with real Claude, then modify + triage
```

Every iOS demo drives the same fixture: the **showcase** suite ([`showcase/`](showcase/README.md)).

| Demo | Command | What it proves | What it needs |
|---|---|---|---|
| **[tour](tour/README.md)** | `make -C demos tour` | The whole lifecycle — run → modify → diagnose (`triage`) — on a real Simulator, fully deterministic | macOS + Simulator (idb auto-installs). **No API key** |
| **[features](features/README.md)** | `make -C demos features` | The scenario-authoring features (tags, parameterized shared steps, secrets) on a real Simulator | macOS + Simulator. **No API key** |
| **[webui](showcase/WEBUI.md)** | `make -C demos webui` | The **Web UI** driving a Simulator and collecting every evidence type: screenshots, video, logs, network (observed + mocked), visual regression, system-alert handling | macOS + Simulator; an API key for the system-alert part only |
| **[record](showcase/README.md)** | `make -C demos record` | AI authoring with **real Claude** against a booted app, then the modify-and-self-heal loop | macOS + Simulator + Claude (CLI or API key) |

> **No Mac / Simulator?** The whole `tour` story also runs against an in-memory fake device —
> no Simulator, idb, or API key, on Linux/CI in seconds: `uv run python demos/tour/tour.py`
> (and the feature showcase: `uv run python demos/features/run_demo.py`). See
> [`tour/README.md`](tour/README.md). These are the fast first look; the `make` targets above
> are the real thing on a device.

> **Web (Playwright) backend.** A separate demo drives a tiny static web app in a browser — no Mac
> or Simulator, on Linux: `make -C demos/web e2e` ([`web/README.md`](web/README.md)). Same scenario
> format, same deterministic runner — **only the backend differs**, which is the whole
> multi-platform story in one demo.

## Which one should I run?

- **First look, on a real device?** → [`tour`](tour/README.md). One command opens a horse from
  the showcase catalog and favorites it on a Simulator, then breaks it two ways to show the
  deterministic check and `triage` self-heal — no API key needed.
- **Want the full evidence set in a browser?** → [`webui`](showcase/WEBUI.md). The Web UI tour
  boots a device and shows screenshots, video, network, visual regression, and system-alert
  handling. The headline demo for iOS developers.
- **Want AI authoring on the command line?** → [`record`](showcase/README.md).

## The two tiers, made concrete

Every demo is an instance of the same two-tier design ([README](../README.md#core-principles)):

- **Tier 1 — AI is the author and the failure investigator.** It *writes* scenarios
  (`record`, the Web UI's Record tab) and *investigates* failures (`triage`), and it handles
  system alerts during a run. It never decides pass/fail.
- **Tier 2 — the deterministic run is the gate.** `run` replays a scenario with no AI; the
  verdict comes only from machine-checkable assertions, with the evidence to audit it attached.

`tour` and `features` exercise Tier 2 alone (deterministic, no API key); `record` adds Tier 1
authoring; `webui` shows both in one window. The fake-device `tour.py` runs both tiers against
an in-memory driver so you can see the seam with nothing installed.

## The apps

- **`showcase`** ([`showcase/`](showcase/README.md)) — the single iOS fixture: the *same* app in
  UIKit and SwiftUI, each in an accessibility-on / -off variant (four products, two codebases),
  built to exercise `record`, `crawl`, and `run` together. Every iOS demo above — `tour`,
  `features`, `webui`, `record` — and the on-device CI workflows drive it.
- **`web`** ([`web/`](web/README.md)) — a tiny static web app driven by the **Playwright** backend
  on Linux (no Mac / Simulator); the cross-platform demo that proves the core is backend-neutral.

> The older single-variant fixtures — `demo` ([`app/`](app/)), `sample` ([`features/app/`](features/app/)),
> and `sample2` ([`record/app/`](record/app/)) — have been superseded by the showcase and are
> slated for removal (BE-0079); nothing in the demo menu or CI drives them any more.

Generated scenarios, working copies (`*/generated.yaml`, `tour/scenario.yaml`), Xcode projects,
and run artifacts (`runs/`) are gitignored — the demos regenerate them.

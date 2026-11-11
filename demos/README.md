# Bajutsu demos

**English** · [日本語](README.ja.md)

The demos here are runnable. The `make -C demos` set below drives a real iOS Simulator, and a separate
web demo drives a browser on Linux. Each demo tells the same core story at a different depth:
**a scenario is the deterministic hub, and a run decides pass/fail with machine assertions only,
never an LLM.** Every iOS demo runs through one entry point, `make -C demos <target>`:

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
| **[tour](tour/README.md)** | `make -C demos tour` | The whole lifecycle — run → modify → diagnose (`triage`) — on a real Simulator, fully deterministic | macOS + Xcode + Simulator. **No API key** |
| **[features](showcase/README.md)** | `make -C demos features` | The scenario-authoring features (tags, parameterized shared steps, secrets) on a real Simulator | macOS + Simulator. **No API key** |
| **[webui](showcase/WEBUI.md)** | `make -C demos webui` | The **Web UI** driving a Simulator and collecting every evidence type: screenshots, video, logs, network (observed + mocked), visual regression, system-alert handling | macOS + Simulator; an API key for the system-alert part only |
| **[record](showcase/README.md)** | `make -C demos record` | AI authoring with **real Claude** against a booted app, then the modify-and-self-heal loop | macOS + Simulator + Claude (CLI or API key) |

> **No Mac or Simulator?** The whole `tour` story also runs against an in-memory fake device,
> with no Simulator, Xcode, or API key, on Linux/CI in seconds: `uv run python demos/tour/tour.py`.
> See [`tour/README.md`](tour/README.md). This run is the fast first look; the `make` targets above
> are the real thing on a device.

> **Web (Playwright) backend.** A separate demo drives a tiny static web app in a browser, on Linux
> with no Mac or Simulator: `make -C demos/web e2e` ([`web/README.md`](web/README.md)). The scenario
> format and the deterministic runner stay the same, and **only the backend differs**, which shows
> the whole multi-platform story in one demo. The demo has its own AI-authoring path too:
> `make -C demos/web record` (real Claude, needs an API key) and `make -C demos/web record-offline`
> (the API-key-free twin, in the `make check` toolchain).

> **Web backend against a live public URL.** [`docs-site/`](docs-site/README.md) points the
> Playwright backend at the public Bajutsu docs site (<https://bajutsu-e2e.github.io/bajutsu/>)
> instead of a local fixture. There is no server to start, because the backend navigates straight to
> the URL:
> `uv run bajutsu run --target docs --backend web --config demos/docs-site/docs-site.config.yaml`.
> The site carries no `data-testid` ids, so its scenarios match by visible text and kind. Its `smoke`
> scenario asserts against live copy, so a docs redesign, not a code change, can break it.

## Which demo to run

- **A first look on a real device?** Run [`tour`](tour/README.md). One command opens a horse from
  the showcase catalog and favorites it on a Simulator, then breaks the scenario two ways to show the
  deterministic check and the `triage` self-heal. No API key is needed.
- **The full evidence set in a browser?** Run [`webui`](showcase/WEBUI.md). The Web UI tour
  boots a device and shows screenshots, video, network, visual regression, and system-alert
  handling. This is the headline demo for iOS developers.
- **AI authoring on the command line?** Run [`record`](showcase/README.md).

## The two tiers in the demos

Every demo is an instance of the same two-tier design ([README](../README.md#core-principles)):

- **Tier 1: AI is the author and the failure investigator.** The AI *writes* scenarios
  (`record`, the Web UI's Record tab), *investigates* failures (`triage`), and handles
  system alerts during a run. It never decides pass/fail.
- **Tier 2: the deterministic run is the gate.** `run` replays a scenario with no AI. The
  verdict comes from machine-checkable assertions alone, with the evidence to audit it attached.

`tour` and `features` exercise Tier 2 alone (deterministic, no API key). `record` adds Tier 1
authoring, and `webui` shows both tiers in one window. The fake-device `tour.py` runs both tiers
against an in-memory driver, so the seam between them is visible with nothing installed.

## The apps

- **`showcase`** ([`showcase/`](showcase/README.md)): the single iOS fixture. It is the *same* app in
  UIKit and SwiftUI, each in an accessibility-on and an accessibility-off variant (four products, two
  codebases), built to exercise `record`, `crawl`, and `run` together. Every iOS demo above
  (`tour`, `features`, `webui`, `record`) and the on-device CI workflows drive it.
- **`web`** ([`web/`](web/README.md)): a tiny static web app driven by the **Playwright** backend
  on Linux (no Mac or Simulator). This cross-platform demo proves the core is backend-neutral.

> The older single-variant fixtures (`demo`, `sample`, and `sample2`) have been retired and
> removed (BE-0079), superseded by the showcase. Nothing in the demo menu or CI drives them any more.

Generated scenarios, working copies (`*/generated.yaml`, `tour/scenario.yaml`), Xcode projects,
and run artifacts (`runs/`) are gitignored. The demos regenerate them.

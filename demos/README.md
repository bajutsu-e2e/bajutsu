# Bajutsu demos

**English** · [日本語](README.ja.md)

Three runnable demos, ordered by how much setup they need. Each tells the same core story —
**a natural-language goal becomes a deterministic scenario; a run decides pass/fail with
machine assertions only, never an LLM** — at a different depth. Start at the top and go as deep
as your setup allows.

Every demo runs the same way, through one entry point — `make -C demos <target>`:

```bash
make -C demos            # the menu (same as `make -C demos help`)
make -C demos tour       # zero setup: the whole lifecycle on a fake device
make -C demos features   # zero setup: the scenario-feature showcase
make -C demos offline    # both zero-setup demos at once
make -C demos webui      # on-device: the Web UI tour (macOS + Simulator)
make -C demos record     # on-device: AI authoring -> run -> modify -> triage
```

| Demo | Command | What it proves | What it needs |
|---|---|---|---|
| **[tour](tour/README.md)** | `make -C demos tour` | The whole lifecycle (author → run → modify → diagnose) end to end, on the real pipeline | **Nothing** — no Simulator, idb, or API key; runs on Linux/CI in seconds |
| **[features](features/WEBUI.md)** | `make -C demos webui` | The **Web UI** driving a real Simulator and collecting every evidence type: screenshots, video, logs, network (observed + mocked), visual regression, system-alert handling | macOS + Simulator (idb auto-installs); an API key for the system-alert part only |
| **[record](record/README.md)** | `make -C demos record` | AI authoring with **real Claude** against a booted app, then the modify-and-self-heal loop (`triage`) on the CLI | macOS + Simulator + idb + Claude (CLI or API key) |

The [features](features/README.md) folder also has a **zero-setup feature showcase** — the
scenario-authoring features (tags, shared steps, data-driven runs, secrets, device control) run
against the FakeDriver: `make -C demos features`.

## Which one should I run?

- **Just want to understand what Bajutsu is?** → [`tour`](tour/README.md). One command, no
  setup, and it writes a real `report.html` you can open. It's the on-ramp for everything below.
- **An iOS developer with a Simulator, want to see it for real?** → [`features`](features/WEBUI.md).
  The Web UI tour boots a device, runs the sample app, and shows the full evidence set in a
  browser. This is the headline demo.
- **Want the AI authoring + self-healing loop on the command line?** → [`record`](record/README.md).

## The two tiers, made concrete

Every demo is an instance of the same two-tier design ([README](../README.md#core-principles)):

- **Tier 1 — AI is the author and the failure investigator.** It *writes* scenarios
  (`record`, the Web UI's Record tab) and *investigates* failures (`triage`), and it handles
  system alerts during a run. It never decides pass/fail.
- **Tier 2 — the deterministic run is the gate.** `run` replays a scenario with no AI; the
  verdict comes only from machine-checkable assertions, with the evidence to audit it attached.

The `tour` runs both tiers against a fake device so you can see the seam with nothing installed;
`features` and `record` run them against a real Simulator.

## The sample apps

- **`sample`** ([`features/app/`](features/app/README.md)) — a SwiftUI fixture built to exercise
  *every* supported interaction and evidence type. The `features` Web UI tour and the
  on-device CI workflows run against it.
- **`sample2`** ([`record/app/`](record/README.md)) — a minimal onboarding → login → counter
  app used by the `record` lifecycle demo. The `tour` mocks this same flow in memory.

Generated scenarios (`*/generated.yaml`) and run artifacts (`runs/`) are gitignored — regenerate
them by running the demos.

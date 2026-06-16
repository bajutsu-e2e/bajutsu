# Bajutsu demos

Three runnable demos, ordered by how much setup they need. Each tells the same core story —
**a natural-language goal becomes a deterministic scenario; a run decides pass/fail with
machine assertions only, never an LLM** — at a different depth. Start at the top and go as deep
as your setup allows.

| Demo | What it proves | What it needs | Audience |
|---|---|---|---|
| **[tour](tour/README.md)** — `uv run python demos/tour/tour.py` | The whole lifecycle (author → run → modify → diagnose) end to end, on the real pipeline | **Nothing.** No Simulator, no idb, no API key — runs on Linux/CI in seconds | Anyone evaluating the tool; the 60-second first look |
| **[features](features/WEBUI.md)** — `make -C demos/features serve` | The **Web UI** driving a real Simulator and collecting every evidence type: screenshots, video, device logs, network (observed + mocked), visual regression, system-alert handling | macOS + Simulator (idb auto-installs); an API key for the system-alert part only | **iOS developers** evaluating on a real device |
| **[record](record/README.md)** — `./demos/record/demo.sh` | AI authoring with **real Claude** against a booted app, then the modify-and-self-heal loop (`triage`) on the CLI | macOS + Simulator + idb + Claude (CLI or API key) | Anyone wanting to see the AI authoring loop on-device |

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

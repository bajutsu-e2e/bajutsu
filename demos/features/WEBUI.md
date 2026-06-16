# The Web UI tour — drive a real Simulator, collect every kind of evidence

This is the demo for an **iOS developer evaluating Bajutsu on a real Simulator**. It uses the
local Web UI ([`bajutsu serve`](../../docs/cli.md)) to boot a Simulator, run scenarios against
the bundled sample app, and browse the evidence each run captures — screenshots, video, device
logs, network exchanges, visual-regression diffs, and system-alert handling — all from a
browser, with **no AI in the pass/fail decision**.

Want the same story with zero setup first (no Mac/Simulator)? Run the
[60-second tour](../tour/README.md): `uv run python demos/tour/tour.py`.

## Prerequisites

- macOS with Xcode and a Simulator you can boot (`open -a Simulator`).
- The idb backend — the Web UI installs it on demand, but you can pre-install:
  `brew install facebook/fb/idb-companion && uv sync --extra idb`.
- For the system-alert part only: `ANTHROPIC_API_KEY` (env or a gitignored `.env`). Everything
  else is fully deterministic and needs no key.

You do **not** need to build the app first — the Web UI runs the configured `build` command and
installs the `.app` on the picked device the first time it's missing.

## Launch it

```bash
make -C demos/features serve         # http://127.0.0.1:8765  (PORT=8766 to change)
```

This wraps the repo-root `make serve` and binds [`demo.config.yaml`](demo.config.yaml), which
points the Web UI at the `sample` app and its scenarios in
[`app/scenarios/`](app/scenarios). Open the printed URL.

> Always start the Web UI via `make serve` (never a bare `bajutsu serve`): the wrapper installs
> the idb client + companion on demand, so a fresh checkout doesn't fail with
> `no available actuator among ['idb']`.

## The tour

The UI has two tabs: **Replay** (run scenarios deterministically, view reports) and **Record**
(author a scenario from a natural-language goal with an agent). The tour below is in Replay
unless noted.

### 1. Run a scenario and read the report

In **Replay → Run**: pick the `sample` app, pick a Simulator from the list (a shut-down one is
booted for you; "booted" means whatever is already running), select `smoke.yaml`, and **Run**.
You'll watch the live log boot the device, build/install the app on first use, then execute. On
completion an interactive **report** loads inline.

Open the report's **Result** panel: every step has its **screenshot** and the **accessibility
tree** it acted on, with the matched element highlighted; the **Expectations** table shows each
machine assertion that decided pass/fail. This is the core promise — a deterministic verdict you
can audit step by step.

### 2. Network exchanges (observe, then mock)

Run [`network.yaml`](app/scenarios/network.yaml): the report grows a **Network** tab listing
every HTTP(S) exchange the app made — method, URL, status, duration, headers, body — captured
*after* TLS by BajutsuKit (no proxy, no CA). The scenario also **asserts on requests** (a
`request:` expectation), so the network itself is part of the deterministic check.

Then run [`network_mock.yaml`](app/scenarios/network_mock.yaml): the same screen, but a `mocks:`
rule serves a canned response (e.g. status `418`) in place of the real endpoint. The Network tab
flags the exchange as **mocked** — deterministic network with no live dependency.

### 3. Visual regression (VRT) — and approve a baseline from the UI

Run [`visual.yaml`](app/scenarios/visual.yaml). The first time there is **no baseline**, so the
`visual` expectation fails on purpose and the report shows the captured screenshot with a
**"Approve as baseline"** button. Click it — the screenshot is promoted to the baselines folder.
Re-run: now the run **pixel-compares** against that baseline and passes; if the screen later
drifts, the report shows a side-by-side **diff** (swipe / onion / blend modes) with the diff
percentage. A `threshold` tolerates anti-aliasing jitter and `exclude` rectangles mask volatile
regions (clock, status bar).

> The same approve step on the CLI: `make -C demos/features vrt` then `vrt-approve`.

### 4. System alerts — punching through a SpringBoard prompt

idb's accessibility query only sees the foreground app, so an iOS system prompt ("Save
Password?", a permission dialog) silently blocks a run. With **Dismiss alerts** enabled (the
default; needs `ANTHROPIC_API_KEY`), run [`permission.yaml`](app/scenarios/permission.yaml):
when a prompt blocks a step, the guard screenshots it, asks Claude vision where the dismiss
button is, taps it, and the step retries. The report's steps table shows a **"system alert
dismissed"** row with the alert's label. This is the one place AI assists a `run` — and it only
*acts on the device*, never on the verdict. A scenario can set `dismissAlerts: { instruction:
"tap Allow" }` to grant instead of dismiss.

### 5. Video and device logs

Run [`evidence.yaml`](app/scenarios/evidence.yaml). Its `capturePolicy` records whole-scenario
intervals, so the report gains a **Video** tab (the screen recording, with a seek bar) and a
**Log** tab (the device log, searchable), alongside an app **Trace** of `os_signpost`
intervals. "Capture on every X" is expressed once as a rule and reproduced on every run with no
AI — evidence as configuration.

### 6. Author a new scenario from natural language (Record tab)

Switch to **Record**: type a goal ("increment the counter twice and check it reads 2"), pick the
app and agent, and **Record**. The agent reads the live screen and proposes one step at a time
(streamed: reasoning → action); the executed steps are written out as a deterministic scenario
you can edit and **Save**, then replay from the Replay tab. This is Tier 1 (AI authoring) handing
off to Tier 2 (deterministic replay) — the whole architecture, in one window.

## What this demonstrates

- **One verdict you can trust and audit** — pass/fail from machine assertions only, with the
  screenshots / element tree / network / video that justify it attached to every step.
- **Breadth of evidence** — UI state, network (observed and mocked), visual regression, system
  alerts, video, and logs, captured by reusable rules rather than ad-hoc scripting.
- **AI where it helps, never where it judges** — authoring (Record) and alert handling assist
  the *operation*; the gate stays deterministic.

Other demos: the [zero-setup tour](../tour/README.md) (this story, no Mac) and the
[record lifecycle](../record/README.md) (AI authoring → run → modify → triage on the CLI). Map:
[`demos/README.md`](../README.md).

# The Web UI tour — drive a real Simulator, collect every kind of evidence

**English** · [日本語](WEBUI.ja.md)

This is the demo for an **iOS developer evaluating Bajutsu on a real Simulator**. It uses the
local Web UI ([`bajutsu serve`](../../docs/cli.md)) to boot a Simulator, run scenarios against
the showcase app, and browse the evidence each run captures — screenshots, video, device logs,
network exchanges, visual-regression diffs, and system-alert handling — all from a browser, with
**no AI in the pass/fail decision**.

Want the same story with zero setup first (no Mac/Simulator)? Run the
[60-second tour](../tour/README.md): `uv run python demos/tour/tour.py`.

## Prerequisites

- macOS with Xcode and a Simulator you can boot (`open -a Simulator`).
- The XCUITest runner — the Web UI builds it on demand, but you can pre-build:
  `make -C demos/showcase runner-build`.
- For the system-alert part only: `ANTHROPIC_API_KEY` (env or a gitignored `.env`). Everything
  else is fully deterministic and needs no key.

You do **not** need to build the app first — the Web UI runs the configured `build` command and
installs the `.app` on the picked device the first time it's missing.

## Launch it

```bash
make -C demos webui                  # http://127.0.0.1:8765  (PORT=8766 to change)
```

This wraps the repo-root `make serve` and binds [`showcase.config.yaml`](showcase.config.yaml),
which points the Web UI at the showcase apps and their scenarios in [`scenarios/`](scenarios).
Open the printed URL.

> Always start the Web UI via `make serve` (never a bare `bajutsu serve`): the wrapper installs
> the backend's deps on demand, so a fresh checkout doesn't fail with
> `no available actuator among ['xcuitest']`.

## The tour

The UI has two tabs: **Replay** (run scenarios deterministically, view reports) and **Record**
(author a scenario from a natural-language goal with an agent). The tour below is in Replay
unless noted.

### 1. Run a scenario and read the report

In **Replay → Run**: pick the `showcase-swiftui` app, pick a Simulator from the list (a shut-down
one is booted for you; "booted" means whatever is already running), select `smoke.yaml`, and
**Run**. You'll watch the live log boot the device, build/install the app on first use, then
execute. On completion an interactive **report** loads inline.

Open the report's **Result** panel: every step has its **screenshot** and the **accessibility
tree** it acted on, with the matched element highlighted; the **Expectations** table shows each
machine assertion that decided pass/fail. This is the core promise — a deterministic verdict you
can audit step by step.

### 2. Network exchanges (observe, then mock)

The showcase links **BajutsuKit**, so every HTTP(S) exchange the app makes is captured *after*
TLS (no proxy, no CA). Run a scenario that hits the network — the Stable catalog's `stable.refresh`
or the Log tab's `log.submit` — and the report grows a **Network** tab listing each exchange:
method, URL, status, duration, headers, body. A request deliberately carries a secret header and
body field, so you can see redaction masking them in place.

Then run [`network_mock.yaml`](scenarios/network_mock.yaml): the same surface, but a `mocks:` rule
serves a canned response in place of the real endpoint. The Network tab flags the exchange as
**mocked** — deterministic network with no live dependency.

### 3. Visual regression (VRT) — and approve a baseline from the UI

Run [`visual.yaml`](scenarios/visual.yaml). The first time there is **no baseline**, so the
`visual` expectation fails on purpose and the report shows the captured screenshot with an
**"Approve as baseline"** button. Click it — the screenshot is promoted to the baselines folder.
Re-run: now the run **pixel-compares** against that baseline and passes; if the screen later
drifts, the report shows a side-by-side **diff** (swipe / onion / blend modes) with the diff
percentage. A `threshold` tolerates anti-aliasing jitter and `exclude` rectangles mask volatile
regions (clock, status bar, the floating tab bar). The UIKit and SwiftUI twins render the same
screens but not pixel-identically, so each keeps its own baseline set.

> The same approve step on the CLI: `make -C demos/showcase vrt` then `vrt-approve`.

### 4. System alerts — punching through a SpringBoard prompt

The iOS backend's accessibility query only sees the foreground app, so an iOS system prompt (a permission
dialog) silently blocks a run. With **Dismiss alerts** enabled (the default; needs
`ANTHROPIC_API_KEY`), run [`permission.yaml`](scenarios/permission.yaml): the Permissions tab
raises the notification / location prompts, and when one blocks a step the guard screenshots it,
asks Claude vision where the dismiss button is, taps it, and the step retries. The report's steps
table shows a **"system alert dismissed"** row with the alert's label. This is the one place AI
assists a `run` — and it only *acts on the device*, never on the verdict. A scenario can set
`alertHandling: { instruction: "tap Allow" }` to grant instead of dismiss.

### 5. Video and device logs

Every scenario records a whole-scenario screen recording, so any run's report has a **Video** tab
(with a seek bar) and a **Log** tab (the device log, searchable), alongside an app **Trace** of
`os_signpost` intervals. Run [`modals.yaml`](scenarios/modals.yaml) — the four modal styles
opening and dismissing make the video especially legible. "Capture on every X" is expressed once
as a rule and reproduced on every run with no AI — evidence as configuration.

### 6. Author a new scenario from natural language (Record tab)

Switch to **Record**: type a goal ("open the third horse and turn on Favorite"), pick the app and
agent, and **Record**. The agent reads the live screen and proposes one step at a time (streamed:
reasoning → action); the executed steps are written out as a deterministic scenario you can edit
and **Save**, then replay from the Replay tab. Pick the `showcase-swiftui-noax` app to watch the
agent fall down the stability ladder when the app exposes no identifiers — the accessibility A/B
the showcase is built for. This is Tier 1 (AI authoring) handing off to Tier 2 (deterministic
replay) — the whole architecture, in one window.

## What this demonstrates

- **One verdict you can trust and audit** — pass/fail from machine assertions only, with the
  screenshots / element tree / network / video that justify it attached to every step.
- **Breadth of evidence** — UI state, network (observed and mocked), visual regression, system
  alerts, video, and logs, captured by reusable rules rather than ad-hoc scripting.
- **AI where it helps, never where it judges** — authoring (Record) and alert handling assist
  the *operation*; the gate stays deterministic.

Other demos: the [zero-setup tour](../tour/README.md) (this story, no Mac) and the
[showcase record A/B](README.md) (AI authoring → run → modify → triage on the CLI). Map:
[`demos/README.md`](../README.md).

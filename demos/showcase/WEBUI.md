# The Web UI tour — drive a real Simulator, collect every kind of evidence

**English** · [日本語](WEBUI.ja.md)

This is the demo for an **iOS developer evaluating Bajutsu on a real Simulator**. It uses the
local Web UI ([`bajutsu serve`](../../docs/cli.md)) to boot a Simulator, run scenarios against
the showcase app, and browse the evidence each run captures: screenshots, video, device logs,
network exchanges, visual-regression diffs, and system-alert handling. The whole tour runs in a
browser, with **no AI in the pass/fail decision**.

To see the same story first with zero setup (no Mac or Simulator), run the
[60-second tour](../tour/README.md): `uv run python demos/tour/tour.py`.

## Prerequisites

- macOS with Xcode and a Simulator you can boot (`open -a Simulator`).
- The XCUITest runner. The Web UI builds it on demand, but you can pre-build it with
  `make -C demos/showcase runner-build`.
- For the system-alert part only: `ANTHROPIC_API_KEY` (env or a gitignored `.env`). Everything
  else is fully deterministic and needs no key.

You do **not** need to build the app first. The first time the `.app` is missing, the Web UI runs
the configured `build` command and installs the result on the picked device.

## Launch it

```bash
make -C demos webui                  # http://127.0.0.1:8765  (PORT=8766 to change)
```

The target wraps the repo-root `make serve` and binds [`showcase.config.yaml`](showcase.config.yaml),
which points the Web UI at the showcase apps and their scenarios in [`scenarios/`](scenarios).
Open the printed URL.

> Always start the Web UI via `make serve` (never a bare `bajutsu serve`). The wrapper installs
> the backend's deps on demand, so a fresh checkout doesn't fail with
> `no available actuator among ['xcuitest']`.

## The tour

The UI has two tabs: **Replay** (run scenarios deterministically, view reports) and **Record**
(author a scenario from a natural-language goal with an agent). Unless noted, the tour below
happens in Replay.

### 1. Run a scenario and read the report

In **Replay → Run**, pick the `showcase-swiftui` app and a Simulator from the list. The tool boots a shut-down
Simulator for you, and "booted" means whatever is already running. Select `smoke.yaml`
and click **Run**. The live log shows the device booting, the app building and installing on first
use, and then the scenario executing. On completion, an interactive **report** loads inline.

Open the report's **Result** panel. Every step shows its **screenshot** and the **accessibility
tree** it acted on, with the matched element highlighted. The **Expectations** table lists each
machine assertion that decided pass/fail. This is the core promise: a deterministic verdict you
can audit step by step.

### 2. Network exchanges (observe, then mock)

The showcase links **BajutsuKit**, so every HTTP(S) exchange the app makes is captured *after*
TLS (no proxy, no CA). Run a scenario that hits the network, such as the Stable catalog's
`stable.refresh` or the Log tab's `log.submit`. The report then grows a **Network** tab listing
each exchange: method, URL, status, duration, headers, and body. One request deliberately carries
a secret header and body field, so you can see redaction masking them in place.

Then run [`network_mock.yaml`](scenarios/network_mock.yaml). It exercises the same surface, but a
`mocks:` rule serves a canned response in place of the real endpoint. The Network tab flags the
exchange as **mocked**, giving a deterministic network with no live dependency.

### 3. Visual regression (VRT) — and approve a baseline from the UI

Run [`visual.yaml`](scenarios/visual.yaml). The first time there is **no baseline**, so the
`visual` expectation fails on purpose and the report shows the captured screenshot with an
**"Approve as baseline"** button. Click it to promote the screenshot to the baselines folder.

Re-run, and the run now **pixel-compares** against that baseline and passes. If the screen later
drifts, the report shows a side-by-side **diff** (swipe / onion / blend modes) with the diff
percentage. A `threshold` tolerates anti-aliasing jitter, and `exclude` rectangles mask volatile
regions (clock, status bar, the floating tab bar). The UIKit and SwiftUI twins render the same
screens but not pixel-identically, so each keeps its own baseline set.

> The same approve step on the CLI: `make -C demos/showcase vrt` then `vrt-approve`.

### 4. System alerts — punching through a SpringBoard prompt

The iOS backend's accessibility query sees only the foreground app, so an iOS system prompt (a
permission dialog) silently blocks a run. With **Dismiss alerts** enabled (the default), run
[`permission.yaml`](scenarios/permission.yaml). The Permissions tab raises the notification prompt.
The reactive alert guard answers it with a SpringBoard accessibility query and a tap. It needs no
screenshot, no model round trip, and no `ANTHROPIC_API_KEY`. The report's steps table then shows a
**"system alert dismissed"** row carrying the button's label.

The scenario declares which prompt to expect and what to answer on it. The `interrupts` entry
`{ systemAlert: notifications, choice: grant }` grants the prompt, and `choice: deny` refuses it
instead. An alert that no entry identifies is left alone. The blocked step's own failure reason
names the buttons the alert offered.

### 5. Video and device logs

Every scenario records a whole-scenario screen recording, so any run's report has a **Video** tab
(with a seek bar) and a **Log** tab (the device log, searchable), alongside an app **Trace** of
`os_signpost` intervals. Run [`modals.yaml`](scenarios/modals.yaml): the four modal styles
opening and dismissing make the video especially legible. "Capture on every X" is expressed once
as a rule and reproduced on every run with no AI, which makes evidence a matter of configuration.

### 6. Author a new scenario from natural language (Record tab)

Switch to **Record**, type a goal ("open the third horse and turn on Favorite"), pick the app and
agent, and click **Record**. The agent reads the live screen and proposes one step at a time,
streamed as reasoning, then action. The executed steps are written out as a deterministic scenario
that you can edit and **Save**, then replay from the Replay tab.

Pick the `showcase-swiftui-noax` app to watch the agent fall down the stability ladder when the app
exposes no identifiers. This is the accessibility A/B the showcase is built for. It is also Tier 1
(AI authoring) handing off to Tier 2 (deterministic replay): the whole architecture, in one window.

## What this demonstrates

- **One verdict you can trust and audit** — pass/fail comes from machine assertions only, with the
  screenshots, element tree, network, and video that justify it attached to every step.
- **Breadth of evidence** — UI state, network (observed and mocked), visual regression, system
  alerts, video, and logs, captured by reusable rules rather than ad-hoc scripting.
- **AI where it helps, never where it judges** — authoring (Record) and alert handling assist
  the *operation*, and the gate stays deterministic.

Other demos: the [zero-setup tour](../tour/README.md) (this story, no Mac) and the
[showcase record A/B](README.md) (AI authoring → run → modify → triage on the CLI). Map:
[`demos/README.md`](../README.md).

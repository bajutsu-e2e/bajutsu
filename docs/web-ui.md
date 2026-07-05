**English** · [日本語](ja/web-ui.md)

# Web UI (the `serve` browser app)

> A task-oriented guide to the browser UI you get from `bajutsu serve`: what each tab does and how
> to drive it. For the launch flags, authentication, and hosting details, this page defers to the
> [CLI reference](cli.md#serve) and [self-hosting](self-hosting.md) — it covers the screen, not the
> command line.
>
> Implementation: `bajutsu/serve/` (a stdlib server) with the markup in `bajutsu/templates/serve.html.j2`.

Related: [CLI reference](cli.md#serve) · [scenarios](scenarios.md) · [recording](recording.md) · [reporting](reporting.md) · [selectors](selectors.md) · [configuration](configuration.md) · [self-hosting](self-hosting.md)

---

## What the Web UI is, and when to use it

The Web UI is a **Tier-1 convenience** — a browser front-end over the same CLI commands you would
run by hand. It is **not part of the CI (continuous integration) gate**: the deterministic
`run` verdict never depends on it, and nothing here judges pass/fail. Reach for it to **author** a
scenario from a natural-language goal, **run** a scenario and read its report, **explore** an app
and watch its screen map draw itself, **edit** a scenario against real screenshots, and **survey**
the run history — all against one active config, without switching between terminal commands.

Most of what the UI does maps onto a CLI command (`record`, `run`, `crawl`, `stats`, `lint`); the
exception is the **Author** tab, whose Capture / Edit / Enrich flows are serve-only helpers for
building and editing scenario files, with no CLI twin. When a detail here needs the full option
list, the [CLI reference](cli.md) is the source of truth.

### Launch it

In a repository checkout, start the server with `make serve` — the one-step path. `make serve`
([`scripts/serve.sh`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/serve.sh)) provisions
the iOS backend's on-demand dependencies (the idb client and `idb_companion`) and then runs the
server; without them, iOS runs fail with `no available actuator`. Pass flags through `ARGS`:

```bash
make serve                                                        # the default port (8765)
make serve ARGS="--config demos/showcase/showcase.config.yaml --port 8766"   # the showcase app
```

The showcase config is needed for the showcase app, since the repository has no root
`bajutsu.config.yaml`.

Under the hood `make serve` runs `python -m bajutsu serve` — the `bajutsu serve` command the
[CLI reference](cli.md#serve) documents. If you installed Bajutsu outside a checkout (so there is no
`make`), run `bajutsu serve` (or `python -m bajutsu serve`) directly; you are then responsible for
the backend's dependencies yourself — the idb client and `idb_companion` for an iOS target (a web /
Playwright target does not need them). Either way the server does not open a browser for you: it
binds `127.0.0.1`, so once it is running, open the printed URL yourself (`http://127.0.0.1:8765` by
default, or the `--port` you passed). The full option list — `--port`, `--config`, `--root`,
`--runs`, `--baselines`, `--host`, `--token`, `--max-concurrent-runs`, `--evidence-store` — is in
the [CLI reference](cli.md#serve).

## The layout

The header holds five top-level tabs: **Record**, **Replay**, **Crawl**, **Author**, and **Stats**.
To their right are **Open config** (with the active config's name shown beside it once one is bound),
**Settings**, and a dark/light theme toggle that follows your system by default. Each tab is a full
screen of its own; switching tabs never discards what another tab was doing.

Some forms change with the backend. Against the iOS Simulator (idb) you see a **Device** picker,
a **Simulators** multi-select, and an **erase device first** option; against a web target (Playwright
/ Chromium) those give way to a **show browser (headed)** option. The rest of each tab is the same
across backends. The **Replay** tab also carries a **History** list of past runs (see below).

## Choosing the active config

Every tab runs against one **active config**. Click **Open config** to bind it. The dialog offers
three sources:

- **From a Git repository.** Enter a spec in the form `github:owner/repo@ref:path/to/bajutsu.config.yaml`
  and click **Load**. The `ref` is a branch, tag, or commit SHA (default: the repository's default
  branch); the whole subtree is fetched, so the config's scenarios and built app come with it.
- **Upload a bundle (.zip).** Click **Choose a .zip…** (or drop a file on the box) to bring your own
  suite to a hosted server with no file-system access. The zip is a working local checkout — a
  `bajutsu.config.yaml`, its `scenarios` tree, and the built app binary the config's `appPath` names.
  It carries no secrets; `${secrets.*}` resolve from the server's environment.
- **or browse the server.** A file browser, confined to the server's `--root`, for picking a local
  `.yml`/`.yaml` config. A hosted deployment hides this source, since a remote user has no
  file-system relationship to the host.

The full behavior of each source — the content-addressed Git cache, the bundle's sandboxing and
zip-slip hardening, the `--root` confinement — is documented in the
[CLI reference](cli.md#serve). Only one config is bound at a time; opening another replaces it.

## Settings

**Settings** holds the two choices the AI paths need. Nothing here is written to disk: settings live
in the running server's memory for the session only and reset when `serve` restarts. For values that
survive a restart, set `ANTHROPIC_API_KEY` / `AWS_*` in your shell or a `.env` before launching.

**AI provider** picks the backend that authoring (Record and Crawl) uses:

- **Anthropic API** — the pay-per-token API, authenticated by the Claude API key set below.
- **Amazon Bedrock** — Claude through AWS, authenticated by your standard AWS credentials; it adds
  **AWS region** and **Bedrock model id** fields (a Bedrock id carries a provider prefix, e.g.
  `global.anthropic.…`, unlike the bare Anthropic id).
- **Claude Code** — the local `claude` CLI on your Pro/Max subscription (text-only authoring). It
  must be installed and authenticated (`claude setup-token`) in the environment that launched
  `serve`.

**Claude API key** is **write-once**: enter a key and **Save**, and it is shown only masked and never
displayed again — to change it, set a new one; **Clear** removes it. It powers the Anthropic API
provider and the alert guard (Bedrock uses AWS credentials instead). Only the AI paths need it:
**Record**, **Crawl**, and Replay with alert-dismiss on.

## Record — author a scenario from a goal

**What it does.** Explores toward a natural-language goal with AI and writes the resulting scenario,
turn by turn — the `record` command in the browser.

**How to use it.**

1. Type what you want in **Goal (natural language)** (for example, "increment the counter twice and
   check it reads 2").
2. Pick a **Target**. On iOS, also pick a **Device** (use the refresh button beside it to re-list
   simulators). On web, tick **show browser (headed)** to watch a visible Chromium window author in
   slow motion instead of the default headless browser.
3. Optionally set **Save as** — the file name for the scenario. Blank defaults to `generated.yaml`;
   if the name already exists, the run's date-time is appended so nothing is overwritten.
4. iOS options: **erase device first** wipes the simulator so the app starts from onboarding, and
   **disable alert-dismiss** turns off the vision alert guard for this authoring run.
5. Click **Generate scenario** (**Stop** aborts). The agent's turn-by-turn progress streams into the
   log.

**What happens.** When authoring finishes, the YAML appears in the **Generated scenario** panel.
Edit it there if you like, then click **Save** (disabled until there is something to save). The
scenario is written under the target's scenarios directory, so it appears in the **Replay** tab's
**Scenario** picker, ready to run. How the authoring loop and the alert guard work:
[recording](recording.md).

## Replay — run a scenario and read its report

**What it does.** Runs a scenario deterministically and embeds its report — the `run` command in the
browser. Two sub-tabs: **Run** and **History**.

**Run.**

1. Pick a **Scenario** and a **Target**.
2. On iOS: pick the **Simulators** to run on (shut-down ones are booted first; pick two or more to
   run scenarios in parallel across them, and **Workers** tracks the count). **erase device first**
   and **disable alert-dismiss** apply as in Record. On web: tick **show browser (headed)** to watch
   the run in a visible Chromium window.
3. Click **Run** (**Stop** aborts). The output streams live in the log, and the run's `report.html`
   embeds beside it on completion.

Inside the embedded report, a visual check's **Approve** button promotes that run's captured
screenshot into the visual baseline (the same promotion as the `approve` CLI command), so the next
run compares against it — the review step of the run → review → approve → re-run loop. What each
report section shows: [reporting](reporting.md).

**History.** The sub-tab lists past runs, newest first, each with a pass/fail dot and a scenario
summary. Click one to reopen its report. Use the refresh button to re-list.

## Crawl — explore the app and map its screens live

**What it does.** Explores the app breadth-first and draws the reachable screens and the transitions
between them as they are discovered — the `crawl` command in the browser. It is a **discovery tool,
never a pass/fail gate**: the exploration engine (screen identity, transitions, crashes) is
deterministic, while the AI provider chosen in **Settings** proposes *what to try*.

**How to use it.**

1. Pick a **Target** and set **Workers** (parallel browser processes or, on iOS, simulators sharing
   one screen map). On iOS, also pick the **Simulators** to crawl on.
2. Set the budget: **Max screens** (default 50) and **Max steps** (default 200). The crawl stops at
   the first limit reached.
3. iOS has **erase device first** and **disable alert-dismiss**; web has **show browser (headed)**.
4. Click **Start crawl** (**Stop** aborts). Progress reports into the status line and the **Console**.

**What you get** (three views beside the form):

- **Screen map** — the graph of discovered screens and transitions, drawn live as the crawl advances.
  Zoom with the **−** / reset / **+** controls. Screens that are the same UI in a different transient
  state (an empty vs. filled form) collapse into one node you can expand in place.
- **Exploration plan** — the plan tree of still-untried operations per screen, with a progress bar.
  **show pruned** toggles the operations the crawl pruned as duplicate global controls (a tab or nav
  bar explored once), struck through and clickable to resume.
- **Console** — the crawl's progress log.

Clicking a screen in the map opens a **screenshot lightbox**: the enlarged screen with **‹** / **›**
to step to a transition into or out of it, and hotspots marking where each transition was taken.

The screen map, the fingerprint that identifies a screen, and the web-vs-iOS differences are covered
in the [CLI reference](cli.md#crawl).

## Author — capture, edit, and enrich one scenario

**What it does.** Works on one open scenario in three modes, chosen with the **Capture** / **Edit** /
**Enrich** buttons. Switching mode never reloads the scenario or drops unsaved YAML edits.

**Capture** — build a scenario by clicking a live screenshot.

1. Pick a **Target** and click **Start capture** to begin a live session.
2. Set **Action mode** to **tap** or **type** (for **type**, fill in **Text to type**), then click on
   the screenshot to record each action.
3. Click **Finish & save** to write the captured flow.

**Edit** — fix a step against the screenshot the run captured.

1. Choose a **Scenario** and a **Run** and click **Load**.
2. Step through with **← Prev** / **Next →**; the label shows which step is loaded.
3. Click on the screenshot to re-target the step, then **Apply** the change to the YAML.

**Enrich** — add suggested assertions.

1. Load a scenario, then click **Enrich**.
2. The **Proposed assertions** panel lists suggestions; **Accept** folds them into the YAML,
   **Dismiss** discards them.

**The YAML editor** (shared by all three modes) validates as you type: a debounced check runs the
same `bajutsu lint` rules and shows line-anchored diagnostics as a gutter marker on each offending
line and a clickable problems list — click a finding to jump to its line. The scenario JSON Schema
also drives lightweight key completion (Ctrl/⌘+Space) and hover descriptions. This is deterministic
and AI-free. Click **Save** to write the scenario. The grammar these checks enforce:
[scenarios](scenarios.md); how selectors are graded: [selectors](selectors.md).

**Generate code** — export the loaded scenario as a native test, without leaving the browser. Load a
scenario, then click **Generate code**: the result opens in a read-only viewer you can **Copy** or
**Download** (the filename is derived from the scenario and destination, e.g. `LoginUITests.swift` /
`login.spec.ts`). The destination follows the target's backend — **XCUITest** for iOS, **Playwright**
for web — so only the format that target supports is offered. This is the [`codegen`](codegen.md)
command surfaced in the UI: a structural mapping from the scenario to the target framework's idiom,
so it runs no device, no AI, and computes no verdict; its known limits are `codegen`'s own.

## Stats — the run-history dashboard

**What it does.** Renders the aggregate run-stats dashboard across the server's run history — the
`stats` command in the browser. It is **read-only and advisory, not a verdict**: pass-rate over time,
the slowest and flakiest scenarios, failure hotspots, and run volume, aggregated from each run's
stored `manifest.json`.

**How to use it.** Open the tab to load the dashboard; use the refresh button to recompute it over
the current run history. No device, AI, or run is involved.

## Security and hosting

By default `make serve` binds `127.0.0.1` with no authentication — safe only on the loopback
interface. To expose it beyond your own machine you need a token (`--token`), which turns on
authentication and CSRF protection; binding a non-loopback `--host` requires one. Team hosting on a
single Mac (`--emit-launchagent`) or the full control-plane backend (`--backend server`), together
with `--max-concurrent-runs` and `--evidence-store`, are covered in the
[CLI reference](cli.md#serve) and [self-hosting](self-hosting.md).

---

Screenshots of each tab are a planned follow-up; this guide is text-only for now.

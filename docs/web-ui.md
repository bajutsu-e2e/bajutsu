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
the configured backend's on-demand dependencies (for an iOS target, the XCUITest runner via
`make -C demos/showcase runner-build`, on top of Xcode's `xcodebuild`) and then runs the
server; without them, iOS runs fail with `no available actuator`. Pass flags through `ARGS`:

```bash
make serve                                                        # the default port (8765)
make serve ARGS="--config demos/showcase/showcase.config.yaml --port 8766"   # the showcase app
```

The showcase app needs the showcase config, since the repository has no root
`bajutsu.config.yaml`.

Under the hood `make serve` runs `python -m bajutsu serve` — the `bajutsu serve` command the
[CLI reference](cli.md#serve) documents. If you installed Bajutsu outside a checkout (so there is no
`make`), run `bajutsu serve` (or `python -m bajutsu serve`) directly; you are then responsible for
the backend's dependencies yourself — the XCUITest runner (and Xcode) for an iOS target (a web /
Playwright target does not need them). Either way the server does not open a browser for you: it
binds `127.0.0.1`, so once it is running, open the printed URL yourself (`http://127.0.0.1:8765` by
default, or the `--port` you passed). The full option list — `--port`, `--config`, `--root`,
`--runs`, `--baselines`, `--themes`, `--host`, `--token`, `--max-concurrent-runs`,
`--evidence-store` — is in the [CLI reference](cli.md#serve).

## The layout

The header holds nine top-level tabs: **Record**, **Replay**, **Crawl**, **Author**, **Stats**,
**Flaky**, **Usage**, **Coverage**, and **Trash** (soft-deleted runs, restorable within a
retention window — see [Trash](#trash--restore-or-permanently-delete-a-deleted-run)). A tenth,
**Metrics**, appears only once a [project hub](#switching-between-projects) exists (more than one
project registered), since it compares projects against each other — see
[Comparing projects](#comparing-projects).
To their right are **Open config** (with the active config's name shown beside it once one is bound,
and a **View** button to inspect it — see below), **Settings**, and a theme picker that
follows your system by default. Each tab is a full screen of its own; switching tabs never discards
what another tab was doing.

Some forms change with the backend. Against the iOS Simulator (XCUITest) you see a **Device** picker,
a **Simulators** multi-select, and an **erase device first** option; against a web target (Playwright
/ Chromium) those give way to a **show browser (headed)** option. The rest of each tab is the same
across backends. The **Replay** tab also carries a **History** list of past runs (see below).

**Theme.** The header's picker chooses the visual theme. It lists every registered theme — the
built-in **Midnight** (dark) and **Daylight** (light), plus any you drop in with `--themes` (see
[the CLI reference](cli.md)) — grouped by dark and light. It follows your operating system's
preference (or the configured `ui.default_theme`) until you choose one; an explicit choice is
remembered (in the browser's local storage) until the system preference next changes, which drops
the override and re-adopts the system mode.

A theme also defines the UI's **motion** — how views, modals, and panels animate as they switch.
Screen transitions are part of the look, not hard-coded: a theme decides the duration, easing, and
enter/leave animation of a view switch, a modal open/close, and a tiler pane reconstruction through
its `--motion-*` tokens (documented alongside the color tokens in `serve.themes.css`). A theme that
sets no motion tokens keeps the built-in animation; one that sets an enter/leave token to `none`
renders that transition instantly. All motion collapses to instant under the operating system's
**reduce motion** accessibility preference, so the UI never animates against a user's wishes — and,
because a run drives the browser with that preference forced on, animation never affects a
deterministic `run`.

**Theme editor.** Click the **Edit** button next to the picker to open the in-UI theme editor. It
generates a form from the live token contract: a **name** and **kind** (dark/light) at the top, then
a native color swatch per color token and a text field per motion token. Edits apply instantly as a
live preview — the running UI reflects the change with no page reload. From the editor you can:
- **Save to Local Draft** — persists the edited theme in the browser's local storage and immediately
  surfaces it as a **custom** entry in the header picker, selecting it. The draft survives page
  reloads but is browser-local (not shared across serve sessions); reopening the editor resumes it.
- **Upload to Server** — writes the theme into the `--themes` directory as a discoverable drop-in,
  shared across sessions on that instance. The server derives the theme's id from the name, so an
  uploaded theme lists in the picker on the next load. This button appears **only when the instance
  was started with `--themes`** — without a themes directory there is nowhere to persist it.
- **Export** — downloads a `<name>-theme.css` file in the same drop-in format that `--themes`
  accepts, so a locally authored theme can be committed to a repo and shared.
- **Import** — loads a previously exported (or hand-authored) `.css` file and populates the form;
  any token in the file with no matching form field is reported rather than silently dropped.

**Panel management (desktop).** On a desktop-width window, the Record, Replay, and Crawl tabs tile
their panels: drag the divider between two panels to redistribute that pair's widths (the other
panels keep their share), and drag a panel's **⠿** grip onto another panel to swap them (drop on the
center) or split beside it (drop on an edge). Each view's layout persists in the browser's local
storage. Record's run-result pane joins and leaves this tiling as a run starts and is dismissed.

**Narrow windows.** Below phone width the tiling gives way to a single-column stack, and each view
gains a small switcher that brings one pane (form, log, report, …) to full width at a time. No
feature is lost — only the arrangement changes.

**When Claude is unreachable.** Record and Crawl need an AI provider (see
[Settings](#settings)). While Claude cannot be reached — no key, no signed-in CLI — those two tabs
read as disabled, their start buttons grey out, and an inline banner names what is missing with an
**Open Settings** shortcut. The state flips live as soon as a provider is configured; everything
else (Replay, Author, Stats, Flaky, Usage, Coverage) works without any AI setup.

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

The full behavior of each source is documented in the [CLI reference](cli.md#serve): the
content-addressed Git cache, the bundle's sandboxing and zip-slip hardening, and the `--root`
confinement. Only one config is bound at a time; opening another replaces it.

### Confirming what is bound

Click **View** (beside the config name) to see the exact config the tabs run from. The dialog opens
on a **Structured** view — a collapsible key/value tree, with each nested object and list a toggle you
expand only where you need it — and a **Raw** toggle switches to the verbatim YAML (comments and all).
Above both, it shows the config's path and — when it came from a Git repository — the source it was
materialized from: the repository, the `ref` you asked for, and the resolved commit SHA. This provenance
line matters for a Git source, whose active path is an opaque content-addressed cache location
(`…/gitsrc/<host>/<owner>/<repo>/<sha>/…`); the provenance line states which commit is actually
bound, not just the path. The config is shown verbatim: `${secrets.*}` placeholders appear as written
and are never resolved (they resolve from the server's environment at run time), so the view adds no
disclosure beyond the file itself. It is not a redactor, though — a secret written **literally** into
a local or uploaded config is shown as-is, so keeping secrets in `${secrets.*}` refs rather than in
the file is what keeps them out of this view. In a hosted deployment the endpoint is admin-only.

### Switching between projects

When you maintain several configs — several apps, or several targets of one app — `serve` is a
**hub** over them, not a single-config launcher. A **project** is a named binding to a config source.
The header carries a **switcher** (a picker beside the config viewer) that stays hidden until more
than one project is registered, and a top-level **Projects** tab that is the hub's home. Unlike the
switcher, the Projects tab shows whenever there is a project to manage — single-config included — so
you can grow into a hub from the UI itself.

On the Projects page each row shows the project name, its config source, and its latest run verdict.
**Switch** on a row **activates** that project: the server rebinds the active config to its source
with no restart, and every tab (Replay, Record, Crawl, the Stats dashboard) then operates against it
(the header switcher does the same quick switch). A project whose source is an uploaded bundle cannot
be switched to (there is no checkout to re-materialize); re-upload its config to bind it.

**Add a project** from the page with a name and a single config-source string — a Git spec
(`github:owner/repo[@ref][:path]`) or a local path — the same source
[`bajutsu project add --config`](cli.md#project) takes; an optional credential covers a private
repository. Re-adding an existing name rebinds its source. **Remove** deregisters a project after a
confirmation; its run history is kept, only the binding is dropped. The `bajutsu project` CLI edits
the same shared store, so a project added either way appears in both. Registering, removing, and
switching all rebind the active config, so in a hosted deployment they are admin actions like binding
a config — the server enforces that restriction, and a refused action shows inline on the page.

### Comparing projects

Once a hub holds more than one project, the **Metrics** tab opens a read-only comparison across all
of them at once — the question a single project's [Stats](#stats--the-run-history-dashboard)
dashboard cannot answer. Each project is one row: its run count, latest pass-rate, flaky-rate (the
share of its scenarios classified flaky over the window), and median (p50) and 95th-percentile (p95)
per-run duration, plus a pass-rate trend sparkline. Click a column header to rank by pass-rate,
flaky-rate, or duration — the first click surfaces the worst offender (lowest pass-rate, highest
flaky-rate or duration), a second click flips the order. A project with no runs yet charts as a blank
row rather than a misleading zero. Clicking a row switches to that project and opens its single-config
Stats dashboard, so the comparison is the entry point and the per-project view is the drill-down. Like
the other dashboards it is advisory only: it re-presents verdicts `run` already decided and never
gates anything.

## Settings

**Settings** holds the two choices the AI paths need. Nothing here is written to disk: settings live
in the running server's memory for the session only and reset when `serve` restarts. For values that
survive a restart, set `ANTHROPIC_API_KEY` / `AWS_*` in your shell or a `.env` before launching.

**AI provider** picks the backend that authoring (Record and Crawl) uses:

- **Anthropic API** — the pay-per-token API, authenticated by the Claude API key set below.
- **Amazon Bedrock** — Claude through AWS, authenticated by your standard AWS credentials; it adds
  **AWS region** and **Bedrock model id** fields (a Bedrock id carries a provider prefix, e.g.
  `global.anthropic.…`, unlike the bare Anthropic id).
- **Anthropic CLI (ant)** — Claude on your Pro/Max/Console seat through the official Anthropic CLI's
  OAuth/SSO sign-in, so no API key is needed and every path keeps full vision. The CLI must be
  installed in the environment that launched `serve`; a **Sign in with SSO** button then starts the
  browser sign-in for you (equivalent to running `ant auth login` in that terminal). The button
  opens the browser on the machine running `serve`, so it works with a local `make serve`; on a
  hosted deployment the server refuses the request (403) and the operator signs the host in out of band.
- **Claude Code CLI (claude)** — Claude on your Claude Code Pro/Max/Console subscription, by shelling
  out to the `claude` CLI. No API key is needed; sign in once with `claude setup-token` (or an
  interactive `claude` login) on the machine running `serve`. Any `ANTHROPIC_API_KEY` in the
  environment is ignored for this provider, so billing stays on the subscription.

The picker starts on a placeholder — there is **no default provider**, and **Save** refuses until
you pick one explicitly. Each provider's own fields (the API key, the Bedrock region and model id,
the CLI sign-in) appear only once that provider is selected. Three overrides apply across providers:
**Model** substitutes a specific model id for the default (Bedrock keeps its own prefixed id
field), **Reasoning effort** trades speed against depth for the authoring agents, and **Output
language** ([BE-0188](../roadmaps/BE-0188-configurable-ai-output-language/BE-0188-configurable-ai-output-language.md))
fixes the language the AI writes its generated prose in — `record`'s `from:` provenance and
`crawl`'s streamed reasoning. Its default *auto* keeps today's behavior (record follows the goal,
crawl stays English); it never affects the deterministic `run` verdict, and it is separate from a
target's device `locale`.

**Claude API key** (shown when the Anthropic API provider is selected) is **write-once**: enter a
key and **Save**, and it is shown only masked and never
displayed again — to change it, set a new one; **Clear** removes it. It powers the Anthropic API
provider and the alert guard (Bedrock uses AWS credentials instead). Only the AI paths need it:
**Record**, **Crawl**, and Replay with alert-dismiss on.

**Scenario secrets** ([BE-0274](../roadmaps/BE-0274-serve-scenario-secrets/BE-0274-serve-scenario-secrets.md))
lists the secrets the *bound config* declares — the environment-variable names under its `secrets:`
list that a scenario references as `${secrets.X}`
([Secrets](configuration.md#secrets-secrets), [BE-0032](../roadmaps/BE-0032-secret-variables/BE-0032-secret-variables.md)). The panel shows
one **write-once** field per declared name (masked, never displayed again, just like the API key), so
you can provision a scenario's credentials from the Web UI instead of `export`ing them or hand-editing
a `.env` before launching `serve`. A value set here materializes under its own declared name, and a
spawned **Replay** / **Record** / **Crawl** inherits it so `${secrets.X}` resolves in the run. The
panel is hidden when the bound config declares no secrets, and refreshes when you switch to a config
with a different `secrets:` list — only names the config itself declares are settable, never an
arbitrary environment variable. Setting one is an **admin** action on a role-gated deployment.

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

**Run it in place.** The **▶ Run** button beside Save (also disabled until there is YAML) runs the
panel's current content — the just-authored scenario, or YAML you pasted or edited by hand — without
switching tabs. A run-result pane opens with the live log and the finished report, with **Stop** to
abort and a close button to dismiss the pane. The verdict is the deterministic runner's, exactly as
on the Replay tab.

**Readiness (doctor).** A **Readiness** panel with a **Check** button sits in the form — the
`doctor` command in the browser, shared by the Record and Replay tabs. It reports two halves: the
runnability checks (is each required tool present, is a Simulator booted, is the web target's page
reachable), and — once the environment is runnable — the current screen's convention score, a
**Ready** / **Partial** / **Blocked** grade with the stable-id coverage and a what-to-fix list
(unnamed controls, off-namespace ids, duplicates). It probes the host environment, is read-only and
AI-free, and never gates a run.

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
   embeds beside it on completion. Before a run, the same **Readiness** panel as on Record (see
   above) answers "is my environment ready, and is my app addressable?" on a click — advisory only.

Under the log panel, a **Generate code** bar exports the selected scenario as a native test — the
same codegen as on the [Author](#author--capture-edit-and-enrich-one-scenario) tab, placed where a
scenario has just run.

Inside the embedded report, a visual check's **Approve** button promotes that run's captured
screenshot into the visual baseline (the same promotion as the `approve` CLI command), so the next
run compares against it — the review step of the run → review → approve → re-run loop. What each
report section shows: [reporting](reporting.md).

**History.** The sub-tab lists past runs, newest first, each with a pass/fail dot and a scenario
summary. Click one to reopen its report. Use the refresh button to re-list.

**Delete a run** ([BE-0239](../roadmaps/BE-0239-deletable-runs-serve/BE-0239-deletable-runs-serve.md)).
Each row carries a trash button that moves that run — its report, screenshots, video, and network
capture — to the [Trash](#trash--restore-or-permanently-delete-a-deleted-run), where it stays
restorable for the retention window rather than being erased at once. To clear several at once, tick
the per-row checkboxes (or **select all**) and use **Delete selected** in the bar above the list.
Both are editor-level actions; a soft-deleted run leaves the history immediately and reappears if you
restore it.

**Triage a failed run.** When a run fails, a **Triage** button appears in the report bar (both on a
just-run report and when you reopen a failed run from History) — the `triage` command in the
browser, so you can ask "why did this fail?" without dropping to a terminal. Click it, then
**Diagnose**: triage reads that run's stored failure context and reports a root-cause summary, the
implicated step, and suggestions. The default is the deterministic rule-based agent; tick **Claude**
(enabled only when an AI provider is configured — see [Settings](#settings)) to diagnose with the
Claude investigator instead. When triage can propose a mechanical fix (rename a selector id,
disambiguate a match, or raise a timeout), it shows the fix as a **diff** against the currently
selected scenario source. **Apply fix** writes that patch back through the same validated save path
the Author tab uses; **Apply & re-run** writes it and immediately re-runs the scenario to confirm.
The fix is written only on that explicit click — nothing is edited automatically, and the run's
pass/fail verdict is read from the deterministic run, never recomputed by AI. Triage previews and
applies against the scenario currently selected in the **Run** sub-tab, so pick the matching
scenario and target before triaging a run from History.

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

**History.** A crawl writes no `manifest.json` and so never appears in the Replay tab's History; its
runs are listed under the Crawl tab's own **History** sub-tab instead, keyed on the `screenmap.json`
every crawl streams. Each entry shows the run id and its screen / transition / crash counts, newest
first. Selecting one reopens that crawl's screen map **read-only** — the same interactive graph, drawn
from the stored map with no re-crawl — with a **past crawl** badge so it cannot be mistaken for a live
run and the live form disabled while it is shown. Beside the map, the run's `crashes/*.yaml` (a
replayable crash) and `flows/*.yaml` (a reachable screen) scenario files are linked directly; each opens
the raw YAML, ready to run with `bajutsu run`. Switch back to the **Form** sub-tab to return to the
live crawl form.

**Delete a crawl** ([BE-0239](../roadmaps/BE-0239-deletable-runs-serve/BE-0239-deletable-runs-serve.md)).
Like the Replay History, each crawl row carries a trash button, and a **select all** / **Delete
selected** bar clears several at once. A deleted crawl moves to the shared
[Trash](#trash--restore-or-permanently-delete-a-deleted-run), restorable within the retention window.

**Continue a past crawl** ([BE-0181](../roadmaps/BE-0181-crawl-continuation/BE-0181-crawl-continuation.md)).
When an open past run stopped on a budget with screens still left to explore, a **▸ continue exploring**
button appears on the map header. Clicking it leaves the read-only history view and continues that run
live — re-exploring its whole remaining frontier (every screen with untried operations) and appending to
the same map, using the current **Max screens** / **Max steps** so you can raise the budget in the same
step, and **Workers** / picked simulators to run the continuation in parallel. Tapping a struck-through
**pruned** operation while a past run is open likewise resumes exploring just that one branch. Both are a
deliberate action: the history view stays read-only until you ask to explore, so a past map is never
driven by accident.

The screen map, the fingerprint that identifies a screen, and the web-vs-iOS differences are covered
in the [CLI reference](cli.md#crawl).

## Trash — restore or permanently delete a deleted run

**What it does** ([BE-0239](../roadmaps/BE-0239-deletable-runs-serve/BE-0239-deletable-runs-serve.md)).
Lists the runs a delete from Replay or Crawl History moved to the trash — regular and crawl runs
share one trash — each with its deletion time. A soft delete is not destructive: the run leaves the
history lists but its bytes stay, so **Restore** returns it, intact, to the history it came from.

**The retention window.** A trashed run is permanently removed once it has sat in the trash past the
retention window (`BAJUTSU_RUN_RETENTION_DAYS`, 30 days by default; set it to `0` to keep trash
until someone deletes it by hand). The purge is lazy — it happens on the next history or trash read,
not on a background timer — so nothing runs when the server is idle. The header states the window in
effect.

**Delete forever.** Each trashed run also has a **Delete forever** action that skips the window and
erases its bytes at once. Being irreversible, it is an **admin**-only action on a hosted deployment
(a local, single-user `serve` has no roles, so it is always available there) and asks for a
distinct, emphatic confirmation. Every delete, restore, and permanent purge is written to the audit
log and emitted as an `oplog` event (`run.soft_deleted` / `run.restored` / `run.purged`), so an
operator can trace who removed which run and when.

## Author — capture, edit, and enrich one scenario

**What it does.** Works on one open scenario in three modes, chosen with the **Capture** / **Edit** /
**Enrich** buttons. Switching mode never reloads the scenario or drops unsaved YAML edits.

**Capture** — build a scenario by clicking a live screenshot.

1. Pick a **Target** and click **Start capture** to begin a live session.
2. Set **Action mode** to **tap** or **type** (for **type**, fill in **Text to type**), then click on
   the screenshot to record each action.
3. Click **Finish & save** to write the captured flow.

**Edit** — fix a step against a screenshot, from a past run or a live session.

1. Choose a **Scenario** and click **Load**. The **Run** picker lists only that scenario's own past
   runs, so a chosen run's steps line up with the scenario instead of silently mismatching; pick a
   **Run** to step through its captured screenshots.
2. No prior run? Click **Start live session** to boot the target and pick against its current screen —
   Edit needs no run. **Stop session** ends it without saving.
3. Step through with **← Prev** / **Next →**; the label shows which step is loaded.
4. Click on the screenshot to re-target the loaded step, then **Apply** the change to the YAML.

**Enrich** — add suggested assertions.

1. Load a scenario, then click **Enrich**.
2. The **Proposed assertions** panel lists suggestions; **Accept** folds them into the YAML,
   **Dismiss** discards them.

**The YAML editor** (shared by all three modes) validates as you type: a debounced check runs the
same `bajutsu lint` rules and shows line-anchored diagnostics as a gutter marker on each offending
line and a clickable problems list — click a finding to jump to its line. The scenario JSON Schema
also drives lightweight key completion (Ctrl/⌘+Space) and hover descriptions. This validation is
deterministic and AI-free. Click **Save** to write the scenario. The grammar these checks enforce:
[scenarios](scenarios.md); how selectors are graded: [selectors](selectors.md).

**The determinism audit badge** grades the same live YAML for stability, alongside the lint check.
A grade badge (**Stable** / **Moderate** / **Fragile**, with the ratio of `id`-based selectors)
sits in the editor header, and a findings list below the editor names each determinism risk — a
selector below the stability ladder, a `wait` with no concrete condition, a raw-coordinate gesture.
It is the [`audit`](cli.md) command's static score in the browser: read-only, device-free, and
AI-free, purely informational and never a gate. The **Replay** tab shows the same badge for the
scenario you have selected, so the stability signal is visible both where a scenario is written and
where it is run.

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

## Flaky — the ranked flaky-scenario surface

**What it does.** Ranks the server's run history by how much each scenario's verdict flips at a
constant content fingerprint — the `flakiness` command in the browser. It is **read-only and
advisory, not a verdict**: scenarios sorted flaky-first by verdict flip rate, each row showing the
pass/fail counts and its class (`flaky` / `deterministic` / `unproven`, reused from the determinism
audit) and linking to the representative passing and failing runs' evidence. When a database is wired
it groups straight from the provenance stamp on each run row (org-scoped); otherwise it builds the
same records from each run's stored `manifest.json`.

**How to use it.** Open the tab to load the ranking; use the refresh button to recompute it over the
current run history. A run with no scenario fingerprint or no recorded verdict cannot be grouped and
is reported as skipped. No device, AI, or run is involved.

**Diagnosing a flaky scenario.** Once this panel names a flaky scenario, the AI cross-run *fix
proposal* — reading its passing and failing runs together and explaining the delta — is a CLI
feature, `bajutsu triage --flaky` (see [CLI reference](cli.md#cross-run-flaky-triage---flaky)). It
is CLI-only by design: this panel is the deterministic ranking (no AI), while the cross-run proposal
needs an AI provider and stays a reviewable diff a human applies.

## Usage — the AI token-usage and cost dashboard

**What it does.** Renders the AI usage and cost dashboard over the attributed usage ledger the AI
paths record (record, crawl, triage, the alert locator). Like Stats it is **read-only and advisory,
never a verdict or a gate**: headline totals (tokens and dollars for the period), breakdowns by
provider, model, command, and scenario, a provider/model comparison (cost per call and per
scenario), and a daily cost trend — every figure a deterministic sum over the ledger, no model
consulted. A subscription provider or an unknown model records tokens without a per-token price, and
those calls are shown as unpriced (a "—") rather than a fabricated `$0.00`.

**How to use it.** Open the tab to load the dashboard; use the refresh button to recompute it over
the current ledger. When no usage has been recorded yet — the AI paths never ran, or persistence is
disabled — the tab shows an empty state explaining how recording is enabled (the ledger defaults to
`runs/usage.jsonl`; `ai.usageLedger` moves it, an empty string disables it). No device, AI, or run
is involved.

## Coverage — the E2E coverage map

**What it does.** Renders the E2E coverage map for a target — the `coverage` command in the browser.
It measures the scenario suite's stable-id references against the app's declared `idNamespaces`,
showing per-namespace coverage, the gap list (declared namespaces no scenario touches), and
off-namespace ids. When you select one or more past runs, it folds in the run-evidence dimensions:
endpoints observed vs asserted (the union of those runs' `network.json` against the suite's network
assertions) and observed ids vs declared namespaces (from each run's `elements.json`). Like Stats it
is **read-only and advisory, never a verdict or a gate** — every figure is a deterministic count, no
model is consulted.

**How to use it.** Pick a target, optionally select runs to add the run-evidence dimensions, and
press **Compute** to render the map. No device, AI, or run is involved.

## Security and hosting

By default `make serve` binds `127.0.0.1` with no authentication — safe only on the loopback
interface. To expose it beyond your own machine you need a token (`--token`), which turns on
authentication and CSRF protection; binding a non-loopback `--host` requires one. Team hosting on a
single Mac (`--emit-launchagent`) or the full control-plane backend (`--backend server`), together
with `--max-concurrent-runs` and `--evidence-store`, are covered in the
[CLI reference](cli.md#serve) and [self-hosting](self-hosting.md).

**Signing in.** On an authenticated server, the first request that comes back unauthorized starts
the sign-in: when GitHub OAuth is configured the browser is sent through it, and otherwise a token
prompt appears — enter the shared token, and a session cookie keeps you signed in from then on. On
an open (local) server neither ever appears.

---

Screenshots of each tab are a planned follow-up; this guide is text-only for now.

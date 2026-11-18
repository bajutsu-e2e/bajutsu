**English** · [日本語](ja/web-ui.md)

# Web UI (the `serve` browser app)

> A task-oriented guide to the browser UI you get from `bajutsu serve`: what each tab does and how
> to drive it. This page covers the screen, not the command line. For the launch flags,
> authentication, and hosting details, see the [CLI reference](cli.md#serve) and
> [self-hosting](self-hosting.md).
>
> Implementation: `bajutsu/serve/` (a stdlib server) with the markup in `bajutsu/templates/serve.html.j2`.

Related: [CLI reference](cli.md#serve) · [scenarios](scenarios.md) · [recording](recording.md) · [reporting](reporting.md) · [selectors](selectors.md) · [configuration](configuration.md) · [self-hosting](self-hosting.md)

---

## What the Web UI is, and when to use it

The Web UI is a **Tier-1 convenience** (see [the two tiers](glossary.md#the-two-tiers)): a browser
front-end over the same CLI commands you would run by hand. It is **not part of the CI (continuous
integration) gate**. The deterministic `run` verdict never depends on the Web UI, and nothing here
judges pass/fail. The UI covers these tasks, all against one active config and without switching
between terminal commands:

- **author** a scenario from a natural-language goal;
- **run** a scenario and read its report;
- **explore** an app and watch its screen map draw itself;
- **edit** a scenario against real screenshots;
- **survey** the run history.

Most of what the UI does maps onto a CLI command (`record`, `run`, `crawl`, `stats`, `lint`). The
exception is the **Author** tab, whose Capture / Edit / Enrich flows are serve-only helpers for
building and editing scenario files, with no CLI twin. When a detail here needs the full option
list, the [CLI reference](cli.md) is the source of truth.

### Launch it

In a repository checkout, start the server with `make serve`, the one-step path.
[`scripts/serve.sh`](https://github.com/bajutsu-e2e/bajutsu/blob/main/scripts/serve.sh) provisions
the configured backend's on-demand dependencies and then runs the server. On macOS, when the target
needs XCUITest, `make serve` also stages the bundled iOS Simulator runner via `make runner-bundle`
(built with Xcode's `xcodebuild` and `xcodegen`) whenever the bundle is missing or stale. Without the
bundle, iOS runs fail with `no available actuator`. Pass flags through `ARGS`:

```bash
make serve                                                        # the default port (8765)
make serve ARGS="--config demos/showcase/showcase.config.yaml --port 8766"   # the showcase app
```

The showcase app needs the showcase config, since the repository has no root
`bajutsu.config.yaml`.

The server does not drive a device for a run. It queues each run for a `bajutsu worker`, which leases
the run and drives the device or browser. For that reason `make serve` starts one worker beside the
server. The worker runs on the same machine with the same token, and it stops when the server stops.
It advertises the platforms of the startup config's targets. A target of another platform, bound
later, needs its own worker; the no-worker notice says so. To start the server alone, for example to
attach your own workers, set `SERVE_WORKER=0`:

```bash
SERVE_WORKER=0 make serve
```

A queued run says why it waits. The Replay status line shows a notice that no worker is available,
and `GET /api/jobs/<id>` carries the same text as `notice`. The notice clears once a worker that can
serve the run polls the server.

Triage, record, and crawl jobs go through the same worker, and the server runs no job in its own
process (BE-0449). A deployment with a bucket must name a database for its queue, or the server
refuses to start. While a crawl runs, the Crawl tab draws its graph from the screen map that the
worker relays. A record that pauses for a person shows its request as before, and the answer then
travels back to the worker.

Under the hood, `make serve` runs `python -m bajutsu serve`, the `bajutsu serve` command that the
[CLI reference](cli.md#serve) documents. If you installed Bajutsu outside a checkout, there is no
`make`, so run `bajutsu serve` (or `python -m bajutsu serve`) directly, and install the backend's
dependencies yourself: the XCUITest runner (and Xcode) for an iOS target. A web (Playwright) target
does not need them. Start the worker yourself too, in a second terminal, with `bajutsu worker`; a web
target needs `--platform web` on that worker.

Either way, the server does not open a browser for you. It binds `127.0.0.1`, so once it is running,
open the printed URL yourself (`http://127.0.0.1:8765` by default, or the `--port` you passed). The
flags `--port`, `--config`, `--root`, `--runs`, `--baselines`, `--themes`, `--host`, `--token`,
`--max-concurrent-runs`, and `--evidence-store` cover everyday use. The complete option list is in the
[CLI reference](cli.md#serve).

## The layout

The header holds nine top-level tabs: **Record**, **Replay**, **Crawl**, **Author**, **Stats**,
**Flaky**, **Usage**, **Coverage**, and **Trash**. The **Trash** tab lists soft-deleted runs, which
stay restorable within a retention window (see
[Trash](#trash--restore-or-permanently-delete-a-deleted-run)).

A tenth tab, **Comparison**, appears only once the bound config declares more than one
[target](glossary.md#target-app-device), since the tab compares targets against each other (see
[Comparing targets](#comparing-targets)). The tab is named for what it shows, not for the Prometheus
scrape endpoint that the same server exposes at `/metrics`, which is a different surface entirely.

To the right of the tabs are **Open config**, **Settings**, and a theme picker that follows your
system by default. Once a config is bound, its name appears beside **Open config**, together with a
**View** button to inspect the config (see below).

On a deployment where you sign in with GitHub, a small badge beside the config name names the **org**
this session acts as. Every tab is scoped to that org: the targets you can run, the runs and evidence
you see, and the secrets. Reading the badge at a glance matters, particularly for an admin who
administers several orgs. Hovering over the badge shows the login too. A local or shared-token
`serve` has no signed-in identity and no tenants, so the badge does not appear there.

If your GitHub memberships admit you to more than one org, the badge is a dropdown instead. Pick
another org and the page reloads acting as that org, with that tenant's runs, evidence, and secrets,
and with the role that tenant grants you, which may differ from the role you held in the previous
org. The choice sticks: your next sign-in lands you in the org you picked rather than re-deciding for
you. It stops sticking only if that org stops admitting you, in which case the next sign-in moves you
to the best remaining match. An admin who belongs to one of the deployment's admin Teams can act as
any org the deployment has, including an org created after the admin signed in. A switch does not
move work already done: a run keeps the org it was started under, and so do its evidence and its
audit entries.

Each tab is a full screen of its own, and switching tabs never discards what another tab was doing.

Some forms change with the backend. Against the iOS Simulator (XCUITest), you see a **Device**
picker, a **Simulators** multi-select, and an **erase device first** option. Against a web target
(Playwright / Chromium), those give way to a **show browser (headed)** option. The rest of each tab is
the same across backends. The **Replay** tab also carries a **History** list of past runs (see below).

**Theme.** The header's picker chooses the visual theme. The picker lists every registered theme,
grouped by dark and light: the built-in **Midnight** (dark) and **Daylight** (light), plus any theme
you drop in with `--themes` (see [the CLI reference](cli.md)). The picker follows your operating
system's preference (or the configured `ui.default_theme`) until you choose a theme. The browser's
local storage remembers an explicit choice until the system preference next changes. That change
drops the override and re-adopts the system mode.

A theme also defines the UI's **motion**: how views, modals, and panels animate as they switch.
Screen transitions are part of the look, not hard-coded. Through its `--motion-*` tokens (documented
alongside the color tokens in `serve.themes.css`), a theme decides the duration, easing, and
enter/leave animation of a view switch, a modal open/close, and a tiler pane reconstruction. A theme
that sets no motion tokens keeps the built-in animation. A theme that sets an enter/leave token to
`none` renders that transition instantly.

All motion collapses to instant under the operating system's **reduce motion** accessibility
preference, so the UI never animates against a user's wishes. A run also drives the browser with that
preference forced on, so animation never affects a deterministic `run`.

**Theme editor.** Click the **Edit** button next to the picker to open the in-UI theme editor. The
editor generates a form from the live token contract: a **name** and **kind** (dark/light) at the
top, then a native color swatch per color token and a text field per motion token. Edits apply
instantly as a live preview, so the running UI reflects each change with no page reload. From the
editor, you can:

- **Save to Local Draft**: persist the edited theme in the browser's local storage and immediately
  surface it as a **custom** entry in the header picker, selecting it. The draft survives page
  reloads but is browser-local (not shared across serve sessions). Reopening the editor resumes the
  draft.
- **Upload to Server**: write the theme into the `--themes` directory as a discoverable drop-in,
  shared across sessions on that instance. The server derives the theme's id from the name, so an
  uploaded theme lists in the picker on the next load. This button appears **only when the instance
  was started with `--themes`**, because without a themes directory there is nowhere to persist the
  theme.
- **Export**: download a `<name>-theme.css` file in the same drop-in format that `--themes` accepts,
  so you can commit a locally authored theme to a repository and share it.
- **Import**: load a previously exported (or hand-authored) `.css` file and populate the form. The
  editor reports any token in the file with no matching form field instead of silently dropping it.

**Panel management (desktop).** On a desktop-width window, the Record, Replay, and Crawl tabs tile
their panels:

- Drag the divider between two panels to redistribute that pair's widths. The other panels keep
  their share.
- Drag a panel's **⠿** grip onto another panel to swap the two (drop on the center) or to split
  beside the target (drop on an edge).

Each view's layout persists in the browser's local storage. Record's run-result pane joins this
tiling when a run starts and leaves it when you dismiss the pane.

**Narrow windows.** Below phone width, the tiling gives way to a single-column stack, and each view
gains a small switcher that brings one pane (form, log, report, and so on) to full width at a time.
No feature is lost; only the arrangement changes.

**When Claude is unreachable.** Record and Crawl need an AI provider (see [Settings](#settings)).
While Claude cannot be reached (no key, no signed-in CLI), those two tabs read as disabled, their
start buttons grey out, and an inline banner names what is missing, with an **Open Settings**
shortcut. The state flips live as soon as you configure a provider. Everything else (Replay, Author,
Stats, Flaky, Usage, Coverage) works without any AI setup.

## Choosing the active config

Every tab runs against one **active config**. Click **Open config** to bind it. The dialog offers
three sources:

- **From a Git repository.** Enter a spec in the form `github:owner/repo@ref:path/to/bajutsu.config.yaml`
  and click **Load**. The `ref` is a branch, tag, or commit SHA (default: the repository's default
  branch). The server fetches the whole subtree, so the config's scenarios and built app come with
  the config.
- **Upload a bundle (.zip).** Click **Choose a .zip…** (or drop a file on the box) to bring your own
  suite to a hosted server with no file-system access. The zip is a working local checkout: a
  `bajutsu.config.yaml`, its `paths.scenarios` tree, and the built app binary that the config's
  `subject.path` names. The zip carries no secrets; `${secrets.*}` resolve from the server's
  environment.
- **or browse the server.** A file browser, confined to the server's `--root`, for picking a local
  `.yml`/`.yaml` config. A hosted deployment hides this source, since a remote user has no
  file-system relationship to the host.

The [CLI reference](cli.md#serve) documents the full behavior of each source: the content-addressed
Git cache, the bundle's sandboxing and zip-slip hardening, and the `--root` confinement. Only one
config is bound at a time, and opening another config replaces the bound one.

### Confirming what is bound

Click **View** (beside the config name) to see the exact config the tabs run from. The dialog opens
on a **Structured** view, a collapsible key/value tree where each nested object and list is a toggle
you expand only where you need it. A **Raw** toggle switches to the verbatim YAML (comments and all).

Above both views, the dialog shows the config's path. When the config came from a Git repository, it
also shows the source the config was materialized from: the repository, the `ref` you asked for, and
the resolved commit SHA. This provenance line matters for a Git source, whose active path is an
opaque content-addressed cache location (`…/gitsrc/<host>/<owner>/<repo>/<sha>/…`). The path alone
does not say which commit is bound; the provenance line does.

The dialog shows the config verbatim. `${secrets.*}` placeholders appear as written and are never
resolved (they resolve from the server's environment at run time), so the view adds no disclosure
beyond the file itself. The view is not a redactor, though: a secret written **literally** into a
local or uploaded config is shown as-is. Keeping secrets in `${secrets.*}` refs rather than in the
file is what keeps them out of this view. In a hosted deployment, the endpoint is admin-only.

### Telling two configs' runs apart

A `serve` process binds one config at a time. If you restart the server against a second config, both
configs' runs land in one history, interleaved. The [label](glossary.md#label) keeps the runs apart
([BE-0404](../roadmaps/BE-0404-collapse-project-layer/BE-0404-collapse-project-layer.md)). Every run
records a short string naming the config it ran, taken from the config's own name: its file stem for
a local file, or its repository and in-repo path for a config fetched from Git. The run list,
Replay, and the [Stats](#stats--the-run-history-dashboard) dashboard show that label's runs by
default. Choose **all labels** to see the whole history again. A deployment whose runs predate the
label has no run carrying it, so its views open unfiltered rather than on an empty page.

An operator who wants a different partition sets a label per run, with `bajutsu run --label <value>`
on the command line or a `label` field in the `POST /api/run` body. Bajutsu treats the label as
opaque: it never parses the label, never matches it against config, and never consults it when
deciding what you may run. Bajutsu refuses a value longer than 120 characters rather than silently
shortening it, so you learn about the limit up front instead of finding a truncated label in the
history.

### Managing orgs

An **org** is a tenant of a hosted deployment. Its membership decides which GitHub logins, GitHub
organizations, and GitHub Teams may sign in as it, and which Team among them may write. The Team that
may write is admitted too, so it needs no second entry to be able to sign in. A deployment that runs
against a database keeps that membership in the database rather than in the config file, and the
**Orgs** tab is where an admin edits it (BE-0375). The tab appears for an admin on such a deployment
and nowhere else: on any other deployment the server declines to list orgs, which keeps the tab
hidden.

Each row shows an org's slug; how many members, GitHub organizations, and GitHub Teams it holds; and
its editor Team. The row also shows the org's display name when that differs from the slug the row
already leads with.

- **Create** takes a slug and an optional display name. The new org starts with no members at all, so
  nobody can sign in as it until you fill in its membership. The page says so inline, so that an
  empty tenant does not read as a bug. The server refuses the slug `default`: a sign-in matching no
  org lands there, including every admin admitted by the admin-Team bypass, so a real tenant there
  would take the namespace an admin recovers through. The `default` row still appears in the list,
  marked as the fallback and with both its controls disabled, since an admin who arrived that way is
  sitting in it.
- **Membership** opens a form that replaces all four fields at once, prefilled from what the server
  holds right now. A change takes effect on each member's next sign-in.
- **Delete** retires an org. Retiring signs out everyone holding a session as that org at the same
  moment, so nobody keeps acting as the org on a cookie issued beforehand. Retiring keeps the org's
  runs and audit history, and it keeps the slug reserved. The confirmation states all three
  consequences, since a reader would otherwise discover none of them until afterwards.

The config file's `orgs:` block still decides which targets an org owns, so an org created here owns
none until an entry there names some.

### Comparing targets

Once the bound config declares more than one target, the **Comparison** tab opens a read-only
comparison across all of them at once. The tab answers a question that a single
[Stats](#stats--the-run-history-dashboard) dashboard cannot, such as "the Android target passes while
the iOS target fails". Each target is one row, showing:

- its run count;
- its latest pass-rate;
- its flaky-rate (the share of its scenarios classified flaky over the window);
- the median (p50) and 95th-percentile (p95) per-run duration;
- a pass-rate trend sparkline.

Click a column header to rank by pass-rate, flaky-rate, or duration. The first click surfaces the
worst offender (lowest pass-rate, highest flaky-rate or duration), and a second click flips the
order. A target with no runs yet charts as a blank row rather than a misleading zero.

Clicking a row opens that target's run history, newest first. The history is a read-only drill-down
across every label, since the comparison ranks a config's targets rather than one partition of them.
The history is reachable by keyboard too: each row's target name is a button, so tab to the name and
press Enter or Space. The view writes nothing at all. Like the other dashboards, it re-presents
verdicts that `run` already decided, and it never gates anything.

## Settings

**Settings** opens on three tabs. **AI** and **Secrets** hold the choices the AI paths need, and
**Server** (below) is a read-only view of how the running server is configured. Nothing on the AI or
Secrets tabs is written to disk. Those settings live in the running server's memory for the session
and reset when `serve` restarts. For values that survive a restart, set `ANTHROPIC_API_KEY` /
`AWS_*` in your shell or a `.env` before launching.

**AI provider** picks the backend that authoring (Record and Crawl) uses:

- **Anthropic API**: the pay-per-token API, authenticated by the Claude API key set below.
- **Amazon Bedrock**: Claude through AWS, authenticated by your standard AWS credentials. This
  provider adds **AWS region** and **Bedrock model id** fields. A Bedrock id carries a provider
  prefix (for example `global.anthropic.…`), unlike the bare Anthropic id.
- **Anthropic CLI (ant)**: Claude on your Pro/Max/Console seat through the official Anthropic CLI's
  OAuth/SSO sign-in, so no API key is needed and every path keeps full vision. The CLI must be
  installed in the environment that launched `serve`. A **Sign in with SSO** button then starts the
  browser sign-in for you, which is equivalent to running `ant auth login` in that terminal. The
  button opens the browser on the machine running `serve`, so it works with a local `make serve`. On
  a hosted deployment, the server refuses the request (403) and the operator signs the host in out of
  band.
- **Claude Code CLI (claude)**: Claude on your Claude Code Pro/Max/Console subscription, by shelling
  out to the `claude` CLI. No API key is needed. Sign in once with `claude setup-token` (or an
  interactive `claude` login) on the machine running `serve`. This provider ignores any
  `ANTHROPIC_API_KEY` in the environment, so billing stays on the subscription.

The picker starts on a placeholder. There is **no default provider**, and **Save** refuses until you
pick one explicitly. Each provider's own fields (the API key, the Bedrock region and model id, the
CLI sign-in) appear only once you select that provider.

Three overrides apply across providers:

- **Model** substitutes a specific model id for the default. Bedrock keeps its own prefixed id field.
- **Reasoning effort** trades speed against depth for the authoring agents. Only the Claude Code CLI
  provider honors it; the Anthropic API and `ant` adapters have no such knob and ignore it.
- **Output language**
  ([BE-0188](../roadmaps/BE-0188-configurable-ai-output-language/BE-0188-configurable-ai-output-language.md))
  fixes the language the AI writes its generated prose in: `record`'s `from:` provenance and
  `crawl`'s streamed reasoning. Its default *auto* keeps today's behavior (record follows the goal,
  crawl stays English). It never affects the deterministic `run` verdict, and it is separate from a
  target's device locale (`runsOn.ios.locale`).

**Claude API key** (shown when you select the Anthropic API provider) is **write-once**. Enter a key
and **Save**; the page then shows the key only masked and never displays it again. To change the key,
set a new one; **Clear** removes it. The key powers the Anthropic API provider (Bedrock uses AWS
credentials instead). Only the AI paths need the key: **Record** and **Crawl**. Replay does not need
it. Since
[BE-0402](../roadmaps/BE-0402-run-alert-guard-drop-vision-fallback/BE-0402-run-alert-guard-drop-vision-fallback.md),
the Replay alert guard is the native path alone (BE-0315), which clears the common prompts with no
key and consults none.

**Scenario secrets** ([BE-0274](../roadmaps/BE-0274-serve-scenario-secrets/BE-0274-serve-scenario-secrets.md))
lists the secrets that the *bound config* declares. These are the environment-variable names under
the config's `evidence.secrets` list that a scenario references as `${secrets.X}`
([Secrets](configuration.md#secrets-evidencesecrets), [BE-0032](../roadmaps/BE-0032-secret-variables/BE-0032-secret-variables.md)).
The panel shows one **write-once** field per declared name (masked and never displayed again, just
like the API key). You can therefore provision a scenario's credentials from the Web UI instead of
`export`ing them or hand-editing a `.env` before launching `serve`.

A value set here materializes under its own declared name, and a spawned **Replay** / **Record** /
**Crawl** inherits it, so `${secrets.X}` resolves in the run. The panel always shows. When the bound
config declares no secrets, the panel carries an empty-state message that explains the feature. The
panel refreshes when you switch to a config with a different `evidence.secrets` list. Only names that
the config itself declares are settable, never an arbitrary environment variable. Setting a secret is
an **admin** action on a role-gated deployment.

**Server** is a read-only tab reporting how the running server is configured
([BE-0318](../roadmaps/BE-0318-serve-server-settings-tab/BE-0318-serve-server-settings-tab.md)). It
shows:

- the deployment mode (local or hosted);
- the bound config and its source;
- the backends this build can run;
- the runs and baselines directories;
- the trash retention window;
- the concurrency caps;
- the running version.

The most telling row is the **iOS test runner**. It states whether this build ships the bundled
XCUITest Simulator runner
([BE-0292](../roadmaps/BE-0292-xcuitest-bundled-runner/BE-0292-xcuitest-bundled-runner.md)), the Xcode
and Simulator SDK versions the runner was built against when it does, and whether the bound config
overrides the runner with an explicit `driver.runner.testRunner`. That is the one fact that decides
whether an iOS Simulator run can start at all.

A hosted deployment withholds the host paths (the config path, the runs and baselines directories);
every other row shows either way. The tab takes no input. It reports what the launch command and
environment already resolved, and it never writes anything back.

## Record — author a scenario from a goal

**What it does.** Explores toward a natural-language goal with AI and writes the resulting scenario,
turn by turn. The tab is the `record` command in the browser.

**How to use it.**

1. Type what you want in **Goal (natural language)** (for example, "increment the counter twice and
   check it reads 2").
2. Pick a **Target**. On iOS, also pick a **Device** (use the refresh button beside it to re-list
   simulators). On web, tick **show browser (headed)** to watch a visible Chromium window author in
   slow motion instead of the default headless browser.
3. Optionally set **Save as**, the file name for the scenario. A blank name defaults to
   `generated.yaml`. If the name already exists, the run's date-time is appended so nothing is
   overwritten.
4. Choose the iOS options. **erase device first** wipes the simulator so the app starts from
   onboarding, and **disable alert-dismiss** turns off the vision alert guard for this authoring run.
5. Click **Generate scenario** (**Stop** aborts). The agent's turn-by-turn progress streams into the
   log.

**What happens.** When authoring finishes, the YAML appears in the **Generated scenario** panel. Edit
it there if you like, then click **Save** (disabled until there is something to save). The tab writes
the scenario under the target's scenarios directory, so the scenario appears in the **Replay** tab's
**Scenario** picker, ready to run. [Recording](recording.md) explains how the authoring loop and the
alert guard work.

**Run it in place.** The **▶ Run** button beside Save (also disabled until there is YAML) runs the
panel's current content without switching tabs. That content is either the just-authored scenario or
YAML you pasted or edited by hand. A run-result pane opens with the live log and the finished report.
**Stop** cancels the run, and a close button dismisses the pane. The verdict is the deterministic
runner's, exactly as on the Replay tab.

**Readiness (doctor).** A **Readiness** panel with a **Check** button sits in the form. The panel is
the `doctor` command in the browser, shared by the Record and Replay tabs. It reports two halves:

- the runnability checks: is each required tool present, is a Simulator booted, is the web target's
  page reachable;
- once the environment is runnable, the current screen's convention score: a **Ready** / **Partial**
  / **Blocked** grade with the stable-id coverage and a what-to-fix list (unnamed controls,
  off-namespace ids, duplicates).

The panel probes the host environment, is read-only and AI-free, and never gates a run.

## Replay — run a scenario and read its report

**What it does.** Runs a scenario deterministically and embeds its report. The tab is the `run`
command in the browser, with two sub-tabs: **Run** and **History**.

**Run.**

1. Pick a **Scenario** and a **Target**.
2. On iOS, pick the **Simulators** to run on. The tab boots shut-down simulators first. Pick two or
   more to run scenarios in parallel across them, and **Workers** tracks the count. **erase device
   first** and **disable alert-dismiss** apply as in Record. On web, tick **show browser (headed)** to
   watch the run in a visible Chromium window.
3. Click **Run** (**Stop** cancels). The output streams live in the log, and the run's `report.html`
   embeds beside the log on completion. Before a run, the same **Readiness** panel as on Record (see
   above) answers "is my environment ready, and is my app addressable?" on a click. The answer is
   advisory only.

**Cancelling a run does not discard it.** **Stop** asks the runner to finish on its own terms. The
runner fails every scenario it did not complete, with the reason `cancelled`, then writes the report
that an ordinary failing run writes. The cancelled attempt then appears under **History** like any
other failure. There, "this scenario was never run" and "this scenario was started, then cancelled"
stay distinguishable.

A run cancelled before its first scenario started (while a Simulator boots or the app builds) still
stops outright and leaves no report, since the run has reached no verdict yet to preserve. **Stop**
on the Record and Crawl tabs is unchanged for the same reason: neither tab reaches a verdict of its
own.

**Fan out to workers.** The checkbox turns the Run form into a fan-out
([BE-0465](../roadmaps/BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out.md)).
A fan-out queues one job per chosen scenario. The server routes each job by its target's platform
(BE-0484), and any worker serving that platform can take the job, whether local or in a device cloud.
The form names no placement. The checkbox works for every target, a web target included. **Apply &
re-run** from triage unchecks the checkbox, so a re-run goes back to one run.

With **Fan out to workers** checked, the form dispatches a fan-out instead of one run:

1. Pick scenarios in the multi-select, or check **All scenarios**. Either way, the form sends every
   chosen name explicitly, so a file saved between the listing and the dispatch cannot move a row
   onto the wrong scenario.
2. Note which fields disappear, because a fan-out ignores them: the device picker, **Workers**,
   **erase device first**, **show browser (headed)**, **disable alert-dismiss**, **Readiness**, and
   the single scenario's select, View, and grade.
3. Click **Run**, which posts to `POST /api/run-set`. The route needs the editor role, like a single
   **Run**. A refusal shows in the status line: 400 for a bad request and 403 for a missing role. A
   full queue never refuses a fan-out, since its jobs wait for a free slot at lease time.

A **run-set panel** then lists one row per dispatched job, in request order. Each row polls its job
about every two seconds until the job finishes, fails, or errors. A row shows its state and a
**report** link once the run exists. A row also shows the notice when no worker can take the job. A
row ends as unknown when the server no longer tracks the job (after a restart), and **Stop all** still
sends the row's cancel.

Expanding a row streams that job's log. One log streams at a time, so a large fan-out never holds a
stream per row. **Stop all** cancels every unfinished row.

Under the log panel, a **Generate code** bar exports the selected scenario as a native test. The bar
uses the same codegen as the [Author](#author--capture-edit-and-enrich-one-scenario) tab, placed where
a scenario has just run.

Inside the embedded report, a visual check's **Approve** button promotes that run's captured
screenshot into the visual baseline (the same promotion as the `approve` CLI command), so the next
run compares against the new baseline. This is the review step of the run → review → approve →
re-run loop. [Reporting](reporting.md) shows what each report section contains.

**History.** The sub-tab lists past runs, newest first, each with a pass/fail dot and a scenario
summary. The summary counts passed runs against the total. A device condition can leave a run with no
matching device ([configuration](configuration.md#device-conditions-runson)); the summary then adds
the count of not-applicable records as `N n/a`. Click a row to reopen its report. Use the refresh
button to re-list.

**Delete a run** ([BE-0239](../roadmaps/BE-0239-deletable-runs-serve/BE-0239-deletable-runs-serve.md)).
Each row carries a trash button that moves that run (its report, screenshots, video, and network
capture) to the [Trash](#trash--restore-or-permanently-delete-a-deleted-run). The run stays
restorable there for the retention window rather than being erased at once. To clear several runs at
once, tick the per-row checkboxes (or **select all**) and use **Delete selected** in the bar above the
list. Both are editor-level actions. A soft-deleted run leaves the history immediately and reappears
if you restore it.

**Triage a failed run.** When a run fails, a **Triage** button appears in the report bar, both on a
just-run report and when you reopen a failed run from History. The button is the `triage` command in
the browser, so you can ask "why did this fail?" without dropping to a terminal. Click it, then
**Diagnose**. Triage reads that run's stored failure context and reports a root-cause summary, the
implicated step, and suggestions. The default is the deterministic rule-based agent. Tick **Claude**
to diagnose with the Claude investigator instead; the checkbox works only once an AI provider is
configured (see [Settings](#settings)).

When triage can propose a mechanical fix (rename a selector id, disambiguate a match, or raise a
timeout), it shows the fix as a **diff** against the selected scenario source:

- **Apply fix** writes the patch back through the same validated save path that the Author tab uses.
- **Apply & re-run** writes the patch and immediately re-runs the scenario to confirm the fix.

The fix is written only on that explicit click. Nothing is edited automatically, and the run's
pass/fail verdict is read from the deterministic run, never recomputed by AI. Triage previews and
applies against the scenario currently selected in the **Run** sub-tab, so pick the matching scenario
and target before triaging a run from History.

## Crawl — explore the app and map its screens live

**What it does.** Explores the app breadth-first and draws the reachable screens and the transitions
between them as the crawl discovers them. The tab is the `crawl` command in the browser. Crawl is a
**discovery tool, never a pass/fail gate**. The exploration engine (screen identity, transitions,
crashes) is deterministic, while the AI provider chosen in **Settings** proposes *what to try*.

**How to use it.**

1. Pick a **Target** and set **Workers** (parallel browser processes or, on iOS, simulators sharing
   one screen map). On iOS, also pick the **Simulators** to crawl on.
2. Set the budget: **Max screens** (default 50) and **Max steps** (default 200). The crawl stops at
   the first limit reached.
3. Choose the platform options. iOS has **erase device first** and **disable alert-dismiss**; web has
   **show browser (headed)**.
4. Click **Start crawl** (**Stop** aborts). Progress reports into the status line and the **Console**.

**What you get** (three views beside the form):

- **Screen map**: the graph of discovered screens and transitions, drawn live as the crawl advances.
  Zoom with the **−** / reset / **+** controls. Screens that are the same UI in a different transient
  state (an empty versus a filled form) collapse into one node that you can expand in place.
- **Exploration plan**: the plan tree of still-untried operations per screen, with a progress bar.
  **show pruned** toggles the operations that the crawl pruned as duplicate global controls (a tab or
  nav bar explored once). Pruned operations appear struck through, and clicking one resumes it.
- **Console**: the crawl's progress log.

Clicking a screen in the map opens a **screenshot lightbox**: the enlarged screen, with **‹** / **›**
to step to a transition into or out of that screen, and hotspots marking where each transition was
taken.

**History.** A crawl writes no `manifest.json`, so a crawl never appears in the Replay tab's History.
The Crawl tab's own **History** sub-tab lists crawl runs instead, keyed on the `screenmap.json` that
every crawl streams. Each entry shows the run id and its screen / transition / crash counts, newest
first.

Selecting an entry reopens that crawl's screen map **read-only**. The map is the same interactive
graph, drawn from the stored map with no re-crawl. A **past crawl** badge keeps the map from being
mistaken for a live run, and the live form is disabled while the map is shown. Beside the map, the
run's `crashes/*.yaml` (a replayable crash) and `flows/*.yaml` (a reachable screen) scenario files are
linked directly. Each link opens the raw YAML, ready to run with `bajutsu run`. Switch back to the
**Form** sub-tab to return to the live crawl form.

**Delete a crawl** ([BE-0239](../roadmaps/BE-0239-deletable-runs-serve/BE-0239-deletable-runs-serve.md)).
Like the Replay History, each crawl row carries a trash button, and a **select all** / **Delete
selected** bar clears several crawls at once. A deleted crawl moves to the shared
[Trash](#trash--restore-or-permanently-delete-a-deleted-run), restorable within the retention window.

**Continue a past crawl** ([BE-0181](../roadmaps/BE-0181-crawl-continuation/BE-0181-crawl-continuation.md)).
When an open past run stopped on a budget with screens still left to explore, a **▸ continue exploring**
button appears on the map header. Clicking the button leaves the read-only history view and continues
that run live. The continuation re-explores the run's whole remaining frontier (every screen with
untried operations) and appends to the same map. It uses the current **Max screens** / **Max steps**,
so you can raise the budget in the same step, and the **Workers** / picked simulators to run in
parallel. Tapping a struck-through **pruned** operation while a past run is open likewise resumes
exploring just that one branch. Both are deliberate actions: the history view stays read-only until
you ask to explore, so a past map is never driven by accident.

The [CLI reference](cli.md#crawl) covers the screen map, the fingerprint that identifies a screen, and
the web-versus-iOS differences.

## Trash — restore or permanently delete a deleted run

**What it does** ([BE-0239](../roadmaps/BE-0239-deletable-runs-serve/BE-0239-deletable-runs-serve.md)).
Lists the runs that a delete from Replay or Crawl History moved to the trash, each with its deletion
time. Regular runs and crawl runs share one trash. A soft delete is not destructive: the run leaves
the history lists but its bytes stay, so **Restore** returns the run, intact, to the history it came
from.

**The retention window.** The trash permanently removes a run once the run has sat there past the
retention window (`BAJUTSU_RUN_RETENTION_DAYS`, 30 days by default; set it to `0` to keep trash until
someone deletes it by hand). The purge is lazy: it happens on the next history or trash read, not on a
background timer, so nothing runs when the server is idle. The header states the window in effect.

**Delete forever.** Each trashed run also has a **Delete forever** action that skips the window and
erases the run's bytes at once. Because the action is irreversible, it is **admin**-only on a hosted
deployment (a local, single-user `serve` has no roles, so the action is always available there), and
it asks for a distinct, emphatic confirmation. The server writes every delete, restore, and permanent
purge to the audit log and emits an `oplog` event (`run.soft_deleted` / `run.restored` /
`run.purged`), so an operator can trace who removed which run and when.

## Author — capture, edit, and enrich one scenario

**What it does.** Works on one open scenario in three modes, chosen with the **Capture** / **Edit** /
**Enrich** buttons. Switching mode never reloads the scenario or drops unsaved YAML edits.

**Capture**: build a scenario by clicking a live screenshot.

1. Pick a **Target** and click **Start capture** to begin a live session.
2. Set **Action mode** to **tap** or **type** (for **type**, fill in **Text to type**), then click on
   the screenshot to record each action.
3. Click **Finish & save** to write the captured flow.

**Edit**: fix a step against a screenshot, from a past run or a live session.

1. Choose a **Scenario** and click **Load**. The **Run** picker lists only that scenario's own past
   runs, so a chosen run's steps line up with the scenario instead of silently mismatching. Pick a
   **Run** to step through its captured screenshots.
2. If no prior run exists, click **Start live session** to boot the target and pick against its
   current screen. Edit needs no run. **Stop session** ends the session without saving.
3. Step through with **← Prev** / **Next →**. The label shows which step is loaded.
4. Click on the screenshot to re-target the loaded step, then **Apply** the change to the YAML.

A run-backed screenshot shows the moment after the step acted, and a click resolves against the tree
of that same moment ([reporting](reporting.md#reporthtml)). A step that navigates therefore shows the
screen it reached, not the screen it targets. Re-target such a step against a live session (step 2)
instead.

**Enrich**: add suggested assertions.

1. Load a scenario, then click **Enrich**.
2. The **Proposed assertions** panel lists suggestions. **Accept** folds them into the YAML, and
   **Dismiss** discards them.

**The YAML editor** (shared by all three modes) validates as you type. A debounced check runs the same
`bajutsu lint` rules and shows line-anchored diagnostics as a gutter marker on each offending line and
a clickable problems list. Click a finding to jump to its line. The scenario JSON Schema also drives
lightweight key completion (Ctrl/⌘+Space) and hover descriptions. This validation is deterministic and
AI-free. Click **Save** to write the scenario. [Scenarios](scenarios.md) describes the grammar these
checks enforce, and [selectors](selectors.md) describes how selectors are graded.

**The determinism audit badge** grades the same live YAML for stability, alongside the lint check. A
grade badge (**Stable** / **Moderate** / **Fragile**, with the ratio of `id`-based selectors) sits in
the editor header. A findings list below the editor names each determinism risk: a selector below the
stability ladder, a `wait` with no concrete condition, or a raw-coordinate gesture. The badge is the
[`audit`](cli.md) command's static score in the browser: read-only, device-free, AI-free, purely
informational, and never a gate. The **Replay** tab shows the same badge for the scenario you have
selected, so the stability signal is visible both where you write a scenario and where you run it.

**Generate code**: export the loaded scenario as a native test, without leaving the browser. Load a
scenario, then click **Generate code**. The result opens in a read-only viewer where you can **Copy**
or **Download** it (the filename is derived from the scenario and destination, for example
`LoginUITests.swift` or `login.spec.ts`). The destination follows the target's backend, **XCUITest**
for iOS and **Playwright** for web, so the UI offers only the format that the target supports. This is
the [`codegen`](codegen.md) command surfaced in the UI: a structural mapping from the scenario to the
target framework's idiom. It runs no device, no AI, and computes no verdict. Its known limits are
`codegen`'s own.

## Stats — the run-history dashboard

**What it does.** Renders the aggregate run-stats dashboard across the server's run history. The tab
is the `stats` command in the browser. It is **read-only and advisory, not a verdict**. The dashboard
shows pass-rate over time, the slowest and flakiest scenarios, failure hotspots, and run volume,
aggregated from each run's stored `manifest.json`.

Every scenario row names the content fingerprint behind its series. Editing a scenario between runs
starts a second series, which reads as its own row, not a repeat.

The dashboard aggregates a window of the newest runs, and the Replay tab's history list always covers
that window. With a database, the dashboard and the history list read the same window. Without one,
the history list carries no cap, so the dashboard's window sits inside the list. Either way, a
drilldown opens a history that agrees with the row behind it.

**How to use it.** Open the tab to load the dashboard, and use the refresh button to recompute the
dashboard over the current run history. No device, AI, or run is involved.

## Flaky — the ranked flaky-scenario surface

**What it does.** Ranks the server's run history by how much each scenario's verdict flips at a
constant content fingerprint. The tab is the `flakiness` command in the browser. It is **read-only and
advisory, not a verdict**. The tab sorts scenarios flaky-first by verdict flip rate. Each row shows
the pass/fail counts and the scenario's class (`flaky` / `deterministic` / `unproven`, reused from the
determinism audit), and links to the evidence of the representative passing and failing runs.

When a database is wired, the tab groups straight from the provenance stamp on each run row
(org-scoped). Otherwise, the tab builds the same records from each run's stored `manifest.json`.

**How to use it.** Open the tab to load the ranking, and use the refresh button to recompute the
ranking over the current run history. A run with no scenario fingerprint or no recorded verdict cannot
be grouped, so the tab reports the run as skipped. No device, AI, or run is involved.

**Diagnosing a flaky scenario.** Once this panel names a flaky scenario, the AI cross-run *fix
proposal* reads the scenario's passing and failing runs together and explains the delta. The proposal
is a CLI feature, `bajutsu triage --flaky` (see the
[CLI reference](cli.md#cross-run-flaky-triage---flaky)). It is CLI-only by design: this panel is the
deterministic ranking (no AI), while the cross-run proposal needs an AI provider and stays a
reviewable diff that a human applies.

## Usage — the AI token-usage and cost dashboard

**What it does.** Renders the AI usage and cost dashboard over the attributed usage ledger that the AI
paths record (record, crawl, triage, the alert locator). Like Stats, the dashboard is **read-only and
advisory, never a verdict or a gate**. It shows:

- headline totals (tokens and dollars for the period);
- breakdowns by provider, model, command, and scenario;
- a provider/model comparison (cost per call and per scenario);
- a daily cost trend.

Every figure is a deterministic sum over the ledger, and no model is consulted. A subscription
provider or an unknown model records tokens without a per-token price. The dashboard shows those
calls as unpriced (a "—") rather than a fabricated `$0.00`.

**How to use it.** Open the tab to load the dashboard, and use the refresh button to recompute the
dashboard over the current ledger. The dashboard resolves the ledger from the target-merged `ai`
block, as the AI paths do. A `targets.<name>.ai.usageLedger` overrides the team-wide
`defaults.ai.usageLedger`. The dashboard covers the serve process as a whole rather than a single
target, so it reads and sums every ledger that the config's targets name.

When no usage has been recorded yet (the AI paths never ran, or persistence is disabled), the tab
shows an empty state that explains how to enable recording. The ledger defaults to
`runs/usage.jsonl`; `ai.usageLedger` moves it, and an empty string disables it. No device, AI, or run
is involved.

## Coverage — the E2E coverage map

**What it does.** Renders the E2E coverage map for a target. The tab is the `coverage` command in the
browser. It measures the scenario suite's stable-id references against the app's declared
`subject.idNamespaces`, and shows per-namespace coverage, the gap list (declared namespaces that no
scenario touches), and off-namespace ids. A selector that the run binds later, such as a `${vars.*}`
placeholder carrying a `totp` or `email` step's output, names no element. The map therefore leaves
the placeholder out instead of counting its text as an id.

Selecting one or more past runs folds in the run-evidence dimensions:

- endpoints observed versus asserted: the union of those runs' `network.json` against the suite's
  network assertions;
- observed ids versus declared namespaces: from each run's `elements.json`.

Selecting a crawl alongside those runs adds the screens-visited dimension: how many of the screens
that the crawl discovered (in its `screenmap.json`) the selected runs actually reached.

A dimension with nothing to measure says so rather than drawing a full bar. A target declaring no
namespaces has an empty denominator, and drawing that as complete coverage would tell the reader the
opposite of the truth. Like Stats, the whole view is **read-only and advisory, never a verdict or a
gate**. Every figure is a deterministic count, and no model is consulted.

**How to use it.** Pick a target and press **Compute** to render the map. The run picker offers only
runs of that target's own scenarios, since a run of another target's scenarios carries evidence that
the map cannot place. Selecting runs adds the run-evidence dimensions, and selecting a crawl
alongside them adds the screens-visited dimension. The crawl list is **not** scoped that way, because
a crawl records no target to scope on. Pick one of the same app's crawls; a crawl of another app
measures as every screen unvisited. No device, AI, or run is involved.

**Opening it directly.** `GET /coverage?target=<name>` renders the same map as a standalone page, so
you can link or bookmark a coverage map the way `/stats`, `/flakiness`, and `/usage` already allow.
Add `&runs=<id>,<id>` for the run-evidence dimensions and `&crawl=<id>` for the screens-visited
dimension.

## Security and hosting

By default, `make serve` binds `127.0.0.1` with no authentication, which is safe only on the loopback
interface. To expose the server beyond your own machine, you need a token (`--token`), which turns on
authentication and CSRF (cross-site request forgery) protection. Binding a non-loopback `--host`
requires a token. The [CLI reference](cli.md#serve) and [self-hosting](self-hosting.md) cover team
hosting on a single Mac (`--emit-launchagent`), the full hosted control plane (a bucket and a
database), `--max-concurrent-runs`, and `--evidence-store`.

**Signing in.** On an authenticated server, the first request that comes back unauthorized starts the
sign-in. When GitHub OAuth is configured, the browser is sent through it. Otherwise, a token prompt
appears: enter the shared token, and a session cookie keeps you signed in from then on. On an open
(local) server, neither ever appears.

---

Screenshots of each tab are a planned follow-up; this guide is text-only for now.

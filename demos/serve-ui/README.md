# Serve Web UI dogfood (Playwright backend)

[日本語](README.ja.md)

Bajutsu testing **its own** `serve` Web UI. The app under test is the serve single-page app, driven
by the **Playwright** backend ([BE-0041](../../roadmaps/BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)).
Like [demos/web](../web) this needs **no Mac and no Simulator** — it runs on Linux inside the same
toolchain as `make check`. It is a deterministic regression net for the Web UI: pass/fail comes only
from machine assertions, never an LLM.

## What's here

| Path | Purpose |
|---|---|
| `dogfood.config.yaml` | `targets.webui` with `baseUrl` (the running serve) + `backend: [web]` (no `bundleId`) |
| `scenarios/shell-navigation.yaml` | the top tabs (Record / Replay / Crawl / Author / Stats / Coverage) swap the visible view |
| `scenarios/theme.yaml` | the theme toggle follows the OS preference and flips on click |
| `scenarios/crawl-form.yaml` | the Crawl form holds the bound target and the default exploration budget |
| `scenarios/crawl-history.yaml` | a past crawl run reopens read-only (past-crawl badge), and one with a remaining frontier offers "continue exploring" — driven off the committed `fixtures/crawl-runs` run |
| `scenarios/modals.yaml` | the config browser and Settings panel open/close; the AI provider needs an explicit choice (no default — Save is rejected until one is picked) |
| `scenarios/config-sources.yaml` | the config modal offers all three binding sources — the file browser (listing `--root`), a Git spec, and a bundle upload |
| `scenarios/replay-contract.yaml` | a bound config reaches the Replay pickers (config → `/api/targets` → `/api/scenarios`) |
| `scenarios/replay-tabs.yaml` | the Replay Run / History tabs swap the left panel |
| `scenarios/replay-tools.yaml` | the selected scenario gets its determinism grade (BE-0145), codegen exports it as a Playwright test (BE-0137), and the readiness panel (BE-0148) is offered |
| `scenarios/record-form.yaml` | Record's Save stays disabled until a scenario exists; the goal field takes input |
| `scenarios/author-modes.yaml` | the Author Capture / Edit / Enrich mode switcher shows each mode's controls |
| `scenarios/author-editor.yaml` | Edit-mode Load fills the YAML editor and grades it; invalid YAML surfaces the inline problems panel (BE-0138) |
| `scenarios/stats.yaml` | the Stats view loads the run dashboard (BE-0102) |
| `scenarios/coverage.yaml` | Compute renders the target's coverage map (BE-0146) |
| `scenarios/platform-ui.yaml` | each panel shows only the selected target's platform controls — a web target shows the headed toggle instead of the iOS device UI (simulators, workers, erase) |
| `scenarios/panel-resize.yaml` | dragging one tiling divider redistributes only its pair, never a third panel |
| `Makefile` | `web-deps` / `serve-ui` / `e2e` |

## Run it

```bash
make -C demos/serve-ui e2e
```

This installs the web backend (`uv sync --extra web` + `playwright install chromium`), launches
`bajutsu serve` (bound to the [web demo](../web)'s config so its dropdowns have a real app + scenario
to show), drives the Web UI through the Playwright backend with the Tier-A scenarios, and tears serve
down. We start serve directly rather than via `make serve` because the dogfood is web-only — no XCUITest runner
companion or iOS actuator is involved.

To poke the Web UI by hand, `make -C demos/serve-ui serve-ui` and open <http://127.0.0.1:8799/>.

CI gates this on every PR that touches the serve UI — the `dogfood (serve UI)` job in
[`.github/workflows/web-e2e.yml`](../../.github/workflows/web-e2e.yml) runs `make -C demos/serve-ui e2e`
on Linux (BE-0189).

## How it maps to the core

The Web UI's controls carry `data-testid` attributes — the web equivalent of an iOS
accessibilityIdentifier — namespaced by view (`nav.*`, `view.*`, `record.*`, `replay.*`, `crawl.*`,
`author.*`, `stats.*`, `coverage.*`, `settings.*`, `config.*`, `upload.*`). A scenario's
`{ id: nav.replay }` selector resolves through the **same**
`resolve_unique` / `find_all` determinism core as every other backend.

The scenarios assert only what the deterministic core can check without an LLM or a device: which
`<main>` view and which modal are present (the SPA toggles them via the `hidden` attribute, so the
Playwright backend sees the active one and not the others), whether a button is `enabled` / `disabled`,
and the `value` a field or picker holds. That keeps this dogfood firmly in **Tier 2** — the
AI-driven Record and Crawl *runs* (which need a model and a device) are out of scope here, by the
same rule that keeps the LLM out of the run/CI gate.

Two states are asserted indirectly, by design. Report-like panes (Stats, Coverage, run reports)
render into a **shadow root**, which the element query does not pierce — so their scenarios assert
the light-DOM placeholder (`stats.empty` / `coverage.empty`) *leaving* the tree, which is exactly
"the render path ran". And Record's Generate button is gated on Claude reachability (BE-0101) —
host state — so only its presence is asserted, never its enabled state.

## Feature coverage map

The feature list is [docs/web-ui.md](../../docs/web-ui.md) — every user-facing behavior of the
serve Web UI is documented there, and this table maps each one to its dogfood scenario. Features
the dogfood cannot exercise are listed with the reason; none of them is silently uncovered — the
server side of every one has deterministic pytest coverage in `tests/` (the `make check` gate),
and the AI-driven flows are excluded from any deterministic gate **by design** (prime directive 1).

| Web-UI feature (docs/web-ui.md) | Scenario |
|---|---|
| Top tabs switch the six views | `shell-navigation.yaml` |
| Theme toggle (OS-following default, flip and return) | `theme.yaml` |
| Panel resize stays local to the dragged pair | `panel-resize.yaml` |
| Config modal open/close | `modals.yaml` |
| Config sources offered: file browser (lists `--root`), Git spec, upload zone | `config-sources.yaml` |
| Settings: no default provider, Save refused, provider blocks hidden until a choice, model/effort overrides offered | `modals.yaml` |
| Record form: Save and ▶ Run disabled until YAML exists, goal takes input | `record-form.yaml` |
| Readiness (doctor) panel offered on Record and Replay | `replay-tools.yaml` |
| Replay pickers filled from the bound config | `replay-contract.yaml` |
| Replay Run / History tabs; report pane's empty state | `replay-tabs.yaml` |
| Determinism-audit badge on the selected scenario | `replay-tools.yaml` |
| Codegen export (Playwright emit offered, spec + filename, close) | `replay-tools.yaml` |
| Platform-aware controls (web hides iOS device UI, shows headed) | `platform-ui.yaml` |
| Crawl form: bound target, default budget (1 / 50 / 200), Start offered | `crawl-form.yaml` |
| Crawl History: reopen a past run read-only (past-crawl badge), plan tree renders the stored map | `crawl-history.yaml` |
| Crawl continue exploring: a past run with a remaining frontier offers the continue control | `crawl-history.yaml` |
| Author modes show each mode's controls | `author-modes.yaml` |
| Author Edit: Load fills + grades the YAML; inline lint flags invalid YAML | `author-editor.yaml` |
| Stats dashboard renders | `stats.yaml` |
| Coverage map computes and renders | `coverage.yaml` |

**Not exercised by the dogfood, and why:**

- **AI-driven flows** — Record's Generate, Crawl's Start, and its **continue exploring** / pruned
  **resume** (both launch a crawl), Enrich's proposals, Triage with Claude. Excluded from every
  deterministic net by design: an LLM must never sit on a gate's verdict path. `crawl-history.yaml`
  asserts the continue control is *offered*, never clicks it.
- **Flows that need a prior run** — the embedded report's content, visual Approve, Triage (even
  rule-based), Replay History entries, Coverage's run fold-in. The Replay run history is host state,
  so asserting its content would be machine-dependent; the operations behind them are pytest-covered.
  The one exception is the **Crawl** History, now driven off a committed `fixtures/crawl-runs` screen
  map (BE-0181) so its read-only reopen and the continue control are asserted deterministically.
- **Run execution round-trips** — Replay's Run and Record's in-place ▶ Run start a nested
  `bajutsu run` (a browser inside the browser test); deferred in BE-0058.
- **Native `<select>` option switching** — the coordinate-tapping web backend cannot open a native
  dropdown, so anything behind "pick a provider / change the target" (API-key save, Bedrock
  fields, provider-specific flows) asserts only the pre-choice state.
- **Native browser chrome** — the upload file chooser, codegen's clipboard Copy and Download, and
  the token-login `prompt()` dialog are not in the page's DOM.
- **Host-dependent state** — Generate/Start enabled state and the gate banner (Claude
  reachability), doctor's check *results* (they probe the host's tools). Presence is asserted;
  outcomes are not.
- **Environment-shaped layout** — the narrow-tier stacked layout (the driver has no viewport
  control) and the drag-to-move panel grips (multi-stage pointer choreography; the tiling *math*
  is guarded by `panel-resize.yaml`).
- **Binding a different config, saving keys, signing in** — these mutate the shared server state
  under every other scenario, so the dogfood only opens and closes those dialogs; the mutations
  are pytest-covered.

## Scope (current)

The web backend taps by coordinate, so it cannot operate a native `<select>` dropdown; these
scenarios assert the **state the page loads** (which needs no dropdown interaction) rather
than switching options. Driving `<select>` and the AI round-trips are tracked as future work in the
dogfood roadmap item.

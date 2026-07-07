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
| `scenarios/platform-ui.yaml` | the Replay panel hides its iOS device UI (simulators, workers, erase) for a non-iOS backend |
| `scenarios/panel-resize.yaml` | dragging one tiling divider redistributes only its pair, never a third panel |
| `package.json` / `playwright.config.ts` | harness for the **generated** native Playwright specs (see below) |
| `Makefile` | `web-deps` / `serve-ui` / `e2e` / `codegen` / `e2e-playwright` |

## Run it

```bash
make -C demos/serve-ui e2e
```

This installs the web backend (`uv sync --extra web` + `playwright install chromium`), launches
`bajutsu serve` (bound to the [web demo](../web)'s config so its dropdowns have a real app + scenario
to show), drives the Web UI through the Playwright backend with the Tier-A scenarios, and tears serve
down. We start serve directly rather than via `make serve` because the dogfood is web-only — no idb
companion or iOS actuator is involved.

To poke the Web UI by hand, `make -C demos/serve-ui serve-ui` and open <http://127.0.0.1:8799/>.

## The same net as native Playwright tests (CI)

```bash
make -C demos/serve-ui e2e-playwright   # needs Node
```

`bajutsu codegen --emit playwright` (BE-0137) exports every scenario above as a native
`@playwright/test` spec into `playwright-tests/` (gitignored — regenerated from the YAML on every
run, so the scenarios stay the single source of truth). The harness (`playwright.config.ts`) brings
the inner serve up itself, so the target is the same self-contained one `e2e` drives. CI runs this
on every PR that touches the serve UI
([`.github/workflows/serve-ui-e2e.yml`](../../.github/workflows/serve-ui-e2e.yml)); the bajutsu-run
`e2e` stays the local dogfood — the same flows, recorded and replayed by bajutsu itself.

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

## Scope (current)

The web backend taps by coordinate, so it cannot operate a native `<select>` dropdown; these
scenarios assert the **state the page loads** (which needs no dropdown interaction) rather
than switching options. Driving `<select>` and the AI round-trips are tracked as future work in the
dogfood roadmap item.

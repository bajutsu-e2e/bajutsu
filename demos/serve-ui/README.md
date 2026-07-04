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
| `scenarios/shell-navigation.yaml` | the Record / Replay / Crawl tabs swap the visible view |
| `scenarios/modals.yaml` | the config browser and Settings panel open/close; the AI provider needs an explicit choice (no default — Save is rejected until one is picked) |
| `scenarios/replay-contract.yaml` | a bound config reaches the Replay pickers (config → `/api/targets` → `/api/scenarios`) |
| `scenarios/record-form.yaml` | Record's Save stays disabled until a scenario exists; the goal field takes input |
| `scenarios/platform-ui.yaml` | the Replay panel hides its iOS device UI (simulators, workers, erase) for a non-iOS backend |
| `Makefile` | `web-deps` / `serve-ui` / `e2e` |

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

## How it maps to the core

The Web UI's controls carry `data-testid` attributes — the web equivalent of an iOS
accessibilityIdentifier — namespaced by view (`nav.*`, `view.*`, `record.*`, `replay.*`,
`settings.*`, `config.*`). A scenario's `{ id: nav.replay }` selector resolves through the **same**
`resolve_unique` / `find_all` determinism core as every other backend.

The scenarios assert only what the deterministic core can check without an LLM or a device: which
`<main>` view and which modal are present (the SPA toggles them via the `hidden` attribute, so the
Playwright backend sees the active one and not the others), whether a button is `enabled` / `disabled`,
and the `value` a field or picker holds. That keeps this dogfood firmly in **Tier 2** — the
AI-driven Record and Crawl *runs* (which need a model and a device) are out of scope here, by the
same rule that keeps the LLM out of the run/CI gate.

## Scope (current)

The web backend taps by coordinate, so it cannot operate a native `<select>` dropdown; these
scenarios assert the **state the page loads** (which needs no dropdown interaction) rather
than switching options. Driving `<select>` and the AI round-trips are tracked as future work in the
dogfood roadmap item.

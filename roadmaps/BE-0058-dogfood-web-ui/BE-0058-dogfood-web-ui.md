**English** · [日本語](BE-0058-dogfood-web-ui-ja.md)

# BE-0058 — Dogfood the serve Web UI (web-backend regression net)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0058](BE-0058-dogfood-web-ui.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0058") |
| Implementing PR | [#169](https://github.com/bajutsu-e2e/bajutsu/pull/169), [#742](https://github.com/bajutsu-e2e/bajutsu/pull/742) |
| Topic | Dogfood fixtures (web UI) |
| Related | [BE-0189](../BE-0189-serve-ui-dogfood-ci-gate/BE-0189-serve-ui-dogfood-ci-gate.md) |
| Origin | Dogfooding |
<!-- /BE-METADATA -->

## Introduction

The local `serve` Web UI ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md))
is itself a web app, so the Web (Playwright) backend
([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) can drive it. This
item makes Bajutsu test its **own** Web UI: a deterministic, Tier-2 regression net that drives the
served single-page app through the same `run` path and the same determinism core as every other
scenario. It is the web-side counterpart to the iOS showcase fixtures
([BE-0045](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md)): a
purpose-shaped *test subject* (the Web UI, plus the `data-testid` ids that make it drivable) and a
scenario set that exercises it, adding **no** LLM call to any gate.

The first slice ships three things: `data-testid` attributes on the Web UI's controls, a
`demos/serve-ui/` harness that launches `serve` and points a web-backend run at it, and a set of
Tier-A deterministic scenarios (navigation, modals, the config→pickers contract, form state, and
platform-aware controls).

## Motivation

**1. The Web UI has real, untested surface that regresses silently.** `serve` grew three views
(Record / Replay / Crawl), two modals (config browser, Settings), a provider-dependent settings
panel, and a config→apps→scenarios contract that drives the Replay pickers. The Python tests cover
the HTTP layer (`bajutsu/serve/operations.py`) and that the index inlines its assets, but **nothing
drives the rendered SPA** — a refactor that breaks tab switching, a modal, or the picker wiring
passes the gate today. A deterministic run against the live UI closes that hole.

**2. It is the cheapest, most honest proof of the web backend.** `demos/web` proves the Playwright
backend on a tiny static page written to be easy. The Web UI is a real, evolving app we own — a
far better witness that the web backend drives genuine UI, and it costs nothing extra to provision:
it runs on Linux, headless, inside the same toolchain as `make check`, with no Mac, Simulator, or
model. Dogfooding the backend on our own product is the strongest signal that it holds up.

**3. It models the id discipline we ask of users.** Selector stability is *the* determinism lever
([DESIGN §2/§5](../../DESIGN.md)). Giving the Web UI `data-testid` ids — the web equivalent of an
iOS `accessibilityIdentifier` — makes it a well-behaved subject and demonstrates, on our own code,
the practice the tool exists to reward. The same controlled-experiment spirit as BE-0045, on the
web side.

**4. It respects every prime directive** ([CLAUDE.md](../../CLAUDE.md)). The scenarios are pure
Tier-2: pass/fail comes only from machine assertions (which view/modal is present, a button's
enabled/disabled state, a field's value), never a model. The Web UI is just another app behind a
`baseUrl`, onboarded through one `apps.<name>` entry — app-agnostic, per-app differences in config.

## Detailed design

### Test subject: `data-testid` on the Web UI

The controls in [`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) carry
`data-testid` ids, namespaced by area — `nav.*` (top tabs, Open config, Settings, theme), `view.*`
(the three `<main>` views), `record.*`, `replay.*`, `crawl.*`, `settings.*`, `config.*`. The web
backend resolves a scenario's `{ id: nav.replay }` selector to `data-testid` through the **same**
`resolve_unique` / `find_all` core as iOS resolves `accessibilityIdentifier`; only the attribute the
driver reads differs ([drivers.md](../../docs/drivers.md)).

### Harness: `demos/serve-ui/`

Mirrors `demos/web`. `dogfood.config.yaml` declares `apps.webui` with a `baseUrl` (the running
`serve`) and `backend: [web]`. The `Makefile`'s `e2e` target launches `serve` in the background —
bound to the `demos/web` config so its dropdowns have a real app + scenario to render — drives the
Web UI through the Playwright backend, then tears `serve` down. `serve` is started directly rather
than via `make serve` because the dogfood is web-only: no idb companion or iOS actuator is involved.

### What makes show/hide checkable

The SPA toggles each view and modal via the HTML `hidden` attribute (`display:none`), and the
Playwright backend's DOM walk drops `display:none` / zero-size nodes from the element tree. So an
`exists` / `exists … negate` pair over a `data-testid`'d container expresses exactly which view or
modal is active — no screenshot diffing, no timing guess.

### Tier-A scenario catalog (this item)

| Scenario | Asserts |
|---|---|
| `shell-navigation` | the three top tabs swap the visible `<main>` (active present, others gone) |
| `modals` | the config browser and Settings panel open/close; Settings defaults to the Anthropic section, Bedrock fields hidden |
| `replay-contract` | a bound config reaches the Replay pickers (`/api/apps` → `/api/scenarios`); the default app + scenario values are what the config declares |
| `record-form` | Record's Save stays disabled until a scenario exists; the goal field records typed input |
| `platform-ui` | the Replay panel's iOS device UI (simulators, workers, erase) shows only for an iOS backend; selecting `web` hides it |

### Tiering (what is, and is not, in this net)

- **Tier A — deterministic (this item).** Frontend behavior + the read/contract surface. These can
  join a Linux CI job exactly as `demos/web` does (a `make -C demos/serve-ui e2e` target, not the
  core `make check`, which carries no browser).
- **Tier B — out of scope here.** The AI-driven Record and Crawl *runs* (a model + a device) are
  Tier-1 and, by the first prime directive, never gated. A deterministic Replay *run* round-trip
  (driving the UI to run the `demos/web` smoke and asserting the report) is possible but nests a
  second browser inside the first; it is deferred.

### Known limitation: native `<select>`

The web backend taps by coordinate and so cannot operate a native `<select>` dropdown. The Tier-A
scenarios assert the **default** selection the page loads (the config→pickers chain auto-loads the
first app's scenarios, needing no dropdown interaction) rather than switching options. Driving a
`<select>` needs a semantic `selectOption` capability on the web driver, which belongs with
[BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) (web backend
completion); until then, scenarios that depend on switching a dropdown (e.g. provider →
Bedrock) stay out of this net.

### Scope and phasing

- **In scope now:** the `data-testid` ids, the `demos/serve-ui` harness, and the five Tier-A
  scenario files. They run today on Linux through the web backend.
- **Forward-looking:** `<select>` operation (with BE-0054); a populated Replay **History** assertion
  (needs a committed fixture run); the deterministic Replay-run round-trip; and wiring the target
  into a CI job alongside `demos/web`.

## Alternatives considered

- **Test the Web UI with a JavaScript stack (Playwright Test / Jest) instead of Bajutsu.** Rejected:
  the point is dogfooding — Bajutsu testing its own UI through its own determinism core. A separate
  JS test stack would duplicate the selector/assertion model and prove nothing about the web backend
  it is the project's job to harden.
- **Fold this into BE-0041 or BE-0045.** Rejected: BE-0041 is the backend (the enabler) and BE-0045
  is the iOS *subjects*; this is a distinct web *subject* and regression net. It is the sibling of
  BE-0045, not a part of it.
- **Add `selectOption` to the web driver as part of this item.** Rejected for scope: it is a
  Driver-protocol / scenario-schema change that belongs with the web-backend completion work
  (BE-0054). The dogfood is already valuable without it, because the page auto-loads the default
  selection.
- **Skip `data-testid` and select by visible text / role.** Rejected: many controls are icon-only
  (theme, refresh, zoom) or share labels, so text/role selectors would be ambiguous — and ambiguous
  selectors fail by design ([DESIGN §2](../../DESIGN.md)). Stable ids are the honest fix and the
  practice the tool rewards.

## Progress

- [x] Shipped the first slice — navigation, modals, config→pickers contract, form state,
  platform-aware controls. [#169](https://github.com/bajutsu-e2e/bajutsu/pull/169)
- [x] Completed the coverage and added a CI vehicle. [#742](https://github.com/bajutsu-e2e/bajutsu/pull/742)
  Scenarios for every remaining view (Author / Stats / Coverage) and the newer Replay tools (audit
  badge, codegen, doctor); the dogfood README carries a feature-coverage map against a completed
  `docs/web-ui.md`, listing what is deliberately not covered and why. `bajutsu codegen --emit
  playwright` exports the scenarios as native `@playwright/test` specs, run by
  `.github/workflows/serve-ui-e2e.yml` on every serve-UI PR — the YAML stays the single source of
  truth. Side fixes the wider net surfaced: the directional swipe now starts on its target, native
  checkbox `checked` reads as the `selected` trait, and three serve-UI rendering defects (empty
  grade badges, shadow-orphaned placeholders, the clipped Author load row).

## References

- [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) — the enabler
- [BE-0045 — Dogfood showcase apps](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md) — the iOS counterpart this mirrors
- [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) — the subject under test
- [BE-0054 — Web backend completion](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) — where `<select>` operation belongs
- [`demos/serve-ui`](../../demos/serve-ui) — the harness · [`demos/web`](../../demos/web) — the pattern it mirrors
- [DESIGN §2 / §5 / §7.1](../../DESIGN.md) — determinism, stability ladder, per-app onboarding · [drivers.md](../../docs/drivers.md) — the Playwright backend

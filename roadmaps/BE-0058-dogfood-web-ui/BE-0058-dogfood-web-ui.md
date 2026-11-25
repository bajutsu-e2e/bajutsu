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

The local `serve` Web UI ([BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)) is itself a web app, so the Web (Playwright) backend ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) can drive it. This item makes Bajutsu test its **own** Web UI.

The result is a deterministic, Tier-2 regression net. It drives the served single-page app through the same `run` path and the same determinism core as every other scenario. It adds **no** LLM call to any gate.

The net is the web-side counterpart to the iOS showcase fixtures ([BE-0045](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md)). It has two parts: a purpose-shaped *test subject* (the Web UI, plus the `data-testid` ids that make it drivable) and a scenario set that exercises the subject.

The first slice ships three things:

- `data-testid` attributes on the Web UI's controls;
- a `demos/serve-ui/` harness that launches `serve` and points a web-backend run at it;
- a set of Tier-A deterministic scenarios covering navigation, modals, the config→pickers contract, form state, and platform-aware controls.

## Motivation

**1. The Web UI has real, untested surface that regresses silently.** `serve` grew three views (Record, Replay, and Crawl), two modals (the config browser and Settings), a provider-dependent settings panel, and a config→apps→scenarios contract that drives the Replay pickers. The Python tests cover the HTTP layer (`bajutsu/serve/operations.py`) and the fact that the index inlines its assets. **Nothing drives the rendered SPA.** A refactor that breaks tab switching, a modal, or the picker wiring passes the gate today. A deterministic run against the live UI closes that hole.

**2. It is the cheapest, most honest proof of the web backend.** `demos/web` proves the Playwright backend on a tiny static page written to be easy. The Web UI is a real, evolving app that we own, so it witnesses far better that the web backend drives genuine UI. It costs nothing extra to provision. It runs on Linux, headless, inside the same toolchain as `make check`, with no Mac, Simulator, or model. Dogfooding the backend on our own product is the strongest signal that the backend holds up.

**3. It models the id discipline we ask of users.** Selector stability is *the* determinism lever ([DESIGN §2/§5](../../DESIGN.md)). The Web UI gets `data-testid` ids, the web equivalent of an iOS `accessibilityIdentifier`. They make it a well-behaved subject, and they demonstrate on our own code the practice that the tool exists to reward. The spirit is the same controlled experiment as BE-0045, on the web side.

**4. It respects every prime directive** ([CLAUDE.md](../../CLAUDE.md)). The scenarios are pure Tier-2. Pass/fail comes from machine assertions alone: which view or modal is present, a button's enabled or disabled state, and a field's value. No model takes part. The Web UI is just another app behind a `baseUrl`, onboarded through one `apps.<name>` entry. It stays app-agnostic, with per-app differences in config.

## Detailed design

### Test subject: `data-testid` on the Web UI

The controls in [`bajutsu/templates/serve.html.j2`](../../bajutsu/templates/serve.html.j2) carry `data-testid` ids, namespaced by area: `nav.*` (top tabs, Open config, Settings, theme), `view.*` (the three `<main>` views), `record.*`, `replay.*`, `crawl.*`, `settings.*`, and `config.*`.

The web backend resolves a scenario's `{ id: nav.replay }` selector to `data-testid`. It uses the **same** `resolve_unique` and `find_all` core that resolves `accessibilityIdentifier` on iOS. Only the attribute that the driver reads differs ([drivers.md](../../docs/drivers.md)).

### Harness: `demos/serve-ui/`

The harness mirrors `demos/web`. `dogfood.config.yaml` declares `apps.webui` with a `baseUrl` (the running `serve`) and `backend: [web]`.

The `Makefile`'s `e2e` target runs three steps. It launches `serve` in the background, bound to the `demos/web` config so the dropdowns have a real app and scenario to render. It drives the Web UI through the Playwright backend. Then it tears `serve` down. The target starts `serve` directly instead of through `make serve`, because the dogfood is web-only. No idb companion or iOS actuator is involved.

### What makes show/hide checkable

The SPA toggles each view and modal through the HTML `hidden` attribute (`display:none`). The Playwright backend's DOM walk drops `display:none` and zero-size nodes from the element tree. An `exists` / `exists … negate` pair over a `data-testid`'d container therefore expresses exactly which view or modal is active. It needs no screenshot diffing and no timing guess.

### Tier-A scenario catalog (this item)

| Scenario | Asserts |
|---|---|
| `shell-navigation` | the three top tabs swap the visible `<main>` (active present, others gone) |
| `modals` | the config browser and Settings panel open/close; Settings defaults to the Anthropic section, Bedrock fields hidden |
| `replay-contract` | a bound config reaches the Replay pickers (`/api/apps` → `/api/scenarios`); the default app + scenario values are what the config declares |
| `record-form` | Record's Save stays disabled until a scenario exists; the goal field records typed input |
| `platform-ui` | the Replay panel's iOS device UI (simulators, workers, erase) shows only for an iOS backend; selecting `web` hides it |

### Tiering (what is, and is not, in this net)

- **Tier A, deterministic (this item).** These scenarios cover frontend behavior and the read/contract surface. They can join a Linux CI job exactly as `demos/web` does, through a `make -C demos/serve-ui e2e` target. The core `make check` carries no browser, so it does not run them.
- **Tier B, out of scope here.** The AI-driven Record and Crawl *runs* need a model and a device. They are Tier-1, and the first prime directive keeps them out of every gate. A deterministic Replay *run* round-trip is possible: drive the UI to run the `demos/web` smoke, then assert the report. It nests a second browser inside the first, so it is deferred.

### Known limitation: native `<select>`

The web backend taps by coordinate, so it cannot operate a native `<select>` dropdown. The Tier-A scenarios assert the **default** selection that the page loads, and they do not switch options. The config→pickers chain auto-loads the first app's scenarios, which needs no dropdown interaction.

Driving a `<select>` needs a semantic `selectOption` capability on the web driver. That capability belongs with [BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) (web backend completion). Until then, scenarios that depend on switching a dropdown (for example, provider → Bedrock) stay out of this net.

### Scope and phasing

- **In scope now:** the `data-testid` ids, the `demos/serve-ui` harness, and the five Tier-A scenario files. They run today on Linux through the web backend.
- **Forward-looking:** `<select>` operation (with BE-0054), a populated Replay **History** assertion (which needs a committed fixture run), the deterministic Replay-run round-trip, and wiring the target into a CI job alongside `demos/web`.

## Alternatives considered

- **Test the Web UI with a JavaScript stack (Playwright Test or Jest) instead of Bajutsu.** Rejected. The point is dogfooding: Bajutsu tests its own UI through its own determinism core. A separate JS test stack would duplicate the selector and assertion model. It would prove nothing about the web backend, which the project must harden.
- **Fold this into BE-0041 or BE-0045.** Rejected. BE-0041 is the backend, the enabler. BE-0045 covers the iOS *subjects*. This item is a distinct web *subject* and regression net. It is the sibling of BE-0045, not a part of it.
- **Add `selectOption` to the web driver as part of this item.** Rejected for scope. It is a Driver-protocol and scenario-schema change that belongs with the web-backend completion work (BE-0054). The dogfood is already valuable without it, because the page auto-loads the default selection.
- **Skip `data-testid` and select by visible text or role.** Rejected. Many controls are icon-only (theme, refresh, zoom) or share labels, so text and role selectors would be ambiguous. Ambiguous selectors fail by design ([DESIGN §2](../../DESIGN.md)). Stable ids are the honest fix, and they are the practice that the tool rewards.

## Progress

- [x] Shipped the first slice — the `data-testid` ids, the `demos/serve-ui` harness, and the
  Tier-A scenarios. [#169](https://github.com/bajutsu-e2e/bajutsu/pull/169)
- [x] Completed the coverage. [#742](https://github.com/bajutsu-e2e/bajutsu/pull/742) Scenarios for
  every remaining view (Author / Stats / Coverage) and the newer Replay tools (audit badge,
  codegen, doctor), with a feature-coverage map in the dogfood README against a completed
  `docs/web-ui.md` (listing what is deliberately not covered and why). Supporting fixes: native
  checkbox `checked` reads as the `selected` trait (so the theme toggle is state-assertable), and
  three serve-UI rendering defects (empty grade badges, shadow-orphaned placeholders, the clipped
  Author load row). The CI gate for the harness is tracked separately under
  [BE-0189](../BE-0189-serve-ui-dogfood-ci-gate/BE-0189-serve-ui-dogfood-ci-gate.md).

## References

- [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md): the enabler
- [BE-0045 — Dogfood showcase apps](../BE-0045-dogfood-showcase-apps/BE-0045-dogfood-showcase-apps.md): the iOS counterpart that this item mirrors
- [BE-0011 — Local web UI (`bajutsu serve`)](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md): the subject under test
- [BE-0054 — Web backend completion](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md): where `<select>` operation belongs
- [`demos/serve-ui`](../../demos/serve-ui): the harness. [`demos/web`](../../demos/web): the pattern that the harness mirrors
- [DESIGN §2 / §5 / §7.1](../../DESIGN.md): determinism, the stability ladder, and per-app onboarding. [drivers.md](../../docs/drivers.md): the Playwright backend

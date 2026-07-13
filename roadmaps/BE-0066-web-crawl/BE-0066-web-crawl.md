**English** · [日本語](BE-0066-web-crawl-ja.md)

# BE-0066 — Web crawl (Playwright backend)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0066](BE-0066-web-crawl.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0066") |
| Implementing PR | [#185](https://github.com/bajutsu-e2e/bajutsu/pull/185) |
| Topic | Autonomous crawl |
<!-- /BE-METADATA -->

## Introduction

Bring the autonomous crawl ([BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md))
to the Web (Playwright) backend ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)),
so `bajutsu crawl --backend web` explores a web app breadth-first and produces the *same* outputs the iOS crawl
does — a screen map, crash repro scenarios, candidate scenarios, and a whole-app coverage feed — at full parity,
including the optional AI guide (`--guide ai`). The crawl engine is already platform-neutral; what this item adds is
the platform-specific lifecycle wiring and web-appropriate crash / dialog semantics, all while keeping AI out of the
verdict ([prime directive #1](../../CLAUDE.md)).

## Motivation

**The engine is already platform-neutral — only the command is iOS-bound.** The crawl engine
([`crawl.py`](../../bajutsu/crawl.py)) is built entirely on the `Driver` abstraction (`query` / `tap` /
`type_text` / `tap_point` / `screenshot`); its module docstring states outright that it carries "no AI and no
Simulator wiring". So a web crawl is a *wiring and semantics* gap, not an engine rewrite. The one Tier-1 path still
pinned to iOS is the `crawl` **command** ([`cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)): its
`reset` closure does a `simctl` relaunch (`_env.Env.terminate` / `launch` keyed by `bundle_id`), it waits via
`_await_ready`, its crash check is the iOS accessibility-tree collapse (`shows_app_ui`), and its progress text says
"preparing the simulator".

**The web lifecycle seam already exists — `run` uses it.** `run` launches the web driver with `driver.navigate()`
([`runner/launch.py`](../../bajutsu/runner/launch.py)) and relaunches between scenarios with `_web_relauncher`,
which is just another `navigate()` ([`runner/pool.py`](../../bajutsu/runner/pool.py)); a fresh `BrowserContext` is
the `erase` equivalent and is ~free. Crawl should reuse that seam instead of carrying its own iOS-only reset.

**Web is the lowest-friction place to make crawl pay off.** Web needs no Mac and no emulator and runs on the
existing Linux `make check` / CI gate ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)),
so a web crawl can run as a discovery step right inside CI. The three gaps BE-0038 fills apply verbatim to web apps:
*discovery before authoring* (a screen map turns "I don't know this app" into "here are its screens, pick ones to
author"), *whole-app coverage measurement* ([DESIGN §7.2](../../DESIGN.md) — the crawl is the run that produces
the per-screen dumps `doctor --from <runId>` consumes), and *robustness smoke testing* (broad, zero-authoring
exploration that surfaces errors the happy paths never hit).

**The determinism boundary is unchanged.** A crawl is non-deterministic by nature, so it stays Tier-1 discovery and
never becomes a CI gate; its deterministic *byproducts* — a crash's reproducing path, each discovered flow — are
emitted as plain YAML that `run` replays AI-free as a Tier-2 regression. Crashes reach CI as committed repro
scenarios, not as the flaky crawl. This is the same hub model BE-0038 already states; web changes none of it.

## Detailed design

### Command surface

```
bajutsu crawl --app <web-app> --backend web
    [--max-screens N] [--max-steps N] [--prune-global]
    [--seed <url-or-path> ...]       # extra entry points besides the base URL
    [--guide ai|off] [--out runs/<runId>]
```

The web app is identified by `baseUrl` rather than `bundleId` — the per-app config already models this
([`config.py`](../../bajutsu/config.py): an app needs `bundleId` (iOS) *or* `baseUrl` (web)). All other flags carry
over unchanged from the iOS crawl; `--seed` entries are URLs / paths instead of deeplinks. As on iOS, this is an
*AI-live* path, not part of the deterministic gate: `--guide off` runs purely on the identifier-driven heuristics and
needs no model, `--guide ai` layers the optional guide on top, and either way AI never decides pass/fail.

### Lifecycle seam (reuse run's, dispatched by platform)

Factor the crawl command's `reset` out of its iOS branch and dispatch it the same way `run` does:

| Phase | iOS (today) | Web (this item) |
|---|---|---|
| launch | `launch_driver` → simctl boot + app launch | `launch_driver` → `driver.navigate()` (already branches) |
| reset / relaunch | `_env.Env.terminate` + `launch` (simctl) | `driver.navigate()` — fresh `BrowserContext` = erase, ~free (mirrors `_web_relauncher`) |
| readiness / settle | `_await_ready` poll | Playwright native auto-wait (`conditionWait`); the engine's `settle` is simply omitted for the synchronous driver |

`launch_driver` already does the right thing for web, so the only crawl-side change is the `reset` closure. Pulling
the relaunch into a small factory shared by `run` and `crawl` keeps one definition of "return to a clean start" per
platform, so the two paths can't drift.

### Crash detection on web (the one new design surface; stays deterministic)

The iOS crash signal — the app process dying and the accessibility tree collapsing to a bare window (`shows_app_ui`)
— does not exist on the web. The web has its own **deterministic** signals, none of which is an LLM judgment:

- **Uncaught JS exception** — `page.on("pageerror")` fired since the last action (an event fact).
- **Navigation to an error** — the main-frame response returned an HTTP 4xx / 5xx status (a number).
- **Blank / collapsed document** — the DOM went effectively empty (set emptiness, the web analogue of the iOS
  collapsed tree).

This needs a small addition to [`PlaywrightDriver`](../../bajutsu/drivers/playwright.py): register the `pageerror`
(and console-error) handlers and track the last main-frame response status, exposed as a health accessor the crawl
reads — the web counterpart of `is_app_alive`. The engine's single `is_app_alive(landed)` call becomes a
platform-dispatched health check (iOS = `shows_app_ui`; web = the driver's signals). On a detected crash the crawl
captures the full evidence set via the existing `result:error` safety net ([DESIGN §9](../../DESIGN.md)) and emits
the replayed path (the entry URL plus the recorded actions) as a minimal repro scenario, directly runnable by `run`
on web. A pageerror is an event, an HTTP status is a number, a blank DOM is set emptiness — so [prime directive
#1](../../CLAUDE.md) holds: AI stays out of the verdict.

### Blocking-overlay guard → web dialog handler

On iOS the alert guard (`_clear_blocking`, Claude vision) dismisses unexpected OS prompts the crawl would otherwise
read as a crash. The web has no OS alerts; it has JS dialogs (`alert` / `confirm` / `beforeunload`), which Playwright
surfaces deterministically via `page.on("dialog")`, plus a `BrowserContext` permission model for geolocation /
notifications. So on web, `clear_blocking` becomes a **deterministic** dialog auto-handler (accept or dismiss per a
fixed policy) — no model call and no vision round-trip. The AI guide that proposes *what to explore* is unaffected;
only the alert-clearing step changes.

### What carries over unchanged

- **State fingerprint** — primary = the `data-testid` id set (the Playwright driver maps `data-testid` →
  `identifier`), structural fallback for low-id pages. The exact `crawl.py` code path, no web special-casing.
- **AI guide (`--guide ai`)** — the guide reads the element tree and proposes actions / realistic inputs; it is
  already driver-agnostic. The one iOS-specific guide feature, the vision *tab locator* that emits a `tap_point` for
  an un-addressable native tab bar, is rarely needed on web (DOM controls are addressable by role / `data-testid`),
  and `tap_point` exists on the Playwright driver so it degrades gracefully if a guide ever uses it.
- **Outputs** — `screenmap.json` + a rendered graph in `report.html`, crash repro scenarios (YAML, runnable by web
  `run`), candidate scenarios (emitted as *proposals* for human review, never written silently into committed YAML),
  and the per-screen `elements` coverage feed for `doctor --from <runId>` once doctor covers web. Screenshots use
  `driver.screenshot()`, which the Playwright driver provides.

### WebUI (serve)

The Crawl tab gains the web backend as a target alongside the Simulator, reusing the existing live screen-map
streaming (`screenmap.json` is written the same way regardless of backend).

## Alternatives considered

**Use Playwright's native crawling / semantic click instead of the shared engine.** Rejected: routing matching
through Playwright's own engine (`get_by_test_id().click()`) diverges from the determinism core — the very reason
[`drivers/playwright.py`](../../bajutsu/drivers/playwright.py) deliberately resolves every action through the
shared `base.resolve_unique` against a `query()` snapshot. The shared crawl engine keeps web and iOS crawl identical
and their screen maps comparable.

**AI-judged crash detection** ("ask the model whether the page looks broken"). Rejected — it violates [prime
directive #1](../../CLAUDE.md), and the web already exposes cheap deterministic signals (pageerror / HTTP status /
blank DOM) that need no model.

**Fold this into BE-0038.** Rejected: BE-0038 is framed entirely around iOS and is already in progress; the web crash
semantics and the lifecycle seam are a distinct design surface. This mirrors how the web `run` path became its own
item ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) rather than an edit to `run`.
It is also the same "same engine, new axis" relationship [BE-0064 (parallel crawl)](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md)
has with BE-0038 (that one varies concurrency; this one varies platform).

**Wait for [BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) (web backend
completion).** Not required: the crawl needs only `query` / `tap` / `type` / `tap_point` / `screenshot`, the
lifecycle seam, and the health signals — all present or small additions. BE-0054's rich capture (native network,
video, parallel runs) enriches a later web crawl but does not block this one; parallel web crawl in particular is the
intersection of this item with the parallel-crawl axis and can follow once both land.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — the platform-neutral engine this reaches a new backend.
- [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) — the web driver and the deterministic web `run` path.
- [BE-0054 — Web backend completion](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) — rich web capture (network / video / parallel) that enriches a web crawl later.
- [BE-0064 — Parallel crawl across multiple simulators](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) — the sibling crawl axis (concurrency); parallel web crawl is the intersection of the two.
- [`crawl.py`](../../bajutsu/crawl.py), [`cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py), [`runner/launch.py`](../../bajutsu/runner/launch.py), [`runner/pool.py`](../../bajutsu/runner/pool.py), [`drivers/playwright.py`](../../bajutsu/drivers/playwright.py), [`config.py`](../../bajutsu/config.py).
- [DESIGN §2 / §3.1 / §5 / §7.2 / §9](../../DESIGN.md), [CLAUDE.md](../../CLAUDE.md) prime directives #1 (AI never judges) and #2 (determinism first), [multi-platform.md](../../docs/multi-platform.md).

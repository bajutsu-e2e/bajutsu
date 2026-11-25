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

This item brings the autonomous crawl ([BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)) to the Web (Playwright) backend ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)). `bajutsu crawl --backend web` then explores a web app breadth-first and produces the *same* outputs as the iOS crawl at full parity: a screen map, crash repro scenarios, candidate scenarios, and a whole-app coverage feed. The optional AI guide (`--guide ai`) is included.

The crawl engine is already platform-neutral. This item adds the platform-specific lifecycle wiring and web-appropriate crash and dialog semantics. AI stays out of the verdict throughout ([prime directive #1](../../CLAUDE.md)).

## Motivation

**The engine is already platform-neutral, and the command alone is iOS-bound.** The crawl engine ([`crawl.py`](../../bajutsu/crawl.py)) is built entirely on the `Driver` abstraction (`query`, `tap`, `type_text`, `tap_point`, and `screenshot`). Its module docstring states that it carries "no AI and no Simulator wiring". A web crawl is thus a gap in *wiring and semantics*, not an engine rewrite.

The one Tier-1 path still pinned to iOS is the `crawl` **command** ([`cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)):

- Its `reset` closure does a `simctl` relaunch (`_env.Env.terminate` and `launch`, keyed by `bundle_id`).
- It waits via `_await_ready`.
- Its crash check is the iOS accessibility-tree collapse (`shows_app_ui`).
- Its progress text says "preparing the simulator".

**The web lifecycle seam already exists, and `run` uses it.** `run` launches the web driver with `driver.navigate()` ([`runner/launch.py`](../../bajutsu/runner/launch.py)). It relaunches between scenarios with `_web_relauncher`, which is another `navigate()` ([`runner/pool.py`](../../bajutsu/runner/pool.py)). A fresh `BrowserContext` is the `erase` equivalent and costs almost nothing. Crawl should reuse that seam instead of carrying its own iOS-only reset.

**Web is the lowest-friction place to make crawl pay off.** Web needs no Mac and no emulator, and it runs on the existing Linux `make check` / CI gate ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)). A web crawl can thus run as a discovery step right inside CI. The three gaps that BE-0038 fills apply verbatim to web apps:

- *Discovery before authoring.* A screen map turns "I don't know this app" into "here are its screens, pick ones to author".
- *Whole-app coverage measurement* ([DESIGN §7.2](../../DESIGN.md)). The crawl is the run that produces the per-screen dumps that `doctor --from <runId>` consumes.
- *Robustness smoke testing.* Broad exploration with zero authoring surfaces errors that the happy paths never hit.

**The determinism boundary is unchanged.** A crawl is non-deterministic by nature, so it stays Tier-1 discovery and never becomes a CI gate. Its deterministic *byproducts* are the reproducing path of a crash and each discovered flow. They are emitted as plain YAML, which `run` replays AI-free as a Tier-2 regression. Crashes reach CI as committed repro scenarios, not as the flaky crawl. BE-0038 already states this hub model, and web changes none of it.

## Detailed design

### Command surface

```
bajutsu crawl --app <web-app> --backend web
    [--max-screens N] [--max-steps N] [--prune-global]
    [--seed <url-or-path> ...]       # extra entry points besides the base URL
    [--guide ai|off] [--out runs/<runId>]
```

The web app is identified by `baseUrl` rather than `bundleId`. The per-app config already models this: an app needs `bundleId` (iOS) *or* `baseUrl` (web) ([`config.py`](../../bajutsu/config.py)). All other flags carry over unchanged from the iOS crawl. `--seed` entries are URLs or paths instead of deeplinks.

As on iOS, this is an *AI-live* path and not part of the deterministic gate. `--guide off` runs on the identifier-driven heuristics alone and needs no model. `--guide ai` layers the optional guide on top. Either way, AI never decides pass/fail.

### Lifecycle seam (reuse run's, dispatched by platform)

Factor the `reset` of the crawl command out of its iOS branch, and dispatch it the same way `run` does:

| Phase | iOS (today) | Web (this item) |
|---|---|---|
| launch | `launch_driver` → simctl boot + app launch | `launch_driver` → `driver.navigate()` (already branches) |
| reset / relaunch | `_env.Env.terminate` + `launch` (simctl) | `driver.navigate()` — fresh `BrowserContext` = erase, ~free (mirrors `_web_relauncher`) |
| readiness / settle | `_await_ready` poll | Playwright native auto-wait (`conditionWait`); the engine's `settle` is simply omitted for the synchronous driver |

`launch_driver` already does the right thing for web, so the sole crawl-side change is the `reset` closure. A small factory that `run` and `crawl` share can hold the relaunch. That keeps one definition of "return to a clean start" per platform, so the two paths cannot drift.

### Crash detection on web (the one new design surface; stays deterministic)

The iOS crash signal is the app process dying while the accessibility tree collapses to a bare window (`shows_app_ui`). That signal does not exist on the web. The web has its own **deterministic** signals, and none of them is an LLM judgment:

- **Uncaught JS exception.** `page.on("pageerror")` fired since the last action (an event fact).
- **Navigation to an error.** The main-frame response returned an HTTP 4xx or 5xx status (a number).
- **Blank or collapsed document.** The DOM went effectively empty (set emptiness, the web analogue of the iOS collapsed tree).

The signals need a small addition to [`PlaywrightDriver`](../../bajutsu/drivers/playwright.py). The driver registers the `pageerror` (and console-error) handlers and tracks the last main-frame response status. It exposes both as a health accessor that the crawl reads, the web counterpart of `is_app_alive`. The single `is_app_alive(landed)` call in the engine becomes a platform-dispatched health check: iOS uses `shows_app_ui`, and web uses the signals of the driver.

On a detected crash, the crawl captures the full evidence set via the existing `result:error` safety net ([DESIGN §9](../../DESIGN.md)). It emits the replayed path (the entry URL plus the recorded actions) as a minimal repro scenario that `run` can execute directly on web. A pageerror is an event, an HTTP status is a number, and a blank DOM is set emptiness. [Prime directive #1](../../CLAUDE.md) therefore holds, and AI stays out of the verdict.

### Blocking-overlay guard → web dialog handler

On iOS, the alert guard (`_clear_blocking`, Claude vision) dismisses unexpected OS prompts that the crawl would otherwise read as a crash. The web has no OS alerts. It has JS dialogs (`alert`, `confirm`, and `beforeunload`), which Playwright surfaces deterministically via `page.on("dialog")`. A `BrowserContext` permission model also covers geolocation and notifications.

On web, `clear_blocking` thus becomes a **deterministic** dialog auto-handler that accepts or dismisses per a fixed policy. It needs no model call and no vision round-trip. The AI guide that proposes *what to explore* is unaffected. Merely the alert-clearing step changes.

### What carries over unchanged

- **State fingerprint.** The primary fingerprint is the `data-testid` id set, because the Playwright driver maps `data-testid` to `identifier`. A structural fallback serves low-id pages. The code path in `crawl.py` is exactly the same, with no web special-casing.
- **AI guide (`--guide ai`).** The guide reads the element tree and proposes actions and realistic inputs. It is already driver-agnostic. One guide feature is iOS-specific: the vision *tab locator*, which emits a `tap_point` for an un-addressable native tab bar. Web rarely needs it, because role or `data-testid` addresses DOM controls. `tap_point` exists on the Playwright driver, so the feature degrades gracefully if a guide ever uses it.
- **Outputs.** The crawl still writes `screenmap.json` and a rendered graph in `report.html`. It still emits crash repro scenarios (YAML, runnable by web `run`) and candidate scenarios. Candidates appear as *proposals* for human review and are never written silently into committed YAML. The per-screen `elements` coverage feed serves `doctor --from <runId>` once doctor covers web. Screenshots use `driver.screenshot()`, which the Playwright driver provides.

### WebUI (serve)

The Crawl tab gains the web backend as a target alongside the Simulator. It reuses the existing live screen-map streaming, because `screenmap.json` is written the same way regardless of backend.

## Alternatives considered

**Use Playwright's native crawling or semantic click instead of the shared engine.** Rejected. Routing matching through the own engine of Playwright (`get_by_test_id().click()`) diverges from the determinism core. That divergence is the reason [`drivers/playwright.py`](../../bajutsu/drivers/playwright.py) deliberately resolves every action through the shared `base.resolve_unique` against a `query()` snapshot. The shared crawl engine keeps the web crawl and the iOS crawl identical and their screen maps comparable.

**AI-judged crash detection** ("ask the model whether the page looks broken"). Rejected. It violates [prime directive #1](../../CLAUDE.md), and the web already exposes cheap deterministic signals (pageerror, HTTP status, and blank DOM) that need no model.

**Fold this into BE-0038.** Rejected. BE-0038 is framed entirely around iOS and is already in progress, and the web crash semantics and the lifecycle seam are a distinct design surface. The web `run` path followed the same course: it became its own item ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) rather than an edit to `run`. [BE-0064 (parallel crawl)](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) has the same "same engine, new axis" relationship with BE-0038. That item varies concurrency, and this one varies platform.

**Wait for [BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) (web backend completion).** Not required. The crawl needs `query`, `tap`, `type`, `tap_point`, and `screenshot`, plus the lifecycle seam and the health signals. All of them are present or small additions. The rich capture of BE-0054 (native network, video, parallel runs) enriches a later web crawl but does not block this one. Parallel web crawl is the intersection of this item with the parallel-crawl axis. It can follow once both land.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — the platform-neutral engine that this item extends to a new backend.
- [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) — the web driver and the deterministic web `run` path.
- [BE-0054 — Web backend completion](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) — rich web capture (network / video / parallel) that enriches a web crawl later.
- [BE-0064 — Parallel crawl across multiple simulators](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) — the sibling crawl axis (concurrency); parallel web crawl is the intersection of the two.
- [`crawl.py`](../../bajutsu/crawl.py), [`cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py), [`runner/launch.py`](../../bajutsu/runner/launch.py), [`runner/pool.py`](../../bajutsu/runner/pool.py), [`drivers/playwright.py`](../../bajutsu/drivers/playwright.py), [`config.py`](../../bajutsu/config.py).
- [DESIGN §2 / §3.1 / §5 / §7.2 / §9](../../DESIGN.md), [CLAUDE.md](../../CLAUDE.md) prime directives #1 (AI never judges) and #2 (determinism first), [multi-platform.md](../../docs/multi-platform.md).

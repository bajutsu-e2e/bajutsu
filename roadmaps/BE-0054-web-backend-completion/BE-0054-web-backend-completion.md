**English** · [日本語](BE-0054-web-backend-completion-ja.md)

# BE-0054 — Web backend completion (rich capabilities & parallel runs)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0054](BE-0054-web-backend-completion.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0054") |
| Implementing PR | [#187](https://github.com/bajutsu-e2e/bajutsu/pull/187) (native network slice), [#297](https://github.com/bajutsu-e2e/bajutsu/pull/297) (parallel lanes), [#298](https://github.com/bajutsu-e2e/bajutsu/pull/298) (console / page-error evidence), [#299](https://github.com/bajutsu-e2e/bajutsu/pull/299) (video evidence), [#300](https://github.com/bajutsu-e2e/bajutsu/pull/300) (emulated multiTouch) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

The first slice of the Web (Playwright) backend ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)) shipped the deterministic `run` path. It had a deliberately lean capability set, `{query, elements, screenshot, semanticTap, conditionWait}`, and drove a tiny demo web app on Linux.

BE-0041 chose Web first because Web reaches the **rich end** of the capability model. This item completes the backend up to that end. It adds native network, video and console evidence, emulated multi-touch, and parallel execution across browser contexts.

## Motivation

BE-0041 argued that Web is the lowest-cost place to prove the abstraction is platform-neutral, because Playwright reaches the rich end of `capabilities()`. That end includes `semanticTap`, native `conditionWait`, native `network`, video, and emulated `multiTouch`. The v1 slice stopped at the lean set on purpose, to land a working, gate-green `run` quickly.

The capabilities that v1 deferred are the ones that make Web the rich-end proof. Closing the gap turns "Web runs" into "Web exercises the whole capability gradient". Four gaps remain:

1. **Native `network`.** Playwright's route interception observes *and* stubs requests in one API. Web would be the **first** backend with native network, because idb mocks at the app layer through BajutsuKit. `request` assertions and HTTP mocking then work without any app-side cooperation.
2. **Video and console evidence.** `BrowserContext` video recording and `console` / `pageerror` capture are the web equivalents of the simctl video and `deviceLog` interval providers. The `capture` policy then carries the same evidence kinds on web.
3. **Emulated `multiTouch`.** Playwright can synthesize pinch and rotate. The gesture steps that `run` rejects on web today (`UnsupportedAction`) become supported.
4. **Parallel runs.** A `BrowserContext` is a near-free "device", so N contexts are N parallel lanes. v1 is single-lane, with one dummy udid and `workers = 1`. This item generalizes the web branch of the device pool to multiple lanes, which matches the iOS parallel-run story.

## Detailed design

### Where v1 left seams

| Capability / feature | v1 (BE-0041) | This item |
|---|---|---|
| `network` (observe + stub) | — | `page.route()` interception → the `request` assertion + HTTP mocks |
| video evidence | — | `BrowserContext` `record_video_dir` → the `video` capture kind — **shipped (#299)** |
| console / page-error log | — | `page.on("console")` / `on("pageerror")` → a `deviceLog`-equivalent kind — **shipped (#298)** |
| `multiTouch` (pinch / rotate) | `UnsupportedAction` | synthesized touch points → advertise `MULTI_TOUCH` — **shipped (#300)** |
| parallel lanes | single-lane (`workers = 1`) | N `BrowserContext` lanes in the pool's web branch — **shipped (#297)**: `--workers N` is N web lanes |

Each row maps onto an existing seam:

- The capture providers extend `FileSink`'s interval handling, which a simctl `udid` gates today.
- The network capability plugs into the same `request` assertion path that BajutsuKit feeds on iOS.
- The parallel lanes generalize the `is_web` branch added to `bajutsu/runner/pool.py`.

The deterministic core (selector resolution, orchestrator, scenario DSL) stays unchanged, exactly as in v1.

### Web record demo

Once the driver advertises the richer capabilities, AI `record` can already author a web scenario, because the driver implements `query`, `tap`, and `type`. A web `record` demo is **not** a new item. The existing record-experience proposals track it: [BE-0012](../BE-0012-action-capture-record/BE-0012-action-capture-record.md) (action-capture record) and [BE-0014](../BE-0014-record-demarcation/BE-0014-record-demarcation.md). Both apply to any backend.

## Alternatives considered

- **Fold these back into BE-0041.** Rejected. BE-0041 has shipped its slice and is now *In progress*. Tracking the remaining rich-end work as its own item keeps the landed scope and the deferred scope legible. It avoids reopening a closed slice.
- **A separate item per capability (network, video, multi-touch, and parallel).** Rejected as over-proliferation. The four capabilities are one coherent push: "raise the web backend to the rich end of the capability model". They share the same seams, the pool's web branch and the `FileSink` intervals.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md), [BE-0009 — Cross-platform abstractions](../BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md), [drivers.md](../../docs/drivers.md), [multi-platform.md](../../docs/multi-platform.md), `bajutsu/drivers/playwright.py`, `bajutsu/runner/pool.py`, and `bajutsu/evidence.py`.

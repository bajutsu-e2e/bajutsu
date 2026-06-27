**English** · [日本語](BE-0054-web-backend-completion-ja.md)

# BE-0054 — Web backend completion (rich capabilities & parallel runs)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0054](BE-0054-web-backend-completion.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Implementing PR | [#187](https://github.com/bajutsu-e2e/bajutsu/pull/187) (native network slice), [#297](https://github.com/bajutsu-e2e/bajutsu/pull/297) (parallel lanes), [#298](https://github.com/bajutsu-e2e/bajutsu/pull/298) (console / page-error evidence), [#299](https://github.com/bajutsu-e2e/bajutsu/pull/299) (video evidence), [#300](https://github.com/bajutsu-e2e/bajutsu/pull/300) (emulated multiTouch) |
| Topic | Platform expansion (landed slices) |
<!-- /BE-METADATA -->

## Introduction

The first slice of the Web (Playwright) backend ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md))
shipped the deterministic `run` path with a deliberately lean capability set —
`{query, elements, screenshot, semanticTap, conditionWait}` — driving a tiny demo web app on
Linux. This item completes the backend up to the **rich end** of the capability model that
BE-0041 set as the reason to choose Web first: native network, video / console evidence,
emulated multi-touch, and parallel execution across browser contexts.

## Motivation

BE-0041 argued that Web is the lowest-cost place to prove the abstraction is platform-neutral
*because* Playwright reaches the rich end of `capabilities()` — `semanticTap`, native
`conditionWait`, native `network`, video, and emulated `multiTouch`. The v1 slice intentionally
stopped at the lean set to land a working, gate-green `run` quickly. The capabilities it deferred
are exactly the ones that make Web the rich-end proof, so closing the gap is what turns "Web runs"
into "Web exercises the whole capability gradient":

1. **Native `network`** — Playwright's route interception observes *and* stubs requests in one
   API. Web would be the **first** backend with native network (idb mocks at the app layer via
   BajutsuKit), so `request` assertions and HTTP mocking work without any app-side cooperation.
2. **Video / console evidence** — `BrowserContext` video recording and `console` / `pageerror`
   capture are the web equivalents of the simctl video / `deviceLog` interval providers, so the
   `capture` policy carries the same evidence kinds on web.
3. **Emulated `multiTouch`** — Playwright can synthesize pinch / rotate, so the gesture steps that
   `run` currently rejects on web (`UnsupportedAction`) become supported.
4. **Parallel runs** — a `BrowserContext` is a near-free "device", so N contexts are N parallel
   lanes. v1 is single-lane (one dummy udid, `workers = 1`); this generalizes the web branch of
   the device pool to multiple lanes, matching the iOS parallel-run story.

## Detailed design

### Where v1 left seams

| Capability / feature | v1 (BE-0041) | This item |
|---|---|---|
| `network` (observe + stub) | — | `page.route()` interception → the `request` assertion + HTTP mocks |
| video evidence | — | `BrowserContext` `record_video_dir` → the `video` capture kind — **shipped (#299)** |
| console / page-error log | — | `page.on("console")` / `on("pageerror")` → a `deviceLog`-equivalent kind — **shipped (#298)** |
| `multiTouch` (pinch / rotate) | `UnsupportedAction` | synthesized touch points → advertise `MULTI_TOUCH` — **shipped (#300)** |
| parallel lanes | single-lane (`workers = 1`) | N `BrowserContext` lanes in the pool's web branch — **shipped (#297)**: `--workers N` is N web lanes |

Each maps onto an existing seam: the capture providers extend `FileSink`'s interval handling
(today gated on a simctl `udid`), the network capability plugs into the same `request` assertion
path BajutsuKit feeds on iOS, and the parallel lanes generalize the `is_web` branch added to
`bajutsu/runner/pool.py`. The deterministic core (selector resolution, orchestrator, scenario DSL)
stays unchanged, exactly as in v1.

### Web record demo

Once the driver advertises the richer capabilities, authoring a web scenario by AI `record` is
already possible (the driver implements `query`/`tap`/`type`). A web `record` demo is **not** a
new item: it is tracked under the existing record-experience proposals
([BE-0012](../../proposals/BE-0012-action-capture-record/BE-0012-action-capture-record.md) action-capture record,
[BE-0014](../../proposals/BE-0014-record-demarcation/BE-0014-record-demarcation.md)),
which apply to any backend.

## Alternatives considered

- **Fold these back into BE-0041.** Rejected: BE-0041 has shipped its slice and is now
  *In progress*; tracking the remaining rich-end work as its own item keeps the landed
  scope and the deferred scope legible instead of reopening a closed slice.
- **A separate item per capability (network / video / multi-touch / parallel).** Rejected as
  over-proliferation: they are one coherent push — "raise the web backend to the rich end of the
  capability model" — and share the same seams (the pool's web branch, `FileSink` intervals).

## References

[BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md),
[BE-0009 — Cross-platform abstractions](../../proposals/BE-0009-cross-platform-abstractions/BE-0009-cross-platform-abstractions.md),
[drivers.md](../../../docs/drivers.md), [multi-platform.md](../../../docs/multi-platform.md),
`bajutsu/drivers/playwright.py`, `bajutsu/runner/pool.py`, `bajutsu/evidence.py`

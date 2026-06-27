**English** · [日本語](BE-XXXX-idb-action-settle-ja.md)

# BE-XXXX — idb action timing robustness (settle before actuation)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-idb-action-settle.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Topic | On-device validation (M1 close-out) |
<!-- /BE-METADATA -->

## Introduction

Reduce flakiness in the idb backend by acting only on a settled screen. Today an actuating step (tap, swipe, type) resolves its target and fires immediately, so a tap launched while the screen is still animating can land on an element that has since moved. This proposal adds a bounded, condition-based settle before each actuation, hardens the transient-empty retry's backoff, and fixes a latent timeout bug in `wait_for` — all inside the idb driver, leaving the deterministic core unchanged.

## Motivation

The idb backend is coordinate-based: it has no semantic tap, so `IdbDriver.tap()` resolves the target's frame center from `query()` and then issues `idb ui tap x y`. That makes the tap only as accurate as the frame at the instant it resolves. During a SwiftUI screen transition the tree is in motion — elements exist but their frames are still animating to their final position. Resolving mid-animation yields an in-flight center, and by the time idb delivers the tap the element may have moved, so the tap misses or hits a neighbour. This is a classic source of non-deterministic failures on slower or busier runners (CI especially).

The driver already defends against one transition symptom — idb intermittently returning a *near-empty* tree mid-transition — via a bounded, `_max_seen`-gated retry in `query()`. But that defence only covers the *empty* case. A screen whose elements are present but still moving is not empty, so the retry passes it straight through and the action fires on a moving target. There is no settle step between an action that triggers a navigation and the next step that acts on the new screen: the orchestrator offers `wait: settled`, but it is opt-in per scenario, so the default path actuates without waiting for stability.

Two smaller issues compound this:

- **`wait_for(sel, timeout)` ignores its timeout.** The idb implementation does a single `query()` and returns whether the selector is present right now, never polling until the deadline. It currently has no caller, so it does not affect runs today, but it is a latent landmine for anyone who wires it up expecting a real wait.
- **The transient-empty retry uses a fixed backoff.** `query()` retries a degenerate tree a fixed five times at a flat 0.2 s. A short exponential backoff reaches the same bound while polling sooner when the screen recovers quickly, and spacing out later when it does not.

The goal is to make the common actuation path ride over transitions deterministically — without a fixed sleep, without relaxing selector strictness, and without an LLM anywhere near the run loop.

## Detailed design

All changes live in `bajutsu/drivers/idb.py` (the idb backend). The web (Playwright) backend has its own auto-wait and is untouched; the `Driver` interface and the deterministic runner are unchanged, so the tool stays app- and backend-agnostic.

- **Settle before actuation.** Add `IdbDriver._settle(timeout, poll)`: poll `query()` until the normalized tree is unchanged across two consecutive reads (reusing the "settled = N consecutive unchanged polls" idea already in the orchestrator's `wait: settled`), or until a bounded timeout elapses. Call it at the start of each actuating method — `tap`, `double_tap`, `long_press`, `swipe`, `type_text` — so the target is resolved against a stable screen. Settle is **best-effort**, exactly like the orchestrator's settle: a screen that never stabilizes (an animation loop, a live spinner) times out and the action proceeds against the current screen rather than failing — a settle is a stabilization hint, not a correctness assertion. Read-only paths (`query()`, assertions, waits) are unchanged, so settle never adds latency to checks, only to actuation.

- **No fixed sleep.** The settle is a condition wait on tree stability, consistent with the prime directive (condition waits only, never a blind `sleep`). The poll interval is the only delay and is bounded; tests inject a zero interval.

- **Exponential backoff for the transient-empty retry.** Replace `query()`'s flat `_EMPTY_BACKOFF_S` loop with a short exponential backoff (e.g. 0.1 → 0.2 → 0.4 s, capped) that reaches no further than today's overall bound. This recovers faster when the empty tree clears quickly and avoids changing the existing "a genuinely sparse first screen is returned as-is" contract (`_max_seen` gating is retained).

- **Make `wait_for` honour its timeout.** Reimplement `IdbDriver.wait_for(sel, timeout)` to poll `query()` until the selector resolves or the timeout elapses, mirroring the orchestrator's wait discipline. This removes the latent bug without affecting current runs (no caller today).

- **Thread the resolve deadline.** `_resolve` keeps its 3.0 s default but accepts the deadline from its callers, so the actuation path's tolerance can be reasoned about in one place rather than hard-coded.

- **Determinism preserved.** Selector strictness is untouched: ambiguous still fails immediately, missing still fails. Settle and backoff only change *when* the screen is read, never *which* element a selector matches. No part of this adds an LLM to the run/CI gate.

This is deliberately **not** tree repair. [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) covers asserting the *steady-state* normalized shape of SwiftUI controls against a golden; this proposal only governs *when* the driver reads a settled tree. The two stay separate: BE-0006's golden is asserted after settle has done its job, and neither masks the other.

### Validation

- **Unit tests (the gate).** Following the existing `IdbDriver` test style — inject a fake `run` returning a scripted sequence of trees, with the poll interval set to zero — assert that settle waits for stability then proceeds, that it gives up at the bound, that an actuation resolves against the settled tree, that the empty-retry backoff series and its bound hold, and that `wait_for` polls until timeout.
- **On-device.** The heavier e2e path (`e2e.yml` smoke / xcuitest) exercises the change against a real Simulator.
- **Before/after flakiness.** Use [BE-0049](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)'s repeat-and-diff audit on a representative scenario to quantify the reduction in non-determinism.

### Delivery

Small, focused PRs in order of value: (1) settle-before-actuation; (2) `wait_for` / `_resolve` hygiene; (3) exponential backoff for the empty-retry.

## Alternatives considered

- **Auto-settle at the runner level instead of the driver.** Settling between every step in the orchestrator would also cover this, but the moving-target problem is an idb-specific timing quirk (the web backend already auto-waits). Putting the settle in the idb driver keeps the fix local to the backend that needs it and avoids slowing backends that don't.
- **A fixed sleep after navigation.** Simple but forbidden by the prime directive, and wrong: it is both too long for fast screens and too short for slow ones. A condition wait on stability is the determinism-preserving equivalent.
- **Rely on the existing `wait: settled` step.** It already exists, but it is opt-in per scenario, so the default actuation path stays exposed and every author must remember to add it. Making actuation settle by default removes a whole class of "forgot to wait" flakes while leaving the explicit step available for finer control.
- **Settle on every `query()`, including assertions.** Maximal stability but it would slow the read/assert path and risks turning a deliberately fast existence check into a multi-poll operation. Limiting settle to actuating methods targets the actual failure mode.

## References

- [DESIGN §11](../../../DESIGN.md) — idb normalization and the transient-empty note
- [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) — steady-state golden normalization (complementary)
- [BE-0049](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — determinism / flakiness audit (before/after measurement)

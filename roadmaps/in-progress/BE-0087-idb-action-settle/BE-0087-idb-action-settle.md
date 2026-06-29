**English** · [日本語](BE-0087-idb-action-settle-ja.md)

# BE-0087 — idb action timing robustness (settle before actuation)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0087](BE-0087-idb-action-settle.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Implementing PR | [#295](https://github.com/bajutsu-e2e/bajutsu/pull/295), [#296](https://github.com/bajutsu-e2e/bajutsu/pull/296) |
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

- **Settle before actuation (deferred — needs a viable on-device design).** The first attempt added `IdbDriver._settle()`: poll the tree until it is unchanged across two consecutive reads, called before each actuating method. A naive full-tree-equality settle proved unworkable on a real device (validated on the smoke E2E): `describe-all` costs ~2.5s on a loaded CI Simulator, and a live screen rarely yields two byte-identical consecutive trees (status bar, volatile values), so settle exhausted its poll budget on *every* action — adding ~50s per action and ballooning the smoke run past its step timeouts. A workable settle needs a **cheap, robust stability signal** — compare a projection that ignores volatile fields (e.g. identifiers + frames only), bound the polls tightly, and reuse the describe-all the subsequent resolve already pays for rather than adding fresh slow calls — and must be validated on-device before landing. This slice is therefore deferred until that design exists; the hygiene and backoff slices below are independent and ship first.

- **No fixed sleep.** The settle is a condition wait on tree stability, consistent with the prime directive (condition waits only, never a blind `sleep`). The poll interval is the only delay and is bounded; tests inject a zero interval.

- **Exponential backoff for the transient-empty retry.** Replace `query()`'s flat `_EMPTY_BACKOFF_S` loop with a short exponential backoff (e.g. 0.1 → 0.2 → 0.4 s, capped) that reaches no further than today's overall bound. This recovers faster when the empty tree clears quickly and avoids changing the existing "a genuinely sparse first screen is returned as-is" contract (`_max_seen` gating is retained).

- **Make `wait_for` honour its timeout.** Reimplement `IdbDriver.wait_for(sel, timeout)` to poll `query()` until the selector resolves or the timeout elapses, mirroring the orchestrator's wait discipline. This removes the latent bug without affecting current runs (no caller today).

- **Thread the resolve deadline.** `_resolve` keeps its 3.0 s default but accepts the deadline from its callers, so the actuation path's tolerance can be reasoned about in one place rather than hard-coded.

- **Determinism preserved.** Selector strictness is untouched: ambiguous still fails immediately, missing still fails. Settle and backoff only change *when* the screen is read, never *which* element a selector matches. No part of this adds an LLM to the run/CI gate.

This is deliberately **not** tree repair. [BE-0006](../../implemented/BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) covers asserting the *steady-state* normalized shape of SwiftUI controls against a golden; this proposal only governs *when* the driver reads a settled tree. The two stay separate: BE-0006's golden is asserted after settle has done its job, and neither masks the other.

### Implementation status

Two of the three slices have shipped (PRs [#295](https://github.com/bajutsu-e2e/bajutsu/pull/295), [#296](https://github.com/bajutsu-e2e/bajutsu/pull/296)), each covered by the fast unit gate with a fake `run` returning scripted trees:

- **`wait_for` / `_resolve` hygiene.** `IdbDriver.wait_for(sel, timeout)` now polls `query()` until the selector resolves or the timeout elapses, instead of checking once and ignoring the deadline; `_resolve` takes a `timeout` so the actuation path's tolerance lives in one place. Covered by `test_wait_for_polls_until_the_element_appears` and `test_wait_for_times_out_when_absent`.
- **Exponential backoff for the transient-empty retry.** `query()`'s flat retry is now `_empty_backoff` — a short exponential backoff (0.05 s doubling to a 0.2 s cap) that reaches no further than the previous fixed bound and keeps the `_max_seen` gate. Covered by `test_empty_backoff_grows_exponentially_then_caps`.

**Settle before actuation remains deferred** (see the first design bullet): the naive full-tree-equality settle proved unworkable on-device, and a cheap, on-device-validated stability signal must exist before it lands. It is the only remaining slice, and it needs the heavier Simulator path rather than the fast gate — so the item stays *In progress*.

### Validation

- **Unit tests (the gate).** Following the existing `IdbDriver` test style — inject a fake `run` returning a scripted sequence of trees, with the poll interval set to zero — assert that settle waits for stability then proceeds, that it gives up at the bound, that an actuation resolves against the settled tree, that the empty-retry backoff series and its bound hold, and that `wait_for` polls until timeout.
- **On-device.** The heavier e2e path (`e2e.yml` smoke / xcuitest) exercises the change against a real Simulator.
- **Before/after flakiness.** Use [BE-0049](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)'s repeat-and-diff audit on a representative scenario to quantify the reduction in non-determinism.

### Delivery

Small, focused PRs. Order revised after on-device validation: (1) `wait_for` / `_resolve` hygiene and (2) exponential backoff for the empty-retry have shipped (PRs #295, #296). Settle-before-actuation is deferred until a cheap, on-device-validated stability signal exists (see above).

## Alternatives considered

- **Auto-settle at the runner level instead of the driver.** Settling between every step in the orchestrator would also cover this, but the moving-target problem is an idb-specific timing quirk (the web backend already auto-waits). Putting the settle in the idb driver keeps the fix local to the backend that needs it and avoids slowing backends that don't.
- **A fixed sleep after navigation.** Simple but forbidden by the prime directive, and wrong: it is both too long for fast screens and too short for slow ones. A condition wait on stability is the determinism-preserving equivalent.
- **Rely on the existing `wait: settled` step.** It already exists, but it is opt-in per scenario, so the default actuation path stays exposed and every author must remember to add it. Making actuation settle by default removes a whole class of "forgot to wait" flakes while leaving the explicit step available for finer control.
- **Settle on every `query()`, including assertions.** Maximal stability but it would slow the read/assert path and risks turning a deliberately fast existence check into a multi-poll operation. Limiting settle to actuating methods targets the actual failure mode.

## References

- [DESIGN §11](../../../DESIGN.md) — idb normalization and the transient-empty note
- [BE-0006](../../implemented/BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) — steady-state golden normalization (complementary)
- [BE-0049](../../implemented/BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md) — determinism / flakiness audit (before/after measurement)

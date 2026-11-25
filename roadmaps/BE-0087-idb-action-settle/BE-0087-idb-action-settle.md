**English** · [日本語](BE-0087-idb-action-settle-ja.md)

# BE-0087 — idb action timing robustness (settle before actuation)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0087](BE-0087-idb-action-settle.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0087") |
| Implementing PR | [#295](https://github.com/bajutsu-e2e/bajutsu/pull/295), [#296](https://github.com/bajutsu-e2e/bajutsu/pull/296), [#416](https://github.com/bajutsu-e2e/bajutsu/pull/416) |
| Topic | Platform support |
<!-- /BE-METADATA -->

## Introduction

This item reduces flakiness in the idb backend by acting only on a settled screen. It adds a bounded, condition-based settle before each actuation, hardens the backoff of the transient-empty retry, and fixes a latent timeout bug in `wait_for`. All three changes live inside the idb driver, and the deterministic core stays unchanged.

Today an actuating step (tap, swipe, type) resolves its target and fires immediately. A tap launched while the screen is still animating can land on an element that has since moved.

## Motivation

The idb backend is coordinate-based and has no semantic tap. `IdbDriver.tap()` resolves the target's frame center from `query()` and then issues `idb ui tap x y`, so the tap is only as accurate as the frame at the instant it resolves.

During a SwiftUI screen transition the tree is in motion. Elements exist, but their frames are still animating to their final position. Resolving mid-animation yields an in-flight center, and by the time idb delivers the tap the element may have moved. The tap then misses or hits a neighbour. This is a classic source of non-deterministic failures on slower or busier runners, CI especially.

The driver already defends against one transition symptom: idb intermittently returns a *near-empty* tree mid-transition. A bounded, `_max_seen`-gated retry in `query()` handles that symptom, but the retry covers only the *empty* case. A screen whose elements are present but still moving is not empty, so the retry passes the tree straight through and the action fires on a moving target.

No settle step sits between an action that triggers a navigation and the next step that acts on the new screen. The orchestrator offers `wait: settled`, but the step is opt-in per scenario, so the default path actuates without waiting for stability.

Two smaller issues compound the problem:

- **`wait_for(sel, timeout)` ignores its timeout.** The idb implementation does a single `query()` and returns whether the selector is present right now, never polling until the deadline. It currently has no caller, so it does not affect runs today. It is still a latent landmine for anyone who wires it up expecting a real wait.
- **The transient-empty retry uses a fixed backoff.** `query()` retries a degenerate tree a fixed five times at a flat 0.2 s. A short exponential backoff reaches the same bound. It polls sooner when the screen recovers quickly and spaces out later when the screen does not.

The goal is to make the common actuation path ride over transitions deterministically. The change uses no fixed sleep, does not relax selector strictness, and puts no LLM near the run loop.

## Detailed design

All changes live in `bajutsu/drivers/idb.py` (the idb backend). The web (Playwright) backend has its own auto-wait and is untouched. The `Driver` interface and the deterministic runner are unchanged, so the tool stays app- and backend-agnostic.

- **Settle before actuation.** `IdbDriver._settle()` polls the tree until its identifier-frame projection is unchanged across two consecutive reads. Each coordinate-based actuating method calls `_settle()` first, via `_center()`.

  A naive full-tree-equality settle proved unworkable on-device. `describe-all` costs about 2.5 s on a loaded CI Simulator, and volatile fields prevent byte-identical consecutive trees. The shipped design therefore uses a **projection** of `(identifier, frame)` only, and ignores `value`, `traits`, and `label`.

  A cached `_last_stable_key`, updated by every `query()`, makes the steady-state cost zero. When the screen has not changed since the last action, `_settle` does one `query()` (the same one `_resolve` would have done anyway) and returns immediately. When the screen *has* changed, `_settle` polls up to `_SETTLE_MAX_POLLS` (3) extra reads at `_SETTLE_POLL_S` (50 ms) intervals and then returns the last tree. The settle is best-effort and never hangs. `_settle` passes the settled tree to `_resolve(initial_tree=...)`, so the subsequent resolution reuses the tree without a redundant `describe-all`.

- **No fixed sleep.** The settle is a condition wait on tree stability, consistent with the prime directive (condition waits only, never a blind `sleep`). The poll interval is the only delay, and the delay is bounded. Tests inject a zero interval.

- **Exponential backoff for the transient-empty retry.** A short exponential backoff (for example 0.1 → 0.2 → 0.4 s, capped) replaces `query()`'s flat `_EMPTY_BACKOFF_S` loop and reaches no further than today's overall bound. The retry recovers faster when the empty tree clears quickly. It keeps the existing contract that "a genuinely sparse first screen is returned as-is", because the `_max_seen` gating is retained.

- **Make `wait_for` honour its timeout.** `IdbDriver.wait_for(sel, timeout)` is reimplemented to poll `query()` until the selector resolves or the timeout elapses, mirroring the orchestrator's wait discipline. The change removes the latent bug without affecting current runs, because no caller exists today.

- **Thread the resolve deadline.** `_resolve` keeps its 3.0 s default but accepts the deadline from its callers. The actuation path's tolerance can then be reasoned about in one place rather than hard-coded.

- **Determinism preserved.** Selector strictness is untouched: an ambiguous selector still fails immediately, and a missing one still fails. Settle and backoff change only *when* the screen is read, never *which* element a selector matches. No part of this change adds an LLM to the run/CI gate.

The change is deliberately **not** tree repair. [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md) covers asserting the *steady-state* normalized shape of SwiftUI controls against a golden. This item governs only *when* the driver reads a settled tree. The two stay separate: BE-0006's golden is asserted after settle has done its job, and neither masks the other.

### Validation

- **Unit tests (the gate).** The tests follow the existing `IdbDriver` test style: inject a fake `run` that returns a scripted sequence of trees, with the poll interval set to zero. They assert five behaviors. Settle waits for stability and then proceeds. Settle gives up at the bound. An actuation resolves against the settled tree. The empty-retry backoff series and its bound hold. `wait_for` polls until timeout.
- **On-device.** The heavier e2e path (`e2e.yml` smoke / xcuitest) exercises the change against a real Simulator.
- **Before/after flakiness.** [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md)'s repeat-and-diff audit, run on a representative scenario, quantifies the reduction in non-determinism.

### Delivery

Delivery uses small, focused PRs:

1. `wait_for` / `_resolve` hygiene (PR #295).
2. Exponential backoff for the empty-retry (PR #296).
3. Settle-before-actuation with projection-based stability detection.

## Alternatives considered

- **Auto-settle at the runner level instead of the driver.** Settling between every step in the orchestrator would also cover the problem. The moving-target problem is an idb-specific timing quirk, though, because the web backend already auto-waits. Putting the settle in the idb driver keeps the fix local to the backend that needs it and avoids slowing backends that don't.
- **A fixed sleep after navigation.** A fixed sleep is simple but forbidden by the prime directive, and it is wrong: the sleep is too long for fast screens and too short for slow ones. A condition wait on stability is the determinism-preserving equivalent.
- **Rely on the existing `wait: settled` step.** The step already exists, but it is opt-in per scenario. The default actuation path stays exposed, and every author must remember to add the step. Making actuation settle by default removes a whole class of "forgot to wait" flakes, and the explicit step stays available for finer control.
- **Settle on every `query()`, including assertions.** This option maximizes stability, but it would slow the read/assert path. It risks turning a deliberately fast existence check into a multi-poll operation. Limiting settle to actuating methods targets the actual failure mode.

## Progress

- [x] `wait_for` / `_resolve` hygiene — `wait_for(sel, timeout)` polls `query()` until the selector resolves or the timeout elapses; `_resolve` takes the deadline.
- [x] Exponential-backoff transient-empty retry — `query()`'s flat retry becomes `_empty_backoff` (0.05 s doubling to a 0.2 s cap), keeping the `_max_seen` gate.
- [x] Settle before actuation — projection-based `_settle()` compares `(identifier, frame)` only, with a cached `_last_stable_key` for zero steady-state overhead. Integrated via `_center()` so all coordinate-based actions (tap, double_tap, long_press) settle automatically.

[#295](https://github.com/bajutsu-e2e/bajutsu/pull/295) and [#296](https://github.com/bajutsu-e2e/bajutsu/pull/296) shipped the hygiene and backoff slices. The settle slice completes the item.

## References

- [DESIGN §11](../../DESIGN.md): idb normalization and the transient-empty note
- [BE-0006](../BE-0006-idb-element-tree-normalization/BE-0006-idb-element-tree-normalization.md): steady-state golden normalization (complementary)
- [BE-0049](../BE-0049-determinism-flakiness-audit/BE-0049-determinism-flakiness-audit.md): determinism / flakiness audit (before/after measurement)

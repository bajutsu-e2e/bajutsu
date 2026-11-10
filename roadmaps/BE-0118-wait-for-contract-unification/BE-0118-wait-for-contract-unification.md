**English** · [日本語](BE-0118-wait-for-contract-unification-ja.md)

# BE-0118 — Unify the wait_for polling contract across drivers

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0118](BE-0118-wait-for-contract-unification.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0118") |
| Implementing PR | [#622](https://github.com/bajutsu-e2e/bajutsu/pull/622) |
| Topic | Driver & backend architecture |
<!-- /BE-METADATA -->

## Introduction

The Playwright backend implements `Driver.wait_for` as one check that returns at once, and it ignores `timeout`. The idb backend polls until `timeout` elapses, which is the intended behavior. The mismatch is a determinism bug. On Web, a caller that asks to wait raises a false `TimeoutError` as soon as the element is absent. This proposal unifies the contract so that every backend behaves the same way.

## Motivation

The shared `Driver` Protocol declares `def wait_for(self, sel: Selector, timeout: float) -> bool: ...` at `bajutsu/drivers/base.py:114`. The Protocol itself carries no docstring, but the idb implementation establishes the intended contract in practice. `bajutsu/drivers/idb.py:306-319` polls in a loop against a monotonic deadline built from `timeout`. It sleeps `poll` seconds between checks. Its docstring is explicit: "Polls until at least one element matches `sel`, or `timeout` elapses". The reason it gives is that "the element may render slightly after the call."

The Playwright backend does not honor that contract. `bajutsu/drivers/playwright.py:546-547` reads:

```python
def wait_for(self, sel: base.Selector, timeout: float) -> bool:
    return len(base.find_all(self.query(), sel)) >= 1
```

This code checks once and returns at once, so `timeout` is an unused parameter. Meanwhile `bajutsu/golden.py:190` calls `driver.wait_for(anchor, timeout)` directly, in `golden_assert`'s "drive-wait-query-compare" flow.

When the call returns `False`, `golden_assert` raises `TimeoutError(f"anchor {anchor!r} did not appear within {timeout}s")`. On idb, the call waits out the full timeout before failing. On Playwright, it fails on the first check. A `golden` assertion that races a page still rendering thus reports a false timeout on Web. The same real-world delay would have passed on iOS.

The severity is High, and this is a genuine bug rather than a design gap. It directly undermines prime directive 2 ("determinism first"). A flaky false failure is the class of problem that determinism-first exists to prevent. The bug is also backend-specific, so it erodes trust in the "platform is a backend" premise. The same scenario behaves differently for reasons unrelated to the app under test.

## Detailed design

No backend should implement its own deadline loop. Instead, the contract will make `wait_for` a single-shot primitive, and one shared helper will do the polling.

1. **Redefine the `Driver.wait_for` contract as single-shot.** Change the docstring and contract at `bajutsu/drivers/base.py:114` to state plainly that `wait_for` checks *once* and returns at once. Remove `timeout` from the signature. If `timeout` stays for interface stability, document it as unused and reserved. Under the new contract, today's Playwright behavior becomes the correct implementation rather than a bug.
2. **Add a shared polling helper.** Introduce a small function, for example `wait_until` in `bajutsu/drivers/base.py` next to `find_all`. The function takes a `Driver`, a `Selector`, a `timeout`, and a `poll` interval. It polls `driver.wait_for(sel, ...)` against a monotonic deadline.
   It may poll `driver.query()` directly instead, mirroring the existing `_wait` pattern in `orchestrator/waits.py`. Either way, the loop is the one that idb's current `wait_for` implements, lifted out of the driver.
3. **Move the idb polling loop into the shared helper.** Simplify `bajutsu/drivers/idb.py:306-319` to a single-shot check that matches the new contract. Delete its bespoke deadline loop. The shared helper will then provide that loop for every backend.
4. **Point every direct caller at the shared helper.** The audit found one direct external caller of `driver.wait_for`: `bajutsu/golden.py:190`. Update it to call the new shared helper. Then `golden_assert` will honor its timeout identically on idb and Playwright.
5. **Add a regression test.** Use a fake or stub driver whose `wait_for` first returns `False` and later returns `True`. That sequence simulates an element that renders late. The test exercises the shared helper and would have caught this bug. Add it alongside the existing driver and golden tests.

This change leaves prime directive 1 alone: `run` and CI stay fully deterministic, and no AI enters this path. The change serves prime directive 2 directly. After the fix, a `timeout` given to a wait means the same number of real seconds on every backend.

## Alternatives considered

- **Make Playwright's `wait_for` poll internally instead of introducing a shared helper.** This fixes the immediate bug. Every future backend (Android, for example) would still have to reimplement the deadline loop that idb has. The same class of bug returns the day someone forgets the loop. A shared helper removes the duplication permanently.
- **Use Playwright's native auto-waiting (`page.wait_for_selector`) instead of polling.** Native waiting is attractive for the Playwright backend alone. But `wait_for` is a cross-backend contract. A uniform polling model is simpler to reason about and to test than special-casing one backend's strategy. Playwright could still use native waiting *underneath* the single-shot check if a future optimization wants it.
- **Leave `timeout` on `wait_for`'s signature but do nothing about the contract mismatch.** Rejected. The current bug has this shape: a single-shot check with a `timeout` parameter that is present and ignored. The signature must either honor `timeout` (poll) or drop it (single-shot, with polling elsewhere). It must not ignore `timeout`.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Redefine `Driver.wait_for` as a documented single-shot contract (`timeout` dropped from the
      signature, not silently ignored)
- [x] Add a shared deadline-polling helper (`wait_until`) in `bajutsu/drivers/base.py`
- [x] Simplify every backend's `wait_for` to single-shot, deleting its bespoke loop — `idb.py`,
      `xcuitest.py`, and `webview.py` each carried their own deadline loop, so unifying all three
      (plus `playwright.py`/`fake.py`, already single-shot) fulfils the item's "no backend
      reimplements the loop" goal
- [x] Point `bajutsu/golden.py:190` at the shared helper instead of calling `driver.wait_for` directly
- [x] Add a regression test with a fake driver that resolves late, covering the shared helper (plus
      per-backend `wait_until` polling tests through idb / xcuitest / webview)

Log:

- **Single slice — contract unification.** Made `Driver.wait_for` single-shot by contract (dropped
  `timeout` from the signature), added the shared `base.wait_until` deadline poll, and simplified
  `idb`, `xcuitest`, `webview`, `playwright`, and `fake` to single-shot checks. Pointed
  `golden_assert` at `wait_until` so its timeout is now honoured identically on every backend
  (Playwright previously ignored it, raising a false `TimeoutError`). The audit found five `wait_for`
  implementations, not the two the proposal named (xcuitest and the WebView driver landed after it
  was written), so the fix unifies all of them. Updated `docs/drivers.md`, `DESIGN.md`, and the
  selector-count docs (both languages) to describe the single-shot contract. No LLM enters this path
  (directive 1 untouched); a `timeout` now means the same real seconds on every backend (directive 2).

## References

- `bajutsu/drivers/base.py:114` — the `Driver.wait_for` Protocol method, currently undocumented at
  the contract level
- `bajutsu/drivers/idb.py:306-319` — idb's polling implementation (the intended contract today)
- `bajutsu/drivers/playwright.py:546-547` — the single-check implementation that ignores `timeout`
- `bajutsu/golden.py:190` — the direct caller that can raise a false `TimeoutError` on Web
- Related: BE-0041 (web Playwright backend), BE-0049 (determinism/flakiness audit)
- Originates from the 2026-07-02 codebase-analysis report (design).

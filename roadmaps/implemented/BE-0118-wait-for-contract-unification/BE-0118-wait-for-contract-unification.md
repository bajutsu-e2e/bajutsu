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
| Topic | Platform expansion (Android / Web / Flutter) |
<!-- /BE-METADATA -->

## Introduction

`Driver.wait_for` is intended to poll until `timeout` elapses (as idb does today), but the
Playwright backend implements it as a single, immediate check that silently ignores `timeout`.
This is a determinism bug — a caller that asks to wait raises a false `TimeoutError` on Web the
instant the element isn't present yet. This proposal unifies the contract so every backend behaves the same way.

## Motivation

`bajutsu/drivers/base.py:114` declares `def wait_for(self, sel: Selector, timeout: float) -> bool: ...`
on the shared `Driver` Protocol, with no docstring on the Protocol itself, but the idb implementation
establishes the intended contract in practice: `bajutsu/drivers/idb.py:306-319` polls in a loop
against a monotonic deadline built from `timeout`, sleeping `poll` seconds between checks, and its
docstring is explicit — "Polls until at least one element matches `sel`, or `timeout` elapses" —
specifically because "the element may render slightly after the call."

The Playwright backend does not honor that contract. `bajutsu/drivers/playwright.py:546-547` is:

```python
def wait_for(self, sel: base.Selector, timeout: float) -> bool:
    return len(base.find_all(self.query(), sel)) >= 1
```

This checks once and returns immediately — `timeout` is an unused parameter. `bajutsu/golden.py:190`
calls `driver.wait_for(anchor, timeout)` directly (`golden_assert`'s "drive-wait-query-compare" flow)
and raises `TimeoutError(f"anchor {anchor!r} did not appear within {timeout}s")` when it returns
`False`. On idb this waits out the full timeout before failing; on Playwright it fails on the very
first check, so a `golden` assertion racing a page that hasn't finished rendering yet reports a false
timeout on Web that would have passed on iOS given the same real-world delay.

Severity: High, and a genuine bug rather than a design gap — it directly undermines prime directive
2 ("determinism first"): a flaky false failure is exactly the class of problem determinism-first is
meant to prevent, and it is backend-specific, so it erodes trust in the "platform is a backend"
premise (the same scenario behaves differently for reasons unrelated to the app under test).

## Detailed design

The fix is to stop asking every backend to implement its own deadline loop and instead make
`wait_for` a single-shot primitive by contract, with one shared helper doing the polling:

1. **Redefine the `Driver.wait_for` contract as single-shot.** Change the docstring/contract at
   `bajutsu/drivers/base.py:114` to state plainly that `wait_for` checks *once* and returns
   immediately; `timeout` is removed from the signature (or, if kept for interface stability, is
   explicitly documented as unused and reserved). This makes today's Playwright behavior the
   correct implementation of the (new) contract instead of a bug.
2. **Add a shared polling helper.** Introduce a small function (e.g. `wait_until` in
   `bajutsu/drivers/base.py`, alongside `find_all`) that takes a `Driver`, a `Selector`, a
   `timeout`, and a `poll` interval, and polls `driver.wait_for(sel, ...)` (or `driver.query()`
   directly, mirroring `orchestrator/waits.py`'s existing `_wait` pattern) against a monotonic
   deadline — the same loop idb's current `wait_for` already implements, lifted out of the driver.
3. **Move the idb polling loop into the shared helper.** Simplify `bajutsu/drivers/idb.py:306-319`
   to a single-shot check (matching the new contract) and delete its bespoke deadline loop, since
   the shared helper now provides it for every backend uniformly.
4. **Point every direct caller at the shared helper.** Update `bajutsu/golden.py:190` (the only
   direct external caller of `driver.wait_for`, per this audit) to call the new shared helper
   instead of `driver.wait_for` directly, so `golden_assert`'s timeout is honored identically on
   idb and Playwright.
5. **Add a regression test.** A fake/stub driver whose `wait_for` starts by returning `False` and
   later `True` (simulating an element that renders late) exercises the shared helper and would have
   caught this bug; add it alongside the existing driver/golden tests.

This keeps `run`/CI fully deterministic (directive 1 is untouched — no AI enters this path) and
directly serves directive 2: after the fix, a `timeout` given to a wait means the same number of
real seconds regardless of which backend is driving.

## Alternatives considered

- **Make Playwright's `wait_for` poll internally instead of introducing a shared helper.** Fixes the
  immediate bug but leaves every future backend (Android) to reimplement the same deadline loop
  idb already has, reintroducing the same class of bug the day someone forgets the loop. A shared
  helper removes the duplication permanently.
- **Use Playwright's native auto-waiting (`page.wait_for_selector`) instead of polling.** Attractive
  for the Playwright backend specifically, but `wait_for` is a cross-backend contract; keeping the
  polling model uniform across backends is simpler to reason about and to test than special-casing
  one backend's implementation strategy, even though Playwright could still use native waiting
  *underneath* the single-shot check if a future optimization wants it.
- **Leave `timeout` on `wait_for`'s signature but do nothing about the contract mismatch.** Rejected
  — the whole point is that "single-shot but a `timeout` parameter is present and ignored" is the
  exact shape of the current bug; the signature must either honor `timeout` (poll) or drop it
  (single-shot with polling elsewhere), not silently ignore it.

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

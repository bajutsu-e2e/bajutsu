**English** · [日本語](BE-XXXX-assert-query-snapshot-reuse-ja.md)

# BE-XXXX — Reuse the settled query snapshot across assert and extract steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-assert-query-snapshot-reuse.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
| Related | [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md), [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) |
<!-- /BE-METADATA -->

## Introduction

The step loop (`bajutsu/orchestrator/loop.py`) can fetch the accessibility tree with
`driver.query()` more times than a single non-mutating step needs. BE-0234 (Implemented, #987)
already closed most of this gap: the loop's post-step read is now lazy — taken only when a
`screenChanged` capture, an attached `extract`, or a wait-timeout diagnostic actually needs the
tree — and it reuses the previous step's `after` as the next step's `before` (the `BE-0234 Unit 2`
comment in `loop.py`). So a plain `assert` with none of those attached no longer double-queries at
all. The residual gap this item targets is narrower: when a non-mutating step (an `assert`, or an
`extract` attached to one) *does* trigger that post-step read, `_run_step_body` has already resolved
and queried the tree to evaluate the step, and nothing mutated the screen in between — yet the
post-step read still fetches it again. This item threads that already-settled snapshot through
instead of re-fetching it, and does the analogous thing one layer down, where a single `tap` stacks
its own redundant fetches underneath the step loop's.

## Motivation

`bajutsu/orchestrator/waits.py:64` documents why this matters on a real backend: idb's
`describe-all` (the query the idb driver's `query()` shells out to) costs roughly 100–300ms per
call, because it is a subprocess round-trip through the Simulator, not an in-process lookup. A
scenario that leans on `assert` steps to check state — the normal shape for a test that reads
before it acts — pays that cost twice for no additional information whenever the post-step read
still fires (a `screenChanged` capture or an `extract` on the assert): `_run_step_body` already
holds the resolved element list at the moment `assertions.evaluate` runs, and nothing mutates the
screen between that call and the loop's own post-step `query()`.

The same shape compounds underneath a single `tap`. On the idb driver
(`bajutsu/drivers/idb.py:317`), `_center` calls `_settle()` (one `query()`, more if the tree's
identifier-frame projection hasn't stabilized) and then `_resolve` (`idb.py:296`), which runs its
own bounded, polling re-query loop up to a 3-second deadline if the selector isn't found on the
first try. `tap` is frequently invoked underneath `base.wait_until` (`base.py:359`), which is
itself a deadline/poll loop — so a single flaky tap can have two independent deadline managers
each re-querying on their own schedule, stacked on top of each other. On the adb driver,
`_scroll_into_view` (`bajutsu/drivers/adb.py:369`) adds one more `_settle()` per scroll attempt
(`adb.py:379`), so a tap that needs to scroll into view can fire on the order of a dozen
full-tree subprocess calls for what is conceptually one action. None of this changes what a
scenario asserts — it is pure, avoidable latency on every `assert`-heavy run and every tap that
isn't already on screen.

## Detailed design

This is a **behavior-preserving** refactor: the elements a step's assertions and extractions see
are the *same* elements, fetched fewer times. No change to selector resolution, assertion
semantics, wait/poll conditions, or driver interfaces is proposed. The work is MECE across three
independent units:

- **Reuse `_run_step_body`'s query result as the loop's `after` snapshot for non-mutating steps.**
  Change `_run_step_body` (`bajutsu/orchestrator/loop.py`, `_run_step_body` at line 141, the
  `assert_` query at 172) to return the element list it queried for `assert_` (and, if it queries
  one, `wait`) alongside its existing `(ok, reason, assertion_results)` tuple. In `exec_steps`, when
  the step kind is one that cannot mutate the screen, use that returned snapshot as `after` instead
  of letting the (post-BE-0234, now-lazy) post-step read call `query()` again. Action steps (`tap`,
  `type`, `swipe`, …) still query fresh
  after they run, because the screen may genuinely have changed — this unit only removes the
  redundant *second* query for steps where "before" and "after" are provably the same query. The
  `extract` modifier (`bajutsu/scenario/models/steps.py:134`) rides along for free: when it
  decorates a non-mutating step it reads the same reused snapshot; when it decorates a mutating
  step it continues to read the fresh post-action query, unchanged from today.

- **Thread one settled snapshot through tap actuation instead of re-querying at each layer.** In
  the idb and adb drivers, `_center` (`idb.py:317`) calls `_settle()` and then `_resolve`
  (`idb.py:296`), and `_resolve` re-queries internally on a cache miss. Change `_resolve` to accept
  and prefer the tree `_settle()` already produced (it already has an `initial_tree` parameter for
  the first attempt — `idb.py:296`) and change `_center` to pass the settled tree through to every
  attempt within its own bounded retry rather than letting `_resolve` call `query()` again on the
  first not-found. The outer `base.wait_until` deadline loop (`base.py:359`) is untouched: this unit
  only removes a query that a lower layer was performing again immediately after a sibling layer
  already had the same data in hand.

- **Add a regression test asserting the query-count reduction.** Using `FakeDriver` (or an
  instrumented driver wrapper that counts `query()` calls), assert that a scenario consisting of a
  single `assert` step calls `query()` exactly once end to end, down from two today. A second test
  covers a `tap` step to confirm action steps are unaffected — their `after` query still fires, since
  the screen may have changed.

## Alternatives considered

- **Cache `query()` results across poll iterations by wall-clock time (e.g. a short TTL).**
  Rejected: a time-based cache can return a stale snapshot after the screen has actually changed,
  which would let a `wait` or `assert` step read data that no longer reflects the device — silently
  weakening the very determinism prime directive 2 protects. This proposal's reuse is narrower and
  safe by construction: it only ever substitutes one query result for the *provably identical*
  query the same step already ran, scoped to a single step's lifetime, never across polls or across
  steps.
- **Leave the loop as-is and rely on `waits.py:64`'s documented cost as an accepted tradeoff.**
  Rejected: the cost is well understood but avoidable without touching semantics, and it compounds
  visibly on `assert`-heavy scenarios and on any tap that scrolls — exactly the CI/local run-time
  cost this repository otherwise works to keep low (`docs/ai-development.md`'s right-sizing
  guidance applies the same principle to compute; this applies it to wall-clock run time).
- **Redesign the driver query interface around a persistent, invalidation-tracked tree cache.**
  Rejected as out of scope: that is a much larger change to the `Driver` interface across every
  backend (idb, adb, Playwright), with its own invalidation-correctness risk, for a win this
  narrower, purely-additive reuse already captures for the common cases. If a future proposal wants
  a general cache, it should build on the query-count regression test this item adds.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Reuse `_run_step_body`'s query result as the loop's `after` snapshot for non-mutating steps
      (`assert_`, and `extract` when attached to one).
- [ ] Thread one settled snapshot through `_center`/`_resolve` in the idb and adb drivers instead of
      re-querying at each layer.
- [ ] Add a regression test asserting the query-count reduction on an `assert` step (and confirming
      action steps are unaffected).

## References

- [BE-0172 — Decompose the run-path step loop and per-scenario runner](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md)
  (the run-path step loop this item optimizes, already decomposed into named helpers)
- `bajutsu/orchestrator/loop.py` — `_run_step_body` (line 141), the `assert_` query (172),
  `exec_steps`, and the lazy post-step `after` read (BE-0234 Unit 2)
- `bajutsu/orchestrator/waits.py:64` (documents idb `describe-all` at ~100–300ms per call)
- `bajutsu/drivers/idb.py:297` (`_settle`), `:376` (`_resolve`), `:397` (`_center`)
- `bajutsu/drivers/adb.py:369` (`_scroll_into_view`)
- `bajutsu/drivers/base.py:359` (`wait_until`)

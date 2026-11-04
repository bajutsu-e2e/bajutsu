**English** · [日本語](BE-0259-assert-query-snapshot-reuse-ja.md)

# BE-0259 — Reuse the settled query snapshot across assert and extract steps

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0259](BE-0259-assert-query-snapshot-reuse.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0259") |
| Implementing PR | [#1083](https://github.com/bajutsu-e2e/bajutsu/pull/1083) |
| Topic | Codebase quality & technical debt |
| Related | [BE-0172](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md), [BE-0234](../BE-0234-adb-run-performance/BE-0234-adb-run-performance.md) |
<!-- /BE-METADATA -->

## Introduction

The step loop (`bajutsu/orchestrator/loop.py`) can fetch the accessibility tree with
`driver.query()` more often than a single non-mutating [step](../../docs/glossary.md#scenario-authoring) needs.

BE-0234 (Implemented, #987) already closed most of that gap. The loop's post-step read is now lazy:
it runs only when a `screenChanged` capture, an attached `extract`, or a wait-timeout diagnostic
needs the tree. The loop also reuses the previous step's `after` as the next step's `before` (the
`BE-0234 Unit 2` comment in `loop.py`). A plain `assert` with none of those attached no longer
queries twice.

The remaining gap is narrower. A non-mutating step (an `assert`, or an `extract` attached to one)
can still trigger the post-step read. By then `_run_step_body` has already resolved and queried the
tree to evaluate the step, and nothing has changed the screen since. The post-step read fetches the
tree a second time anyway.

This item passes the already-settled snapshot along instead of fetching it again. It applies the
same idea one layer down, where a single `tap` stacks redundant fetches beneath the step loop's own.

## Motivation

`bajutsu/orchestrator/waits.py:64` documents the cost on a real backend. The idb `describe-all`
command (the query that the idb driver's `query()` shells out to) takes roughly 100–300ms per call.
Each call is a subprocess round-trip through the Simulator, not an in-process lookup.

A scenario that leans on `assert` steps, the normal shape for a test that reads before it acts,
pays that cost twice for no extra information whenever the post-step read still fires. That happens
when the `assert` carries a `screenChanged` capture or an `extract`. `_run_step_body` already holds
the resolved element list when `assertions.evaluate` runs. Nothing changes the screen between that
call and the loop's own post-step `query()`.

The same pattern compounds beneath a single `tap`:

- On the idb [driver](../../docs/glossary.md#driver-backend-actuator-platform) (`bajutsu/drivers/idb.py:397`), `_center` calls `_settle()`. That costs one
  `query()`, and more if the tree's identifier-frame projection has not stabilized.
- `_center` then calls `_resolve` (`idb.py:376`). If the selector is not found on the first try,
  `_resolve` runs its own bounded, polling re-query loop with a 3-second deadline.
- `tap` often runs beneath `base.wait_until` (`base.py:359`), which is itself a deadline and poll
  loop. A single flaky tap can therefore have two independent deadline managers, each re-querying on
  its own schedule.
- On the adb driver, `_scroll_into_view` (`bajutsu/drivers/adb.py:369`) adds one more `_settle()` per
  scroll attempt (`adb.py:379`). A tap that must scroll into view can fire on the order of a dozen
  full-tree subprocess calls for what is conceptually one action.

None of this changes what a scenario asserts. The cost is avoidable latency on every `assert`-heavy
run and on every tap whose target is not already on screen.

## Detailed design

This is a **behavior-preserving** refactor. The elements that a step's assertions and extractions
see are the same elements, fetched fewer times. The item proposes no change to selector resolution,
assertion semantics, wait and poll conditions, or driver interfaces. The work is MECE across three
independent units.

- **Reuse `_run_step_body`'s query result as the loop's `after` snapshot for non-mutating steps.**
  - Change `_run_step_body` (`bajutsu/orchestrator/loop.py`; the function starts at line 141 and the
    `assert_` query is at line 172). It returns the element list it queried for `assert_`, and for
    `wait` if it queries one, alongside its existing `(ok, reason, assertion_results)` tuple.
  - In `exec_steps`, when the step kind cannot mutate the screen, use the returned snapshot as
    `after`. The post-step read (lazy since BE-0234) then skips a second `query()`.
  - Action steps (`tap`, `type`, `swipe`, …) still query fresh after they run, because the screen
    may genuinely have changed. This unit removes only the redundant second query, for steps whose
    "before" and "after" are provably the same query.
  - The `extract` modifier (`bajutsu/scenario/models/steps.py:134`) benefits without further work.
    On a non-mutating step it reads the reused snapshot. On a mutating step it keeps reading the
    fresh post-action query, as it does today.

- **Thread one settled snapshot through tap actuation instead of re-querying at each layer.**
  - In the idb and adb drivers, `_center` (`idb.py:397`) calls `_settle()` and then `_resolve`
    (`idb.py:376`). On a cache miss, `_resolve` re-queries internally.
  - Change `_resolve` to accept and prefer the tree that `_settle()` already produced. It already
    has an `initial_tree` parameter for the first attempt (`idb.py:376`).
  - Change `_center` to pass the settled tree through to every first not-found.
  - Leave the outer `base.wait_until` deadline loop (`base.py:359`) untouched. This unit removes
    only a query that a lower layer ran again right after a sibling layer already held the same data.

- **Add a regression test that asserts the query-count reduction.**
  - Use `FakeDriver`, or an instrumented driver wrapper that counts `query()` calls.
  - Assert that a scenario of a single `assert` step calls `query()` exactly once end to end, down
    from two today.
  - Add a second test on a `tap` step to confirm that action steps are unaffected. Their `after`
    query still fires, because the screen may have changed.

## Alternatives considered

- **Cache `query()` results across poll iterations by wall-clock time (for example, a short time-to-live, or TTL).**
  Rejected. A time-based cache can return a stale snapshot after the screen has changed. A `wait`
  or `assert` step could then read data that no longer reflects the device, which silently weakens
  the determinism that prime directive 2 protects. The reuse proposed here is narrower and safe by
  construction. It substitutes one query result for the provably identical query that the same step
  already ran. The substitution is scoped to a single step's lifetime and never crosses polls or
  steps.
- **Leave the loop as it is and accept the cost documented at `waits.py:64`.**
  Rejected. The cost is well understood and avoidable without touching semantics. It compounds on
  `assert`-heavy scenarios and on any tap that scrolls. This repository otherwise works to keep that
  CI and local run-time cost low. `docs/ai-development.md`'s right-sizing guidance applies the same
  principle to compute, and this item applies it to wall-clock run time.
- **Redesign the driver query interface around a persistent, invalidation-tracked tree cache.**
  Rejected as out of scope. That change would touch the `Driver` interface across every backend
  (idb, adb, Playwright) and carries its own invalidation-correctness risk. The narrower, purely
  additive reuse already captures the win for the common cases. A future proposal for a general
  cache should build on the query-count regression test that this item adds.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Reuse `_run_step_body`'s query result as the loop's `after` snapshot for non-mutating steps
      (`assert_` and `wait`, and `extract` when attached to one).
- [x] Thread one settled snapshot through `_center`/`_resolve` in the idb and adb drivers instead of
      re-querying at each layer. *(Already satisfied by existing code — see the log below.)*
- [x] Add a regression test asserting the query-count reduction on an `assert` step (and confirming
      action steps are unaffected).

### Log

- **Unit 1 — done.** `_run_step_body` now returns the settled tree it queried for a non-mutating
  step. That covers `assert_`, and `wait` through `_wait` and `_wait_settled`, which return their
  last-polled tree. The step loop seeds `_ScreenRead` with the tree. The `screenChanged`, `extract`,
  wait-diagnostic, and capture consumers reuse that snapshot instead of issuing a second identical
  `query()`. Mutating and tree-less steps (`tap`/`type`/…, `email`, `wait until: request`) return
  `None`, so their post-step read stays a fresh query. Behavior is preserved.
  (`bajutsu/orchestrator/loop.py`, `bajutsu/orchestrator/waits.py`.)
- **Unit 2 — already satisfied, no change needed.** The redundant tap-time query that this unit
  targeted no longer exists. `_center` (idb) and `_resolve_frame_and_screen` (adb) already pass the
  tree that `_settle()` produced into `_resolve(..., initial_tree=tree)`. A tap resolves on the
  settled tree, with no second query on the happy path. idb has done this since BE-0087
  (`settle before actuation`), and adb's equivalent landed with BE-0210. The per-scroll `_settle()`
  that remains in `adb._scroll_into_view` is a genuine re-read after the screen has moved, not a
  redundant one, so it stays.
- **Unit 3 — done.** `tests/orchestrator/test_read_count.py` gains BE-0259 cases:
  - An `assert`+extract step issues exactly one `query()` (down from two).
  - An `assert` under a `screenChanged` policy costs two (down from three).
  - A `wait for`+extract step reuses its settled tree.
  - An action step still reads a fresh `after`, which checks that mutating steps are never seeded.

## References

- [BE-0172 — Decompose the run-path step loop and per-scenario runner](../BE-0172-run-loop-step-decomposition/BE-0172-run-loop-step-decomposition.md)
  (the run-path step loop this item optimizes, already decomposed into named helpers)
- `bajutsu/orchestrator/loop.py` — `_run_step_body` (line 141), the `assert_` query (172),
  `exec_steps`, and the lazy post-step `after` read (BE-0234 Unit 2)
- `bajutsu/orchestrator/waits.py:64` (documents idb `describe-all` at ~100–300ms per call)
- `bajutsu/drivers/idb.py:297` (`_settle`), `:376` (`_resolve`), `:397` (`_center`)
- `bajutsu/drivers/adb.py:369` (`_scroll_into_view`)
- `bajutsu/drivers/base.py:359` (`wait_until`)

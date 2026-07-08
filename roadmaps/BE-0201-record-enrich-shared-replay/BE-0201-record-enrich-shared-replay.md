**English** · [日本語](BE-0201-record-enrich-shared-replay-ja.md)

# BE-0201 — Consolidate the duplicated replay helpers of record and enrich

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0201](BE-0201-record-enrich-shared-replay.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0201") |
| Implementing PR | [#813](https://github.com/bajutsu-e2e/bajutsu/pull/813) |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/enrich.py` re-implements two of `bajutsu/record.py`'s helpers as stripped-down copies:
the step-replay dispatch (`record._execute` vs `enrich._execute_step`) and the pre-observation
alert clearing (`_clear_blocking` in both). This item consolidates each pair onto one
implementation, with the small behavioral differences expressed as parameters rather than as a
second copy.

## Motivation

Both pairs are the same logic maintained twice:

- **Step replay** (`record.py:266-274` vs `enrich.py:26-37`): the same `_action_of` dispatch
  (wait / `assert_` no-op / `_do_action`). They differ only in wait-failure handling — enrich
  checks `_wait`'s `(ok, reason)` and raises `_ReplayFailed`, record ignores the return value.
- **Alert clearing** (`_clear_blocking` in both): the same
  `for _ in range(max_tries)` / `shows_app_ui` / `guard(driver)` / `clock.sleep(0.5)` loop, and
  similar "screen … blocked …" reporting (record: "the app screen looks blocked …"; enrich:
  "screen blocked …"). `record.py`'s version is the richer one (returns the dismissed labels,
  reports each dismissal); `enrich.py`'s is a stripped copy that discards the guard's return value.

Independently maintained copies drift — the screenshot helpers had exactly this history until
BE-0132 consolidated them onto `record._screenshot_bytes`, which `enrich.py` already imports.
This item finishes the same consolidation for the two remaining pairs, in the same file pair.

## Detailed design

1. One shared step-replay executor with a wait-failure hook: `record.py` exports it (or it moves
   to a small shared module beside the two), with an `on_wait_failure` callback (or returned
   status) so enrich can raise `_ReplayFailed` and record can keep ignoring the result.
2. One `_clear_blocking`, the reporting-rich `record.py` variant; `enrich.py` calls it and drops
   the return value it does not need.
3. Unit tests pinning the two behaviors that differ today (enrich fails the replay on a wait
   failure; record proceeds), so the consolidation provably preserves both.

## Alternatives considered

- **Leave the copies.** The known drift pattern (BE-0132's motivation) repeats: a fix to the
  alert-clearing loop or the dispatch lands on one side only.
- **Move the shared helpers into `orchestrator`.** Wrong layer: these helpers exist for the AI
  authoring/enrichment paths (Tier 1), and the deterministic core must not grow
  periphery-serving hooks (BE-0112 keeps the direction core ← periphery).

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Shared step-replay executor with a wait-failure hook; both callers migrated
- [x] Single `_clear_blocking` (record's reporting variant); enrich migrated
- [x] Unit tests pinning both wait-failure behaviors

**Log**

- Added an `on_wait_failure` hook to `record._execute` and migrated `enrich`'s replay onto it
  (dropping the duplicated `_execute_step`); replaced `enrich._clear_blocking` with an import of
  `record`'s reporting variant. Added unit tests pinning that `record` records forward past a
  wait timeout while `enrich`'s hook raises `_ReplayFailed`.

## References

- [`bajutsu/record.py`](../../bajutsu/record.py) · [`bajutsu/enrich.py`](../../bajutsu/enrich.py)
- [BE-0132](../BE-0132-dedupe-crawl-screenshot-helpers/BE-0132-dedupe-crawl-screenshot-helpers.md) — the precedent consolidation this completes for the same file pair

**English** · [日本語](BE-XXXX-run-id-contract-ja.md)

# BE-XXXX — Make the run-id format a single named contract

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-run-id-contract.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

Run ids are minted as `datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")` spelled out independently
at five sites (`cli/commands/run.py`, `cli/commands/crawl.py`, `serve/operations/dispatch.py`,
`serve/helpers.py`, and the `audit-` variant in `cli/commands/audit.py`). The format is not a
local detail — other code parses it back, validates it, and sorts by it — yet nothing in the
codebase names it. This item introduces one `new_run_id()` helper (plus the format constant) and
points the mint, validation, and parse sites at it.

## Motivation

The timestamp format is a cross-surface contract with three independent consumers:

- `serve/jobs.py:47` regex-parses run ids back out of CLI output,
- `serve/helpers.py` (`valid_run_id`) validates ids at the API boundary,
- the Web UI sorts run history lexicographically, which is chronological *only because* ids are
  zero-padded UTC timestamps in this exact shape.

Today the contract holds by convention: five call sites repeat the `strftime` pattern, and the
regex/validator repeat its shape. A well-meaning change at any one site (a timezone tweak, a
precision bump, a separator change) would silently break parsing or history ordering elsewhere.
Naming the contract once makes the dependency visible and the format changeable in one place.

## Detailed design

1. Add a small deterministic-core helper (e.g. `bajutsu/run_id.py`): the format constant, a
   documented `new_run_id(prefix="")` (covering the `audit-` variant), and the matching
   validation pattern, with a docstring naming the three consumers above.
2. Replace the five mint sites with `new_run_id()`.
3. Point `valid_run_id` and the `serve/jobs.py` parse regex at the shared pattern.
4. A unit test pinning the contract: mint → validate → lexicographic order equals chronological
   order.

## Alternatives considered

- **Leave the five copies.** The contract keeps holding only by convention; the failure mode is
  a silent one (history order or run-id parsing breaks with no test naming the cause).
- **Random/UUID run ids.** Loses the property the UI relies on (lexicographic = chronological)
  and human-scannable run directories, for no gain — collisions are already impossible enough
  at one-second granularity per host for this tool's usage.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `new_run_id()` + format constant + validation pattern in one module
- [ ] Five mint sites migrated
- [ ] `valid_run_id` and the jobs.py parse regex share the pattern
- [ ] Contract unit test (mint → validate → sort order)

## References

- [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) · [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) · [`bajutsu/cli/commands/audit.py`](../../bajutsu/cli/commands/audit.py) · [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py) · [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py) · [`bajutsu/serve/jobs.py`](../../bajutsu/serve/jobs.py)

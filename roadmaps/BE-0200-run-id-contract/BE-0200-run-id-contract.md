**English** · [日本語](BE-0200-run-id-contract-ja.md)

# BE-0200 — Make the run-id format a single named contract

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0200](BE-0200-run-id-contract.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0200") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

Run ids are minted as `datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")`, spelled out independently
at four sites: `cli/commands/run.py`, `cli/commands/crawl.py`, `serve/operations/dispatch.py`, and
the `audit-` prefixed variant in `cli/commands/audit.py`. The format is not a local detail — other
code parses it back and derives timestamps from it, and the Web UI sorts by it — yet nothing in the
codebase names it. This item introduces one `new_run_id()` helper (plus the format constant and a
timestamp-shape pattern) and points the mint and timestamp-consumer sites at it.

## Motivation

The timestamp format is a cross-surface contract with several independent consumers:

- `serve/jobs.py:47` regex-parses run ids back out of CLI output (a safe-segment pattern, not a
  timestamp-shape one);
- `bajutsu/report/ctrf.py` parses the `YYYYmmdd-HHMMSS` shape with `strptime` to derive a UTC run
  start time;
- `serve/helpers.py`'s `valid_run_id` is a path-safety check (a single safe path segment,
  `^[A-Za-z0-9][A-Za-z0-9._-]*$`), so `runs_dir / run_id` can't escape — not a timestamp-format
  validator, and it intentionally accepts non-timestamp, client-supplied ids;
- the Web UI sorts run history lexicographically, which is chronological *only because* ids are
  zero-padded UTC timestamps in this exact shape.

Today the contract holds by convention: four call sites repeat the `strftime` pattern, and
`report/ctrf.py` repeats the shape when parsing it back. A well-meaning change at any one site (a
timezone tweak, a precision bump, a separator change) would silently break timestamp parsing or
history ordering elsewhere. Naming the contract once makes the dependency visible and the format
changeable in one place.

## Detailed design

1. Add a small deterministic-core helper (e.g. `bajutsu/run_id.py`): the format constant
   (`RUN_ID_FORMAT = "%Y%m%d-%H%M%S"`), a `new_run_id()` factory, and a timestamp-shape pattern for
   the consumers that need timestamp semantics — kept separate from `valid_run_id`, which stays the
   path-safety check it is.
2. Replace the four run-id mint sites with `new_run_id()` — `cli/commands/run.py`,
   `cli/commands/crawl.py`, `serve/operations/dispatch.py`, and the `audit-` prefixed variant in
   `cli/commands/audit.py`.
3. Point the timestamp-shape consumer, `report/ctrf.py`'s `strptime` parse, at the shared
   format/pattern, so a format change lands in one place. Leave `serve/jobs.py`'s output-parsing
   regex and `valid_run_id` untouched: neither validates the timestamp shape, and tightening
   `valid_run_id` would reject legitimate client-supplied ids.
4. Add a unit test pinning the contract: mint → parse → lexicographic order equals chronological
   order.

## Alternatives considered

- **Leave the four copies.** The contract keeps holding only by convention; the failure mode is a
  silent one (history order or timestamp parsing breaks with no test naming the cause).
- **Random/UUID run ids.** Loses the property the UI relies on (lexicographic = chronological)
  and human-scannable run directories, for no gain — collisions are already impossible enough
  at one-second granularity per host for this tool's usage.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Helper module added (`bajutsu/run_id.py`: `RUN_ID_FORMAT`, `new_run_id()`, `parse_run_id_timestamp()`)
- [x] Four run-id mint sites migrated (including the `audit-` prefixed variant)
- [x] Timestamp-shape consumer (`report/ctrf.py`) pointed at the shared format
- [x] Contract unit test added (`tests/test_run_id.py`)

## References

- [`bajutsu/cli/commands/run.py`](../../bajutsu/cli/commands/run.py) · [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) · [`bajutsu/cli/commands/audit.py`](../../bajutsu/cli/commands/audit.py) · [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py) · [`bajutsu/serve/jobs.py`](../../bajutsu/serve/jobs.py) · [`bajutsu/report/ctrf.py`](../../bajutsu/report/ctrf.py) · [`bajutsu/serve/helpers.py`](../../bajutsu/serve/helpers.py)

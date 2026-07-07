**English** · [日本語](BE-XXXX-crawl-command-decomposition-ja.md)

# BE-XXXX — Decompose the crawl CLI command like run

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-crawl-command-decomposition.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Codebase quality & technical debt |
<!-- /BE-METADATA -->

## Introduction

`bajutsu/cli/commands/crawl.py`'s `crawl` function runs 321 lines beyond its option
declarations. BE-0143 already solved this exact problem for the `run` command — a frozen plan
record plus small `_resolve_*` helpers, leaving a thin readable sequence — and shipped it. This
item applies the same, now-established pattern to `crawl`, behavior unchanged.

## Motivation

The body mixes several self-contained phases that today share one function scope:

- **Warm-start resolution** (lines ~190–232): `_load_base_map` plus the resume/continue
  branching that produces `(base_map, seed_path, seed_ops)` — clear inputs and outputs, its own
  error handling.
- **Lane planning** (~234–247), **progress/persist callbacks** (~295–310), and **alert-guard
  wiring** (~327–339) — each a small unit with an obvious name.

`run.py` after BE-0143 shows both the target shape and the constraint: the ~135-line typer
option signature stays inline (serve introspects the exact typer metadata via the BE-0134 flag
mirror — `serve/helpers.py`'s `crawl_command` builds `python -m bajutsu crawl …` argv the same
way it does for run), while the body becomes a plan record and a helper sequence. The same
constraint and the same solution apply here; the decomposition is mostly naming and moving, not
redesign.

## Detailed design

1. A `_CrawlPlan` frozen record capturing the resolved inputs the phases hand each other.
2. `_resolve_warm_start(…)` extracting the resume/continue block (base map, seed path, seed
   ops).
3. Lane-planning, callback, and guard-wiring helpers (one each, plain data in/out).
4. `crawl` becomes options + a thin sequence over the helpers; the typer signature is untouched
   (BE-0134 mirror constraint).
5. Unit tests for the extracted helpers (they take plain data, so no Simulator is needed);
   existing CLI tests keep covering the composed command.

## Alternatives considered

- **Leave it.** The 321-line body has the same costs BE-0143 documented for run: hard to read
  end to end, testable only at whole-command granularity, and risky to edit because every line
  can touch state built up hundreds of lines earlier.
- **Share plan machinery with `run.py`.** The two plans have almost no common fields; a shared
  abstraction would be forced. The shared thing is the *pattern*, not the code.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `_CrawlPlan` record
- [ ] `_resolve_warm_start` extraction
- [ ] Lane-planning / callback / guard-wiring helpers
- [ ] `crawl` body reduced to options + thin sequence (typer signature untouched)
- [ ] Unit tests for the extracted helpers

## References

- [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)
- [BE-0143](../BE-0143-run-command-decomposition/BE-0143-run-command-decomposition.md) — the shipped pattern this item applies to crawl
- [BE-0134](../BE-0134-serve-cli-flag-mirror-drift/BE-0134-serve-cli-flag-mirror-drift.md) — why the typer option signature must stay inline

**English** · [日本語](BE-0181-crawl-continuation-ja.md)

# BE-0181 — Resumable crawl continuation (Web UI + full-frontier resume)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0181](BE-0181-crawl-continuation.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0181") |
| Topic | Crawl performance / scale-out |
<!-- /BE-METADATA -->

## Introduction

Let a crawl (BE-0038) be picked back up later — from the Web UI, against the run it produced —
instead of only being extendable within the live browser tab that started it. Two things are
missing today: (1) the Web UI's existing "resume a pruned branch" affordance only works while
`crawlRunId` is still set in that tab's in-memory state, so it is unusable once the page reloads
or a different run is reopened; and (2) there is no way to continue exploring *past* a
`--max-screens`/`--max-steps` stop at all — only a single named pruned branch can be resumed, never
the whole remaining frontier. This proposal closes both gaps and, since the underlying engine
already reuses its backtrack machinery for anything seeded from a saved map, adds parallel workers
to the continuation for free.

## Motivation

[BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) shipped
crawl resume for exactly one case: a **pruned global control**
([`Pruned`](../../bajutsu/crawl.py) — a tab/nav skipped because another screen already claimed
it). `bajutsu crawl --resume-src <fp> --resume-key <key> --out <existing run>` replays to that
screen and explores the one skipped operation
([`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py)); the Web UI's crawl
graph shows every pruned op struck through and tapping one fires the same request
([`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js), `resumePruned`).

Two real gaps remain once you actually try to use this after the fact:

**1. The Web UI resume only works inside the tab that just ran the crawl.** `resumePruned` sends
`runId: crawlRunId` — a module-level JS variable set only by the response of the `/api/crawl` call
that started (or last resumed) a crawl in *that page*. There is no way to open the Crawl tab, load
a run that finished five minutes — or five days — ago from `runs/<id>/screenmap.json`, and tap one
of its pruned branches: `crawlRunId` is `null`, and every tap fails with "no active run to resume".
The Replay tab already solves the analogous problem for scenario runs with a run picker fed by
`history` ([`serve.js`](../../bajutsu/templates/serve.js), `loadHistory`); the Crawl tab has no
equivalent for reopening a past screen map.

**2. There is no way to keep exploring once a crawl stops on a budget.** `stop_reason` is
`"max_screens"` or `"max_steps"` far more often than `"completed"` on any app past toy size — that
is what the budgets are *for*. Today "I want to go deeper" means re-running the whole crawl from
scratch with a bigger `--max-screens`/`--max-steps`, discarding every already-discovered screen's
position in the map and re-walking from the entry screen. `--resume-src`/`--resume-key` cannot help
here: it takes exactly one named branch, not "everything this run left on the table."

Both gaps share one fix: give the engine a **full-frontier continuation** mode (resume *every*
screen with outstanding untried operations, not one), and give the Web UI a way to load an
arbitrary past run into the state the resume affordances already assume. Once continuation is not
restricted to a single named branch, there is no reason to restrict it to a single worker either —
the existing multi-worker pool ([BE-0064](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) /
[BE-0077](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md)) applies unchanged.

## Detailed design

### Reconstructing the frontier without new persisted state

`ScreenMap` already persists everything a full-frontier resume needs, because BE-0038 designed it
that way for the report/live-graph view:

- `paths: dict[fingerprint, tuple[Action, ...]]` — the canonical replayable path to every
  discovered screen.
- `plan: dict[fingerprint, list[str]]` — the human-readable description of each screen's
  still-untried operations, kept live for the graph view.

No schema change is needed. To resume screen `fp` with `plan[fp]` non-empty: replay `paths[fp]`
from a clean `reset()` (the same replay `select_next_work`'s existing backtrack path already does
for a live crawl), call `driver.query()`, compute the deterministic candidates with
`candidate_actions(elements)` ([`bajutsu/crawl.py`](../../bajutsu/crawl.py)), and keep only the
ones whose `.describe()` matches an entry still listed in `plan[fp]`. That is the exact set the
original crawl had not yet tried when it stopped — screen identity and candidate ordering are pure
functions of the element tree ([`crawl.py`](../../bajutsu/crawl.py) module docstring), so
re-deriving them on resume reproduces the same candidates the first run would have tried next,
with no risk of drifting from what `plan` promised.

### Engine: a full-frontier continuation mode

`crawl()` already accepts `base_map` for a warm start; the single-branch resume additionally passes
`seed_path`/`seed_ops` to seed exactly one screen's frontier and, deliberately, drops
`extra_workers` for it ("a resume is a single branch walk"). Add a second way to use `base_map`:
supplied *without* `seed_path`/`seed_ops`, skip `_bootstrap()` (the map's nodes are already known)
and instead seed `coord.pending[fp]` for every node reconstructed as above. From there, the existing
`select_next_work` backtrack loop — pick the cheapest pending entry, replay to it, continue — already
drives an arbitrary number of workers over a multi-root frontier exactly as it does mid-crawl today;
`extra_workers` needs no special-casing for this mode. The stop conditions
(`max_screens`/`max_steps`/`completed`) are unchanged; a continuation typically pairs with raising
whichever budget the prior run hit, though continuing at the same `--max-screens` is still useful
when the prior `stop_reason` was `max_steps` with screen budget left.

### CLI: `--continue`

Add `bajutsu crawl --out <existing run> --continue [--max-screens N] [--max-steps N] [--workers N]`,
distinct from `--resume-src`/`--resume-key` (which keeps its single-branch meaning). `--continue`
loads `screenmap.json`, reconstructs the full frontier, and runs the normal worker pool sized by
`--udid`/`--workers` exactly like a fresh crawl — the parallel continuation the single-branch resume
never supported. `--continue` and `--resume-src`/`--resume-key` are mutually exclusive (the CLI
rejects both being set).

### Web UI

- **Reopen a past run into the Crawl tab.** Add a run picker to the Crawl panel, mirroring the
  Replay tab's `history`-fed picker (`loadHistory` in
  [`serve.js`](../../bajutsu/templates/serve.js)): choosing a run loads its `screenmap.json` via
  the existing `loadGraph(runId)` and sets `crawlRunId = runId`, so the pruned-branch "tap to
  resume" affordance works on any past run, not only the tab that produced it.
- **A "continue exploring" control.** Next to the pruned-branch rows in the plan tree, add a
  "continue exploring" action that posts the new `continue: true` mode (reusing the existing
  `#crawl-maxscreens`/`#crawl-maxsteps` inputs so the user can raise the budget in the same request)
  instead of a single `resumeSrc`/`resumeKey`. `start_crawl`
  ([`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py)) grows a
  `continue` branch alongside its existing `resuming` branch, threading a `continue_crawl` flag
  through `crawl_command` to the CLI's `--continue`.

## Alternatives considered

- **Auto-continue until the crawl is truly exhausted, dropping the budgets.** Rejected: the budgets
  exist to bound device time and AI-guide calls on purpose; an explicit, reviewable continue step
  keeps the same "spend more only when asked" gate the single-branch resume already established.
- **Persist a fully materialized list of pending `Action`s in `screenmap.json` instead of
  reconstructing them from `paths` + `plan` on resume.** Would work, but doubles the state that must
  stay in sync with the live element tree and adds a schema change; re-deriving via
  `candidate_actions` on replay costs one extra query per resumed screen and reuses the same
  determinism guarantee (identical elements → identical candidates) the whole engine already relies
  on, for no schema change at all.
- **Fix only the Web UI's "resume only works in the originating tab" bug, without the
  full-frontier continuation.** Handles the pruned-branch gap but not "explore deeper," which is
  the more common way an app outgrows its first crawl's budget. The two are cheap to ship together
  once the run picker exists, since the continuation button is a variant of the same request.
- **A client-side-only fix (persist `crawlRunId` to `localStorage`).** Solves reload-in-the-same-tab
  but not reopening a *different* run's map days later, which is the actual use case ("go back to
  that crawl and push further"); a real run picker backed by `runs/` is needed regardless.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Engine: full-frontier reconstruction from `paths` + `plan` (no schema change) and the
      `base_map`-without-`seed_ops` continuation path in `crawl()`.
- [ ] Engine: allow `extra_workers` for a full-frontier continuation (parallel resume).
- [ ] CLI: `--continue` flag on `bajutsu crawl`, mutually exclusive with `--resume-src`/`--resume-key`.
- [ ] Web UI: run picker to reopen a past run's `screenmap.json` into the Crawl tab (fixes
      pruned-branch resume outside the originating tab).
- [ ] Web UI: "continue exploring" control wired through `dispatch.py`'s `start_crawl`.

## References

- [`bajutsu/crawl.py`](../../bajutsu/crawl.py) — `ScreenMap` (`paths`, `plan`, `pruned`),
  `candidate_actions`, `_Coordinator.select_next_work`, `crawl()`.
- [`bajutsu/cli/commands/crawl.py`](../../bajutsu/cli/commands/crawl.py) — the existing
  `--resume-src`/`--resume-key` single-branch resume this proposal extends.
- [`bajutsu/serve/operations/dispatch.py`](../../bajutsu/serve/operations/dispatch.py),
  [`bajutsu/templates/serve.js`](../../bajutsu/templates/serve.js) — `start_crawl`,
  `resumePruned`, `loadHistory` (the run-picker precedent from the Replay tab).
- [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)
  — introduced pruned-branch resume, which this item generalizes.
- [BE-0064 — Parallel crawl across multiple simulators](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md),
  [BE-0077 — Parallel web crawl across multiple browsers](../BE-0077-parallel-web-crawl/BE-0077-parallel-web-crawl.md)
  — the worker pool a full-frontier continuation reuses unchanged.
- [BE-0092 — Extract the crawl coordinator into a class](../BE-0092-crawl-coordinator-extraction/BE-0092-crawl-coordinator-extraction.md)
  — the `_Coordinator` this item's frontier-seeding hooks into.

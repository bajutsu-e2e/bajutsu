**English** · [日本語](BE-0064-parallel-crawl-ja.md)

# BE-0064 — Parallel crawl across multiple simulators

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0064](BE-0064-parallel-crawl.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0064") |
| Implementing PR | [#198](https://github.com/bajutsu-e2e/bajutsu/pull/198) |
| Topic | Autonomous crawl |
| Origin | User request (crawl efficiency) |
<!-- /BE-METADATA -->

## Introduction

This item runs the [BE-0038 autonomous crawl](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) across **N booted simulators at once**. Independent frontier work then overlaps, and a full screen map takes a fraction of the wall-clock time.

The crawl stays a discovery tool (Tier 1, never a CI gate). Only its *scheduling* becomes concurrent. Screen identity, transitions, and crashes are decided exactly as before.

## Motivation

A crawl is serial today: it explores one screen at a time on one device. Two latency-bound waits dominate the per-screen cost, and both leave the machine idle:

1. **AI guide round-trips.** With `--guide ai`, every newly discovered screen triggers one or more model calls. The action proposer makes one call. The vision tab locator adds another for a tab bar that no selector can address. All of these calls are network round-trips.
2. **Device work.** Backtracking to an unexplored screen resets the app and replays a recorded path, and the cost grows with the depth of the screen. Each tap and observe also waits on the simulator.

Both waits overlap cleanly across independent simulators. Wall-clock time falls roughly with the number of devices, until AI rate limits or coordinator contention dominate.

`run` already scales across a simulator pool ([`runner/pool.py`](../../bajutsu/runner/pool.py)), and the WebUI already lets Replay pick a multi-device pool. Crawl is the one Tier-1 path still pinned to a single device. That makes it slow to use as the front end to `record` and as the whole-app coverage-measurement run ([DESIGN §7.2](../../DESIGN.md); [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) motivation #2).

## Detailed design

### Coordinator + workers

A **coordinator** owns the shared screen map and the frontier under a lock. It holds these fields:

- `nodes` and `edges`
- `path_to`, a replayable path to each screen
- `pending`, the untried actions per screen
- `visited`
- the budget counters

**N workers** each own one booted simulator with the app installed. Each worker loops:

1. **Lock.** Pick the cheapest frontier entry by the same deterministic rule as today (shortest `path_to`, then fingerprint). Pop one action and mark it in-flight. **Unlock.**
2. On its own simulator, `reset` and replay `path_to[fp]`, dismissing OS alerts en route. Perform the action, then `observe` (with `clear_blocking`).
3. **Lock.** Record the edge, crash, or alert. If the destination screen is new, add the node, `path_to`, and `pending`, and run the guide on that simulator while it is positioned there. **Unlock.**

The AI calls of the guide thus run concurrently across workers, which is the primary speedup. Each worker keeps the **forward-walk** optimization. A worker keeps operating on its current screen until no untried action remains. It backtracks (reset and replay) only to reach another frontier entry. That is the single-device strategy of today, now run on each worker.

### The determinism boundary (the crux)

Screen *identity* (the fingerprint), transition detection, crash detection, and the content of the screen map stay pure deterministic functions of the element tree, unchanged. AI still chooses only *what to try* and never judges ([prime directive #1](../../CLAUDE.md)).

Parallelism relaxes two things: the **exploration order** and the **recorded canonical `path_to`**. Which worker reaches a screen first depends on scheduling. For an app with its own non-determinism, the recorded paths and the tie-broken discovery order can differ from run to run. For a deterministic app, the *set* of discovered nodes and edges is invariant, and only the ordering and path metadata vary.

The relaxation is acceptable because crawl is **Tier 1 and emits a discovery artifact, never a pass/fail**. [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) already states the same reason: the crawl itself can never be a CI gate. The deterministic *byproducts* keep their guarantees. A recorded repro or flow path still replays AI-free under `run` as a Tier 2 regression.

### Surface

- **CLI.** `bajutsu crawl --workers N` (default `1`, today's behavior) plus an explicit pool via `--udid a,b,c`, mirroring the pool flags of `run`. With `--workers N` and fewer udids, the command boots or allocates devices like the run pool.
- **WebUI.** The single-device dropdown of the Crawl tab becomes a multi-select pool, like Replay. The worker count follows from the selection. The live screen map and plan tree already stream from the shared map, so rendering needs no change.
- **Budgets and stop.** `--max-screens` and `--max-steps` become shared counters checked under the lock. The crawl stops when the frontier is empty or a budget is hit, and it reports the same `stop_reason`.
- **Failure isolation.** A worker whose simulator wedges (a replay that no longer resolves, or a device error) drops its current frontier entry and continues. One bad device cannot sink the crawl, which mirrors the per-worker isolation of the run pool.

## Alternatives considered

- **Static subtree partitioning** (give each worker a branch of the app). Rejected. The tree is discovered dynamically, so no partition can be computed up front. Workers would sit idle or explore redundantly.
- **Independent crawls merged afterward** (each worker crawls from the entry and the results are deduplicated at the end). Rejected. Shared screens would be re-explored massively. The shared frontier is what makes the work disjoint.
- **Process-level parallelism** (N `bajutsu crawl` processes writing one map). Rejected. Cross-process shared state and file locking are more fragile than one coordinator with worker tasks, the model that `run` already uses.
- **Async on a single device** (overlap AI calls without more simulators). This helps AI latency but not device work, and one simulator cannot act on two screens at once. The simulator pool is the real lever. Combining both is worth doing later, but async is no substitute.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

- [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — the engine this extends.
- [`bajutsu/runner/pool.py`](../../bajutsu/runner/pool.py) — `run`'s simulator pool, the existing concurrency model to mirror.
- [CLAUDE.md](../../CLAUDE.md) — prime directive #1 (AI never judges) and #2 (determinism first).
- [DESIGN §7.2](../../DESIGN.md) — whole-app coverage from crawl dumps; a faster crawl makes it practical.

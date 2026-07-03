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
| Topic | Crawl performance / scale-out |
| Origin | User request (crawl efficiency) |
<!-- /BE-METADATA -->

## Introduction

Run the [BE-0038 autonomous crawl](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) across **N booted simulators at once**, so independent frontier work overlaps and a full screen map is built in a fraction of the wall-clock time. The crawl stays a discovery tool (Tier 1, never a CI gate); only its *scheduling* becomes concurrent — screen identity, transitions, and crashes are decided exactly as before.

## Motivation

A crawl is serial today: it explores one screen at a time on one device. The per-screen cost is dominated by two latency-bound waits that leave the machine idle:

1. **AI guide round-trips.** With `--guide ai`, every newly discovered screen makes one or more model calls — the action proposer, and (for an un-addressable tab bar) the vision tab locator. These are network round-trips.
2. **Device work.** Backtracking to an unexplored screen resets the app and replays a recorded path, whose cost grows with the screen's depth; each tap/observe also waits on the simulator.

Both overlap cleanly across independent simulators, so wall-clock time falls roughly with the number of devices until AI rate limits or coordinator contention dominate.

`run` already scales across a simulator pool ([`runner/pool.py`](../../../bajutsu/runner/pool.py)), and the WebUI already lets Replay pick a multi-device pool. Crawl is the one Tier-1 path still pinned to a single device — which makes it slow to use as the front end to `record` and as the whole-app coverage-measurement run ([DESIGN §7.2](../../../DESIGN.md); [BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) motivation #2).

## Detailed design

### Coordinator + workers

A **coordinator** owns the shared screen map and frontier under a lock: `nodes`, `edges`, `path_to` (a replayable path to each screen), `pending` (untried actions per screen), `visited`, and the budget counters. **N workers** each own one booted simulator with the app installed.

Each worker loops:

1. **Lock** → pick the cheapest frontier entry by the same deterministic rule used today (shortest `path_to`, then fingerprint), pop one action and mark it in-flight → **unlock**.
2. On its own simulator: `reset`, replay `path_to[fp]` (dismissing OS alerts en route), perform the action, `observe` (with `clear_blocking`).
3. **Lock** → record the edge / crash / alert; if the destination screen is new, add the node + `path_to` + `pending`, running the guide on that simulator while positioned there → **unlock**.

The guide's AI calls thus run concurrently across workers — the primary speedup. The **forward-walk** optimization is preserved per worker: a worker keeps operating on the screen it is on until it has no untried action, backtracking (reset + replay) only to reach another frontier entry — exactly today's single-device strategy, now run on each worker.

### The determinism boundary (the crux)

Screen *identity* (the fingerprint), transition detection, crash detection, and the screen map's content stay pure deterministic functions of the element tree — unchanged. AI still only chooses *what to try* and never judges ([prime directive #1](../../../CLAUDE.md)).

What parallelism relaxes is **exploration order** and the **recorded canonical `path_to`**: which worker reaches a screen first is scheduling-dependent, so for an app with its own non-determinism the recorded paths (and the tie-broken discovery order) can differ run to run. For a deterministic app the *set* of nodes and edges discovered is invariant; only ordering/path metadata varies.

This is acceptable precisely because crawl is **Tier 1 and emits a discovery artifact, never a pass/fail** — the same reason [BE-0038](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) already states the crawl itself can never be a CI gate. The deterministic *byproducts* keep their guarantees: a recorded repro/flow path still replays AI-free under `run` as a Tier 2 regression.

### Surface

* **CLI:** `bajutsu crawl --workers N` (default `1` = today's behavior) plus an explicit pool via `--udid a,b,c`, mirroring `run`'s pool flags; with `--workers N` and fewer udids, boot/allocate devices like the run pool.
* **WebUI:** the Crawl tab's single-device dropdown becomes a multi-select pool (like Replay), with the worker count derived from the selection. The live screen map and plan tree already stream from the shared map, so no rendering change is needed.
* **Budgets & stop:** `--max-screens` / `--max-steps` become shared counters checked under the lock; the crawl stops when the frontier is empty or a budget is hit, reporting the same `stop_reason`.
* **Failure isolation:** a worker whose simulator wedges (a replay that no longer resolves, a device error) drops its current frontier entry and continues, so one bad device can't sink the crawl — mirroring the run pool's per-worker isolation.

## Alternatives considered

* **Static subtree partitioning** (give each worker a branch of the app). Rejected: the tree is discovered dynamically, so a partition can't be computed up front; it would leave workers idle or redundantly exploring.
* **Independent crawls merged afterward** (each worker crawls from the entry, dedup at the end). Rejected: massive redundant re-exploration of shared screens — the shared frontier is what makes the work disjoint.
* **Process-level parallelism** (N `bajutsu crawl` processes writing one map). Rejected: cross-process shared state + file locking is more fragile than one coordinator with worker tasks, the model `run` already uses.
* **Async on a single device** (overlap AI calls without more simulators). Helps AI latency but not device work, and one simulator can't act on two screens at once; the simulator pool is the real lever. Worth combining later, not a substitute.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

* [BE-0038 — Autonomous crawl exploration](../../in-progress/BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — the engine this extends.
* [`bajutsu/runner/pool.py`](../../../bajutsu/runner/pool.py) — `run`'s simulator pool, the existing concurrency model to mirror.
* [CLAUDE.md](../../../CLAUDE.md) — prime directive #1 (AI never judges) and #2 (determinism first).
* [DESIGN §7.2](../../../DESIGN.md) — whole-app coverage from crawl dumps; a faster crawl makes it practical.

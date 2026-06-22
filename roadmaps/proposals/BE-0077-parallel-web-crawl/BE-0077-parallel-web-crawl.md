**English** · [日本語](BE-0077-parallel-web-crawl-ja.md)

# BE-0077 — Parallel web crawl across multiple browsers

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0077](BE-0077-parallel-web-crawl.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Track | [Proposals](../../README.md#proposals) |
| Topic | Crawl performance / scale-out |
| Origin | User request (parallel web crawl) |
<!-- /BE-METADATA -->

## Introduction

Run the [BE-0066 web crawl](../../implemented/BE-0066-web-crawl/BE-0066-web-crawl.md) across **N browsers at once**, so independent frontier work overlaps and one screen map is built in a fraction of the wall-clock time. This is the web counterpart of [BE-0064 (parallel crawl across simulators)](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) — the intersection both that item and BE-0066 already flagged as a follow-on ("parallel web crawl … is the intersection of this item with the parallel-crawl axis and can follow once both land"). As on iOS, the crawl stays a Tier-1 discovery tool (never a CI gate); only its *scheduling* becomes concurrent, and screen identity, transitions, and crashes are decided exactly as before.

Each worker owns its own **browser process** (a launched Playwright `Browser`), not a `BrowserContext` lane inside one shared browser. The goal here is **speed** — one shared screen map, built faster — not cross-engine coverage; running the same app on different engines (Chromium / Firefox / WebKit) to surface engine-specific differences is a distinct feature, kept out of scope below.

## Motivation

A web crawl is serial today: it explores one screen at a time in one browser. The per-screen cost is dominated by two latency-bound waits that leave the machine idle — the same two BE-0064 identifies on iOS, in their web form:

1. **AI guide round-trips.** With `--guide ai`, every newly discovered screen makes one or more model calls (the action proposer). These are network round-trips.
2. **Browser and page work.** Reaching an unexplored screen resets to a clean start and replays a recorded path (handling JS dialogs en route), then performs the action and observes; each navigation, action, and observation waits on the browser and the network, and the replay cost grows with the screen's depth.

Both overlap cleanly across independent browsers, so wall-clock time falls roughly with the number of workers until AI rate limits or coordinator contention dominate.

**Web is the lowest-friction place to make a parallel crawl pay off.** It needs no Mac and no emulator and runs on the existing Linux `make check` / CI gate ([BE-0041](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md)), so a fast crawl can run as a discovery step right inside CI. A browser process also starts in seconds with no device boot, so standing up N lanes is cheap. `run` already scales across lanes — [BE-0054](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) generalizes the web branch of the pool to N — leaving crawl the one Tier-1 path still pinned to a single browser, which makes it slow to use as the front end to `record` and as the whole-app coverage run ([DESIGN §7.2](../../../DESIGN.md); [BE-0038](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) motivation #2).

## Detailed design

### Coordinator + workers

A **coordinator** owns the shared screen map and frontier under a lock — `nodes`, `edges`, `path_to` (a replayable path to each screen), `pending` (untried actions per screen), `visited`, and the budget counters — exactly as in BE-0064. **N workers** each own one browser process with the target app loaded.

Each worker loops:

1. **Lock** → pick the cheapest frontier entry by the same deterministic rule (shortest `path_to`, then fingerprint), pop one action and mark it in-flight → **unlock**.
2. In its own browser: `reset`, replay `path_to[fp]` (handling JS dialogs en route via the deterministic dialog auto-handler), perform the action, `observe`.
3. **Lock** → record the edge / crash / dialog; if the destination screen is new, add the node + `path_to` + `pending`, running the guide in that browser while positioned there → **unlock**.

The guide's AI calls thus run concurrently across workers — the primary speedup. The **forward-walk** optimization is preserved per worker: a worker keeps operating on the screen it is on until it has no untried action, backtracking (reset + replay) only to reach another frontier entry — today's single-browser strategy, now run on each worker.

### The lane is a browser process; the per-reset clean state is a fresh context

A worker's durable lane is a full **browser process** — the unit the coordinator hard-kills and relaunches on a fault (below). The per-iteration `reset`, however, is **not** a browser relaunch: it is a fresh `BrowserContext` inside that worker's browser, which is the `erase` equivalent and is ~free. This reuses BE-0066's existing web reset seam (`reset` is already just `driver.navigate()` into a fresh context, mirroring `_web_relauncher` in [`runner/pool.py`](../../../bajutsu/runner/pool.py)). So "separate browser processes" costs one browser launch per *worker* (and per fault), not per screen.

### The determinism boundary (the crux)

Screen *identity* (the fingerprint), transition detection, crash detection, and the screen map's content stay pure deterministic functions of the element tree — unchanged. Web crash detection keeps BE-0066's deterministic signals (an uncaught JS exception via `page.on("pageerror")`, an HTTP 4xx/5xx main-frame status, or a blank DOM); none is an LLM judgment. AI still only chooses *what to try* and never judges ([prime directive #1](../../../CLAUDE.md)).

What parallelism relaxes is **exploration order** and the **recorded canonical `path_to`**: which worker reaches a screen first is scheduling-dependent, so for an app with its own non-determinism the recorded paths (and the tie-broken discovery order) can differ run to run. For a deterministic app the *set* of nodes and edges discovered is invariant; only ordering / path metadata varies. This is acceptable precisely because crawl is **Tier 1 and emits a discovery artifact, never a pass/fail** — the same reason BE-0038 and BE-0064 already give. The deterministic *byproducts* keep their guarantees: a recorded crash repro / flow path still replays AI-free under web `run` as a Tier-2 regression.

### Surface

* **CLI:** `bajutsu crawl --backend web --workers N` (default `1` = today's serial web crawl). Each worker launches its own browser process. Web has no devices, so unlike BE-0064's `--udid a,b,c` pool the worker count alone sizes the lane set — generalizing BE-0054's single-lane web pool (one dummy udid, `workers = 1`) to N. All workers use the one configured engine (cross-engine fan-out is out of scope — see Alternatives).
* **WebUI (serve):** the Crawl tab's web target gains a worker-count control (a number input, since the web has no device multi-select like the Simulator picker). The live screen map already streams from the shared map, so no rendering change is needed — same as BE-0064.
* **Budgets & stop:** `--max-screens` / `--max-steps` become shared counters checked under the lock; the crawl stops when the frontier is empty or a budget is hit, reporting the same `stop_reason`.
* **Failure isolation:** a worker whose browser wedges — a navigation that times out, a renderer crash, a hung page, a replay that no longer resolves — drops its current frontier entry, and its browser process is torn down and relaunched before it continues, so one bad browser can't sink the crawl. This is where process-level lanes earn their cost: the coordinator can hard-kill one worker's browser without touching the others — the web counterpart of BE-0064's per-simulator isolation.

### Reuse of the web pool (BE-0054)

The web branch of [`runner/pool.py`](../../../bajutsu/runner/pool.py) that BE-0054 generalizes to N lanes is the seam this builds on. The one difference is the lane's isolation unit: this item's crawl lane is a full browser process (for fault isolation, since a crawl deliberately provokes crashes and hangs), whereas BE-0054's `run` lanes are `BrowserContext`s (cheaper, and fine for independent scenarios). Both are "N lanes in the web pool"; the unit is chosen per use case.

## Alternatives considered

* **`BrowserContext` lanes (BE-0054's `run` model) instead of separate processes.** Rejected for crawl: N contexts share one browser process, so a hung page, a runaway script, or a browser-process crash in one lane stalls or kills every lane — unacceptable when the crawl's whole job is to provoke crashes and hangs. Separate processes give OS-level fault isolation and independent hard-kill / relaunch, mirroring BE-0064's per-simulator isolation. (A fresh context is still used *within* each worker's browser for the cheap per-reset erase.)
* **Cross-engine fan-out** (crawl the same app on Chromium / Firefox / WebKit at once). A different feature — cross-browser compatibility discovery, not speed; it would want N *independent* maps to diff, not one shared frontier. This item runs N processes of the one configured engine to build a single map faster. A future cross-browser-crawl item could compose with this one (run this parallel crawl per engine, then diff the maps); folding it in here would muddy both the shared-frontier model and the determinism story.
* **Static partitioning of the site** (give each worker a URL subtree). Rejected, as in BE-0064: the graph is discovered dynamically, so a partition can't be computed up front; it would leave workers idle or redundantly exploring.
* **Independent crawls merged afterward** (each worker crawls from the entry, dedup at the end). Rejected, as in BE-0064: massive redundant re-exploration of shared screens — the shared frontier is what makes the work disjoint.
* **Async on a single browser** (overlap AI calls without more browsers). Helps AI latency but not browser / page work, gives no fault isolation, and one page can't act on two screens at once; the browser pool is the real lever. Worth combining later, not a substitute.
* **Fold this into BE-0064.** Rejected: BE-0064 is framed around iOS simulators (simctl reset / replay, a booted-device pool); web uses browser-process lanes, context reset, and BE-0066's web crash / dialog semantics — a distinct design surface. This is the same "same axis, different platform → new item" relationship BE-0066 has with BE-0038, and BE-0066 explicitly named parallel web crawl as the intersection to follow.

## References

* [BE-0064 — Parallel crawl across multiple simulators](../BE-0064-parallel-crawl/BE-0064-parallel-crawl.md) — the concurrency model this mirrors on web.
* [BE-0066 — Web crawl (Playwright backend)](../../implemented/BE-0066-web-crawl/BE-0066-web-crawl.md) — the serial web crawl this parallelizes; its reset seam, crash signals, and dialog handler carry over per worker.
* [BE-0054 — Web backend completion](../BE-0054-web-backend-completion/BE-0054-web-backend-completion.md) — generalizes the web pool to N lanes; this item's lane is a browser process rather than a context.
* [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) — the platform-neutral crawl engine.
* [BE-0041 — Web (Playwright) backend](../BE-0041-web-playwright-backend/BE-0041-web-playwright-backend.md) — the web driver and the deterministic web `run` path.
* [`bajutsu/runner/pool.py`](../../../bajutsu/runner/pool.py), [`bajutsu/crawl.py`](../../../bajutsu/crawl.py), [`bajutsu/cli/commands/crawl.py`](../../../bajutsu/cli/commands/crawl.py), [`bajutsu/drivers/playwright.py`](../../../bajutsu/drivers/playwright.py).
* [CLAUDE.md](../../../CLAUDE.md) — prime directive #1 (AI never judges) and #2 (determinism first); [DESIGN §7.2](../../../DESIGN.md) — whole-app coverage from crawl dumps, which a faster crawl makes practical.

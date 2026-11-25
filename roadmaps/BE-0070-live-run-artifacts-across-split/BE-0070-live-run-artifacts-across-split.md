**English** · [日本語](BE-0070-live-run-artifacts-across-split-ja.md)

# BE-0070 — Live in-progress run artifacts across the worker split

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0070](BE-0070-live-run-artifacts-across-split.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Deferred** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0070") |
| Topic | Hosting the web UI |
<!-- /BE-METADATA -->

## Introduction

The hosted `bajutsu serve` runs as **two instances**. A control plane (the web UI and API, on Linux) serves the browser. A separate **worker** (idb or Playwright, on a Mac) runs the test. Redis and an object store connect the two
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)).

During a run, the browser shows three live surfaces:

- the **console log**
- the **final result and report**
- the **exploration graph** that grows as a
  [`crawl`](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) explores

Two of the three already cross the instance split. The exploration graph does not.

This proposal closes that gap. It makes a run's **in-progress (live) artifacts** visible across the control-plane/worker split, with the **live crawl graph** as the first consumer. To keep the scope sharp, it deliberately does not re-propose the two surfaces that already cross the split:

- **Console log (already crosses).** Live run output travels over the **LogBus seam**. The worker publishes each stdout line to Redis (`RedisLogBus`). The control plane relays the line to the browser over server-sent events (SSE). This proposal leaves it alone.
- **Final result and report (already crosses).** Terminal state persists. The worker uploads the `runs/<id>/` tree to the object store and writes a run record. The UI reads both after the run. This proposal leaves it alone.

The missing piece is the **live crawl graph**. It relies on a shared filesystem, and the worker split breaks that assumption.

## Motivation

**The gap, concretely.** A crawl writes `runs/<id>/screenmap.json` *incrementally*. The engine fires an `on_event` callback after every discovery, and the callback rewrites the file (`bajutsu/cli/commands/crawl.py`). The browser polls `GET /runs/<id>/screenmap.json` on each SSE log line and redraws (`bajutsu/templates/serve.js`, `loadGraph`). In **local** mode this works, because the process that writes the file and the server that serves it share one filesystem.

In **server** mode it breaks. The worker writes `screenmap.json` to *its own* disk, but artifacts reach the object store at job end. `_upload_runs` runs after `run_job` returns, which means after the crawl subprocess has already exited (`bajutsu/serve/server/worker_job.py`). While the crawl runs, the control plane's `ObjectStorageArtifactStore.get` finds no key and returns **404**. The browser's poll fails, and the graph stays empty **until the crawl finishes**.

**Root cause.** Logs have a transport that crosses the split (the LogBus). In-progress artifacts have none. The artifact store uploads at the end and reads per request. That design suits a report, but it does not suit a file that the UI **reads while the file is still changing**. The crawl graph is the first such "live artifact", and nothing carries it across the split.

**Why it matters.** A crawl runs for a long time, and the growing graph is its primary user experience (UX). A user watches coverage build, screens appear, and the frontier shrink. The hosted topology is the one teams actually deploy ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)). On that topology the UX silently degrades to "watch the log scroll, then see the whole graph at the end". The feature looks finished in local dogfooding and quietly regresses in production.

## Detailed design

### Scope of live artifacts

A **live artifact** is a file that a run updates *during* execution and that the UI reads *mid-run*. A terminal artifact (the report, evidence) differs: the UI reads it after the run. Exactly one live artifact exists today: `screenmap.json`.

The design names a small, explicit **allowlist of live-artifact paths**. The allowlist keeps the mechanism general, so a future live progress file can reuse it. It also avoids turning every artifact into a streamed one.

### Primary design: incremental upload of live artifacts

The worker already has the right boundary: the `on_event` callback that rewrites `screenmap.json` on the worker's disk after each discovery. The design hooks the **same** boundary to also `put` the artifact to the object store. The control plane's `ObjectStorageArtifactStore.get` then finds the artifact mid-run. The browser keeps polling the **same** `/runs/<id>/...` route. Only *where the bytes come from* changes, which has four consequences:

- **No UI change and no GET-route change.** Local mode is unaffected and still serves the live file from disk. Server mode finds the same file in the object store. One code path serves both modes.
- **Throttled, not per-event.** A chatty crawl fires many events. The worker coalesces uploads to at most one per short interval (about 1–2 s, matching the browser's poll cadence). It adds a final authoritative write at job end. This bounds object-store PUTs without a perceptible UX loss, because the graph already updates about once per second.
- **Consistency is not a worry.** S3 and R2 overwrites are strongly read-after-write consistent. A poll issued after an upload returns the fresh snapshot, so no stale-read window needs a design.
- **Terminal upload still wins.** The end-of-job `_upload_runs` remains the authority for the complete `runs/<id>/` tree. Incremental upload makes the *live* artifact visible earlier and changes nothing else.

### The contract: machine-checkable invariants

The Linux gate verifies these invariants with an in-memory (fake) object store. It involves no Mac and no Simulator.

1. **Mid-run visibility (the regression).** A fake worker emits N screen-map snapshots before it finishes. The control plane's artifact `get` returns the k-th snapshot **before** the job is marked done. Today that case returns 404.
2. **Terminal authority.** After the job completes, the stored `screenmap.json` equals the last on-disk snapshot. The bulk terminal upload agrees with the incremental writes.
3. **Bounded uploads.** M discoveries over a duration produce at most `ceil(duration / interval) + 1` uploads. PUTs scale with wall-clock time, not with event count.
4. **Local-mode parity.** In local mode the live graph still serves from disk, unchanged. The path that already works does not regress.
5. **Gate-clean.** The default path makes no large language model (LLM) call and adds no heavy dependency. The deterministic core is untouched.

### Prime-directive fit

This change is **Tier-1 `crawl` and serve plumbing** alone. No LLM enters the `run` / CI gate ([DESIGN §2](../../DESIGN.md)). Determinism holds, because the change alters *when bytes become visible* and never *what the crawl explores*. A crawl's traversal stays a pure function of the element tree. The mechanism is app-agnostic: it moves a file, whatever the target app.

## Alternatives considered

- **Push graph events on the bus (deltas over SSE) instead of polling a file.** This is the more general "progress channel". The worker emits each new node or edge as a structured event. The control plane relays the event over SSE alongside the log lines, and the browser updates from the payload without polling. The option is attractive because it unifies on the transport that already crosses the split (the LogBus), and a delta is far smaller than re-fetching the whole map. **Deferred**, not rejected: it is a larger change. It needs a new event type, moves the UI's data path from polling to events, and must handle both local and server modes. Incremental upload preserves the existing seam and the existing UI. A future progress channel can subsume the polling once it earns its keep.
- **Worker → control-plane artifact proxy.** The control plane would read in-progress files from the worker over HTTP. **Rejected**: workers are **pull-based** and lease jobs from Redis, so they are not addressable servers. Making the worker serve files inverts the lease model. It adds network and security surface for no gain over uploading.
- **Accept a terminal-only graph in server mode (do nothing).** **Rejected**: the live graph is the primary UX of crawl, and the hosted topology is the one users deploy. A feature that works in local dogfooding alone is a silent production regression.
- **Upload on every event (no throttle).** **Rejected**: a chatty crawl would produce unbounded PUTs. Coalescing at the poll cadence gives the same UX.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

**Deferred (2026-07-02).** The premise that `crawl` runs on a remote worker — making the live
graph invisible to the control plane — no longer holds: crawl is not distributed. Only test
execution is distributed, and the design for distributed test execution collects results
**after** the run completes on the worker, then sends them back to the orchestrator. There is
no mid-run live artifact to carry across the split, so the problem this proposal solves does
not exist. If a future design introduces a genuinely live artifact during distributed
execution, this proposal can be revisited.

## References

- [DESIGN.md](../../DESIGN.md) §2 — determinism-first; AI stays out of the `run` / CI gate. This
  change keeps the crawl's traversal deterministic and adds no model call.
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
  — introduces the control-plane ⇄ worker split and the LogBus / artifact-store seams this item builds on.
- [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  — the worker that "leases jobs … streams logs back, and uploads the `runs/<id>/` tree" at job end;
  this item makes the live artifact visible *before* that terminal upload.
- [BE-0038 — Autonomous crawl exploration](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md)
  — the crawl and its local-mode live graph that this extends across the split; the first consumer.
- [BE-0055 — Operational logging for the hosted serve](../BE-0055-operational-logging/BE-0055-operational-logging.md)
  — sibling hosting item; notes that the LogBus already carries run *output* live, i.e. console logs
  already cross the split.
- [architecture.md#implementation-status](../../docs/architecture.md#implementation-status) — the
  serve UI's "live job streaming" and the crawl screen map.
- Source touchpoints: `bajutsu/serve/server/worker_job.py` (`_upload_runs`, the terminal upload),
  `bajutsu/serve/artifacts.py` / `bajutsu/serve/server/artifacts.py` (the artifact-store seam),
  `bajutsu/cli/commands/crawl.py` (`on_event`, the incremental write), `bajutsu/templates/serve.js`
  (`loadGraph`, the poll-and-redraw).

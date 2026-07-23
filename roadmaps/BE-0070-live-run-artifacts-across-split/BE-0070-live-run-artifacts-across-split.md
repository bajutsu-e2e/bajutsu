**English** · [日本語](BE-0070-live-run-artifacts-across-split-ja.md)

# BE-0070 — Live in-progress run artifacts across the worker split

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0070](BE-0070-live-run-artifacts-across-split.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal (deferred)** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0070") |
| Topic | Hosting the web UI |
<!-- /BE-METADATA -->

## Introduction

The hosted `bajutsu serve` is a **two-instance** system: a control plane (the web UI + API, on
Linux) and a separate **worker** that actually runs the test (idb / Playwright, on a Mac), wired
together over Redis and an object store
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)). During a
run the browser shows three live surfaces: the **console log**, the **final result / report**, and —
for [`crawl`](../BE-0038-autonomous-crawl-exploration/BE-0038-autonomous-crawl-exploration.md) —
the **exploration graph** that grows as the crawl explores. Two of the three already cross the
instance split; one does not.

This proposal closes that one gap: make a run's **in-progress (live) artifacts** visible across the
control-plane/worker split, with the **live crawl graph** as the first consumer. It deliberately does
**not** re-propose the two surfaces that already cross the split, so the scope stays sharp:

- **Console log — already crosses.** Live run output travels over the **LogBus seam**: the worker
  publishes each stdout line to Redis (`RedisLogBus`) and the control plane relays it to the browser
  over SSE (server-sent events). Not this.
- **Final result / report — already crosses.** Terminal state persists: the worker uploads the
  `runs/<id>/` tree to the object store and writes a run record, and the UI reads it after the run.
  Not this.

The missing piece is the **live crawl graph**, which today relies on a shared-filesystem assumption
that the worker split breaks.

## Motivation

**The gap, concretely.** A crawl writes `runs/<id>/screenmap.json` *incrementally* — the engine fires
an `on_event` callback after every discovery, which rewrites the file (`bajutsu/cli/commands/crawl.py`).
The browser polls `GET /runs/<id>/screenmap.json` on each SSE log line and redraws
(`bajutsu/templates/serve.js`, `loadGraph`). In **local** mode this works because the process that
writes the file and the server that serves it share one filesystem.

In **server** mode it breaks. The worker writes `screenmap.json` to *its own* disk, but artifacts are
uploaded to the object store **only at job end** — `_upload_runs` runs after `run_job` returns, i.e.
after the crawl subprocess has already exited (`bajutsu/serve/server/worker_job.py`). While the crawl
is in progress, the control plane's `ObjectStorageArtifactStore.get` finds no key and returns **404**,
so the browser's poll fails and the graph stays empty **until the crawl finishes**.

**Root cause.** Logs were given a transport that crosses the split (the LogBus); in-progress
artifacts were not. The artifact store is *terminal-upload, read-per-request* — exactly right for a
report, but wrong for a file that is meant to be **read while it is still changing**. The crawl graph
is the first "live artifact", and nothing carries it across the split.

**Why it matters.** A crawl is long-running, and the growing graph is its primary UX — you watch
coverage build, screens appear, and the frontier shrink. On the hosted topology (the one teams
actually deploy, [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md))
that UX silently degrades to "watch the log scroll, then see the whole graph at the end". The feature
looks finished in local dogfooding and quietly regresses in production.

## Detailed design

### Scope: live artifacts

A **live artifact** is a file a run updates *during* execution and the UI reads *mid-run* — as opposed
to terminal artifacts (the report, evidence) read only after the run. Today there is exactly one:
`screenmap.json`. The design names a small, explicit **allowlist of live-artifact paths** so the
mechanism is general (a future live progress file reuses it) without turning every artifact into a
streamed one.

### Primary design — incremental upload of live artifacts

The worker already has the right boundary: the `on_event` callback that rewrites `screenmap.json` on
the worker's disk after each discovery. Hook the **same** boundary to also `put` the artifact to the
object store, so the control plane's `ObjectStorageArtifactStore.get` finds it mid-run. The browser
keeps polling the **same** `/runs/<id>/...` route; only *where the bytes come from* changes, so:

- **No UI change and no GET-route change.** Local mode is unaffected (it still serves the live file
  from disk); server mode now finds the same file in the object store. One code path, both modes.
- **Throttled, not per-event.** A chatty crawl fires many events; coalesce uploads to at most once per
  short interval (≈1–2 s, matching the browser's poll cadence) **plus** a final authoritative write at
  job end. This bounds object-store PUTs with no perceptible UX loss — the graph already updates about
  once per second.
- **Consistency is not a worry.** S3 / R2 overwrites are strongly read-after-write consistent, so a
  poll issued after an upload returns the fresh snapshot; there is no stale-read window to design
  around.
- **Terminal upload still wins.** The end-of-job `_upload_runs` remains the authority for the complete
  `runs/<id>/` tree; incremental upload only makes the *live* artifact visible earlier.

### The contract — machine-checkable invariants

Verified on the Linux gate with the in-memory / fake object store (no Mac, no Simulator):

1. **Mid-run visibility (the regression).** Given a fake worker that emits N screen-map snapshots
   before finishing, the control plane's artifact `get` returns the k-th snapshot **before** the job is
   marked done — the case that returns 404 today.
2. **Terminal authority.** After the job completes, the stored `screenmap.json` equals the last
   on-disk snapshot (the bulk terminal upload is consistent with the incremental writes).
3. **Bounded uploads.** M discoveries over a duration produce at most `ceil(duration / interval) + 1`
   uploads — PUTs scale with wall-clock time, not with event count.
4. **Local-mode parity.** In local mode the live graph still serves from disk, unchanged — no
   regression to the path that already works.
5. **Gate-clean.** No LLM call and no new heavy dependency on the default path; the deterministic core
   is untouched.

### Prime-directive fit

This is **Tier-1 `crawl` + serve plumbing only**. No LLM enters the `run` / CI gate
([DESIGN §2](../../DESIGN.md)). Determinism is preserved because the change alters only *when bytes
become visible*, never *what the crawl explores* — a crawl's traversal stays a pure function of the
element tree. The mechanism is app-agnostic (it moves a file, regardless of the target app).

## Alternatives considered

- **Push graph events on the bus (deltas over SSE) instead of polling a file** — the more general
  "progress channel": the worker emits each new node/edge as a structured event the control plane
  relays over SSE alongside log lines, and the browser updates from the payload with no polling.
  Attractive because it unifies on the transport that already crosses the split (the LogBus) and a
  delta is far smaller than re-fetching the whole map. **Deferred**, not rejected: it is a larger
  change (a new event type, the UI's data path moves from polling to events, and both local and server
  modes must be handled), whereas incremental upload preserves the existing seam and the existing UI.
  A future progress channel can subsume the polling once it earns its keep.
- **Worker → control-plane artifact proxy** (the control plane reads in-progress files from the worker
  over HTTP) — **rejected**: workers are **pull-based**, leasing jobs from Redis; they are not
  addressable servers. Making the worker serve files inverts the lease model and adds network and
  security surface for no gain over uploading.
- **Accept a terminal-only graph in server mode** (do nothing) — **rejected**: the live graph is
  crawl's primary UX, and the hosted topology is the one users deploy. A feature that works only in
  local dogfooding is a silent production regression.
- **Upload on every event (no throttle)** — **rejected**: unbounded PUTs for a chatty crawl, with no
  UX benefit over coalescing at the poll cadence.

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

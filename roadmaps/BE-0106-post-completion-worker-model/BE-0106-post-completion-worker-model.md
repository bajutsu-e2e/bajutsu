**English** · [日本語](BE-0106-post-completion-worker-model-ja.md)

# BE-0106 — Post-completion worker model (eliminate Redis dependency)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0106](BE-0106-post-completion-worker-model.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0106") |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md) |
<!-- /BE-METADATA -->

## Introduction

The hosted `bajutsu serve` architecture
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) currently
relies on **Redis** for three roles: a **job broker** (RQ), a **live-log pub/sub bus** (LogBus),
and a **session store**. This dependency was shaped by two premises that no longer hold:

1. **Crawl runs on remote workers.** It does not. Crawl is not distributed; it runs locally on the
   control plane or the operator's machine. The live crawl graph therefore never crosses the
   control-plane/worker split, and
   [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md)
   has been deferred accordingly.
2. **Test execution streams results live during the run.** It does not need to. Distributed test
   execution collects results **after** the run completes on the worker, then sends the full result
   back to the orchestrator. There is no mid-run artifact or log line that must cross the split in
   real time.

Under these revised premises, all three Redis roles become either unnecessary or replaceable by
simpler alternatives already in the stack. This proposal designs the **post-completion worker
model** that eliminates Redis as an infrastructure dependency.

## Motivation

Redis adds operational complexity to every self-hosted deployment
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)): a
container to run, monitor, and back up; a network surface between the control plane and workers;
and a component that becomes a single point of failure unless promoted to a Sentinel cluster (the
high-availability item in BE-0016). The three roles Redis fills today can each be served by
something simpler, now that live mid-run streaming is not required:

| Redis role today | Why it existed | Why it is no longer necessary |
|---|---|---|
| **Job broker (RQ)** | Dispatch jobs to a pool of workers via `BRPOP` | Workers can poll the control plane over HTTP (or the control plane can push to workers), removing the need for a separate broker process |
| **LogBus (pub/sub)** | Stream each stdout line from worker to browser in real time via SSE | Results are collected post-completion; logs travel as part of the result payload, not line-by-line during execution |
| **Session store** | Persist web sessions across control-plane restarts and replicas | PostgreSQL (already in the stack) can store sessions; no second stateful service needed |

Removing Redis simplifies the deployment topology from five containers (`app`, `postgres`, `redis`,
`minio`, `caddy`) to four, eliminates the pub/sub and broker network surface between control plane
and workers, and removes the high-availability concern (Redis Sentinel) from the operational
checklist entirely.

## Detailed design

The four swap-in seams that let local and server hosting diverge already exist
(`RunExecutor`, `LogBus`, `SessionStore`, and the `Repository` system of record). This design
**replaces the three server-side seam implementations** that reach for Redis with ones backed by
Postgres and HTTP, and leaves the **local implementations untouched** — `LocalExecutor`,
`InMemoryLogBus`, and `InMemorySessionStore` are what a laptop `bajutsu serve` uses, and this change
never loads them differently. Postgres, already in the deployment and already the `Repository`
seam's backing, becomes the single stateful dependency the server backend needs.

### 1. Job dispatch — a Postgres jobs table leased over HTTP

Today `QueueExecutor.dispatch` enqueues `execute_job_spec(job_spec)` onto an RQ `Queue`, and
`bajutsu worker` runs an RQ `Worker` that leases with `BRPOP`. Both are replaced:

- **A `jobs` table** (a new Alembic migration on the existing schema): `id`, `org_id`, `spec`
  (JSONB — the `job_spec` payload the worker already reconstructs from), `status`
  (`queued` → `leased` → `done`/`failed`), `leased_at`, `leased_by`, `result` (JSONB, written on
  completion), `created_at`. The `Repository` seam gains `enqueue_job`, `lease_job`, and
  `complete_job`.
- **`DbQueueExecutor`** (the new server `RunExecutor`) inserts a `queued` row instead of enqueuing
  onto Redis.
- **Two worker HTTP endpoints on the control plane**, operator-token-authenticated (BE-0051):
  `POST /api/worker/lease` atomically leases the oldest `queued` job (Postgres
  `SELECT … FOR UPDATE SKIP LOCKED`, so two workers never take the same job) and returns its spec, or
  `204 No Content` when the queue is empty; `POST /api/worker/result` receives the finished result
  and marks the job `done`.
- **`bajutsu worker` becomes an HTTP poll loop**: lease → (if a job) run `execute_job_spec` → upload
  the run tree → post the result; (if none) wait a short interval and lease again. The wait is a
  control-plane-infrastructure poll, **not** a fixed `sleep` inside a run — the deterministic
  `bajutsu run` the worker spawns is byte-for-byte unchanged, so prime-directive #2 (no `sleep` in
  the run/gate) is untouched. This mirrors the poll `RedisLogBus` already uses.

Workers stay **pull-based and need not be addressable** — the same property `BRPOP` gave, preserved
so a worker behind a home NAT or a tailnet still works. A stale lease (a worker that died mid-run)
is a `leased` row whose `leased_at` has aged past a timeout; requeuing it (reset to `queued`) is the
natural Postgres form of BE-0016's "worker liveness & job re-queue" item, folded in here rather than
left to Redis ack-late.

### 2. Result collection — artifacts to the object store, metadata to the control plane

The split that already exists is kept: **large artifacts go straight to the object store**, and only
**small metadata crosses to the control plane**. After the run, the worker uploads the `runs/<id>/`
tree to object storage exactly as today (video never routes through the control plane), then
`POST`s `/api/worker/result` with the terminal metadata (`run_id`, exit status, `ok`, the manifest
summary). The **control plane** records the finished run into the system of record from that POST —
so, unlike today, **the worker no longer needs `BAJUTSU_DATABASE_URL` or the `db` extra**: recording
moves to the one process that already owns the database. The worker's dependency shrinks to an HTTP
client plus the object store.

### 3. Live logs — a post-completion console log, not a live stream

Server mode drops live line-by-line streaming (confirmed scope): during a run the browser shows a
**running** state; when the job completes it shows the **full log**.

- The worker writes the run's stdout to `runs/<id>/console.log` and uploads it with the rest of the
  run tree — one more artifact, no new transport.
- **`PostCompletionLogBus`** (the new server `LogBus`) keeps the existing `/events` contract — a
  stream that a late subscriber can open and that ends when the job is done — but sources it from the
  jobs table and the object store: while the job is `queued`/`leased` it yields periodic heartbeats
  (so the connection and the "running" state stay live), and once the job is `done` it yields the
  uploaded `console.log` and closes. The browser and the `/events` route are unchanged; only *where
  the lines come from* changes, so the SSE client keeps working.
- **Local mode is unchanged**: `InMemoryLogBus` still streams live in-process over the same `/events`
  endpoint. The two modes keep one browser code path.

### 4. Sessions — a Postgres-backed store

`RedisSessionStore` is replaced by **`SqlSessionStore`** over the existing engine: a `sessions` table
(`id`, `identity`, `expires_at`) added in the same migration. `issue` inserts a row with
`expires_at = now + ttl`; `valid` treats a missing or expired row as invalid; `identity` returns the
bound GitHub login (or None for a shared-token login). Expiry is enforced on read, with a periodic
delete of expired rows for hygiene. Sessions survive a restart and span replicas exactly as the Redis
store did, with no second stateful service.

### 5. Wiring and the migration path

`_build_server_state` stops importing `redis`/`rq`: `executor` becomes `DbQueueExecutor`, `logbus`
becomes `PostCompletionLogBus`, `sessions` becomes `SqlSessionStore`, all over the `Repository`
engine. Because jobs and sessions now live in Postgres, **`BAJUTSU_DATABASE_URL` becomes required for
`--backend=server`** (it was optional before); Postgres was already in the recommended stack, so this
formalizes what a real deployment already ran. The `worker` extra drops RQ + Redis. `deploy/self-host`
loses the `redis` container (five services → four), and the self-hosting docs and the BE-0015/BE-0016
architecture sections are updated to the Redis-free topology.

Everything above is verified on the **Linux gate with SQLite and fakes** — the jobs-table lease, the
HTTP lease/result handlers, `PostCompletionLogBus`, and `SqlSessionStore` all have machine-checkable
unit tests that need no Redis, no Postgres, no Mac. No LLM enters any path (prime-directive #1), the
deterministic run is unchanged (#2), and nothing here is app-specific (#3).

### Work breakdown (MECE)

1. **Session store → Postgres.** `sessions` table + migration, `SqlSessionStore`, wiring; independent
   of the dispatch change, so it lands first and removes one Redis role on its own.
2. **Job dispatch + result collection + post-completion log.** The coupled core: the `jobs` table +
   migration, `Repository.enqueue_job`/`lease_job`/`complete_job`, `DbQueueExecutor`, the
   `/api/worker/lease` + `/api/worker/result` endpoints, the `bajutsu worker` HTTP loop, the worker's
   `console.log` upload, and `PostCompletionLogBus`. This is the slice that lets Redis leave the
   worker↔control-plane path entirely.
3. **Deployment & docs cleanup.** Drop `redis` from `deploy/self-host`, drop RQ/Redis from the
   `worker` extra, require `BAJUTSU_DATABASE_URL` for the server backend, update `docs/self-hosting.md`
   (+ `docs/ja/`), and rewrite the BE-0015/BE-0016 Redis sections (removing the "under revision"
   notices) to the Redis-free architecture.

## Alternatives considered

- **Keep Redis but remove the LogBus only.** This removes the pub/sub role but keeps Redis for the
  job broker and sessions. Simpler change, but misses the opportunity to eliminate the Redis
  dependency entirely when the broker and session store can also be replaced.
- **Replace Redis with a lighter message queue (e.g. SQLite-backed queue, Postgres LISTEN/NOTIFY).**
  Still requires a broker abstraction; the question is whether the broker is needed at all when
  HTTP polling or push can serve the same purpose with fewer moving parts.
- **Keep live log streaming but over HTTP long-poll instead of Redis pub/sub.** Preserves the
  real-time UX but still requires the worker to push logs during execution, which contradicts the
  post-completion model.

## Progress

> Kept current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design*; the log records what changed and when (oldest first), linking the PRs.

- [x] 1 — Session store → Postgres (`sessions` table + migration, `SqlSessionStore`, wiring).
- [x] 2 — Job dispatch + result collection + post-completion log (`jobs` table + migration,
  `Repository` lease methods, `DbQueueExecutor`, `/api/worker/lease` + `/api/worker/result`, the
  `bajutsu worker` HTTP loop, the `console.log` upload, `PostCompletionLogBus`).
- [x] 3 — Deployment & docs cleanup (drop `redis` from `deploy/self-host` and the `worker` extra,
  update `docs/self-hosting.md` + `docs/ja/`, rewrite the BE-0015/BE-0016 Redis sections).

Log:

- 2026-07-02 — design fleshed out from the accepted premise (post-completion collection, no live
  streaming in server mode); Detailed design and this breakdown scoped.
- 2026-07-02 — slices 1+2 shipped: `SqlSessionStore`, `DbQueueExecutor`, `PostCompletionLogBus`,
  worker HTTP endpoints, `bajutsu worker` HTTP loop (#445).
- 2026-07-02 — slice 3 shipped: Redis removed from deploy, deps, docs, roadmap.
- 2026-07-13 — correction: slice 3's roadmap rewrite covered BE-0016 but missed BE-0015, whose
  Deployment plan / Migration / sessions / Alternatives still described the Redis/RQ plan. BE-0015's
  Redis/RQ sections were reconciled to this model later, in the BE-0015 status PR (#986).

## References

- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
  — the public-cloud architecture whose Redis dependency this item revisits. Affected sections: the
  stack table (Redis 7 / RQ rows), the Job queue section, the worker description, Phase 1/2
  deployment, the LogBus seam in Migration, and the Alternatives section on Redis vs
  RabbitMQ/NATS/SQS.
- [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  — the self-hosted architecture whose Docker Compose stack includes a `redis` container. Affected
  sections: the Tier B stack description, the job distribution paragraph, the remaining-work items
  (capability-routed queues, worker liveness, control-plane scale-out, high availability), and the
  architecture diagram.
- [BE-0070 — Live in-progress run artifacts across the worker split](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md)
  — deferred because crawl is not distributed and test execution collects results post-completion;
  this item formalizes the same premise change into the hosting architecture.
- Source touchpoints: `bajutsu/serve/server/executor.py` (`QueueExecutor`),
  `bajutsu/serve/server/logbus.py` (`RedisLogBus`),
  `bajutsu/serve/server/sessions.py` (`RedisSessionStore`),
  `bajutsu/cli/commands/worker.py` (the `bajutsu worker` CLI),
  `bajutsu/serve/server/worker_job.py` (job execution and artifact upload).

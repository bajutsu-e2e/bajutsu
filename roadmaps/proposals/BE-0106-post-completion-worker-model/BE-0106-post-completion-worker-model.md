**English** · [日本語](BE-0106-post-completion-worker-model-ja.md)

# BE-0106 — Post-completion worker model (eliminate Redis dependency)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0106](BE-0106-post-completion-worker-model.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Topic | Hosting the web UI (cloud / self-hosted) |
| Related | [BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0070](../../deferred/BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md) |
<!-- /BE-METADATA -->

## Introduction

The hosted `bajutsu serve` architecture
([BE-0015](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
[BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) currently
relies on **Redis** for three roles: a **job broker** (RQ), a **live-log pub/sub bus** (LogBus),
and a **session store**. This dependency was shaped by two premises that no longer hold:

1. **Crawl runs on remote workers.** It does not. Crawl is not distributed; it runs locally on the
   control plane or the operator's machine. The live crawl graph therefore never crosses the
   control-plane/worker split, and
   [BE-0070](../../deferred/BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md)
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
([BE-0016](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)): a
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

TBD. The design needs to address:

1. **Job dispatch without a broker.** How the control plane distributes jobs to workers without
   Redis/RQ. Candidates include HTTP-based polling (workers pull from a `/api/jobs/lease` endpoint
   backed by Postgres) and HTTP-based push (control plane calls the worker's API directly, though
   this inverts the current pull model and requires workers to be addressable).
2. **Result collection.** How a worker returns the full result (exit code, manifest summary, logs,
   artifact references) to the control plane after a run completes. The worker may upload artifacts
   to the object store (MinIO/R2) and then POST the result metadata to the control plane, or return
   everything in a single payload.
3. **Session migration.** Moving from `RedisSessionStore` to a Postgres-backed session store (or a
   signed-cookie approach that needs no server-side state).
4. **Browser UX during a run.** Without live log streaming, the browser sees a "running" state and
   then the final result. The UX tradeoff (polling for completion vs. waiting) and whether a
   lightweight progress signal (e.g. heartbeat or phase indicator, without full log streaming) is
   worth the complexity.
5. **Migration path.** How to transition the existing `QueueExecutor`, `RedisLogBus`, and
   `RedisSessionStore` seams to the new implementations without breaking the local backend (which
   uses in-memory/in-process implementations unaffected by this change).

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

- [ ] TBD — enumerate the work breakdown (MECE) here once the design is scoped.

## References

- [BE-0015 — Public hosting of the web UI](../../in-progress/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
  — the public-cloud architecture whose Redis dependency this item revisits. Affected sections: the
  stack table (Redis 7 / RQ rows), the Job queue section, the worker description, Phase 1/2
  deployment, the LogBus seam in Migration, and the Alternatives section on Redis vs
  RabbitMQ/NATS/SQS.
- [BE-0016 — Self-hosting of the web UI](../../in-progress/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  — the self-hosted architecture whose Docker Compose stack includes a `redis` container. Affected
  sections: the Tier B stack description, the job distribution paragraph, the remaining-work items
  (capability-routed queues, worker liveness, control-plane scale-out, high availability), and the
  architecture diagram.
- [BE-0070 — Live in-progress run artifacts across the worker split](../../deferred/BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md)
  — deferred because crawl is not distributed and test execution collects results post-completion;
  this item formalizes the same premise change into the hosting architecture.
- Source touchpoints: `bajutsu/serve/server/executor.py` (`QueueExecutor`),
  `bajutsu/serve/server/logbus.py` (`RedisLogBus`),
  `bajutsu/serve/server/sessions.py` (`RedisSessionStore`),
  `bajutsu/cli/commands/worker.py` (the `bajutsu worker` CLI),
  `bajutsu/serve/server/worker_job.py` (job execution and artifact upload).

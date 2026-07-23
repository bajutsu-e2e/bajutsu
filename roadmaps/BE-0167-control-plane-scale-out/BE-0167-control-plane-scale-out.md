**English** · [日本語](BE-0167-control-plane-scale-out-ja.md)

# BE-0167 — Control-plane scale-out behind a load balancer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0167](BE-0167-control-plane-scale-out.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0167") |
| Topic | Hosting the web UI |
| Related | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) |
| Origin | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

The self-hosted control plane
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) runs today as a **single**
`bajutsu serve --asgi --backend=server` app container in the `deploy/self-host/` compose stack. It is
cheap HTTP work and can be scaled horizontally. This proposal specifies running **N app replicas
behind a load balancer** so the control plane survives a replica failure and handles more concurrent
users, without changing the pull-based job distribution. It is carved out of BE-0016's "growing one
node into a pool" work.

## Motivation

Even after the Mac pool grows, the control plane stays one container. That single app process is a
capacity ceiling (every browser session, worker lease call, and run-history read goes through it) and
a liveness risk (if it restarts, the whole UI is briefly unavailable). Because the control plane is
stateless HTTP over a shared database and object store, replicating it is the natural way to remove
both limits. The work is to make that replication correct — no sticky sessions, no lost long-lived
connections — and to document the load-balancer configuration the topology needs.

## Detailed design

The design rests on two facts already true of the stack: sessions live in **Postgres**
(BE-0106 folded the session store into the database), and the worker model is **post-completion**, so
no mid-run log stream has to be fanned out across replicas. The work breakdown:

1. **Stateless replicas, no sticky sessions.** Any replica must serve any request. Auth is a signed
   cookie and the session lives in Postgres, so a request authenticated on one replica is honored on
   another with no sticky routing. This is verified by hand: log in on one replica and read run
   history on another.
2. **Load-balancer policy.** Front the replicas with Caddy or HAProxy using **least-connections**
   rather than round-robin, because server-sent events (SSE) connections are long-lived and
   round-robin would skew load toward whichever replica accumulated open streams. TLS terminates at
   the load balancer (or the existing `caddy` profile).
3. **Worker path across replicas.** Because the worker model is post-completion, a `bajutsu worker`
   can `POST /api/worker/lease` from any replica and post the whole result to `/api/worker/result` on
   any replica — there is no per-run affinity between a worker and the replica that served its lease.
   The browser reads finished runs from the shared database and object store, so it needs no affinity
   either.
4. **Compose wiring for N replicas.** Extend `deploy/self-host/` to run N app replicas behind the
   load balancer (a scale factor plus the LB config), with the stateful services (Postgres, MinIO)
   unchanged and shared. Document the operator steps.

**Verification.** This tier is deployment and operations work that the Linux-only `make check` gate
cannot exercise (no Docker, no multi-container topology). It is verified by hand on a deployment:
bring up two app replicas, confirm a login on one replica is honored by a request served by the
other, and confirm a run leased from one replica completes and its result is readable through the
other. Any small statelessness helper that lands in `bajutsu/serve/` (e.g. asserting no in-process
session state) keeps unit coverage, but the topology itself has no gate contract.

## Alternatives considered

- **Sticky sessions (session affinity at the load balancer)** — rejected: it reintroduces per-replica
  state coupling, unbalances load when a replica restarts, and is unnecessary once sessions live in
  Postgres. Stateless replicas are simpler and more robust.
- **Round-robin load balancing** — rejected in favor of least-connections: SSE streams are
  long-lived, so round-robin steadily concentrates open connections on some replicas; least-connections
  tracks actual load.
- **Keeping a single replica and scaling only vertically** — acceptable as the starting point but
  rejected as the end state: one app container remains a liveness single point of failure and a
  capacity ceiling, which horizontal replicas remove.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Stateless replicas verified — any replica serves any request, no sticky sessions.
- [ ] Load-balancer policy — least-connections over round-robin, TLS at the LB.
- [ ] Worker lease/result path confirmed to work across replicas.
- [ ] Compose wiring for N app replicas behind the load balancer, documented.

## References

`deploy/self-host/` (the single-node compose stack this extends), `bajutsu/serve/` (the ASGI control
plane), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (the self-hosting
umbrella this is carved from),
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) (the
post-completion worker model and Postgres session store that make stateless replicas possible).

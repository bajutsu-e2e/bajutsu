**English** · [日本語](BE-0167-control-plane-scale-out-ja.md)

# BE-0167 — Control-plane scale-out behind a load balancer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0167](BE-0167-control-plane-scale-out.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Rejected** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0167") |
| Topic | Hosting the web UI |
| Related | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) |
| Superseded by | [BE-0518](../BE-0518-control-plane-replica-topology/BE-0518-control-plane-replica-topology.md) |
| Origin | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

> **Rejected — the premise of this item no longer holds, and [BE-0518](../BE-0518-control-plane-replica-topology/BE-0518-control-plane-replica-topology.md) replaces it.**
> This item assumed that a worker posts its result once the job ends. Under that premise, no log
> stream crosses replicas. [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md) changed that behavior. A worker now posts log lines while the job
> runs. Replicas thus need a shared Redis bus for live logs, which this design leaves out. Other
> items have also delivered most of the application-side work this item planned. The remaining work
> is a different, smaller scope, and BE-0518 states that scope from the current code. `Rejected`
> fits better than `Deferred`, because no later date makes this design correct again.

The self-hosted control plane is the `bajutsu serve` app that queues jobs and serves the web UI.
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) covers self-hosting it. The `deploy/self-host/` Docker Compose stack runs it as one app
container. Its work is cheap HTTP, so it can scale horizontally.

This proposal runs **N app replicas behind a load balancer**. The control plane then survives the
loss of one replica. It also serves more concurrent users. The pull-based job distribution stays
unchanged. BE-0016 named this work as part of growing one node into a pool.

## Motivation

Even after the Mac pool grows, the control plane stays one container. That one process is a
capacity ceiling. Every browser request, worker lease call, and run-history read goes through it.
The process is also a liveness risk. When it restarts, the whole UI goes away for a moment.

The control plane is stateless HTTP over a shared database and object store. Running replicas
removes both limits. The work is to make the replicas correct, with no sticky routing and no lost
long-lived connections. The work also documents the load-balancer configuration.

## Detailed design

The design rests on two facts about the stack. Sign-in state lives in **Postgres**, because
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) moved the store into the database. The worker model posts results after completion. No
mid-run log stream thus has to reach other replicas. The work breakdown follows.

1. **Stateless replicas, no sticky sessions.** Any replica must serve any request. A signed Cookie
   names a row in Postgres. A request signed in on one replica thus works on another. A hands-on
   check covers this step: sign in on one replica, then read run history on another.
2. **Load-balancer policy.** Front the replicas with Caddy or HAProxy.
   Use **least-connections** rather than round-robin. Event streams stay open for a long time. Round-robin would pile load
   onto the replica holding the most open streams. Transport Layer Security (TLS) ends at the load
   balancer or at the existing `caddy` profile.
3. **Worker path across replicas.** A `bajutsu worker` can `POST /api/worker/lease` on any replica.
   The worker can post the whole result to `/api/worker/result` on any replica. No affinity ties a
   worker to the replica that served its lease. The browser reads finished runs from the shared
   stores, so the browser needs no affinity either.
4. **Compose wiring for N replicas.** Give `deploy/self-host/` a scale factor and a
   load-balancer config. Postgres and MinIO stay shared and unchanged. Document the operator steps.

**Verification.** This tier is deployment and operations work. The `make check` gate runs no Docker
and no multi-container topology, so it cannot exercise this tier. We check it by hand on a
deployment:

- Bring up two app replicas.
- Sign in through one replica, and confirm the other accepts the next request.
- Lease a run from one replica, and read its result through the other.

A small statelessness helper in `bajutsu/serve/` keeps unit coverage. The topology itself has no
gate contract.

## Alternatives considered

- **Sticky sessions at the load balancer.** We decline affinity. Affinity brings back per-replica
  coupling. It also unbalances load when a replica restarts. Sign-in state already lives in
  Postgres, so affinity buys nothing.
- **Round-robin load balancing.** We pick least-connections instead. Event streams stay open for a
  long time. Round-robin steadily piles open connections onto some replicas. Least-connections
  tracks the actual load.
- **One replica, scaled vertically.** This option works as a starting point but not as the end
  state. One app container stays a capacity ceiling and a single point of liveness risk.
  Horizontal replicas remove both.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Stateless replicas verified, with any replica serving any request.
- [ ] Load-balancer policy, with least-connections and TLS at the load balancer.
- [ ] Worker lease and result path confirmed to work across replicas.
- [ ] Compose wiring for N app replicas behind the load balancer, documented.

## References

- `deploy/self-host/`: the single-node compose stack this item extends.
- `bajutsu/serve/`: the control plane.
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md): the self-hosting umbrella this item comes from.
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md): the worker model and the database store for sign-in state.
- [BE-0518](../BE-0518-control-plane-replica-topology/BE-0518-control-plane-replica-topology.md): the item that replaces this one.

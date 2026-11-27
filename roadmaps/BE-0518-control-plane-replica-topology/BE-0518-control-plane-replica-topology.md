**English** · [日本語](BE-0518-control-plane-replica-topology-ja.md)

# BE-0518 — Control-plane replicas behind a load balancer

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0518](BE-0518-control-plane-replica-topology.md) |
| Author | [@akira-matsuda](https://github.com/akira-matsuda) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0518") |
| Topic | Hosting the web UI |
| Related | [BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md) |
| Origin | [BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md) |
<!-- /BE-METADATA -->

## Introduction

The control plane is the `bajutsu serve` app that queues jobs and serves the web UI.
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) covers self-hosting the
control plane. The `deploy/self-host/` Docker Compose stack runs the control plane as one app
container. Its work is cheap HTTP over a shared database and object store. The app can thus scale
horizontally.

This proposal runs **N app replicas behind a load balancer**. The control plane then survives the
loss of one replica. It also serves more concurrent users. The pull-based job distribution stays
unchanged. This item replaces [BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md), which proposed the same goal on an older premise.

Other items have already delivered most of the application-side work. The remaining work is
the deployment topology and a hands-on check of that topology. *Detailed design* keeps the two apart.

## Motivation

Even after the Mac pool grows, the control plane stays one container. That one process is a
capacity ceiling. Every browser request, worker lease call, and run-history read goes through it.
The process is also a liveness risk. When it restarts, the whole UI goes away for a moment.

Running replicas removes both limits. The work is to make the replicas correct. No request may
depend on sticky routing, and no live log line may get lost between replicas. The work also
documents the load-balancer configuration that the topology needs.

One observable difference shows that the change arrived. An operator stops one app replica of a
two-replica stack. The UI stays reachable through the other replica. A viewer who watches a run live
keeps getting its log lines. The worker may post those lines to either replica.

## Detailed design

### What the application already provides

Implemented items have already removed the per-process state that tied a request to one replica.
The following table lists each concern, how the code handles it today, and the item behind it.

| Concern | Current behavior | Item |
|---|---|---|
| Sign-in state | A signed Cookie names a row in the database | [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) |
| Job queue | Every job waits in the jobs table for a worker | [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md) |
| Worker lease and result | Any replica handles either call by the job's row | [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md) |
| Uploaded bundles | A replica fetches a missing bundle from the object store | [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md) |
| Live log lines | A Redis bus carries lines between replicas | [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md) |
| Database | A bucket-backed deployment must name a shared database | [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md) |

BE-0167 rested on a premise that no longer holds. In BE-0167, a worker
posted its result once the job ended, so no log stream crossed replicas. A worker now posts log lines
while the job runs. Each line reaches the browser over a long-lived event stream. With the default
in-memory bus, a browser misses the lines a worker posted to another replica.

The Redis bus closes that gap. Each replica reads `BAJUTSU_REDIS_URL` to find the shared Redis.
[`docs/self-hosting.md`](../../docs/self-hosting.md) already documents that setting for a multi-replica deployment. The compose stack has no Redis
service yet.

Some state stays per replica by design, and none of it needs affinity:

- The cap on live lines counts per process, so it bounds each replica's memory.
- The cache of extracted bundles is local, and a replica without a bundle fetches it.

### Work breakdown

1. **Stateless replicas, checked by hand.** The code already lets any replica serve any request.
   The remaining work is a check on a real topology. Sign in through one replica. Then read run
   history through the other.
2. **Load-balancer policy.** Front the replicas with Caddy or HAProxy using
   **least-connections** rather than round-robin. Event streams stay open for a long time.
   Round-robin would pile load onto the replica holding the most open streams. Transport Layer
   Security (TLS) ends at the load balancer or at the existing `caddy` profile. Today the stack's
   `Caddyfile` proxies to the one `bajutsu:8765` upstream.
3. **Live logs across replicas.** Add a Redis service to the compose stack. Set
   `BAJUTSU_REDIS_URL` on every app replica. Confirm that a viewer on one replica sees the lines a
   worker posts to the other.
4. **Compose wiring for N replicas.** Give `deploy/self-host/` a scale factor and a
   load-balancer config. Today the app service publishes port 8765 on the machine. The replicas
   instead expose that port to the load balancer alone. Postgres and MinIO stay shared and
   unchanged. Document the operator steps in `deploy/self-host/README.md` and the self-hosting
   guide.

**Verification.** This tier is deployment and operations work. The `make check` gate runs no Docker
and no multi-container topology, so it cannot exercise this tier. We check it by hand on a
deployment:

- Bring up two app replicas behind the load balancer.
- Sign in through one replica, and confirm the other accepts the next request.
- Lease a run from one replica, and read its result through the other.
- Watch a run on one replica while the worker posts to the other, and confirm the lines arrive.
- Stop one replica, and confirm the UI stays reachable.

A small statelessness helper in `bajutsu/serve/` keeps unit coverage. The topology itself has no
gate contract.

## Alternatives considered

- **Sticky sessions at the load balancer.** We decline affinity. Affinity brings back per-replica
  coupling. It also unbalances load when a replica restarts. Sign-in state already lives in the
  database, so affinity buys nothing.
- **Round-robin load balancing.** We pick least-connections instead. Event streams stay open for a
  long time. Round-robin steadily piles open connections onto some replicas. Least-connections
  tracks the actual load.
- **Live logs without Redis.** We decline this option. A browser would miss every line a worker
  posts to a different replica. The uploaded `console.log` still arrives at the end. The loss is
  the live view, not the log.
- **One replica, scaled vertically.** This option works as a starting point but not as the end
  state. One app container stays a capacity ceiling and a single point of liveness risk.
  Horizontal replicas remove both.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Stateless replicas checked by hand, with any replica serving any request.
- [ ] Load-balancer policy, with least-connections and TLS at the load balancer.
- [ ] Live logs across replicas, with Redis in the stack and `BAJUTSU_REDIS_URL` set.
- [ ] Compose wiring for N app replicas behind the load balancer, documented.

## References

- `deploy/self-host/`: the single-node compose stack this item extends.
- `bajutsu/serve/`: the control plane.
- [`docs/self-hosting.md`](../../docs/self-hosting.md): the self-hosting guide, which documents `BAJUTSU_REDIS_URL`.
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md): the self-hosting umbrella this item comes from.
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md): the worker model and the database store for sign-in state.
- [BE-0449](../BE-0449-server-runs-no-jobs/BE-0449-server-runs-no-jobs.md): the jobs table every replica reads, and the live log bus.
- [BE-0243](../BE-0243-upload-bundle-durable-storage/BE-0243-upload-bundle-durable-storage.md): uploaded bundles that another replica can fetch.
- [BE-0168](../BE-0168-self-host-high-availability/BE-0168-self-host-high-availability.md): the sibling item that removes the remaining single points of outage.

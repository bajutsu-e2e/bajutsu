**English** · [日本語](BE-0168-self-host-high-availability-ja.md)

# BE-0168 — Self-hosted high availability and single-point-of-failure hardening

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0168](BE-0168-self-host-high-availability.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0168") |
| Topic | Hosting the web UI |
| Related | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) |
| Origin | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

The self-hosted server backend
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) runs today on a single
Linux node. Once Redis was removed
([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)),
Postgres became the one stateful single point of failure (SPOF). Postgres holds the job queue (the
`jobs` table), the sessions, the quota state, and the run metadata all at once.

This proposal specifies the high-availability topology that removes that SPOF: a Postgres primary
with a replica, a redundant load balancer, and a backed-up object store. It also states when a
single-node deploy is an acceptable, explicitly flagged trade-off. The work is carved out of
BE-0016's "growing one node into a pool" effort.

## Motivation

A single node is fine for a small self-host. But if Postgres, the load balancer, or the MinIO node
fails, the whole service goes down. Postgres holds the queue, the sessions, the quotas, and the run
history together, so losing it loses all four at once.

A team that depends on the web UI for its release gate cannot accept an unbounded outage from one
disk or one host. An operator needs a documented, tested, hardened topology to remove each SPOF
deliberately. The operator also needs a clear statement that a single node is acceptable **if** the
operator has explicitly accepted it as a SPOF with a tested backup.

## Detailed design

Every part of this tier is deployment and operations work. None of it has a Python surface, so the
`make check` gate does not exercise any of it. The value is the documented, tested topology. The
work breakdown follows, one SPOF at a time:

1. **Postgres high availability.** Run a primary plus a replica with automated failover (for example
   Patroni). At minimum, keep a tested point-in-time backup and a rehearsed restore. Postgres holds
   the queue, sessions, quotas, and run metadata, so this is the highest-value SPOF to remove first.
2. **Object store redundancy.** Run MinIO with redundancy (a distributed or erasure-coded
   deployment). At minimum, keep a backed-up bucket, so a single disk failure does not lose
   artifacts. Video evidence is heavy, so the backup target must be sized for it.
3. **Redundant load balancer.** Add a second load balancer with a floating address, using keepalived
   with Virtual Router Redundancy Protocol (VRRP) or DNS-based failover. The entry point is then no
   longer a SPOF. This composes with the N-replica control-plane scale-out (the sibling
   *control-plane scale-out* item) but is separable: it hardens the entry point even for a single
   app replica.
4. **The single-node trade-off, stated.** Document that a single node is an acceptable self-host
   **only** when the operator has explicitly flagged it as a SPOF and has a tested backup and
   restore. This keeps the honest default (many self-hosts are single-node) without pretending the
   node is fault-tolerant.

```
[DNS] → [HAProxy ×2 (VRRP)] → [FastAPI ×N] ─┬─ Postgres (primary+replica)  ← queue / sessions / quotas
                                            └─ MinIO (redundant / backed-up)
```

**Verification.** The operator verifies the deployment by hand, because the gate provides no
coverage. The failover drills are the real test:

- Stop the Postgres primary and confirm the replica takes over with the queue and sessions intact.
- Stop a load balancer and confirm the floating address moves.
- Restore the object store from backup and confirm the artifacts are readable.

The deliverable includes the runbook for these drills.

## Alternatives considered

- **Accept a single node as permanent.** Rejected as the end state, but kept as a flagged option. A
  single node is fine for a hobby deploy, but a team release gate cannot tolerate an unbounded
  single-disk outage. The compromise is to *document* the single-node trade-off rather than pretend
  it is HA.
- **Managed cloud Postgres or object store instead of self-hosted HA.** Out of scope here. That is
  the fully managed public cloud, which belongs to
  [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), not to this
  self-hosted item.
- **Application-level queue replication instead of Postgres HA.** Rejected. It would reinvent
  durable replication that Postgres already provides well. Leaning on Postgres HA keeps the queue,
  sessions, and metadata consistent through one mechanism rather than several.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Postgres HA — primary + replica with failover, or a tested point-in-time backup and restore.
- [ ] Object store redundancy — MinIO redundancy or a backed-up, appropriately-sized bucket.
- [ ] Redundant load balancer — floating address via keepalived/VRRP or DNS failover.
- [ ] Documented single-node SPOF trade-off with a tested backup/restore runbook.

## References

`deploy/self-host/` (the single-node stack this hardens),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (the self-hosting umbrella
this is carved from),
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) (removing
Redis made Postgres the lone stateful SPOF this addresses),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the fully managed
cloud
counterpart).

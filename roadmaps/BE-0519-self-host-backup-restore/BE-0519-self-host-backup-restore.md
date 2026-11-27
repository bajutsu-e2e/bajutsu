**English** · [日本語](BE-0519-self-host-backup-restore-ja.md)

# BE-0519 — Self-hosted backup, restore, and the single-node risk statement

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0519](BE-0519-self-host-backup-restore.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0519") |
| Topic | Hosting the web UI |
| Related | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md), [BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md), [BE-0168](../BE-0168-self-host-high-availability/BE-0168-self-host-high-availability.md) |
| Origin | [BE-0168](../BE-0168-self-host-high-availability/BE-0168-self-host-high-availability.md) |
<!-- /BE-METADATA -->

## Introduction

The self-hosted server backend runs on one Linux node. The `deploy/self-host/` Docker Compose stack
runs one copy of each service. Three of them matter here: Postgres, the MinIO object store, and the
Caddy reverse proxy. None of the three has a spare, so the loss of any one stops the service.
Postgres matters most. It holds the job queue (the `jobs` table), the sessions, the quota state, and
the run metadata.

This proposal gives the single node a tested way back from a lost disk or a lost machine. It adds
three pieces:

- A Postgres backup with a rehearsed restore.
- A backup of the artifact bucket.
- A plain statement of which components have no spare, and what each loss costs.

This item replaces BE-0168, which specified a fully redundant topology. That design added a
Postgres replica, a distributed MinIO, and two load balancers. We rejected BE-0168 and kept its
backups and its statement here. *Alternatives considered* gives the reasons.

The item comes out of the self-hosting umbrella,
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md). Then
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) removed
Redis. That removal left Postgres as the one stateful store.

## Motivation

The stack today ships with no backup at all. The `deploy/self-host/` README gives no command to dump
the database or copy the bucket. The self-hosting guide gives none either, and neither page gives a
restore. One broken disk under the `pgdata` volume thus loses the queue, the sessions, and the quotas
together. It loses the whole run history too, with no documented path back.

A team that depends on the web UI for its release gate needs that path more than zero downtime. A
restore bounds the loss to the work since the last backup. An outage of minutes to hours suits most
self-hosted teams. An unbounded loss of the run history does not. A backup also adds no new service
to the stack, while a replica or a second load balancer does.

An operator can check the outcome. The operator follows the runbook on a fresh machine. The
restored stack then shows the run history and artifacts from the last backup.

## Detailed design

Every part of this item is deployment and operations work. None of the parts has a Python surface,
so the `make check` gate does not exercise them. The work breaks down into three units:

1. **Postgres backup and restore.** A scheduled `pg_dump` runs in the Postgres container. A second
   step copies the dump off the node. The runbook gives the dump command, a retention rule, and the
   restore into a fresh `pgdata` volume. The runbook also states the recovery point. A restore keeps
   nothing written after the last dump. The runbook also names a tighter option: archiving the
   Postgres transaction log.
2. **Artifact bucket backup.** A scheduled `mc mirror` copies the MinIO bucket to a target off the
   node. Video evidence is heavy, so the runbook says how to size the target. The restore mirrors the
   copy back into a fresh `miniodata` volume.
3. **The single-node risk statement.** The deploy README lists the three components without a
   spare. Those are Postgres, MinIO, and Caddy. For each one, the statement says what its loss
   stops. It also says how to recover, by a restart or by a restore. The self-hosting guide and its
   translation carry the same statement. A single node suits a self-hosted team that accepts these
   risks and has rehearsed the restore.

**Verification.** The gate gives no coverage here, so the operator checks the deployment by hand.
The restore drill is the real test:

- Restore the latest dump into a fresh stack. Sign in, then confirm the run history and the
  sessions.
- Restore the bucket copy. Open a restored run's report, then confirm its screenshots and video load.
- Note what the restore did to the jobs in the queue at dump time. Record that result in the runbook.

## Alternatives considered

- **Specify the full high-availability topology in this item.** Left out of this item. We have three reasons.
  - Each part is general operations practice, with nothing specific to Bajutsu. The parts are a
    Postgres replica with failover, a distributed MinIO, and two load balancers.
  - Each part adds services that the gate cannot check.
  - Managed Postgres and object storage already give failover to a team that needs it.
    The managed hosting item, BE-0015, runs on that kind of stack.

  A new item can take up the redundant topology once a self-hosted team asks for it. The load
  balancer pair fits best after the multi-replica control plane lands. That control plane is
  [BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md).
- **Leave the single node as it is, undocumented.** Rejected. A single node suits a small team. An
  operator who does not know the risk cannot plan for a lost disk. The statement and the restore
  drill cost little.
- **Replicate the queue in the application instead of in Postgres.** Rejected. That approach would
  rebuild the durable replication that Postgres already offers. One database backup keeps the queue,
  sessions, and metadata consistent through one mechanism.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Postgres backup and restore: a scheduled `pg_dump` copied off the node, with a restore drill.
- [ ] Artifact bucket backup: a scheduled `mc mirror` to an off-node target sized for video.
- [ ] Single-node risk statement in the deploy README and the self-hosting guide.

## References

- `deploy/self-host/`: the single-node stack this item backs up.
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md): the self-hosting
  umbrella.
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md): the
  removal of Redis.
- [BE-0167](../BE-0167-control-plane-scale-out/BE-0167-control-plane-scale-out.md): the control
  plane scale-out.
- [BE-0168](../BE-0168-self-host-high-availability/BE-0168-self-host-high-availability.md): the rejected high-availability item this one replaces.
- [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md): the managed cloud
  counterpart.

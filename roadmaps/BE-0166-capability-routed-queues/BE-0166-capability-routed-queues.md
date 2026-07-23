**English** · [日本語](BE-0166-capability-routed-queues-ja.md)

# BE-0166 — Capability-routed job queues

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0166](BE-0166-capability-routed-queues.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0166") |
| Implementing PR | [#872](https://github.com/bajutsu-e2e/bajutsu/pull/872) |
| Topic | Hosting the web UI |
| Related | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md), [BE-0173](../BE-0173-slim-web-worker-image/BE-0173-slim-web-worker-image.md) |
| Origin | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

The self-hosted server backend
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) distributes test jobs to
a pool of Mac workers, which lease jobs from a **single** queue backed by the Postgres `jobs` table.
That works when every worker can run every job. A **heterogeneous** pool cannot: a job whose target
needs iOS 18 must not land on a worker that only has iOS 17 installed, and an iPad-only scenario must
not land on an iPhone-only worker. This proposal adds **capability-routed queues** so a job is only
ever leased by a worker that can actually run it. It is carved out of BE-0016's "growing one node
into a pool" work.

## Motivation

A real Mac pool is rarely uniform. Machines carry different installed iOS runtimes, some are set up
for iPad and others for iPhone, and over time the fleet drifts as runtimes are added and retired.
With one undifferentiated queue, any worker can lease any job, so a job can be picked up by a worker
that lacks the runtime or device class its target requires. The run then fails — not for a real
defect, but because it was routed to the wrong machine. As the pool grows and diversifies, this
misrouting becomes the common case rather than the exception.

Routing must therefore respect **worker capability**: the queue a job goes on has to match the
device its target declares, and a worker must only pull jobs it can serve. This keeps the
deterministic verdict honest — a failure means the app misbehaved, never that the scheduler sent the
job to an incompatible machine.

## Detailed design

Routing is purely about *which* idle worker picks a job up; it does **not** change the deterministic
`run` — the same scenario, run on a capable worker, produces the same verdict. The work breakdown:

1. **Declare worker capabilities.** Each `bajutsu worker` advertises the capabilities it can serve
   (installed iOS runtimes, device classes such as `ios18` or `ipad`) when it leases. The capability
   set is derived from the worker's Simulator inventory, with an operator override for pinning.
2. **Route jobs to per-capability queues.** The control plane maps a job's target to the capability
   its device requires and enqueues onto the matching logical queue (`q:ios18`, `q:ipad`). The queue
   is still the Postgres `jobs` table; the capability becomes a routing key on the row rather than a
   new store.
3. **Capability-aware leasing.** `POST /api/worker/lease` filters candidate jobs to those whose
   required capability the leasing worker advertises, so a worker only ever leases jobs it can run.
   This composes with the existing lease, heartbeat, and reclaim path
   ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md))
   unchanged: a reclaim returns a job to its own capability queue.
4. **Fallback and unroutable jobs.** A job whose required capability no worker in the pool advertises
   must not hang silently. It stays queued and is surfaced as unroutable (an operator signal), rather
   than being leased by an incompatible worker or dropped.
5. **`backend` as a capability axis (idb vs web).** Beyond iOS runtime and device class, the worker's
   **backend** is itself a capability: a Mac idb worker advertises `backend=idb`, and the Linux web
   worker container ([BE-0173](../BE-0173-slim-web-worker-image/BE-0173-slim-web-worker-image.md))
   advertises `backend=web`. A job's target backend becomes part of its required-capability key, so a
   `web` job is only ever leased by a web worker and an `idb` job only by a Mac worker — the two never
   cross. This is the same declare/route/filter machinery as the axes above, with `backend` as an
   additional dimension; it matters once the pool mixes backends (a homogeneous pool is unaffected).

**Verification.** The routing decision (target to capability to queue) and the lease filter have a
Python surface and are unit-tested with no Simulator: a job requiring `ios18` is never offered to an
iOS-17-only worker, an iPad job is never offered to an iPhone-only worker, and a job with no capable
worker stays queued and unrouted. End to end, routing is verified by hand on a multi-device pool,
confirming that a mixed fleet drains each capability's work onto the right machines.

## Alternatives considered

- **Keep a single queue and fail jobs routed to an incompatible worker** — rejected: it turns a
  scheduling gap into a spurious test failure, which erodes trust in the deterministic verdict. The
  scheduler, not the run, should own compatibility.
- **Let workers lease anything and re-queue on mismatch** — rejected: re-queueing after a worker
  discovers it cannot run the job wastes a lease cycle on the scarce pool and can livelock (a job
  bouncing between incapable workers). Filtering at lease time avoids the wasted attempt.
- **Static per-worker queue assignment by the operator** — rejected as the primary mechanism:
  deriving capability from the worker's actual Simulator inventory keeps routing correct as the fleet
  drifts, without hand-maintained assignments; the operator override remains for pinning.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Workers advertise their capabilities (iOS runtimes, device classes) at lease time.
- [x] Control plane routes a job to the queue matching its target's required capability.
- [x] Capability-aware `lease` filter so a worker only leases jobs it can run.
- [x] Unroutable jobs stay queued and are surfaced, never leased by an incompatible worker.
- [x] `backend` (idb vs web) as a capability axis so web jobs route only to web workers (BE-0173).

Log:

- Implemented capability-routed leasing end to end: a pure capability-token module
  (`bajutsu/serve/capabilities.py`), a `requires:` config axis, a `jobs.capabilities` routing key
  plus a `workers` liveness registry (migration 0007), a capability-filtered `lease_job`, worker
  advertisement (`bajutsu worker --platform/--capabilities` + Simulator-inventory derivation), and a
  `bajutsu_unroutable_jobs` metric surfacing jobs no live worker can serve.

## References

`bajutsu/serve/` (the control plane and `POST /api/worker/lease`),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (the self-hosting umbrella
this is carved from),
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) (the
post-completion HTTP worker model whose lease, heartbeat, and result path this routing composes
with).

**English** · [日本語](BE-0170-weighted-fair-org-dispatch-ja.md)

# BE-0170 — Weighted-fair cross-org job dispatch

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0170](BE-0170-weighted-fair-org-dispatch.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0170") |
| Topic | Hosting the web UI |
| Related | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md), [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md), [BE-0309](../BE-0309-serve-postgres-ci-lane/BE-0309-serve-postgres-ci-lane.md), [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md) |
| Origin | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

The self-hosted server backend
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) distributes test jobs
to a pool of Mac workers over a Postgres `jobs` table. It already bounds concurrency with a
**global** cap, a **per-user** cap, and a **per-org** cap (`max_concurrent_per_org`, shipped in
[#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)) — but every cap is enforced by
**rejecting** an over-cap job with HTTP 429. This proposal turns that rejection into **holding**:
per-org pending queues and a dispatcher that round-robins across organizations so the scarce Mac
pool stays fair under contention. It is carved out of BE-0016's "growing one node into a pool"
work, where it was the one remaining piece with a machine-checkable contract.

## Motivation

The Mac pool is a scarce, non-elastic resource — you cannot spin up more Simulators on demand the
way you would stateless web containers. Under pure first-in, first-out (FIFO) dispatch, a single
organization that submits a burst of jobs takes every free slot and holds it until its runs finish,
while other organizations wait behind the queue even though they are under their own caps. The
per-org cap (BE-0016) limits how many slots one org can *hold at once*, but a job that hits the cap
is simply **rejected** (429) rather than queued — so the caller must retry, and a well-behaved org
that submits work slightly faster than the pool drains still sees spurious failures.

What is missing is **fair scheduling**: when several organizations have pending work, the pool
should be shared between them rather than drained in submission order. That requires the control
plane to *hold* jobs it cannot admit yet and pick the next one by fairness, not arrival time.

A second gap shows once a pipeline submits a whole suite at once. On a database-backed deployment the
same caps bound how many jobs are *submitted and unfinished*, not how many *run*: a job still waiting
in the queue holds a cap slot, while the number of workers already bounds how many run. With the
global cap's default of 4, a fifth scenario submitted together with four others is refused with 429
even when five idle workers are waiting. A pipeline that dispatches a pull request's scenarios as one set
therefore cannot submit more scenarios than the smallest cap, although the pool could run them all
in turn. A companion proposal, which reports a CI-dispatched run set as a GitHub check run, depends
on exactly that use. Holding on that deployment has to separate the two limits, not merely stop
rejecting.

## Detailed design

The design extends the existing admission seam rather than adding a new subsystem. Today the tail
of `_register_and_dispatch` (`bajutsu/serve/operations.py`) calls `try_register`
(`bajutsu/serve/jobs.py`), which atomically counts in-flight jobs against the global, per-user, and
per-org caps under a lock and **rejects with HTTP 429** when any cap is hit. The work breakdown:

1. **Per-org pending queues.** Replace the "reject on cap" tail with **holding**: a job that cannot
   be admitted immediately (its org is at its cap, or the global cap is full) is enqueued onto a
   per-org pending queue instead of being rejected. The queue lives in the same state that
   `try_register` guards, so admission stays atomic.
2. **Round-robin dispatcher.** When a slot frees (a run finishes, or on each new submission), the
   dispatcher walks the organizations that have pending work in **round-robin** order and admits the
   next job from the first org that is under its cap. An org at its cap is skipped this round; the
   round-robin cursor is what prevents any one org from monopolizing the pool.
3. **Priority tiers as weights.** Priority tiers ride the same round-robin — a higher-priority tier
   is visited more often (weighted round-robin) rather than needing a separate scheduler. Within a
   tier, jobs keep their submission order.
4. **Backward compatibility.** With a single tenant (no `orgs:` block, one default org) the
   dispatcher degrades to plain FIFO, so single-tenant deployments are unchanged. The per-org cap
   default of `0` (unlimited) likewise leaves existing behavior untouched until an operator sets it.

### Holding on the database-backed queue

Units 1–4 apply to a local `serve` only. They hold jobs inside the in-process registry, which
suits a local `serve`: a job there runs the moment it registers, so admitting a job and starting it
are the same event. A
database-backed deployment already holds jobs elsewhere. `DbQueueExecutor`
(`bajutsu/serve/server/db_executor.py`, [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md))
inserts a `queued` row into the `jobs` table, and an idle worker leases the oldest row it can serve.
The registry (`bajutsu/serve/state/job_registry.py`) counts a dispatched job against every cap until
the jobs table reports it finished, so on this deployment the caps measure submitted work while each
worker, leasing one job at a time, bounds running work. The units below separate the two limits
there and hold jobs in the jobs table itself, so no second queue lives in memory:

5. **A queue-depth cap at admission.** A new cap bounds the unfinished jobs, queued or leased, that
   admission accepts, globally and per org. On a database-backed deployment it takes over the
   concurrency caps' place in `try_register` and answers 429 when full. Its default is large (500
   unfinished jobs; `0` means unlimited), because its purpose is abuse protection, not scheduling.
   A request whose own size exceeds the cap is refused with 400 naming the cap, since no amount of waiting would admit it.
   The cloud-batch device budget
   ([BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md))
   stays at admission. Cloud-batch dispatch works today only on the in-process path: a batch job
   enqueued to the worker queue fails the provider's package validation on the worker
   (`_run_batch_job` in `bajutsu/serve/jobs.py`), so these units leave the budget where it is, and a
   run-set that hits the budget still dispatches part of its set. The budget moves to lease time
   once the worker path runs cloud-batch jobs.
6. **The concurrency caps at lease time.** `lease_job` in `bajutsu/serve/server/db/sql_repository.py`
   already walks queued rows oldest-first and skips any row the leasing worker cannot serve
   ([BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md)). This unit
   adds one more skip: a row whose org or actor already has as many leased jobs as its cap allows,
   and every row once the leased total reaches the global cap. An over-cap job thus stays `queued`,
   held rather than rejected, and a worker leases it when a slot frees. No new job state and no
   promotion step are needed. The jobs row gains an `actor` column, written at enqueue: the per-user
   cap counts by actor, and today the actor lives only inside the JSON `spec` (`job_spec` in
   `bajutsu/serve/server/worker_job.py`), which the count cannot group by portably across SQLite and
   Postgres.

   The count and the lease must be atomic. Two workers that both read "org A has 3 leased" under a
   cap of 4 would otherwise both lease. On Postgres the lease path takes one transaction-scoped
   advisory lock (`pg_advisory_xact_lock`) on a single fixed key before the walk and commits right
   after the update. Per-org keys would not do: a transaction-scoped lock cannot be released early,
   so a walk that skips an over-cap org would hold two org keys in the order it met them, and two
   workers meeting them in opposite orders deadlock. One key also gives unit 7's walk a consistent
   view of every org's leased count, and leases arrive at most once per idle worker per poll
   interval, so serializing them costs little. SQLite is the `make check` gate's engine ("SQLite on
   the gate and Postgres in production", `sql_repository.py`) and runs a single control-plane
   process, so a process lock around the walk and the update suffices there.
7. **Fairness as lease order.** Units 2 and 3's weighted round-robin becomes the order in which
   `lease_job` walks servable rows, in place of oldest-first alone. The walk takes the org with the
   fewest leased jobs relative to its weight first, and the oldest queued job within that org. Orgs
   tied on that ratio are ordered by their oldest queued job, so the walk is deterministic.
   Unit 3's priority tiers are per-org weights set in the org's configuration, so the jobs row
   needs no priority column. Jobs within one org keep their submission order. The
   state a round-robin cursor would keep is then the leased counts the jobs table already holds, so
   the order survives a restart and agrees across control-plane replicas. With one org the order is
   oldest-first, which keeps unit 4's single-tenant FIFO.
8. **What the caps mean to an operator.** `--max-concurrent-runs`, `BAJUTSU_MAX_CONCURRENT_PER_ORG`,
   and `BAJUTSU_MAX_CONCURRENT_PER_USER` keep their names and now bound running jobs on both
   deployments. A local `serve` behaves as before, because there the two limits coincide.
   `docs/self-hosting.md` and its `docs/ja/` mirror document the queue-depth cap and the changed
   meaning, and a gauge for the configured queue-depth cap joins `bajutsu_max_concurrent` (the
   existing per-org `bajutsu_queue_depth` and `bajutsu_leased_jobs` gauges already report the two
   counts).

**Verification.** This is the one piece of the remaining pool work with a machine-checkable
contract, so it is unit-tested against `ServeState` with **no Simulator** (mirroring the per-org
cap's tests): under contention across two orgs the admitted jobs alternate fairly, an org never
exceeds its cap, a single-tenant deployment stays FIFO, and priority tiers are admitted more often
in proportion to their weight. These invariants sit entirely in the Python control plane and run in
the Linux `make check` gate.

The database-backed units add an observable outcome. With `--max-concurrent-runs 4` and six workers,
a ten-scenario submission to a target that runs on the worker queue is admitted whole, and no more than four of its jobs are ever leased at
once while two workers stay idle. Under two orgs of equal weight that both have queued work, their
leased counts never differ by more than one, and a control-plane restart does not change which job
is leased next, because the order is computed from the jobs table. The lease-order and cap-skip
logic runs on the SQLite gate.
The atomicity of the count and the lease needs real concurrent leasers and the advisory lock, so it
runs in the Postgres lane ([BE-0309](../BE-0309-serve-postgres-ci-lane/BE-0309-serve-postgres-ci-lane.md)).

**Coordination note.** The change lands in `bajutsu/serve/operations.py` and `jobs.py` — the same
surface other in-flight serve work touches — so it should land after any open PR editing that file
merges, or be coordinated to avoid a conflict.

## Alternatives considered

- **Keep rejecting with 429 and let clients retry** — rejected: it pushes the scheduling problem
  onto every caller, produces spurious failures for well-behaved orgs, and cannot express fairness
  (a retry storm from one org still wins the race). Holding jobs server-side is what lets the
  control plane enforce a fair share.
- **Pure FIFO across all orgs** — rejected: this is the status quo the per-org cap already had to
  work around; FIFO lets one org's burst monopolize the scarce Mac pool. Per-org pending queues
  with a quota-respecting round-robin are the fix.
- **Promote a held job each time another finishes** — rejected for the database-backed deployment:
  it needs a `held` job state and a promotion step on every completion path, lease reclaim
  included. Filtering at lease time gets the same holding from rows that are already `queued`.
- **Keep the per-org pending queues in memory on a database-backed deployment too** — rejected: an
  in-memory queue neither spans control-plane replicas nor survives a restart, and it duplicates the
  queue the jobs table already is.
- **A separate priority scheduler distinct from the fairness round-robin** — rejected as
  unnecessary: folding priority into the round-robin as a weight keeps one dispatch path instead of
  two interacting ones, which is simpler to reason about and to test.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Per-org pending queues — hold over-cap jobs instead of rejecting with 429.
- [ ] Round-robin dispatcher across orgs with pending work, admitting only under-cap orgs.
- [ ] Priority tiers as weights on the same round-robin.
- [ ] Single-tenant / unlimited-cap paths verified to stay plain FIFO.
- [ ] Queue-depth cap at admission on a database-backed deployment.
- [ ] Concurrency caps enforced at lease time, with an atomic count-and-lease and an `actor` column.
- [ ] Fairness and priority weights as the lease order.
- [ ] Operator documentation and metrics for the separated caps.

## References

`bajutsu/serve/operations.py` (`_register_and_dispatch`), `bajutsu/serve/jobs.py` (`try_register`),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (the self-hosting umbrella
this is carved from; the per-org cap it builds on shipped in
[#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the managed cloud
counterpart). The database-backed units build on `bajutsu/serve/server/db_executor.py`
(`DbQueueExecutor`), `bajutsu/serve/state/job_registry.py` (`try_register`, `_release_finished`),
and `bajutsu/serve/server/db/sql_repository.py` (`lease_job`), and on
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md),
[BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md), and
[BE-0309](../BE-0309-serve-postgres-ci-lane/BE-0309-serve-postgres-ci-lane.md).

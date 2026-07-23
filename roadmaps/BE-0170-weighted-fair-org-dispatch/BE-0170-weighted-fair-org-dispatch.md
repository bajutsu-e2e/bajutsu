**English** · [日本語](BE-0170-weighted-fair-org-dispatch-ja.md)

# BE-0170 — Weighted-fair cross-org job dispatch

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0170](BE-0170-weighted-fair-org-dispatch.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0170") |
| Topic | Hosting the web UI |
| Related | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
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

**Verification.** This is the one piece of the remaining pool work with a machine-checkable
contract, so it is unit-tested against `ServeState` with **no Simulator** (mirroring the per-org
cap's tests): under contention across two orgs the admitted jobs alternate fairly, an org never
exceeds its cap, a single-tenant deployment stays FIFO, and priority tiers are admitted more often
in proportion to their weight. These invariants sit entirely in the Python control plane and run in
the Linux `make check` gate.

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

## References

`bajutsu/serve/operations.py` (`_register_and_dispatch`), `bajutsu/serve/jobs.py` (`try_register`),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (the self-hosting umbrella
this is carved from; the per-org cap it builds on shipped in
[#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the managed cloud
counterpart).

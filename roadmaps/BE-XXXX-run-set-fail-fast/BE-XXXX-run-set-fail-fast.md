**English** · [日本語](BE-XXXX-run-set-fail-fast-ja.md)

# BE-XXXX — Stop a run-set group at its first failure

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-run-set-fail-fast.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Device-cloud execution |
| Related | [BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds an opt-in `failFast` flag to `POST /api/run-set`. When the flag is set, the first job of the group that ends in failure cancels every unfinished job of the same group. The group's conclusion stays `failure`. Each job the flag stopped records why it stopped, so a reader of the group can tell a scenario that failed from a scenario that never ran.

A `run-set` registers one job per scenario of a target, as a **group** with its own id ([BE-0484](../BE-0484-job-group-lease/BE-0484-job-group-lease.md)). Today every job of a group runs to the end, whatever its siblings do.

## Motivation

A pull request that breaks login breaks most scenarios of a suite. Each later scenario fails on the same screen, after the same timeouts. A group of forty scenarios then holds devices and worker slots for the whole suite, even though the answer is known after the first failure. On a device cloud, every one of those runs is also billed device time.

The group already has the parts a stop needs. `POST /api/run-set/<groupId>/cancel` cancels every unfinished job of a group. A queued job becomes `cancelled` at once, and a leased job stops on its worker's next heartbeat. What is missing is a trigger: nothing calls that cancel when a job of the group fails. A caller could poll the group and cancel by hand. Each caller would then repeat the same polling loop, and the stop would wait on the caller's polling interval.

The observable difference has two parts. A group registered with `failFast: true`, whose first finished job fails, leaves no job queued after that failure is recorded. And `GET /api/run-set/<groupId>` marks each job the flag stopped with `cancelReason: failFast`, which tells it apart from a job a person cancelled.

## Detailed design

### The request field

`POST /api/run-set` takes one more optional field, `failFast`, a boolean that defaults to `false`. The server refuses any other type with status 400. A group stores the flag in a new `fail_fast` column of `job_groups`.

The request digest, which tells a retry of a group key from a different request, gains the flag. A repeated group key whose request differs in `failFast` alone is a different request and gets status 409, as any other difference does today.

### What counts as a failure

The trigger uses the same rule as the group's `conclusion`. A job triggers the stop when it reaches a terminal status without being cancelled, and either of two conditions holds:

- the job's status is `failed`, which covers a lease that expired on every attempt;
- the job's status is `done` with an `exitCode` other than 0.

A cancelled job never triggers the stop. Otherwise a person's cancel of one job would cancel the whole group.

### Where the stop happens

A job reaches a terminal status other than a cancel on three paths in the repository:

- `complete_job`, when a worker posts the result;
- `fail_job`, when a worker reports an error;
- `reclaim_expired_leases`, when a lease expires on its last attempt.

Each path already writes the terminal status in one store transaction. When the job belongs to a group whose `fail_fast` is set and the job meets the failure rule, the same transaction marks the group's other unfinished jobs. A queued job becomes `cancelled`. A leased job gets its cancel flag, which its worker reads on the next heartbeat, as with a cancel today. The stop fires only when the conditional write of the terminal status wins its row: `complete_job` and `fail_job` reject a stale worker's result, and `reclaim_expired_leases` rejects a lease that a heartbeat renewed, so a rejected write cancels nothing. Writing the terminal status and the marks in one transaction leaves no window in which a worker could lease a sibling after the failure is recorded.

Each path returns the ids of the queued jobs it cancelled. After the commit, its caller closes the live log stream of each such job with the cancelled result, as the cancel route does today. No worker will ever post a result for those jobs, so without the close each would read as running forever. The reclaim path needs one more step. `reclaim_expired_leases` runs inside `lease_jobs`, which today discards the sweep's result, so the lease must hand those ids back to the lease route that holds the log bus. The implementation chooses how.

### Recording why a job stopped

Each job record gains a nullable `cancel_reason` column. The server writes `user` when a cancel route cancels a queued job or sets a leased job's cancel flag, and `failFast` when the stop above does either. A write never replaces a reason already recorded, so the first reason wins. A person who cancelled a leased job keeps `user`, even when a sibling fails before the worker reads the flag. The column stays `NULL` for a job that neither a cancel route nor the stop cancelled, such as one that a worker's own stop signal cancelled.

`GET /api/run-set/<groupId>` returns the column as `cancelReason` on each job that ended cancelled (its status is `cancelled`, or its result reports a cancel), and `null` on every other job. The column records a request to stop, and a leased job can finish before its worker reads the flag. A job that finished without a cancel thus never reports a reason, whatever the column holds. The group's `conclusion` keeps its present rule: `failure` wins over `cancelled`, so a group stopped by the flag concludes `failure`.

### Determinism

The flag never changes a verdict. A group with a failing job concludes `failure` with or without the flag. Which other scenarios ran before the stop depends on lease order and on how many slots the workers had. The set of scenarios that ran can thus differ between two runs of the same group. The report of the group names every job the flag stopped, so a reader never mistakes a stopped scenario for a passed one.

### Documentation

`docs/devicefarm.md` and `docs/self-hosting.md` describe the `run-set` request in both languages. Both pages gain the field and the `cancelReason` value.

## Alternatives considered

- **Stop after N failures (`maxFailures`).** A count generalizes the flag. No caller has asked for a threshold other than one, and a boolean leaves the door open: `maxFailures: 1` can replace it later without changing what `failFast: true` means.
- **Let the caller poll and cancel.** The routes exist today, so this needs no server change. Each caller would repeat the same loop, and every job a worker leases during the polling interval would still run.
- **Stop the jobs already running, without waiting for the heartbeat.** The server cannot reach a worker. A worker holds the connection, and the heartbeat is the only channel back to it. Cancelling through the heartbeat matches the cancel route the group already has.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] The `failFast` request field, its column, and the request digest
- [ ] The stop on the three terminal paths
- [ ] The `cancel_reason` column and the `cancelReason` field of the group read
- [ ] Documentation in both languages

## References

- [BE-0484 — Register jobs as a group and let a worker lease a group together](../BE-0484-job-group-lease/BE-0484-job-group-lease.md) — the group, its `conclusion`, and the group cancel this item triggers.
- `bajutsu/serve/operations/dispatch.py` — `start_run_set` and `_request_digest`.
- `bajutsu/serve/server/db/sql_repository.py` — `complete_job`, `fail_job`, `reclaim_expired_leases`, and `cancel_job`.
- `bajutsu/serve/operations/reads.py` — `job_group_view` and `_group_conclusion`.

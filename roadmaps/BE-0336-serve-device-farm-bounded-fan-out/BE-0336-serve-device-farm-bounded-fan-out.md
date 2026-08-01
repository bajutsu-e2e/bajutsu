**English** · [日本語](BE-0336-serve-device-farm-bounded-fan-out-ja.md)

# BE-0336 — serve-driven Device Farm dispatch with bounded per-scenario fan-out

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0336](BE-0336-serve-device-farm-bounded-fan-out.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0336") |
| Implementing PR | [#1425](https://github.com/bajutsu-e2e/bajutsu/pull/1425) (Unit 1 — submitter core migration) |
| Topic | Device-cloud execution |
| Related | [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md), [BE-0236](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md), [BE-0198](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets the serve web UI dispatch AWS Device Farm runs directly, and it splits the dispatch
one scenario at a time so scenarios run in parallel under a bounded device budget. Bajutsu can
already run Android and iOS scenarios on Device Farm, but only through the batch submitter of
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md): a manual
GitHub Actions workflow packages every scenario into one run that executes them back to back on a
single reserved device. This item moves that capability into serve — the surface where operators
already dispatch runs and watch history — and changes its shape. Instead of one run for the whole
suite, serve fans out one Device Farm run per scenario, and a configurable device budget `K` caps
how many of those runs are reserved at once. The device budget is the primary control: a hosted
deployment must never reserve more Device Farm devices than its quota allows, and per-scenario
parallelism fills that budget up to `K` without exceeding it.

## Motivation

The batch submitter serializes the suite. One run holds one reserved device, and its test spec
lists the scenarios as sequential `bajutsu run` commands, so wall-clock time grows with the scenario
count while every other device in the pool sits idle. A device cloud's value is running on many
devices at once; the shape shipped in
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md) uses one.
Splitting the suite into one run per scenario is what turns that idle capacity into parallelism.

The capability also lives only in continuous integration today. A contributor reaches Device Farm
by dispatching a GitHub Actions workflow by hand, away from the serve web UI. Yet serve already owns
run dispatch, the concurrency caps of its job registry
([BE-0198](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split.md)), and
run history for local device-pool runs. Extending serve to Device Farm gives the same operators the
same surface for the cloud, rather than a second, hidden path.

The device count is the hard constraint that orders the two requirements. Device Farm reservations
draw on a finite quota and cost money, so a hosted Bajutsu serving many users must cap how many
devices it reserves at any moment. Unbounded per-scenario fan-out would reserve one device per
scenario and overrun that quota — the parallelism this item introduces is exactly what makes the
cap necessary. So the device budget is the requirement that takes priority, and per-scenario
parallelism is the requirement it bounds: run as many scenarios in parallel as the budget allows,
and no more.

None of this touches the verdict. A Device Farm run reports pass or fail from Bajutsu's own
`manifest.json`, exactly as a local run does, so prime directive 1 holds — the submission and
polling machinery stays off the `run` / CI verdict path, and no large language model (LLM) call
enters it.

## Detailed design

The design reuses two assets rather than building new machinery: the per-scenario execution unit is
[BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md)'s
`submit_and_collect`, and the bounded queue is serve's executor seam together with the job
registry's concurrency caps
([BE-0198](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split.md)).
The device budget `K` maps onto the same atomic cap that already limits concurrent local runs, so
"limit the device count" and "limit concurrent Device Farm jobs" become one mechanism.

The work breaks down into six units.

1. **Single-scenario submission unit.** `scripts/devicefarm_submit.py` already renders a scenario
   list into the test spec with `render_test_spec` and uploads and executes it with
   `submit_and_collect`; today the caller passes the whole suite to `render_test_spec`. Confirm the
   one-scenario invocation as the fan-out unit, so each Device Farm run carries exactly one scenario
   and reports that scenario's verdict from its manifest.

2. **Device Farm executor for serve.** Add an executor implementation alongside the local-thread and
   database-queue executors that serve already selects between. Its dispatch submits a Device Farm
   run for one scenario, polls the run to completion within the 150-minute hard cap the batch
   topology imposes, and records the manifest verdict. The executor stays off the verdict path: it
   ferries a deterministic run to the cloud and back, and makes no pass/fail judgment of its own.

3. **Per-scenario fan-out.** Add a serve dispatch mode that expands a scenario-set request into one
   job per scenario. serve already dispatches one job per scenario for local runs, so the fan-out is
   a thin layer that enumerates the requested scenarios and registers a job for each.

4. **Device budget `K`.** Bound the number of in-flight Device Farm jobs with the job registry's
   concurrency cap, keyed on the Device Farm device pool as the contended resource, so at most `K`
   runs reserve devices at once and the rest queue. The default `K` lives in config under
   `targets.<name>`, keeping the per-target difference in config as prime directive 3 requires, and a
   request may lower it. Pin Device Farm's own `maxDevices` to one per run so a single run reserves a
   single device, and the Bajutsu-side cap alone governs the device count.

5. **Durable state for long polls.** Prefer the hosted, database-backed backend, where the
   database-queue executor enqueues each job and workers lease it: the run's state — queued,
   submitted, polling, done — persists, so a serve restart during a 150-minute poll resumes instead
   of losing the run. The local single-process backend keeps a thinner, best-effort path on a
   background thread for a single-operator deployment.

6. **Documentation and tests.** Extend the bilingual Device Farm how-to with the serve dispatch flow
   and the device-budget setting. Cover the fan-out and the cap with the in-memory AWS fake that
   [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md) already uses,
   so the gate exercises the seam without reaching real AWS.

## Alternatives considered

**One run with a `K`-device pool, letting Device Farm shard the scenarios.** A single
`schedule_run` against a pool of `K` devices looks simpler than `K` separate submissions. Device
Farm's custom test environment, however, replicates the test spec on every device in the pool rather
than distributing scenarios across them, so this approach reruns the whole suite `K` times instead
of splitting it. It does not satisfy per-scenario parallelism without extra sharding logic inside
the spec, so this item fans out one run per scenario on the Bajutsu side, where the budget `K` and
the device count are the same number.

**Keep Device Farm in continuous integration and add a build matrix.** A GitHub Actions matrix
could dispatch one workflow leg per scenario and bound the legs. This bounds parallelism at the
continuous-integration layer, but it leaves serve — where operators already dispatch and watch runs
— without the capability, and it cannot share serve's per-user and per-org caps or its run history.
This item makes serve the primary path and leaves the existing manual workflow in place for headless
use.

**A dedicated device semaphore separate from the job registry.** A standalone semaphore for Device
Farm devices would work, but the job registry already enforces atomic global, per-user, and per-org
caps
([BE-0198](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split.md)). A
second mechanism would duplicate that logic and drift from it, so this item routes the device budget
through the registry's existing cap.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Single-scenario submission unit — expose and confirm the one-scenario `submit_and_collect`
  invocation as the fan-out unit.
- [ ] Device Farm executor for serve — submit and poll one scenario, record the manifest verdict off
  the verdict path.
- [ ] Per-scenario fan-out — expand a scenario-set request into one job per scenario.
- [ ] Device budget `K` — bound in-flight Device Farm jobs through the job registry, default from
  config with a per-request override, and pin Device Farm `maxDevices` to one.
- [ ] Durable state for long polls — persist queued/polling/done state on the hosted backend; keep a
  best-effort local path.
- [ ] Documentation and tests — bilingual how-to update and faked-AWS coverage of the fan-out and the
  cap.

Log:

- [#1425](https://github.com/bajutsu-e2e/bajutsu/pull/1425) (Unit 1) — migrated the submitter core (`render_test_spec`, `build_package`,
  `verdict_from_manifest`, `submit_and_collect` and its helpers, the `DeviceFarmClient` / `Transfer`
  seams) from `scripts/devicefarm_submit.py` into `bajutsu/cloud/devicefarm.py`, so serve's coming
  fan-out and executor reuse one submitter on the coverage-measured path; `scripts/devicefarm_submit.py`
  is now a thin CLI wrapper holding only argparse and the real boto3/urllib adapters. Confirmed the
  one-scenario invocation as the fan-out unit (a single-scenario spec renders exactly one `bajutsu run`).
  No behavior change — the existing faked-AWS suite passes unchanged.

## References

- [BE-0235 — AWS Device Farm batch submitter](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md)
  — the batch submitter and `submit_and_collect` this item fans out and drives from serve.
- [BE-0236 — Device-cloud provider abstraction](../BE-0236-device-cloud-provider-abstraction/BE-0236-device-cloud-provider-abstraction.md)
  — the live-device topology, distinct from the batch topology this item extends.
- [BE-0198 — serve state and job-registry split](../BE-0198-serve-state-job-registry-split/BE-0198-serve-state-job-registry-split.md)
  — the job registry whose concurrency cap enforces the device budget.
- [`docs/devicefarm.md`](../../docs/devicefarm.md) — the Device Farm how-to this item extends with the
  serve dispatch flow.

**English** · [日本語](BE-XXXX-job-group-lease-ja.md)

# BE-XXXX — Register jobs as a group and let a worker lease a group together

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-job-group-lease.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Device-cloud execution |
| Related | [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md), [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md), [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md), [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md), [BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out.md), [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of.md) |
<!-- /BE-METADATA -->

## Introduction

This item changes what a `run-set` is and how workers consume it. A `run-set` registers a **group** of jobs and does nothing else. A worker leases several jobs of one group in a single request, uploads the shared package and the app binary once, and runs the jobs in parallel. Several workers may share one group, each taking as many jobs as it has free slots. The submitter names a platform (`ios`, `android`, or `web`) and no execution environment. The server routes each job to any worker that can serve the platform, whether that worker drives a local device or submits to a device cloud.

Today a `run-set` already fans a target out into one job per scenario. Each job is then independent. A Device Farm worker leases one job at a time, builds a package for it, and uploads the same binary again. The submitter must also state `environment: devicefarm`. This item keeps the per-scenario job and the per-job result. It adds a group identity above the jobs, so that the work the jobs share is done once per worker.

## Motivation

[BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md) split a suite into one Device Farm run per scenario so that scenarios run in parallel. [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) moved that dispatch onto the worker, which leases up to `maxJobConcurrency` jobs. The split bought parallelism, and it left four costs in place.

- **Shared work repeats per job.** The Device Farm provider builds a package and calls `submit_and_collect` with the application path for every job. A `run-set` of forty scenarios against one target uploads the same binary forty times. The config and the scenarios in the package are the same bundle each time.
- **The submitter must know where the run happens.** The request requires `environment` naming a batch provider. A caller such as a GitHub Actions job wants to say "run these on Android" and leave the placement to the operator. [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) made the environment a choice per run, and that choice now falls on every caller.
- **A large set is cut short at registration.** The server's global, per-user, and per-org concurrency caps count queued jobs. When a fan-out reaches a cap, the server stops registering and returns the jobs it has so far, with status 200. The remaining scenarios are never queued.
- **A set of jobs has no identity.** The response lists `jobIds` and nothing names the set. A caller cannot ask for the set's status. A retried request registers the same scenarios a second time, and nothing detects the duplicate. A caller that wants to publish the set as one check on a pull request has no key to attach it to.

The observable difference has four parts. A `run-set` of N scenarios that share a config and a binary uploads the binary once per stretch in which a Device Farm worker holds jobs of the group, where today it uploads it N times. A `run-set` request without `environment` is accepted and runs on whichever workers serve the platform. A `run-set` larger than the concurrency caps registers every scenario and runs them in turns. And a second request that carries the same group key returns the existing group and registers no new jobs.

## Detailed design

### Terms

- **Group**: the jobs one `run-set` request registers. A group has a **group key**, which the caller may choose, and a **group id**, which the server assigns.
- **Run intent**: what a job asks to run, independent of where. It names the target, the scenario, the config, the application reference, and the platform.
- **Compatibility key**: a digest of what a worker can share between jobs. It covers the config bytes, the application binary digest, the platform, and the target name. It leaves out the scenario, which differs from job to job.
- **Bundle**: the jobs one worker leases in one request. Every job in a bundle belongs to one group and has one compatibility key. A group may split into many bundles across many workers.

### Registering a group

`POST /api/run-set` keeps its body, with `environment` now optional and one optional field added, `groupKey`. The response gains `groupId` and keeps `jobIds`.

- The group key is a string of at most 128 characters from `[A-Za-z0-9._/:-]`. It is unique within the caller's org. The server generates a key when the caller sends none.
- Registration is all or nothing. Either every scenario of the request becomes a queued job, or the request is refused and no group exists. The concurrency caps no longer cut a group short, because they apply at lease time, as a later section describes. A server setting instead bounds a group's scenario count, and a request above it is refused. Without it, one request could queue an unbounded number of jobs.
- A server without a queue still refuses every `run-set`, as today. A single-machine start gets a queue by default, so only a server started without a queue meets the refusal.
- A repeated request with the same key and the same request digest returns the existing group with status 200 and registers no jobs. A repeated key with a different digest is refused with status 409. The digest covers the target, the scenario names, the config digest, the application digest, the run label, and the `environment` (empty when omitted).
- A GitHub Actions caller builds the key from `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, and `GITHUB_JOB`. `GITHUB_RUN_ID` alone is wrong, because a re-run of a workflow keeps its run id and changes only the attempt. The key therefore makes a retried request within one attempt idempotent. A deliberate re-run asks for a fresh group and gets one.
- A new table, `job_groups`, holds the org, the key, the request digest, and the creation time, with a unique index on the org and key. Each job record gains `group_id` and `compat_key` columns. Jobs registered by other routes leave both empty.
- `GET /api/run-set/<groupId>` returns the group's jobs and their statuses, for the caller's own org alone. A group of another org answers 404 rather than 403, non-leaky like every other org-scoped read (BE-0015). It is the read route a reporter polls. Publishing the result as a GitHub check is a separate concern and is out of scope.

### A job that any environment can run

A `run-set` job today carries a `BatchRequest`, which fixes the provider at registration. A local `bajutsu run` job carries a command line and device arguments instead. Neither shape can run on the other kind of worker. A group job therefore carries the run intent alone, and the worker that leases the job turns the intent into its own form.

- A local worker builds the `bajutsu run` invocation for the scenario on its own device, in the way it runs a plain run job today.
- A device-cloud worker builds a `BatchRequest` with its own provider kind and submits it.

The application reference stays logical, as [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) already requires. A web target needs no application, so the server no longer demands `subject.path` for a web target. An Android or iOS target without `subject.path` can still run locally, and the server leaves out its device-cloud alternatives (next section). A target of any other platform is refused with status 400, as today.

When a request passes `environment`, the job carries the same run intent and routes to that environment alone. Pinning changes the routing, not the job's shape.

### Routing without an environment

A job's `capabilities` today is an all-of list, and a worker must advertise every token. A job registered without an `environment` needs an either-or. [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of.md) already stores one any-of group as a single token, `anyof:<token>|<token>`, which a worker satisfies by advertising any one member. That token cannot say "all of these tokens, or all of those", and the local alternative needs several tokens together. A new field, `anyOf`, therefore holds alternative token lists, and a worker may serve the job when at least one alternative is a subset of its advertised tokens.

The server writes the alternatives from the target's platform. An `appium` grid (BE-0452) is not among them, so an environment-less request reaches no grid worker, and a grid-only deployment still passes `environment`. Adding that alternative is out of scope here.

| Platform | Local alternative | Device-cloud alternatives |
|---|---|---|
| `android`, `ios` | every token a plain run of the target requires (`target_capabilities`): the platform and host tokens, BE-0468's device-family tokens, and the `cap:` and `setting:` tokens | one `environment:<provider>` per device cloud the deployment names, when the target has `subject.path` |
| `web` | platform and host tokens, plus the same `cap:` and `setting:` tokens | none |

The server runs the same `route_refusal` check on the local alternative that it runs for a plain run. A refused local alternative is dropped, and the request is refused with status 400 only when no alternative remains. The deployment names its device clouds in the server's configuration, so no caller has to know them. A request that passes `environment` keeps today's single all-of list.

Four consumers judge whether a worker can serve a job: the lease filter in `lease_job`, the unroutable count, the no-worker notice (`lacks_live_worker`), and `route_refusal`. All four go through `can_serve`, which accepts the alternatives and passes when any one does. The four thus keep agreeing, as BE-0468 requires of its own token.

The worker that leases the job decides where it runs, and the first worker to lease wins. The place a scenario runs is therefore not fixed, and the same scenario may pass on a Simulator and fail on a cloud device. Prime directive 2 concerns waits and selectors, and the verdict still comes from assertions alone. The cost remains a reproducibility cost, so the run records the environment of the worker that executed it in its provenance, and the report shows it. A caller that needs one placement passes `environment`.

### Leasing a bundle

The lease request gains `maxJobs`, the number of free slots the worker has: `maxJobConcurrency` minus the jobs in flight. When the request carries `maxJobs`, the response is a list of leased jobs, each in today's per-job shape. Without it, the response is today's single object, so a worker from before this item still leases a plain job. A run-intent job additionally requires a token that only a worker from this item advertises, so an older worker never leases one. An older worker would fail the job instead of leaving it queued: a device-cloud worker's `environment_refusal` refuses a spec that carries no `batch` (BE-0448), and a local worker finds no `cmd` to run.

`lease_job` first picks the anchor in the way it does today: the oldest queued job the worker can serve. When the anchor has a group, the call then claims up to `maxJobs - 1` further queued jobs. Each must carry the anchor's `group_id` and `compat_key` and be servable by the worker. Each claim is the same conditional `UPDATE ... WHERE status = 'queued'` as the anchor's claim, so a lost race skips one job and never fails the lease.

The anchor stays the oldest servable job, so the order between groups does not change. The followers may overtake older jobs of other groups, and `maxJobs` bounds how far. This is a deliberate cost. The saved uploads are worth more than strict age order inside the queue.

The scan that finds the anchor reads one candidate per pair of `group_id` and `compat_key`. Today `lease_job` loads every queued job, oldest first, and tests each with `can_serve`. A worker that cannot serve a group of five hundred scenarios thus tests five hundred jobs first. This item still reads every queued job outside a group. For group jobs, it reads the oldest queued job of each pair alone. A window function selects that job:

```sql
ROW_NUMBER() OVER (PARTITION BY group_id, compat_key ORDER BY created_at)
```

SQLite and PostgreSQL both support window functions, so the SQLite gate exercises the same query. An index on `(status, group_id, compat_key, created_at)` keeps the selection inside the database. The `can_serve` calls in Python then grow with the groups and the plain jobs, not the queue.

One candidate can stand for its pair because every job of a pair gets the same verdict. The server computes the required tokens and the `anyOf` alternatives once per request. It derives them from the target, the config, the `backend`, and the `environment`. Every job of a group thus stores the same set. The jobs also share the org and the actor, against which the caps count. This sameness is an invariant of the design. A later change could add a per-job input to the required set. That change would break the invariant. It would then have to add a digest of the required tokens to the pair.

The candidate query takes no lock, as today. PostgreSQL would refuse `FOR UPDATE` on it anyway, since it uses a window function. The claim then runs a second query on the chosen pair alone, with no window function. That query takes the queued jobs of the pair oldest first, up to `maxJobs`. PostgreSQL locks them with `FOR UPDATE SKIP LOCKED`. SQLite claims each with the conditional `UPDATE`. Workers that chose the same pair thus spread across its jobs instead of racing for its oldest one.

The capability check stays in `can_serve` alone, so the four consumers keep one definition. The total number of queued group jobs stays unbounded. Registration bounds each group, and the `can_serve` calls in Python no longer grow with the total. The database still reads every queued group row through the index.

A secret-store read failure during the lease fails every job of the bundle, where today it fails one job. The jobs share one org, and the read would fail for each of them alike.

A local worker still accepts a `maxJobConcurrency` of one only, as [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) states. It leases bundles of one, and the grouping gives it nothing to share.

### Splitting a group at the concurrency caps

The global, per-user, and per-org caps of the server keep their meaning: the number of jobs one deployment, one user, or one org may run at once. For a group job they count leased jobs instead of queued ones. Two stores count jobs today, and the design fixes which one owns each count.

- **The jobs table owns the lease-time count.** It already holds every queued job, since the server runs no job itself. `lease_job` counts the jobs that hold a slot, globally, per org, and per user: leased jobs of every kind, plus queued jobs outside a group. The registry admits plain jobs on the same definition, so the two counts never disagree, and a queued plain job keeps its slot against a group lease. `lease_job` skips a group job when any of the three caps is reached, and stops adding followers to a bundle at the same point.
- **Each job record gains an `actor` column**, so the per-user count needs no read of the spec JSON. Today the actor lives inside the spec alone.
- **The in-memory registry leaves queued group jobs out of its count.** The registry counts every registered job that has not finished, and plain jobs are capped on that count at registration. A group job enters that count when it is leased. The registry learns of a lease the way it learns of a finish today: it probes the jobs table before each count. Otherwise a queued group of two hundred scenarios would hold the caps and refuse every plain run of its org until the group drains.

The rest of the group stays queued and is leased as earlier jobs finish.

A group larger than the caps thus splits into bundles that run in turns. The bundles may go to different workers, and to different environments when the group was registered without one. Each worker shares uploads among its own bundle's jobs alone. A group served by three Device Farm workers therefore uploads the binary three times, where today it uploads it once per scenario.

Plain run, record, crawl, and triage jobs keep today's registration-time caps. Moving them is separate work.

### Sharing the upload on a Device Farm worker

Each leased job keeps its own slot, heartbeat, run-identifier checkpoint, and posted result, exactly as in [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md). The slots share two things. One package archive carries the bundle directory and the materials of every scenario in the bundle. One application upload carries the binary. The first slot to need them uploads them, and the other slots wait on that upload. Each slot then builds its own test spec, which names its scenario and target, and schedules its own Device Farm run.

A batch lifecycle hook can make the package differ per job, because `before_submit` may set a launch environment that the provider writes into the packaged config. The package is therefore shared only when no hook sets a launch environment for the job. Otherwise each job builds its own package, and the application upload alone is shared.

The worker keeps the application upload identifier by compatibility key while any job with that key is in flight. A slot that frees up and leases a later job of the same group reuses it. The worker reuses the package only when the package already contains the later job's scenario. A package built from the config directory holds every scenario of the target, while a package built from shipped materials holds only its own bundle's scenarios. Otherwise the slot builds and uploads a new package. Once no job holds the key, the worker drops the entry, and a later lease uploads again.

This design assumes that several Device Farm runs can reference one upload. Unit 4 confirms it against the service before relying on it. If a run cannot reuse an upload, the provider falls back to one upload per job. The result is then today's behavior, and only the saving is lost.

A failed shared upload fails each slot that waited on it, through the same path as a failed upload today. Each job then follows its own attempt count.

### Compatibility

`environment` is still accepted on `run-set`, and [BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out.md)'s Replay tab keeps working unchanged. Its environment choice gains an "any" entry later, which this item does not require. The plain `POST /api/run` route is unchanged.

[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) states that `run-set` rejects a missing environment with a 400 response. This item reverses that rule, and the reversal is intended. The rule existed because a `run-set` job carried a device-cloud `BatchRequest` from registration on. Under this item the job carries a run intent that a local worker can run as well.

### Prime-directive compliance

- **AI never judges.** No change touches how a verdict is computed, and no model call enters the run path.
- **Determinism first.** A bundle changes how jobs are claimed, not how any job waits or selects. The one new source of variation, the placement, is recorded and can be pinned.
- **App-agnostic.** Nothing in a group, a key, or a bundle names an app. The compatibility key is a digest of what the target already declares.

### Work breakdown (MECE)

1. **Group registration.** The `job_groups` table, the server setting that bounds a group's scenario count, the `group_id`, `compat_key`, and `actor` columns, the `groupKey` field, all-or-nothing registration, the idempotency and conflict rules, and `GET /api/run-set/<groupId>`.
2. **Run intent.** The job shape that names a run intent, its conversion into a local run on a local worker and into a `BatchRequest` on a device-cloud worker, the token that keeps an older worker from leasing it, `environment_refusal` accepting a run-intent spec, which carries no `batch`, and the web path.
3. **Grouped lease and caps.** `maxJobs` on the lease request, the multi-claim in `lease_job`, the scan of one candidate per pair and its index, the list response, the worker client's use of its free slots, the lease-time caps counted on the jobs table, the registry's exclusion of queued group jobs, and its probe of the jobs table for leases.
4. **Shared upload.** The Device Farm provider's application upload held by compatibility key, the package reused only when it holds the job's scenario, the exception for a hook that sets a launch environment, and the fallback to one upload per job.
5. **Either-or routing.** The `anyOf` field, `can_serve` accepting the alternatives for all four consumers, the server's choice of alternatives per platform, the dropped local alternative, and the provenance record.
6. **Documentation.** The `docs/` pages and their `docs/ja/` mirrors for the serve API, the worker, and routing, plus the affected passages of `DESIGN.md` and `docs/architecture.md`.

Out of scope: publishing a group's result as a GitHub check, sharing uploads between workers, running concurrent jobs on a local device, and lease-time caps for jobs outside a group.

## Alternatives considered

- **One Device Farm run per group, with the scenarios run in sequence.** This is the shape of [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md). It reserves the fewest devices, and the wall-clock time grows with the scenario count. BE-0336 left it for that reason, so this item keeps one run per job.
- **Bundle by the compatibility key alone, with no group key.** Jobs from any route that share a config and a binary would bundle. Nothing would tell the caller which jobs form a set, and a retried request could not be detected as a duplicate. The group key supplies both, and the compatibility key still guards against a wrong bundle.
- **Keep the registration-time caps and let a retry register the rest.** A retry with the same key would add the scenarios the cap left out. A group would gain an "incomplete" state, and a caller would have to retry until the group fills. Lease-time caps make the same group run to completion with no retry.
- **Let the server build the package and upload for the group.** The server would hold the cloud credentials again, which [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) removed on purpose. The worker keeps both.
- **Test capabilities in the query.** The scan would then skip the jobs a worker cannot serve. The cost is a second implementation of `can_serve` in two Structured Query Language (SQL) dialects. That query would carry the three rules of `can_serve` and the cap counts:
  - the environment rule
  - the route-token rule for older workers
  - the `anyOf` alternatives
  - the caps

  The other three consumers would keep the Python implementation. The two copies would have to stay in step. The scan of one candidate per pair bounds the cost by the group count, with one implementation.
- **Keep the explicit environment and add grouping only.** This is the smaller change, and it needs no run intent. It leaves the second cost, that every caller must know the placement, and a CI workflow would still hard-code `devicefarm`.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — Group registration
- [ ] Unit 2 — Run intent
- [ ] Unit 3 — Grouped lease and caps
- [ ] Unit 4 — Shared upload
- [ ] Unit 5 — Either-or routing
- [ ] Unit 6 — Documentation

## References

- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md): the per-scenario fan-out.
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md): the Device Farm worker and its slot model.
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md): the worker capability and `maxJobConcurrency`.
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the environment as a choice per run.
- [BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out.md): the Replay tab's fan-out.
- [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of.md): the `anyof:` token and the four consumers of `can_serve`.
- `bajutsu/serve/operations/dispatch.py` (`start_run_set`), `bajutsu/serve/server/db/sql_repository.py` (`lease_job`), `bajutsu/serve/state/job_registry.py` (`try_register`), and `bajutsu/serve/batch_provider/device_farm_batch_provider.py`: the code this item changes.

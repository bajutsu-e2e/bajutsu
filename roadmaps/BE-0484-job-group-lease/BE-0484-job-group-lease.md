**English** · [日本語](BE-0484-job-group-lease-ja.md)

# BE-0484 — Register jobs as a group and let a worker lease a group together

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0484](BE-0484-job-group-lease.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0484") |
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

The observable difference has four parts. A `run-set` of N scenarios that share a config and a binary uploads the binary once per stretch in which a Device Farm worker holds jobs of the group, where today it uploads it N times. A `run-set` request names no environment and runs on whichever workers serve the platform. A `run-set` larger than the concurrency caps registers every scenario and runs them in turns. And a second request that carries the same group key returns the existing group and registers no new jobs.

## Detailed design

### Terms

- **Group**: the jobs one `run-set` request registers. A group has a **group key**, which the caller may choose, and a **group id**, which the server assigns.
- **Run intent**: what a job asks to run, independent of where. It names the target, the scenario, the config, the application reference, and the platform.
- **Compatibility key**: a digest of what a worker can share between jobs. It covers the config bytes, the application binary digest, the platform, and the target name. It leaves out the scenario, which differs from job to job. One request fixes all four inputs, so a group holds one compatibility key.
- **Route key**: a digest of a job's `capabilities` list. Two jobs with the same route key get the same verdict from `can_serve`.
- **Base key**: the same digest taken without the route tokens (`cap:` and `setting:`). A worker that checks no routes gives one verdict to two jobs with the same base key.
- **Bundle**: the jobs one worker leases in one request. Every job in a bundle belongs to one group. A group may split into many bundles across many workers.

### Registering a group

`POST /api/run-set` keeps its body with two changes. It drops `environment`, and it adds one optional field, `groupKey`. The response gains `groupId` and keeps `jobIds`.

- The group key is a string of at most 128 characters from `[A-Za-z0-9._/:-]`. It is unique within the caller's org. The server generates a key when the caller sends none.
- The server refuses a request that still sends `environment`, with status 400. `_run_set_refusal` refuses `deviceBudget` and the override fields the same way today. The error names this item. Which worker runs a job is not the request's to say.
- Registration is all or nothing. Either every scenario becomes a queued job, or the request is refused and no group exists. The group row and every job row land in one store transaction, after the scenarios' files are stored. A store error rolls the whole write back and answers the request with that error. A half-written fan-out thus never exists for a retry of the key to mistake for a complete group. Group jobs bypass `try_register`. Their ids are drawn before the transaction, and no group job enters the registry before the commit. The concurrency caps no longer cut a group short, because they apply at lease time, as a later section describes. The server refuses a request that names the same scenario twice, with status 400. It judges duplicates after each name resolves to its scenario file. `runnable` keeps the basename alone, so `a.yaml` and `x/a.yaml` name one scenario. A group thus never holds more jobs than the target has distinct scenarios.
- A server without a queue still refuses every `run-set`, as today. A single-machine start gets a queue by default, so only a server started without a queue meets the refusal.
- A repeated request with the same key and the same request digest returns the existing group with status 200 and registers no jobs. A repeated key with a different digest is refused with status 409. The digest covers the target, the resolved scenario names, the config digest, the application digest, and the run label.
- A GitHub Actions caller builds the key from `GITHUB_RUN_ID`, `GITHUB_RUN_ATTEMPT`, `GITHUB_JOB`, and `strategy.job-index`, the zero-based index of the matrix leg. Raw matrix values can hold spaces or whole objects, which the key's character set refuses. `GITHUB_RUN_ID` alone is wrong, because a re-run of a workflow keeps its run id and changes only the attempt. `GITHUB_JOB` alone is wrong too, because every leg of a `strategy.matrix` shares it. A matrixed fan-out would send one key with a different digest per leg, and every leg but the first would get 409. The key therefore makes a retried request within one attempt idempotent. A deliberate re-run asks for a fresh group and gets one.
- A new table, `job_groups`, holds the org, the key, the request digest, and the creation time, with a unique index on the org and key. Unit 1 creates the org, key, and creation-time columns. Unit 3 adds the digest column and the unique index. Each job record gains `group_id`, `compat_key`, `route_key`, and `base_key` columns. Jobs registered by other routes leave all four `NULL`. The window function below runs over the rows with a `group_id` alone. Every queued plain job thus stays a candidate. The lease response carries `compat_key`, which keys the worker's upload cache (unit 4).
- A GitHub Actions caller authenticates as a machine (BE-0414). `forbidden_for_machine` refuses every route it does not name. Unit 1 adds `POST /api/run-set` to `_MACHINE_PATHS`. It admits `GET /api/run-set/<groupId>` by shape, as `_is_machine_job_route` admits `/api/jobs/<id>`. The group cancel stays closed to a machine, as the job cancel is today. Both opened routes take `machine_org` from the request context, as `POST /api/run` does, and the register route takes `ci_job` for provenance. The group's org and the read's org check resolve through `state.org_for`. Without `machine_org`, every machine call would resolve the `default` org and reach another tenant's groups.
- `GET /api/run-set/<groupId>` returns the group's jobs and their statuses, for the caller's own org alone. A group of another org answers 404 rather than 403, non-leaky like every other org-scoped read (BE-0015). It is the read route a reporter polls. Publishing the result as a GitHub check is a separate concern and is out of scope.
- `POST /api/run-set/<groupId>/cancel` cancels every unfinished job in the group. It reuses today's per-job cancel. A queued job becomes `cancelled` at once, and no worker leases it. A leased job carries the cancel flag. Its worker stops the job on its next heartbeat, as today. The route needs the same role as `POST /api/jobs/<id>/cancel`. `required_role` matches that route by its `/api/jobs/` prefix and `/cancel` suffix. The group cancel needs a rule of the same shape: `path.startswith("/api/run-set/") and path.endswith("/cancel")`. An entry in the exact-match `_EDITOR_PATHS` set never matches a concrete group id. Without the rule, a viewer could cancel a group. It answers for the caller's own org alone, and a group of another org returns 404. Cancelling a group with no unfinished job returns 200 and changes nothing.

### A job that any environment can run

A `run-set` job today carries a `BatchRequest`, which fixes the provider at registration. A local `bajutsu run` job carries a command line and device arguments instead. Neither shape can run on the other kind of worker. A group job therefore carries the run intent alone, and the worker that leases the job turns the intent into its own form.

- A local worker builds the `bajutsu run` invocation for the scenario on its own device. It calls the builder the server uses for a plain run job (`run_command`). The intent carries the four values that the server alone decides:
  - the upload-execution policy for an uploaded bundle's `launchServer` command (BE-0090);
  - whether to copy the org's stored baselines into the workspace (BE-0073). An uploaded bundle never gets them, and its command drops `--baselines`;
  - the label;
  - the provenance.
- A device-cloud worker builds a `BatchRequest` with its own provider kind and submits it.

The application reference stays logical, as [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) already requires. A web target needs no application, so the server no longer demands `subject.path` for a web target (unit 5). An Android or iOS target still needs `subject.path`. The server refuses one without it with status 400, as today. A device-cloud worker serving its platform could lease the job and would have nothing to install. A target of any other platform is refused with status 400, as today.

### Routing by platform

A group job routes the way a plain job does. Its `capabilities` is an all-of list, and a worker serves the job when it advertises every token. The job names what its run needs, and each worker advertises what it provides. The request names no placement, and the server writes none.

A group job requires these tokens:

- the platform token: `platform:ios`, `platform:android`, or `platform:web`
- for an iOS target, the device-family tokens of BE-0468
- for an iOS target, a `kind:simulator` or `kind:device` token for each kind its runs take, with or without a family
- the scenario's own `cap:` and `setting:` tokens, read from its text (BE-0467)
- the run-intent token, which only a worker from this item advertises

A group job carries no `machine:<id>` token. Every group job ships its files, as a bundle of the config's directory or as materials (BE-0465), so none names a server path. Registration checks this for each job, not only the first scenario.

It requires no `host:` token. The host a driver needs belongs to the local worker. Since BE-0450, a local worker advertises only the platforms its host can drive. The platform token thus covers the host. This rests on every actuator of a platform sharing one host. A later actuator that breaks the rule must bring the `host:` token back. The deprecated `--capabilities` override could still carry `platform:ios`. Unit 5 thus reserves `platform:` and `kind:` in it.

The kind tokens keep a target on the kind it declares. Without them, an iOS target that names no family would require nothing about its kind. A device-cloud worker could then run a Simulator target on a physical device. The kinds are the ones `_family_requirements` already derives. That is the listed `kind`, or else the kind the target's driver implies (`driven_kind`). An iOS target meant for a device cloud thus declares the scalar `kind: device`, which is the kind `driven_kind` reads through its driver. A scalar kind says how the target is driven, so `unpack_conditions` does not count it as a condition. A *listed* `ios.kind` is a condition, and a device cloud refuses it at submit. Such a target's build must install on a physical device anyway, which a Simulator build cannot. A target that lists both kinds routes to a local worker that holds both. A plain job requires no kind token. A device cloud refuses the other `runsOn` conditions at submit as well (`_check_no_conditions`): `ios.model`, `ios.os`, `android.avd`, and `android.apiLevel`. The schema already refuses `ios.model` and `ios.os` for a physical device kind, so a listed `ios.kind` is the one an iOS device-cloud target can still declare. This item adds no token for them. A group job for a target that declares one can go to a device-cloud worker, fail at submit, and spend an attempt. A `run-set` of such a target fails that way on every attempt today, since the request must name a batch provider. This item takes the case no further, and some of those requests now succeed on a local worker.

A local worker advertises `kind:simulator` when its Simulator inventory holds a device, from the same startup read that yields its `iosNN` and `iphone` / `ipad` tokens. It advertises `kind:device` while it drives an attached iPhone or iPad. A `worker.yaml` that sets `realDevices: false` turns that token off. The worker reads `kind:device` with its real-device tokens, on the same per-lease schedule.

A device-cloud worker changes what it advertises. Today it advertises `environment:<provider>` in place of any `platform:*` token (BE-0448). With this item it also advertises its provider's platforms. It advertises no device-family token, because the provider's device selection filters by platform alone. A target that names a family (`runsOn.ios.family`) thus routes to workers that can honor it. Today `environment: devicefarm` hands such a target to a Device Farm worker. That worker refuses it at submit, because no pool matches a `runsOn` condition (`_check_no_conditions`). The family token moves the refusal onto routing. The job then waits for a worker that can honor it. It no longer burns an attempt. The worker advertises `kind:device`, since every device it reserves is physical.

BE-0448's environment rule keeps an `environment:*` worker off every job that lacks that token. The rule stops applying to a job that requires the run-intent token. It still applies to every other job. A device-cloud worker thus never leases a plain run, which would start a local device. An `appium` grid worker (BE-0452) advertises no `platform:*` token, so it takes no group job. A deployment of grid workers alone keeps running scenarios through plain run requests.

The server runs `route_refusal` on each job's list, as it does for a plain run. It reads the live workers' capabilities once per request, and it computes the route needs once per distinct scenario text. Registration still costs one parse per distinct scenario, so it grows with the group. We accept that cost. It lands on one request, before any row exists. It refuses the whole request with status 400 when these three hold for one job:

- Live workers drive the job's route.
- Each of them lacks one of the job's `cap:` or `setting:` tokens.
- No live worker can serve the job.

Registration is all or nothing, so such a refusal drops the whole group, and the response names the scenario and the missing tokens. Otherwise a job that no live worker can serve stays queued.

Five consumers judge whether a worker can serve a job:

- the lease filter in `lease_job`
- the lease-time slot count
- the unroutable count
- the no-worker notice (`lacks_live_worker`)
- `route_refusal`

All five go through `can_serve`, whose one change is the exemption above. The five consumers thus keep agreeing, as BE-0468 requires of its own token. The worker's claim-time re-check (`claim_refusal`, BE-0467) reads the same list and needs no change, beyond running once per job of a bundle. A route token that leaves the worker between its advertise and its claim therefore fails every job of the bundle, as the secret-store read does below. The refusal judges the worker, not the job, so each slot reaches the same verdict and each failure spends its job's attempt.

The worker that leases the job decides where it runs, and the first worker to lease wins. The place a scenario runs is therefore not fixed, and the same scenario may pass on a Simulator and fail on a cloud device. Prime directive 2 concerns waits and selectors, and the verdict still comes from assertions alone. The cost remains a reproducibility cost, so the run records the environment of the worker that executed it in its provenance, and the report shows it. A caller cannot pin a placement. An operator who needs one controls it through the workers the deployment runs.

### Leasing a bundle

The lease request gains `maxJobs`, the number of free slots the worker has: `maxJobConcurrency` minus the jobs in flight. When the request carries `maxJobs`, the response is a list of leased jobs, each in today's per-job shape. Without it, the response is today's single object, so a worker from before this item still leases a plain job. A run-intent job additionally requires a token that only a worker from this item advertises, so an older worker never leases one. An older worker would fail the job instead of leaving it queued: a device-cloud worker's `environment_refusal` refuses a spec that carries no `batch` (BE-0448), and a local worker finds no `cmd` to run.

`lease_job` first picks the anchor in the way it does today: the oldest queued job the worker can serve. When the anchor has a group, the call claims the anchor together with further queued jobs, up to `maxJobs` in all and within the headroom the caps leave. Each further job must carry the anchor's `group_id` and route key, or its base key on a worker that checks no routes. It then shares the anchor's verdict, so the worker can serve it. Each claim is the conditional `UPDATE ... WHERE status = 'queued'`, so a lost race skips one job and never fails the lease.

The anchor stays the oldest servable job, so the order between groups does not change. The followers may overtake older jobs of other groups, and `maxJobs` bounds how far. This is a deliberate cost. The saved uploads are worth more than strict age order inside the queue.

The scan that finds the anchor reads one candidate per pair of `group_id` and route key. Registration stores the route key with the job. Today `lease_job` loads every queued job, oldest first, and tests each with `can_serve`. A worker that cannot serve a group of five hundred scenarios thus tests five hundred jobs first. This item still reads every queued job outside a group. For group jobs, it reads the oldest queued job of each pair alone. A window function selects that job:

```sql
ROW_NUMBER() OVER (PARTITION BY group_id, route_key ORDER BY created_at)
```

SQLite and PostgreSQL both support window functions, so the SQLite gate exercises the same query. An index on `(status, group_id, route_key, created_at)` keeps the selection inside the database. In Python, `can_serve` then runs once per plain job and once per route key of a group. The queue length no longer sets the cost.

One candidate can stand for its pair because every job of a pair gets the same verdict. The verdict of `can_serve` reads the job's `capabilities` list, which the route key digests. The caps count against the org and the actor, which one request fixes.

A group needs the route key because its jobs do not share one required set. A scenario's own `cap:` and `setting:` tokens come from its text (BE-0467). Two scenarios of one group can thus need different tokens. Such a group splits into more pairs, and each pair costs one `can_serve` call. When every scenario needs its own set, the scan costs what today's scan costs. A pair has one verdict, so its oldest job never hides a younger job that the worker could serve.

A worker that checks no routes pairs jobs by base key instead. `can_serve` drops every route token from the required set when the worker advertises none (`checks_routes`), and a device-cloud worker advertises none. For such a worker, the jobs of a group with one base key share one verdict, however their `cap:` and `setting:` tokens differ. Its scan thus partitions by `group_id` and base key, and its followers match on the base key. A second index on `(status, group_id, base_key, created_at)` serves that scan. A suite whose scenarios need different `cap:` tokens still fills one bundle on a Device Farm worker. The lease marks each claimed job `unchecked_lease` by its own capability list, not the anchor's.

The invariant is that the route key covers every per-job input of the verdict. The key digests the list the row stores. A later change that adds an input must add it to the key. The base key follows the same rule for every input other than the route tokens.

The candidate query takes no lock, as today. PostgreSQL would refuse `FOR UPDATE` on it anyway, since it uses a window function. The claim then runs a second query on the chosen pair alone, with no window function. It claims the anchor together with its followers, so the whole bundle falls inside one counted transaction. That query takes the queued jobs of the pair oldest first. It stops at the smaller of `maxJobs` and the headroom the three caps leave, counting the anchor against both. The count that sets the headroom shares the claim's transaction, as the caps section describes. PostgreSQL locks them with `FOR UPDATE SKIP LOCKED`. SQLite claims each with the conditional `UPDATE`. Workers that chose the same pair thus spread across its jobs instead of racing for its oldest one. A bundle holds jobs of one route key, or of one base key on a worker that checks no routes. A group with more than one such key fills its bundles one key at a time.

The capability check stays in `can_serve` alone, so the five consumers keep one definition. The total number of queued group jobs stays unbounded. The `can_serve` calls in the lease scan follow the groups and their route keys, not their scenarios. The unroutable count keys queued rows: group rows by route key, as the lease scan does, and plain rows by capability list, as the lease-time count does. It runs one `can_serve` per key against each live worker and weights the result by that key's job count. A metrics scrape thus costs per route key, not per scenario. The database still reads every queued group row through the index.

A secret-store read failure during the lease fails every job of the bundle, where today it fails one job. The jobs share one org, and the read would fail for each of them alike.

A local worker still accepts a `maxJobConcurrency` of one only, as [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md) states. It leases bundles of one, and the grouping gives it nothing to share.

### Splitting a group at the concurrency caps

The global, per-user, and per-org caps of the server keep their meaning: the number of jobs one deployment, one user, or one org may run at once. For a group job they count leased jobs instead of queued ones. Two stores count jobs today, and the design fixes which one owns each count.

- **The jobs table owns the lease-time count.** It already holds every queued job, since the server runs no job itself. `lease_job` counts the jobs that hold a slot, globally, per org, and per user: leased jobs of every kind, plus the queued jobs outside a group that a live worker can serve. A queued plain job that no live worker serves holds no slot. It never expires, so it would block every group of its org while it sat there. The check runs once per distinct capability list among the queued plain jobs. A servable queued plain job keeps its slot against a group lease. The registry counts every unfinished plain job at registration, which is stricter, so it errs toward the cap. `lease_job` skips a group job when any of the three caps is reached, and stops adding followers to a bundle at the same point. The count and the claim run in one transaction, under a lock taken before the count. SQLite starts the transaction with `BEGIN IMMEDIATE`, which takes the write lock first. On SQLite the claim opens its own session, separate from the candidate scan's, so the scan holds no write lock. On PostgreSQL the claim's `FOR UPDATE SKIP LOCKED` and its `UPDATE` share the claim's transaction. The claim session issues that statement itself as the first statement on its connection, through `exec_driver_sql`. The engine keeps pysqlite's default transaction handling, because `isolation_level=None` plus a `begin` handler would change every other path on the shared engine. pysqlite opens no implicit transaction while one is open, so the statement holds until the commit. PostgreSQL takes a transaction-scoped advisory lock for each capped scope. The scopes are the deployment, the org, and the user. It takes them in that order. The candidate scan runs once and takes no lock. The lease then tries its candidates in order, each in its own transaction. When a cap stops the candidate, or its claim takes no job, the transaction ends and releases its locks before the next one. A lease thus holds the locks of one candidate at a time, and two leasers cannot deadlock. Each lock uses the two-key form, with the scope as the first key. Keys of different scopes thus never collide. A scope with no cap, and the user scope of a job with no actor, takes no lock. A second leaser counts after the first has claimed, so leasers never overshoot a cap together. A plain registration in flight still can. The registry admits it before the jobs table holds it. The overshoot is at most the registrations in flight. Plain jobs that no live worker could serve when the count ran can overshoot as well. Once a worker that serves them returns, `lease_job` leases them with no lease-time check.
- **Each job record gains an `actor` column**, so the per-user count needs no read of the spec JSON. Today the actor lives inside the spec alone.
- **The in-memory registry leaves queued group jobs out of its count.** The registry counts every registered job that has not finished, and plain jobs are capped on that count at registration. A group job enters that count when it is leased. Otherwise a queued group of two hundred scenarios would hold the caps before any worker took it, and the server would refuse every plain run of its org. The registry learns of a lease through a new read, not through the finish probe. The finish probe checks the ids the registry already counts and no others, and a queued group job is not among them. The new read counts the `leased` group rows of the deployment, of the org, and of the job's actor. It runs before `try_register` takes its lock, as the finish probe does. A registration thus waits on the database outside the lock, and never stalls another registration behind it. A lease that lands after the read is the in-flight overshoot the caps bullet already bounds. The two reads have opposite biases. A missed finish probe leaves a job counted, which errs toward the cap. A missed lease read would leave a job uncounted, which errs past it. A read that errors fails the registration with that error rather than a 429. A failing finish probe does the same today. `in_flight_by_org` and `active_jobs` read the registry's count too, and they add the same lease read, so the `bajutsu_in_flight_jobs` gauge keeps counting group jobs. A group that workers are running can fill its org's cap, as a `run-set` does today. Plain runs of that org then get status 429 until the group's queued jobs drain. A worker's next lease tends to take each freed slot first. Reserving headroom for plain jobs at lease time is a separate choice that this item leaves open.

The rest of the group stays queued and is leased as earlier jobs finish.

A group larger than the caps thus splits into bundles that run in turns. The bundles may go to different workers, and to different environments. Each worker shares uploads among its own bundle's jobs alone. A group served by three Device Farm workers therefore uploads the binary three times, where today it uploads it once per scenario.

Plain run, record, crawl, and triage jobs keep today's registration-time caps. Moving them is separate work.

### Sharing the upload on a Device Farm worker

Each leased job keeps its own slot, heartbeat, run-identifier checkpoint, and posted result, exactly as in [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md). The slots share two things. The worker stages the materials of every scenario in the bundle in one bundle-scoped directory, which the slots read. Each slot keeps its private workspace, for run output alone. One package archive is built from the bundle-scoped directory, and it is uploaded once. The directory is read-only to batch lifecycle hooks, so one job's hook cannot change another job's package. The worker removes it once the last slot of the bundle has finished. It replaces today's rule that a job's batch directory holds no other job's materials. One application upload carries the binary. The first slot to need either upload makes it, and the other slots wait on that upload. Each slot then builds its own test spec, which names its scenario and target, and schedules its own Device Farm run.

A batch lifecycle hook can make the package differ per job, because `before_submit` may set a launch environment that the provider writes into the packaged config. The package is therefore shared only when no hook sets a launch environment for the job. Otherwise each job builds its own package, and the application upload alone is shared.

The worker keeps the application upload identifier by compatibility key while any job with that key is in flight. A slot that frees up and leases a later job reuses it. The worker keeps a built package by `group_id` as well. A compatibility key spans groups, so two `run-set` calls with an edited scenario in between share a key but not a package. A package built from the config directory holds every scenario of the target, while a package built from shipped materials holds only its own bundle's scenarios. The worker thus reuses the package only for a job of the same group, and only when it holds that job's scenario. Otherwise the slot builds and uploads a new one. Once no job holds the key, the worker drops the entry, and a later lease uploads again. The package entry drops once no job of its group is in flight.

This design assumes that several Device Farm runs can reference one upload. Unit 4 confirms it against the service before relying on it. If a run cannot reuse an upload, the provider falls back to one upload per job. The result is then today's behavior, and only the saving is lost.

A failed shared upload fails each slot that waited on it, through the same path as a failed upload today. Each job then follows its own attempt count.

### Compatibility

`run-set` no longer accepts `environment`. [BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out.md)'s Replay tab sends it today. The tab stops sending the field in the same change. Its environment selector is also what turns the Run form into a fan-out, so the selector leaves the form (unit 5). An explicit fan-out control takes its place as the trigger. The tab offers it for a web target too, which the selector's own guard refused. The plain `POST /api/run` route is unchanged.

Unit 5 reserves `platform:` and `kind:` in the deprecated `--capabilities` override, as the worker already reserves `environment:`, `host:`, and the other routing prefixes. A worker started with `--capabilities platform:ios`, or that value in `$WORKER_CAPABILITIES`, starts today and refuses to start after unit 5. The refusal names the reserved prefix, and the operator removes the token. We refuse rather than drop it with a warning, since a silently dropped platform would leave the worker's capacity out of the routing the group jobs depend on.

[BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md) states that `run-set` requires an environment and rejects a missing one with a 400 response. This item removes the field instead, and the removal is intended. The rule existed because a `run-set` job carried a device-cloud `BatchRequest` from registration on. Under this item the job carries a run intent that any worker serving its platform can run.

### Prime-directive compliance

- **AI never judges.** No change touches how a verdict is computed, and no model call enters the run path.
- **Determinism first.** A bundle changes how jobs are claimed, not how any job waits or selects. The one new source of variation, the placement, is recorded in the run's provenance.
- **App-agnostic.** Nothing in a group, a key, or a bundle names an app. The compatibility key is a digest of what the target already declares.

### Work breakdown (MECE)

1. **Group registration.** The `job_groups` table with its org, key, and creation-time columns (the server-generated key is unique by construction, so it needs no unique index yet), the refusal of duplicate scenario names, the `group_id`, `compat_key`, `route_key`, `base_key`, and `actor` columns, a server-generated group key, `GET /api/run-set/<groupId>`, `POST /api/run-set/<groupId>/cancel`, its editor gate in `required_role`, and the machine allowlist entries for the register and read routes, with `machine_org` passed to both. Registration keeps today's truncation at the caps until unit 3 moves them to lease time. All-or-nothing registration and the idempotency and conflict rules both land with unit 3. The server must not answer a cap-truncated group as the whole request. A repeat of its key would return 200 and queue none of the scenarios the cap left out. Refusing whole requests while caps still bind at registration would cost some deployments `run-set`. A deployment whose cap is below its target's scenario count could register no group.
2. **Run intent.** The job shape that names a run intent, its conversion into a local run on a local worker and into a `BatchRequest` on a device-cloud worker, the token that keeps an older worker from leasing it, and `environment_refusal` accepting a run-intent spec, which carries no `batch`. The web path waits for unit 5.
3. **Grouped lease and caps.** `maxJobs` on the lease request, the multi-claim in `lease_job`, the scan of one candidate per group and route key (or base key, for a worker that checks no routes), with its two indexes, the list response, the worker client's use of its free slots, the lease-time caps counted on the jobs table, the registry's exclusion of queued group jobs, its probe of the jobs table for leases, the unroutable count grouped by route key, all-or-nothing registration, the `groupKey` field, the request-digest column and the unique index on the org and key on `job_groups`, and the idempotency and conflict rules.
4. **Shared upload.** The Device Farm provider's application upload held by compatibility key, the package reused only within its group and only when it holds the job's scenario, the bundle-scoped staging directory and its removal, the exception for a hook that sets a launch environment, and the fallback to one upload per job.
5. **Platform routing.** The group job's required tokens, with no `host:` token, the `kind:` tokens of an iOS group job and the workers that advertise them, the device-cloud worker advertising its platforms, `platform:` and `kind:` reserved in the `--capabilities` override, the per-job `scenario_route_needs` and `route_refusal` at registration (the worker read once per request, the route needs once per distinct scenario text), the environment rule's exemption for a run-intent job, the 400 for a request that still sends `environment`, the Replay tab no longer sending it and its fan-out control replacing the environment selector, and the provenance record. The web path lands here too: a web target needs no `subject.path`, and no device-cloud worker serves it. Until this unit lands, `run-set` keeps refusing a web target, as today, and keeps requiring `environment`. A group job then requires `environment:<provider>` and the run-intent token, so it routes to that environment as today.
6. **Documentation.** The `docs/` pages and their `docs/ja/` mirrors for the serve API, the worker, and routing, plus the affected passages of `DESIGN.md` and `docs/architecture.md`.

Out of scope: publishing a group's result as a GitHub check, sharing uploads between workers, running concurrent jobs on a local device, and lease-time caps for jobs outside a group. Checking a device-cloud worker's `cap:` and `setting:` support is out of scope too. Such a worker computes no route tokens today (BE-0467). The lease marks such a job unchecked (`unchecked_lease`), and its report says so. Routing group jobs to an `appium` grid and choosing devices by family on a device cloud are out of scope as well. So is routing the `runsOn` conditions that a device cloud cannot match (a listed `ios.kind`, `ios.model`, `ios.os`, `android.avd`, `android.apiLevel`).

## Alternatives considered

- **One Device Farm run per group, with the scenarios run in sequence.** This is the shape of [BE-0235](../BE-0235-aws-device-farm-submitter/BE-0235-aws-device-farm-submitter.md). It reserves the fewest devices, and the wall-clock time grows with the scenario count. BE-0336 left it for that reason, so this item keeps one run per job.
- **Bundle by the compatibility key alone, with no group key.** Jobs from any route that share a config and a binary would bundle. Nothing would tell the caller which jobs form a set, and a retried request could not be detected as a duplicate. The group key supplies both.
- **Keep the registration-time caps and let a retry register the rest.** A retry with the same key would add the scenarios the cap left out. A group would gain an "incomplete" state, and a caller would have to retry until the group fills. Lease-time caps make the same group run to completion with no retry.
- **Let the server build the package and upload for the group.** The server would hold the cloud credentials again, which [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) removed on purpose. The worker keeps both.
- **Test capabilities in the query.** The scan would then skip the jobs a worker cannot serve. The cost is a second implementation of `can_serve` in two Structured Query Language (SQL) dialects. That query would carry the two rules of `can_serve` and the cap counts:
  - the environment rule
  - the route-token rule for older workers
  - the caps

  The other four consumers would keep the Python implementation. The two copies would have to stay in step. The one-candidate scan bounds the cost by groups and route keys, with a single implementation.
- **Let the server write one token list per placement (`anyOf`).** A group job would carry a local alternative and one device-cloud alternative per provider. `can_serve` would accept any one of them. The server would then have to know every placement the deployment offers. Every reader of a job's tokens would also have to learn the alternatives. The worker already knows what it provides. Its advertised platforms keep `can_serve` a subset test and the request free of placement.
- **Keep the explicit environment and add grouping only.** This is the smaller change, and it needs no run intent. It leaves the second cost, that every caller must know the placement, and a CI workflow would still hard-code `devicefarm`.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Unit 1 — Group registration
- [ ] Unit 2 — Run intent
- [ ] Unit 3 — Grouped lease and caps
- [ ] Unit 4 — Shared upload
- [ ] Unit 5 — Platform routing
- [ ] Unit 6 — Documentation

## References

- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md): the per-scenario fan-out.
- [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md): the Device Farm worker and its slot model.
- [BE-0450](../BE-0450-worker-capability/BE-0450-worker-capability.md): the worker capability and `maxJobConcurrency`.
- [BE-0452](../BE-0452-target-config-restructure/BE-0452-target-config-restructure.md): the environment as a choice per run.
- [BE-0465](../BE-0465-serve-ui-device-farm-fan-out/BE-0465-serve-ui-device-farm-fan-out.md): the Replay tab's fan-out.
- [BE-0468](../BE-0468-runson-family-and-any-of/BE-0468-runson-family-and-any-of.md): the `anyof:` token and the four consumers of `can_serve`.
- `bajutsu/serve/operations/dispatch.py` (`start_run_set`), `bajutsu/serve/server/db/sql_repository.py` (`lease_job`), and `bajutsu/serve/state/job_registry.py` (`try_register`): the registration and lease code this item changes.
- `bajutsu/serve/capabilities.py` (`can_serve`), `bajutsu/serve/cli/worker.py` (`_advertised_capabilities`), and `bajutsu/serve/batch_provider/device_farm_batch_provider.py`: the routing and worker code this item changes.

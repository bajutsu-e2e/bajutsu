**English** · [日本語](BE-0449-server-runs-no-jobs-ja.md)

# BE-0449 — The server queues jobs and workers run every one

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0449](BE-0449-server-runs-no-jobs.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0449") |
| Implementing PR | [#2167](https://github.com/bajutsu-e2e/bajutsu/pull/2167) (Unit 1, part 1 of 3), [#2168](https://github.com/bajutsu-e2e/bajutsu/pull/2168) (Unit 1, part 2 of 3), [#2170](https://github.com/bajutsu-e2e/bajutsu/pull/2170) (Unit 1, part 3 of 3), [#2171](https://github.com/bajutsu-e2e/bajutsu/pull/2171) (Unit 2), [#2172](https://github.com/bajutsu-e2e/bajutsu/pull/2172) (Unit 4), [#2173](https://github.com/bajutsu-e2e/bajutsu/pull/2173) (Unit 3), [#2182](https://github.com/bajutsu-e2e/bajutsu/pull/2182) (Unit 5, part 1 of 2: a lossless local start), [#2183](https://github.com/bajutsu-e2e/bajutsu/pull/2183) (Unit 5, part 2 of 2: `run` on a worker), [#2184](https://github.com/bajutsu-e2e/bajutsu/pull/2184), [#2185](https://github.com/bajutsu-e2e/bajutsu/pull/2185), [#2186](https://github.com/bajutsu-e2e/bajutsu/pull/2186) (Unit 6, through BE-0448), and PR_PLACEHOLDER13 (Unit 7) |
| Topic | Hosting the web UI |
<!-- /BE-METADATA -->

## Introduction

The server becomes a control plane and nothing more. It accepts requests, keeps the queue, and serves the UI and reports, but it never executes a job. A **job** here is a unit of work that goes through the queue: `run` (with its build phase), `run-set` (the Device Farm fan-out), `triage`, `record`, and `crawl`. A worker executes every job, on iOS, Android, and the web alike. On one machine, an operator starts two processes, `bajutsu serve` and `bajutsu worker`. Without a server, `bajutsu worker --once` executes a single job spec, and `bajutsu run` becomes a thin wrapper around that one-shot mode.

Today the server has two ways to run a job. The local backend runs it inside the server process, and the hosted backend queues it for a worker. This item removes the first way, so one execution path remains and every feature is written once. The interactive helpers that touch a device or a model outside the queue (capture sessions, enrich, the screen probe of `doctor`, the simulator list, and screenshots) are not jobs, and moving them is separate work.

## Motivation

The local backend takes `LocalExecutor` by default ([`serve_state.py:138`](../../bajutsu/serve/state/serve_state.py)). The server backend picks `DbQueueExecutor` when `BAJUTSU_DATABASE_URL` is set and falls back to `LocalExecutor` otherwise ([`serve/__init__.py:582`](../../bajutsu/serve/__init__.py)). The local executor starts a thread per job, and `run_job` spawns `python -m bajutsu run` (or `record`, `crawl`, and so on) as a subprocess ([`local_executor.py`](../../bajutsu/serve/executor/local_executor.py), [`jobs.py`](../../bajutsu/serve/jobs.py)). The worker path reaches the same `run_job` through `execute_job_spec` ([`worker_job.py`](../../bajutsu/serve/server/worker_job.py)), but around it sits a second set of mechanisms: leases, heartbeats, presigned uploads, and a job spec that must round-trip through the database.

Keeping both paths makes every feature a two-path feature, and the second path lags. Three cases show it.

- **The Device Farm dispatch works only in the server process.** The worker path builds its own state without a package root, so a Device Farm job leased from the queue fails validation ([`jobs.py`](../../bajutsu/serve/jobs.py)).
- **The per-job artifact overrides of BE-0431 work only on the worker path.** A single-process server refuses them, and `run-set` refuses them on every topology because its fan-out does not yet run on the split ([`dispatch.py`](../../bajutsu/serve/operations/dispatch.py)).
- **The live crawl graph assumes a shared filesystem**, which the worker split breaks. [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md) recorded that gap and was deferred, and [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) assumed crawl never leaves the control plane.

The observable difference has three parts. The server process spawns no `bajutsu run`, `record`, or `crawl` subprocess, and a test can assert it. A job submitted while no worker is running stays queued, and the UI says that no worker is available, in place of the job running inside the server. And a feature that works on a hosted worker, the Device Farm dispatch for one, works on a single machine with no extra code.

## Detailed design

### Roles

| | Server (`bajutsu serve`) | Worker (`bajutsu worker`) |
|---|---|---|
| Accepts requests, serves the UI and reports | yes | no |
| Keeps the queue, sessions, configuration binding, and run index | yes | no |
| Executes a job (device, browser, build, AI call, cloud submission) | never | always |
| Holds the AWS role of a Device Farm dispatch | no | yes |
| Holds the AI key and the secrets a user sets in the UI | yes, write-once as today | receives them per job |

### Secrets and credentials

Today the UI writes the AI key and the `${secrets.X}` values into the server's environment, and a spawned run inherits them ([`jobs.py`](../../bajutsu/serve/jobs.py)). A worker shares no environment with the server, so the values must travel. The server keeps them where it keeps them now, write-once ([BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md)), and hands the values a job needs to the worker in the response to its lease. The values never enter the stored job spec, which the queue keeps in the database. The AWS role of a Device Farm dispatch does not travel: the worker holds it in its own environment, as the Device Farm dispatch item describes.

### One machine, two processes

An operator on one machine starts `bajutsu serve` and `bajutsu worker`. The server alone executes nothing, and it says so: a queued job with no live worker carries a "no worker available" notice, so an operator who forgets the worker does not wait in silence.

The hosted deployment already needs a queue, sessions, and a place for artifacts. A local start must supply them without cloud services. Attaching a repository to the local start changes more than storage, so the defaults are listed here.

- **The queue store** is SQLite by default, a file beside the runs directory, so queued jobs survive a restart. `BAJUTSU_DATABASE_URL` selects PostgreSQL as it does today. A worker reaches the server over HTTP and never opens the database, so the concurrent writers are the server's own request threads. SQLite skips `FOR UPDATE SKIP LOCKED`, so every status transition becomes a conditional `UPDATE` that checks the row count, each with its own predicate: the lease on `status = 'queued'`; the heartbeat, the result, and the cancel on `status = 'leased' AND leased_by = :worker`; the reclaim on `status = 'leased' AND leased_at < :cutoff`. Alternatively, each transition runs under `BEGIN IMMEDIATE`. A test leases concurrently on a file-backed SQLite and races a heartbeat against a reclaim. The engine sets write-ahead logging and a busy timeout.
- **Migrations** run automatically on the default SQLite, since the server never runs Alembic today.
- **The secrets key**, `BAJUTSU_SECRETS_KEY`, is mandatory for a database-backed start today. A local start generates one and persists it beside the runs directory. The file is created atomically with owner-only permissions (`0600`), as a regular file that is not a symlink, and the start fails on an existing path that is unsafe, in the way `bajutsu/common/run_meta/artifact_perms.py` treats secret-bearing files. A world-readable key on a shared host would undo BE-0136.
- **`hosted`** stays a deployment setting. A repository must not imply it, or the file browser of BE-0108 disappears and the device takeover of `record` (BE-0185) is refused for an author who sits at the device.
- **Run history** comes from the database window once a repository exists, so runs that `bajutsu run` wrote from the command line, and runs already under `runs/`, would vanish from the local UI. The server indexes the run trees under `runs/`, both the existing ones and those that `bajutsu run` writes from the command line, into the runs table at start and on a rescan. The file store mounts the runs tree at the default org's `artifacts/` prefix, so their artifacts resolve. Targets are org-scoped once a repository exists, and a local start uses one default org.
- **The artifact store** gets a filesystem implementation. The object store accepts only `s3://` and `gs://` today ([`object_store.py`](../../bajutsu/serve/server/object_store.py)). A `file://` store lets the worker write a run tree and the server read it. The worker still reaches it through the signed-URL interface of [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md), and the server serves those URLs for this store. Each URL is signed with an HMAC key that the server generates and persists under the same rules as the secrets key (atomic `0600` creation, regular file, unsafe existing path rejected), expires, binds the object key and content type into the signature, and is confined to the store root by the existing key validation. The routes accept nothing else.

The local backend and the server backend then differ in these defaults and nothing else. The `--backend` flag loses its meaning and is deprecated in one release and removed in the next.

### Cancellation across the split

Cancelling a job reaches only the server's in-memory `Job` today, and for a queued job that `Job` holds no process. Locally the server stops a `run` gracefully (BE-0370) and kills the process group of every other kind. On a worker, a 409 from the heartbeat makes the worker wait for the run to finish. The split needs a cancel path of its own.

- Cancelling a queued job marks its row cancelled, and the lease skips it. The cancel finds the job in the repository, so a job queued before a restart can still be cancelled.
- Cancelling a leased job keeps the row leased and sets a cancel-requested flag, which the heartbeat response returns with a 200. The worker then cancels its own `Job`: gracefully for `run`, and by a process-group kill for the other kinds. The result route accepts a cancelled outcome from the lease holder, and only that moves the row to cancelled.

### Live output across the split

The hosted model delivers a worker's console log after the job completes ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md), [`post_completion_logbus.py`](../../bajutsu/serve/server/post_completion_logbus.py)). That was acceptable while local use bypassed it. Once local use goes through a worker, the console must be live again, and `crawl` needs its exploration graph while it grows. The worker therefore streams: it posts log chunks and live artifact chunks to worker routes that check the lease owner, as the heartbeat route does, and the server publishes them to a log bus that accepts writes (the in-memory bus for a single server process, or `RedisLogBus` across replicas), in place of the post-completion bus, which ignores publishes, and relays them to the browser over Server-Sent Events (SSE). The uploaded `console.log` stays the durable copy. The log stream comes before any kind moves, so a moved `run` keeps a live console. The artifact stream arrives with `crawl`, and its first consumer is the crawl graph, which is the scope [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md) left open.

`record` also hands control to a person: the server writes the person's response to the run's standard input today (BE-0179). A worker holds that input, so the response travels the other way, from the server to the worker, on the heartbeat response, and the worker writes it. The device takeover then follows the worker's host and not the server's.

### The one-shot worker

`bajutsu worker --once <spec>` executes one job spec without a server and exits with the job's exit code. The spec has the shape the queue already stores (`job_spec` in `worker_job.py`). The worker reads its `worker.yaml` through `--worker-config`, runs the worker capability check, executes the job through `execute_job_spec` with local input and output in place of HTTP and signed URLs, and, in one-shot mode, spawns the child with inherited standard input, output, and error in place of the piped, merged capture a leased job uses. It takes the run id from the run tree the child wrote and not from the PASS line, and it passes its resolved `--worker-config` path to the internal run entry point it spawns. A spec that names an uploaded bundle or artifact overrides is rejected with a clear message, because those arrive by signed URL and a one-shot run has none. `worker --once` maps SIGINT and SIGTERM to the same cancel a leased job receives from its heartbeat. The same command serves tests, investigations, and the wrapper below.

### `bajutsu run` as a thin wrapper

`bajutsu run` builds the job spec from its arguments and calls the one-shot worker path, so the command line and a leased job run through one implementation. There is a trap to avoid. `run_job` spawns whatever `job.cmd` holds, and today `run_command` emits `python -m bajutsu run`, so a public `run` that calls `run_job` would spawn itself. The body of the current `run` therefore moves behind an internal entry point, and the spec builders emit that entry point. A spec queued before the change still names `-m bajutsu run`; it enters the wrapper, which spawns the internal entry point once, so it does not recurse.

The wrapper keeps the public behaviour:

- The spec it builds leaves `udids` and `build` empty, so the command line keeps its own device acquisition.
- The internal process inherits standard input, output, and error, so the PASS or FAIL line stays on standard output and progress stays on standard error.
- SIGINT and SIGTERM go to the same cancel path, so an interrupt does not orphan a run that keeps driving the device.
- The wrapper exits with the child's code, or 128 plus the signal number after a signal.

The parity test runs `bajutsu run` as a subprocess, since the existing suite calls it in process and would not see the extra layer. The layer costs one process, which the item accepts for a single pipeline.

The wrapper is a local command: it rejects a `worker.yaml` whose `environment` is not `local`. A Device Farm submission is the job of a worker whose environment is `devicefarm`, and `bajutsu worker --once` with that file submits a single Device Farm job spec without a server.

### The executor seam

During the transition, one routing executor chooses per job kind: `DbQueueExecutor` for the kinds that have moved, and `LocalExecutor` for the rest. At the end, `RunExecutor` has one implementation, `DbQueueExecutor`. `LocalExecutor`, the override-refusal branch of `dispatch` that exists for the single-process server, `_run_batch_job` in the server, and `register_batch_providers` on the server are removed. So are the settings that existed only to bound the server-side Device Farm count: the target's `cloudBatchBudget`, the request's `deviceBudget`, the registry's `max_concurrent_batch`, and `try_register(device_budget=…)`. The Device Farm dispatch item delivers that removal, after a one-release deprecation with a notice that names `maxJobConcurrency`, because the config schema rejects an unknown key and a straight removal would break existing files. The number of concurrent Device Farm runs is then the `maxJobConcurrency` of the workers.

### Order of work

The end state is that the server executes nothing, and the path to it moves one job kind at a time, each step reviewable on its own. The local defaults, the cancel path, the one-shot worker, and the log stream come first, because a moved kind needs all four. `run` moves next, then `run-set` (the Device Farm fan-out), then `triage`, and `record` and `crawl` last, because they need the artifact stream and the handoff relay. `LocalExecutor` is removed only when the last kind has moved.

### Boundaries

This item does not change what a job does, how a verdict is decided, or the run tree. It leaves the CLI-only commands that never went through the server as they are, and it leaves the interactive helpers listed in the Introduction on the server for now. A single-machine UI runs one job at a time per worker process, because a local worker accepts a `maxJobConcurrency` of one; several worker processes, each with its own device, are the workaround until per-job device allocation lands. Four points are open for review:

- The registry's global, per-user, and per-org caps count queued jobs, and `run-set` returns a partial success once a cap is hit. Whether cloud jobs are exempt from the caps is left open.
- The signed URLs of the filesystem store are served by the server, so the server must be reachable from the worker on the same machine; a local worker uses the loopback address.
- Two workers leasing at once from one SQLite file rely on the conditional updates above, and the concurrent-lease test is the check.
- The server adds a `host:<os>` token to the required set of a job whose driver runs on one host only, so a worker that does not yet advertise `host:*` would not match it. Upgrade the workers before the server.

### Prime-directive compliance

- **AI never judges.** Moving the AI calls of `triage`, `record`, and `crawl` to the worker changes where they run and not what they decide. The `run` path stays free of model calls, and the verdict still comes from machine-checkable assertions.
- **Determinism first.** One execution path replaces two, so a fix or a feature lands once, and the no-worker notice replaces a silent wait.
- **App-agnostic.** The item touches the server and the worker, and no application configuration changes.

### Work breakdown (MECE)

1. **Local defaults.** Add the filesystem object store with signed URLs, the SQLite default with a safe conditional lease, automatic migrations, a generated secrets key, `hosted` kept independent, and a run history that keeps command-line and existing runs, so `bajutsu serve` starts with no cloud service.
2. **Cancellation across the split.** Mark a queued job cancelled, return a cancel flag on the heartbeat response, and cancel the worker's own job.
3. **One-shot worker and wrapper.** Add `bajutsu worker --once`, the internal run entry point that the spec builders emit, and the `bajutsu run` wrapper with its stream, signal, and exit-code rules.
4. **Live log stream and the routing executor.** Add the log stream route and the server relay, and route each job kind to `DbQueueExecutor` or `LocalExecutor` during the transition.
5. **Move `run`.** Route `run`, with its build phase, through `DbQueueExecutor` only, relay the AI key and secrets in the lease response, and add the no-worker notice.
6. **Move `run-set` (the Device Farm batch).** Delivered by the Device Farm dispatch item: remove the in-server dispatch, and retire the server-side budget settings after a one-release deprecation.
7. **Move `triage`.** Run it on the worker.
8. **Move `record` and `crawl`.** Run them on the worker, with the live crawl graph on the artifact stream and the handoff relay for `record`.
9. **Remove the local executor.** Delete `LocalExecutor` and the in-process branches, deprecate `--backend`, and update `docs/self-hosting.md`, `docs/architecture.md`, and `docs/cli.md` with their Japanese mirrors.

## Alternatives considered

| Option | Summary | Why not |
|---|---|---|
| Keep the local executor | The server runs jobs itself for a single machine. | Two execution paths remain, and every feature still lands on one path before the other. |
| The server embeds a worker | `bajutsu serve` runs a worker in the same process. | It adds a second startup mode and hides the boundary this item draws; two processes keep one shape for every deployment. |
| Leave `bajutsu run` as it is | `run` keeps its own pipeline beside the worker's. | The command line and a leased job would keep two implementations that can diverge. |
| Move only the run kinds | `record` and `crawl` stay on the server. | The server would still execute jobs, and the local executor could not be removed. |
| Move every kind at once | One change moves all kinds and the live streams. | The change is too large to review, and a regression in one kind blocks the rest. |
| Retire the UI secret endpoints | Operators set secrets in the worker's environment only. | Users who set secrets in the UI would lose that path; relaying per job keeps it. |

## Progress

> Keep this section current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one entry per unit of work), and the log records what changed and when, oldest
> first, each with a link to its PR.

- [x] Local defaults
- [x] Cancellation across the split
- [x] One-shot worker and wrapper
- [x] Live log stream and the routing executor
- [x] Move `run`
- [x] Move `run-set` (the Device Farm batch)
- [x] Move `triage` (on a local start; hosted triage and same-machine affinity are follow-ups, see the log)
- [ ] Move `record` and `crawl`
- [ ] Remove the local executor

Log:

- [#2167](https://github.com/bajutsu-e2e/bajutsu/pull/2167) (Unit 1, part 1 of 3: the queue store) — every job transition in `SqlRepository`
  (lease, heartbeat, result, reclaim) is now one conditional `UPDATE` whose row count decides the
  race, on SQLite and PostgreSQL alike, in place of a select-then-write that skipped its row lock on
  SQLite. A file-backed SQLite engine sets write-ahead logging and a busy timeout, and
  `repository_from_env(default_url=…)` migrates the default database to the latest revision before
  use, while a configured `BAJUTSU_DATABASE_URL` is still never migrated by the server. Tests lease
  concurrently on a file-backed SQLite and race a heartbeat against a reclaim. The rest of unit 1
  (the generated secrets key, the `file://` artifact store with signed URLs, and the local start
  wiring) follows.
- [#2168](https://github.com/bajutsu-e2e/bajutsu/pull/2168) (Unit 1, part 2 of 3: keys and the file store) — `local_keys` generates the secrets
  key and the URL-signing key on first use and keeps them in `.bajutsu/` beside the runs directory.
  Each key file is written to an `O_EXCL | O_NOFOLLOW` staging file with mode `0600`, synced, and
  hard-linked into place, so a concurrent start reads the winner's key, and an existing path that is
  a symlink, not a regular file, owned by another account, or open to group or other is refused.
  `fernet_from_env_or_local` prefers `BAJUTSU_SECRETS_KEY` and falls back to the local key.
  `FileObjectStore` implements `ObjectStore` on a directory, confines every key to the root
  (including through symlinks), and writes atomically with owner-only modes. Its signed URLs point at
  new `GET` and `PUT /api/store/objects/{key}` routes on the FastAPI app, which the signature alone
  authorizes: an HMAC-SHA256 over the method, the key, the content type, and the expiry. The routes
  exist only when the object store is a `FileObjectStore`, and the default start is unchanged until
  part 3 wires it.
- [#2170](https://github.com/bajutsu-e2e/bajutsu/pull/2170) (Unit 1, part 3 of 3: the local start) — `bajutsu serve --backend server` now
  starts with no cloud service. With neither `BAJUTSU_SERVER_STORE` nor `BAJUTSU_DATABASE_URL`
  set, the server keeps a migrated SQLite queue at `.bajutsu/queue.db`. The secrets key is
  `BAJUTSU_SECRETS_KEY` when set, else generated into `.bajutsu/secrets.key`. A configured database
  without `BAJUTSU_SECRETS_KEY` still fails to start. A deployment with a bucket and no database
  keeps its in-process shape, since a SQLite file per replica would split the queue. With no
  bucket, the artifact store is a `FileObjectStore` that keeps the bucket's key layout under
  `.bajutsu/objects/` and mounts the runs tree at the default org's `artifacts/` prefix. Runs that
  `bajutsu run` wrote resolve without moving, and the default org's store cannot name another
  org's runs or a store folder. The store signs with the local key and addresses the server's
  loopback URL, so a local start refuses `--port 0` and switches itself to the FastAPI transport,
  which serves the signed-URL routes. `hosted` now reads `BAJUTSU_HOSTED` and, when unset, follows
  whether the deployment names a bucket, so a local start keeps the file browser and the device
  takeover. At startup the server indexes every run tree under `runs/` into the runs table under
  the default org, through a new insert-only `record_run_if_absent`, so a completing job's row is
  never overwritten. An admin's `POST /api/runs/rescan` does the same on demand and answers `409`
  without the local layout. Unit 1 is complete.
- [#2171](https://github.com/bajutsu-e2e/bajutsu/pull/2171) (Unit 2) — a cancel now reaches a job wherever the split put it. The cancel route
  looks the job up in the jobs table first, so it still reaches a job queued before a restart. A job
  with no row stops in process, as before. A queued job becomes `cancelled`
  through a conditional `UPDATE` on `status = 'queued'`, so no lease returns it. A leased job keeps
  its lease and gets a new `cancel_requested` column (migration `0022`). The heartbeat then answers
  `{"ok": true, "cancel": true}`, and the worker cancels its own `Job` through `jobs.cancel_job`:
  gracefully for `run`, and by a process-group kill for the other kinds. `execute_job_spec` hands
  the rebuilt `Job` to the heartbeat loop before the run starts, and a cancel during the bundle
  fetch skips the run. The result route records `"cancelled": true` from the lease holder alone,
  by a conditional `UPDATE` on `status = 'leased' AND leased_by = :worker`. A cancel after the job
  finished changes nothing and reports the terminal status. A reclaimed lease that carries a cancel
  ends `cancelled` rather than re-queued, even at the attempt cap. `cancelled` joins the terminal
  statuses that release a job's concurrency slot and end its log stream, and the `0022` downgrade
  folds it into `failed`. A cancel that lands between a job's registration and its enqueue reaches
  the row either way: `DbQueueExecutor.dispatch` cancels the row of a job already cancelled in
  memory, and the cancel route re-reads the jobs table after its in-process cancel. A signed-in
  caller may cancel a job, in process or in the jobs table, only if the job's org is the caller's
  active org or one of the caller's memberships. The route answers `404` for any other job, and the
  job view stays unscoped for people. The route also scopes a machine principal to its org, as the
  job read does, but the gate does not admit a machine to the cancel route, so that check is
  defensive. A leased-job cancel needs workers at this version, since
  an older worker ignores the heartbeat's `cancel`. The SIGINT and SIGTERM mapping for
  `worker --once` arrives with unit 3.
- [#2172](https://github.com/bajutsu-e2e/bajutsu/pull/2172) (Unit 4) — a queued job's console is live again. The worker posts its log lines
  to a new `POST /api/worker/log` about once a second. It posts the tail before its result, within
  five seconds. A stateful line masker hides credential shapes. It keeps a private key block masked
  from BEGIN to END, across lines and batches. The server runs the per-line backstop again on each
  posted line. Handoff request lines stay out of the live stream. A new read-only
  `Repository.holds_lease` lets the route accept a batch from the lease holder alone. Any other
  worker gets `409` and drops the job, as on a heartbeat `409`. One post carries at most 500 lines.
  The server cuts a line past 8 KiB. Past 50,000 live lines or 16 MiB for one job, the server
  publishes one truncation notice and drops the rest. Each server process keeps its own count. The worker drops a
  batch it cannot post, and caps its queue of unsent lines. It stops streaming on a `401`, `404`,
  or `405` from an older control plane. With a repository, the server swaps the post-completion bus
  for a new `QueueLogBus`, on the local start too. `QueueLogBus` publishes the posted lines to an
  in-memory bus. The new `BAJUTSU_REDIS_URL` switches that bus to `RedisLogBus`. `RedisLogBus` now
  pushes a batch in one command. It also bounds the list's lifetime on every write. The in-memory
  bus drops a finished job's channel once 256 later jobs have finished. The result route still
  closes the stream. An idle stream also polls the jobs table. A job that ends with no close
  reaching the bus thus still ends its stream. A viewer who opens a finished job reads the uploaded
  `console.log`, as does a live viewer who received no line. A failing live bus falls back to that
  post-completion read. `Job.kind` derives the job kind from `batch`, or from the subcommand that
  `cmd` spawns. `RoutingExecutor` sends a kind in `queued_kinds` to `DbQueueExecutor`. A job of
  unknown kind goes there too. Any other job goes to `LocalExecutor`. The server queues every kind,
  which keeps today's behavior. The per-job artifact override refusal now asks where a `run` job
  routes, not what type the executor has. The bucket-only shape and `--backend local` keep their
  behavior. The worker now writes `console.log` into the run tree before
  that tree uploads, through a new `before_upload` callback on `execute_job_spec`. The worker used
  to write the file after the upload, so `console.log` never reached the artifact store. The live artifact stream for
  `crawl` arrives with unit 8.
- [#2173](https://github.com/bajutsu-e2e/bajutsu/pull/2173) (Unit 3) — `bajutsu run` is now a thin wrapper over the one-shot worker path. The
  body of the command moved behind an internal entry point, `python -m bajutsu.run.internal`, a
  module of its own rather than a hidden subcommand. The module stays out of `bajutsu --help` and
  the BE-0101 command list, and its process imports the run body alone. `run_command`, the `triage
  --rerun` command, and the MCP `run` tool emit it. `Job.kind` and `_with_worker_config` recognise
  the module and still accept the `-m bajutsu run` that a spec queued before the change names.
  `_local_interpreter` rewrites that legacy form to the internal module, so such a spec runs in one
  process, as it did when it was queued. The wrapper shares the body's signature, so the two have
  one flag surface, and it passes on every flag the caller typed, whatever its value. A default or
  an environment value is left for the run to resolve. The parser now checks `--label`, so the
  wrapper refuses an over-long label in the caller's words. `bajutsu worker --once <spec.json>` runs
  one spec through `execute_job_spec` with a no-op `LocalWorkerIO` and no repository. A new
  `inherit_stdio` mode of `run_job` spawns the child on the caller's standard streams, in a session
  of its own (the public `bajutsu run` alone also keeps the caller's `PATH`), and reads the run id from the new run tree. The caller holds a liveness pipe
  open while it waits. When the pipe reaches end of file, the internal run sends itself SIGTERM, so
  a wrapper killed by SIGKILL does not leave a run driving the device. `--once` refuses a bundle,
  artifact overrides, a Device Farm batch, a non-`local` worker file (naming BE-0448), mistyped
  `udids` or `materials`, and materials that would overwrite a different file in the current
  directory. SIGINT, SIGTERM, and SIGHUP become the unit 2 cancel. A second SIGINT kills the child's
  group at once. SIGTSTP stops the child's group and then the wrapper, and SIGCONT continues both. A
  signal the caller already ignores stays ignored. The exit code is the child's, or 128 plus the
  signal number. A `--step` prompt now polls the run's cancel source while it waits on the terminal,
  so a Ctrl-C at the prompt still ends the run with its report written. A Ctrl-C or a SIGTERM on
  `bajutsu run` now exits 130 or 143 where the single process exited 1. Ctrl-C no longer reaches the
  run's driver children directly, and a background `--step` run no longer stops on terminal input.
  Subprocess parity tests cover a pass, a fail, a usage error, both signals, a SIGKILLed wrapper, a
  second Ctrl-C, Ctrl-Z, the `--step` prompt on a pseudo-terminal, and a pre-change spec under
  `--once`. A round-trip test renders every `run` option and parses it back with the internal entry
  point. The in-process CLI tests now invoke the internal entry point. Workers must be upgraded
  before or with the control plane: a worker built before this change does not map the queued
  `-m bajutsu.run.internal` onto its own interpreter, so it fails every `run` it leases.
- [#2182](https://github.com/bajutsu-e2e/bajutsu/pull/2182) (Unit 5, part 1 of 2: a lossless local start) — a local start now keeps the
  features a `--backend local` user has, so the local backend can become this shape in part 2. One
  gap stays open until part 2: a local start queues every job kind, and a queued job does not get
  the stored secrets until the lease relays them. The
  FastAPI app serves the `local_only` routes on any deployment that is not hosted: capture mode,
  the live step picker, and `/api/ant/login`. It adds a screenshot handler that mirrors the stdlib
  one, and the boot read reports capture as available. A hosted deployment still refuses those
  routes. `SecretStore` gains `values_for_job`, the one plaintext read. `DbSecretStore` decrypts the
  org's rows, and a row the current key cannot decrypt is skipped with a warning. `EnvSecretStore`
  names the secrets it wrote, under a lock. The in-process job thread (`jobs.run_job_in_process`)
  reads the values into a new `Job.secret_env` through `jobs.job_secret_env`, and a store error
  fails that one job with a line saying why. `_spawn_env` merges that field under the
  provider overlay. The field stays out of the job spec and out of `repr`. The Git credential stays
  out of it too, since a job never fetches. A test flags any direct call of `values_for_job` or
  `job_secret_env` outside `serve.jobs`, so an HTTP handler that reached for a value would fail it. Part 2 relays the
  same read in the lease response. For the default org, a local start keeps scenarios in the bound
  config's tree and baselines in the `--baselines` directory, so a saved scenario lists back and an
  approved baseline is the one a run reads. `FileObjectStore` gains further mounts, and the local
  start mounts the `--baselines` directory at `baselines/`, so a worker's signed download reads
  the baselines an approve wrote. The start refuses a `--baselines` directory that holds the runs
  or state directory, and no org may take a slug the default org's layout uses (`artifacts`,
  `baselines`, `scenarios`, `uploads`), through the API or `orgs:` alike. It also keeps the provider settings in
  `provider-settings.json`, so both backends share one copy instead of a database copy that drifts.
  Other orgs keep the bucket layout. FastAPI, uvicorn, SQLAlchemy, Alembic, and `cryptography`
  move into the base dependencies. The `server` extra keeps boto3, and the `db` extra keeps psycopg.
  An install command that names either extra still resolves. The CLI still imports none of the
  server stack. The `--asgi` install check is gone, and the server backend's install hint now names
  only the cloud clients. The local backend's executor and defaults are unchanged.
- [#2183](https://github.com/bajutsu-e2e/bajutsu/pull/2183) (Unit 5, part 2 of 2: `run` on a worker) — the local backend is now the local start. Both
  backends build the server shape, and they differ in the kinds they queue. `local` queues `run`
  with its build phase (`LOCAL_QUEUED_KINDS`) and runs every other kind in the server's process.
  `server` queues every kind. The flag stays, and its removal is still unit 9's. A local start now
  also keeps the startup config's directory, its Git provenance, and the theme settings. The lease
  response carries `secret_env`. That field holds the leased job's org's stored secrets, under the
  variables a job reads them from. The server reads them at lease time through
  `jobs.lease_secret_env` and never stores them in the spec. A lease taken by a browser or machine
  login gets none. A store the server cannot read fails the job rather than run it without them.
  The worker lays the values over the run's environment, never its argv. A job that ships no files
  now names its config directory in the spec's `cwd`. A worker on the same machine builds and runs
  it from there. It writes `console.log` into the runs directory the run wrote. A Git credential
  set in the UI now reaches the server's own private-repository fetch through
  `jobs.stored_git_credential`, even from the database store. It ranks after a GitHub App and ahead
  of an ambient token. A queued job with no live worker able to serve it carries a notice.
  `Repository.lacks_live_worker` decides it from the lease timeout and the capabilities alone. The
  job view returns it as `notice`, and the live stream sends a `notice` event each time it
  changes. The Replay and Record status lines show it. `make serve` starts a local worker beside the
  server, from a scratch workspace, with the same token. The worker stops when serve exits.
  `SERVE_WORKER=0` turns it off. A SIGINT or SIGTERM to the leasing worker now cancels its job,
  posts the cancelled result, and exits, so the queue does not re-run an orphaned job. `serve.sh`
  stops the worker first and waits out the grace window. The worker runs jobs in a temporary
  directory of its own unless `--workdir` names one, and no longer in its current directory. It
  records the directory's owner, and a later worker removes a directory whose owner has exited. It
  also records the running job's process group there, so `serve.sh` can end the run when it must
  stop the worker outright. A job that names its files by path fails with a clear message on a
  worker that lacks them. Neither a stored secret nor a lease may set a loader, interpreter, shell, or
  `BAJUTSU_*` variable; the server and the worker log a dropped name without its value.
  `bajutsu serve --port 0` exits 2 with the reason. A known gap stays for a follow-up: such a job
  carries no capability that the server's machine alone advertises, so a remote worker may lease
  it and then fail it.
  An end-to-end test runs a queued `run` through the real worker loop on the fake backend.
- [#2184](https://github.com/bajutsu-e2e/bajutsu/pull/2184), [#2185](https://github.com/bajutsu-e2e/bajutsu/pull/2185),
  and [#2186](https://github.com/bajutsu-e2e/bajutsu/pull/2186) (Unit 6) —
  [BE-0448](../BE-0448-devicefarm-worker-dispatch/BE-0448-devicefarm-worker-dispatch.md) delivers this
  unit. The server no longer runs the Device Farm fan-out. It registers no batch provider and holds no
  AWS credential. Every `run-set` job waits in the queue for a Device Farm worker. Such a worker's
  `worker.yaml` names `environment: devicefarm`. That worker submits the run and stores its Amazon
  Resource Name (ARN) through the server over HTTP. It then polls the run and posts the verdict. The
  worker's `maxJobConcurrency` caps the number of such jobs it holds at once. Four server-side budget
  settings remain for one release as deprecated. The two an operator writes log a notice that names
  `maxJobConcurrency`, once per server process. Until
  their removal, they still cap the fan-out.
- PR_PLACEHOLDER13 (Unit 7) — a local start now queues `triage` too (`LOCAL_QUEUED_KINDS`). A
  worker runs it, and the server spawns no triage process. The job names the run directory by its
  absolute path on the server's disk. It names the bound config and the scenario source the same
  way. A worker on the same machine runs the job from the config's directory. It writes
  `triage.json` into the server's run directory, and the UI reads the file from there. A worker on
  another machine fails the job with the unit 5 message. The job no longer carries an uploaded
  bundle, since it reads none of it. An `--ai` triage gets the AI key from the lease, like every
  queued job. The server's credential check now counts a key the org stored in the UI. It reads the
  masked description alone, and it counts the key when the key travels under the variable the
  target reads. Before, a local start refused `--ai` triage without the key in its own environment.
  A triage job no longer takes a run path in its output as its own run id. Otherwise its tail would
  record the run it read once more. On a worker, it would also write over that run's `console.log`.
  The triage panel shows the no-worker notice while the job waits. No change touches the verdict
  path. A new import guard checks the run entry point and the worker loop. Neither loads the triage
  module, a Claude agent, or the Anthropic client library. An end-to-end test runs a queued
  triage through the real worker loop. A stub agent stands in for the model on the `--ai` path.
  Two follow-ups remain open. First, a hosted deployment still refuses triage. Its scenario ships
  as `materials`, and its runs live in object storage. The worker must first download the run tree
  and upload `triage.json` back. Second, a job that names server paths (`run` and `triage`) carries
  no capability that ties it to the server's machine. A worker on another machine may lease such a job
  and then fail it.

## References

- The worker capability item (slug `worker-capability`): defines `worker.yaml`, `--worker-config`, `maxJobConcurrency`, and the worker capability check that the one-shot worker runs.
- The Device Farm dispatch item (slug `devicefarm-worker-dispatch`): moves the Device Farm batch onto a worker and retires the server-side budget settings.
- [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md): the worker lease model and post-completion logs.
- [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md): write-once secrets.
- [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md): signed-URL object I/O for workers.
- [BE-0204](../BE-0204-server-storage-gcs-support/BE-0204-server-storage-gcs-support.md): the server object store.
- [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md): live artifacts across the split.
- [BE-0166](../BE-0166-capability-routed-queues/BE-0166-capability-routed-queues.md): capability-routed leasing.
- [BE-0336](../BE-0336-serve-device-farm-bounded-fan-out/BE-0336-serve-device-farm-bounded-fan-out.md): the in-process Device Farm dispatch this item removes from the server.
- [BE-0431](../BE-0431-job-scoped-artifact-override/BE-0431-job-scoped-artifact-override.md): the per-job artifact overrides.

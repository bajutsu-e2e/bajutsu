**English** · [日本語](BE-XXXX-server-runs-no-jobs-ja.md)

# BE-XXXX — The server queues jobs and workers run every one

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-server-runs-no-jobs.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Approved** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
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
- **The secrets key**, `BAJUTSU_SECRETS_KEY`, is mandatory for a database-backed start today. A local start generates one and persists it beside the runs directory.
- **`hosted`** stays a deployment setting. A repository must not imply it, or the file browser of BE-0108 disappears and the device takeover of `record` (BE-0185) is refused for an author who sits at the device.
- **Run history** comes from the database window once a repository exists, so runs that `bajutsu run` wrote from the command line, and runs already under `runs/`, would vanish from the local UI. The server indexes the run trees under `runs/`, both the existing ones and those that `bajutsu run` writes from the command line, into the runs table at start and on a rescan. The file store is rooted at the runs tree, so their artifacts resolve. Targets are org-scoped once a repository exists, and a local start uses one default org.
- **The artifact store** gets a filesystem implementation. The object store accepts only `s3://` and `gs://` today ([`object_store.py`](../../bajutsu/serve/server/object_store.py)). A `file://` store lets the worker write a run tree and the server read it. The worker still reaches it through the signed-URL interface of [BE-0160](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md), and the server serves those URLs for this store. Each URL is signed with an HMAC key that the server generates and persists, expires, binds the object key and content type into the signature, and is confined to the store root by the existing key validation. The routes accept nothing else.

The local backend and the server backend then differ in these defaults and nothing else. The `--backend` flag loses its meaning and is deprecated in one release and removed in the next.

### Cancellation across the split

Cancelling a job reaches only the server's in-memory `Job` today, and for a queued job that `Job` holds no process. Locally the server stops a `run` gracefully (BE-0370) and kills the process group of every other kind. On a worker, a 409 from the heartbeat makes the worker wait for the run to finish. The split needs a cancel path of its own.

- Cancelling a queued job marks its row cancelled, and the lease skips it. The cancel finds the job in the repository, so a job queued before a restart can still be cancelled.
- Cancelling a leased job keeps the row leased and sets a cancel-requested flag, which the heartbeat response returns with a 200. The worker then cancels its own `Job`: gracefully for `run`, and by a process-group kill for the other kinds. The result route accepts a cancelled outcome from the lease holder, and only that moves the row to cancelled.
- `worker --once` maps SIGINT and SIGTERM to the same cancel.

### Live output across the split

The hosted model delivers a worker's console log after the job completes ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md), [`post_completion_logbus.py`](../../bajutsu/serve/server/post_completion_logbus.py)). That was acceptable while local use bypassed it. Once local use goes through a worker, the console must be live again, and `crawl` needs its exploration graph while it grows. The worker therefore streams: it posts log chunks and live artifact chunks to worker routes that check the lease owner, as the heartbeat route does, and the server publishes them to a log bus that accepts writes (the in-memory bus for a single server process, or `RedisLogBus` across replicas), in place of the post-completion bus, which ignores publishes, and relays them to the browser over Server-Sent Events (SSE). The uploaded `console.log` stays the durable copy. The log stream comes before any kind moves, so a moved `run` keeps a live console. The artifact stream arrives with `crawl`, and its first consumer is the crawl graph, which is the scope [BE-0070](../BE-0070-live-run-artifacts-across-split/BE-0070-live-run-artifacts-across-split.md) left open.

`record` also hands control to a person: the server writes the person's response to the run's standard input today (BE-0179). A worker holds that input, so the response travels the other way, from the server to the worker, on the heartbeat response, and the worker writes it. The device takeover then follows the worker's host and not the server's.

### The one-shot worker

`bajutsu worker --once <spec>` executes one job spec without a server and exits with the job's exit code. The spec has the shape the queue already stores (`job_spec` in `worker_job.py`). The worker reads its `worker.yaml` through `--worker-config`, runs the worker capability check, executes the job through `execute_job_spec` with local input and output in place of HTTP and signed URLs, and, in one-shot mode, spawns the child with inherited standard input, output, and error in place of the piped, merged capture a leased job uses. It takes the run id from the run tree the child wrote and not from the PASS line, and it passes its resolved `--worker-config` path to the internal run entry point it spawns. A spec that names an uploaded bundle or artifact overrides is rejected with a clear message, because those arrive by signed URL and a one-shot run has none. The same command serves tests, investigations, and the wrapper below.

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

This item does not change what a job does, how a verdict is decided, or the run tree. It leaves the CLI-only commands that never went through the server as they are, and it leaves the interactive helpers listed in the Introduction on the server for now. A single-machine UI runs one job at a time per worker process, because a local worker accepts a `maxJobConcurrency` of one; several worker processes, each with its own device, are the workaround until per-job device allocation lands. Three points are open for review:

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

- [ ] Local defaults
- [ ] Cancellation across the split
- [ ] One-shot worker and wrapper
- [ ] Live log stream and the routing executor
- [ ] Move `run`
- [ ] Move `run-set` (the Device Farm batch)
- [ ] Move `triage`
- [ ] Move `record` and `crawl`
- [ ] Remove the local executor

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

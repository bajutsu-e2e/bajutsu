**English** · [日本語](BE-0015-web-ui-public-hosting-ja.md)

# BE-0015 — Public hosting of the web UI

* Proposal: [BE-0015](BE-0015-web-ui-public-hosting.md)
* Status: **Proposal**
* Track: [Proposals](../README.md#proposals)
* Topic: Authoring experience (record / GUI editor)

## Introduction

Convert the local `serve` into a shared, publicly accessible service. The architecture splits into a control plane (Linux: FastAPI + Postgres + Redis + R2) and a macOS worker pool (Orka), with auth, isolation, and per-run Simulators. This requires a core refactor that replaces `subprocess.Popen` with a job queue.

## Motivation

The local web UI (`bajutsu serve`, [bajutsu/serve/](../../../bajutsu/serve/)) is a Tier-1 authoring convenience: it binds `127.0.0.1`, has no authentication, and shells out to `bajutsu run` on the same host. That makes it a single-machine, single-user tool, with three limitations that public hosting is meant to lift:

- **No sharing.** Authoring a scenario and investigating a failure are collaborative activities, but the run tree, the live log, and the report all stay on one laptop. A teammate cannot open a colleague's report, watch an authoring session in progress, or review a result without copying files by hand.
- **Every author needs a configured Mac.** A run drives an iOS Simulator, which only exists on macOS. So today only people with a set-up macOS machine can author or run at all. A shared service lets anyone with a browser do so against a managed Mac pool.
- **The UI is bound to its host.** Each request spawns a subprocess on the serving machine ([jobs.py](../../../bajutsu/serve/jobs.py) `run_job`), so the tool cannot scale past one host or isolate one user's run from another's.

Public hosting turns this per-laptop convenience into shared infrastructure: a logged-in user picks a project and a scenario, runs it on a managed Mac pool, watches live logs, and shares the resulting report by URL. This is squarely the *authoring experience* topic — it removes the macOS-on-every-desk requirement and makes runs and reports first-class, shareable artifacts.

It must do so without touching the prime directives ([CLAUDE.md](../../../CLAUDE.md)): `run` stays fully deterministic, with no large language model (LLM) anywhere in the pass/fail path, and the deterministic gate stays on ephemeral continuous-integration (CI) runners. The hosted Mac pool carries only interactive authoring, never regression volume.

## Detailed design

The concrete server, database, storage, and deployment stack — with a per-layer comparison of the products considered — lives in [cloud-hosting.md](../../cloud-hosting.md). This section gives the architecture the proposal commits to; that page is its detailed technology selection.

### Split topology

The web UI is a thin launcher: `/api/run` ultimately drives an iOS Simulator through `idb` and `simctl`, and the Simulator exists only on macOS. "Host the web UI" therefore means "host the runner," and the runner needs a Mac. No general Linux platform-as-a-service can execute a run. That forces a split the current single-process design does not have:

- A **control plane** on Linux — cheap and horizontally scalable — owns the stateful multi-user work: authentication, run history, the job queue, and serving reports.
- A **macOS worker pool** — expensive, macOS-only, and isolated — is reduced to a stateless worker that leases a job, runs it on a fresh Simulator, streams logs, and uploads artifacts.

### The core refactor: in-process subprocess to job queue

Today `bajutsu serve` runs the CLI in-process. `ServeState.popen` (default `subprocess.Popen`) spawns `bajutsu run`/`record` on a background thread; `run_job` buffers combined output into `Job.lines`, and the browser polls roughly once a second to read it ([jobs.py](../../../bajutsu/serve/jobs.py)). This is the piece that has to change, because a remote worker — not the serving process — runs the command.

In the hosted model the same `bajutsu run` argv that `serve` builds today is still run, but its invocation moves across a broker:

1. The control plane validates the request against the caller's project, writes a `run` record, and **enqueues a job** describing it — roughly `{run_id, project, scenario_ref, app, options, byo_key_ref}` — instead of spawning a subprocess.
2. A macOS worker **leases** the job, fetches the scenario for that project (by id, never a client-supplied path), and runs the command.
3. The worker **publishes** log lines and status to a per-run channel as it goes; the control plane subscribes to that channel and relays it to the browser over **server-sent events (SSE)**, replacing the polling loop.
4. On finish the worker **uploads the `runs/<id>/` tree** to object storage and **posts the result** (exit code, run id, manifest summary) back to the control plane.

So the in-memory `Job`/`ServeState` lifecycle splits in two: the queued job payload replaces the in-process `Job`, and live-log delivery moves from `Job.lines` polling to publish/subscribe plus SSE. The deterministic core (`bajutsu run` and its report) is unchanged throughout; only its invocation and plumbing move.

### Per-run Simulator isolation

A public, multi-tenant service must isolate runs from one another. Each run is provisioned on a fresh, erased Simulator (`bajutsu run --erase`), which deliberately drops the local UI's fast `--no-erase` reuse loop. Per-app differences stay in config (`apps.<name>`) exactly as they are today, so the runner and drivers are unchanged across apps.

### Security requirements (mandatory before any public exposure)

Today's `serve` is safe only because it is localhost-only and single-user; public hosting removes both assumptions. The following are prerequisites, not options (full detail in [cloud-hosting.md](../../cloud-hosting.md#security-hardening-mandatory-before-any-public-exposure)):

- **Authentication on every endpoint**, with per-organization role-based access control, and run dispatch rate-limited per user and organization.
- **No arbitrary-path scenario execution.** `/api/run` today passes the request's `scenario` straight into the `bajutsu run` argv with no check that it is within `scenarios_dir` ([helpers.py](../../../bajutsu/serve/helpers.py) `run_command`). In the hosted model, scenarios are stored per project and fetched by the worker by id, and `backend`/`udid` are validated against an allowlist rather than passed as free text.
- **Per-organization bring-your-own `ANTHROPIC_API_KEY`**, so the cost and abuse surface of the AI paths (`record`, `run --dismiss-alerts`) is bounded to the organization that owns the key.
- **Worker sandboxing.** A scenario is effectively untrusted code driving a device, so each run executes on an ephemeral Mac and Simulator, with an egress allowlist and no cross-tenant secret reuse.
- **Signed, expiring artifact URLs**; cross-origin and cross-site-request-forgery protection; standard security headers; and an audit log of who ran what, when.
- **Per-organization quotas and concurrency caps**, so one tenant cannot starve the scarce, expensive Mac pool.

### Deployment, phased

The full phased plan is in [cloud-hosting.md](../../cloud-hosting.md#deployment-plan-phased). In short: Phase 1 is a minimum viable product — a containerized control plane on a Linux platform, managed Postgres and Redis, artifacts on object storage, GitHub login, and a single macOS worker node — so that a logged-in user can pick a project and scenario, run it, watch live logs, and view the report end to end on shared infrastructure. Phase 2 scales the control plane and autoscales the Mac pool off queue depth, with per-organization concurrency quotas and full observability. The Linux side is cheap and elastic; the Macs dominate the bill and do not scale to zero cleanly, which is why the deterministic gate stays on ephemeral CI and the hosted pool carries only interactive authoring.

### Migration from `serve` (incremental, not a rewrite)

Each step below is independently shippable and testable; the deterministic core is unchanged throughout. Detail in [cloud-hosting.md](../../cloud-hosting.md#migration-from-servepy-incremental-not-a-rewrite-from-zero).

1. Lift the pure helpers (`list_scenarios`, `list_runs`, `run_command`, the `Job` model) into a web application, keeping the existing HTML as the first frontend.
2. Replace the in-process `run_job` with enqueue-to-broker, and add a worker entrypoint that runs the same `bajutsu run` argv built today.
3. Swap polling for SSE over publish/subscribe, and swap local file serving for signed object-storage URLs.
4. Add authentication and a relational database (organizations, projects, runs); move scenario and app sources from the filesystem into per-project storage.
5. Stand up one macOS worker, close the security items above, then scale the pool.

## Alternatives considered

These are the architecture-level choices. Per-layer product alternatives (for example RQ versus Celery, Orka versus EC2 Mac, self-hosted GitHub OAuth versus a managed provider) are tabulated in [cloud-hosting.md](../../cloud-hosting.md#selected-stack-the-recommendation).

- **Keep `serve` local-only (status quo).** The simplest option, and it remains the right default for solo authoring. It is rejected as the answer to *this* proposal because the topic is the shared authoring experience: a localhost tool cannot share runs or reports, and cannot serve authors without a Mac.
- **One hosted Mac, no split (run everything on a single macOS host).** This avoids the queue refactor by keeping the in-process `subprocess.Popen` model on a remote Mac. Rejected: it cannot scale past one host, cannot isolate tenants, and spends scarce, expensive Mac time on the cheap stateful work (auth, history, report serving) that belongs on Linux. The split topology exists precisely to keep that work off the Macs.
- **Stream live logs over WebSocket instead of SSE.** WebSocket is bidirectional and would also work. SSE is chosen because log delivery is one-directional (worker to browser) and SSE is simpler over plain HTTP; WebSocket is held in reserve for a later need for bidirectional run control.
- **Keep scenarios on the filesystem and pass paths through `/api/run`.** This is what `serve` does today and is the least work. Rejected for public hosting: passing a client-chosen path into the run argv is an arbitrary-path execution hole, so scenarios must be stored per project and fetched by id (see Security requirements).

## References

[cloud-hosting.md](../../cloud-hosting.md), `bajutsu/serve.py`

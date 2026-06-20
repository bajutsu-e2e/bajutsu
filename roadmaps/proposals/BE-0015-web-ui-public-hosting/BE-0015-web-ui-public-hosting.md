**English** · [日本語](BE-0015-web-ui-public-hosting-ja.md)

# BE-0015 — Public hosting of the web UI

* Proposal: [BE-0015](BE-0015-web-ui-public-hosting.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Hosting the web UI (cloud / self-hosted)

## Introduction

Forward-looking design — the **hosted service is not implemented yet**, but the
local/server-parity groundwork it builds on has landed (see *Migration* below). This proposal
selects a concrete server, database, storage, and deployment stack for turning the local
`bajutsu serve` (`bajutsu/serve/`) into a **shared, publicly hosted** service. The local UI today is a Tier-1
convenience that binds `127.0.0.1`, has no auth, and shells out to `bajutsu run` on the same host
([cli](../../../docs/cli.md#serve) · [reporting](../../../docs/reporting.md)). Going public changes the shape of
the system, not just its address: the web UI is a **thin launcher**, so hosting it really means
hosting the **runner** — which leads to a shared public service built from a Linux control plane
and a macOS worker pool.

Related: [architecture](../../../docs/architecture.md) · [ci](../../../docs/ci.md) · the self-hosting counterpart
[BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).

## Motivation

The web UI is a **thin launcher**. `/api/run` spawns `python -m bajutsu run …`, which drives an
**iOS Simulator** through `idb` + `simctl` — and the Simulator only exists on **macOS**. So
"host the web UI" really means "host the **runner**," and the runner needs a Mac. No general
Linux PaaS (Cloud Run, Vercel, Fly Machines on Linux) can execute a run.

That forces a **split topology** the current single-process design does not have:

```
        Browser (many users)
              │  HTTPS + OAuth
              ▼
   ┌──────────────────────────┐        ┌───────────────────────────────┐
   │   CONTROL PLANE (Linux)  │        │     macOS WORKER POOL          │
   │  FastAPI · Postgres · S3 │  jobs  │  bajutsu run · idb · Simulator │
   │  enqueue + serve reports │ ─────▶ │  one ephemeral Simulator/run   │
   │  SSE live logs           │ ◀───── │  stream logs · upload artifacts│
   └──────────────────────────┘  Redis └───────────────────────────────┘
        cheap, scales out              expensive, macOS-only, isolated
```

The cheap, stateful, multi-user part (auth, history, queue, report viewer) lives on Linux. The
expensive macOS-only part is reduced to a stateless worker that pulls a job, runs it on a fresh
Simulator, streams logs, and uploads artifacts. This is the central refactor: **`serve`'s
in-process `subprocess.Popen` becomes a job enqueued onto a broker and consumed by remote
workers.**

## Detailed design

### Selected stack (the recommendation)

| Layer | Selected | Why this one | Notable alternatives |
|---|---|---|---|
| **API / web** | **FastAPI** on **Uvicorn** (prod: Gunicorn + uvicorn workers) | Async (SSE — server-sent events — / WebSocket for live logs), Pydantic is **already a dependency** ([pyproject](../../../pyproject.toml)), OpenAPI for free, same Python as the core | Django (heavier, sync-first), Litestar, keep stdlib (won't scale to auth/multi-user) |
| **Frontend** | Keep the **single-page UI** from `serve`, served by the API; add auth + project pickers | The UI is one HTML string already; no SPA build step needed for v1 | React/Svelte SPA later if the UI grows |
| **Reverse proxy + TLS** (Transport Layer Security) | **Caddy** | Automatic HTTPS (Let's Encrypt) with near-zero config; clean reverse proxy + headers | nginx + certbot (more knobs, more setup), Traefik |
| **AuthN/Z** (authentication / authorization) | **OAuth2 — GitHub provider** via **Authlib**, signed-cookie sessions; per-org RBAC (role-based access control) | Audience is developers (they have GitHub); no passwords to store; org model maps to GitHub orgs | oauth2-proxy at the edge, Auth0/Clerk/WorkOS (managed, paid), Google OAuth |
| **System of record** | **PostgreSQL 16** + **SQLAlchemy 2.0** + **Alembic** | Relational core (orgs/users/projects/runs) with **JSONB** for manifest summaries; managed everywhere (RDS, Cloud SQL, Neon, Supabase) | SQLite (no concurrency for multi-user), MySQL |
| **Queue / cache / pub-sub** | **Redis 7** | One component does three jobs: **job broker**, cache, and **pub/sub fan-out** for live logs (worker → Redis → SSE) | RabbitMQ/NATS (broker only), SQS (broker only, no pub/sub) |
| **Task framework** | **RQ** (Redis Queue) to start | Tiny, Redis-native, easy to read; matches "enqueue a `bajutsu run`, a worker consumes it" | Celery (more features: routing/retries/beat — adopt when needed), Dramatiq |
| **Artifact storage** | **Cloudflare R2** (S3-compatible) | Run trees (`report.html`, screenshots, **video**, `network.json`) are large binaries — keep them **out of Postgres**; R2 has **no egress fees** | AWS S3 (egress costs), MinIO (self-host), GCS |
| **macOS workers** | **MacStadium Orka** | Purpose-built macOS-VM orchestration ("k8s for Mac") — the only option that gives a **scalable, schedulable pool** of clean Macs | AWS EC2 Mac (24h min allocation, pricey), Scaleway Apple silicon, self-hosted Mac minis |
| **Secrets** | Cloud secret manager (**Doppler** or platform-native: Fly/AWS Secrets Manager) | Centralized rotation; per-org **BYO (bring-your-own) `ANTHROPIC_API_KEY`** (bounds cost/abuse for `--dismiss-alerts` and `record`) | Vault (heavier), env files (don't, for public) |
| **Observability** | **Sentry** (errors) + **Prometheus/Grafana** (metrics) + structured JSON logs | Standard, cheap, hosted tiers exist | OpenTelemetry collector, Datadog (paid) |
| **IaC (infrastructure as code) + CI/CD** (continuous integration / continuous delivery) | **Terraform** + **GitHub Actions** → **GHCR** (GitHub Container Registry) images | Reproducible infra; the repo already lives in Actions ([ci](../../../docs/ci.md)) | Pulumi, manual (don't) |

### What each piece does

#### Control plane (Linux, cheap, scales horizontally)
The evolution of today's `serve`. Endpoints (auth'd):

- `GET /` → the project-scoped UI (scenario/app pickers come from the DB, **not** the filesystem).
- `POST /api/run` → validate the request **against the caller's project** (no client-supplied
  filesystem paths — see Security), write a `run` row, **enqueue an RQ job**, return its id.
- `GET /api/runs/stream/<id>` → **SSE** stream of live log lines (replaces the 1 s polling loop in
  the current UI), backed by Redis pub/sub that the worker publishes to.
- `GET /runs/<id>/…` → serve report assets via **short-lived signed R2 URLs** (or proxy them),
  replacing today's local-filesystem `_serve_run_file`.

#### Job queue (control plane ↔ workers)
Redis is the broker. A run becomes a serialized job `{run_id, project, scenario_ref, app, options,
byo_key_ref}`. Workers `BRPOP`/lease it. The worker also `PUBLISH`es log lines and status to a
per-run Redis channel that the control plane's SSE endpoint subscribes to.

#### macOS worker (stateless, isolated, ephemeral)
A small Python agent (launchd service) on each Orka-provisioned Mac:

1. Lease a job from Redis.
2. **Fetch the scenario** for that project from the control plane / object store (never a path the
   client chose).
3. Provision a **fresh, erased Simulator** (`bajutsu run --erase`) — public multi-tenant **must
   isolate**, which deliberately drops the local UI's fast `--no-erase` reuse loop.
4. Stream stdout → Redis; on finish, **upload the `runs/<id>/` tree to R2** and `POST` the result
   (exit code, run id, manifest summary) back to the control plane.
5. Tear the Simulator down.

### Deployment plan (phased)

#### Phase 1 — MVP (minimum viable product), ship fast
- **Control plane** containerized → **Fly.io** (or Render). Managed **Fly Postgres** + **Upstash
  Redis**. Artifacts on **Cloudflare R2**. TLS via the platform / Caddy. **GitHub OAuth**.
- **Workers**: a **single MacStadium Orka** node running the agent. Scenarios + app configs stored
  per project in Postgres/R2.
- **Secrets** in Fly/Doppler; each org supplies its own `ANTHROPIC_API_KEY`.
- Goal: a logged-in user picks a project + scenario, hits Run, watches live logs, views the report —
  end to end, securely, on shared infra.

#### Phase 2 — scale
- Control plane → **Kubernetes** (GKE/EKS), managed **Cloud SQL/RDS** + **ElastiCache/Upstash**.
- **Orka autoscaling** Mac pool keyed off Redis queue depth; per-org **concurrency quotas**.
- Artifact **CDN** (content delivery network) in front of R2; multi-region control plane if needed.
- Full observability (Sentry + Grafana dashboards + alerting on queue depth / worker health).

#### Cost shape
The Linux control plane, Postgres, Redis, and R2 are **cheap and elastic**. The **Macs dominate the
bill** and don't scale to zero cleanly (Orka nodes / EC2 Mac 24 h minimums). Design the pool around
**queue depth with a small warm floor**, and push the deterministic *gate* to ephemeral CI
([ci](../../../docs/ci.md)) so the hosted pool only carries **interactive authoring**, not regression volume.

### Security hardening (mandatory before any public exposure)

Today's `serve` is safe because it's localhost-only and single-user. Public hosting removes
both assumptions, so these are not optional:

- **Auth on every endpoint** (OAuth + per-org RBAC); **rate-limit** run dispatch per user/org.
- **Eliminate arbitrary-path scenario execution.** `/api/run` currently passes `body["scenario"]`
  straight into the `bajutsu run` argv with no check that it's within `scenarios_dir`
  (`bajutsu/serve/` `run_command` / `do_POST`). In the hosted model, scenarios are
  **stored per project and fetched by the worker by id** — the client never names a filesystem path,
  and `backend`/`udid` are validated against an allowlist, not passed as free text.
- **Per-org BYO `ANTHROPIC_API_KEY`** so AI features (`--dismiss-alerts`, `record`) bound cost and
  abuse to the org that owns the key.
- **Worker sandboxing.** A scenario is effectively untrusted code driving a device: run each on an
  **ephemeral Mac/Simulator**, with an **egress allowlist** and **no cross-tenant secret reuse**.
- **Signed, expiring artifact URLs**; **CORS/CSRF** (cross-origin resource sharing /
  cross-site request forgery) protection; standard security headers; an
  **audit log** of who ran what, when.
- **Quotas / concurrency caps per org** so one tenant can't starve the (scarce, expensive) Mac pool.

### Migration from the stdlib `serve` (incremental, not a rewrite from zero)

**The groundwork has landed.** `bajutsu serve` is now a package (`bajutsu/serve/`) structured
around four swap-in **seams**, each with a local implementation on `main`, so the steps below are
now "swap the local implementation for a server one behind the existing seam," not a rewrite:

- **`RunExecutor`** — job dispatch (local: an in-process daemon thread running `run_job`).
- **`LogBus`** — live-log delivery (local: an in-memory buffer; the UI already streams it over SSE).
- **`ArtifactStore`** — run-artifact reads (local: filesystem, confined to `runs_dir`).
- **`ScenarioStore`** — scenario resolution (local: confined to the app's scenarios dir).

[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
also shipped the auth + input validation, and the pure helpers (`list_scenarios`, `list_runs`,
`run_command`, the `Job` model) are already isolated in `bajutsu/serve/helpers.py`. So:

1. Lift the (already-isolated) pure helpers and the embedded HTML into a FastAPI app as the v1
   frontend.
2. Provide a **queue-based `RunExecutor`** that enqueues → RQ, plus a worker entrypoint that runs
   the same `bajutsu run` argv `run_job` builds today.
3. Provide a **Redis-backed `LogBus`** (pub/sub) and an **object-storage `ArtifactStore`** that
   returns R2 signed-URL redirects in place of the local filesystem reads.
4. Add **OAuth + Postgres** (orgs/projects/runs) and a **per-project `ScenarioStore`** that resolves
   by id from storage instead of the local filesystem — the **7a/7b/7c** breakdown below details
   this step.
5. Stand up **one Orka worker**; close the security items above; then scale the pool.

Each step is independently shippable and testable, and the deterministic core (`bajutsu run`, the
report) is unchanged throughout — only its *invocation* and *plumbing* move to the cloud.

### Persistence and identity — the remaining slice (7a/7b/7c)

The single-tenant server backend now reaches feature parity with local across **five** seams (the
four above plus a **`BaselineStore`** for visual-regression baselines), so migration steps 1–3 have
landed. What remains is **step 4 — persistence and identity**: the system of record and the login
that turn that single-tenant backend multi-tenant. `ScenarioStore` and the object store are already
parameterized by a tenant prefix (`<org>/`), so this slice adds the database and the identity on
top of seams that already exist. It proceeds under three invariants — **local behavior is
unchanged; every slice's tests run on the Linux gate (no Simulator, no live Postgres/Redis/object
storage); and each slice follows the existing seam pattern** (a Protocol with an injected
implementation, lazy-imported behind an optional extra so the default `serve`/CLI path never loads
it). It splits into three independently shippable slices.

#### 7a — the persistence layer (a `Repository` seam)

A fifth seam, `Repository`, lands in `bajutsu/serve/server/db.py` the same way `ObjectStore` was
built: a Protocol, an injected SQLAlchemy 2.0 implementation, an env-driven factory, and a lazy
import. The schema is fixed up front in the first Alembic migration — adding foreign keys later is
painful under SQLite, which the gate uses — even though only `runs` is read or written in 7a:

```
orgs       id, slug (unique), name, created_at
users      id, org_id → orgs, email (unique), github_login (used in 7b), created_at
projects   id, org_id → orgs, name (= the config app name), created_at, unique(org_id, name)
runs       id, org_id → orgs, project_id → projects, created_by → users,
           status, ok, created_at, summary (JSONB)
audit_log  id, org_id → orgs, actor_id → users, action, target, at, detail (JSONB)
```

`org_id` threads through every table so that 7c's per-org scoping and quotas can filter on it. The
relational core (ids, status, timestamps) stays in ordinary columns; only the variable manifest
summary and audit detail use `JSON().with_variant(JSONB, "postgresql")`, so the same models run on
SQLite (the gate) and Postgres (production). 7a implements only the `runs` methods on the seam —
`record_run` / `get_run` / `list_runs` — returning a `RunRecord` boundary type so ORM rows never
leak past the seam; the `orgs`/`users`/`projects`/`audit_log` behavior arrives with 7b and 7c.
Wiring stays in the one place seams are assembled, `_build_server_state`, keyed off
`BAJUTSU_DATABASE_URL`; when that is unset, and for the local backend, `repository` stays `None`, so
behavior is unchanged. Tests build an in-memory SQLite engine inside each test (no fixtures), and
the import guard already forbids `sqlalchemy`/`alembic`/`psycopg` on the default path. A `db` extra
carries `sqlalchemy`, `alembic`, and `psycopg`. Ship it as two PRs: 7a-1 (schema + repository +
SQLite tests, touching no existing file) and 7a-2 (the `_build_server_state` wiring + Alembic).

#### 7b — GitHub OAuth and durable sessions

Sessions today live in an in-memory `set[str]` on `ServeState`, which cannot survive a restart or
span worker processes. 7b moves them into the system of record (or Redis) and adds **GitHub OAuth
via Authlib**: a login/callback pair, signed-cookie sessions, and resolving the OAuth identity into
an `orgs`/`users` upsert. The `operations` layer stays token-agnostic; authentication remains in the
handler/app middleware, exactly where [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
put it. An `authlib` extra carries the dependency.

#### 7c — per-org RBAC, audit log, and quotas

The last slice enforces tenancy. Each user carries a role within its org (viewer / editor / admin);
every endpoint checks org scope, so a cross-org access returns 403; the `audit_log` records who ran
what and when; and per-org concurrency quotas apply at enqueue time so one tenant cannot starve the
scarce Mac pool. The tenant prefix already present on `ScenarioStore`, the object store, and
`BaselineStore` is fed the resolved `org_id`, so artifacts, scenarios, and baselines all become
org-scoped without a contract change.

## Alternatives considered

A general Linux PaaS (Cloud Run, Vercel, Fly Machines on Linux) was rejected outright: it cannot
execute a run, because the Simulator only exists on macOS — hosting the web UI necessarily means
hosting a Mac-bound runner. Beyond that, each layer of the selected stack has a rejected
counterpart:

- **API / web**: Django (heavier, sync-first), Litestar, or keeping the stdlib server (won't scale
  to auth/multi-user) — rejected in favor of FastAPI, which is async and reuses Pydantic already in
  the dependency tree.
- **Reverse proxy + TLS**: nginx + certbot (more knobs, more setup) and Traefik — rejected in favor
  of Caddy's near-zero-config automatic HTTPS.
- **AuthN/Z**: oauth2-proxy at the edge, Auth0/Clerk/WorkOS (managed, paid), and Google OAuth —
  rejected in favor of GitHub OAuth, since the audience already has GitHub and the org model maps
  to GitHub orgs.
- **System of record**: SQLite (no concurrency for multi-user) and MySQL — rejected in favor of
  PostgreSQL with JSONB and broad managed availability.
- **Queue / cache / pub-sub**: RabbitMQ/NATS (broker only) and SQS (broker only, no pub/sub) —
  rejected because Redis covers broker, cache, and pub/sub fan-out in one component.
- **Task framework**: Celery (more features than needed at the start) and Dramatiq — rejected in
  favor of starting with RQ; Celery can be adopted later when routing/retries/beat are needed.
- **Artifact storage**: AWS S3 (egress costs), MinIO (self-host), and GCS — rejected in favor of
  Cloudflare R2's S3 compatibility with no egress fees.
- **macOS workers**: AWS EC2 Mac (24h minimum allocation, pricey), Scaleway Apple silicon, and
  self-hosted Mac minis — rejected in favor of MacStadium Orka, the only option giving a scalable,
  schedulable pool of clean Macs.
- **Secrets**: Vault (heavier) and env files (unsafe for public) — rejected in favor of a cloud
  secret manager.
- **Observability**: an OpenTelemetry collector and Datadog (paid) — alternatives to the
  Sentry + Prometheus/Grafana baseline.
- **IaC + CI/CD**: Pulumi and manual provisioning (don't) — rejected in favor of Terraform +
  GitHub Actions, since the repo already lives in Actions.

## References

`bajutsu/serve/`, [ci](../../../docs/ci.md), [architecture](../../../docs/architecture.md),
[reporting](../../../docs/reporting.md), [cli](../../../docs/cli.md#serve), and the self-hosting counterpart
[BE-0016](../../proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).

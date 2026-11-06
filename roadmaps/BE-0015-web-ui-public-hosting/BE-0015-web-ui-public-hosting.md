**English** · [日本語](BE-0015-web-ui-public-hosting-ja.md)

# BE-0015 — Public hosting of the web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0015](BE-0015-web-ui-public-hosting.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0015") |
| Implementing PR | [#105](https://github.com/bajutsu-e2e/bajutsu/pull/105), [#106](https://github.com/bajutsu-e2e/bajutsu/pull/106), [#108](https://github.com/bajutsu-e2e/bajutsu/pull/108), [#112](https://github.com/bajutsu-e2e/bajutsu/pull/112), [#117](https://github.com/bajutsu-e2e/bajutsu/pull/117), [#118](https://github.com/bajutsu-e2e/bajutsu/pull/118), [#119](https://github.com/bajutsu-e2e/bajutsu/pull/119), [#120](https://github.com/bajutsu-e2e/bajutsu/pull/120), [#121](https://github.com/bajutsu-e2e/bajutsu/pull/121), [#122](https://github.com/bajutsu-e2e/bajutsu/pull/122), [#127](https://github.com/bajutsu-e2e/bajutsu/pull/127), [#129](https://github.com/bajutsu-e2e/bajutsu/pull/129), [#130](https://github.com/bajutsu-e2e/bajutsu/pull/130), [#131](https://github.com/bajutsu-e2e/bajutsu/pull/131), [#132](https://github.com/bajutsu-e2e/bajutsu/pull/132), [#133](https://github.com/bajutsu-e2e/bajutsu/pull/133), [#134](https://github.com/bajutsu-e2e/bajutsu/pull/134), [#139](https://github.com/bajutsu-e2e/bajutsu/pull/139), [#143](https://github.com/bajutsu-e2e/bajutsu/pull/143), [#149](https://github.com/bajutsu-e2e/bajutsu/pull/149), [#150](https://github.com/bajutsu-e2e/bajutsu/pull/150), [#151](https://github.com/bajutsu-e2e/bajutsu/pull/151), [#152](https://github.com/bajutsu-e2e/bajutsu/pull/152), [#153](https://github.com/bajutsu-e2e/bajutsu/pull/153), [#156](https://github.com/bajutsu-e2e/bajutsu/pull/156), [#157](https://github.com/bajutsu-e2e/bajutsu/pull/157), [#159](https://github.com/bajutsu-e2e/bajutsu/pull/159) |
| Topic | Hosting the web UI |
| Related | [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) |
<!-- /BE-METADATA -->

## Introduction

This item is the **design and the enabling software** for a publicly hosted web UI. That software has **fully landed**. Beyond local/server parity, a server backend now ships. The backend provides auth, persistence, role-based access control (RBAC), audit, quotas, and **multi-tenancy**. See *Migration*, *Persistence and identity*, and *§8 — multi-tenancy* below.

What remains is the **operational deployment** onto real infrastructure:

- Provision the control plane, the macOS worker pool, the database, and object storage.
- Wire production auth and secrets.
- Close the live-environment security items.

That deployment is infrastructure and operations work, gated on paid macOS-only capacity. It is tracked separately in its own item, *Deploy the hosted web UI service*. The item was carved off this umbrella the same way [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) and [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) were.

This proposal selects a concrete server, database, storage, and deployment stack. The stack turns the local `bajutsu serve` (`bajutsu/serve/`) into a **shared, publicly hosted** service. The local UI today is a [Tier 1](../../docs/glossary.md#the-two-tiers) convenience. It binds `127.0.0.1`, has no auth, and shells out to `bajutsu run` on the same host ([cli](../../docs/cli.md#serve) · [reporting](../../docs/reporting.md)).

Going public changes the shape of the system, not just its address. The web UI is a **thin launcher**, so hosting the UI means hosting the **runner**. The result is a shared public service built from a Linux control plane and a macOS worker pool.

Related: [architecture](../../docs/architecture.md) · [ci](../../docs/ci.md) · the self-hosting counterpart
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).

## Motivation

The web UI is a **thin launcher**. `/api/run` spawns `python -m bajutsu run …`, which drives an **iOS Simulator** through `idb` + `simctl`. The Simulator exists on **macOS** alone. Hosting the web UI therefore means hosting the **runner**, and the runner needs a Mac. No general Linux platform as a service (PaaS) can execute a run. Cloud Run, Vercel, and Fly Machines on Linux all fail for this reason.

That requirement forces a **split topology** that the current single-process design does not have:

```
        Browser (many users)
              │  HTTPS + OAuth
              ▼
   ┌──────────────────────────┐        ┌───────────────────────────────┐
   │   CONTROL PLANE (Linux)  │        │     macOS WORKER POOL          │
   │  FastAPI · Postgres · S3 │  jobs  │  bajutsu run · idb · Simulator │
   │  enqueue + serve reports │ ─────▶ │  one ephemeral Simulator/run   │
   │  SSE live logs           │ ◀───── │  stream logs · upload artifacts│
   └──────────────────────────┘  HTTP  └───────────────────────────────┘
        cheap, scales out              expensive, macOS-only, isolated
```

The cheap, stateful, multi-user part (auth, history, queue, report viewer) lives on Linux. The
expensive macOS-only part is reduced to a stateless worker. The worker pulls a job, runs the job
on a fresh Simulator, streams logs, and uploads artifacts. The central refactor follows from this
split: **`serve`'s in-process `subprocess.Popen` becomes a job enqueued onto a broker and
consumed by remote workers.**

## Detailed design

### Selected stack (the recommendation)

| Layer | Selected | Why this one | Notable alternatives |
|---|---|---|---|
| **API / web** | **FastAPI** on **Uvicorn** (prod: Gunicorn + uvicorn workers) | Async (SSE — server-sent events — / WebSocket for live logs), Pydantic is **already a dependency** ([pyproject](../../pyproject.toml)), OpenAPI for free, same Python as the core | Django (heavier, sync-first), Litestar, keep stdlib (won't scale to auth/multi-user) |
| **Frontend** | Keep the **single-page UI** from `serve`, served by the API; add auth + project pickers | The UI is one HTML string already; no SPA build step needed for v1 | React/Svelte SPA later if the UI grows |
| **Reverse proxy + TLS** (Transport Layer Security) | **Caddy** | Automatic HTTPS (Let's Encrypt) with near-zero config; clean reverse proxy + headers | nginx + certbot (more knobs, more setup), Traefik |
| **AuthN/Z** (authentication / authorization) | **OAuth2 — GitHub provider** via **Authlib**, signed-cookie sessions; per-org RBAC (role-based access control) | Audience is developers (they have GitHub); no passwords to store; org model maps to GitHub orgs | oauth2-proxy at the edge, Auth0/Clerk/WorkOS (managed, paid), Google OAuth |
| **System of record** | **PostgreSQL 16** + **SQLAlchemy 2.0** + **Alembic** | Relational core (orgs/users/projects/runs) with **JSONB** for manifest summaries; managed everywhere (RDS, Cloud SQL, Neon, Supabase) | SQLite (no concurrency for multi-user), MySQL |
| **Queue / job dispatch** | **Postgres `jobs` table** (leased over HTTP) | Workers poll `POST /api/worker/lease`; the control plane leases with `SELECT … FOR UPDATE SKIP LOCKED`. No separate broker process. Replaced Redis 7 / RQ ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)) | Redis/RQ (removed), RabbitMQ/NATS, SQS |
| **Sessions** | **Postgres `sessions` table** | Sessions survive restarts and span replicas, using the same database the system of record already uses. Replaced Redis session store ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)) | Redis (removed), signed cookies |
| **Artifact storage** | **Cloudflare R2** (S3-compatible) | Run trees (`report.html`, screenshots, **video**, `network.json`) are large binaries — keep them **out of Postgres**; R2 has **no egress fees** | AWS S3 (egress costs), MinIO (self-host), GCS |
| **macOS workers** | **MacStadium Orka** | Purpose-built macOS-VM orchestration ("k8s for Mac") — the only option that gives a **scalable, schedulable pool** of clean Macs | AWS EC2 Mac (24h min allocation, pricey), Scaleway Apple silicon, self-hosted Mac minis |
| **Secrets** | Cloud secret manager (**Doppler** or platform-native: Fly/AWS Secrets Manager) | Centralized rotation; per-org **BYO (bring-your-own) `ANTHROPIC_API_KEY`** (bounds cost/abuse for `--dismiss-alerts` and `record`) | Vault (heavier), env files (don't, for public) |
| **Observability** | **Sentry** (errors) + **Prometheus/Grafana** (metrics) + structured JSON logs | Standard, cheap, hosted tiers exist | OpenTelemetry collector, Datadog (paid) |
| **IaC (infrastructure as code) + CI/CD** (continuous integration / continuous delivery) | **Terraform** + **GitHub Actions** → **GHCR** (GitHub Container Registry) images | Reproducible infra; the repo already lives in Actions ([ci](../../docs/ci.md)) | Pulumi, manual (don't) |

### What each piece does

#### Control plane (Linux, cheap, scales horizontally)
The control plane evolves today's `serve`. Every endpoint requires authentication:

- `GET /` → the project-scoped UI (scenario/app pickers come from the DB, **not** the filesystem).
- `POST /api/run` → validate the request **against the caller's project**, with no
  client-supplied filesystem paths (see Security). The route writes a `run` row, **enqueues a
  job** (a `queued` row in the Postgres `jobs` table), and returns the job id.
- `GET /api/runs/stream/<id>` → **SSE** stream of live log lines. The stream replaces the 1 s
  polling loop in the current UI. The `LogBus` seam backs the stream
  ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)
  replaced the Redis pub/sub originally planned here).
- `GET /runs/<id>/…` → serve report assets via **short-lived signed R2 URLs** (or proxy the
  assets). This replaces today's local-filesystem `_serve_run_file`.

#### Job dispatch (control plane ↔ workers)

Job dispatch uses a Postgres `jobs` table leased over HTTP
([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)).
A run becomes a `queued` row, and workers poll `POST /api/worker/lease` to lease the row. After
the run completes, the worker uploads the run tree (including `console.log`) to object storage
and posts the result to `POST /api/worker/result`. The control plane records the finished run, so
the worker needs no database access. The design uses no Redis and no RQ.

#### macOS worker (stateless, isolated, ephemeral)
Each Orka-provisioned Mac runs a small Python agent as a launchd service. The agent works in five steps:

1. Lease a job over HTTP (`POST /api/worker/lease`).
2. **Fetch the scenario** for that project from the control plane or the object store, never from
   a path the client chose.
3. Provision a **fresh, erased Simulator** (`bajutsu run --erase`). Public multi-tenant hosting
   **must isolate** runs, which deliberately drops the local UI's fast `--no-erase` reuse loop.
4. Stream stdout to the control plane's `LogBus`. On finish, **upload the `runs/<id>/` tree to R2**
   and `POST` the result (exit code, run id, manifest summary) back to the control plane.
5. Tear the Simulator down.

### Deployment plan (phased)

#### Phase 1 — MVP (minimum viable product), ship fast
- **Control plane**: containerized, on **Fly.io** (or Render). Managed **Fly Postgres** also backs
  the job queue and sessions. Artifacts live on **Cloudflare R2**. TLS comes from the platform or
  Caddy. Sign-in uses **GitHub OAuth**.
- **Workers**: a **single MacStadium Orka** node running the agent. Scenarios and app configs are
  stored per project in Postgres/R2.
- **Secrets**: kept in Fly/Doppler. Each org supplies its own `ANTHROPIC_API_KEY`.
- **Goal**: a logged-in user picks a project and a scenario, hits Run, watches live logs, and views
  the report. The whole path runs end to end, securely, on shared infrastructure.

#### Phase 2 — scale
- Move the control plane to **Kubernetes** (GKE/EKS) with managed **Cloud SQL/RDS**.
- Autoscale the Mac pool with **Orka**, keyed off the Postgres `jobs` queue depth. Add per-org **concurrency quotas**.
- Put an artifact **CDN** (content delivery network) in front of R2. Make the control plane multi-region if needed.
- Provide full observability: Sentry, Grafana dashboards, and alerting on queue depth and worker health.

#### Cost shape
The Linux control plane, Postgres, and R2 are **cheap and elastic**. The **Macs dominate the
bill**, and they don't scale to zero cleanly (Orka nodes and EC2 Mac have 24 h minimums).

The pool design follows from that cost. Size the pool by **queue depth with a small warm floor**.
Push the deterministic *gate* to ephemeral CI ([ci](../../docs/ci.md)), so the hosted pool carries
**interactive authoring** alone and no regression volume.

### Security hardening (mandatory before any public exposure)

Today's `serve` is safe because it is localhost-only and single-user. Public hosting removes
both assumptions, so the following items are not optional:

- **Auth on every endpoint** (OAuth + per-org RBAC); **rate-limit** run dispatch per user/org.
- **Eliminate arbitrary-path scenario execution.** `/api/run` currently passes `body["scenario"]`
  straight into the `bajutsu run` argv, with no check that the path is within `scenarios_dir`
  (`bajutsu/serve/` `run_command` / `do_POST`). In the hosted model, scenarios are **stored per
  project and fetched by the worker by id**. The client never names a filesystem path. The server
  validates `backend` and `udid` against an allowlist and never passes them as free text.
- **Per-org BYO `ANTHROPIC_API_KEY`** so AI features (`--dismiss-alerts`, `record`) bound cost and
  abuse to the org that owns the key.
- **Worker sandboxing.** A scenario is effectively untrusted code that drives a device. Run each
  scenario on an **ephemeral Mac/Simulator**, with an **egress allowlist** and **no cross-tenant
  secret reuse**.
- **Web-layer protections.** Use **signed, expiring artifact URLs**, cross-origin resource sharing
  (CORS) and cross-site request forgery (CSRF) protection, and standard security headers.
- **Audit log.** Record who ran what, and when.
- **Quotas and concurrency caps per org**, so one tenant can't starve the scarce, expensive Mac
  pool.

### Migration from the stdlib `serve` (incremental, not a rewrite from zero)

**The groundwork has landed.** `bajutsu serve` is now a package (`bajutsu/serve/`) structured
around four swap-in **seams**, each with a local implementation on `main`. The steps below
therefore swap the local implementation for a server one behind the existing seam, and none is a
rewrite:

- **`RunExecutor`** dispatches jobs. Locally, an in-process daemon thread runs `run_job`.
- **`LogBus`** delivers live logs. Locally, an in-memory buffer holds the logs, and the UI already streams the buffer over SSE.
- **`ArtifactStore`** reads run artifacts. Locally, it reads the filesystem, confined to `runs_dir`.
- **`ScenarioStore`** resolves scenarios. Locally, it is confined to the app's scenarios dir.

[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
also shipped the auth and input validation. The pure helpers (`list_scenarios`, `list_runs`,
`run_command`, the `Job` model) are already isolated in `bajutsu/serve/helpers.py`. The migration
then proceeds in five steps:

1. Lift the already-isolated pure helpers and the embedded HTML into a FastAPI app, as the v1 frontend.
2. Provide a **queue-based `RunExecutor`** that enqueues onto the Postgres `jobs` table. Add a worker entrypoint that runs the same `bajutsu run` argv that `run_job` builds today.
3. Provide a **server `LogBus`** and an **object-storage `ArtifactStore`**. The `ArtifactStore` returns R2 signed-URL redirects in place of the local filesystem reads.
4. Add **OAuth + Postgres** (orgs, projects, runs) and a **per-project `ScenarioStore`**. The `ScenarioStore` resolves a scenario by id from storage, not from the local filesystem. This step is **shipped for the single-tenant backend**. The **7a/7b/7c** breakdown below details what landed and what is still ahead.
5. Stand up **one Orka worker**, close the security items above, and then scale the pool.

Each step is independently shippable and testable. The deterministic core (`bajutsu run`, the
report) is unchanged throughout. Only the core's *invocation* and *plumbing* move to the cloud.

### Persistence and identity — shipped for the single-tenant backend (7a/7b/7c)

Migration steps 1–3 had already landed (the five swap-in seams). **Step 4, persistence and
identity, has now shipped for the single-tenant server backend.** "Single-tenant" here means one
fixed default org holds every user. The multi-tenant, org-scoped pieces landed later on top of this
backend (see *§8 — multi-tenancy* below).

Step 4 was built under three invariants that still hold:

- Local behavior is unchanged.
- Every slice's tests run on the Linux gate, with no Simulator and no live Postgres or object
  storage.
- Every slice follows the existing seam pattern: a Protocol with an injected implementation,
  lazy-imported behind an optional extra, so the default `serve`/CLI path never loads it.

#### 7a — the persistence layer (#144, #145)

A fifth seam, `Repository` (`bajutsu/serve/server/db.py`), is built the same way as `ObjectStore`:
a Protocol, an injected SQLAlchemy 2.0 implementation, an env-driven factory, and a lazy import.
The first Alembic migration fixes the table set and foreign keys up front, because adding them
later is painful under SQLite, which the gate uses. A follow-up migration (0002) added
`users.role`:

```
orgs       id, slug (unique), name, created_at
users      id, org_id → orgs, email (unique), github_login, role, created_at
projects   id, org_id → orgs, name (= the config app name), created_at, unique(org_id, name)
runs       id, org_id → orgs, project_id → projects, created_by → users,
           status, ok, created_at, summary (JSONB)
audit_log  id, org_id → orgs, actor_id → users, action, target, at, detail (JSONB)
```

`org_id` threads through every table, so per-org scoping can filter on it once multi-tenancy lands.
Only the variable manifest summary and the audit detail use `JSON().with_variant(JSONB,
"postgresql")`. The same models therefore run on SQLite (the gate) and Postgres (production).

Wiring stays in the one place where seams are assembled, `_build_server_state`, keyed off
`BAJUTSU_DATABASE_URL`. When the variable is unset, and on the local backend, `repository` stays
`None`, so behavior is unchanged without a database. A `db` extra carries `sqlalchemy`, `alembic`,
and `psycopg`.

The `Repository` exposes `record_run`, `get_run`, and `list_runs`. Once a database is wired, the
run history is served from the database (see 7c-4 below). Otherwise, the run history is served
from object storage.

#### 7b — GitHub OAuth and durable sessions (#148, #149)

Sessions moved off the in-memory `set[str]` behind a `SessionStore` seam. Locally, the store is
in-memory, and a restart drops the sessions. On the server, the store is durable and TTL'd
(time-to-live): it survives restarts and spans replicas. The server store was originally Redis.
[BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) later
moved it to a Postgres `sessions` table.

**GitHub OAuth via Authlib** was added as an extra browser sign-in. It consists of
`/api/oauth/login` and `/api/oauth/callback`, a CSRF state cookie, and an HttpOnly cookie that
holds an opaque session id, checked against the server-side store. A **GitHub-username
allowlist** (`BAJUTSU_OAUTH_ALLOWED_USERS`) gates the sign-in, and the login is bound to the
session as the user's identity.

OAuth coexists with the shared token (BE-0051). The token stays the operator credential, with full
access, for example for CI. OAuth is the per-user browser login. (**Superseded by
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md):** sign-in now follows
GitHub organization membership rather than this username allowlist. Once OAuth is configured, the
shared token narrows to worker traffic, and its operator-credential reach over other endpoints is
retired.)

`operations` stays provider-agnostic. Auth lives in the handler/app middleware, where
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
put it. An `authlib` extra carries the dependency.

#### 7c — identity, RBAC, audit, and quotas (single-tenant) (#150, #151, #152)

On the server backend with a database wired:

- **Identity and audit log** (#150): an OAuth login upserts the user into the default org. Run,
  record, and crawl append an `audit_log` entry that records who acted, on what, and when.
- **Per-user RBAC** (#151): each user has a role. A viewer reads only. An editor can run, record,
  crawl, approve, and save. An admin controls config, API key, and provider. The role derives from
  env policy (`BAJUTSU_OAUTH_ADMINS` / `BAJUTSU_OAUTH_VIEWERS`, default editor) and is recomputed on
  each login, so changing the policy needs no data migration. Enforcement lives in the auth gate,
  mirroring authentication. Only an OAuth session is gated, and the operator token stays
  full-access. (**Superseded by
  [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md):** the role now follows
  GitHub Team membership: the org's `editorTeam` grants editor, and one server-wide
  `BAJUTSU_OAUTH_ADMIN_TEAM` grants admin. Viewer is the base role. These teams replace the login
  lists. Recompute-on-login is kept.)
- **Per-user concurrency quota** (#152): `BAJUTSU_MAX_CONCURRENT_PER_USER` caps one user's in-flight
  jobs, so no single user starves the scarce device pool. While there is one org, the per-org quota
  is the existing global `max_concurrent`.
- **Database-backed run listing** (7c-4): a finished run is recorded into the `runs` table, with
  its id, org, who started it, pass/fail, and the manifest summary. The run-history endpoint then
  serves the org's recorded runs instead of scanning the artifact store. Without a database (local
  or stdlib serve), the listing still reads straight from the artifact store, so behavior is
  unchanged there. The listing is already org-scoped, and it resolves to the single default org
  until org resolution lands.

#### 8 — multi-tenancy (org model, resolution, enforcement, per-org storage)

Real multi-tenancy now ships on top of the single-tenant backend. The org model is **declared in
config**, which keeps the model app-agnostic:

```yaml
orgs:
  acme:
    members: [alice, bob]      # explicit GitHub logins, and/or…
    githubOrgs: [acme-gh]      # …everyone in this GitHub org
    apps: [demo, checkout]
  globex:
    members: [carol]
    apps: [other]
```

A login or app named in no org falls into the single `default` org. A config with no `orgs:`
block therefore stays single-tenant, and the shipped behavior is unchanged.

- **Org resolution (8b):** at OAuth login, the user is assigned their config org, and the
  assignment is persisted on the user row. Each later request resolves the actor's org from that
  row (`ServeState.org_of`). The org comes from an explicit `members` listing. Failing that, the
  org comes from the user's **GitHub org membership** (`org_for_identity`): the OAuth flow requests
  `read:org` and reads `/user/orgs`, and a `githubOrgs` entry maps a GitHub org to a bajutsu org.
  The run listing, run records, and audit entries are written and read under the resolved org.
- **Org-scope enforcement (8c):** a user sees only their org's apps. Starting a run, record, or
  crawl on another org's app, or saving into that app, returns 403. Reading a scenario or a run
  artifact outside the user's org reads as not-found, so the response leaks nothing.
- **Per-org storage (8d):** a server backend keeps each org's artifacts, scenarios, and baselines
  under its own object-store prefix (`<base><org>/`). The default org keeps the base prefix, so the
  single-tenant layout is untouched. The org travels in the job spec, so a worker reads and writes
  the same prefix that the control plane serves from.

A server-backend run executes on a worker, so the **worker** records the run into the system of
record. Give the worker the `db` extra and `BAJUTSU_DATABASE_URL`, and a finished run is recorded
under its org and actor. Without them, recording is a no-op: the run still works, but the listing
omits it.

## Alternatives considered

A general Linux PaaS (Cloud Run, Vercel, Fly Machines on Linux) was rejected outright. A Linux
PaaS cannot execute a run, because the Simulator exists on macOS alone, and hosting the web UI
necessarily means hosting a Mac-bound runner. Beyond that, each layer of the selected stack has a
rejected counterpart:

- **API / web.** Django (heavier, sync-first), Litestar, and the stdlib server lost to FastAPI. The stdlib server won't scale to auth and multi-user. FastAPI is async and reuses the Pydantic already in the dependency tree.
- **Reverse proxy + TLS.** nginx + certbot (more knobs, more setup) and Traefik lost to Caddy's near-zero-config automatic HTTPS.
- **AuthN/Z.** oauth2-proxy at the edge, Auth0/Clerk/WorkOS (managed, paid), and Google OAuth lost to GitHub OAuth. The audience already has GitHub, and the org model maps to GitHub orgs.
- **System of record.** SQLite and MySQL lost to PostgreSQL. SQLite has no concurrency for multi-user. PostgreSQL offers JSONB and broad managed availability.
- **Queue / job dispatch.** The original selection was Redis 7 + RQ. RabbitMQ/NATS and SQS were broker-only alternatives, and Celery/Dramatiq were heavier task frameworks. [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) replaced the original selection with a **Postgres `jobs` table leased over HTTP**. The design now needs no separate broker, cache, or task-framework process. Sessions moved to a Postgres table too.
- **Artifact storage.** AWS S3 (egress costs), MinIO (self-host), and GCS lost to Cloudflare R2. R2 is S3-compatible and has no egress fees.
- **macOS workers.** AWS EC2 Mac (24h minimum allocation, pricey), Scaleway Apple silicon, and self-hosted Mac minis lost to MacStadium Orka. Orka is the only option that gives a scalable, schedulable pool of clean Macs.
- **Secrets.** Vault (heavier) and env files (unsafe for public) lost to a cloud secret manager.
- **Observability.** An OpenTelemetry collector and Datadog (paid) are alternatives to the Sentry + Prometheus/Grafana baseline.
- **IaC + CI/CD.** Pulumi and manual provisioning (don't) lost to Terraform + GitHub Actions. The repo already lives in Actions.

## Progress

- [x] 7a — the persistence layer (`Repository`, `bajutsu/serve/server/db.py`) ([#144](https://github.com/bajutsu-e2e/bajutsu/pull/144), [#145](https://github.com/bajutsu-e2e/bajutsu/pull/145)).
- [x] 7b — GitHub OAuth (Authlib) and durable sessions ([#148](https://github.com/bajutsu-e2e/bajutsu/pull/148), [#149](https://github.com/bajutsu-e2e/bajutsu/pull/149)).
- [x] 7c — identity, RBAC, audit, and quotas for the single-tenant backend ([#150](https://github.com/bajutsu-e2e/bajutsu/pull/150)–[#152](https://github.com/bajutsu-e2e/bajutsu/pull/152)).
- [x] 8 — multi-tenancy: the org model, request resolution, enforcement, and per-org storage
  ([#156](https://github.com/bajutsu-e2e/bajutsu/pull/156), [#159](https://github.com/bajutsu-e2e/bajutsu/pull/159)).

All of the enabling software, through multi-tenancy, has landed (across #105–#159), so this
design-and-software item is **Implemented**. The *Deploy the hosted web UI service* item tracks
the remaining **operational deployment** of the public service separately: Phase 1 MVP, Phase 2
scale, and live-environment security hardening.

## References

`bajutsu/serve/`, [ci](../../docs/ci.md), [architecture](../../docs/architecture.md),
[reporting](../../docs/reporting.md), [cli](../../docs/cli.md#serve), the self-hosting counterpart
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), and
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md). BE-0055 designs
the operational logging that realizes the "structured JSON logs" observability row above.

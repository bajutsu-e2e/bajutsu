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

This item is the **design and the enabling software** for a publicly hosted web UI, and that
software has **fully landed**: beyond local/server parity, a server backend with auth, persistence,
RBAC, audit, quotas, and **multi-tenancy** now ships (see *Migration*, *Persistence and identity*,
and *§8 — multi-tenancy* below). What remains is the **operational deployment** onto real
infrastructure — provisioning the control plane, the macOS worker pool, the database, and object
storage, wiring production auth and secrets, and closing the live-environment security items. That
deployment is a different kind of work (infrastructure and operations, gated on paid macOS-only
capacity) and is tracked separately in its own item, *Deploy the hosted web UI service*, carved off
this umbrella the same way [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)
and [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
were. This proposal
selects a concrete server, database, storage, and deployment stack for turning the local
`bajutsu serve` (`bajutsu/serve/`) into a **shared, publicly hosted** service. The local UI today is a Tier-1
convenience that binds `127.0.0.1`, has no auth, and shells out to `bajutsu run` on the same host
([cli](../../docs/cli.md#serve) · [reporting](../../docs/reporting.md)). Going public changes the shape of
the system, not just its address: the web UI is a **thin launcher**, so hosting it really means
hosting the **runner** — which leads to a shared public service built from a Linux control plane
and a macOS worker pool.

Related: [architecture](../../docs/architecture.md) · [ci](../../docs/ci.md) · the self-hosting counterpart
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).

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
   └──────────────────────────┘  HTTP  └───────────────────────────────┘
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
The evolution of today's `serve`. Endpoints (auth'd):

- `GET /` → the project-scoped UI (scenario/app pickers come from the DB, **not** the filesystem).
- `POST /api/run` → validate the request **against the caller's project** (no client-supplied
  filesystem paths — see Security), write a `run` row, **enqueue a job** (a `queued` row in the
  Postgres `jobs` table), return its id.
- `GET /api/runs/stream/<id>` → **SSE** stream of live log lines (replaces the 1 s polling loop in
  the current UI), backed by the `LogBus` seam ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)
  replaced the Redis pub/sub originally planned here).
- `GET /runs/<id>/…` → serve report assets via **short-lived signed R2 URLs** (or proxy them),
  replacing today's local-filesystem `_serve_run_file`.

#### Job dispatch (control plane ↔ workers)

Job dispatch uses a Postgres `jobs` table leased over HTTP
([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)).
A run becomes a `queued` row; workers poll `POST /api/worker/lease` to lease it. After the run
completes, the worker uploads the run tree (including `console.log`) to object storage and posts
the result to `POST /api/worker/result`. The control plane records the finished run — the worker
needs no database access. No Redis or RQ.

#### macOS worker (stateless, isolated, ephemeral)
A small Python agent (launchd service) on each Orka-provisioned Mac:

1. Lease a job over HTTP (`POST /api/worker/lease`).
2. **Fetch the scenario** for that project from the control plane / object store (never a path the
   client chose).
3. Provision a **fresh, erased Simulator** (`bajutsu run --erase`) — public multi-tenant **must
   isolate**, which deliberately drops the local UI's fast `--no-erase` reuse loop.
4. Stream stdout to the control plane's `LogBus`; on finish, **upload the `runs/<id>/` tree to R2**
   and `POST` the result (exit code, run id, manifest summary) back to the control plane.
5. Tear the Simulator down.

### Deployment plan (phased)

#### Phase 1 — MVP (minimum viable product), ship fast
- **Control plane** containerized → **Fly.io** (or Render). Managed **Fly Postgres** (which also
  backs the job queue and sessions). Artifacts on **Cloudflare R2**. TLS via the platform / Caddy.
  **GitHub OAuth**.
- **Workers**: a **single MacStadium Orka** node running the agent. Scenarios + app configs stored
  per project in Postgres/R2.
- **Secrets** in Fly/Doppler; each org supplies its own `ANTHROPIC_API_KEY`.
- Goal: a logged-in user picks a project + scenario, hits Run, watches live logs, views the report —
  end to end, securely, on shared infra.

#### Phase 2 — scale
- Control plane → **Kubernetes** (GKE/EKS), managed **Cloud SQL/RDS**.
- **Orka autoscaling** Mac pool keyed off the Postgres `jobs` queue depth; per-org **concurrency quotas**.
- Artifact **CDN** (content delivery network) in front of R2; multi-region control plane if needed.
- Full observability (Sentry + Grafana dashboards + alerting on queue depth / worker health).

#### Cost shape
The Linux control plane, Postgres, and R2 are **cheap and elastic**. The **Macs dominate the
bill** and don't scale to zero cleanly (Orka nodes / EC2 Mac 24 h minimums). Design the pool around
**queue depth with a small warm floor**, and push the deterministic *gate* to ephemeral CI
([ci](../../docs/ci.md)) so the hosted pool only carries **interactive authoring**, not regression volume.

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

[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
also shipped the auth + input validation, and the pure helpers (`list_scenarios`, `list_runs`,
`run_command`, the `Job` model) are already isolated in `bajutsu/serve/helpers.py`. So:

1. Lift the (already-isolated) pure helpers and the embedded HTML into a FastAPI app as the v1
   frontend.
2. Provide a **queue-based `RunExecutor`** that enqueues onto the Postgres `jobs` table, plus a
   worker entrypoint that runs the same `bajutsu run` argv `run_job` builds today.
3. Provide a **server `LogBus`** and an **object-storage `ArtifactStore`** that returns R2
   signed-URL redirects in place of the local filesystem reads.
4. Add **OAuth + Postgres** (orgs/projects/runs) and a **per-project `ScenarioStore`** that resolves
   by id from storage instead of the local filesystem — **shipped for the single-tenant backend**;
   the **7a/7b/7c** breakdown below details what landed and what is still ahead.
5. Stand up **one Orka worker**; close the security items above; then scale the pool.

Each step is independently shippable and testable, and the deterministic core (`bajutsu run`, the
report) is unchanged throughout — only its *invocation* and *plumbing* move to the cloud.

### Persistence and identity — shipped for the single-tenant backend (7a/7b/7c)

Migration steps 1–3 had already landed (the five swap-in seams), and **step 4 — persistence and
identity — has now shipped for the single-tenant server backend.** It was built under three
invariants that still hold: local behavior is unchanged; every slice's tests run on the Linux gate
(no Simulator, no live Postgres/object storage); and each follows the existing seam pattern (a
Protocol with an injected implementation, lazy-imported behind an optional extra so the default
`serve`/CLI path never loads it). "Single-tenant" here means one fixed default org holds every user;
the multi-tenant, org-scoped pieces landed later on top of this backend (see *§8 — multi-tenancy* below).

#### 7a — the persistence layer (#144, #145)

A fifth seam, `Repository` (`bajutsu/serve/server/db.py`), built the same way as `ObjectStore`: a
Protocol, an injected SQLAlchemy 2.0 implementation, an env-driven factory, and a lazy import. The
table set and foreign keys are fixed up front in the first Alembic migration (adding them later is
painful under SQLite, which the gate uses); `users.role` was added by a follow-up migration (0002):

```
orgs       id, slug (unique), name, created_at
users      id, org_id → orgs, email (unique), github_login, role, created_at
projects   id, org_id → orgs, name (= the config app name), created_at, unique(org_id, name)
runs       id, org_id → orgs, project_id → projects, created_by → users,
           status, ok, created_at, summary (JSONB)
audit_log  id, org_id → orgs, actor_id → users, action, target, at, detail (JSONB)
```

`org_id` threads through every table so per-org scoping can filter on it once multi-tenancy lands.
Only the variable manifest summary and audit detail use `JSON().with_variant(JSONB, "postgresql")`,
so the same models run on SQLite (the gate) and Postgres (production). Wiring stays in the one place
seams are assembled, `_build_server_state`, keyed off `BAJUTSU_DATABASE_URL`; unset — and the local
backend — leaves `repository` as `None`, so behavior is unchanged without a database. A `db` extra
carries `sqlalchemy`, `alembic`, and `psycopg`. (The `Repository` exposes `record_run`/`get_run`/
`list_runs`; the run history is served from the DB once one is wired — see 7c-4 below — and from
object storage otherwise.)

#### 7b — GitHub OAuth and durable sessions (#148, #149)

Sessions moved off the in-memory `set[str]` behind a `SessionStore` seam: in-memory locally (a
restart drops them), a durable TTL'd store on the server (surviving restarts, spanning replicas —
originally Redis; [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)
later moved that server store to a Postgres `sessions` table).
**GitHub OAuth via Authlib** was added as an extra browser sign-in — `/api/oauth/login` +
`/api/oauth/callback`, a CSRF state cookie, and an HttpOnly cookie holding an opaque session id
(checked against the server-side store) — gated by a **GitHub-username
allowlist** (`BAJUTSU_OAUTH_ALLOWED_USERS`), with the login bound to the session as the user's
identity. It coexists with the shared token (BE-0051): the token stays the operator credential (full
access, e.g. for CI), OAuth is the per-user browser login. `operations` stays provider-agnostic;
auth lives in the handler/app middleware, where
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
put it. An `authlib` extra carries the dependency.

#### 7c — identity, RBAC, audit, and quotas (single-tenant) (#150, #151, #152)

On the server backend with a database wired:

- **Identity + audit log** (#150): an OAuth login upserts the user (into the default org), and
  run/record/crawl append an `audit_log` entry recording who acted, on what, and when.
- **Per-user RBAC** (#151): each user has a role — viewer (reads only), editor (run/record/crawl/
  approve/save), admin (config / API key / provider). The role is derived from env policy
  (`BAJUTSU_OAUTH_ADMINS` / `BAJUTSU_OAUTH_VIEWERS`, default editor) and recomputed on each login, so
  changing the policy needs no data migration. Enforcement lives in the auth gate (mirroring authN);
  only an OAuth session is gated — the operator token stays full-access.
- **Per-user concurrency quota** (#152): `BAJUTSU_MAX_CONCURRENT_PER_USER` caps one user's in-flight
  jobs so no single user starves the scarce device pool (the per-org quota is just the existing
  global `max_concurrent` while there is one org).
- **DB-backed run listing** (7c-4): a finished run is recorded into the `runs` table (its id, org,
  who started it, pass/fail, and the manifest summary), and the run-history endpoint serves the org's
  recorded runs instead of scanning the artifact store. Without a database (local / stdlib serve) the
  listing still reads straight from the artifact store, so behavior is unchanged there. The listing is
  already org-scoped — it just resolves to the single default org until org resolution lands.

#### 8 — multi-tenancy (org model, resolution, enforcement, per-org storage)

Real multi-tenancy now ships on top of the single-tenant backend. The org model is **declared in
config**, so it stays app-agnostic:

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

A login or app named in no org falls into the single `default` org, so a config with no `orgs:`
block stays single-tenant — the shipped behavior is unchanged.

- **Org resolution (8b):** at OAuth login the user is assigned their config org and it's persisted
  on the user row; each later request resolves the actor's org from that row (`ServeState.org_of`).
  The org comes from an explicit `members` listing or, failing that, the user's **GitHub org
  membership** (`org_for_identity`): the OAuth flow requests `read:org` and reads `/user/orgs`, and a
  `githubOrgs` entry maps a GitHub org to a bajutsu org. The run listing, run records, and audit
  entries are written/read under the resolved org.
- **Org-scope enforcement (8c):** a user sees only their org's apps; starting a run/record/crawl on,
  or saving into, another org's app returns 403; reading a scenario or a run artifact outside the
  org reads as not-found (non-leaky).
- **Per-org storage (8d):** a server backend keeps each org's artifacts/scenarios/baselines under
  its own object-store prefix (`<base><org>/`); the default org keeps the base prefix, so the
  single-tenant layout is untouched. The org travels in the job spec, so a worker reads and writes
  the same prefix the control plane serves from.

A server-backend run executes on a worker, so the **worker** records it into the system of record:
give the worker the `db` extra and `BAJUTSU_DATABASE_URL` and a finished run is recorded under its
org/actor (no-op without them — the run still works, it just isn't listed).

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
- **Queue / job dispatch**: the original selection was Redis 7 + RQ (with RabbitMQ/NATS and SQS as
  broker-only alternatives, and Celery/Dramatiq as heavier task frameworks), but
  [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) replaced
  it with a **Postgres `jobs` table leased over HTTP** — no separate broker, cache, or task-framework
  process, and sessions moved to a Postgres table too.
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

## Progress

- [x] 7a — the persistence layer (`Repository`, `bajutsu/serve/server/db.py`) ([#144](https://github.com/bajutsu-e2e/bajutsu/pull/144), [#145](https://github.com/bajutsu-e2e/bajutsu/pull/145)).
- [x] 7b — GitHub OAuth (Authlib) and durable sessions ([#148](https://github.com/bajutsu-e2e/bajutsu/pull/148), [#149](https://github.com/bajutsu-e2e/bajutsu/pull/149)).
- [x] 7c — identity, RBAC, audit, and quotas for the single-tenant backend ([#150](https://github.com/bajutsu-e2e/bajutsu/pull/150)–[#152](https://github.com/bajutsu-e2e/bajutsu/pull/152)).
- [x] 8 — multi-tenancy: the org model, request resolution, enforcement, and per-org storage
  ([#156](https://github.com/bajutsu-e2e/bajutsu/pull/156), [#159](https://github.com/bajutsu-e2e/bajutsu/pull/159)).

All of the enabling software — through multi-tenancy — has landed (across #105–#159), so this
design-and-software item is **Implemented**. The remaining **operational deployment** of the public
service (Phase 1 MVP and Phase 2 scale, plus live-environment security hardening) is tracked
separately in the *Deploy the hosted web UI service* item.

## References

`bajutsu/serve/`, [ci](../../docs/ci.md), [architecture](../../docs/architecture.md),
[reporting](../../docs/reporting.md), [cli](../../docs/cli.md#serve), the self-hosting counterpart
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), and
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) — which designs
the operational logging that realizes the "structured JSON logs" observability row above.

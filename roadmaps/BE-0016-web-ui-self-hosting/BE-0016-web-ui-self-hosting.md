**English** · [日本語](BE-0016-web-ui-self-hosting-ja.md)

# BE-0016 — Self-hosting of the web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0016](BE-0016-web-ui-self-hosting.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0016") |
| Implementing PR | [#103](https://github.com/bajutsu-e2e/bajutsu/pull/103), [#154](https://github.com/bajutsu-e2e/bajutsu/pull/154), [#365](https://github.com/bajutsu-e2e/bajutsu/pull/365), [#367](https://github.com/bajutsu-e2e/bajutsu/pull/367), [#507](https://github.com/bajutsu-e2e/bajutsu/pull/507), [#674](https://github.com/bajutsu-e2e/bajutsu/pull/674) |
| Topic | Hosting the web UI |
| Related | [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) |
<!-- /BE-METADATA -->

## Introduction

This proposal describes how to **stand up and operate** the web UI on **your own Mac(s)**, as the
self-hosted counterpart to the managed, multi-tenant public stack in
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md). It documents two tiers:

- **Tier A — today-ready.** What runs *today* with the existing stdlib `bajutsu serve`
  (`bajutsu/serve/`), already shipped as [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)
  and made safe to expose by the hardening in
  [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  (token auth + input validation), plus operational configuration (LaunchAgent, auto-login,
  Tailscale). The one small piece of code it adds is `serve --emit-launchagent`, which prints the
  LaunchAgent plist for you. The step-by-step operator guide is
  [docs/self-hosting.md](../../docs/self-hosting.md).
- **Tier B — the server backend with a Mac worker pool.** A self-hosted version of
  [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)'s server backend,
  with every managed service replaced by self-hosted open-source software (OSS). Its single-node form —
  **including multi-org isolation** — has shipped and is runnable today
  ([`deploy/self-host/`](../../deploy/self-host/)); what remains is growing that node into a
  fault-tolerant, org-fair *pool*. The fully managed public cloud stays BE-0015's.

This item now records the **shipped self-hosting baselines** — Tier A and the single-node Tier B
(including multi-org isolation, the per-org concurrency cap, and worker-liveness re-queue). The
further work of growing that single node into a fault-tolerant, org-fair *pool* has been split into
five focused roadmap items, each tracked on its own (see *Growing one node into a pool — tracked
separately* below); this item is the umbrella that links them.

## Motivation

Some teams cannot or will not put their test infrastructure on a managed cloud — for cost, data
residency, or policy reasons. They want to run the web UI on hardware they own. Self-hosting bajutsu
is **not** like self-hosting a normal web service, because the runner drives an **iOS Simulator**,
which constrains where and how the process can run. This proposal captures both the immediately
usable path (Tier A), the runnable single-node multi-tenant backend (Tier B today), and the design for
growing it into a pool, so that the operational design is not lost and a team can adopt it incrementally.

## Detailed design

### The macOS requirement that shapes self-hosting

The runner drives an **iOS Simulator**, and the Simulator needs a **GUI login session**
(WindowServer / the Aqua session) — it will **not** run from a headless daemon. Every self-hosting
choice below follows from this:

- Run the process as a **`LaunchAgent`** (per-user, GUI session), **not** a `LaunchDaemon`.
- The Mac must be **auto-logged-in** so it recovers a GUI session after reboot (note FileVault
  requires one interactive login after a cold boot before auto-login proceeds).
- **Disable sleep** so the session stays alive (`caffeinate` / `pmset`).

This is the operational difference between hosting bajutsu and hosting a normal web service.

### Tier A — run it today (single Mac, current `serve.py`)

The only thing that actually runs today is the stdlib `bajutsu serve` + the CLI, already shipped as
[BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) and hardened for
exposure by [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md).
This tier makes it safely reachable for a team with **near-zero code change** (only the
`--emit-launchagent` helper) — it is **runnable today** on the existing `bajutsu serve`. See
[docs/self-hosting.md](../../docs/self-hosting.md) for the full walkthrough.

```
            team laptops
               │  HTTPS (inside the Tailscale tailnet only)
               ▼
   ┌─────────────────────────────────────┐
   │  Mac mini (Apple Silicon)           │
   │  · Xcode + Simulator + idb_companion│
   │  · bajutsu serve  (LaunchAgent)     │  ← 127.0.0.1:8765
   │  · tailscale serve → tailnet HTTPS  │
   │  · auto-login + caffeinate          │
   └─────────────────────────────────────┘
```

**Hardware.** Mac mini M2/M4, 16 GB RAM (32 GB if running several Simulators at once).

**1) Run `serve` as a LaunchAgent** — keep it bound to `127.0.0.1` (never exposed raw). Generate the
plist with `bajutsu serve --emit-launchagent --config <config.yml> --token <token>`, which prints a
`com.bajutsu.serve.plist` running `python -m bajutsu serve --host 127.0.0.1 --port 8765 --config
<config.yml>` with `RunAtLoad` + `KeepAlive`, the token in `EnvironmentVariables` (so it never
appears in `ps`), and stdout/stderr to `~/Library/Logs/`. Load it with
`launchctl bootstrap gui/$(id -u) …`. It must be a **LaunchAgent** (GUI session), not a
LaunchDaemon.

**2) Keep the session alive.** Enable auto-login in System Settings, and disable sleep
(`sudo pmset -a sleep 0 disablesleep 1`).

**3) Expose it — Tailscale (recommended).** BE-0051 gives serve token auth on every request and
confines `/api/run` / `/api/record` to the app's scenarios dir, so exposure is no longer
unauthenticated. Even so, the safe default is a **private tailnet**, not `0.0.0.0` on the public
internet — identity-based access plus automatic TLS, no public surface:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net (reachable only inside the tailnet)
```

**Only** if you need a real internal hostname, front it with **Caddy** for TLS + basic auth
(`reverse_proxy 127.0.0.1:8765` behind `basic_auth`) — but keep it off the open internet.

This tier is usable by a team **today**.

### Tier B — self-hosted control plane with a Mac worker pool

Tier B runs BE-0015's **server backend** — a FastAPI control plane on a Linux node, with a pool of
Mac workers leasing jobs over a Tailscale tailnet. Much of this has **already shipped**, so this
section is in two parts: **what runs today** (the runnable stack and the multi-tenant isolation that
already crosses the org boundary), and **what remains** to grow that single node into a fault-tolerant
pool (cross-org fairness, capability routing, high availability, and observability). The genuinely
future piece is the **fully managed public cloud** — a hosted Mac pool plus infrastructure-as-code —
which is [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)'s,
not this item's.

#### What runs today

The single-node server backend is runnable now — the compose stack, walkthrough, and multi-org setup
are in [`deploy/self-host/`](../../deploy/self-host/) and
[docs/self-hosting.md](../../docs/self-hosting.md#tier-b--self-hosting-the-server-backend). It ships:

- **The control-plane stack.** A `docker-compose.yml` wires `postgres`, `minio`
  (S3-compatible storage), a one-shot `migrate` (Alembic to head), the `bajutsu` app
  (`serve --asgi --backend=server`), and an optional `caddy` profile for a public hostname — each
  stateful service on a named volume. The Mac worker is **not** containerized: it needs the Aqua
  graphical user interface (GUI) session, exactly like Tier A, and runs `bajutsu worker`.
- **Identity and roles.** GitHub OAuth (open authorization) in the app itself (not a separate
  identity provider), with three roles — admin / editor / viewer (role-based access control, RBAC).
  [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) derives these from GitHub
  organization and Team membership rather than a hand-maintained login allowlist: org membership gates
  sign-in and grants viewer, the org's `editorTeam` grants editor, and one server-wide admin Team
  grants admin.
- **Multi-org isolation.** Declaring an `orgs:` block in the mounted config turns the same backend
  multi-tenant: each user is scoped to their org by GitHub login or GitHub-org membership (the login
  requests the `read:org` scope), sees only that org's targets, and a cross-org run / scenario /
  artifact read returns not-found or 403. Each org's artifacts, scenarios, and baselines live under
  their own object-store prefix (`org_prefix`). With no `orgs:` block the backend stays single-tenant
  (one default org).
- **Job distribution.** The control plane inserts a `queued` row into the Postgres `jobs` table via
  `DbQueueExecutor`; a `bajutsu worker` polls `POST /api/worker/lease` over HTTP to lease it, runs
  the job on an **ephemeral Simulator** (`--erase`), uploads the `runs/<id>/` tree (including
  `console.log`) to MinIO, and posts the result back to `POST /api/worker/result`. No Redis or
  RQ — the worker needs only HTTP and (optionally) the object-store client
  ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)).
- **Quotas.** A **global** concurrency cap (`--max-concurrent-runs`, default 4), a **per-user** cap
  (`max_concurrent_per_user`), and a **per-org** cap (`max_concurrent_per_org`,
  [#367](https://github.com/bajutsu-e2e/bajutsu/pull/367); `0` = unlimited, so single-tenant is
  unchanged) keep one caller — or one org — from monopolizing the scarce devices, enforced atomically
  under a lock in `try_register` (`bajutsu/serve/jobs.py`). An over-cap job is rejected (HTTP 429);
  turning that rejection into fair *holding* is one of the split-out items below.
- **Worker liveness and job re-queue.** Every lease carries a timer: `bajutsu worker` sends a
  periodic heartbeat (`POST /api/worker/heartbeat`) that renews the lease, and the control plane
  reclaims any lease with no heartbeat past a timeout — returning the job to `queued` for another
  worker (`reclaim_expired_leases`, swept on each `lease_job`), failing a poison job past an attempt
  cap rather than re-queuing it forever ([#507](https://github.com/bajutsu-e2e/bajutsu/pull/507)).

The mapping below is the menu for the remaining managed services; the **Shipped today** column marks
what `deploy/self-host/` already uses, versus what is still a suggested choice for scale-out.

| Cloud-hosting selection | Self-hosted replacement | Shipped today |
|---|---|---|
| Fly.io / Render (control plane) | Your own **Linux node** + **Docker Compose** | ✅ |
| Fly Postgres | **postgres** container | ✅ |
| Cloudflare R2 (artifacts) | **MinIO** (S3-compatible, self-hosted) | ✅ |
| GitHub OAuth (Authlib) | GitHub OAuth in the app, or a self-hosted identity provider (Authelia / Keycloak / oauth2-proxy) | ✅ (in-app GitHub OAuth) |
| Caddy / TLS | **Caddy** (Let's Encrypt or an internal CA) | ✅ (optional `caddy` profile) |
| MacStadium Orka (Mac pool) | Your own **Mac mini pool (1…N)** + worker as a `LaunchAgent` | ✅ (single worker; pool = below) |
| Doppler (secrets) | **SOPS + age** or Vault (or a permission-locked `.env`) | `.env` |
| Sentry / Grafana (observability) | **GlitchTip** + self-hosted **Prometheus/Grafana** | ⏳ (see below) |

```
        team laptops
           │  HTTPS (Tailscale tailnet, or Caddy at a hostname)
           ▼
   ┌────────────────────────────────────────┐  lease  ┌──────────────────────────┐
   │  Linux node — docker compose           │ ◀────── │  Mac worker × N          │
   │  bajutsu serve --asgi --backend=server │   HTTP  │  bajutsu worker (polls    │
   │  postgres (jobs = queue) · minio       │ ──────▶ │   lease/heartbeat/result) │
   │  (· caddy)                             │  result │  bajutsu run --erase      │
   └────────────────────────────────────────┘         │  Simulator (GUI session) │
                       └──────────── Tailscale tailnet ───────┴──────────────────┘
```

**Sizing.** Video evidence is heavy — budget a few hundred GB for MinIO. The Linux node is modest
(2 vCPU / 4 GB; it can even co-locate on the Mac via OrbStack). The **Macs dominate** the footprint.

#### Growing one node into a pool — tracked separately

Everything above runs on **one** Linux node with **one or a few** Mac workers. Turning that into a
fault-tolerant pool that stays fair across orgs is further work. Because each piece is independent,
substantial, and mostly a deployment or operations concern the Linux-only `make check` gate cannot
exercise (no Docker, no Mac), it has been **split out of this umbrella into five focused roadmap
items** — the per-org cap and worker-liveness re-queue this work built on have already shipped (see
*What runs today*). Each successor names its shipped base, its concrete design, and how it is
verified; three expose a machine-checkable surface — fully for weighted-fair dispatch, partially for
capability routing and observability — while control-plane scale-out and high availability are
verified by hand. By topic:

1. **Weighted-fair cross-org job dispatch** — turn the per-org cap's 429 rejection into *holding*:
   per-org pending queues and a round-robin dispatcher that keeps the scarce pool fair under
   contention. The only piece that is *fully* gate-checkable (unit-tested on `ServeState`, no Mac);
   it lands in `bajutsu/serve/operations.py` and `jobs.py`.
2. **Capability-routed job queues** — per-capability queues (`q:ios18`, `q:ipad`) so a job is only
   ever leased by a worker that can run it. The lease filter is gate-checkable; the multi-device
   routing is verified by hand. No change to the deterministic `run`.
3. **Control-plane scale-out behind a load balancer** — N stateless app replicas behind a
   least-connections load balancer, no sticky sessions (sessions live in Postgres). Verified by hand.
4. **Self-hosted high availability** — remove the lone stateful single point of failure (SPOF): a
   Postgres primary + replica, a redundant load balancer, and a backed-up object store. Verified by
   hand.
5. **Serve metrics and observability** — a `/metrics` endpoint (queue depth, in-flight jobs per org,
   run durations, worker liveness) plus optional Prometheus / Grafana containers. The endpoint is
   gate-checkable; the containers are verified by hand.

Each of these five is its own proposal and names this item as its origin; this item stays the
umbrella holding the shipped self-hosting baselines together. (The reciprocal links from this item's
metadata are added once CI allocates the successors' BE IDs.)

#### Multi-tenancy — the four axes, reconciled

The multi-tenant design rests on four axes; three have shipped, one (fairness) is the
*Weighted-fair cross-org job dispatch* successor item above.

- **Data isolation — shipped.** Each org's artifacts, scenarios, and baselines live under their own
  object-store prefix, and every query is org-scoped so a cross-org read returns not-found / 403.
  Postgres row-level security (RLS) as defense-in-depth is a future hardening on top of the
  app-layer scoping that already enforces the boundary.
- **Execution isolation — shipped (with a known gap).** Each run gets an ephemeral Simulator
  (`--erase`) so nothing bleeds between runs on a shared Mac. The remaining gaps: secrets are a
  **single server-level** API key today, not **per-org** injected secrets; and **per-job egress
  controls** and **dedicating whole Macs** to high-isolation tenants are operator choices, not yet
  automated.
- **Fairness / noisy-neighbor — partly shipped.** The per-user cap and now the **per-org** cap exist
  (both reject over-cap requests); weighted-fair scheduling across the scarce pool (holding rather
  than rejecting) is the *Weighted-fair cross-org job dispatch* successor item.
- **Authorization boundary — shipped.** Every request carries its org (the OAuth claim); org scope is
  enforced on each endpoint, RBAC applies within the org, and a worker is handed only its job's org
  context.

In short: the **front load balancer balances only the cheap control plane**; the **real distribution
is the pull queue plus per-worker slots**; and **multi-tenancy = org-scoped data + ephemeral Simulator
(execution) + per-tenant quotas and fair scheduling (resources)**. The data, execution, and
authorization axes already cross the org boundary; because the Mac pool cannot scale elastically,
**fairness and availability are what the remaining work is about.**

### Recommendation

Start with **Tier A** (Tailscale + LaunchAgent) — it runs the real, existing system safely for a
team with essentially no code, on a single Mac. Move to **Tier B** once you need multi-user history
and isolation: the single-node control plane, including **multi-org isolation**, is runnable today
([`deploy/self-host/`](../../deploy/self-host/)). Take on the **split-out pool work** (cross-org
fairness, capability routing, control-plane scale-out, high availability, observability — each a
separate roadmap item that names this one as its origin) only when the pool and the contention are
real — and the **fully managed public cloud** offering remains
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)'s, not this
item's.

## Alternatives considered

- **`LaunchDaemon` instead of `LaunchAgent`** — rejected: a daemon has no GUI/Aqua session, and the
  Simulator will not run without one. The per-user `LaunchAgent` (plus auto-login and caffeinate) is
  mandatory, not a preference.
- **Binding `0.0.0.0` to the public internet** — rejected: even with BE-0051's token auth, a public
  bind widens the attack surface needlessly. Use a private tailnet (Tailscale) and, only for an
  internal hostname, Caddy + basic auth. serve already refuses a non-loopback `--host` without a
  token.
- **Embedding the plist as a template file (or a shell snippet in docs) vs `serve --emit-launchagent`**
  — rejected: generating the plist from the same flags the operator already passes keeps the argv,
  interpreter path, and token placement correct and in one place, with no copy-paste drift.
- **Schema-per-tenant / DB-per-tenant vs shared schema + `org_id` + RLS** — rejected for self-host
  ops: per-tenant schemas/DBs multiply migration and connection management overhead; a shared schema
  with `org_id` and Postgres RLS gives isolation with far less operational burden.
- **Per-user quota alone vs adding a per-org quota** — the per-user cap already shipped, and in a
  single-tenant deploy it is enough. But it does not bound an *org* whose many users each stay under
  the per-user cap, so a multi-org pool still needs a per-org cap layered on the same mechanism
  (shipped; the holding half is the *Weighted-fair cross-org job dispatch* item). Keeping both caps —
  not replacing one with the other — is deliberate.
- **Pure FIFO scheduling vs weighted-fair scheduling** — rejected for the cross-org case: pure
  first-in, first-out (FIFO) lets one org monopolize the scarce Mac pool. Per-org pending queues with
  a quota-respecting round-robin dispatcher keep the pool fair under contention.

## Progress

This item is **Implemented**: it tracks the shipped self-hosting baselines. Growing the single node
into a fault-tolerant, org-fair pool is tracked as five separate roadmap items (see *Growing one node
into a pool — tracked separately*), each of which carries its own `Progress`.

- [x] Tier A — run `serve` as a LaunchAgent, keep the session alive, expose via Tailscale.
- [x] Tier B, single node — the control-plane compose stack, GitHub OAuth + RBAC, multi-org isolation, HTTP-leased job distribution over the Postgres `jobs` table with ephemeral Simulators, and global + per-user quotas (`deploy/self-host/`).
- [x] Per-org concurrency cap (`max_concurrent_per_org`) ([#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)).
- [x] Worker liveness and job re-queue — lease heartbeat + timeout-based reclaim over the `jobs` table, with a re-queue attempt cap.
- [x] Split the remaining pool work (weighted-fair dispatch, capability-routed queues, control-plane scale-out, high availability, observability) into five focused roadmap items that name this one as their origin.

The single node is runnable now ([#103](https://github.com/bajutsu-e2e/bajutsu/pull/103), [#154](https://github.com/bajutsu-e2e/bajutsu/pull/154), [#365](https://github.com/bajutsu-e2e/bajutsu/pull/365), [#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)); the pool-growth work now lives in the split-out items.

- Worker liveness & job re-queue — heartbeat (`POST /api/worker/heartbeat`) that renews a lease, `reclaim_expired_leases` (swept on `lease_job`) that re-queues a dead worker's lease and fails a job past its attempt cap, and a worker-side heartbeat loop; also re-grounded this item's Tier B stack, diagrams, and remaining-work items on BE-0106's Redis-free HTTP worker model ([#507](https://github.com/bajutsu-e2e/bajutsu/pull/507)).
- Split the remaining pool work into five focused roadmap items (weighted-fair dispatch, capability-routed queues, control-plane scale-out, high availability, observability) and flipped this umbrella to Implemented, recording the shipped self-hosting baselines ([#674](https://github.com/bajutsu-e2e/bajutsu/pull/674)).

## References

`bajutsu/serve/`, [docs/self-hosting.md](../../docs/self-hosting.md) (the Tier A and Tier B
operator guide), [`deploy/self-host/`](../../deploy/self-host/) (the runnable single-node compose
stack), [cli.md](../../docs/cli.md#serve), [ci.md](../../docs/ci.md),
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
(the hardening that makes exposure safe),
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) (the structured
serve logs the observability work builds on),
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the cloud-hosting
counterpart), [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)

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

This proposal describes how to **stand up and operate** the web UI on **your own Mac(s)**. It is the self-hosted counterpart to the managed, multi-tenant public stack in [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md). It documents two tiers:

- **Tier A — today-ready.** Tier A runs *today* on the existing stdlib `bajutsu serve` (`bajutsu/serve/`). That server shipped as [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md), and the hardening in [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) (token auth and input validation) made it safe to expose. Tier A adds operational configuration: a LaunchAgent, auto-login, and Tailscale. The one small piece of code it adds is `serve --emit-launchagent`, which prints the LaunchAgent plist. The step-by-step operator guide is [docs/self-hosting.md](../../docs/self-hosting.md).
- **Tier B — the server backend with a Mac worker pool.** Tier B is a self-hosted version of the server backend from [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), with every managed service replaced by self-hosted open-source software (OSS). Its single-node form, **including multi-org isolation**, has shipped and is runnable today ([`deploy/self-host/`](../../deploy/self-host/)). What remains is growing that node into a fault-tolerant, org-fair *pool*. The fully managed public cloud stays with BE-0015.

This item now records the **shipped self-hosting baselines**: Tier A and the single-node Tier B. Tier B includes multi-org isolation, the per-org concurrency cap, and worker-liveness re-queue.

The further work of growing that single node into a fault-tolerant, org-fair *pool* has been split into five focused roadmap items. Each item is tracked on its own (see *Growing one node into a pool — tracked separately* below). This item is the umbrella that links them.

## Motivation

Some teams cannot or will not put their test infrastructure on a managed cloud. Their reasons are cost, data residency, or policy. These teams want to run the web UI on hardware they own.

Self-hosting bajutsu is **not** like self-hosting a normal web service. The runner drives an **iOS Simulator**, which constrains where and how the process can run.

This proposal captures three things, so that the operational design is not lost and a team can adopt it incrementally:

- The immediately usable path (Tier A).
- The runnable single-node multi-tenant backend (Tier B today).
- The design for growing that backend into a pool.

## Detailed design

### The macOS requirement that shapes self-hosting

The runner drives an **iOS Simulator**, and the Simulator needs a **GUI login session**
(WindowServer, the Aqua session). The Simulator will **not** run from a headless daemon. Every
self-hosting choice below follows from this requirement:

- Run the process as a **`LaunchAgent`** (per-user, GUI session), **not** a `LaunchDaemon`.
- Configure the Mac to **auto-login**, so it recovers a GUI session after a reboot. FileVault
  requires one interactive login after a cold boot before auto-login proceeds.
- **Disable sleep** so the session stays alive (`caffeinate` / `pmset`).

These requirements are the operational difference between hosting bajutsu and hosting a normal web service.

### Tier A — run it today (single Mac, current `serve.py`)

Tier A runs the stdlib `bajutsu serve` plus the CLI. [BE-0011](../BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)
shipped both, and [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
hardened them for exposure. Tier A makes the existing `bajutsu serve` safely reachable for a team
with **near-zero code change**, which amounts to the `--emit-launchagent` helper. Tier A is
**runnable today**. See [docs/self-hosting.md](../../docs/self-hosting.md) for the full
walkthrough.

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

**1) Run `serve` as a LaunchAgent.** Keep `serve` bound to `127.0.0.1`, and never expose it raw.
Generate the plist with `bajutsu serve --emit-launchagent --config <config.yml> --token <token>`.
The command prints a `com.bajutsu.serve.plist` that runs `python -m bajutsu serve --host 127.0.0.1
--port 8765 --config <config.yml>` with `RunAtLoad` and `KeepAlive`. The plist puts the token in
`EnvironmentVariables`, so the token never appears in `ps`, and sends stdout and stderr to
`~/Library/Logs/`. Load the plist with `launchctl bootstrap gui/$(id -u) …`. The job must be a
**LaunchAgent** (GUI session), not a LaunchDaemon.

**2) Keep the session alive.** Enable auto-login in System Settings, and disable sleep
(`sudo pmset -a sleep 0 disablesleep 1`).

**3) Expose it with Tailscale (recommended).** BE-0051 gives serve token auth on every request. It
also confines `/api/run` and `/api/record` to the app's scenarios dir, so exposure is no longer
unauthenticated. Even so, the safe default is a **private tailnet**, not `0.0.0.0` on the public
internet. A tailnet gives identity-based access and automatic TLS, with no public surface:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net (reachable only inside the tailnet)
```

Only if you need a real internal hostname, front `serve` with **Caddy** for TLS and basic auth
(`reverse_proxy 127.0.0.1:8765` behind `basic_auth`). Keep that setup off the open internet.

A team can use Tier A **today**.

### Tier B — self-hosted control plane with a Mac worker pool

Tier B runs the **server backend** of BE-0015: a FastAPI control plane on a Linux node, with a pool
of Mac workers that lease jobs over a Tailscale tailnet. Much of Tier B has **already shipped**, so
this section has two parts:

- **What runs today**: the runnable stack, and the multi-tenant isolation that already crosses the
  org boundary.
- **What remains**: growing the single node into a fault-tolerant pool, with cross-org fairness,
  capability routing, high availability, and observability.

The genuinely future piece is the **fully managed public cloud**: a hosted Mac pool plus
infrastructure-as-code. That piece belongs to
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), not to this item.

#### What runs today

The single-node server backend is runnable now. The compose stack, walkthrough, and multi-org setup
are in [`deploy/self-host/`](../../deploy/self-host/) and
[docs/self-hosting.md](../../docs/self-hosting.md#tier-b--self-hosting-the-server-backend). The
backend ships six pieces:

- **The control-plane stack.** A `docker-compose.yml` wires `postgres`, `minio` (S3-compatible
  storage), a one-shot `migrate` (Alembic to head), the `bajutsu` app
  (`serve --asgi --backend=server`), and an optional `caddy` profile for a public hostname. Each
  stateful service uses a named volume. The Mac worker is **not** containerized. Like Tier A, the
  worker needs the Aqua graphical user interface (GUI) session, and it runs `bajutsu worker`.
- **Identity and roles.** GitHub OAuth (open authorization) runs in the app itself, not in a
  separate identity provider. Three roles apply: admin, editor, and viewer (role-based access
  control, RBAC). [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) derives
  the roles from GitHub organization and Team membership, not from a hand-maintained login
  allowlist. Org membership gates sign-in and grants viewer. The org's `editorTeam` grants editor.
  One server-wide admin Team grants admin.
- **Multi-org isolation.** An `orgs:` block in the mounted config turns the same backend
  multi-tenant. Each user is scoped to their org by GitHub login or GitHub-org membership (the login
  requests the `read:org` scope). A user sees only that org's targets. A cross-org read of a run,
  scenario, or artifact returns not-found or 403. Each org's artifacts, scenarios, and baselines
  live under their own object-store prefix (`org_prefix`). With no `orgs:` block, the backend stays
  single-tenant, with one default org.
- **Job distribution.** The control plane inserts a `queued` row into the Postgres `jobs` table via
  `DbQueueExecutor`. A `bajutsu worker` polls `POST /api/worker/lease` over HTTP to lease the row.
  The worker runs the job on an **ephemeral Simulator** (`--erase`), uploads the `runs/<id>/` tree
  (including `console.log`) to MinIO, and posts the result back to `POST /api/worker/result`. The
  design uses no Redis and no RQ. The worker needs HTTP and, optionally, the object-store client
  ([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)).
- **Quotas.** Three concurrency caps keep one caller, or one org, from monopolizing the scarce
  devices: a **global** cap (`--max-concurrent-runs`, default 4), a **per-user** cap
  (`max_concurrent_per_user`), and a **per-org** cap (`max_concurrent_per_org`,
  [#367](https://github.com/bajutsu-e2e/bajutsu/pull/367)). A per-org value of `0` means
  unlimited, so single-tenant is unchanged. `try_register` (`bajutsu/serve/jobs.py`) enforces the
  caps atomically under a lock. An over-cap job is rejected (HTTP 429). One of the split-out items
  below turns that rejection into fair *holding*.
- **Worker liveness and job re-queue.** Every lease carries a timer. `bajutsu worker` sends a
  periodic heartbeat (`POST /api/worker/heartbeat`) that renews the lease. The control plane
  reclaims any lease whose heartbeat is overdue past a timeout, and returns the job to `queued` for
  another worker (`reclaim_expired_leases`, swept on each `lease_job`). A poison job that exceeds an
  attempt cap fails, instead of being re-queued forever
  ([#507](https://github.com/bajutsu-e2e/bajutsu/pull/507)).

The mapping below is the menu for the remaining managed services. The **Shipped today** column marks
what `deploy/self-host/` already uses, as opposed to what is still a suggested choice for scale-out.

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

**Sizing.** Video evidence is heavy, so budget a few hundred GB for MinIO. The Linux node is modest
(2 vCPU / 4 GB), and it can even co-locate on the Mac via OrbStack. The **Macs dominate** the
footprint.

#### Growing one node into a pool — tracked separately

Everything above runs on **one** Linux node with **one or a few** Mac workers. Turning that node
into a fault-tolerant pool that stays fair across orgs is further work.

Each piece of that work is independent and substantial. Most of it is a deployment or operations
concern that the Linux-only `make check` gate cannot exercise, because the gate has no Docker and
no Mac. The work has therefore been **split out of this umbrella into five focused roadmap items**.
The per-org cap and worker-liveness re-queue that the work builds on have already shipped (see
*What runs today*).

Each successor names its shipped base, its concrete design, and how it is verified. Three of the
five expose a machine-checkable surface: fully for weighted-fair dispatch, and partially for
capability routing and observability. Control-plane scale-out and high availability are verified by
hand. By topic:

1. **Weighted-fair cross-org job dispatch.** This item turns the per-org cap's 429 rejection into
   *holding*, with per-org pending queues and a round-robin dispatcher that keeps the scarce pool
   fair under contention. It is the one piece that is *fully* gate-checkable: unit tests on
   `ServeState` need no Mac. The change lands in `bajutsu/serve/operations.py` and `jobs.py`.
2. **Capability-routed job queues.** Per-capability queues (`q:ios18`, `q:ipad`) ensure that only a
   worker able to run a job ever leases the job. The lease filter is gate-checkable, and the
   multi-device routing is verified by hand. The deterministic `run` does not change.
3. **Control-plane scale-out behind a load balancer.** N stateless app replicas sit behind a
   least-connections load balancer, with no sticky sessions because sessions live in Postgres.
   Verified by hand.
4. **Self-hosted high availability.** This item removes the lone stateful single point of failure
   (SPOF) with a Postgres primary and replica, a redundant load balancer, and a backed-up object
   store. Verified by hand.
5. **Serve metrics and observability.** A `/metrics` endpoint reports queue depth, in-flight jobs
   per org, run durations, and worker liveness. Optional Prometheus and Grafana containers
   accompany the endpoint. The endpoint is gate-checkable, and the containers are verified by hand.

Each of the five is its own proposal and names this item as its origin. This item stays the
umbrella that holds the shipped self-hosting baselines together. The reciprocal links from this
item's metadata are added once CI allocates the successors' BE IDs.

#### Multi-tenancy — the four axes, reconciled

The multi-tenant design rests on four axes. Three have shipped. The fourth, fairness, is the
*Weighted-fair cross-org job dispatch* successor item above.

- **Data isolation (shipped).** Each org's artifacts, scenarios, and baselines live under their own
  object-store prefix. Every query is org-scoped, so a cross-org read returns not-found or 403.
  Postgres row-level security (RLS) as defense-in-depth is a future hardening on top of the
  app-layer scoping that already enforces the boundary.
- **Execution isolation (shipped, with a known gap).** Each run gets an ephemeral Simulator
  (`--erase`), so nothing bleeds between runs on a shared Mac. Three gaps remain. Secrets are a
  **single server-level** API key today, not **per-org** injected secrets. **Per-job egress
  controls** are an operator choice, not yet automated. **Dedicating whole Macs** to
  high-isolation tenants is also an operator choice, not yet automated.
- **Fairness and noisy neighbors (partly shipped).** The per-user cap and the **per-org** cap both
  exist, and both reject over-cap requests. Weighted-fair scheduling across the scarce pool, which
  holds requests instead of rejecting them, is the *Weighted-fair cross-org job dispatch*
  successor item.
- **Authorization boundary (shipped).** Every request carries its org (the OAuth claim). The server
  enforces org scope on each endpoint, RBAC applies within the org, and a worker is handed only its
  job's org context.

In short, three statements summarize the design:

- The **front load balancer balances the cheap control plane** alone.
- The **real distribution is the pull queue plus per-worker slots**.
- **Multi-tenancy = org-scoped data + ephemeral Simulator (execution) + per-tenant quotas and fair
  scheduling (resources)**.

The data, execution, and authorization axes already cross the org boundary. The Mac pool cannot
scale elastically, so **fairness and availability are what the remaining work is about.**

### Recommendation

Start with **Tier A** (Tailscale + LaunchAgent). Tier A runs the real, existing system safely for a
team on a single Mac, with essentially no new code.

Move to **Tier B** once you need multi-user history and isolation. The single-node control plane,
including **multi-org isolation**, is runnable today ([`deploy/self-host/`](../../deploy/self-host/)).

Take on the **split-out pool work** only when the pool and the contention are real. The pool work
covers cross-org fairness, capability routing, control-plane scale-out, high availability, and
observability, and each part is a separate roadmap item that names this one as its origin. The
**fully managed public cloud** offering remains with
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), not this item.

## Alternatives considered

- **`LaunchDaemon` instead of `LaunchAgent`.** Rejected. A daemon has no GUI/Aqua session, and the
  Simulator will not run without one. The per-user `LaunchAgent` (plus auto-login and caffeinate)
  is mandatory, not a preference.
- **Binding `0.0.0.0` to the public internet.** Rejected. Even with BE-0051's token auth, a public
  bind widens the attack surface needlessly. Use a private tailnet (Tailscale). Only for an internal
  hostname, use Caddy + basic auth. `serve` already refuses a non-loopback `--host` without a
  token.
- **Embedding the plist as a template file (or a shell snippet in docs) instead of
  `serve --emit-launchagent`.** Rejected. Generating the plist from the same flags the operator
  already passes keeps the argv, interpreter path, and token placement correct and in one place,
  with no copy-paste drift.
- **Schema-per-tenant or DB-per-tenant instead of a shared schema + `org_id` + RLS.** Rejected for
  self-host ops. Per-tenant schemas or databases multiply the migration and connection-management
  overhead. A shared schema with `org_id` and Postgres RLS gives isolation with far less
  operational burden.
- **Per-user quota alone instead of adding a per-org quota.** The per-user cap already shipped, and
  in a single-tenant deploy the per-user cap is enough. The per-user cap does not bound an *org*
  whose many users each stay under the per-user cap. A multi-org pool therefore still needs a
  per-org cap layered on the same mechanism. The per-org cap has shipped, and the holding half is
  the *Weighted-fair cross-org job dispatch* item. Keeping both caps, and not replacing one with
  the other, is deliberate.
- **Pure FIFO scheduling instead of weighted-fair scheduling.** Rejected for the cross-org case.
  Pure first-in, first-out (FIFO) scheduling lets one org monopolize the scarce Mac pool. Per-org
  pending queues with a quota-respecting round-robin dispatcher keep the pool fair under
  contention.

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

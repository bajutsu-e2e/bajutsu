**English** · [日本語](BE-0016-web-ui-self-hosting-ja.md)

# BE-0016 — Self-hosting of the web UI

* Proposal: [BE-0016](BE-0016-web-ui-self-hosting.md)
* Author: [@0x0c](https://github.com/0x0c)
* Status: **Proposal**
* Track: [Proposals](../../README.md#proposals)
* Topic: Hosting the web UI (cloud / self-hosted)

## Introduction

This proposal describes how to **stand up and operate** the web UI on **your own Mac(s)**, as the
self-hosted counterpart to the managed, multi-tenant public stack in
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md). It documents two tiers:

- **Tier A — today-ready.** What runs *today* with the existing stdlib `bajutsu serve`
  (`bajutsu/serve.py`), already shipped as [BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md),
  plus operational configuration (LaunchAgent, auto-login, Tailscale). It needs essentially no new
  code.
- **Tier B — future.** A fully self-hosted version of the future multi-tenant system from
  [BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), with every managed
  service replaced by self-hosted OSS (open-source software). It depends on the (unimplemented)
  control plane proposed in BE-0015.

The item as a whole is a proposal; Tier A is the today-ready baseline within it.

## Motivation

Some teams cannot or will not put their test infrastructure on a managed cloud — for cost, data
residency, or policy reasons. They want to run the web UI on hardware they own. Self-hosting bajutsu
is **not** like self-hosting a normal web service, because the runner drives an **iOS Simulator**,
which constrains where and how the process can run. This proposal captures both the immediately
usable path (Tier A) and the full self-hosted multi-tenant target (Tier B) so that the operational
design is not lost and a team can adopt it incrementally.

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
[BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md). This tier makes it safely
reachable for a team with **near-zero code change** — it is **runnable today** on the existing
`bajutsu serve`.

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

**1) Run `serve` as a LaunchAgent** — keep it bound to `127.0.0.1` (never exposed raw). A
`~/Library/LaunchAgents/com.bajutsu.serve.plist` runs
`python -m bajutsu serve --host 127.0.0.1 --port 8765 --config <config.yml>` with
`RunAtLoad` + `KeepAlive`, `ANTHROPIC_API_KEY` in `EnvironmentVariables` (for `--dismiss-alerts`),
and stdout/stderr to `~/Library/Logs/`. Load it with
`launchctl bootstrap gui/$(id -u) …`. It must be a **LaunchAgent** (GUI session), not a
LaunchDaemon.

**2) Keep the session alive.** Enable auto-login in System Settings, and disable sleep
(`sudo pmset -a sleep 0 disablesleep 1`).

**3) Expose it — Tailscale (recommended).** `serve.py` has **no auth** and `/api/run` will run a
**client-supplied scenario path** (`bajutsu/serve.py` `run_command` / `do_POST`), so
**binding `0.0.0.0` to the public internet is not acceptable.** Put it on a private tailnet
instead — identity-based access plus automatic TLS, no public surface:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net (reachable only inside the tailnet)
```

**Only** if you need a real internal hostname, front it with **Caddy** for TLS + basic auth
(`reverse_proxy 127.0.0.1:8765` behind `basic_auth`) — but keep it off the open internet given the
unauthenticated, arbitrary-path surface.

This tier is usable by a team **today**.

### Tier B — fully self-hosted multi-tenant (depends on BE-0015's control plane)

Take the architecture from
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) and replace every
managed service with self-hosted OSS. Topology: **one Linux node (Docker Compose) + a pool of Mac
workers**, all wired together over a **Tailscale tailnet**; the only public surface is Caddy's
`:443`.

| Cloud-hosting selection | Self-hosted replacement |
|---|---|
| Fly.io / Render (control plane) | Your own **Linux node** + **Docker Compose** |
| Fly Postgres | **postgres** container |
| Upstash Redis | **redis** container |
| Cloudflare R2 (artifacts) | **MinIO** (S3-compatible, self-hosted) |
| GitHub OAuth (Authlib) | **Authelia** or **Keycloak** (self-hosted IdP — identity provider), or oauth2-proxy |
| Caddy / TLS | **Caddy** (Let's Encrypt or an internal CA) |
| MacStadium Orka (Mac pool) | Your own **Mac mini pool (1…N)** + worker agent as a `LaunchAgent` |
| Doppler (secrets) | **SOPS + age** or Vault (or a permission-locked `.env`) |
| Sentry / Grafana (observability) | **GlitchTip** + self-hosted **Prometheus/Grafana** |

```
            team laptops
               │  HTTPS :443
               ▼
   ┌──────────────────────────────────────────────┐        ┌─────────────────────────┐
   │  Linux node — docker compose                  │ Redis  │  Mac worker × N         │
   │  caddy · authelia · app · postgres · redis    │ ─────▶ │  worker agent           │
   │  minio · prometheus · grafana                 │ ◀───── │  bajutsu run --erase    │
   └──────────────────────────────────────────────┘  jobs  │  Simulator (GUI session)│
                  └──────────────── Tailscale tailnet ──────┴─────────────────────────┘
```

A `docker-compose.yml` for the Linux node would wire `caddy` (TLS + reverse proxy), `authelia`
(auth), `app` (the control plane — **unimplemented**, see
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)), `postgres`, `redis`,
`minio`, and `prometheus`/`grafana`, with named volumes for the stateful ones. Each Mac worker runs
the **same `LaunchAgent` pattern as Tier A** (GUI session, auto-login, caffeinate), but runs a
**worker agent** that leases jobs from the Linux node's Redis over the tailnet, runs
`bajutsu run --erase` on a fresh Simulator, streams logs back, and uploads the `runs/<id>/` tree to
MinIO.

**Sizing.** Video evidence is heavy — budget a few hundred GB for MinIO. The Linux node is modest
(2 vCPU / 4 GB; it can even co-locate on the Mac via OrbStack). The **Macs dominate** the footprint.

#### Load balancing — two separate problems

The control plane (HTTP, cheap, scales out) and the **Mac pool** (scarce, low-concurrency, slow)
need **opposite** techniques. Don't treat this as one "load balancer" problem.

**Control-plane load balancing (easy, standard).** Run **N replicas** of the FastAPI app behind
**Caddy/HAProxy**:

- Prefer **least-conn** over round-robin (SSE — server-sent events — connections are long-lived);
  health-check upstreams.
- **No sticky sessions** — use signed-cookie/JWT (JSON Web Token) auth or keep sessions in Redis,
  so any replica serves any request.
- **SSE is naturally shardable**: live logs come from Redis pub/sub, so *any* replica can stream
  *any* run's logs. Configure the LB (load balancer) to **not buffer** SSE (flush / no-buffering).
  Async uvicorn handles many SSE on few workers, but size worker concurrency to the expected live
  viewers.

**Worker "load balancing" = job scheduling (the real problem).** A Mac runs only **K** concurrent
Simulators (small — RAM-bound, often 1–3), and a run takes minutes. The rule is **pull, not push**:

- **Pull-based queue.** Workers lease a job from Redis *only when a Simulator slot is free*. That
  alone gives automatic balancing **and** back-pressure — no central scheduler needs to track each
  Mac's load.
- **Slots = concurrency.** Pin each worker's concurrency to its physical Simulator slots (RQ/Celery
  worker concurrency). Total throughput = sum of slots across the pool.
- **Route by capability.** Separate queues per device / iOS runtime (`q:ios18`, `q:ipad`); a worker
  subscribes only to queues it can serve.
- **Lease + heartbeat → re-queue.** If a Mac dies mid-run, the job must re-enqueue (Celery
  ack-late / RQ liveness). The Mac pool is scarce — don't drop jobs.

#### Multi-tenancy

Four axes:

- **Data isolation.** Shared Postgres with an `org_id` on every table, **org-scoped on every query**
  in the app layer, with **Postgres RLS** (row-level security) policies as defense-in-depth. MinIO
  uses a **tenant prefix** (`artifacts/<org_id>/runs/…`) served only through **org-scoped signed
  URLs**. Shared schema + `org_id` + RLS beats schema/DB-per-tenant for self-host ops.
- **Execution isolation** (a scenario is effectively untrusted code driving a device). Use an
  **ephemeral Simulator per run** (`--erase` / create+delete) so no state, keychain, screenshots,
  or network bleed between tenants on the same Mac. **Inject per-org secrets** (the org's
  `ANTHROPIC_API_KEY`, app credentials) into the job process env only, never persisted, scrubbed on
  exit. For high-isolation tenants, **dedicate whole Macs** (or macOS VMs) and don't co-run two
  tenants' Simulators on one Mac — utilization vs isolation. Add **per-job egress controls**.
- **Fairness / noisy-neighbor.** Enforce **per-tenant concurrency quotas** at enqueue time (count an
  org's in-flight jobs; hold the rest in a per-org pending queue) so one tenant can't monopolize the
  scarce Macs. Replace pure FIFO (first-in, first-out) with **weighted-fair scheduling**: per-tenant
  queues + a dispatcher that round-robins across orgs with pending work (respecting quotas) into the
  worker-facing queue. Priority tiers ride the same mechanism.
- **Authorization boundary.** Every request carries its org (the OAuth/Authelia claim); enforce org
  scope on every endpoint, RBAC (role-based access control) within the org, and hand workers **only
  that job's org context and scoped secrets**.

#### Self-host SPOFs (single points of failure) once you scale out

Horizontal scale makes **Redis and Postgres single points of failure** (Redis now holds the queue,
pub/sub, *and* quota state). Make **Redis HA (highly available) with Sentinel**, **Postgres**
primary+replica (Patroni) or at least a solid backup (a single node is acceptable for self-host
**if** flagged as a SPOF), and the **LB itself** redundant via keepalived/VRRP (Virtual Router
Redundancy Protocol) or DNS.

```
[DNS] → [HAProxy ×2 (VRRP)] → [FastAPI ×N] ─┬─ Postgres (primary+replica)
                                            ├─ Redis (Sentinel)   ← queue / pub-sub / quotas
                                            └─ MinIO (tenant prefix)
                                                   │ pull queue (per-device + per-org fairness)
                              Mac mini pool ×M (K slots each · erase per run · dedicated/isolated)
```

In short: the **front LB balances only the cheap control plane**; the **real distribution is the
pull queue + slots**; and **multi-tenancy = `org_id`/RLS (data) + ephemeral Simulator (execution) +
per-tenant quotas and fair scheduling (resources)**. Because the Mac pool can't scale elastically,
**fairness and isolation are the center of the design.**

### Recommendation

Start with **Tier A** (Tailscale + LaunchAgent) — it runs the real, existing system safely for a
team with essentially no code. Graduate to **Tier B** only once you actually need multi-user
isolation and history, i.e. after the control plane from
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) exists.

## Alternatives considered

- **`LaunchDaemon` instead of `LaunchAgent`** — rejected: a daemon has no GUI/Aqua session, and the
  Simulator will not run without one. The per-user `LaunchAgent` (plus auto-login and caffeinate) is
  mandatory, not a preference.
- **Binding `0.0.0.0` to the public internet** — rejected: `serve.py` is unauthenticated and runs a
  client-supplied scenario path, so a public bind is an unauthenticated arbitrary-path surface. Use a
  private tailnet (Tailscale) and, only for an internal hostname, Caddy + basic auth.
- **Schema-per-tenant / DB-per-tenant vs shared schema + `org_id` + RLS** — rejected for self-host
  ops: per-tenant schemas/DBs multiply migration and connection management overhead; a shared schema
  with `org_id` and Postgres RLS gives isolation with far less operational burden.
- **Pure FIFO scheduling vs weighted-fair scheduling** — rejected: pure FIFO lets one tenant
  monopolize the scarce Mac pool. Per-tenant queues with a quota-respecting round-robin dispatcher
  keep the pool fair under contention.

## References

`bajutsu/serve.py`, [cli.md](../../../docs/cli.md#serve), [ci.md](../../../docs/ci.md),
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the cloud-hosting
counterpart), [BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)

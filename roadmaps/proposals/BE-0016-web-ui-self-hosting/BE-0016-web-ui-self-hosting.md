**English** · [日本語](BE-0016-web-ui-self-hosting-ja.md)

# BE-0016 — Self-hosting of the web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0016](BE-0016-web-ui-self-hosting.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | Hosting the web UI (cloud / self-hosted) |
<!-- /BE-METADATA -->

## Introduction

This proposal describes how to **stand up and operate** the web UI on **your own Mac(s)**, as the
self-hosted counterpart to the managed, multi-tenant public stack in
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md). It documents two tiers:

- **Tier A — today-ready.** What runs *today* with the existing stdlib `bajutsu serve`
  (`bajutsu/serve/`), already shipped as [BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)
  and made safe to expose by the hardening in
  [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  (token auth + input validation), plus operational configuration (LaunchAgent, auto-login,
  Tailscale). The one small piece of code it adds is `serve --emit-launchagent`, which prints the
  LaunchAgent plist for you. The step-by-step operator guide is
  [docs/self-hosting.md](../../../docs/self-hosting.md).
- **Tier B — the server backend with a Mac worker pool.** A self-hosted version of
  [BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)'s server backend,
  with every managed service replaced by self-hosted open-source software (OSS). Its single-node form —
  **including multi-org isolation** — has shipped and is runnable today
  ([`deploy/self-host/`](../../../deploy/self-host/)); what remains is growing that node into a
  fault-tolerant, org-fair *pool*. The fully managed public cloud stays BE-0015's.

The item as a whole is a proposal; Tier A and the single-node Tier B are the runnable baselines
within it, and the remaining pool work is the design that follows.

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
[BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md) and hardened for
exposure by [BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md).
This tier makes it safely reachable for a team with **near-zero code change** (only the
`--emit-launchagent` helper) — it is **runnable today** on the existing `bajutsu serve`. See
[docs/self-hosting.md](../../../docs/self-hosting.md) for the full walkthrough.

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
which is [BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)'s,
not this item's.

#### What runs today

The single-node server backend is runnable now — the compose stack, walkthrough, and multi-org setup
are in [`deploy/self-host/`](../../../deploy/self-host/) and
[docs/self-hosting.md](../../../docs/self-hosting.md#tier-b--self-hosting-the-server-backend). It ships:

- **The control-plane stack.** A `docker-compose.yml` wires `postgres`, `redis`, `minio`
  (S3-compatible storage), a one-shot `migrate` (Alembic to head), the `bajutsu` app
  (`serve --asgi --backend=server`), and an optional `caddy` profile for a public hostname — each
  stateful service on a named volume. The Mac worker is **not** containerized: it needs the Aqua
  graphical user interface (GUI) session, exactly like Tier A, and runs `bajutsu worker`.
- **Identity and roles.** GitHub OAuth (open authorization) in the app itself (not a separate
  identity provider), with an allowlist and three roles — admin / editor / viewer (role-based access
  control, RBAC).
- **Multi-org isolation.** Declaring an `orgs:` block in the mounted config turns the same backend
  multi-tenant: each user is scoped to their org by GitHub login or GitHub-org membership (the login
  requests the `read:org` scope), sees only that org's targets, and a cross-org run / scenario /
  artifact read returns not-found or 403. Each org's artifacts, scenarios, and baselines live under
  their own object-store prefix (`org_prefix`). With no `orgs:` block the backend stays single-tenant
  (one default org).
- **Job distribution.** The control plane **enqueues** a job onto Redis (RQ — Redis Queue) via the
  server-side `QueueExecutor`; a `bajutsu worker` leases and runs it, streams logs back over the
  Redis log bus, and uploads the `runs/<id>/` tree to MinIO. Each run gets an **ephemeral Simulator**
  (`--erase`) so no state, keychain, or screenshots bleed between runs on a shared Mac.
- **Quotas.** A **global** concurrency cap (`--max-concurrent-runs`, default 4) and a **per-user** cap
  (`max_concurrent_per_user`) keep one caller from monopolizing the scarce devices — enforced
  atomically under a lock in `try_register` (`bajutsu/serve/jobs.py`).

The mapping below is the menu for the remaining managed services; the **Shipped today** column marks
what `deploy/self-host/` already uses, versus what is still a suggested choice for scale-out.

| Cloud-hosting selection | Self-hosted replacement | Shipped today |
|---|---|---|
| Fly.io / Render (control plane) | Your own **Linux node** + **Docker Compose** | ✅ |
| Fly Postgres | **postgres** container | ✅ |
| Upstash Redis | **redis** container | ✅ |
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
   ┌───────────────────────────────────────┐  jobs  ┌──────────────────────────┐
   │  Linux node — docker compose          │ Redis  │  Mac worker × N          │
   │  bajutsu serve --asgi --backend=server│ ─────▶ │  bajutsu worker          │
   │  postgres · redis · minio (· caddy)   │ ◀───── │  bajutsu run --erase     │
   └───────────────────────────────────────┘ result │  Simulator (GUI session) │
                       └──────────── Tailscale tailnet ──────┴─────────────────┘
```

**Sizing.** Video evidence is heavy — budget a few hundred GB for MinIO. The Linux node is modest
(2 vCPU / 4 GB; it can even co-locate on the Mac via OrbStack). The **Macs dominate** the footprint.

#### What remains — growing one node into a pool

Everything above runs on **one** Linux node with **one or a few** Mac workers. Turning that into a
fault-tolerant pool that stays fair across orgs is the remaining work. Each item below names its
**shipped base**, the **concrete design** to finish it, and how it is **verified** — most of these
are deployment and operations concerns that the Linux-only `make check` gate cannot exercise (no
Docker, no Mac), so the table is explicit about which piece has a machine-checkable contract and which
is verified by hand on a deployment.

**1. Cross-org fairness and quota (the one piece with a gate-checkable contract).** Before this, the
caps were global and per-user, with no per-org bound, so one org could crowd the scarce Mac pool even
when its users each stayed under their per-user cap. The design extends the existing seam rather than
adding a new one, and lands in two slices:

- **Per-org cap — shipped.** `max_concurrent_per_org` is counted on `job.org` exactly as
  `max_concurrent_per_user` is counted on `job.actor`, in the same atomic count-and-insert under the
  lock in `try_register` (`bajutsu/serve/jobs.py`); a server backend sets it from
  `BAJUTSU_MAX_CONCURRENT_PER_ORG` (`0` = unlimited, so single-tenant is unchanged). A job over its
  org's cap is **rejected** (HTTP 429) today, mirroring the per-user cap. The unit tests pin the
  invariants on `ServeState` (no Mac): an org never exceeds its cap, a different org is unaffected,
  the default is unlimited, and the per-user and per-org caps compose. Landed in
  [#367](https://github.com/bajutsu-e2e/bajutsu/pull/367).
- **Weighted-fair dispatch — remaining.** Turn that rejection into **holding**: replace the
  "reject with 429 when a cap is hit" tail (`_register_and_dispatch`, `bajutsu/serve/operations.py`)
  with **per-org pending queues** and a dispatcher that round-robins across orgs with pending work,
  admitting the next job only while that org is under its cap. Priority tiers ride the same
  round-robin as weights. Pure first-in, first-out (FIFO) is what lets one org monopolize the pool;
  per-org queues fix it. **Conflict note:** this lands in `bajutsu/serve/operations.py`, the surface
  open PR #166 (BE-0056) is editing — so it should land **after** #166 merges, or be coordinated.

**2. Capability-routed queues.** Today there is a single job queue. A mixed pool (different iOS
runtimes, iPad vs iPhone) needs **per-capability queues** (`q:ios18`, `q:ipad`); the control plane
enqueues onto the queue matching the target's device, and each worker subscribes only to the queues
it can serve. Verified by hand on a multi-device pool. (No change to the deterministic `run`; routing
is purely about *which* idle worker picks the job up.)

**3. Worker liveness and job re-queue.** A Mac that dies mid-run must not silently drop the job. Use
RQ's liveness / ack-late so a lease whose worker stops heart-beating **re-enqueues** rather than
vanishing — the Mac pool is scarce, so a lost job is worse than a retried one. Verified by killing a
worker mid-run on a test deployment and confirming the job re-runs.

**4. Control-plane scale-out.** The control plane is cheap HTTP and scales out, but the compose runs
**one** app container today. To run **N replicas** behind a load balancer (Caddy or HAProxy):
**least-conn** over round-robin (server-sent events (SSE) connections are long-lived); **no sticky
sessions** (auth is a signed cookie and sessions live in Redis, so any replica serves any request);
and the load balancer must **not buffer** SSE, since live logs come from Redis pub/sub and any replica
can stream any run. Verified by hand: bring up two app replicas, confirm a login on one and a live log
stream on the other.

**5. High availability — the single points of failure (SPOF).** A single node makes **Redis and
Postgres** SPOFs (Redis holds the queue, the pub/sub, *and* — once item 1 lands — the quota state).
The hardened topology: **Redis with Sentinel**, **Postgres primary + replica** (Patroni) or at minimum
a tested backup, and a redundant load balancer (keepalived / Virtual Router Redundancy Protocol, VRRP,
or DNS). A single node is acceptable for a self-host **if** it is explicitly flagged as a SPOF.
Verified by hand on the deployment; no gate coverage.

```
[DNS] → [HAProxy ×2 (VRRP)] → [FastAPI ×N] ─┬─ Postgres (primary+replica)
                                            ├─ Redis (Sentinel)   ← queue / pub-sub / quotas
                                            └─ MinIO (tenant prefix)
                                                   │ pull queue (per-device + per-org fairness)
                              Mac mini pool ×M (K slots each · erase per run · dedicated/isolated)
```

**6. Observability.** The serve backend already emits **structured JSON logs to stdout** with secrets
redacted ([BE-0055](../../implemented/BE-0055-operational-logging/BE-0055-operational-logging.md)) —
shipping those to a log stack is the deployment's job. What is missing is **metrics**: a `/metrics`
endpoint (queue depth, in-flight jobs per org, run durations, worker liveness) and `prometheus` +
`grafana` containers in the compose to scrape and chart them. The `/metrics` endpoint is the only part
with a Python surface (and so a gate-checkable contract); the containers are verified by hand. The
`/metrics` route would touch `bajutsu/serve/`, so it carries the same #166 coordination note as item 1.

#### Multi-tenancy — the four axes, reconciled

The multi-tenant design rests on four axes; three have shipped, one (fairness) is item 1 above.

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
  than rejecting) is the remaining half of item 1.
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
([`deploy/self-host/`](../../../deploy/self-host/)). Take on the **remaining pool work**
(cross-org fairness, capability routing, high availability, observability) only when the pool and the
contention are real — and the **fully managed public cloud** offering remains
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)'s, not this
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
  the per-user cap, so a multi-org pool still needs a per-org cap layered on the same mechanism (the
  remaining work, item 1). Keeping both caps — not replacing one with the other — is deliberate.
- **Pure FIFO scheduling vs weighted-fair scheduling** — rejected for the cross-org case: pure
  first-in, first-out (FIFO) lets one org monopolize the scarce Mac pool. Per-org pending queues with
  a quota-respecting round-robin dispatcher keep the pool fair under contention.

## References

`bajutsu/serve/`, [docs/self-hosting.md](../../../docs/self-hosting.md) (the Tier A and Tier B
operator guide), [`deploy/self-host/`](../../../deploy/self-host/) (the runnable single-node compose
stack), [cli.md](../../../docs/cli.md#serve), [ci.md](../../../docs/ci.md),
[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
(the hardening that makes exposure safe),
[BE-0055](../../implemented/BE-0055-operational-logging/BE-0055-operational-logging.md) (the structured
serve logs the observability work builds on),
[BE-0015](../../proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (the cloud-hosting
counterpart), [BE-0011](../../implemented/BE-0011-local-web-ui-serve/BE-0011-local-web-ui-serve.md)

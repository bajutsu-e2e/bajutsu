**English** · [日本語](ja/self-hosting.md)

# Self-hosting the web UI (your own hardware)

> Forward-looking design — **not implemented**. Where [cloud-hosting](cloud-hosting.md) selects a
> managed, multi-tenant public stack, this page describes how to **stand up and operate** the web
> UI on **your own Mac(s)**. It documents two tiers: **(A)** what runs *today* with the existing
> stdlib `bajutsu serve` ([serve.py](../bajutsu/serve.py)), and **(B)** a fully self-hosted version
> of the future multi-tenant system from [cloud-hosting](cloud-hosting.md), with every managed
> service replaced by self-hosted OSS.

Related: [cloud-hosting](cloud-hosting.md) · [cli](cli.md#serve) · [ci](ci.md)

---

## The macOS gotcha that shapes self-hosting

The runner drives an **iOS Simulator**, and the Simulator needs a **GUI login session**
(WindowServer / the Aqua session) — it will **not** run from a headless daemon. Every
self-hosting choice below follows from this:

- Run the process as a **`LaunchAgent`** (per-user, GUI session), **not** a `LaunchDaemon`.
- The Mac must be **auto-logged-in** so it recovers a GUI session after reboot (note FileVault
  requires one interactive login after a cold boot before auto-login proceeds).
- **Disable sleep** so the session stays alive (`caffeinate` / `pmset`).

This is the operational difference between hosting bajutsu and hosting a normal web service.

---

## Tier A — run it today (single Mac, current `serve.py`)

The only thing that actually runs today is the stdlib `bajutsu serve` + the CLI. This tier makes
it safely reachable for a team with **near-zero code change**.

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
`python -m bajutsu serve --host 127.0.0.1 --port 8765 --scenarios <dir>` with
`RunAtLoad` + `KeepAlive`, `ANTHROPIC_API_KEY` in `EnvironmentVariables` (for `--dismiss-alerts`),
and stdout/stderr to `~/Library/Logs/`. Load it with
`launchctl bootstrap gui/$(id -u) …`. It must be a **LaunchAgent** (GUI session), not a
LaunchDaemon.

**2) Keep the session alive.** Enable auto-login in System Settings, and disable sleep
(`sudo pmset -a sleep 0 disablesleep 1`).

**3) Expose it — Tailscale (recommended).** `serve.py` has **no auth** and `/api/run` will run a
**client-supplied scenario path** ([serve.py](../bajutsu/serve.py) `run_command` / `do_POST`), so
**binding `0.0.0.0` to the public internet is not acceptable.** Put it on a private tailnet
instead — identity-based access plus automatic TLS, no public surface:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net (reachable only inside the tailnet)
```

**Only** if you need a real internal hostname, front it with **Caddy** for TLS + basic auth
(`reverse_proxy 127.0.0.1:8765` behind `basic_auth`) — but keep it off the open internet given the
unauthenticated, arbitrary-path surface.

This tier is usable by a team **today**.

---

## Tier B — fully self-hosted multi-tenant (when the system from cloud-hosting is built)

Take the architecture from [cloud-hosting](cloud-hosting.md) and replace every managed service with
self-hosted OSS. Topology: **one Linux node (Docker Compose) + a pool of Mac workers**, all wired
together over a **Tailscale tailnet**; the only public surface is Caddy's `:443`.

| Cloud-hosting selection | Self-hosted replacement |
|---|---|
| Fly.io / Render (control plane) | Your own **Linux node** + **Docker Compose** |
| Fly Postgres | **postgres** container |
| Upstash Redis | **redis** container |
| Cloudflare R2 (artifacts) | **MinIO** (S3-compatible, self-hosted) |
| GitHub OAuth (Authlib) | **Authelia** or **Keycloak** (self-hosted IdP), or oauth2-proxy |
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
(auth), `app` (the control plane — **unimplemented**, see [cloud-hosting](cloud-hosting.md)),
`postgres`, `redis`, `minio`, and `prometheus`/`grafana`, with named volumes for the stateful ones.
Each Mac worker runs the **same `LaunchAgent` pattern as Tier A** (GUI session, auto-login,
caffeinate), but runs a **worker agent** that leases jobs from the Linux node's Redis over the
tailnet, runs `bajutsu run --erase` on a fresh Simulator, streams logs back, and uploads the
`runs/<id>/` tree to MinIO.

**Sizing.** Video evidence is heavy — budget a few hundred GB for MinIO. The Linux node is modest
(2 vCPU / 4 GB; it can even co-locate on the Mac via OrbStack). The **Macs dominate** the footprint.

---

## Recommendation

Start with **Tier A** (Tailscale + LaunchAgent) — it runs the real, existing system safely for a
team with essentially no code. Graduate to **Tier B** only once you actually need multi-user
isolation and history, i.e. after the control plane from [cloud-hosting](cloud-hosting.md) exists.

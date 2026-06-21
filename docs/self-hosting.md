**English** · [日本語](ja/self-hosting.md)

# Self-hosting the web UI

> Run the bajutsu web UI ([cli](cli.md#serve)) on hardware you own, reachable by your team over a
> private Tailscale network, from the self-hosting roadmap
> ([BE-0016](../roadmaps/proposals/BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)).
> Two tiers are available today, both made safe to expose by
> [BE-0051](../roadmaps/proposals/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)'s
> auth + input validation:
>
> - **Tier A — a single Mac.** One `bajutsu serve` process, token-authenticated, on one Mac. The
>   rest of this page up to *Tier B* covers it.
> - **Tier B — a single-team server backend.** BE-0015's control plane (FastAPI + Postgres + Redis +
>   S3-compatible storage + GitHub OAuth + RBAC + quotas) on a Linux node, with Mac workers — for one
>   team (it is single-tenant). See *[Tier B — single-team self-hosting](#tier-b--single-team-self-hosting-the-server-backend)*.
>
> The fully multi-tenant public/self-hosted system (multiple orgs) remains future
> ([BE-0015](../roadmaps/proposals/BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).

## The macOS constraint

The runner drives an **iOS Simulator**, which needs a **GUI login session** (the Aqua session) —
it will not run from a headless daemon. Every choice below follows from that:

- Run serve as a per-user **`LaunchAgent`** (GUI session), **not** a `LaunchDaemon`.
- **Auto-login** the Mac so a GUI session is recovered after a reboot (FileVault needs one
  interactive login after a cold boot before auto-login proceeds).
- **Disable sleep** so the session stays alive: `sudo pmset -a sleep 0 disablesleep 1`.

## 1. Generate the LaunchAgent

`bajutsu serve --emit-launchagent` prints a launchd plist matching the serve flags you pass, then
exits without starting a server. Pick a strong token and write the plist into your LaunchAgents:

```bash
export TOKEN="$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')"
bajutsu serve --emit-launchagent --config bajutsu.config.yaml --token "$TOKEN" \
  > ~/Library/LaunchAgents/com.bajutsu.serve.plist
chmod 600 ~/Library/LaunchAgents/com.bajutsu.serve.plist   # the plist holds the token
```

The emitted plist:

- runs `python -m bajutsu serve --host 127.0.0.1 --port 8765 --config …` (the same interpreter you
  ran the command with, so it uses your venv) with **`RunAtLoad`** + **`KeepAlive`**;
- puts the token in **`EnvironmentVariables`** (`BAJUTSU_SERVE_TOKEN`) — never in the argv, so it
  isn't visible in `ps`;
- writes stdout/stderr to `~/Library/Logs/bajutsu-serve.{out,err}.log`.

If you use the AI paths (`record`, `--dismiss-alerts`), add `ANTHROPIC_API_KEY` to the plist's
`EnvironmentVariables` dict (it isn't baked in for you). It stays bound to `127.0.0.1`; the next
step is what makes it reachable.

## 2. Load it

```bash
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.bajutsu.serve.plist
launchctl print gui/$(id -u)/com.bajutsu.serve        # verify it's loaded
```

To reload after editing the plist: `launchctl bootout gui/$(id -u)/com.bajutsu.serve` then
bootstrap again.

## 3. Expose it over Tailscale (recommended)

serve stays on `127.0.0.1`; **Tailscale** publishes it inside your tailnet only — identity-based
access plus automatic TLS, no public surface:

```bash
tailscale serve --bg 8765    # → https://<machine>.<tailnet>.ts.net (reachable only in the tailnet)
```

Teammates open that URL; the UI prompts for the token on first load (the browser then carries a
session cookie). API clients send `Authorization: Bearer $TOKEN`.

> **Do not bind `0.0.0.0` to the public internet.** Even with a token, the safe default is a
> private tailnet. serve refuses a non-loopback `--host` without a token, but a public bind widens
> the surface needlessly. If you need a real internal hostname, front serve with **Caddy** for TLS
> (+ basic auth) and keep it off the open internet.

## Security recap (BE-0051)

A self-hosted serve relies on the hardening from
[BE-0051](../roadmaps/proposals/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md):
token auth on every request, `/api/run` and `/api/record` confined to the app's scenarios dir with
validated `backend`/`udid`, a CSRF Origin check plus security headers, and a concurrency cap on run
dispatch. Keep the token secret, keep the Mac on a tailnet, and keep the OS patched.

## Tier B — single-team self-hosting (the server backend)

Tier A is one process on one Mac. **Tier B** runs BE-0015's **server backend** — the FastAPI control
plane with Postgres, Redis, S3-compatible storage (MinIO), GitHub OAuth, RBAC, and a per-user quota
— on a Linux node, with one or more Macs as workers. It is **single-tenant**: every user belongs to
one default org, so run it for **one team** (multi-tenant isolation across orgs is not implemented
yet). The ready-to-run stack is in [`deploy/self-host/`](../deploy/self-host/) (compose + Dockerfile
+ `.env.example`).

```
        team laptops
           │  HTTPS (Tailscale tailnet, or Caddy at a hostname)
           ▼
   ┌───────────────────────────────────────┐  jobs  ┌──────────────────────────┐
   │  Linux node — docker compose          │ ─────▶ │  Mac worker × N          │
   │  bajutsu serve --asgi --backend=server│  Redis │  bajutsu worker          │
   │  postgres · redis · minio             │ ◀───── │  bajutsu run · Simulator │
   └───────────────────────────────────────┘ result └──────────────────────────┘
                       └──────────── Tailscale tailnet ──────┘
```

The Linux control plane is cheap; the **Mac workers** carry the Simulator runs and are the scarce
part. The worker is **not** containerized — it needs the Aqua GUI session, exactly like Tier A.

### 1. Bring up the control plane

```bash
cd deploy/self-host
cp .env.example .env            # set BAJUTSU_SERVE_TOKEN, POSTGRES_PASSWORD, AWS_* (MinIO), bucket
mkdir -p config && cp /path/to/bajutsu.config.yaml config/   # the app/project list to expose
docker compose up -d            # postgres + redis + minio + migrate (alembic upgrade head) + bajutsu
```

`migrate` runs the Alembic migrations to head before `bajutsu` starts, and `minio-init` creates the
bucket. The control plane then listens on `:8765`.

Published ports bind to `BIND_ADDR` (default `127.0.0.1`). For a Mac worker to reach Redis and
MinIO from another host, set `BIND_ADDR` in `.env` to the node's **tailnet IP** — never `0.0.0.0`
on a host with a public interface, since that would expose Redis and the artifacts bucket.

### 2. Add GitHub OAuth (optional)

The shared token (`BAJUTSU_SERVE_TOKEN`) alone is enough for a couple of operators. For per-user
browser login, create a GitHub OAuth app (callback `https://<your-host>/api/oauth/callback`) and set
in `.env`: `BAJUTSU_OAUTH_GITHUB_CLIENT_ID` / `_SECRET` / `_REDIRECT_URI`, plus the allowlist
`BAJUTSU_OAUTH_ALLOWED_USERS` (and optionally `BAJUTSU_OAUTH_ADMINS` / `BAJUTSU_OAUTH_VIEWERS`).
Allowlisted users are **editors** by default (they can run); admins also change server settings
(config / API key / provider); viewers are read-only. The token stays the operator/CI credential
(full access); OAuth is the team's per-user login.

Login requests the `read:org` scope so a user can be mapped to an org by GitHub org membership
(config `githubOrgs`); the consent screen will mention organization access. Single-tenant deploys
(no `orgs:` block) don't use it.

### 3. Run a Mac worker

On each Mac (the same Aqua-session setup as Tier A — auto-login, `caffeinate`/`pmset`), install
`bajutsu[worker,idb]` and point it at the Linux node over the tailnet:

```bash
export BAJUTSU_REDIS_URL=redis://<linux-node>.<tailnet>.ts.net:6379
export BAJUTSU_S3_BUCKET=bajutsu
export BAJUTSU_S3_ENDPOINT=http://<linux-node>.<tailnet>.ts.net:9000
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=…
export ANTHROPIC_API_KEY=…     # only if scenarios use the AI paths (record / --dismiss-alerts)
bajutsu worker
```

Wrap it in a `LaunchAgent` (as in Tier A) so it survives reboots. Each job runs on a fresh Simulator
and uploads its `runs/<id>/` tree to MinIO, which the control plane serves back.

> **Run history in the database.** A run executes on the worker, so the worker is what records it
> into Postgres for the run-history list. To enable that, install `bajutsu[worker,idb,db]` and give
> the worker `BAJUTSU_DATABASE_URL` (the Postgres node over the tailnet) — the same URL the control
> plane uses. Without it the run still works and its artifacts are served, but it won't appear in the
> durable history list.

### 4. Expose it

Front the control plane like Tier A: `tailscale serve --bg 8765` (tailnet-only, recommended), or
Caddy for a real hostname (`docker compose --profile caddy up -d`, with `BAJUTSU_PUBLIC_HOST` set).
The worker reaches Redis (`:6379`) and MinIO (`:9000`) over the tailnet, so keep the node on the
private tailnet.

### Still single-tenant

Multiple orgs, org-scoped run history, cross-org access checks, and per-org quotas are **not**
implemented (they wait on BE-0015's multi-tenant control plane). Tier B is for **one team**; the
shared token and the GitHub allowlist are the access boundary.


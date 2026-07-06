# Self-hosting the bajutsu server backend (single-tenant)

A Docker Compose stack that runs bajutsu's **single-tenant** server backend (BE-0015's control
plane) on one Linux node: the FastAPI app, Postgres, Redis, and MinIO (S3-compatible), with GitHub
OAuth, RBAC, and a per-user quota. The macOS **worker** runs natively on a Mac (it needs the
Simulator) and joins over a Tailscale tailnet — it is **not** part of this compose.

This is the bridge between BE-0016 Tier A (single Mac, no control plane) and the future fully
multi-tenant Tier B. **One team = one org**; multi-tenant isolation is not implemented yet.

The step-by-step guide — including the macOS worker, OAuth setup, and exposure — is
[docs/self-hosting.md](../../docs/self-hosting.md) ("Tier B — single-team self-hosting").

## Quick start

```sh
cd deploy/self-host           # the compose file and .env live here
cp .env.example .env          # then edit: tokens, passwords, OAuth, bucket
mkdir -p config               # put your bajutsu.config.yaml (the app list) here
docker compose up -d          # postgres, redis, minio, migrate (one-shot), bajutsu
```

The control plane is then on `:8765`. Front it with `tailscale serve --bg 8765` (tailnet-only,
recommended) or enable Caddy for a public hostname: `docker compose --profile caddy up -d` (set
`BAJUTSU_PUBLIC_HOST`).

On a Mac, run the worker (see the guide):

```sh
export BAJUTSU_REDIS_URL=redis://<linux-node-on-tailnet>:6379
export BAJUTSU_S3_BUCKET=bajutsu BAJUTSU_S3_ENDPOINT=http://<linux-node>:9000
export AWS_ACCESS_KEY_ID=… AWS_SECRET_ACCESS_KEY=… ANTHROPIC_API_KEY=…   # AI paths
# To record runs in the history list, install bajutsu[worker,idb,db] and also set:
export BAJUTSU_DATABASE_URL=postgresql+psycopg://bajutsu:<password>@<linux-node>:5432/bajutsu
bajutsu worker
```

For a **web (Playwright) backend** the worker is instead a Linux container (BE-0173) — the web
backend has no Simulator/GUI constraint. An optional `worker-web` service (off by default) builds
[`worker-web.Dockerfile`](worker-web.Dockerfile), a slim multi-stage image carrying only
`bajutsu[worker-web]` and Chromium's headless shell. Enable it once `BAJUTSU_SERVE_TOKEN` is set:

```sh
docker compose --profile web-worker up -d --build
```

Until BE-0166 routes jobs by backend, `/api/worker/lease` has no backend/capability filtering, so
keep the pool **homogeneous per backend** — run either idb workers or web workers against a control
plane, not both — or a web worker may lease an idb job (and vice versa) and fail.

> The compose stack and images are **not exercised by CI** (no Docker on the gate). Verify a
> deployment by hand: `docker compose up`, then check the migrate step succeeds, `/` returns 200,
> OAuth login works, and a worker consumes a job.

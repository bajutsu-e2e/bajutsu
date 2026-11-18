# Self-hosting the bajutsu server backend (single-tenant)

This directory holds a Docker Compose stack that runs bajutsu's **single-tenant** server backend
(BE-0015's control plane) on one Linux node. The stack contains the FastAPI app, Postgres, and MinIO
(S3-compatible), with GitHub OAuth, role-based access control (RBAC), and a per-user quota.

The macOS **worker** is not part of this compose. It runs natively on a Mac, because it needs the
Simulator, and joins over a Tailscale tailnet.

The stack is the bridge between BE-0016 Tier A (single Mac, no control plane) and the future fully
multi-tenant Tier B. **One team = one org**; multi-tenant isolation is not implemented yet.

The step-by-step guide covers the macOS worker, OAuth setup, and exposure. It is
[docs/self-hosting.md](../../docs/self-hosting.md) ("Tier B — single-team self-hosting").

## Quick start

```sh
cd deploy/self-host           # the compose file and .env live here
cp .env.example .env          # then edit: tokens, passwords, OAuth, bucket
mkdir -p config               # put your bajutsu.config.yaml (the app list) here
docker compose up -d          # postgres, minio, minio-init (one-shot), migrate (one-shot), bajutsu
```

The control plane is then on `:8765`. Front it in one of two ways:

- `tailscale serve --bg 8765` (tailnet-only, recommended)
- Caddy for a public hostname: `docker compose --profile caddy up -d` (set `BAJUTSU_PUBLIC_HOST`)

### Metrics (optional)

The control plane exposes Prometheus-format metrics at `/metrics`, behind the same auth as the rest of
serve. The metrics are queue depth and in-flight jobs per org, worker heartbeat freshness, and the
oldest in-flight run. Turn on the optional stack to chart them:

```sh
docker compose --profile metrics up -d   # adds prometheus + grafana
```

Grafana then lands on `:3000` (log in as `admin` / `GRAFANA_ADMIN_PASSWORD`), with the Prometheus
datasource and a starter **Bajutsu serve** dashboard already provisioned. Prometheus scrapes `/metrics`
using `BAJUTSU_SERVE_TOKEN`, so the endpoint is never an unauthenticated public surface.

### macOS worker

On a Mac, run the worker (see the guide):

```sh
export BAJUTSU_SERVER_URL=http://<linux-node-on-tailnet>:8765
export BAJUTSU_TOKEN=<BAJUTSU_SERVE_TOKEN>   # the same operator token the control plane uses
export ANTHROPIC_API_KEY=…                   # only if scenarios use the AI paths
bajutsu worker
```

### Linux web worker

For a **web (Playwright) backend**, the worker is instead a Linux container (BE-0173), because the web
backend has no Simulator/GUI constraint. An optional `worker-web` service (off by default) builds
[`worker-web.Dockerfile`](worker-web.Dockerfile). The image is a slim multi-stage build that carries
`bajutsu[worker-web]` and Chromium's headless shell, and nothing else. Enable the service once
`BAJUTSU_SERVE_TOKEN` is set:

```sh
docker compose --profile web-worker up -d --build
```

### Capability routing

`/api/worker/lease` filters by **worker capability** (BE-0166), so a mixed-backend fleet is safe:

- The web-worker container advertises `web` and leases web jobs alone.
- A Mac worker leases iOS jobs alone.
- The two never cross.

Every worker also advertises `host:<os>` for its machine. An iOS job requires `host:darwin`, so the job
reaches a Mac worker alone (BE-0450).

A worker capability file declares what one machine can run. The file lists the drivers and caps the
targets one scenario may hold. Pass it with `bajutsu worker --worker-config <worker.yaml>`.

BE-0452 removed a target's `requires:` list, so no job can pin an iOS runtime or a device class yet.
The worker's `--capabilities` logs a deprecation notice and goes away in the next release. See
[capability-routed queues](../../docs/self-hosting.md#capability-routed-queues-be-0166).

## Verification

> The compose stack and images are **not exercised by CI** (no Docker on the gate). Verify a
> deployment by hand: run `docker compose up`, then check that the migrate step succeeds, `/` returns
> 200, OAuth login works, and a worker consumes a job. With the `metrics` profile, also confirm that
> Prometheus's `bajutsu-serve` target is `UP` and the Grafana dashboard charts the series.

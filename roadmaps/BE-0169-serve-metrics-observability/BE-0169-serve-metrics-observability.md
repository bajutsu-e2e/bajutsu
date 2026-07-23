**English** · [日本語](BE-0169-serve-metrics-observability-ja.md)

# BE-0169 — Serve metrics and observability endpoint

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0169](BE-0169-serve-metrics-observability.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0169") |
| Implementing PR | [#719](https://github.com/bajutsu-e2e/bajutsu/pull/719) |
| Topic | Hosting the web UI |
| Related | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) |
| Origin | [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

The self-hosted serve backend
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) already emits **structured
JSON logs** to stdout with secrets redacted
([BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md)) — shipping those to a log
stack is the deployment's job. What is missing is **metrics**: there is no way to see queue depth,
in-flight jobs per org, run durations, or worker liveness at a glance. This proposal adds a
`/metrics` endpoint plus optional Prometheus and Grafana containers in the compose stack. It is
carved out of BE-0016's "growing one node into a pool" work.

## Motivation

Operating a Mac pool means answering questions the logs alone do not surface well: is the queue
backing up, which org is consuming the pool right now, are runs slower than usual, and is a worker
dead? Structured logs (BE-0055) capture events, but an operator watching a growing pool needs
**time-series signals** to spot saturation and liveness problems before they become outages, and to
size the pool. A scrapeable metrics endpoint plus a chart stack is the standard way to get that, and
it is the last unshipped piece of BE-0016's observability story.

## Detailed design

The endpoint is the only part with a Python surface (and so a gate-checkable contract); the
containers are a deployment concern verified by hand. The work breakdown:

1. **A `/metrics` endpoint.** Expose Prometheus-format metrics from the serve backend covering at
   least: **queue depth** (pending jobs, and per-org / per-capability where those exist),
   **in-flight jobs per org**, **run durations**, and **worker liveness** (leases and heartbeat
   freshness). The metric surface is derived from state the control plane already tracks (the `jobs`
   table and lease/heartbeat records), so it reads existing state rather than adding new bookkeeping.
2. **Compose wiring.** Add `prometheus` (scraping `/metrics`) and `grafana` (charting it) containers,
   plus a starter dashboard, to `deploy/self-host/`. These are optional, like the existing `caddy`
   profile, so a minimal deploy is unchanged.
3. **Auth on the endpoint.** `/metrics` must respect the same exposure rules as the rest of serve
   (BE-0051) — it is not an unauthenticated public surface — and must never leak secrets, consistent
   with BE-0055's redaction.

**Verification.** The `/metrics` endpoint has a Python surface and is unit-tested with no Simulator:
given a `ServeState` with known queued and in-flight jobs, the rendered metrics report the expected
queue depth and per-org in-flight counts, and no secret appears in the output. The Prometheus and
Grafana containers are verified by hand on a deployment (scrape succeeds, the dashboard charts the
series).

**Coordination note.** The `/metrics` route touches `bajutsu/serve/`, so it carries the same
coordination note as other in-flight serve work: land it after any open PR editing that surface
merges, or coordinate to avoid a conflict.

## Alternatives considered

- **Rely on the structured logs alone** — rejected: logs are events, not time series; deriving queue
  depth or worker liveness from log scraping is fragile and lagging. A first-class metrics endpoint is
  the standard, low-friction source for these signals.
- **Push metrics to an external hosted APM instead of a scrapeable endpoint** — rejected as the
  default for self-host: it reintroduces an external dependency the self-hosted stack exists to avoid.
  A Prometheus-format `/metrics` endpoint works with self-hosted Prometheus/Grafana and still lets an
  operator point a hosted scraper at it if they choose.
- **Bundle Prometheus/Grafana as always-on rather than an optional profile** — rejected: not every
  self-host wants the extra containers; making them optional (like `caddy`) keeps a minimal deploy
  lean while offering the full stack when wanted.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `/metrics` endpoint exposing queue depth, per-org in-flight, run durations, worker liveness.
- [x] `/metrics` respects serve exposure rules and never leaks secrets (unit-tested).
- [x] Optional `prometheus` + `grafana` containers and a starter dashboard in `deploy/self-host/`.

- [#719](https://github.com/bajutsu-e2e/bajutsu/pull/719) — Shipped the `/metrics` endpoint on both serve backends (the stdlib handler and the
  FastAPI control plane), rendering Prometheus-format metrics from state the control plane already
  tracks: in-flight jobs per org (`state.jobs`), and — with a database wired — queue depth, leased
  jobs, worker heartbeat freshness, and the oldest in-flight run (a new one-pass
  `Repository.metrics_snapshot`). The endpoint sits behind serve's auth gate (BE-0051) and emits
  only counts, ages, and org / worker ids — never a job spec or the token — so a scrape cannot leak
  a secret. Added the optional `metrics` compose profile (`prometheus` + `grafana`, a provisioned
  datasource, and a starter dashboard) to `deploy/self-host/`, and documented it in the self-host
  README and `docs/self-hosting.md` (+ `docs/ja/`).

## References

`bajutsu/serve/` (where `/metrics` lands), `deploy/self-host/` (the compose stack the containers join),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (the self-hosting umbrella
this is carved from),
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) (the structured serve logs
this metrics work complements),
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) (the exposure
rules `/metrics` must follow).

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
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) already emits
**structured JSON logs** to stdout with secrets redacted
([BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md)). Shipping those logs to a
log stack is the deployment's job.

What is missing is **metrics**. An operator has no way to see queue depth, in-flight jobs per org,
run durations, or worker liveness at a glance. This proposal adds a `/metrics` endpoint, plus
optional Prometheus and Grafana containers in the compose stack. The work is carved out of BE-0016's
"growing one node into a pool" effort.

## Motivation

Operating a Mac pool means answering questions that the logs alone do not surface well. Four
examples: whether the queue is backing up, which org is consuming the pool right now, whether runs
are slower than usual, and whether a worker is dead.

Structured logs (BE-0055) capture events. An operator watching a growing pool also needs
**time-series signals**, to spot saturation and liveness problems before they become outages and to
size the pool. A scrapeable metrics endpoint plus a chart stack is the standard way to get those
signals. It is also the last unshipped piece of BE-0016's observability story.

## Detailed design

The endpoint is the sole part with a Python surface, so it has a contract the gate can check. The
containers are a deployment concern, verified by hand. The work breakdown:

1. **A `/metrics` endpoint.** Expose Prometheus-format metrics from the serve backend. They cover at
   least **queue depth** (pending jobs, plus per-org or per-capability counts where those exist) and
   **in-flight jobs per org**. They also cover **run durations** and **worker liveness** (leases and
   heartbeat freshness). The metrics derive from state the control plane already tracks (the `jobs`
   table and the lease and heartbeat records). The endpoint reads existing state rather than adding
   new bookkeeping.
2. **Compose wiring.** Add a `prometheus` container (scraping `/metrics`) and a `grafana` container
   (charting the metrics) to `deploy/self-host/`, plus a starter dashboard. These containers are
   optional, like the existing `caddy` profile, so a minimal deploy is unchanged.
3. **Auth on the endpoint.** `/metrics` must follow the same exposure rules as the rest of serve
   (BE-0051), because it is not an unauthenticated public surface. It must also never leak secrets,
   consistent with BE-0055's redaction.

**Verification.** The `/metrics` endpoint has a Python surface, and a unit test covers it without a
Simulator. Given a `ServeState` with known queued and in-flight jobs, the rendered metrics report
the expected queue depth and per-org in-flight counts. No secret appears in the output. The
Prometheus and Grafana containers are verified by hand on a deployment: the scrape succeeds, and the
dashboard charts the series.

**Coordination note.** The `/metrics` route touches `bajutsu/serve/`, so it carries the same
coordination note as other in-flight serve work. Land the route after any open PR that edits that
surface merges, or coordinate to avoid a conflict.

## Alternatives considered

- **Rely on the structured logs alone.** Rejected. Logs are events, not time series. Deriving queue
  depth or worker liveness from log scraping is fragile and lagging. A first-class metrics endpoint
  is the standard, low-friction source for these signals.
- **Push metrics to an external hosted APM instead of a scrapeable endpoint.** Rejected as the
  default for self-host, because it reintroduces an external dependency that the self-hosted stack
  exists to avoid. A Prometheus-format `/metrics` endpoint works with self-hosted Prometheus and
  Grafana. An operator can still point a hosted scraper at the endpoint if they choose.
- **Bundle Prometheus and Grafana as always-on rather than an optional profile.** Rejected. Not
  every self-host wants the extra containers. Making them optional (like `caddy`) keeps a minimal
  deploy lean while offering the full stack when wanted.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `/metrics` endpoint exposing queue depth, per-org in-flight, run durations, worker liveness.
- [x] `/metrics` respects serve exposure rules and never leaks secrets (unit-tested).
- [x] Optional `prometheus` + `grafana` containers and a starter dashboard in `deploy/self-host/`.

- [#719](https://github.com/bajutsu-e2e/bajutsu/pull/719) — Shipped the `/metrics` endpoint on both
  serve backends (the stdlib handler and the
  FastAPI control plane), rendering Prometheus-format metrics from state the control plane already
  tracks: in-flight jobs per org (`state.jobs`), and — with a database wired — queue depth, leased
  jobs, worker heartbeat freshness, and the oldest in-flight run (a new one-pass
  `Repository.metrics_snapshot`). The endpoint sits behind serve's auth gate (BE-0051) and emits
  only counts, ages, and org / worker ids — never a job spec or the token — so a scrape cannot leak
  a secret. Added the optional `metrics` compose profile (`prometheus` + `grafana`, a provisioned
  datasource, and a starter dashboard) to `deploy/self-host/`, and documented it in the self-host
  README and `docs/self-hosting.md` (+ `docs/ja/`).

## References

`bajutsu/serve/` (where `/metrics` lands), `deploy/self-host/` (the compose stack the containers
join),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (the self-hosting umbrella
this is carved from),
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) (the structured serve logs
this metrics work complements),
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) (the
exposure
rules `/metrics` must follow).

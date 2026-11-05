**English** · [日本語](BE-0244-deploy-hosted-web-ui-service-ja.md)

# BE-0244 — Deploy the hosted web UI service

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0244](BE-0244-deploy-hosted-web-ui-service.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Rejected** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0244") |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

> **Rejected — the service is already live.** A hosted, shared web UI service now runs through a
> path outside the phased plan below. The provisioning work this item tracked is therefore no longer
> needed. The item stays for the historical record.

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) selected the stack
for a publicly hosted web UI. It also shipped **all of the enabling software**:

- the five swap-in `serve` seams
- the persistence layer
- GitHub OAuth with durable sessions
- per-user role-based access control (RBAC)
- an audit log
- concurrency quotas
- multi-tenancy (the org model, request-time org resolution, org-scope enforcement, and per-org storage)

That work is code, and it is merged.

What remains is **not code**. The service still has to stand up on real infrastructure. This item
tracks that operational work:

- provisioning the Linux control plane, the macOS worker pool, the database, and object storage
- wiring production auth and secrets
- closing the security hardening items against a live, internet-exposed deployment

BE-0015 is the *design and software*, and this item is the *deployment*. The split lets BE-0015
close as Implemented, because its software is done. The ops work has its own lifecycle and depends
on paid, macOS-only capacity, so it is tracked on its own.

The split mirrors how [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)
(the post-completion worker model) and
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
(hosted config-source restriction) were already carved off the BE-0015 umbrella as their own
numbered items.

Related: [architecture](../../docs/architecture.md) · [ci](../../docs/ci.md) · the self-hosting
counterpart [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).

## Motivation

BE-0015's software groundwork is inert until it runs somewhere. A logged-in user still cannot pick
a project, hit Run, watch live logs, and view a report on shared infrastructure. No shared
infrastructure exists yet. The control plane, the database, and object storage have not been
provisioned. Neither has the macOS worker pool, which is the critical piece because it executes a run.

This deployment is a distinct kind of work from everything BE-0015 shipped:

- It is infrastructure-as-code and operations, not Python behind a seam.
- Much of it lives outside the repository (cloud accounts, domain name system (DNS) records, transport layer
  security (TLS) certificates, secret managers).
- It is gated on a cost decision. The **macOS workers dominate the bill** and do not scale to zero
  cleanly (MacStadium Orka nodes and EC2 Mac minimum allocations).

Keeping the deployment inside BE-0015 would force one of two outcomes. BE-0015 would stay open
indefinitely against paid-capacity provisioning. Or it would be marked Implemented while nothing is
actually hosted. A separate item keeps both honest: BE-0015 means "the software is done", and this
item means "the service is live".

The deterministic core (`bajutsu run`, the report) and the security posture are unchanged by this
work. Only the *invocation and plumbing* move to the cloud, as BE-0015's *Migration* section framed it.

## Detailed design

The scope is BE-0015's **Deployment plan (phased)** and the live-environment portion of its
**Security hardening** section. This item takes both as its work breakdown. BE-0015 **already
decided** the stack selections:

- FastAPI
- Caddy
- GitHub OAuth
- PostgreSQL
- the Postgres-table job queue
- Cloudflare R2
- MacStadium Orka
- Terraform and GitHub Actions

This item provisions and wires those choices. It does not reselect them.

### Phase 1 — MVP (ship a working shared service)

1. **Control plane hosting.** Containerize the server backend (GitHub Container Registry (GHCR)
   image) and deploy it to **Fly.io** (or Render). The platform or Caddy provides the reverse proxy
   and automatic TLS.
2. **System of record.** Provision managed **PostgreSQL** (Fly Postgres) and run the Alembic
   migrations. Point `BAJUTSU_DATABASE_URL` at the database. Run history, identity, audit, and the
   Postgres-table job queue are then live.
3. **Artifact storage.** Create the **Cloudflare R2** bucket. Wire the object-store `ArtifactStore`
   and the per-org prefixes. Serve report assets via short-lived signed URLs.
4. **One macOS worker.** Provision a single **MacStadium Orka** node. Install the worker agent as a
   launchd service. Give the agent the `db` extra and `BAJUTSU_DATABASE_URL`, so a finished run is
   recorded under its org and actor. Confirm the lease → run (`--erase`) → upload → result loop
   against the deployed control plane.
5. **Production auth and secrets.** Register the **GitHub OAuth** app for the deployed origin. Put
   the session and OAuth config, and the per-org `ANTHROPIC_API_KEY`, into a secret manager
   (Fly or Doppler).
6. **Acceptance.** A logged-in user picks a project and scenario, hits Run, watches live logs over
   server-sent events (SSE), and views the report. The flow works end to end on shared infrastructure.
   It works for both the single default org and a second configured org.

### Phase 2 — scale

7. **Orchestration.** Move the control plane to **Kubernetes** (GKE or EKS) with managed
   Postgres (Cloud SQL or RDS).
8. **Worker autoscaling.** Scale the **Orka** Mac pool off job-queue depth, with a small warm floor.
   Enforce **per-org concurrency quotas** on the scarce pool.
9. **Delivery and multi-region.** Put a content delivery network (**CDN**) in front of R2 for report
   assets. Make the control plane multi-region if needed.
10. **Observability.** Stand up **Sentry** (errors) and **Prometheus/Grafana** (metrics). Alert on
    queue depth and worker health. Emit the structured JSON logs
    ([BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md)).

### Security hardening (live-environment closure)

11. **Rate limiting** on run dispatch per user and per org.
12. **Worker sandboxing** in the deployed environment: an ephemeral Mac and Simulator per run, an
    **egress allowlist**, and no cross-tenant secret reuse.
13. **Edge protections.** Verify cross-origin resource sharing (CORS) and cross-site request forgery (CSRF)
    defenses, standard security headers, and signed, expiring artifact URLs against the live origin.

Each numbered unit is independently shippable. *Progress* below mirrors the units one for one.

## Alternatives considered

- **Keep the remaining work inside BE-0015.** Rejected. BE-0015 would stay open indefinitely
  against paid-capacity provisioning, or be marked Implemented while nothing is hosted. Splitting
  matches the existing precedent: BE-0106 and BE-0108 were carved off the same umbrella.
- **Fold deployment into the self-hosting item [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).**
  Rejected. BE-0016 runs the stack on *your own* Mac(s). This item is the *managed, multi-tenant
  public* deployment that BE-0015 selected. The audience, the infrastructure, and the cost model all differ.
- **Reselect the stack.** Out of scope. BE-0015's *Detailed design* and *Alternatives considered*
  decided the stack. This item provisions those choices and does not reopen them.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] 1 — Control plane hosting (containerize → Fly.io/Render, reverse proxy + TLS).
- [ ] 2 — Managed PostgreSQL provisioned; Alembic migrations run; `BAJUTSU_DATABASE_URL` wired.
- [ ] 3 — Cloudflare R2 bucket + object-store `ArtifactStore` with per-org prefixes and signed URLs.
- [ ] 4 — One MacStadium Orka worker agent (launchd) with the `db` extra; lease → run → upload → result verified.
- [ ] 5 — Production GitHub OAuth app + secret manager (sessions, per-org `ANTHROPIC_API_KEY`).
- [ ] 6 — Phase 1 acceptance: end-to-end run on shared infra for two orgs.
- [ ] 7 — Kubernetes control plane + managed Postgres.
- [ ] 8 — Orka autoscaling off queue depth + per-org concurrency quotas.
- [ ] 9 — CDN in front of R2; multi-region control plane if needed.
- [ ] 10 — Observability (Sentry + Prometheus/Grafana + alerting).
- [ ] 11 — Rate limiting on run dispatch per user/org.
- [ ] 12 — Worker sandboxing (ephemeral Mac/Simulator, egress allowlist, no cross-tenant secret reuse).
- [ ] 13 — Edge protections (CORS/CSRF, security headers, signed + expiring artifact URLs) verified live.

## References

`bajutsu/serve/`, [ci](../../docs/ci.md), [architecture](../../docs/architecture.md),
[reporting](../../docs/reporting.md), [cli](../../docs/cli.md#serve), the design-and-software item
[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), the self-hosting
counterpart [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), and
[BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md) for the operational logging
that realizes the observability row above.

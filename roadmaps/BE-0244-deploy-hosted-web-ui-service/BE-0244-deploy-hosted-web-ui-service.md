**English** · [日本語](BE-0244-deploy-hosted-web-ui-service-ja.md)

# BE-0244 — Deploy the hosted web UI service

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0244](BE-0244-deploy-hosted-web-ui-service.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0244") |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) selected the stack
for a publicly hosted web UI and shipped **all of the enabling software**: the five swap-in
`serve` seams, the persistence layer, GitHub OAuth with durable sessions, per-user RBAC, an audit
log, concurrency quotas, and multi-tenancy (the org model, request-time org resolution, org-scope
enforcement, and per-org storage). That work is code, and it is merged.

What remains is **not code** — it is standing the service up on real infrastructure. This item
tracks that operational work: provisioning the Linux control plane, the macOS worker pool, the
database, and object storage; wiring production auth and secrets; and closing the security
hardening items against a live, internet-exposed deployment. BE-0015 is the *design and software*;
this item is the *deployment*. Splitting it out lets BE-0015 close as Implemented (its software is
done) while the ops work — which has its own lifecycle and depends on paid, macOS-only capacity —
is tracked on its own.

This mirrors how [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)
(the post-completion worker model) and
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
(hosted config-source restriction) were already carved off the BE-0015 umbrella as their own
numbered items.

Related: [architecture](../../docs/architecture.md) · [ci](../../docs/ci.md) · the self-hosting
counterpart [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).

## Motivation

BE-0015's software groundwork is inert until it runs somewhere. A logged-in user still cannot pick
a project, hit Run, watch live logs, and view a report on shared infrastructure, because no shared
infrastructure exists yet — the control plane, the database, object storage, and (critically) the
macOS worker pool that actually executes a run have not been provisioned.

That deployment is a distinct kind of work from everything BE-0015 shipped. It is
infrastructure-as-code and operations rather than Python behind a seam; much of it lives outside
the repository (cloud accounts, DNS, TLS certificates, secret managers); and it is gated on a cost
decision, because the **macOS workers dominate the bill** and do not scale to zero cleanly
(MacStadium Orka nodes / EC2 Mac minimum allocations). Keeping it inside BE-0015 would either force
that item to stay open indefinitely against paid-capacity provisioning, or tempt marking it
Implemented while nothing is actually hosted. A separate item keeps both honest: BE-0015 =
"the software is done", this item = "the service is live".

The deterministic core (`bajutsu run`, the report) and the security posture are unchanged by this
work — only the *invocation and plumbing* move to the cloud, exactly as BE-0015's *Migration*
section framed it.

## Detailed design

The scope is BE-0015's **Deployment plan (phased)** and the live-environment portion of its
**Security hardening** section, taken as the work breakdown here. The stack selections
(FastAPI, Caddy, GitHub OAuth, PostgreSQL, the Postgres-table job queue, Cloudflare R2, MacStadium
Orka, Terraform + GitHub Actions) are **already decided in BE-0015** and are not reopened here;
this item is about provisioning and wiring them, not reselecting them.

### Phase 1 — MVP (ship a working shared service)

1. **Control plane hosting.** Containerize the server backend (GHCR image) and deploy it to
   **Fly.io** (or Render). Reverse proxy + automatic TLS via the platform or Caddy.
2. **System of record.** Provision managed **PostgreSQL** (Fly Postgres) and run the Alembic
   migrations; point `BAJUTSU_DATABASE_URL` at it so run history, identity, audit, and the
   Postgres-table job queue are live.
3. **Artifact storage.** Create the **Cloudflare R2** bucket and wire the object-store
   `ArtifactStore` + per-org prefixes; serve report assets via short-lived signed URLs.
4. **One macOS worker.** Provision a single **MacStadium Orka** node, install the worker agent as a
   launchd service, and give it the `db` extra + `BAJUTSU_DATABASE_URL` so a finished run is
   recorded under its org/actor. Confirm the lease → run (`--erase`) → upload → result loop against
   the deployed control plane.
5. **Production auth + secrets.** Register the **GitHub OAuth** app for the deployed origin; put the
   session/OAuth config and the per-org `ANTHROPIC_API_KEY` into a secret manager (Fly/Doppler).
6. **Acceptance:** a logged-in user picks a project + scenario, hits Run, watches live logs over
   SSE, and views the report — end to end, on shared infra, for both the single default org and a
   second configured org.

### Phase 2 — scale

7. **Orchestration.** Move the control plane to **Kubernetes** (GKE/EKS) with managed
   Postgres (Cloud SQL/RDS).
8. **Worker autoscaling.** Scale the **Orka** Mac pool off job-queue depth with a small warm floor;
   enforce **per-org concurrency quotas** on the scarce pool.
9. **Delivery + multi-region.** Put a **CDN** in front of R2 for report assets; make the control
   plane multi-region if needed.
10. **Observability.** Stand up **Sentry** (errors) + **Prometheus/Grafana** (metrics) with
    alerting on queue depth and worker health; emit the structured JSON logs
    ([BE-0055](../BE-0055-operational-logging/BE-0055-operational-logging.md)).

### Security hardening (live-environment closure)

11. **Rate limiting** on run dispatch per user/org.
12. **Worker sandboxing** in the deployed environment: ephemeral Mac/Simulator per run, an **egress
    allowlist**, and no cross-tenant secret reuse.
13. **Edge protections:** CORS/CSRF, standard security headers, and signed + expiring artifact URLs
    verified against the live origin.

Each numbered unit is independently shippable and is mirrored one-for-one in *Progress* below.

## Alternatives considered

- **Keep the remaining work inside BE-0015.** Rejected: it would leave BE-0015 open indefinitely
  against paid-capacity provisioning, or invite marking it Implemented while nothing is hosted.
  Splitting matches the existing precedent (BE-0106, BE-0108 were carved off the same umbrella).
- **Fold deployment into the self-hosting item [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).**
  Rejected: BE-0016 is running the stack on *your own* Mac(s); this item is the *managed,
  multi-tenant public* deployment BE-0015 selected. Different audience, different infra, different
  cost model.
- **Reselect the stack.** Out of scope: the stack was decided in BE-0015's *Detailed design* and its
  *Alternatives considered*; this item provisions those choices, it does not reopen them.

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

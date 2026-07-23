**English** · [日本語](BE-0051-serve-hardening-for-hosting-ja.md)

# BE-0051 — Serve hardening for hosting (auth, input validation)

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0051](BE-0051-serve-hardening-for-hosting.md) |
| Author | [@hirosassa](https://github.com/hirosassa) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0051") |
| Implementing PR | [#92](https://github.com/bajutsu-e2e/bajutsu/pull/92) (slice 1), [#94](https://github.com/bajutsu-e2e/bajutsu/pull/94) (slice 2), [#95](https://github.com/bajutsu-e2e/bajutsu/pull/95) (slice 3), [#96](https://github.com/bajutsu-e2e/bajutsu/pull/96) (slice 4), [#97](https://github.com/bajutsu-e2e/bajutsu/pull/97) (slice 5), [#114](https://github.com/bajutsu-e2e/bajutsu/pull/114) (crawl parity for slices 3 & 5) |
| Topic | Hosting the web UI |
| Related | [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md) |
<!-- /BE-METADATA -->

## Introduction

The stdlib `bajutsu serve` (`bajutsu/serve/`) is safe today only because it is **localhost-only and
single-user**: it has no authentication and, until slice 1 below (#92) confined it, `/api/run`
accepted a client-supplied scenario path.
Both hosting proposals — public/cloud [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
and self-hosted [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) — name a set of
**mandatory** security fixes before `serve` may be reached beyond loopback. This item collects those
fixes as a single, incrementally-shippable hardening track on the **existing** stdlib server, so the
deterministic core never changes and each slice is testable on the Linux gate without a Simulator.

It is the **prerequisite layer** under BE-0015/BE-0016: those describe the full hosted topology
(FastAPI control plane, macOS worker pool, OAuth, object storage); this one makes the server we
already ship safe to expose, before any of that exists.

## Motivation

`serve` is a thin launcher that shells out to `bajutsu run`. Exposed as-is, two properties make it
unsafe on any network beyond loopback:

- **No authentication.** Every endpoint is open; anyone who can reach the port can launch runs, read
  run artifacts, and read/write scenario files.
- **Client-controlled execution surface.** `/api/run` passed `body["scenario"]` straight into the
  `bajutsu run` argv with no check that it stayed within the app's scenarios dir, and `backend` /
  `udid` were free text — an arbitrary-path execution surface.

These are not theoretical: they are the exact items [BE-0015 §Security hardening](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
and [BE-0016 Tier A](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) flag as blockers. Closing
them on the current server is cheap, independently useful (it makes a Tailscale/LaunchAgent single-Mac
deployment safe today, per BE-0016 Tier A), and a clean foundation for the hosted system later.

## Detailed design

A sequence of independently-shippable slices on the existing `bajutsu/serve/` server. None touches
pass/fail or the deterministic core; all are testable through the serve HTTP harness without a
Simulator.

1. **Input validation on `/api/run`** *(implemented in #92)* — match the requested scenario against
   the selected app's scenarios dir by name (the run uses the path from the dir listing, never the
   client string) and reject a `backend` / `udid` that isn't a known token. Eliminates the
   arbitrary-path execution surface. The other endpoints (`/api/config`, `/api/scenario`,
   `/api/approve`, `/runs/...` serving) already confine their paths.
2. **Token authentication + non-loopback guard** *(implemented in #94)* — an optional shared token
   (`--token` / `BAJUTSU_SERVE_TOKEN`), compared in constant time. API clients present it as a
   `Bearer` header; the browser establishes an **HttpOnly, SameSite=Strict cookie via a POST login
   endpoint** (the token is never put in a URL — query strings leak through history, logs, and
   `Referer`). **Binding a non-loopback host without a token is refused at startup**, so the server
   can never be exposed unauthenticated by accident.
3. **Apply the same input validation to the other run-spawning endpoints** *(implemented in #95;
   `/api/crawl` in #114)* — `/api/record` and `/api/crawl`: the `backend` / `udid` token checks,
   mirroring slice 1.
4. **CSRF protection + standard security headers** *(implemented in #96)* — once cookie auth exists,
   protect state-changing
   POSTs with an **Origin check** (a present `Origin` must match `Host`) layered on the
   `SameSite=Strict` session cookie; API clients authenticate with the `Authorization` header and
   carry no ambient cookie. Add the standard security headers. Deferred behind slice 2 because it
   only matters once a cookie is in play.
5. **Rate-limiting run dispatch per token/org** *(implemented in #97; `/api/crawl` in #114)* — cap
   concurrent/inflight runs so one caller can't monopolize the (scarce) device, a lightweight
   precursor to BE-0015's per-org quotas.

Slices 1–2 are the minimum that makes a single-Mac, tailnet-reachable deployment (BE-0016 Tier A)
safe. Slices 3–5 round out the surface. Anything multi-tenant (per-org keys, RBAC, object storage,
the worker pool) stays in BE-0015/BE-0016 — this item deliberately stops at "the server we ship is
safe to put behind a private network with a token."

## Alternatives considered

- **Do the hardening only as part of the full BE-0015 control plane.** Rejected: that couples a
  safe-to-expose single-Mac server to a large, unbuilt FastAPI/OAuth/worker-pool effort. The fixes
  are small and independently valuable on the current server (they make BE-0016 Tier A safe today),
  so they belong in their own incremental track.
- **OAuth / per-org RBAC on the stdlib server.** Rejected for this layer: full identity belongs in
  the hosted control plane (BE-0015). A single shared token is the right weight for a single-Mac,
  private-network deployment and is implementable without new dependencies.
- **Bind `0.0.0.0` and rely on a network ACL / reverse proxy for auth.** Rejected as the *default*:
  an unauthenticated server one misconfiguration away from exposure is unsafe. Refusing a non-loopback
  bind without a token makes the safe path the default; a reverse proxy (Caddy basic-auth, per
  BE-0016) remains an option on top.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (public/cloud hosting),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (self-hosting),
`bajutsu/serve/`, [cli.md](../../docs/cli.md#serve)

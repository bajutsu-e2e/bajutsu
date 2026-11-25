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

The stdlib `bajutsu serve` (`bajutsu/serve/`) is safe today because it is **localhost-only and single-user**. It has no authentication. Until slice 1 below (#92) confined it, `/api/run` accepted a client-supplied scenario path.

Two hosting proposals name a set of **mandatory** security fixes that must land before `serve` is reachable beyond loopback. They are public/cloud [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) and self-hosted [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md). This item collects those fixes as a single hardening track that ships in increments on the **existing** stdlib server. The deterministic core never changes, and each slice is testable on the Linux gate without a Simulator.

This item is the **prerequisite layer** under BE-0015 and BE-0016. They describe the full hosted topology: a FastAPI control plane, a macOS worker pool, OAuth, and object storage. This item makes the server we already ship safe to expose before any of that exists.

## Motivation

`serve` is a thin launcher that shells out to `bajutsu run`. Exposed as-is on any network beyond loopback, it has two unsafe properties:

- **No authentication.** Every endpoint is open. Anyone who can reach the port can launch runs, read run artifacts, and read or write scenario files.
- **A client-controlled execution surface.** `/api/run` passed `body["scenario"]` straight into the `bajutsu run` argv. Nothing checked that the path stayed within the app's scenarios dir. `backend` and `udid` were free text. Together they formed an arbitrary-path execution surface.

These risks are not theoretical. [BE-0015 §Security hardening](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) and [BE-0016 Tier A](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) flag the same items as blockers.

Closing them on the current server is cheap and useful on its own. Per BE-0016 Tier A, it makes a Tailscale/LaunchAgent single-Mac deployment safe today. It also gives the later hosted system a clean foundation.

## Detailed design

The design is a sequence of slices on the existing `bajutsu/serve/` server. Each slice ships independently. None touches pass/fail or the deterministic core. All are testable through the serve HTTP harness without a Simulator.

1. **Input validation on `/api/run`** *(implemented in #92)*. The server matches the requested scenario by name against the selected app's scenarios dir. The run uses the path from the dir listing, never the client string. The server rejects a `backend` or `udid` that is not a known token. This removes the arbitrary-path execution surface. The other endpoints (`/api/config`, `/api/scenario`, `/api/approve`, and `/runs/...` serving) already confine their paths.
2. **Token authentication and a non-loopback guard** *(implemented in #94)*. An optional shared token (`--token` or `BAJUTSU_SERVE_TOKEN`) is compared in constant time. API clients present it as a `Bearer` header. The browser establishes an **HttpOnly, SameSite=Strict cookie through a POST login endpoint**. The token never goes in a URL, because query strings leak through history, logs, and `Referer`. **The server refuses to bind a non-loopback host without a token at startup**, so it can never be exposed unauthenticated by accident.
3. **The same input validation on the other run-spawning endpoints** *(implemented in #95; `/api/crawl` in #114)*. `/api/record` and `/api/crawl` get the `backend` and `udid` token checks, mirroring slice 1.
4. **CSRF protection and standard security headers** *(implemented in #96)*. Once cookie authentication exists, state-changing POSTs need protection. An **Origin check** does it: a present `Origin` must match `Host`. The check layers on the `SameSite=Strict` session cookie. API clients authenticate with the `Authorization` header and carry no ambient cookie. The slice also adds the standard security headers. It follows slice 2 because it matters merely once a cookie is in play.
5. **Rate-limiting run dispatch per token or org** *(implemented in #97; `/api/crawl` in #114)*. A cap on concurrent and in-flight runs stops one caller from monopolizing the scarce device. It is a lightweight precursor to the per-org quotas in BE-0015.

Slices 1 and 2 are the minimum that makes a single-Mac, tailnet-reachable deployment (BE-0016 Tier A) safe. Slices 3 to 5 round out the surface.

Anything multi-tenant stays in BE-0015 and BE-0016: per-org keys, role-based access control (RBAC), object storage, and the worker pool. This item deliberately stops at "the server we ship is safe to put behind a private network with a token."

## Alternatives considered

- **Do the hardening as part of the full BE-0015 control plane alone.** Rejected. That would couple a safe-to-expose single-Mac server to a large, unbuilt FastAPI, OAuth, and worker-pool effort. The fixes are small and valuable on their own on the current server, because they make BE-0016 Tier A safe today. They belong in their own incremental track.
- **OAuth and per-org RBAC on the stdlib server.** Rejected for this layer. Full identity belongs in the hosted control plane (BE-0015). A single shared token is the right weight for a single-Mac, private-network deployment, and it needs no new dependencies.
- **Bind `0.0.0.0` and rely on a network ACL or reverse proxy for authentication.** Rejected as the *default*. An unauthenticated server that sits one misconfiguration away from exposure is unsafe. Refusing a non-loopback bind without a token makes the safe path the default. A reverse proxy (Caddy basic-auth, per BE-0016) remains an option on top.

## Progress

- [x] Shipped — see the *Implementing PR* above.

## References

[BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) (public/cloud hosting),
[BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (self-hosting),
`bajutsu/serve/`, [cli.md](../../docs/cli.md#serve)

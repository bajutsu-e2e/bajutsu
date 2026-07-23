**English** · [日本語](BE-0108-hosted-config-source-restriction-ja.md)

# BE-0108 — Restrict config sources to upload and Git when hosted

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0108](BE-0108-hosted-config-source-restriction.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0108") |
| Implementing PR | [#648](https://github.com/bajutsu-e2e/bajutsu/pull/648) |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md), [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) |
<!-- /BE-METADATA -->

## Introduction

The web UI's **"Open config"** dialog offers three ways to bind the active config: a **Git
repository** ([BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md)),
an **uploaded `.zip` bundle** ([BE-0073](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md)),
and a **file browser over the serve host's `--root`** (the original source,
`bajutsu/serve/operations.py` `browse_fs` / `bind_config`). The file browser is the right default
for a local single-user `serve` on your own machine, but it makes no sense on a **hosted** deployment
(the `server` backend, [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)):
a browser user there has no relationship to the host's filesystem, and exposing a directory listing
of the operator's `--root` to every authenticated user is an unnecessary surface.

This item makes the set of offered config sources **deployment-aware**: on the `server` backend the
"Open config" dialog offers only the two sources a remote user can meaningfully use — **upload** and
**Git** — and the file-browser source is removed from the UI **and refused server-side**. The local
backend is unchanged: all three sources stay. Nothing in the schema, the runner, the drivers, or the
deterministic gate changes; no LLM is added anywhere.

## Motivation

The file browser is a **local-affordance** that leaks into the hosted deployment, where it is at best
useless and at worst a mild information-disclosure surface:

1. **A hosted user has no filesystem relationship to the host.** On the `server` backend the config,
   scenarios, and `runs/` live on a shared worker the browser user never provisioned. The two paths
   that put *their* suite on that host are exactly upload (BE-0073) and Git (BE-0063). Browsing the
   operator's `--root` cannot bind anything the user owns — it can only bind files an operator
   hand-placed, which is not how a hosted deployment is meant to be used.
2. **It exposes the operator's `--root` tree to every authenticated user.** BE-0051 confined the
   browser to `--root`, so it is not an arbitrary-path escape, but on a multi-user hosted server it
   still hands every logged-in user a directory listing of, and the ability to bind any config under,
   a tree the operator controls. On a single-user local `serve` that is the operator's own machine
   and is fine; on a shared host it is an avoidable exposure that neither BE-0015 nor BE-0051 removes.
3. **Hiding it in the UI alone would be cosmetic.** `/api/fs` (browse) and the path branch of
   `POST /api/config` (bind by path) would still answer a hand-crafted request. The restriction has
   to be enforced on the server so it is real, not just absent from the rendered dialog.

The fix is small and self-contained: the sources on offer become a property of the deployment, the
UI renders only the available ones, and the two file-browser endpoints refuse when the source is off.

## Detailed design

The signal is the **backend**: the `server` backend is *hosted*, the `local` backend (stdlib
`serve`, including a self-hosted single Mac) keeps all three sources. `ServeState` gains an explicit
`hosted: bool` (defaulting `False`), set `True` when the server backend wires its seams
(`bajutsu/serve/server/`), so the deterministic core and the local path are untouched and the flag is
testable through the serve HTTP harness without a Simulator.

1. **A hosting flag on `ServeState`.** Add `hosted: bool = False`; the server backend sets it `True`
   where it already swaps in its hosted seams (executor / logbus / stores / repository). The local
   backend never sets it. This is the single source of truth the rest of the item reads.

2. **Advertise the available config sources on `/api/config`.** `config_info` gains a
   `configSources` field — the list the UI may offer, e.g. `["git", "upload", "fs"]` locally and
   `["git", "upload"]` when hosted. Modelled on the existing `oauthEnabled` capability the same
   payload already carries, so the frontend reads deployment capability from one place.

3. **Hide the file-browser source in the UI when it is not offered.** `serve.js`, reading
   `configSources` from `/api/config` at startup, hides the "or browse the server" section of the
   "Open config" modal (the `.fsor` / `#fspath` / `#fslist` / `.fshint` block in `serve.html.j2`)
   and does not call `browseFs` when `fs` is absent. The Git and Upload sections are untouched. When
   `fs` is the *only* remaining trigger for auto-opening the dialog (no config bound at startup), the
   dialog still opens on Git + Upload.

4. **Refuse the file-browser endpoints server-side when hosted (defense in depth).** `browse_fs`
   (`/api/fs`) and the path branch of `bind_config` (`POST /api/config` with `path`) return a `4xx`
   (`{"error": "the file browser is disabled on a hosted server"}`) when `state.hosted` is set, so a
   hand-crafted request cannot bypass the hidden UI. The `git` and `upload` branches of
   `POST /api/config` / `/api/upload` are unaffected.

5. **Tests.** Extend the serve HTTP harness: on a hosted state, `/api/config` omits `fs` from
   `configSources`, `/api/fs` and the path-bind branch return the `4xx`, while `git`/`upload` still
   bind; on a local state, all three remain available and behavior is unchanged (a regression net for
   the local default).

6. **Docs.** Note the deployment-dependent source set where the config sources are documented
   (the serve / hosting docs, both `docs/` and `docs/ja/`).

No change touches pass/fail, the runner, the drivers, or the scenario schema; the restriction lives
entirely in the serve config-sourcing layer.

## Alternatives considered

- **Trigger on non-loopback exposure instead of the backend.** Disable the file browser whenever
  `serve` is bound beyond loopback (token-authed public stdlib server), not only on the `server`
  backend. Rejected as the default: a self-hosted single Mac reached over Tailscale
  ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) Tier A) is
  operated by the same person who owns the filesystem, so the browser is still useful there. Keying
  on the backend draws the line exactly where the filesystem stops being the user's own. An explicit
  operator override (a flag/env) could layer on later if a token-authed stdlib deployment wants the
  same restriction, but it is out of scope here.
- **Hide the source in the UI only.** Simpler, but cosmetic — the `/api/fs` and path-bind endpoints
  would still answer. Rejected in favor of server-side enforcement so the restriction is real.
- **Remove the file browser entirely.** It is the right and only local affordance for binding a
  config already on your own machine; removing it would regress the local single-user flow the tool
  started from. The restriction must be deployment-scoped, not global.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `hosted: bool` on `ServeState`, set by the server backend
- [x] `configSources` advertised on `/api/config` (`config_info`)
- [x] Frontend hides the file-browser source when `fs` is not offered
- [x] `browse_fs` / `bind_config` path branch refuse when hosted (server-side enforcement)
- [x] Tests: hosted omits/refuses `fs`, local keeps all three
- [x] Docs updated (config sources are deployment-dependent), both languages

**Log**

- Shipped in one PR: `ServeState.hosted` (set `True` only in `_build_server_state`), `configSources`
  on `config_info`, `403` refusals in `browse_fs` and the path branch of `bind_config`, the
  frontend gate on the `#fssrc` block, serve HTTP tests for both deployments, and the bilingual
  self-hosting note.

## References

- [BE-0015 — Web UI public hosting](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — the hosted (`server`) backend this item keys on.
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) — confined the browser to `--root`; this item removes it entirely when hosted.
- [BE-0063 — Git config source](../BE-0063-git-config-source/BE-0063-git-config-source.md) — one of the two sources that survive hosting.
- [BE-0073 — Upload a config bundle as a zip](../BE-0073-serve-zip-bundle-upload/BE-0073-serve-zip-bundle-upload.md) — the other surviving source.

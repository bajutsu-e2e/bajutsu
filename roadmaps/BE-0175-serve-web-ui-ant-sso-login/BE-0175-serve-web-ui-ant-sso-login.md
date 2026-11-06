**English** · [日本語](BE-0175-serve-web-ui-ant-sso-login-ja.md)

# BE-0175 — Sign in to the `ant` provider from the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0175](BE-0175-serve-web-ui-ant-sso-login.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0175") |
| Implementing PR | [#705](https://github.com/bajutsu-e2e/bajutsu/pull/705) |
| Topic | AI provider configuration |
| Related | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) |
<!-- /BE-METADATA -->

## Introduction

BE-0163 registered `ant` as a third AI provider. `ant` is the official Anthropic CLI's browser-based OAuth/SSO credential. With it, every AI path can bill a Claude Pro/Max/Console seat with no API key.

The serve Web UI's **Settings** panel lets an operator select the `ant` provider, but the sign-in itself stayed out of band. The panel told the operator to run `ant auth login` in the terminal that launched `serve`.

This item adds a **Sign in with SSO** button to that panel, so the Web UI can start the sign-in. The provider gate flips to reachable the moment the credential lands.

## Motivation

Selecting `ant` in Settings and then dropping to a terminal to run `ant auth login` is an awkward seam. The operator is already in the Web UI. The credential the CLI writes is exactly what the selected provider needs. The reachability that the Record/Crawl gate reads (`GET /api/provider`) already reflects the CLI's sign-in state. The missing piece is a way to *start* the sign-in from the same panel.

In its default mode, `ant auth login` opens a browser on the host and binds its own loopback callback. For a local `make serve`, the documented way to run the Web UI, serve needs to do three things: spawn the CLI, let the operator complete the browser flow, and poll for completion. No token passes through serve, because the CLI writes the machine credential itself.

## Detailed design

- **`ant_login` / `ant_login_status` operations** (`bajutsu/serve/operations/config.py`).

  `ant_login` refuses a hosted/multi-tenant deployment (403), because the sign-in writes a machine-global credential that the whole server would share. It refuses when the `ant` binary is absent (400, reusing the availability hint). Otherwise it spawns `ant auth login` through the injectable `ServeState.popen` seam with `stdin` closed, so only the browser+loopback path drives it, and returns 202.

  A second click while one login is in flight **supersedes** it. Serve terminates the stale process and starts a fresh one, never two concurrently. An abandoned browser flow can then be retried at once, instead of wedging the button until the CLI's own timeout.

  `ant_login_status` polls the held process and returns `idle` / `running` / `ok` / `error`, with the CLI's last output line as the detail for `error`.
- **Routing + RBAC** (`bajutsu/serve/handler.py`, `bajutsu/serve/authz.py`). `POST /api/ant/login` and `GET /api/ant/login` route to the two operations. The path joins `_ADMIN_PATHS`, so it is an admin action like `/api/provider`.
- **`ServeState` handle** (`bajutsu/serve/jobs.py`). A single `ant_login_proc` field holds the in-flight subprocess between the POST that starts it and the GET that polls it.
- **Settings UI** (`bajutsu/templates/serve.html.j2`, `bajutsu/templates/serve.js`). The `ant` section gains a **Sign in with SSO** button and a status line. The button's label and enabled state derive from `GET /api/provider`, so "Signed in ✓" and the gate never disagree. A click starts the login and polls to completion. It then aligns the active provider to `ant` and refreshes the Record/Crawl gate live.
- **Docs** (`docs/web-ui.md`, `docs/ja/web-ui.md`). The Settings section still described the removed Claude Code provider. It is corrected to the three current providers and the new SSO button.

## Alternatives considered

- **Headless sign-in for remote serve** (`ant auth login --no-browser`: print the authorize URL, paste the code back). Deferred. It requires keeping the subprocess alive across two requests and feeding it stdin, and remote serve is out of scope here. The button is gated to local serve. A hosted deployment signs the host in out of band, unchanged from BE-0163.
- **Leave sign-in to the terminal** (the BE-0163 status quo). Rejected. The panel already selects the provider, so starting the sign-in from the same place removes the one manual, out-of-UI step.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] `ant_login` / `ant_login_status` operations, with the injectable spawn seam
- [x] `POST` / `GET /api/ant/login` routing and the admin RBAC entry
- [x] `ServeState.ant_login_proc` handle
- [x] Settings UI button, status line, and polling
- [x] Docs (en + ja) corrected and the SSO button documented
- [x] Tests: endpoint (hosted 403, missing-binary 400, spawn 202, status transitions,
  supersede-on-reclick) and the status op

Log:

- Proposed in [#708](https://github.com/bajutsu-e2e/bajutsu/pull/708).
- [#705](https://github.com/bajutsu-e2e/bajutsu/pull/705) — implements the feature: the
  `ant_login` / `ant_login_status` operations and their `/api/ant/login` routing, the admin RBAC
  entry, the `ServeState.ant_login_proc` handle, the Settings **Sign in with SSO** button (with
  supersede-on-reclick), the corrected docs, and the tests. Flips this item to *Implemented*.

## References

- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) — the `ant` OAuth
  provider this builds on.
- [Anthropic CLI](https://github.com/anthropics/anthropic-cli) — `ant auth login`, the browser-based
  OAuth/SSO flow started from the button.

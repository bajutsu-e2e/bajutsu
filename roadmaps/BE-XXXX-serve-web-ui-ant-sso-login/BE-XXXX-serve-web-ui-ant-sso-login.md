**English** · [日本語](BE-XXXX-serve-web-ui-ant-sso-login-ja.md)

# BE-XXXX — Sign in to the `ant` provider from the serve Web UI

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-serve-web-ui-ant-sso-login.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | AI provider configuration |
| Related | [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) |
<!-- /BE-METADATA -->

## Introduction

BE-0163 registered `ant` — the official Anthropic CLI's browser-based OAuth/SSO credential — as a
third AI provider, so every AI path can bill a Claude Pro/Max/Console seat with no API key. The
serve Web UI's **Settings** panel lets an operator select the `ant` provider, but signing in was
left out of band: the panel only told the operator to run `ant auth login` in the terminal that
launched `serve`. This item adds a **Sign in with SSO** button to that panel so the sign-in can be
started from the Web UI, and the provider gate flips to reachable the moment the credential lands.

## Motivation

Selecting `ant` in Settings and then dropping to a terminal to run `ant auth login` is an awkward
seam: the operator is already in the Web UI, the credential the CLI writes is exactly what the
selected provider needs, and the reachability the Record/Crawl gate reads (`GET /api/provider`)
already reflects the CLI's sign-in state. The only missing piece is a way to *start* the sign-in
from the same panel. `ant auth login` in its default mode opens a browser on the host and binds its
own loopback callback, so for a local `make serve` — the documented way to run the Web UI — serve
only needs to spawn the CLI and let the operator complete the browser flow, then poll for
completion. No token ever passes through serve; the CLI writes the machine credential itself.

## Detailed design

- **`ant_login` / `ant_login_status` operations** (`bajutsu/serve/operations/config.py`).
  `ant_login` refuses a hosted/multi-tenant deployment (403 — the sign-in writes a machine-global
  credential the whole server would share), refuses when the `ant` binary is absent (400, reusing
  the availability hint), and otherwise spawns `ant auth login` through the injectable
  `ServeState.popen` seam with `stdin` closed (so only the browser+loopback path drives it),
  returning 202. A second click while one is in flight never spawns a duplicate. `ant_login_status`
  polls the held process: `idle` / `running` / `ok` / `error` (with the CLI's last output line as
  the detail).
- **Routing + RBAC** (`bajutsu/serve/handler.py`, `bajutsu/serve/authz.py`). `POST /api/ant/login`
  and `GET /api/ant/login` route to the two operations; the path joins `_ADMIN_PATHS`, so it is an
  admin action like `/api/provider`.
- **`ServeState` handle** (`bajutsu/serve/jobs.py`). A single `ant_login_proc` field holds the
  in-flight subprocess between the POST that starts it and the GET that polls it.
- **Settings UI** (`bajutsu/templates/serve.html.j2`, `bajutsu/templates/serve.js`). The `ant`
  section gains a **Sign in with SSO** button and a status line; the button's label/enabled state
  derives from `GET /api/provider` (so "Signed in ✓" and the gate never disagree), and its click
  starts the login, polls to completion, aligns the active provider to `ant`, and refreshes the
  Record/Crawl gate live.
- **Docs** (`docs/web-ui.md`, `docs/ja/web-ui.md`). The Settings section, which still described the
  removed Claude Code provider, is corrected to the three current providers and the new SSO button.

## Alternatives considered

- **Headless sign-in for remote serve** (`ant auth login --no-browser`: print the authorize URL,
  paste the code back). Deferred — it requires keeping the subprocess alive across two requests and
  feeding it stdin, and remote serve is out of scope here. The button is gated to local serve; a
  hosted deployment signs the host in out of band, unchanged from BE-0163.
- **Leave sign-in to the terminal** (the BE-0163 status quo). Rejected: the panel already selects
  the provider, so starting the sign-in from the same place removes the only manual, out-of-UI step.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] `ant_login` / `ant_login_status` operations, with the injectable spawn seam
- [ ] `POST` / `GET /api/ant/login` routing and the admin RBAC entry
- [ ] `ServeState.ant_login_proc` handle
- [ ] Settings UI button, status line, and polling
- [ ] Docs (en + ja) corrected and the SSO button documented
- [ ] Tests: endpoint (hosted 403, missing-binary 400, spawn 202, status transitions,
  supersede-on-reclick) and the status op

Log:

- Proposed. The implementation is drafted in [#705](https://github.com/bajutsu-e2e/bajutsu/pull/705)
  (open, not yet merged); when it lands, tick the boxes above, flip **Status** to *Implemented*, and
  record the PR under `Implementing PR`.

## References

- [BE-0163](../BE-0163-ant-cli-oauth-provider/BE-0163-ant-cli-oauth-provider.md) — the `ant` OAuth
  provider this builds on.
- [Anthropic CLI](https://github.com/anthropics/anthropic-cli) — `ant auth login`, the browser-based
  OAuth/SSO flow started from the button.

**English** · [日本語](BE-0056-web-ui-aws-sso-login-ja.md)

# BE-0056 — AWS SSO sign-in from the web UI for Bedrock

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0056](BE-0056-web-ui-aws-sso-login.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Topic | AI provider configuration |
<!-- /BE-METADATA -->

## Introduction

Let the web UI (`bajutsu serve`) **start an AWS SSO (IAM Identity Center) sign-in** and obtain the
AWS credentials the Bedrock AI provider needs, instead of requiring the operator to run
`aws sso login` in the shell before launching `serve`.
[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md) made Bedrock
a selectable provider and delegated authentication to the standard AWS credential chain; it
deliberately left credential *acquisition* to the environment. Today the web UI's Settings panel can
only pick the provider, model id, and region — the credentials must already be resolvable in the
process that launched `serve`. This item closes that gap: a Settings-panel sign-in that runs the SSO
device-authorization flow, surfaces the verification URL and code in the browser, and — once
approved — points spawned `record` / `crawl` jobs at the resulting SSO session.

It stays strictly on the Tier-1 side. The deterministic `run` / CI gate calls no model and is
untouched ([DESIGN §2 / §3.1](../../../DESIGN.md)); nothing here can put an LLM call into the gate.

## Motivation

[BE-0053](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md)'s motivation
is that teams standardized on AWS authenticate "via the IAM roles / SSO they already run" rather than
a provisioned `ANTHROPIC_API_KEY`. The provider seam delivers that for the CLI, but the web UI — the
front door for many users, and the only entry point for a remote, self-hosted `serve`
([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) Tier A) — leaves SSO entirely
out of band. Three concrete frictions follow:

- **The shell prerequisite isn't visible from the UI.** A user must know to run `aws sso login` and
  set `AWS_PROFILE` *before* `make serve`, then watch for silent failures. Nothing in the web UI
  tells them the Bedrock path is unauthenticated, or why a `record` job suddenly fails.
- **SSO sessions are short-lived (hours).** When the token expires, Bedrock jobs start failing with
  no in-UI signal and no way to re-authenticate without returning to a shell. A first-class
  "session status + re-authenticate" control is the real UX win, not just the initial sign-in.
- **Remote `serve` can't use `aws sso login` at all.** That command opens a browser **on the serve
  host**. When the host isn't the user's machine (a Mac mini reached over Tailscale, per
  [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)), the browser is in the
  wrong place. The SSO **device-authorization** flow solves this: surface the `verificationUriComplete`
  and user code in the web UI so the user approves in *their* browser.

The correction BE-0053 makes is worth repeating, because it shapes the design: "SSO" does **not**
mean "no authentication". It swaps the Anthropic key for **AWS credentials**; this item helps the
user *obtain* those credentials from the UI — it does not store or broker a credential of its own.

## Detailed design

Proposal altitude. This builds on three seams that already exist: the provider client factory
(`bajutsu/anthropic_client.py`), the serve settings handlers that write only into `os.environ`
(`bajutsu/serve/operations.py` — `set_provider` / `set_api_key`), and the job spawner that inherits
that environment (`bajutsu/serve/jobs.py` — `_spawn_env`).

### Scope (v1)

- **Assumes an existing SSO profile.** The operator has run `aws configure sso` once, so
  `~/.aws/config` holds a profile with `sso_session` / `sso_account_id` / `sso_role_name`. The web UI
  selects a profile by name, starts sign-in, and sets `AWS_PROFILE`. Entering the SSO config (start
  URL, account, role) from the UI is a noted future extension, not v1.
- **Local *and* remote `serve`.** The verification URL/code are shown in the browser, so a
  Tailscale-reached host ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)
  Tier A) works as well as a local one.
- **Single operator.** The serve process holds one SSO session for everyone who reaches it. Per-user
  identity on a shared, multi-tenant server is explicitly **out of scope** and belongs to
  [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) /
  [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (OAuth/IdP, per-org scoped
  secrets).

### How credentials reach a job (reuse the existing seam)

`record` / `crawl` jobs run as **subprocesses that inherit a copy of the serve process's
`os.environ`** (`_spawn_env` in `bajutsu/serve/jobs.py`). So if the serve process exports
`AWS_PROFILE` (and, per BE-0053, `AWS_REGION` for the Bedrock inference region), each job's
`AnthropicBedrock()` resolves credentials through botocore's SSO provider from that profile and the
standard token cache (`~/.aws/sso/cache`).

The consequence is the key UX property: **re-authentication needs no `serve` restart.** Each job is a
fresh process that re-resolves the credential chain, so once the SSO token cache is refreshed, the
next job picks it up. This is exactly why we anchor on `AWS_PROFILE` rather than letting the user
paste static temporary keys (which would freeze at launch and expire). As with `set_provider` /
`set_api_key`, serve writes **only** `AWS_PROFILE` / `AWS_REGION` into `os.environ` — never to disk;
the SSO token lives in AWS's standard cache, managed outside Bajutsu.

### The sign-in flow (SSO device authorization), with two engines

The user chose to support **both** mechanisms; they produce the same outcome (a populated standard
SSO token cache reachable via `AWS_PROFILE`) and are selected automatically (prefer the CLI when
present, else the native flow), overridable in settings:

1. **Native (boto3 `sso-oidc`).** Bajutsu calls `RegisterClient` → `StartDeviceAuthorization` and
   returns `verificationUriComplete` + `userCode` to the web UI, then polls `CreateToken` in the
   background until the user approves, and writes the token to the standard cache
   (`~/.aws/sso/cache/<sha1>.json`) so botocore consumes it. No AWS CLI dependency — boto3 is already
   pulled by the `anthropic[bedrock]` extra. The key risk to validate is writing the cache in exactly
   the shape botocore expects for auto-refresh.
2. **CLI delegation (`aws sso login --no-browser`).** When AWS CLI v2 is present, shell out to it
   (mirroring how `make serve` shells out for idb): it prints the verification URL/code (relayed to
   the UI) and owns the token cache and its refresh — the more robust path where the CLI exists.

### Serve endpoints (follow BE-0051's rules)

New endpoints alongside `provider_info` / `set_provider`, all obeying
[BE-0051](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
(token auth on every request; a non-loopback bind without a token is refused; state-changing POSTs
carry the Origin check + session cookie / Bearer):

- `GET /api/sso` — session status: signed-in or not, the active `AWS_PROFILE`, the expiry, and
  whether the profile resolves.
- `POST /api/sso/login` — body names the profile; starts the device flow; returns
  `{ verificationUri, userCode, expiresIn }`.
- `GET /api/sso/login/<id>` — poll; on completion, set `AWS_PROFILE` (and `AWS_REGION` if supplied)
  in `os.environ` and report success.
- `POST /api/sso/logout` — clear `AWS_PROFILE` (optionally invalidate the cached token).

### Web UI (Settings panel)

When the provider is `bedrock`, add an **AWS SSO** block beside the existing model id / region
controls: a profile selector (or name field) and a **Sign in with AWS SSO** button. Pressing it shows
the verification URL (opens in a new tab) and a copyable user code with a "waiting for approval…"
state; on completion it flips to a session view (profile + expiry) with **Re-authenticate** and
**Sign out**. The block shows only for `bedrock`, mirroring how the API-key block shows only for
`anthropic`.

### Authentication detail

The native flow distinguishes the **SSO session region** (for the `sso-oidc` calls, from the
profile's `sso_session`) from the **Bedrock inference region** (`AWS_REGION`, per BE-0053). Role
credentials themselves are resolved by botocore's SSO provider via `AWS_PROFILE`; Bajutsu only
triggers the device flow and populates the token cache. This dovetails with BE-0047's fail-closed
rule: if the configured provider's credentials don't resolve, the job fails with a clear error — no
silent fallback to another provider
([BE-0047](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md)).

### Dependency

The native flow needs only boto3/botocore (already in the `anthropic[bedrock]` extra); CLI delegation
needs AWS CLI v2 (optional, auto-detected). As with BE-0053, using the Bedrock path at all assumes
`uv sync --extra bedrock`; `make serve` installs the idb extra on demand but not the Bedrock one.

### doctor (optional)

`doctor` could gain a deterministic check that the selected SSO profile resolves and the session is
unexpired — the same spirit as BE-0053's optional provider-credential check. **TBD** for this item.

### Out of scope

- Per-user SSO identity on a shared server (multi-tenant) →
  [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) /
  [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md).
- Pasting static AWS keys or managing `AWS_BEARER_TOKEN_BEDROCK` from the UI — the existing env / `.env`
  path already covers those; this item is specifically the SSO experience.
- Bedrock for the `claude-code` agent — a separate mechanism, out of scope in BE-0053 too.

## Alternatives considered

- **A field to paste static temporary AWS keys (`AWS_ACCESS_KEY_ID` / `…SECRET…` / `…SESSION_TOKEN`).**
  Rejected: temporary keys freeze into the serve process at set time and can't refresh, so they expire
  mid-session and force a restart — the exact problem an `AWS_PROFILE`-anchored SSO session avoids
  (the BE-0053 discussion).
- **CLI delegation only (no native flow).** Rejected on its own: it forces AWS CLI v2 onto the serve
  host and, for a remote host, opens the browser in the wrong place. We support both and auto-select.
- **Native flow only (no CLI delegation).** Rejected on its own: re-implementing the SSO token cache
  format couples us to a botocore-internal shape; where the CLI exists, delegating to it is more
  robust. Hence both engines.
- **A Bajutsu-owned credential store.** Rejected: per BE-0047 / BE-0053 the credentials stay the
  user's, in AWS's standard locations; serve only points at them via `AWS_PROFILE`.
- **Enter the full SSO config (start URL / account / role) from the UI.** Deferred past v1, which
  assumes an existing `aws configure sso` profile; noted as a future extension.

## References

`bajutsu/anthropic_client.py` (the provider client factory), `bajutsu/serve/operations.py`
(`set_provider` / `provider_info` / `set_api_key` — the env-only settings handlers),
`bajutsu/serve/jobs.py` (`_spawn_env` — jobs inherit the serve environment),
`bajutsu/templates/serve.js` (the Settings panel),
[DESIGN §2 / §3.1](../../../DESIGN.md),
[BE-0053 — Amazon Bedrock as a pluggable AI provider](../../implemented/BE-0053-bedrock-ai-provider/BE-0053-bedrock-ai-provider.md),
[BE-0051 — Serve hardening for hosting](../../implemented/BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md),
[BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md),
[BE-0047 — AI data sovereignty](../BE-0047-ai-data-sovereignty/BE-0047-ai-data-sovereignty.md),
AWS docs — IAM Identity Center device authorization flow; botocore SSO credential provider.

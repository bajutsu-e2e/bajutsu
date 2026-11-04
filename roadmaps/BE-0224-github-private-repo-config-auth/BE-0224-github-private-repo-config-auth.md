**English** · [日本語](BE-0224-github-private-repo-config-auth-ja.md)

# BE-0224 — Granting private-repository access for the GitHub config source

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0224](BE-0224-github-private-repo-config-auth.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0224") |
| Implementing PR | [#956](https://github.com/bajutsu-e2e/bajutsu/pull/956) |
| Topic | Configuration sourcing |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md), [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) let `--config` (and the serve
config picker) name a GitHub repository at a ref: `github:<owner>/<repo>@<ref>:<path>`. Bajutsu then
materializes that subtree and loads the config from it. A **public** repository needs no credential.
A **private** one does.

One helper resolves that credential today, `github_token()`
([`bajutsu/config_source.py:100`](../../bajutsu/config_source.py)). It reads `GITHUB_TOKEN` or
`GH_TOKEN`, falls back to `gh auth token`, and otherwise stays anonymous. The result is a single,
process-global token, sent as `Authorization: Bearer` to `api.github.com`.

That is enough for one developer reading their own private repository on their own machine. It is
under-specified for the case the feature actually targets: an **unattended, possibly multi-user
self-hosted `serve`** ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) that
must read one or more private test repositories.

This item stays scoped to **github.com** and asks a single question: *how is access to a private
repository granted to the GitHub config source?* It organizes four things:

- the accepted credential types
- how each type is supplied and scoped
- the least-privilege permission each type needs
- how a missing or insufficient credential is reported

Only credential acquisition changes. The schema, runner,
[drivers](../../docs/glossary.md#driver-backend-actuator-platform), and deterministic gate are
untouched, and no large language model (LLM) call is added.

## Motivation

The current single-token model has three gaps. They appear only once the config source is used the
way BE-0016 intends.

- **No service identity for an unattended host.** A self-hosted `serve` daemon (launchd or systemd)
  has no interactive `gh` session, so `gh auth token` does not apply. The operator must inject a
  personal access token (PAT) at the daemon level. A PAT is tied to a *person*: it carries that
  person's access, and it stops working when they rotate it or leave. A service that reads private
  repositories unattended should authenticate **as itself**. On GitHub, that identity is a **GitHub
  App installation**, whose token is short-lived, per-installation, and auditable. Bajutsu has no
  path to mint or use one.
- **No scoping: one token for every repository and every user.** `github_token()` returns one token
  for the whole process and sends it for whatever repository is requested. On a multi-org self-host
  (BE-0016 ships multi-org isolation), every tenant's config fetch borrows the operator's single
  identity and its full access. A credential cannot be bound to a particular source (owner/repo) or
  org.
- **Least privilege is neither documented nor steerable.** A classic PAT with the `repo` scope grants
  read and write access to *all* the user's private repositories. That is far more than "read this
  one test repository". GitHub offers narrower grants: a fine-grained PAT, or an App installation
  limited to specific repositories with **Contents: read**. Nothing documents them or lets an
  operator prefer them, so the path of least resistance is the most over-privileged credential.

A fourth, smaller gap makes the other three harder to operate. A private repository the caller
cannot see returns **404**, not 403. `_get` lets that `HTTPError` propagate raw
([`config_source.py:126`](../../bajutsu/config_source.py)). "You have not granted access" therefore
looks like "the repository does not exist".

The web UI sharpens every one of these gaps. serve's "Open config" dialog already offers a **"From a
Git repository"** source (BE-0063, bound through `ops.bind_config` in `bajutsu/serve/handler.py:318`).
The dialog has **no field for a credential**, so the token still comes only from the serve process's
environment. A UI user who points serve at a private repository, on a machine whose daemon has no
token, gets the opaque 404. Nothing on the screen in front of them lets them supply access.

That user is the self-hosted operator, and the dialog is the one place a credential can be *entered
and stored* rather than pre-injected into the daemon.

None of this touches the prime directives. Credential resolution is deterministic and model-free: it
merely *acquires* the tree, and the resolved SHA stays the determinism anchor. Every credential
difference lives in the environment or config, not in the tool, drivers, or runner.

## Detailed design

The design organizes private-repository access into five mutually exclusive, collectively exhaustive
(MECE) pieces. All are github.com-only. Piece 4 is independently shippable.

### 1. The accepted credential types

This piece defines and documents the set of credentials that grant a private-repository read. The
list runs from least to most suited to an unattended service:

- **`gh auth token` (interactive, for a developer).** Kept as today's fallback for a developer on
  their own machine.
- **Personal access token via `GITHUB_TOKEN` / `GH_TOKEN`.** Kept. The documentation strongly prefers
  a **fine-grained** PAT scoped to the specific repositories with **Contents: read**, over a classic
  broad-`repo` PAT.
- **GitHub App installation token (recommended for a self-hosted service).** New. Bajutsu is given an
  App id, a private key, and the target installation (or resolves the installation from the
  repository). It mints a short-lived installation access token and uses that token as the Bearer.

The App token is the service-identity answer. It is short-lived, limited to the installation's
repositories, and not tied to a person.

This is the one piece that adds real logic: JSON Web Token (JWT) signing plus the installation-token
endpoint. It is designed as an optional credential provider behind the same resolution seam. A
deployment that uses only a PAT pulls in nothing extra.

### 2. Supply path and precedence

This piece defines **where** each credential comes from and **in what order** it is resolved. The
behavior becomes predictable, and an unattended daemon gets a clear way in.

- A documented precedence, for example an explicit App credential, then `GITHUB_TOKEN` / `GH_TOKEN`,
  then `gh auth token`.
- The daemon supply path, spelled out. A PAT comes from launchd `EnvironmentVariables`, systemd
  `Environment`, or a secrets file. The App path uses an App private-key file.
- Resolution **per acquisition**, not once at startup. The env path already behaves this way, since
  the transport is built per `materialize`. A rotated secret then takes effect without a restart.

### 3. Scoping a credential to a source

A credential can be **bound to a config source** (a given owner/repo, or an org) rather than only to
the one process-global token. A multi-org self-host then does not share one identity across tenants.

This piece reconciles the config source with BE-0016's multi-org isolation. Two existing items bound
its surface in a *hosted* deployment:

- [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
  decides which config sources a hosted UI even offers.
- The auth already in
  [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) bounds the
  surface as well.

The main open design choice to settle here is where the binding is expressed: in config, in the
environment, or through serve's authenticated API. Any credential stays out of logs and joins
redaction's defaults, as BE-0063 already requires.

### 4. Least-privilege guidance and auth diagnostics

This piece is independently shippable.

- **Document the minimal grant** for each type. Fine-grained PATs and App installations need
  Contents: read on the target repositories. The documentation goes in
  [docs/configuration.md](../../docs/configuration.md) and
  [docs/self-hosting.md](../../docs/self-hosting.md), bilingually, and steers operators to the narrow
  grant.
- **Make the auth failure legible.** Wrap the transport's `HTTPError` and map it by status *and*
  sub-type. Check the specific cases first, so the message names the real cause rather than always
  blaming access:
  - a **403 with `X-RateLimit-Remaining: 0`** (or a `Retry-After` header) → a rate-limit or abuse
    message (wait, or authenticate to raise the limit), *not* an access message. More repository
    permission does not fix a rate limit.
  - a **403 carrying an `X-GitHub-SSO` response header** (the org requires security assertion markup
    language (SAML) single sign-on (SSO) authorization) → "authorize this credential for the org's
    SSO". This is again distinct from granting more repository access.
  - **401** → "the supplied token was rejected".
  - **404**, or any other **403** → "repository not found *or* access not granted: provide a
    credential with Contents: read for `<owner>/<repo>`". Naming the most-likely-missing grant is
    the point, but only as the fall-through after the specific cases above.

  Inspecting the `403` sub-type (headers) before proposing "Contents: read" keeps the diagnostic from
  sending an operator down the wrong path. This needs no credential-provider change and can land
  first.

### 5. The serve Web UI surface

The web UI is where a self-hosted operator actually grants access. The credential model above has to
reach the screen, and it does so by reusing serve's existing seams instead of inventing a parallel
one.

- **A credential affordance on the "From a Git repository" dialog.** When binding a private Git
  source, the operator can **pick an already-stored credential** or **enter a new one**: a
  fine-grained PAT, or the App credential from piece 1. The browser holds the value only until
  submit and never echoes it back. The dialog shows a masked preview, matching how serve already
  treats secrets.
- **Stored through the existing `SecretStore` seam, not a new store.** serve already has a fitting
  seam: `EnvSecretStore` for a local single-user serve, and the per-org, encrypted-at-rest
  `DbSecretStore` for the hosted backend
  ([BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md);
  `bajutsu/serve/secrets.py`, `bajutsu/serve/server/secrets.py`). A UI-entered Git credential goes
  through that seam. It shows a masked preview only, and no plaintext is readable by an HTTP handler.
  Durability is whatever the two backends already provide:
  - **Held for the process lifetime** on a local serve. `EnvSecretStore` keeps the credential in
    memory, so a local single-user credential does not survive a restart, exactly as today's env
    path.
  - **Persisted per-org, encrypted at rest** on the hosted backend (`DbSecretStore`).

  The hosted path also makes piece 3's **per-source / per-org scoping** concrete. The credential is
  already scoped by `org_id`, so each tenant's stored Git credential is naturally its own.

  The readable `provider_store` that persists AI-provider settings
  ([BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md))
  is the precedent for a serve settings surface. A Git credential still belongs in the *write-once*
  `SecretStore`, not the readable one.
- **Deployment-aware, per [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md).**
  The credential field rides only on the Git source, which both the local and hosted backends offer.
  Nothing new is exposed on a backend that does not offer the Git source. On the hosted backend the
  credential is per-org as above. On a local serve it defaults to the process env exactly as today,
  so a single-user local run is unchanged.
- **Diagnostics surfaced in the dialog.** When a bind fails, piece 4's auth message appears **inline
  in the bind dialog**. The message for 404/403 reads "access not granted: provide a credential with
  Contents: read for `<owner>/<repo>`". The operator fixes the problem in place instead of reading a
  server log.

### What stays unchanged

The spec syntax, the SHA-keyed cache, `_load_effective`'s seam, the config schema, `resolve()`, the
runner, the drivers, the assertion evaluator, and the deterministic gate are untouched. Only *how a
credential for a private github.com repository is chosen, scoped, and diagnosed* changes. This is
BE-0063's "only its acquisition changes", narrowed to the private-access question.

## Alternatives considered

- **SSH deploy keys.** Rejected. BE-0063's transport is the HTTPS REST tarball endpoint, not
  git-over-SSH, so a deploy key does not authenticate the call. Supporting it would mean switching to
  `git clone`, which BE-0063 already rejected: it needs the `git` binary, and shallow-fetching an
  arbitrary SHA is not uniform.
- **Classic PAT as the recommended path.** Rejected as the *recommendation*, but kept as a supported
  input. A classic `repo`-scope PAT over-grants (all private repositories, read and write) and is tied
  to a person. The guidance steers to a fine-grained PAT or an App installation.
- **Keep the single process-global token only.** Rejected. It is fine for a single-team self-host.
  It is wrong for BE-0016's multi-org isolation, where one shared identity crosses tenant boundaries.
- **Store the credential in the config file or repository.** Rejected. A secret does not belong in
  version control. Credentials stay in the environment, a key file, or serve's secret store.
- **OAuth device flow inside serve.** Deferred. It is heavier, and the env and App paths already
  cover the unattended case. A device flow can follow if an interactive self-host login is wanted.
- **GitHub App only, dropping the PAT path.** Rejected. An App is the right *default* for a service
  but is overkill for a single developer. Both stay, with the App recommended for unattended hosts.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] #4 Least-privilege docs (configuration + self-hosting, bilingual) and `HTTPError` 401/403/404 auth diagnostics in `config_source.py` (`github_http_error_message` maps rate-limit / SSO / 401 / 404, specific sub-types first; `GitHubAccessError`, a `ValueError`, surfaces the cause to the CLI as exit-2 and to serve inline).
- [x] #1 GitHub App installation-token provider (`bajutsu/github_app.py`: RS256 JWT via `cryptography` behind the `githubapp` extra + the installation-token endpoint), lazy-loaded behind the credential seam alongside the existing PAT / `gh` paths.
- [x] #2 Documented credential precedence (App → `GITHUB_TOKEN`/`GH_TOKEN` → `gh auth token` → anonymous, resolved per acquisition) and the unattended-daemon supply path (launchd / systemd env, an App key file).
- [x] #3 Per-org credential scoping via the hosted `DbSecretStore` (each org's stored Git credential is its own), bounded by BE-0108 / BE-0051. Per-*source* (owner/repo) binding was scoped out this round and is a future follow-up.
- [x] #5 serve "From a Git repository" dialog credential field, stored via the `SecretStore` seam (BE-0136, new `gitConfigToken` secret → `GITHUB_TOKEN` locally, encrypted per-org on the hosted backend), deployment-aware (BE-0108), with the auth diagnostic surfaced inline.

**Log**

- Implemented #1–#5 in one change: the credential seam + App provider + diagnostics in `config_source.py` / `github_app.py`, the serve `/api/gitcredential` endpoint + UI field, and the bilingual docs. Per-source (owner/repo) scoping deferred (#3 delivered as per-org). PR: [#956](https://github.com/bajutsu-e2e/bajutsu/pull/956).

## References

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) — the Git config source this
  extends; [`bajutsu/config_source.py`](../../bajutsu/config_source.py) (`github_token()` at `:100`,
  `_GitHubTransport` bearer header at `:124`, the `api.github.com` requests at `:133,137`, `_get` at
  `:126`).
- [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) (self-hosting and its
  multi-org isolation — why per-source scoping matters),
  [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) (serve
  hardening — token auth, redaction, path confinement the credential path honors),
  [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
  (which config sources a hosted deployment offers),
  [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md) (the
  `SecretStore` seam a UI-entered credential is stored through),
  [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md)
  (the serve settings-persistence precedent).
- serve: `bajutsu/serve/handler.py` (`bind_config`), `bajutsu/serve/secrets.py` /
  `bajutsu/serve/server/secrets.py` (the `SecretStore` seam), `bajutsu/serve/state.py` (the active
  config's Git-source provenance).
- GitHub docs: personal access tokens (fine-grained vs classic), GitHub Apps and installation access
  tokens, the `Contents` repository permission.
- [docs/configuration.md](../../docs/configuration.md), [docs/self-hosting.md](../../docs/self-hosting.md).

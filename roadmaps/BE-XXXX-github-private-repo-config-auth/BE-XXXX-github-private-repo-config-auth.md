**English** · [日本語](BE-XXXX-github-private-repo-config-auth-ja.md)

# BE-XXXX — Granting private-repository access for the GitHub config source

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-github-private-repo-config-auth.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Configuration sourcing |
| Related | [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md), [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md), [BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md) |
<!-- /BE-METADATA -->

## Introduction

[BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) let `--config` (and the serve
config picker) name a GitHub repository at a ref — `github:<owner>/<repo>@<ref>:<path>` — so Bajutsu
materializes that subtree and loads the config from it. A **public** repository needs no credential;
a **private** one does. Today that credential is resolved by one helper, `github_token()`
([`bajutsu/config_source.py:100`](../../bajutsu/config_source.py)): `GITHUB_TOKEN` / `GH_TOKEN`, then
a fallback to `gh auth token`, else anonymous — a single, process-global token sent as
`Authorization: Bearer` to `api.github.com`.

That is enough for one developer reading their own private repo on their own machine, but it is
under-specified for the case the feature actually targets: an **unattended, possibly multi-user
self-hosted `serve`** ([BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md)) that
must read one or more private test repositories. This item stays scoped to **github.com** and asks a
single question: *how is access to a private repository granted to the GitHub config source?* It
organizes the accepted credential types, how each is supplied and scoped, the least-privilege
permission each needs, and how a missing or insufficient credential is reported. It changes only
credential acquisition; the schema, runner, drivers, and the deterministic gate are untouched, and no
large language model (LLM) call is added.

## Motivation

The current single-token model has three gaps that only appear once the config source is used the way
BE-0016 intends:

- **No service identity for an unattended host.** A self-hosted `serve` daemon (launchd / systemd)
  has no interactive `gh` session, so `gh auth token` does not apply; the operator must inject a
  personal access token (PAT) at the daemon level. A PAT is tied to a *person*: it carries that
  person's access, and it stops working when they rotate it or leave. A service that reads private
  repos unattended should authenticate **as itself**, which on GitHub is a **GitHub App
  installation** (a short-lived, per-installation, auditable token) — but Bajutsu has no path to mint
  or use one.
- **No scoping: one token for every repo and every user.** `github_token()` returns one token for
  the whole process, sent for whatever repo is requested. On a multi-org self-host (BE-0016 ships
  multi-org isolation), every tenant's config fetch borrows the operator's single identity and its
  full access. There is no way to bind a credential to a particular source (owner/repo) or org.
- **Least privilege is neither documented nor steerable.** A classic PAT with the `repo` scope grants
  read/write to *all* the user's private repos — far more than "read this one test repo". GitHub
  offers narrower grants (a fine-grained PAT or an App installation limited to specific repos with
  **Contents: read**), but nothing documents that or lets an operator prefer it, so the path of least
  resistance is the most over-privileged credential.

A fourth, smaller gap makes all of the above harder to operate: a private repo the caller cannot see
returns **404** (not 403), and `_get` lets that `HTTPError` propagate raw
([`config_source.py:127`](../../bajutsu/config_source.py)), so "you have not granted access" looks
like "the repository does not exist".

The web UI sharpens every one of these. serve's "Open config" dialog already offers a **"From a Git
repository"** source (BE-0063; bound through `ops.bind_config`, `bajutsu/serve/handler.py:318`), but
it has **no field for a credential** — the token still comes only from the serve process's
environment. So a UI user who points serve at a private repo, on a machine whose daemon has no token,
gets the opaque 404 with no way to supply access from the screen in front of them. This is exactly the
self-hosted operator's path, and it is the one place a credential can be *entered and stored* rather
than pre-injected into the daemon.

None of this touches the prime directives: credential resolution is deterministic and model-free (it
only *acquires* the tree; the resolved SHA stays the determinism anchor), and every credential
difference lives in the environment or config, not in the tool, drivers, or runner.

## Detailed design

The design organizes private-repo access into four mutually exclusive, collectively exhaustive
pieces. All are github.com-only. #4 is independently shippable.

### 1. The accepted credential types (a defined, documented set)

State explicitly which credentials grant a private-repo read, from least to most suited to an
unattended service:

- **`gh auth token` (interactive / developer).** Kept as today's fallback for a developer on their
  own machine.
- **Personal access token via `GITHUB_TOKEN` / `GH_TOKEN`.** Kept. Documented with a strong
  preference for a **fine-grained** PAT scoped to the specific repos with **Contents: read**, over a
  classic broad-`repo` PAT.
- **GitHub App installation token (recommended for a self-hosted service).** New: given an App id, a
  private key, and the target installation (or resolved from the repo), Bajutsu mints a short-lived
  installation access token and uses it as the Bearer. This is the service-identity answer — the
  token is short-lived, limited to the installation's repos, and not tied to a person. This is the
  one piece that adds real logic (JSON Web Token (JWT) signing plus the installation-token endpoint);
  it is designed as an optional credential provider behind the same resolution seam, so a deployment
  that only uses a PAT pulls in nothing extra.

### 2. Supply path and precedence

Define **where** each credential comes from and **in what order** it is resolved, so the behavior is
predictable and an unattended daemon has a clear way in:

- A documented precedence (e.g. an explicit App credential, then `GITHUB_TOKEN` / `GH_TOKEN`, then
  `gh auth token`).
- The daemon supply path spelled out (launchd `EnvironmentVariables` / systemd `Environment` / a
  secrets file for a PAT; an App private-key file for the App path).
- Resolved **per acquisition**, not captured once at startup (already true for the env path, since the
  transport is built per `materialize`), so a rotated secret takes effect without a restart.

### 3. Scoping a credential to a source (not just process-global)

Allow a credential to be **bound to a config source** — a given owner/repo, or an org — rather than
only the one process-global token, so a multi-org self-host does not share one identity across
tenants. This is the piece that reconciles the config source with BE-0016's multi-org isolation, and
its surface in a *hosted* deployment is bounded by
[BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md)
(which config sources a hosted UI even offers) and the auth already in
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md). Whether the
binding is expressed in config, in the environment, or through serve's authenticated API is the main
open design choice to settle here. Any credential stays out of logs and joins redaction's defaults,
as BE-0063 already requires.

### 4. Least-privilege guidance and auth diagnostics (independently shippable)

- **Document the minimal grant** for each type — Contents: read on the target repos for a
  fine-grained PAT or an App installation — in
  [docs/configuration.md](../../docs/configuration.md) and
  [docs/self-hosting.md](../../docs/self-hosting.md), bilingually, steering operators to the narrow
  grant.
- **Make the auth failure legible.** Wrap the transport's `HTTPError`: a **404 / 403** on the repo
  becomes "repository not found *or* access not granted — provide a credential with Contents: read
  for `<owner>/<repo>`", and a **401** becomes "the supplied token was rejected". Naming the
  most-likely-missing grant is the point. This needs no credential-provider change and can land first.

### 5. serve (Web UI) surface

The web UI is where a self-hosted operator actually grants access, so the credential model above has
to reach the screen — reusing serve's existing seams, not inventing a parallel one:

- **A credential affordance on the "From a Git repository" dialog.** When binding a private Git
  source, the dialog lets the operator either **pick an already-stored credential** or **enter a new
  one** (a fine-grained PAT, or the App credential from #1). The value is never held in the browser
  beyond submit and never echoed back — the dialog shows a masked preview, matching how serve already
  treats secrets.
- **Stored through the existing `SecretStore` seam, not a new store.** serve already has the seam that
  fits this: `EnvSecretStore` for a local single-user serve and the per-org, encrypted-at-rest
  `DbSecretStore` for the hosted backend
  ([BE-0136](../BE-0136-serve-write-once-secrets/BE-0136-serve-write-once-secrets.md);
  `bajutsu/serve/secrets.py`, `bajutsu/serve/server/secrets.py`). A UI-entered Git credential is
  persisted there — masked-preview only, no plaintext an HTTP handler can read back — which is also
  what makes #3's **per-source / per-org scoping** concrete: on the hosted backend the credential is
  already scoped by `org_id`, so each tenant's stored Git credential is naturally its own. (The
  readable `provider_store` that persists AI-provider settings,
  [BE-0184](../BE-0184-persist-serve-ai-provider-settings/BE-0184-persist-serve-ai-provider-settings.md),
  is the precedent for a serve settings surface — but a Git credential belongs in the *write-once*
  `SecretStore`, not the readable one.)
- **Deployment-aware, per [BE-0108](../BE-0108-hosted-config-source-restriction/BE-0108-hosted-config-source-restriction.md).**
  The credential field rides only on the Git source, which is offered on both the local and hosted
  backends; nothing new is exposed on a backend that does not offer the Git source. On the hosted
  backend the credential is per-org as above; on a local serve it defaults to the process env exactly
  as today, so a single-user local run is unchanged.
- **Diagnostics surfaced in the dialog.** #4's auth message (404/403 → "access not granted — provide
  a credential with Contents: read for `<owner>/<repo>`") is shown **inline in the bind dialog** when
  a bind fails, so the operator fixes it in place instead of reading a server log.

### What stays unchanged

The spec syntax, the SHA-keyed cache, `_load_effective`'s seam, the config schema, `resolve()`, the
runner, the drivers, the assertion evaluator, and the deterministic gate are untouched. Only *how a
credential for a private github.com repo is chosen, scoped, and diagnosed* changes — BE-0063's "only
its acquisition changes", narrowed to the private-access question.

## Alternatives considered

- **SSH deploy keys.** Rejected: BE-0063's transport is the HTTPS REST tarball endpoint, not
  git-over-SSH, so a deploy key does not authenticate the call. Supporting it would mean switching to
  `git clone`, which BE-0063 already rejected (needs the `git` binary; shallow-fetching an arbitrary
  SHA is not uniform).
- **Classic PAT as the recommended path.** Rejected as the *recommendation* (kept as a supported
  input): a classic `repo`-scope PAT over-grants (all private repos, read/write) and is tied to a
  person. The guidance steers to a fine-grained PAT or an App installation.
- **Keep the single process-global token only.** Rejected: fine for a single-team self-host, wrong
  for BE-0016's multi-org isolation, where one shared identity crosses tenant boundaries.
- **Store the credential in the config file / repo.** Rejected: a secret does not belong in
  version control; credentials stay in the environment, a key file, or serve's secret store.
- **OAuth device flow inside serve.** Deferred: heavier, and the env / App paths already cover the
  unattended case; a device flow can follow if an interactive self-host login is wanted.
- **GitHub App only, dropping the PAT path.** Rejected: an App is the right *default* for a service
  but is overkill for a single developer; both stay, with the App recommended for unattended hosts.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] #4 Least-privilege docs (configuration + self-hosting, bilingual) and `HTTPError` 401/403/404 auth diagnostics in `config_source.py` (independently shippable).
- [ ] #1 GitHub App installation-token provider (JWT + installation-token endpoint) behind the credential seam, alongside the existing PAT / `gh` paths.
- [ ] #2 Documented credential precedence and unattended-daemon supply path.
- [ ] #3 Per-source (owner/repo or org) credential scoping, bounded by BE-0108 / BE-0051 for hosted deployments.
- [ ] #5 serve "From a Git repository" dialog credential field, stored via the `SecretStore` seam (BE-0136), deployment-aware (BE-0108), with the auth diagnostic surfaced inline.

## References

- [BE-0063](../BE-0063-git-config-source/BE-0063-git-config-source.md) — the Git config source this
  extends; [`bajutsu/config_source.py`](../../bajutsu/config_source.py) (`github_token()` at `:100`,
  `_GitHubTransport` bearer header at `:123`, the `api.github.com` requests at `:133,137`, `_get` at
  `:127`).
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

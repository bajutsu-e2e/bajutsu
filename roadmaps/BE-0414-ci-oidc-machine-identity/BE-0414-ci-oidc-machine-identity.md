**English** · [日本語](BE-0414-ci-oidc-machine-identity-ja.md)

# BE-0414 — Authenticate a CI job to serve with a GitHub Actions OIDC token

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0414](BE-0414-ci-oidc-machine-identity.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0414") |
| Implementing PR | [#1986](https://github.com/bajutsu-e2e/bajutsu/pull/1986) (units 1-2), [#2004](https://github.com/bajutsu-e2e/bajutsu/pull/2004) (units 3-4) |
| Topic | Hosting the web UI |
| Related | [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md), [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) |
<!-- /BE-METADATA -->

## Introduction

A hosted `bajutsu serve` authenticates two kinds of caller today.

- A human signs in through GitHub OAuth (Open Authorization) and gets an identity. The role gate
  then checks that identity per endpoint
  ([BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)).
- A worker presents the shared token
  ([BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)) and
  gets no identity at all.

A continuous-integration (CI) job is neither. It cannot complete a browser sign-in. On a deployment
with OAuth configured, the shared token no longer reaches any endpoint outside worker traffic.

This item gives such a job an identity of its own. The job presents the OpenID Connect (OIDC) token
that GitHub Actions issues to a workflow. It presents the token once, to a dedicated exchange
endpoint. Serve verifies the token there and mints a short-lived machine session in its place. The
repository named in the token's claims decides which org that session acts as. Access is scoped to
what a machine needs, and never to a human role.

## Motivation

BE-0313 replaced serve's login allowlist with GitHub's own organization and Team membership. In the
same change, it narrowed the shared token to worker traffic. That was the right move for people:
an identity that the role gate can check beats a secret that grants everything. But it left machines
with no replacement. `docs/self-hosting.md` records the consequence plainly: a deployment that
scripted non-worker endpoints with the token loses that path once OAuth is on.

The hole is visible in the request gate. `_gate` in `bajutsu/serve/handler.py` authenticates a
request, then splits on whether the request carries an identity.

- An OAuth session has an identity, so `forbidden_for_role` checks it.
- A bearer-token request has none. Its own comment says what follows: it "stays full-access (the
  operator credential)".

Serve therefore knows exactly two shapes, and a CI job fits neither.

| Caller | Credential | Identity | Access |
|---|---|---|---|
| Human | GitHub OAuth session | GitHub login | role-gated: viewer / editor / admin |
| Worker | shared token | none | full, on worker routes |
| CI job | *(none available)* | — | — |

Handing CI the shared token where it still works does not close that hole either. Beyond the OAuth
case, there are two reasons:

- A shared secret cannot say *which* pipeline acted. An audit trail records "the token" and stops
  there.
- The token is identity-less, so it arrives with full access. Every pipeline would hold a credential
  that can rebind an org's configuration or read a config body with embedded secrets.

Concretely, five endpoints that a CI pipeline has reason to call are closed to it.

- `POST /api/artifacts/binary` and `GET /api/artifacts/exists` require admin.
- `POST /api/run` requires editor.

The job-scoped binary artifact override proposal is a sibling of this item. It wants exactly that
sequence: upload a build, then dispatch a run naming it. It can only assume a credential that an
OAuth deployment does not have.

A pipeline may also publish a configuration or scenario tree alongside its build. That needs two more
admin-tier routes, `POST /api/artifacts/config` and `POST /api/artifacts/scenarios`. The allowlist
below therefore covers all three artifact kinds, not the binary alone.

That pipeline is the consumer of what it publishes. It is not a courier that stages bytes for a human
to compose afterwards. It runs a named scenario against the very triple it just uploaded. Unit 3 says
how the triple becomes effective without the org-wide rebind that `POST /api/compose` performs.

**Verifiable outcome.** A GitHub Actions workflow has `permissions: id-token: write` and **no
repository secret**. It dispatches a run against an OAuth-configured deployment, and the run lands in
the org that the workflow's repository belongs to. A workflow in a repository that the org does not
list is refused with 403. The run's audit record names that repository, instead of recording only that
a token was presented.

## Detailed design

The work is mutually exclusive and collectively exhaustive (MECE) across four units:

- Verifying the token.
- Mapping its claims to an org.
- Deciding what a machine may do.
- The tests plus documentation.

### Unit 1 — Exchange the token once for a machine session

The pipeline this item targets calls three endpoints in sequence: `GET /api/artifacts/exists`, then
`POST /api/artifacts/binary`, then `POST /api/run`.

Suppose serve verified an OIDC token on every request and refused a `jti` it had already seen. That
scheme would refuse the sequence at its second call, because the first call already spent the
token's `jti`. A per-request scheme with single-use replay cannot serve this pipeline.

Serve instead verifies the token once, at a dedicated exchange endpoint, `POST /api/oidc/exchange`.
The endpoint mints a short-lived **machine session**, and every later call presents that session
instead. Every check below still runs in full. It runs once, at the exchange, not on every request.

GitHub Actions issues a signed JSON Web Token (JWT) to any workflow job that declares
`permissions: id-token: write`. The job requests the token from `$ACTIONS_ID_TOKEN_REQUEST_URL` with
`$ACTIONS_ID_TOKEN_REQUEST_TOKEN`. Both variables are available once the job declares that
permission. No GitHub App, OAuth app, or identity-provider registration is involved on GitHub's
side.

The job sends the result to the exchange endpoint as a bearer token. Serve verifies the signature
there before it trusts any claim:

- **Issuer.** `iss` must equal the configured issuer. For GitHub-hosted Actions, that is
  `https://token.actions.githubusercontent.com`.
- **Signature.** The signature is RS256 against the issuer's JSON Web Key Set (JWKS).
  - Serve discovers the JWKS from `/.well-known/openid-configuration` and caches it with a
    time-to-live, so an exchange does not fetch keys.
  - The verifier passes an explicit algorithms allowlist of RS256. It refuses a token whose header
    names a different algorithm. That blocks the classic `alg: none` and HS256-confusion forgery.
  - A JWKS entry matches only when its key type is RSA and its advertised algorithm agrees.
  - A missing `kid` triggers a refresh. A refresh-interval floor caps the refresh (or a negative
    cache of missing ids does). A stream of random `kid`s then costs one outbound fetch per
    interval, not one per call. The exposure is now confined to the exchange endpoint alone
    (Unit 3).
  - The fetch carries a timeout, since the gate runs synchronously in the stdlib backend.
  - A key set that cannot be fetched, combined with an expired cache, refuses the exchange. Serve
    does not trust a stale key set. A signature that still fails to verify fails closed too.
- **Audience.** `aud` must equal a value that the **deployment** configures. It is the one check an
  operator must not skip.
  - The workflow chooses its own audience (`core.getIDToken(audience)` takes it as an argument). A
    deployment that accepts any `aud` therefore accepts a token minted for an unrelated service.
  - A deployment with no expected `aud` configured disables the OIDC caller shape entirely. An
    exchange presenting an OIDC token is refused outright. It never falls back to accepting any
    audience. The design fails closed, and does not rely on operator discipline.
- **Lifetime.** Serve checks `exp`, `nbf`, and `iat` with a small skew allowance. The allowance
  matches the 60-second backdating that `bajutsu/common/github/app.py` already applies when it signs
  an App JWT. A token carrying no `iat` is refused outright, because the serve-side age ceiling below
  has nothing to measure from. It is the same fail-closed rule as a missing `jti`.
- **Replay.** GitHub documents no numeric token lifetime, so a captured token is replayable for its
  whole window.
  - Serve spends each token's `jti` (documented as a unique identifier) in a replay cache. The
    token's `exp` bounds the cache entry.
  - Serve also refuses a token past a serve-side age ceiling, independent of `exp`.
  - A token carrying no `jti` is refused outright. Unit 2 applies the same fail-closed rule to a
    missing `environment` claim.
  - The cache lives in the shared system of record, the `Repository` seam. That is the same database
    that the encrypted per-org secret store writes to through its own `SecretStore` seam.
    `docs/self-hosting.md` documents the hosted control plane as multiple replicas sharing that
    store.
  - A per-process cache would fall to a replay against a second replica. The age ceiling would then
    be the only real bound.
  - The cache costs one write per job, since an exchange happens once per job rather than once per
    call.

Verification uses [`joserfc`](https://jose.authlib.org/), a maintained JOSE (JavaScript Object
Signing and Encryption) implementation. The `oauth` extra declares it directly. `authlib` began
depending on `joserfc` only in 1.7.0, so the extra's `authlib>=1.3` floor does not guarantee it.
Today's lock has it only because that lock pins 1.7.2.

The verification itself lives in its own module, imported lazily once OIDC is configured. It covers
the JWKS fetch, its cache, and the checks above. `bajutsu/serve/gate.py` keeps only the
machine-session policy decision (Unit 3). The reason is that `bajutsu/serve/__init__.py` imports
`gate` unconditionally, and `joserfc` ships only with the `oauth` extra. A module-level import in
`gate.py` would break `import bajutsu.serve` on every base install. Unit 4 adds `joserfc` to the
`FORBIDDEN` set of `tests/serve/test_import_guard.py`, which lists `authlib` today but not
`joserfc`.

**The session store gains what this design needs, and refuses what it cannot enforce.**
`SessionStore.issue()` takes no expiry argument today. Time-to-live is store-level
(`BAJUTSU_SESSION_TTL`, seven days), and that cannot express a token's own `exp`.

Unit 1 therefore adds a per-session expiry to the Protocol and to all three implementations:

- `in_memory_session_store.py`
- `server/sessions/sql_session_store.py`
- `server/sessions/redis_session_store.py`

Unit 1 also adds two record columns beside `id` / `identity` / `expires_at`: the resolved **org** and
the **principal kind**. Unit 3 reads both per request.

`InMemorySessionStore` enforces no expiry at all (`return sid in self._sessions`). **The exchange is
refused outright on a store that cannot enforce an expiry.** This is the same fail-closed posture as
an unconfigured `aud`. It narrows the OIDC caller shape to OAuth-configured, database-backed
deployments.

The minted session's lifetime also never exceeds the token's own `exp`. The exchange improves on
reusing the token only if the credential it mints is shorter-lived.

The `sessions` columns above and the `jti` replay cache are both schema changes. They ship with an
Alembic revision under `bajutsu/serve/server/migrations/versions/`, the way every prior addition did.
The replay cache needs the revision too. It reads as a cache, but it is a table like any other.

Without the revision, an existing deployment upgrades to code that selects columns its `sessions`
table lacks. The code also writes rows to a table that does not exist. The exchange then fails on
exactly the database-backed deployments that this design narrows itself to.

**What the exchange buys, and what it does not.** Both the token and the session are bearer
credentials over TLS, so resistance to a captured credential is roughly unchanged. The honest
difference is lifetime, which is why the cap above matters. Four things do change:

- The session can be **revoked**. Unit 3's identity for it makes that possible. Serve cannot revoke
  a GitHub-issued token.
- JWT verification and the JWKS fetch leave the per-request path. They stay reachable
  pre-authentication on one endpoint rather than on every request.
- A single-use `jti` becomes possible at all. A stolen token turns from a silent success into a
  **visible failure**. The attacker's exchange spends the `jti`, so the legitimate job's exchange
  then fails loudly.
- The minted session is worthless to any other service. A token whose `aud` another service might
  also accept lacks that property.

The cost is one more endpoint, a session lifetime to get right, and a second credential in the
pipeline.

After the exchange, a machine request carries no JWT. It takes the same session lookup that every
human request already takes. The session comes from the same `state.auth.issue_session()` that mints
a human's session on the OAuth callback. What OAuth disables is `authz.login()`, the token sign-in.
The exchange takes its place for a machine.

### Unit 2 — Map the claims to an org, on the claims themselves

`OrgConfig` (`bajutsu/serve/orgs.py`) already declares who belongs to an org: `members`,
`githubOrgs`, `githubTeams`, `editorTeams`, and the `targets` it owns.

This item adds `allowedRepositories`. Each entry is an `"<owner>/<repo>"` string. An entry can also
be an object that narrows the entry further (see below).

**The exchange request names the org it wants, always.** The exchange succeeds when that org's
`allowedRepositories` lists the token's repository. It is refused when the list does not. That is
the whole rule.

Naming the org **selects and never grants**. The request says which org to check the claim against,
and the check is what admits it. That keeps the rule of `worker_lease` one level up: the org comes
from a validated source, never from the caller's word.

Requiring the name lets one repository serve several orgs, a real shape. One example is a shared
pipeline repository that tests apps owned by different teams. Inferring the org would have to forbid
that shape, or pick a winner among the orgs that list the repository.

- A human resolves such an ambiguity afterwards, through the header's org selector backed by
  `eligible_orgs`. A person can see where they landed and switch.
- A pipeline sees nothing. The same after-the-fact choice would drop it silently into one of several
  tenants.

Naming the org in the workflow states the target where it is already written down, and leaves one
membership test. The machine session records the org. The org is therefore settled at the exchange,
not re-derived per request.

**`allowedRepositories` keeps `"<owner>/<repo>"` names. This is a consciously accepted trade-off,
not a passing caveat.** `repository` is a *mutable* name, and name recycling is precisely why GitHub
introduced immutable subjects. A deleted, renamed, or transferred repository frees its name. Whoever
claims the name next can mint tokens that match the listed entry.

This item keeps the named form. The operator carries a duty: update or remove an entry the moment its
repository changes. The entry admits the name, not the repository.

**Configuration binds at exchange time only.** Under a per-request design, removing an entry took
effect on the next call. So did tightening an `environment` / `ref` bound. With a one-time exchange,
an outstanding machine session keeps acting as that org, under the old bound, until the session
expires. That is the real blast radius of the name-recycling exposure above.

The operator duty is therefore not only to remove or update the entry. The operator must also revoke
the outstanding machine sessions for it. The identity that Unit 3 gives each session
(`repo:<owner>/<repo>`) makes that possible.

**Match the discrete claims, never a parse of `sub`.** The token carries `repository` and
`repository_owner`, plus `repository_id` and `repository_owner_id`. Authorization compares
`repository` by exact equality. Two facts make reading `sub` the wrong choice:

- **The `sub` format has already changed for new repositories.**
  - A repository created after 15 July 2026 gets an immutable subject that embeds numeric ids
    (`repo:octo-org@123456/octo-repo@456789:ref:refs/heads/main`).
  - GitHub also moves renames and transfers after that date to the immutable format. A listed
    repository can therefore change format with nobody opting in.
  - Older repositories keep the previous shape unless they opt in.
  - Immutable subjects are unavailable on GitHub Enterprise Server.
  - A deployment that parsed `sub` would have to handle every shape, and would end up mis-parsing
    one.
- **`sub` is customizable.**
  - A repository can redefine which claims compose the subject, through an `include_claim_keys`
    array.
  - An organization can only template the subject. A repository already using OIDC stays unaffected
    unless that repository opts in.
  - Authority sits with the repository, not the organization. What `sub` contains is therefore not
    serve's to assume.

Exact equality also rules out a prefix match, which is the classic failure here. `repo:acme/app`
prefix-matches `repo:acme/app-evil`. A substring test would therefore admit a repository that the
operator never listed.

**The repository is not the whole trust boundary.** Anyone who can merge a workflow change to a
listed repository can mint a token from it. `allowedRepositories` alone therefore means "whoever can
write that repository's workflows may dispatch as this org".

A deployment that needs a tighter bound narrows further on claims the token already carries:
`environment` (a GitHub Environment can require reviewers before a job runs), `ref`, or
`job_workflow_ref`. The bound is set **per entry**, not for the org as a whole.

- An `allowedRepositories` entry is either the plain `"<owner>/<repo>"` string, or an object. The
  object carries that same name plus its own `environment` / `ref` / `job_workflow_ref` bound.
- One org can therefore list a repository that runs under a GitHub Environment next to one that does
  not. No second org is needed just to hold the unbounded entry.
- `environment` is a *conditional* claim, emitted only when the job references one. A configured
  bound must refuse a token whose claim is absent, the same as one whose value differs. Otherwise a
  job declaring no environment escapes the narrowing entirely.

One hazard belongs in the documentation rather than the code. A pull request from a fork does not
receive `id-token: write` by default. But a `pull_request_target` workflow runs in the base
repository's context and can receive it. So can a run under a repository setting that sends write
tokens to fork pull requests. A deployment that lists such a repository should narrow by
`environment`. A token can then never be minted from a run that an outside contributor influenced.

### Unit 3 — A machine principal, not a human role

The machine session that the exchange mints is a **third caller shape**, beside the OAuth session and
the shared token. It carries an identity, like a session. It is not a person, so no viewer / editor
/ admin rank describes it. What it may do is an explicit allowlist of endpoints:

| Allowed | Refused |
|---|---|
| `POST /api/artifacts/{config,scenarios,binary}` | `POST /api/config`, `POST /api/compose` (rebinding the org's active configuration — see below for what a pipeline uses instead) |
| `GET /api/artifacts/exists` | `GET /api/config/content` (a config body may embed secrets) |
| `POST /api/run`, and reading runs in its org | `POST /api/apikey`, `POST /api/claudecodetoken` (operator secrets) |
| | `/api/orgs*` (who may sign in and write) |

**Publishing an artifact is half of what a pipeline needs, and `POST /api/compose` is not the other
half.** A real pipeline uploads a config and a scenario tree alongside its build. It then runs a
named scenario against all three. The three upload routes above leave the pipeline holding bytes
with no allowed call to make them effective, because `bind_artifact` binds nothing on its own.

`POST /api/compose` is nevertheless the wrong way to close that gap. It binds the org's **active**
configuration. It also writes the org's remembered configuration through
`remember_org_config_source`. Two problems follow:

- A member's next session inherits whatever the last pipeline composed.
- Concurrent jobs of one repository contend for a single binding. Each job does not run what it
  asked for.

The sibling job-scoped binary override gives both as its reasons for refusing a rebind before every
dispatch. They apply to a triple exactly as they apply to a binary.

The pipeline's triple therefore becomes effective the way that sibling makes a binary effective: as
a **per-job artifact reference named at dispatch**. The run materializes the reference, and the
reference binds nothing.

`materialize_composition` already assembles a triple into a content-addressed tree. It works
independently of `state.bind_upload` and `remember_org_config_source`. A per-job triple therefore
needs no new composition machinery and no binding slot. That is what lets a machine session keep the
sessionless `binding_for` path described below.

This item does not specify that field. It depends on the sibling widening its `binaryArtifact`
override from the binary leg to the triple. The sibling's Alternatives reject that widening. They
rest on the premise that CI holds config and scenarios fixed while varying only the binary. This
item's motivating case does not meet that premise.

`POST /api/oidc/exchange` itself sits outside this allowlist. It is the entry point, so it requires
no prior serve credential, only a verifiable OIDC token. Nobody should read the table above as
gating the way in. The table governs what an already-minted machine session may do, not how a token
becomes one.

Reaching the endpoint at all needs a separate change. The POST arm of `gate.is_open`
(`bajutsu/serve/gate.py`) is exactly `path == _LOGIN_PATH` today. An unauthenticated request to any
other path therefore returns 401 before any verification runs. Unit 3 adds `/api/oidc/exchange` to
that arm. The addition is enabled only once OIDC is configured, so the route stays closed on a
deployment with no expected `aud`. It is the same fail-closed rule as above.

**The identity it carries.** A machine session must carry a non-`None` identity to be revocable at
all. `revoke_identities` revokes by identity string, and its docstring says a session carrying none
is never touched.

Give the session a reserved form that no GitHub login can collide with. A login cannot contain `/`,
so the form is `repo:<owner>/<repo>`. It is not a `users.id` (see the audit entry below).

- The only caller of `revoke_identities` today is org retirement. Unit 3 owns exposing a revocation
  path, so an operator can revoke a repository's outstanding machine sessions without retiring its
  org.
- The granularity is per repository, not per job. Every session that a repository mints shares one
  identity. A revocation therefore ends the repository's concurrent pipelines too. Unit 2's operator
  duty relies on exactly that shared identity.
- Org retirement itself needs widening for the same reason. `delete_org` revokes the roster that
  `list_org_user_ids` returns, which reads the `users` table. A machine has no row there.
- Retiring an org must therefore also revoke the machine sessions bound to it. Otherwise they keep
  acting as the retired tenant until they expire, the exact leak that BE-0375 added that revocation
  to close.

Run reads are **org**-scoped, not per-actor. The read paths take an `org_id` and never filter on
`created_by`. That column is the nullable foreign key to `users.id` that already exists on `runs`
(`bajutsu/serve/server/models/run.py`). A machine reads its org's runs, including ones it did not
start.

`bajutsu/serve/jobs.py` already writes `created_by` only when the actor is a user that exists. Its
own comment notes that a token or CI run has no actor. A machine-dispatched run therefore leaves the
column null for free. A synthetic `users` row would otherwise need to satisfy that second foreign
key. Per-run attribution beyond that would be its own separate change.

The allowlist is what makes this safe to hand to a pipeline. Four of the endpoints it opens are admin
today. Granting a machine the admin *rank* would come with config rebinding and secret reads
attached. Naming endpoints instead keeps the machine's reach to publishing an artifact and starting
a run, which is the whole of what a pipeline needs. Meanwhile `required_role` keeps deciding what a
human needs. This allowlist governs a machine principal and nothing else, so neither gate widens the
other.

The allowlist applies to a machine principal unconditionally, with or without a database wired.
`forbidden_for_role` returns *allowed* when `state.repository is None` ("DB-less = full access",
`bajutsu/serve/authz.py`). An allowlist conditioned on the database the same way would inherit that
fail-open behavior. No machine principal can exist on a database-less deployment in the first place,
because Unit 1's exchange refuses one. The unconditional rule is therefore belt-and-braces, not a
live path.

The shared policy that both backends enforce lives in `bajutsu/serve/gate.py` (BE-0253), not in
`_gate` in `handler.py` alone. That placement keeps the stdlib handler and the FastAPI app from
diverging on security posture. The docstring of `gate.py` calls the prior duplication "a latent path
to a security-relevant divergence with nothing checking for it". `bajutsu/serve/server/app.py`
mirrors `handler.py` line for line.

Unit 3's machine-session allowlist lands in `gate.py` alongside the human role gate. Both backends
then get the machine principal from one place. Unit 1's token verification stays in its own
lazily-imported module (above), and only the exchange endpoint reaches it.

The machine session is authenticated exactly like a human one: a valid session cookie. `gate.is_authorized`
already returns True for any such cookie. The new branch sits beside `forbidden_for_role`, keyed on
the principal kind that the session record now carries (Unit 1).

- `forbidden_for_role` must not run for a machine principal. An unknown user defaults to viewer, and
  viewer refuses `POST /api/run`.
- The endpoint allowlist decides instead.
- The principal kind is what keeps a human session from ever reaching the allowlist.

**The org travels with the machine session, not through `org_of`.** Today every `start_*` endpoint
resolves the org through `state.org_of(actor)`, which reads the actor's *persisted user row*. So do
`bind_artifact` / `artifact_exists` (`bajutsu/serve/operations/upload.py`) and the run-read call
sites in `bajutsu/serve/operations/reads.py`. Together they are every endpoint the allowlist opens.

A machine has no row, so `org_of` would answer `default` for every one of those calls, whatever
`allowedRepositories` says. That is a cross-tenant hole on the very routes the allowlist opens first.
`Repository.get_run(run_id)` compounds it: it calls `session.get(Run, run_id)` with no `org_id`, so
a per-run read built on it carries no tenant boundary of its own.

Every allowlisted operation instead reads the org from the machine session, resolved once at the
exchange (Unit 1).

A machine session also resolves its configuration by calling `binding_for` with no session. It never
takes a per-session binding slot. That is the sessionless path that `binding_for` already documents
for a CI request. The path reads the deployment's fallback and restores nothing.

BE-0393 unit 2 sized the slot map for members (`MAX_SESSION_BINDINGS`, "thousands of concurrent
members"). Its restore is a Git or bundle fetch, paid once per session. One session per job would pay
that fetch per job and would evict members' slots.

Minting a synthetic `users` row to carry the org instead brings its own cost. The row would carry a
role column too, which reopens the viewer-default fork above. The machine would also show up in the
roster that `/api/orgs` discloses.

For the same reason, the audit record does not gain the repository as an *actor*. `actor_id` is a
nullable foreign key to `users.id` (`bajutsu/serve/server/models/audit_log.py`). Minting a synthetic
row to meet that constraint carries the cost described above.

`created_by` differs here. `jobs.py` already guards `created_by` on the actor having a user row.
`_record_audit` instead passes its actor straight through to `actor_id`. Unit 3 changes that seam to
write null for a machine principal, in place of a login with no `users` row behind it.

The entry itself must stay. `_record_audit` returns early on a falsy actor
(`bajutsu/serve/authz.py:408`), so simply passing none would drop the row instead of nulling the
column. The repository goes into the audit entry's own detail payload instead. That answers "which
pipeline started this run" without a fake user row.

### Unit 4 — Tests and documentation

The tests cover the exchange endpoint's verification with no network and no Simulator. They inject
the JWKS and a locally signed token:

- A token signed by the configured key, with the configured `aud` and a listed repository, is
  exchanged for a machine session that acts as that repository's org.
- A token whose `aud` is anything else is refused at the exchange, including one otherwise valid for
  a listed repository.
- A token from an unlisted repository is refused with 403. A token whose `repository` merely shares a
  prefix with a listed entry is refused too.
- A repository listed under two orgs exchanges into whichever of them the request names, so one
  shared pipeline repository can serve both.
- A request naming an org whose `allowedRepositories` does not list the token's repository is
  refused, so naming an org selects and never grants.
- An exchange request naming no org is refused, rather than inferring one.
- A token in the immutable-subject format authorizes on its `repository` claim, so the two `sub`
  shapes behave identically.
- Each of the following is refused, instead of trusting a stale key set:
  - an expired token;
  - a token with a `kid` still absent from the JWKS after the bounded refresh;
  - a token with a broken signature;
  - an unreachable JWKS with an expired cache.
- A token whose header names `none` is refused. So is one naming HS256 signed with the RSA public key
  bytes. The algorithm comes from configuration, never from the token.
- Replay protection holds in four ways:
  - A token exchanged twice is accepted once. Single-use holds across replicas: a second replica
    refuses the same `jti` that the first replica already consumed. A token merely re-presented to
    the same replica is not the only case.
  - A token older than the serve-side maximum age is refused even while its own `exp` is still in
    the future.
  - A token carrying no `jti` is refused outright, rather than skipping the cache.
  - A token carrying no `iat` is refused outright too, rather than exchanging with no origin for the
    age ceiling to measure from.
- Two kinds of deployment refuse an otherwise valid OIDC token at the exchange:
  - A deployment that configures no expected `aud`. It does not accept whichever audience the token
    carries.
  - A deployment whose session store cannot enforce a per-session expiry, `InMemorySessionStore`
    included.

  The minted machine session's lifetime never exceeds the presented token's own `exp`.
- `import bajutsu.serve` pulls in no `joserfc`, the same way it already pulls in none of the other
  server-only dependencies that the import guard's `FORBIDDEN` set lists.
- A machine session reaches every endpoint on the allowlist. It is refused on `POST /api/config`,
  `POST /api/compose`, `GET /api/config/content`, and the operator-secret endpoints.
  - A refused `POST /api/compose` leaves the org's active binding and its remembered configuration
    untouched. A member's next session inherits nothing from a pipeline.
  - That holds the same way with no database wired.
  - `POST /api/oidc/exchange` itself needs no prior session or token to be reached, only a
    verifiable OIDC token.
- Both backends (`handler.py` and `server/app.py`) enforce the machine session identically, since
  both go through `gate.py`.
- A deployment that configures an `environment`, `ref`, or `job_workflow_ref` bound refuses an
  otherwise valid token whose corresponding claim differs. For `environment` alone, the deployment
  also refuses a token whose claim is absent.
- An org listing one repository under an `environment` bound and another with none exchanges both. A
  bound applies to its own entry only, never to the org as a whole.
- A machine's artifact upload (`bind_artifact`) and its exists-probe (`artifact_exists`) resolve the
  org that its repository is listed under, not `default`, on a multi-org deployment. So do a
  machine-dispatched run and a run read.
- The audit record for a machine-dispatched run names the repository in its detail payload. It writes
  `actor_id` as null, not a login that no `users` row backs. That holds for an artifact upload as
  well as a run, since `bind_artifact` audits too.
- Retiring an org revokes its outstanding machine sessions, not only the sessions of the members
  that `list_org_user_ids` returns.

`docs/self-hosting.md` and its Japanese mirror gain a section covering:

- the workflow permission and the two environment variables;
- the `aud` an operator must configure, and that omitting it disables the OIDC caller shape
  entirely;
- the exchange endpoint, the machine session it mints, and that the presented token's own `exp`
  caps the session's time-to-live;
- the `allowedRepositories` shape, and that the exchange request names the org to check against;
- the operator duty to update an entry, and to revoke outstanding machine sessions for it, when its
  repository is renamed, transferred, or deleted, or when a narrowing tightens;
- the optional `environment` / `ref` / `job_workflow_ref` narrowing, including an `environment`
  bound refusing an absent claim the same as a differing one;
- the `pull_request_target` and fork-pull-request write-token hazards;
- that every later call in the pipeline presents the minted session, never the OIDC token itself.

A worked workflow snippet belongs there too. The GitHub-side setup is two lines, and the rest is
serve configuration.

## Alternatives considered

| Alternative | Why we did not take it |
|---|---|
| Verify the OIDC token on every request instead of exchanging it once | The target pipeline calls several endpoints in sequence, so a single-use `jti` would refuse every call after the first. Dropping single-use to allow reuse leaves an age ceiling as the only bound. Per-request verification also keeps the JWKS fetch and JWT parsing reachable pre-authentication on every route, not one. And it cannot revoke a credential that serve did not mint. |
| Issue a long-lived per-org CI token from the settings panel | It is simpler, but it reintroduces the secret this item exists to avoid. Every pipeline would store a token, rotate it by hand, and risk leaking it from a log. The token also cannot say which repository acted. The identity gap is the point, not just the storage. |
| Reuse the shared token for CI, reopening what BE-0313 narrowed | It reverses a deliberate decision. The shared token is identity-less and full-access. Restoring it for non-worker endpoints hands every pipeline the ability to rebind a config and read secrets. |
| Give a CI identity the admin role | Four of the five endpoints CI needs are admin today, so the rank looks like a fit. But it carries config rebinding, `GET /api/config/content`, and the operator-secret endpoints with it. A pipeline needs none of those, and it would then hold all of them. |
| Authorize on the `sub` claim | The format has already changed for new repositories under an immutable-id rollout, and each repository can customize it. Its shape is not serve's to assume. A prefix match on it also admits a repository whose name merely extends a listed one. The discrete claims say the same thing without either problem. |
| Authorize on the immutable numeric claims (`repository_id` / `repository_owner_id`) instead of `repository` | Those claims are immutable and unaffected by subject customization, which closes the name-recycling exposure outright. The cost is a configuration that nobody can read or write from the repository name alone. Adding an entry needs a lookup, and auditing the config gives no hint which repository an entry means. The named form stays for that legibility, with the operator duty above as its price. |
| Use a GitHub App installation token instead of OIDC | Bajutsu already signs App JWTs for the private-repo config source (BE-0224), so the machinery is familiar. But the approach needs an App registered and a private key distributed to the deployment. The resulting token authenticates the App rather than the pipeline. That is a return to a shared secret with a coarser identity. |
| Accept OIDC from any issuer out of the gate | GitLab, Buildkite, and CircleCI all issue OIDC tokens, and per-org issuer configuration would generalize this item. The claim names differ per provider, so a general mapping is a larger design than the case in hand. Scoping to GitHub Actions first leaves the issuer configurable and defers the mapping. |

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Unit 1 — Exchange the token for a machine session at `POST /api/oidc/exchange`. It verifies
      issuer, JWKS with a bounded `kid` refresh and a pinned RS256 algorithm allowlist,
      deployment-configured `aud` (fail closed if unset), and lifetime. A `jti` replay defense shared
      across replicas through the `Repository` seam (refusing a token with no `jti`), a per-session
      expiry on `SessionStore.issue()` capped by the token's own `exp` and refused outright on a store
      that cannot enforce it, an org and principal-kind column on the session record, an Alembic
      revision for those columns and the replay table, `joserfc`
      declared directly in the `oauth` extra, and its verification kept in its own lazily-imported
      module so `gate.py` stays free of it.
- [x] Unit 2 — `allowedRepositories` on `OrgConfig`, checked against the discrete claims for the org
      the exchange request names, with the optional per-entry `environment` / `ref` /
      `job_workflow_ref` narrowing and an `environment` bound refusing an absent claim.
- [x] Unit 3 — The machine session (identity `repo:<owner>/<repo>`, revocable) and its endpoint
      allowlist in `bajutsu/serve/gate.py`, added to `gate.is_open`'s POST arm and enforced
      unconditionally regardless of the database, the verified org carried on the machine session
      rather than read through `org_of` (run reads included), `_record_audit` writing null for a
      machine principal with the repository recorded in the audit entry's detail payload, a
      revocation path for a repository's outstanding sessions, and org retirement widened to revoke
      the machine sessions bound to the retired org.
- [x] Unit 4 — Tests for each seam, including the cross-replica `jti` replay test, the DB-less
      exchange refusal, and the import-guard check for `joserfc`, and the self-hosting documentation.

Log:

- [#1986](https://github.com/bajutsu-e2e/bajutsu/pull/1986) — Units 1 and 2. Added
  `POST /api/oidc/exchange` and the verification behind it (`bajutsu/serve/oidc.py`): JWKS
  discovery with a bounded-refresh cache, a pinned RS256 allowlist, issuer / audience / lifetime
  with a 60s skew, a serve-side age ceiling, mandatory `iat` and `jti`, and a single-use `jti`
  spent through the `Repository` seam so replay is refused across replicas. `SessionStore` gained
  a per-session expiry, an org and a principal kind across all three implementations, plus a
  `Principal` read narrowed in one place. Added `allowedRepositories` on `OrgConfig` with
  exact-equality matching on the discrete claims and optional per-entry `environment` / `ref` /
  `workflowRef` bounds. Alembic revision 0020 carries the two session columns, the org column and
  the `oidc_jti` table.
- [#2004](https://github.com/bajutsu-e2e/bajutsu/pull/2004) — Units 3 and 4. Replaced
  `gate.forbidden_for_machine`'s refuse-everything stub with a default-deny endpoint allowlist —
  the exists-probe, the three artifact uploads, `POST /api/run`, and `GET /api/runs` plus
  `GET /api/jobs/{id}` to watch the result — refusing a machine principal that carries no org, and
  enforced with or without a database. The verified org travels on the machine session and reaches
  every allowlisted operation through `ServeState.org_for`, rather than `org_of`, which reads a
  persisted user row a pipeline has none of and would resolve `default` for every tenant. A machine
  resolves its configuration through the sessionless `binding_for` path, so a CI job takes no
  member binding slot. `_record_audit` keeps a machine's entry with a null `actor_id` and records
  the repository in the detail payload; `artifact_exists` gained the audit line it lacked. Added
  `SessionStore.revoke_machine_sessions` across all three implementations, org-scoped because one
  repository may be listed by several orgs, with two callers: a new admin
  `POST /api/orgs/{slug}/machine-sessions/revoke` and org retirement, which `revoke_identities`
  could not reach. The exchange is refused when no shared token is configured, since both backends
  skip the request gate entirely then. `machine_identity` folds case, matching the roster's own
  case-insensitive comparison, so a revocation cannot silently match nothing. `job_view`'s org
  check applies to a machine principal rather than to every caller: the job routes are uniformly
  unscoped for people today, and scoping one here would refuse a member their own in-flight run
  after an org switch. Documented in `docs/self-hosting.md` and its Japanese mirror. The per-job
  artifact triple this unit's prose describes still awaits the sibling job-scoped binary override
  proposal; the allowlist stands without it.

  Three deviations from this item's text, each because the literal reading does not work.
  `allowedRepositories` needed a database column as well as the config field, since a DB-backed
  deployment reads its org model from `orgs_from_db` and the exchange requires a database — a
  config-only field would be empty on exactly the deployments that can use it. The route was added
  to `gate.is_open` here rather than in unit 3, or unit 1 would be unreachable and untestable.
  And instead of refusing the exchange on a store that cannot enforce a per-session expiry,
  `InMemorySessionStore` gained one; the database requirement reaches the same narrowing with less
  code. The per-entry bound is spelled `workflowRef` rather than `job_workflow_ref`, matching the
  camelCase of the other config keys and staying provider-neutral.

  Verification is provider-independent: `OidcProvider` is a table of claim names, so a second CI
  platform is one entry rather than a redesign. Units 3 and 4 remain, so a minted machine session
  is refused on every endpoint for now — `gate.forbidden_for_machine` is the single seam unit 3
  replaces, and the deny-all is what keeps a machine session out of the role gate's viewer default
  in the meantime.

## References

- [BE-0313 — GitHub org membership and Team-based RBAC for serve](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)
  — the item that gave humans an identity and narrowed the shared token to worker traffic, leaving
  the machine gap this item fills.
- [BE-0051 — Serve hardening for hosting (auth, input validation)](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)
  — the shared token and the request gate this item adds a third caller shape to.
- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)
  — the multi-tenancy and role model the machine allowlist sits beside.
- [BE-0224 — GitHub private-repo config authentication](../BE-0224-github-private-repo-config-auth/BE-0224-github-private-repo-config-auth.md)
  — a separate config-source concern that also signs a JWT (App JWTs, with `cryptography`), unrelated
  to this item's own `joserfc` dependency in the `oauth` extra.
- [BE-0160 — Credential-free worker uploads via presigned URLs](../BE-0160-worker-credential-free-uploads/BE-0160-worker-credential-free-uploads.md)
  — the same "hold no long-lived credential" posture, applied to the worker.
- [BE-0268 — Composable upload artifacts](../BE-0268-composable-upload-artifacts/BE-0268-composable-upload-artifacts.md)
  — the triple and `materialize_composition`, which assembles one independently of any binding.
- The job-scoped binary artifact override proposal — the sibling this item's allowlist depends on
  for making a pipeline's uploaded triple effective, and whose Alternatives currently reject
  widening that override past the binary leg on a premise this item's motivating case does not meet.
- [OpenID Connect reference — GitHub Docs](https://docs.github.com/en/actions/reference/security/oidc)
  — the issuer, the claim set, the immutable-subject rollout, and `include_claim_keys`.
- [OpenID Connect — GitHub Docs](https://docs.github.com/en/actions/concepts/security/openid-connect)
  — what `permissions: id-token: write` grants and how a job requests a token.
- [Using OpenID Connect in cloud providers — GitHub Docs](https://docs.github.com/en/actions/how-tos/secure-your-work/security-harden-deployments/oidc-in-cloud-providers)
  — the request environment variables, and why the trust conditions live on the verifying side.

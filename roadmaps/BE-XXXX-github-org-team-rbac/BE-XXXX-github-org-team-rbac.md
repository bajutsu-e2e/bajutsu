**English** · [日本語](BE-XXXX-github-org-team-rbac-ja.md)

# BE-XXXX — GitHub org membership and Team-based RBAC for serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-github-org-team-rbac.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Hosting the web UI (cloud / self-hosted) |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) |
<!-- /BE-METADATA -->

## Introduction

This item replaces two of serve's role-based access control (RBAC) mechanisms — the GitHub-login
allowlist that gates OAuth sign-in and the login lists that assign the admin/editor/viewer roles
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) §7b–7c) — with GitHub's
own organization and Team membership. Signing in requires membership in a configured GitHub
organization, which grants the viewer role; membership in one flat GitHub Team promotes a user to
editor; and membership in one separate, server-wide Team grants admin. It also narrows the shared
token (`BAJUTSU_SERVE_TOKEN`,
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)) to
worker-to-server traffic once OAuth is configured, retiring every other use of it as an
authorization path — both the human cookie sign-in and a direct `Authorization: Bearer` call to
any other endpoint.

## Motivation

Serve's current RBAC (BE-0015 §7c) reads three GitHub-login lists from environment variables —
`BAJUTSU_OAUTH_ALLOWED_USERS` gates sign-in itself, `BAJUTSU_OAUTH_ADMINS` and
`BAJUTSU_OAUTH_VIEWERS` assign the admin and viewer roles, and everyone else on the allowlist
defaults to editor. Each list is a login roster that a deploy operator maintains by hand, duplicating
a roster GitHub already maintains: the organization's member list and its Teams. Every hire, leave,
or transfer needs an environment-variable edit and a redeploy before the corresponding server-side
list matches reality again.

Bajutsu's target deployment already fits a GitHub-shaped structure — an engineering organization
that authenticates on GitHub, with a subset of members who write and maintain scenarios. Deriving
serve's roles from that structure removes the duplicate roster: read access follows organization
membership, and write access follows membership in a Team the organization already uses to group
its scenario maintainers. BE-0015 §7c-2 already recomputes a user's role from policy on every login
so that a policy change takes effect without a data migration; this item keeps that principle and
only changes where the policy is read from — GitHub's own membership records, not a static
environment-variable list.

Retiring the shared token as a sign-in path closes a second, unrelated gap — but the gap is wider than
a browser sign-in. Today, on any deployment with both OAuth and the token configured, a browser can
sign in with the shared token through `POST /api/login`, bypassing OAuth and the RBAC built on top of
it. Independently of that, any client can present the same token directly as an `Authorization: Bearer`
header on any endpoint, which `gate.is_authorized` honors with no identity to check a role for — the
"operator credential, full access" path BE-0015 §7b documented for the token. Once GitHub org and Team
membership decide who can act as what, both paths need closing, not only the browser one, or role
enforcement keeps a hole exactly the width of the token — just moved from the login form to any
authenticated HTTP client.

## Detailed design

### Terminology: GitHub organization, Bajutsu org, and project

This item's name for its GitHub-side identity source, "GitHub organization," and Bajutsu's own
multi-tenancy unit, "Bajutsu org" (an `orgs:` entry, already called "org (tenant)" in the
codebase), share the word "org" but name different things, so the rest of this design states
which one it means at each use. A third, finer-grained unit sits below the Bajutsu org: a
**project** ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)) is one
config-source binding — typically one repository — registered under a Bajutsu org, and one
Bajutsu org can hold many projects (`ProjectRecord.org_id`, one-to-many). A project carries no
role-based access control (RBAC) of its own today: every request resolves a role from the actor's
Bajutsu org alone, and only one project is active per Bajutsu org at a time, with switching which
one is active gated at admin (`activate_project`,
[`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py)).

This item attaches `editorTeam` (below) to the Bajutsu org, not to the project: a deployment
where every project under one Bajutsu org shares the same set of maintainers fits this directly.
A deployment that instead wants a different write roster per repository — a mobile team pushing
to one repository, a web team to another, both under the same GitHub organization — models that
today by declaring **multiple Bajutsu orgs**, each matching that GitHub organization (or an
overlapping `githubOrgs` list) but naming a different `editorTeam`, rather than by adding
project-level access control; see *Alternatives considered*.

### Read access follows GitHub organization membership

The `orgs:` block already maps a login onto a Bajutsu org, through an explicit `members` listing or a
matching entry in a `githubOrgs` list intersected against the GitHub organizations the login belongs
to (`org_for_identity`, [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)). Today that mapping
only chooses *which* org an already-allowlisted login belongs to; a login matching no org still
falls back to the single `default` org rather than being turned away, because the separate
`BAJUTSU_OAUTH_ALLOWED_USERS` allowlist is what currently decides whether sign-in succeeds at all.

This item folds that decision into the same mapping: sign-in succeeds only when the login is an
explicit `members` entry or a member of a `githubOrgs`-listed GitHub organization, for the deployment's
configured `orgs:` block; every other login is rejected with the existing "user not allowed" response
that `BAJUTSU_OAUTH_ALLOWED_USERS` produces today. A successful sign-in is granted the viewer role at
minimum. `BAJUTSU_OAUTH_ALLOWED_USERS` and `BAJUTSU_OAUTH_VIEWERS` are retired — the organization's own
roster, not a separate list, is now the allowlist. A `members`-only tenant (an `orgs:` block with
`members` but no `githubOrgs`) now gates sign-in on that `members` listing for the first time — today,
`members` only decides which org an already-allowed login lands in, while `BAJUTSU_OAUTH_ALLOWED_USERS`
alone decides whether sign-in succeeds at all. A deployment with no `orgs:` block at all has no
`members` listing to fall back to, so retiring `BAJUTSU_OAUTH_ALLOWED_USERS` there rejects every login;
adopting this item requires such a deployment to declare an `orgs:` block (a `members` listing or a
`githubOrgs` entry), or it loses sign-in entirely.

This makes sign-in itself depend on a live call to GitHub's `/user/orgs` for any login that isn't an
explicit `members` entry (`_fetch_orgs`, [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py))
— a dependency that doesn't exist today, since `BAJUTSU_OAUTH_ALLOWED_USERS` alone decides sign-in and
needs no GitHub API call to succeed. `_fetch_orgs` already fails open to an empty org list on a
non-200 response or an unparseable body, which today only sends the login to the `default` org; under
this item, the same failure makes a `githubOrgs`-only login fail the sign-in gate outright, for as long
as the outage lasts. (A genuine network failure is a separate, pre-existing case: it propagates out of
`_fetch_orgs` uncaught, through `fetch_identity`, to `oauth_callback`'s own exception handler, which
already fails the whole exchange with a 502 today, for every login — unaffected by this item either
way.) This item accepts that new non-200/parse-error trade-off rather than adding retry or caching
logic to the org-membership fetch: an explicit `members` entry is
unaffected either way — `fetch_identity` still calls `/user/orgs` for every login, but
`org_for_identity`'s explicit `members` match returns before ever consulting the fetched list — and a
`githubOrgs`-only login can simply sign in again once GitHub's API is reachable.

### Write access follows membership in one flat GitHub Team

`OrgConfig` ([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)) gains one new field per org,
`editorTeam`, naming a single GitHub Team as `"<github-org>/<team-slug>"`:

```yaml
orgs:
  acme:
    githubOrgs: [acme-gh]
    editorTeam: acme-gh/scenario-maintainers
```

At login, once a login has cleared the organization-membership check above, serve calls GitHub's
`GET /user/teams` (already covered by the `read:org` scope the OAuth flow already requests) and checks
whether the response lists the configured org/Team pair as a **direct** membership. A match promotes
the login to editor within the org it resolved into; no match leaves it at viewer. `/user/teams` lists
child ("nested") Teams as memberships distinct from their parent, so checking only for the exact
configured Team, and not a nested Team beneath it, keeps this check flat by construction, matching the
decision to leave nested-Team resolution out of this item's first cut.

The lookup follows the same `Link`-header pagination `_fetch_orgs` already uses for org membership
([`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)), so a login whose target Team
sits on a later page of a large Team list still resolves correctly. An unsuccessful call — a non-200
response, a network failure, or an unparseable body — is treated the same as a genuine non-match: the
login resolves to viewer, not to a sign-in failure. This is the opposite failure direction from
`_fetch_orgs`'s org-membership fetch, which fails open to the `default` org because a lookup failure
there only affects *which* org a login lands in; here a lookup failure could otherwise grant write
access, so it fails closed to the lower-privilege outcome instead.

### Admin stays one server-wide tier

`_ADMIN_PATHS` and the admin-gated `GET` endpoints
([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) are enforced instance-wide today — `role_for`,
`user_role`, and `forbidden_for_role` take no org argument, so one user's admin role reaches every
org's config, secrets, and provider settings, not only the org that user belongs to. Giving each org
its own admin Team would silently hand that org's admin instance-wide power over every other org,
because nothing downstream of the role check narrows it back to one org. This item does not change
that enforcement scope, so admin stays a single, server-wide tier: one Team, named once for the whole
deployment as `BAJUTSU_OAUTH_ADMIN_TEAM` (the same `"<github-org>/<team-slug>"` form, checked the same
way as `editorTeam`), replaces the `BAJUTSU_OAUTH_ADMINS` login list. Scoping admin itself to one org —
and introducing a separate, higher tier for cross-org operations above that — is a materially larger
change, tracked as a possible future item rather than folded into this one (see *Alternatives
considered*).

### Revocation reflects on the next login, unchanged

BE-0015 §7c-2 already recomputes a user's role from policy on every login, so a policy change needs no
data migration. This item keeps that principle exactly: leaving a GitHub organization or Team takes
effect the next time that user logs in, the same way removal from `BAJUTSU_OAUTH_ADMINS` does today. No
new revocation mechanism is needed.

### The admin-gated `GET` exceptions are unaffected

`required_role()` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already requires admin for
three reads that disclose more than their path implies — `GET /api/config/content`, `GET
/api/artifacts/exists`, and `GET /api/version/checkout` — regardless of how a viewer or editor role was
granted. Broadening who qualifies as a viewer does not touch these entries: they check the resolved
role against a fixed "admin" requirement, not the organization or Team the role came from.

### The shared token narrows to worker traffic once OAuth is configured

Three independent things currently answer to `BAJUTSU_SERVE_TOKEN`, not two. A worker authenticates to
the five `/api/worker/*` routes (`lease`, `heartbeat`, `result`, `artifact-urls`, `scenario-url` —
[`bajutsu/serve/routes.py`](../../bajutsu/serve/routes.py)) with it as a bearer token (unrelated to any
human, and unaffected by this item either way). A human browser can also exchange it for a session
cookie through `POST /api/login` (`login()` in
[`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)), bypassing GitHub OAuth and the RBAC built on
top of it. And any client — human or automated — can present the token directly as an
`Authorization: Bearer` header on **any** endpoint: `gate.is_authorized`
([`bajutsu/serve/gate.py`](../../bajutsu/serve/gate.py)) accepts it there, on either of serve's two HTTP
backends — the stdlib handler's `_gate()` ([`bajutsu/serve/handler.py`](../../bajutsu/serve/handler.py))
and the FastAPI app's `_security_gate` ([`bajutsu/serve/server/app.py`](../../bajutsu/serve/server/app.py))
both call it and both skip `forbidden_for_role` entirely for that request, because a Bearer-authenticated
request carries no identity to check a role for — the same "operator credential, full access, e.g. for
CI" path BE-0015 §7b documented when OAuth was introduced. This third path is the widest of the three:
it reaches every RBAC-gated endpoint, not only the login form, so retiring only the cookie exchange
would leave the larger bypass untouched.

This item retires both the cookie exchange and the direct Bearer path, and only once OAuth is
configured (all three `BAJUTSU_OAUTH_GITHUB_CLIENT_ID`/`_CLIENT_SECRET`/`_REDIRECT_URI` environment
variables are set): `gate.is_authorized`'s Bearer-token branch is honored only for the `/api/worker/*`
routes, on both backends, and a human signs in exclusively through `/api/oauth/login`. A deployment
that today relies on
the token's "operator credential" reach for scripted or CI access to non-worker endpoints loses that
access once OAuth is configured; this item designs no replacement machine-credential mechanism for
that use case — a service-account or personal-access-token-style identity distinct from both the
worker token and a human OAuth session is a materially larger addition, left to a future proposal. When
OAuth is not configured — the single-Mac, private-network deployment BE-0051 designed the shared token
for — every one of the token's three uses is unchanged, because RBAC does not apply there regardless
(no database is wired, and `forbidden_for_role` returns `False` whenever `state.repository is None`);
narrowing any of that deployment's sign-in or automation paths would leave it with less than it started
with.

## Alternatives considered

- **An identity-aware proxy (IAP) in front of serve.** Rejected: Bajutsu already has its own GitHub
  OAuth mechanism (BE-0015), and the deployment's users already authenticate on GitHub, so an IAP would
  add a second, redundant identity system rather than reuse the one already in place.
- **Per-org admin Teams, with a new, higher tier above them for cross-org operations.** Considered as
  the org-scoped counterpart to the per-org editor Team above. Rejected for this item: `_ADMIN_PATHS`
  and the admin-gated reads are enforced instance-wide today, so scoping admin to one org would first
  require making that enforcement org-aware — a materially larger change, orthogonal to swapping the
  role *source* from login lists to GitHub membership, that this item leaves for a future proposal.
- **Attaching `editorTeam` to the project (BE-0225) instead of the Bajutsu org.** Considered for a
  deployment where different repositories under one GitHub organization need different write
  rosters. Rejected for this item: a project carries no access control of its own today (no field
  on `ProjectRecord`, no project-aware branch in `required_role`), so this would add a new
  access-control axis to a concept BE-0225 defined purely as a config-source binding — a
  materially larger change than swapping the role *source* for the Bajutsu org this item already
  has. A deployment that needs different write rosters per repository can already get there by
  declaring multiple Bajutsu orgs, each naming a different `editorTeam`, rather than by adding
  project-level access control.
- **Nested-Team membership for the editor and admin Teams.** GitHub Teams support parent/child
  hierarchies, and `/user/teams` reports a login's membership in a child Team distinct from its parent.
  Rejected for this item's first cut in favor of checking only the exact configured Team: it matches the
  flat structure the deployment already decided on, and nested resolution can follow later if a
  deployment's Team hierarchy needs it.
- **Retiring the shared token as a human sign-in path unconditionally.** Rejected: the single-Mac,
  private-network deployment BE-0051 designed the token for has no OAuth app to register and no RBAC to
  enforce (no database is wired). Retiring the token there would remove the only sign-in path that
  deployment has, with nothing to replace it.
- **Keeping the login-list allowlists alongside the new organization/Team checks, as an escape hatch.**
  Rejected: a second, independent grant path defeats the purpose of deriving roles from GitHub's own
  membership records — it recreates the roster-drift problem this item exists to remove, just for the
  users routed through the escape hatch instead of all of them.
- **Leaving the token's direct `Authorization: Bearer` path untouched on non-worker endpoints once
  OAuth is configured.** Rejected: this item's whole premise is that GitHub org and Team membership
  decide who can act as what once OAuth is configured; a client that can still reach every endpoint by
  presenting the raw token, with no role check at all, would leave that premise false regardless of how
  carefully `editorTeam`/`BAJUTSU_OAUTH_ADMIN_TEAM` are configured.
- **Shortening the revocation window with a session time-to-live (TTL).** Considered so a Team removal takes effect
  before the affected user's next login, rather than only on it. Deferred: session expiry is a property
  of the session store (BE-0015), independent of where a role's source data comes from, and today's
  login-list-based revocation already waits for the next login on the same terms this item keeps. A
  future item could shorten that window for every revocation path at once, rather than singling this
  one out.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Extend `OrgConfig` with the `editorTeam` field and its config-schema documentation.
- [ ] Add the `GET /user/teams` lookup (direct membership only) and the `BAJUTSU_OAUTH_ADMIN_TEAM`
      environment variable.
- [ ] Fold the organization-membership sign-in gate into `org_for_identity`/`_org_for_login`; retire
      `BAJUTSU_OAUTH_ALLOWED_USERS` and `BAJUTSU_OAUTH_VIEWERS`.
- [ ] Replace `role_for()`'s login-list resolution with the organization/Team resolution above; retire
      `BAJUTSU_OAUTH_ADMINS`.
- [ ] Gate the token-backed `POST /api/login` cookie path on "OAuth is not configured" for this
      deployment.
- [ ] Scope `gate.is_authorized`'s Bearer-token branch to the `/api/worker/*` routes once OAuth is
      configured (`bajutsu/serve/gate.py`), and keep both call sites — the stdlib handler
      (`bajutsu/serve/handler.py`) and the FastAPI app (`bajutsu/serve/server/app.py`) — in lockstep,
      closing the direct-Bearer RBAC bypass on every other endpoint alongside the cookie-login
      retirement.
- [ ] Update `docs/architecture.md` and the BE-0015/BE-0016 cross-references (both languages) to
      describe the organization/Team-based RBAC in place of the login lists.
- [ ] Tests: the organization-membership sign-in gate (allow and reject), editor/admin Team resolution
      against a faked Teams API — including a paginated Team list and a failed lookup resolving to
      viewer — the admin-gated `GET` exceptions staying admin-only, the token-login gate switching on
      whether OAuth is configured, and the direct Bearer-token path being rejected on non-worker
      endpoints on both HTTP backends (while still working for all five `/api/worker/*` routes and for
      an OAuth-less deployment).

## References

- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md),
  §7b–7c and §8 — the GitHub OAuth sign-in, the login-list RBAC this item replaces, and the `orgs:`
  multi-tenancy model this item extends.
- [BE-0225 — Config project hub in serve (register, list, switch, run)](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) —
  the project concept this item's *Terminology* subsection distinguishes from the GitHub
  organization and the Bajutsu org.
- [BE-0016 — Self-hosting of the web UI](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md) —
  the self-hosted deployment shape whose OAuth-less path this item leaves unchanged.
- [BE-0051 — Serve hardening for hosting](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md) —
  the shared token this item narrows to worker traffic once OAuth is configured.
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py) — the GitHub OAuth client
  (`Identity`, org-membership fetch) this item extends to also fetch Team membership.
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — the RBAC role resolution and admin-gated
  `GET` exceptions this item changes the role source for.
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) — the `orgs:` config model this item extends
  with `editorTeam`.
- [`bajutsu/serve/gate.py`](../../bajutsu/serve/gate.py) — `is_authorized`'s Bearer-token branch, which
  this item scopes to the `/api/worker/*` routes once OAuth is configured.
- [`bajutsu/serve/handler.py`](../../bajutsu/serve/handler.py) — `_gate()`, one of the two call sites
  that skip `forbidden_for_role` for a Bearer-authenticated request today (no identity to check a role
  for).
- [`bajutsu/serve/server/app.py`](../../bajutsu/serve/server/app.py) — `_security_gate`, the FastAPI
  backend's `is_authorized` call site, which this item must scope the same way as the stdlib handler's
  so the two backends `gate.py` exists to keep in lockstep don't diverge.
- GitHub REST API — [List teams for the authenticated user](https://docs.github.com/en/rest/teams/teams#list-teams-for-the-authenticated-user)
  (`GET /user/teams`).

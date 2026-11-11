**English** · [日本語](BE-0313-github-org-team-rbac-ja.md)

# BE-0313 — GitHub org membership and Team-based RBAC for serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0313](BE-0313-github-org-team-rbac.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0313") |
| Implementing PR | [#1325](https://github.com/bajutsu-e2e/bajutsu/pull/1325) |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0016](../BE-0016-web-ui-self-hosting/BE-0016-web-ui-self-hosting.md), [BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) |
<!-- /BE-METADATA -->

## Introduction

This item replaces two role-based access control (RBAC) mechanisms of serve with the organization and
Team membership of GitHub itself. The two mechanisms are the GitHub-login allowlist that gates OAuth
sign-in and the login lists that assign the admin, editor, and viewer roles
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) §7b–7c).

The new rules are:

- Signing in requires membership in a configured GitHub organization. The membership grants the
  viewer role.
- Membership in one flat GitHub Team promotes a user to editor.
- Membership in one separate, server-wide Team grants admin.

The item also narrows the shared token (`BAJUTSU_SERVE_TOKEN`,
[BE-0051](../BE-0051-serve-hardening-for-hosting/BE-0051-serve-hardening-for-hosting.md)) to
worker-to-server traffic once OAuth is configured. The item retires every other use of the token as
an authorization path: both the human cookie sign-in and a direct `Authorization: Bearer` call to any
other endpoint.

## Motivation

The current RBAC of serve (BE-0015 §7c) reads three GitHub-login lists from environment variables:

- `BAJUTSU_OAUTH_ALLOWED_USERS` gates sign-in itself.
- `BAJUTSU_OAUTH_ADMINS` and `BAJUTSU_OAUTH_VIEWERS` assign the admin and viewer roles.
- Everyone else on the allowlist defaults to editor.

Each list is a login roster that a deploy operator maintains by hand. GitHub already maintains the
same roster as the member list and the Teams of the organization. Every hire, leave, or transfer
needs an environment-variable edit and a redeploy before the server-side list matches reality again.

The target deployment of Bajutsu already fits a GitHub-shaped structure: an engineering organization
that authenticates on GitHub, with a subset of members who write and maintain scenarios. Deriving the
roles of serve from that structure removes the duplicate roster. Read access follows organization
membership. Write access follows membership in a Team that the organization already uses to group
its scenario maintainers.

BE-0015 §7c-2 already recomputes the role of a user from policy on every login, so a policy change
takes effect without a data migration. This item keeps that principle. The item changes where the
policy is read from: GitHub's own membership records replace a static environment-variable list.

Retiring the shared token as a sign-in path closes a second, unrelated gap. The gap is wider than a
browser sign-in. Today, on any deployment with both OAuth and the token configured, a browser can
sign in with the shared token through `POST /api/login`. That sign-in bypasses OAuth and the RBAC
built on top of it.

Independently, any client can present the same token directly as an `Authorization: Bearer` header
on any endpoint. `gate.is_authorized` honors the header, with no identity to check a role for. This
is the "operator credential, full access" path that BE-0015 §7b documented for the token.

Once GitHub organization and Team membership decide who can act as what, both paths need closing.
If only the browser path closed, role enforcement would keep a hole exactly the width of the token,
moved from the login form to any authenticated HTTP client.

## Detailed design

### Terminology: GitHub organization, Bajutsu org, and project

This item uses "GitHub organization" for its GitHub-side identity source. Bajutsu's own multi-tenancy
unit is the "Bajutsu org": an `orgs:` entry, already called "org (tenant)" in the codebase. The two
terms share the word "org" but name different things. The rest of this design states which one it
means at each use.

A third, finer-grained unit sits below the Bajutsu org. A **project**
([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)) is one config-source
binding, typically one repository, registered under a Bajutsu org. One Bajutsu org can hold many
projects (`ProjectRecord.org_id`, one-to-many).

A project carries no RBAC of its own today. Every request resolves a role from the Bajutsu org of the
actor alone. Only one project is active per Bajutsu org at a time. Switching which project is active
is gated at admin (`activate_project`,
[`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py)).

This item attaches `editorTeam` (below) to the Bajutsu org, not to the project. A deployment where
every project under one Bajutsu org shares the same set of maintainers fits this directly.

A deployment may want a different write roster per repository. For example, a mobile team pushes to
one repository and a web team to another, both under the same GitHub organization. Today that
deployment declares **multiple Bajutsu orgs**. Each Bajutsu org matches the GitHub organization (or an
overlapping `githubOrgs` list) and names a different `editorTeam`. The deployment does not add
project-level access control. See *Alternatives considered*.

### Read access follows GitHub organization membership

The `orgs:` block already maps a login onto a Bajutsu org. The mapping uses an explicit `members`
listing, or a matching entry in a `githubOrgs` list intersected against the GitHub organizations that
the login belongs to (`org_for_identity`, [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)).

Today the mapping chooses only *which* org an already-allowlisted login belongs to. A login that
matches no org falls back to the single `default` org and is not turned away. The separate
`BAJUTSU_OAUTH_ALLOWED_USERS` allowlist currently decides whether sign-in succeeds at all.

This item folds that decision into the same mapping. For the configured `orgs:` block of the
deployment, sign-in succeeds when the login is an explicit `members` entry or a member of a
`githubOrgs`-listed GitHub organization. Every other login is rejected with the existing "user not
allowed" response that `BAJUTSU_OAUTH_ALLOWED_USERS` produces today.

A successful sign-in is granted the viewer role at minimum. The item retires
`BAJUTSU_OAUTH_ALLOWED_USERS` and `BAJUTSU_OAUTH_VIEWERS`. The roster of the organization replaces
the separate list as the allowlist.

"At minimum" is a real downgrade for every current editor, not just a rewording. Today `role_for`
([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) defaults an allowlisted login to editor,
unless the login is explicitly in `BAJUTSU_OAUTH_VIEWERS`. Every scenario author is an editor by
default now.

This item flips that base role to viewer. Users who are implicit editors today lose write access on
their next login, unless a deployment adds them to `editorTeam` first. The step is the same kind of
adoption step as the `orgs:`-block requirement below.

`org_for_identity` cannot signal the rejection on its own. It returns a plain `str`. A login that
legitimately resolves to the shared `default` org and a login that matches nothing both fall through
to the same `DEFAULT_ORG` sentinel ([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)).

The gate therefore needs its own match check. The check applies the same `members`/`githubOrgs` test
that `org_for_identity` already performs, exposed as a plain yes/no. The gate runs the check *before*
it calls `org_for_identity` for that login:

- A login that the gate accepts always matches something, so `org_for_identity` never falls through
  to `default` for it.
- A login that the gate rejects never reaches `org_for_identity` at all.

One consequence follows directly. For any deployment with an `orgs:` block, the `default` org becomes
unreachable through OAuth sign-in. Today `default` is the catch-all landing spot for an unmatched
login. A login that would have landed there is now rejected before placement is ever computed.

A `members`-only tenant is an `orgs:` block with `members` but no `githubOrgs`. Such a tenant now
gates sign-in on its `members` listing for the first time. Today `members` decides only which org an
already-allowed login lands in, and `BAJUTSU_OAUTH_ALLOWED_USERS` alone decides whether sign-in
succeeds.

A deployment with no `orgs:` block at all has no `members` listing to fall back to. Retiring
`BAJUTSU_OAUTH_ALLOWED_USERS` there rejects every login. Adopting this item requires such a
deployment to declare an `orgs:` block (a `members` listing or a `githubOrgs` entry). Otherwise the
deployment loses sign-in entirely.

The gate must run unconditionally. It must not run only inside the `if state.repository is not None:`
block that `_org_for_login` currently lives in
([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)). Today the
`BAJUTSU_OAUTH_ALLOWED_USERS` rejection sits at the top of `oauth_callback`, before that block. The
rejection therefore still gates sign-in on a deployment with OAuth configured but no database wired.
That deployment mints a session for the identity at the end of `oauth_callback`, regardless of
`state.repository`.

Folding the new org/Team gate into `_org_for_login` as-is would remove the only gate that the
deployment has once `BAJUTSU_OAUTH_ALLOWED_USERS` retires. Every GitHub user would be admitted. The
rejection stays at the same top-level position instead. The code computes the match check and decides
accept or reject before the database block. The call to `_org_for_login` inside that block decides
only *which* org to persist for an already-accepted login, and never whether to reject.

Sign-in itself now depends on a live call to GitHub's `/user/orgs` for any login that is not an
explicit `members` entry (`_fetch_orgs`,
[`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)). That dependency does not
exist today, because `BAJUTSU_OAUTH_ALLOWED_USERS` alone decides sign-in and needs no GitHub API call
to succeed.

`_fetch_orgs` already fails open to an empty org list on a non-200 response or an unparseable body.
Today the failure sends the login to the `default` org. Under this item, the same failure makes a
`githubOrgs`-only login fail the sign-in gate outright, for as long as the outage lasts.

A genuine network failure is a separate, pre-existing case. The failure propagates out of
`_fetch_orgs` uncaught, through `fetch_identity`, to the own exception handler of `oauth_callback`.
That handler already fails the whole exchange with a 502 today, for every login. This item leaves
the case unaffected either way.

This item accepts the new non-200 and parse-error trade-off. It adds no retry or caching logic to the
org-membership fetch, for two reasons:

- An explicit `members` entry is unaffected. `fetch_identity` still calls `/user/orgs` for every
  login, but the explicit `members` match in `org_for_identity` returns before the fetched list is
  consulted.
- A `githubOrgs`-only login can sign in again once the GitHub API is reachable.

### Write access follows membership in one flat GitHub Team

`OrgConfig` ([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)) gains one new field per org,
`editorTeam`. The field names a single GitHub Team as `"<github-org>/<team-slug>"`:

```yaml
orgs:
  acme:
    githubOrgs: [acme-gh]
    editorTeam: acme-gh/scenario-maintainers
```

At login, a login first clears the organization-membership check above. Serve then calls GitHub's
`GET /user/teams`, which the `read:org` scope of the OAuth flow already covers. Serve checks whether
the response lists the configured org/Team pair as a **direct** membership. A match promotes the
login to editor within the org that it resolved into. No match leaves the login at viewer.

`/user/teams` lists child ("nested") Teams as memberships distinct from their parent. The check
looks for the exact configured Team only, and not for a nested Team beneath it. That choice keeps the
check flat by construction. It matches the decision to leave nested-Team resolution out of the first
cut of this item.

The lookup follows the same `Link`-header pagination that `_fetch_orgs` already uses for org
membership ([`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)). A login whose
target Team sits on a later page of a large Team list still resolves correctly.

An unsuccessful call — a non-200 response, a network failure, or an unparseable body — is treated the same as a genuine non-match. The login resolves to viewer and does not fail sign-in.

This failure direction is the opposite of the org-membership fetch in `_fetch_orgs`. That fetch fails
open to the `default` org, because a lookup failure there affects only *which* org a login lands in.
Here a lookup failure could otherwise grant write access, so the lookup fails closed to the
lower-privilege outcome.

### Admin stays one server-wide tier

Serve enforces `_ADMIN_PATHS` and the admin-gated `GET` endpoints
([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) instance-wide today. `role_for`,
`user_role`, and `forbidden_for_role` take no org argument. The admin role of one user therefore
reaches the config, secrets, and provider settings of every org, not only the org that the user
belongs to.

Giving each org its own admin Team would silently hand the admin of that org instance-wide power
over every other org. Nothing downstream of the role check narrows the power back to one org.

This item does not change that enforcement scope, so admin stays a single, server-wide tier. One
Team replaces the `BAJUTSU_OAUTH_ADMINS` login list. The deployment names the Team once, as
`BAJUTSU_OAUTH_ADMIN_TEAM`, in the same `"<github-org>/<team-slug>"` form. The check works the same
way as the check for `editorTeam`.

Scoping admin itself to one org is a materially larger change. So is introducing a separate, higher
tier for cross-org operations above that. The item tracks both as a possible future item and does not
fold them in (see *Alternatives considered*).

The admin-Team check runs after a login clears the organization-membership gate. This is the same
ordering that *Write access* states for `editorTeam`. Consider a `BAJUTSU_OAUTH_ADMIN_TEAM` member
whose GitHub organization is not reachable through the `githubOrgs` or `members` of any tenant. An
ops-only GitHub organization that no `orgs:` entry lists is an example. Sign-in rejects that member
before the admin Team is consulted. The member loses sign-in entirely and does not gain admin.

This section names the admin Team independently of any tenant org. The naming only works in practice when
the admin is also a member of some configured tenant org, through `members` or `githubOrgs`. This
item adds no separate path around the organization-membership gate for admins. A deployment must
ensure that overlap itself.

### Revocation reflects on the next login, unchanged

BE-0015 §7c-2 already recomputes the role of a user from policy on every login, so a policy change
needs no data migration. This item keeps that principle exactly. Leaving a GitHub organization or
Team takes effect the next time that user logs in. Removal from `BAJUTSU_OAUTH_ADMINS` works the same
way today. No new revocation mechanism is needed.

### The admin-gated `GET` exceptions are unaffected

`required_role()` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already requires admin
for three reads that disclose more than their path implies: `GET /api/config/content`,
`GET /api/artifacts/exists`, and `GET /api/version/checkout`. The requirement holds regardless of how
a viewer or editor role was granted.

Broadening who qualifies as a viewer does not touch these entries. They check the resolved role
against a fixed "admin" requirement. They do not check the organization or Team that the role came
from.

### The shared token narrows to worker traffic once OAuth is configured

Three independent things currently answer to `BAJUTSU_SERVE_TOKEN`, not two:

- A worker authenticates to the five `/api/worker/*` routes (`lease`, `heartbeat`, `result`,
  `artifact-urls`, `scenario-url`; [`bajutsu/serve/routes.py`](../../bajutsu/serve/routes.py)) with
  the token as a bearer token. The worker use is unrelated to any human, and this item leaves it
  unaffected either way.
- A human browser can exchange the token for a session cookie through `POST /api/login` (`login()` in
  [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)). The exchange bypasses GitHub OAuth and
  the RBAC built on top of it.
- Any client, human or automated, can present the token directly as an `Authorization: Bearer` header
  on **any** endpoint.

The third path works as follows. `gate.is_authorized`
([`bajutsu/serve/gate.py`](../../bajutsu/serve/gate.py)) accepts the header, on either of the two
HTTP backends of serve:

- The `_gate()` of the stdlib handler ([`bajutsu/serve/handler.py`](../../bajutsu/serve/handler.py)).
- The `_security_gate` of the FastAPI app ([`bajutsu/serve/server/app.py`](../../bajutsu/serve/server/app.py)).

Both backends call `is_authorized`, and both skip `forbidden_for_role` entirely for that request. A
Bearer-authenticated request carries no identity to check a role for. This is the same "operator
credential, full access, e.g. for CI" path that BE-0015 §7b documented when OAuth was introduced.

The third path is the widest of the three. It reaches every RBAC-gated endpoint, not only the login
form. Retiring the cookie exchange alone would leave the larger bypass untouched.

This item retires both the cookie exchange and the direct Bearer path, once OAuth is configured. OAuth
is configured when all three `BAJUTSU_OAUTH_GITHUB_CLIENT_ID`/`_CLIENT_SECRET`/`_REDIRECT_URI`
environment variables are set. The change has two parts:

- The Bearer-token branch of `gate.is_authorized` is honored for the `/api/worker/*` routes alone, on
  both backends.
- A human signs in through `/api/oauth/login` alone.

A deployment that relies today on the "operator credential" reach of the token loses that access once
OAuth is configured. The reach covers scripted or CI access to non-worker endpoints. This item
designs no replacement machine-credential mechanism for that use case. A service-account or
personal-access-token-style identity, distinct from both the worker token and a human OAuth session,
is a materially larger addition. The item leaves it to a future proposal.

When OAuth is not configured, every one of the three uses of the token is unchanged. BE-0051 designed
the shared token for that deployment: the single-Mac, private-network deployment. RBAC does not apply
there regardless, because no database is wired and `forbidden_for_role` returns `False` whenever
`state.repository is None`. Narrowing any sign-in or automation path of that deployment would leave
it with less than it started with.

## Alternatives considered

- **An identity-aware proxy (IAP) in front of serve.** Rejected. Bajutsu already has its own GitHub
  OAuth mechanism (BE-0015), and the users of the deployment already authenticate on GitHub. An IAP
  would add a second, redundant identity system and would not reuse the one already in place.
- **Per-org admin Teams, with a new, higher tier above them for cross-org operations.** Considered as
  the org-scoped counterpart to the per-org editor Team above. Rejected for this item. Serve enforces
  `_ADMIN_PATHS` and the admin-gated reads instance-wide today. Scoping admin to one org would first
  require making that enforcement org-aware. That change is materially larger and orthogonal to
  swapping the role *source* from login lists to GitHub membership. This item leaves it for a future
  proposal.
- **Attaching `editorTeam` to the project (BE-0225) instead of the Bajutsu org.** Considered for a
  deployment where different repositories under one GitHub organization need different write rosters.
  Rejected for this item. A project carries no access control of its own today: `ProjectRecord` has no
  such field, and `required_role` has no project-aware branch. The attachment would add a new
  access-control axis to a concept that BE-0225 defined purely as a config-source binding. That
  change is materially larger than swapping the role *source* for the Bajutsu org that this item
  already has. A deployment that needs different write rosters per repository can already declare
  multiple Bajutsu orgs, each naming a different `editorTeam`, rather than add project-level access control.
- **Nested-Team membership for the editor and admin Teams.** GitHub Teams support parent/child
  hierarchies, and `/user/teams` reports the membership of a login in a child Team distinct from its
  parent. Rejected for the first cut of this item, in favor of checking the exact configured Team
  alone. The exact check matches the flat structure that the deployment already decided on. Nested
  resolution can follow later if the Team hierarchy of a deployment needs it.
- **Retiring the shared token as a human sign-in path unconditionally.** Rejected. BE-0051 designed
  the token for the single-Mac, private-network deployment. That deployment has no OAuth app to
  register and no RBAC to enforce, because no database is wired. Retiring the token there would remove
  the only sign-in path that the deployment has, with nothing to replace it.
- **Keeping the login-list allowlists alongside the new organization/Team checks, as an escape
  hatch.** Rejected. A second, independent grant path defeats the purpose of deriving roles from the
  membership records of GitHub itself. The path recreates the roster-drift problem that this item
  exists to remove, for the users routed through the escape hatch instead of for all users.
- **Leaving the direct `Authorization: Bearer` path of the token untouched on non-worker endpoints
  once OAuth is configured.** Rejected. The premise of this item is that GitHub organization and Team
  membership decide who can act as what once OAuth is configured. A client that can still reach
  every endpoint by presenting the raw token, with no role check at all, would leave that premise
  false. The result holds regardless of how carefully `editorTeam` and `BAJUTSU_OAUTH_ADMIN_TEAM` are
  configured.
- **Shortening the revocation window with a session time-to-live (TTL).** Considered, so that a Team
  removal takes effect before the next login of the affected user and not only on it. Deferred.
  Session expiry is a property of the session store (BE-0015), independent of where the source data
  of a role comes from. Today's login-list-based revocation already waits for the next login on the
  same terms that this item keeps. A future item could shorten that window for every revocation path
  at once, and would not single this one out.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Extend `OrgConfig` with the `editorTeam` field and its config-schema documentation.
- [x] Add the `GET /user/teams` lookup (direct membership only) and the `BAJUTSU_OAUTH_ADMIN_TEAM`
      environment variable.
- [x] Add a plain match/no-match check (the same `members`/`githubOrgs` test `org_for_identity`
      already performs) for the sign-in gate to call before `org_for_identity` — `org_for_identity`'s
      `str` return can't distinguish "matches nothing" from "legitimately resolves to `default`."
      Keep the rejection at the top of `oauth_callback` — before the `if state.repository is not
      None:` block — so an OAuth-configured but database-less deployment still gates sign-in; retire
      `BAJUTSU_OAUTH_ALLOWED_USERS` and `BAJUTSU_OAUTH_VIEWERS`.
- [x] Replace `role_for()`'s login-list resolution with the organization/Team resolution above; retire
      `BAJUTSU_OAUTH_ADMINS`.
- [x] Gate the token-backed `POST /api/login` cookie path on "OAuth is not configured" for this
      deployment.
- [x] Scope `gate.is_authorized`'s Bearer-token branch to the `/api/worker/*` routes once OAuth is
      configured (`bajutsu/serve/gate.py`), and keep both call sites — the stdlib handler
      (`bajutsu/serve/handler.py`) and the FastAPI app (`bajutsu/serve/server/app.py`) — in lockstep,
      closing the direct-Bearer RBAC bypass on every other endpoint alongside the cookie-login
      retirement.
- [x] Update the configuration/self-hosting docs and the BE-0015/BE-0016 cross-references (both
      languages) to describe the organization/Team-based RBAC in place of the login lists.
- [x] Tests: the organization-membership sign-in gate (allow and reject), editor/admin Team resolution
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

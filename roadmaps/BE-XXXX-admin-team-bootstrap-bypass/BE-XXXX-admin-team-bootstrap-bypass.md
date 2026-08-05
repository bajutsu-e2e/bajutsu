**English** · [日本語](BE-XXXX-admin-team-bootstrap-bypass-ja.md)

# BE-XXXX — Admin GitHub Team env var bypasses the org-membership sign-in gate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-admin-team-bootstrap-bypass.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | TBD — filled in once the PR is opened (this is a BE-creation PR; the id and PR are not opened by this session) |
| Topic | Hosting the web UI |
| Related | [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets `serve`'s server-wide admin GitHub Team sign in even when the `orgs:` config has no
entry for that admin's own GitHub organization — including when `orgs:` is missing entirely. It
renames the environment variable that names the admin Team from `BAJUTSU_OAUTH_ADMIN_TEAM` to
`BAJUTSU_OAUTH_ADMIN_TEAMS` (a comma-separated list, so a deployment can name more than one Team) and
lets a match against it clear GitHub OAuth's sign-in gate directly, rather than only deciding a role
after that gate has already let a login through.

## Motivation

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) gates GitHub OAuth
sign-in on membership in a Bajutsu org declared under `orgs:` (an explicit `members` entry or a
member of a `githubOrgs`-listed GitHub organization). Only a login that clears this gate is even
checked against `BAJUTSU_OAUTH_ADMIN_TEAM` for the admin role. BE-0313's own design text names the
resulting gap. Take an admin Team member whose GitHub organization is not reachable through any
tenant's `githubOrgs` or `members` — an operations-only GitHub organization no `orgs:` entry lists,
for instance. That member is rejected at sign-in before the admin Team is ever consulted, losing
sign-in entirely rather than gaining admin. The same rejection reaches every login when `orgs:` is
missing from the config altogether, or its `members`/`githubOrgs` entries don't yet cover the
deployment's operators: `identity_matches_org` admits nobody against an empty org roster. A GitHub
OAuth deployment that starts up with no `orgs:` block, or with one an operator has not yet finished
editing, locks out every admin along with everyone else.

That failure mode falls on the one person meant to fix it. A broken or incomplete `orgs:` block is
exactly the kind of config mistake an admin exists to correct, by repointing the server at a
corrected config source (`POST /api/config`) or uploading a corrected bundle (`POST /api/upload`).
BE-0313's design leaves no path to sign in and make that fix once the block is already broken. The
deployment waits on a manual edit to the environment or the config store from outside `serve`
itself, with no admin able to reach the running server to correct it from the web UI.

## Detailed design

### The admin env var becomes a comma-separated list, and its check moves earlier

`BAJUTSU_OAUTH_ADMIN_TEAM` (singular) is renamed to `BAJUTSU_OAUTH_ADMIN_TEAMS` (plural): a
comma-separated list of GitHub Teams, each written `"<github-org>/<team-slug>"`. A deployment can
then name more than one admin Team, for example one per GitHub organization it operates across.
Renaming rather than adding a second variable avoids leaving two admin-Team knobs with different
sign-in behavior. This item changes what an admin Team membership means at sign-in (see below), so
the existing single-Team variable would otherwise need the same behavior change under its old name.
The rename and the behavior change become one edit rather than two.

`oauth_callback` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already fetches the
login's GitHub Team memberships (`identity.teams`, via `fetch_identity`) before it runs the
org-membership gate, because that same fetch also supplies the `editorTeam` role check further down.
The gate and the Team fetch simply weren't ordered to use it together. This item adds one more check
alongside `identity_matches_org`: a login whose `identity.teams` intersects the configured admin Team
list clears the sign-in gate regardless of what `identity_matches_org` returns. An admin Team member
then signs in even when no `orgs:` entry lists their GitHub organization, or when `orgs:` is absent
altogether. A login that satisfies neither check is still rejected exactly as today.

### Role resolution is unaffected in shape, only in name

`role_for` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already ranks admin above editor
above the viewer base role, from a single admin-Team parameter. This item only widens that parameter
from one Team to a list, checking for any intersection with `identity.teams` instead of a single
membership test. The sign-in gate above already establishes that a bypassing login carries a matching
Team, so `role_for` resolves it to admin the same way it resolves any other admin Team member today.
No separate role path is added for the bypass case.

`role_for` and the org placement below both run only inside `oauth_callback`'s
`if state.repository is not None:` block, exactly as they do today for every other admin Team member;
this item adds no new call to either. On a deployment with no database wired, neither runs, and the
bypass's only effect is on the sign-in gate itself: `forbidden_for_role` already grants full access to
any signed-in user there regardless of role, because `state.repository is None` short-circuits it
(BE-0313), so a bypassing login gains only the sign-in that gate already grants any other admitted
login on that deployment shape.

### Org placement for a bypassing admin

An admin who clears the gate through the Team bypass, rather than through `orgs:` membership, is
placed the same way `org_for_identity` already places any login that matches no
`members`/`githubOrgs` entry: the shared `default` org. BE-0313's own text states that the `default`
org "becomes unreachable through OAuth sign-in" once an `orgs:` block is declared, because every
login that would have landed there is rejected before placement is computed. This item makes that
statement no longer universally true, since a bypassing admin now reaches `default` precisely because
no other org claims their login. `default` carries no special targets beyond whatever no other org
has claimed. Admin's `_ADMIN_PATHS` enforcement is already instance-wide regardless of org (BE-0313
§"Admin stays one server-wide tier"), so this placement grants a bypassing admin nothing beyond the
admin role BE-0313 already made server-wide.

### Failure mode of the underlying Team fetch is unchanged

`_fetch_teams` ([`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)) already fails
closed to an empty team list on a non-200 response, a network hiccup mid-pagination, or an
unparseable body. This item adds no new call to GitHub: it reads the same `identity.teams` BE-0313
already fetches. A GitHub API outage therefore still leaves an admin Team member unable to prove
membership, and therefore unable to use the bypass. They fall back to whatever the org-membership
gate alone would have granted them, rejection if their org isn't reachable through `orgs:` either.
This item adds no login-list fallback for that outage case; see *Alternatives considered*.

## Alternatives considered

- **Adding a login-list environment variable (an `ADMIN_USERS`-style roster) instead of, or alongside,
  the Team-based bypass.** Considered because a login list survives a GitHub Teams API outage that a
  Team-based bypass cannot. Rejected for this item: BE-0313 already rejected keeping a login-list
  allowlist alongside its organization/Team checks. Its reasoning was that a second, independent grant
  path recreates the roster-drift problem GitHub-membership-based role-based access control (RBAC)
  exists to remove. That rejection targeted a general escape hatch available to every role. The
  narrower, admin-only bypass this item adds targets a different problem, recovering from a broken or
  absent `orgs:` block, and keeps deriving admin from GitHub Team membership rather than a
  hand-maintained roster. It therefore does not reopen the roster-drift case BE-0313 argued against. A
  login-list variable remains open to propose separately if the Teams-API-outage gap turns out to
  matter in practice.
- **Leaving `BAJUTSU_OAUTH_ADMIN_TEAM` singular and adding a second, bypass-only variable.** Rejected:
  it would leave two admin-Team variables with different sign-in behavior for as long as both exist,
  with no way for an operator to tell which one they're using without reading the source. A single
  renamed variable is one behavior to document and reason about.
- **Reading `BAJUTSU_OAUTH_ADMIN_TEAM` as a deprecated fallback when `BAJUTSU_OAUTH_ADMIN_TEAMS` is
  unset, so an existing deployment keeps working across the upgrade without an env-var edit.**
  Rejected: `BAJUTSU_OAUTH_ADMIN_TEAM` is itself BE-0313's own addition, part of the same GitHub
  Team-based RBAC design this item continues to refine, not a long-settled variable with a wide
  installed base. BE-0313 already retired `BAJUTSU_OAUTH_ADMINS` outright rather than aliasing it when
  it replaced the login-list admin roster with this same Team; a hard cutover for its direct successor
  keeps that precedent rather than introducing a fallback path this design otherwise has no use for. A
  deployment that already sets `BAJUTSU_OAUTH_ADMIN_TEAM` renames it to `BAJUTSU_OAUTH_ADMIN_TEAMS` at
  upgrade time, the same kind of adoption step BE-0313's own `orgs:`-block requirement already asks of
  every OAuth deployment.
- **Scoping the bypass to only the deployment shape with no database wired, where BE-0313 already
  documents the org gate as the sole line of defense.** Rejected: the config-editing recovery scenario
  this item targets, a broken `orgs:` block on an otherwise fully wired server backend, is exactly the
  shape with a database. Scoping the bypass away from it would leave the motivating case unsolved.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Rename `BAJUTSU_OAUTH_ADMIN_TEAM` to `BAJUTSU_OAUTH_ADMIN_TEAMS` (comma-separated), threading the
      list through `SessionManager`, `role_for`, and the server-backend env wiring.
- [x] Add the admin-Team bypass to the sign-in gate in `oauth_callback`, alongside
      `identity_matches_org`, using the Team list already fetched for role resolution.
- [x] Update the self-hosting and configuration docs (both languages) and `.env.example` to describe
      the renamed variable and the bypass, including BE-0313's now-superseded claim that the `default`
      org is unreachable through OAuth sign-in.
- [x] Tests: sign-in accepted for an admin-Team member with no matching `orgs:` entry and with no
      `orgs:` block at all; resolved role is admin in both cases; a login matching neither the org
      gate nor the admin-Team list is still rejected; the renamed variable parses a multi-Team list;
      a bypassing admin is placed in the `default` org.

## References

- [BE-0313 — GitHub org membership and Team-based RBAC for serve](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) —
  the organization/Team-based sign-in gate and role resolution this item narrows a gap in, including
  the design text that names the gap this item closes ("Admin stays one server-wide tier").
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — `oauth_callback`'s sign-in gate and
  `role_for`'s role resolution, both touched by this item.
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) — `identity_matches_org` and
  `org_for_identity`, the org-membership checks the admin-Team bypass sits alongside.
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py) — `_fetch_teams`, whose
  already-fail-closed behavior on a GitHub API failure this item's bypass inherits unchanged.
- [`bajutsu/serve/state.py`](../../bajutsu/serve/state.py) — `SessionManager`, whose
  `oauth_admin_team` field this item widens to a list.
- [`docs/self-hosting.md`](../../docs/self-hosting.md) — the self-hosting guide's GitHub OAuth
  section, which already documents the gap this item closes ("An admin still has to clear the sign-in
  gate above first").

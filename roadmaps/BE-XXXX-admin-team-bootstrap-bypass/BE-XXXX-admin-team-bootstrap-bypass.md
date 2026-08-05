**English** · [日本語](BE-XXXX-admin-team-bootstrap-bypass-ja.md)

# BE-XXXX — Admin GitHub Team env var bypasses the org-membership sign-in gate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-admin-team-bootstrap-bypass.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | [#1485](https://github.com/bajutsu-e2e/bajutsu/pull/1485) |
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
org's `githubOrgs` or `members` — an operations-only GitHub organization no `orgs:` entry lists,
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

Two startup checks keep this parsing from losing every admin without a trace. `_build_server_state`
([`bajutsu/serve/__init__.py`](../../bajutsu/serve/__init__.py)) prints a stderr warning whenever
GitHub OAuth is configured but the parsed `oauth_admin_teams` list comes out empty — whether because
the deployment never set `BAJUTSU_OAUTH_ADMIN_TEAMS` at all, or because only the retired
`BAJUTSU_OAUTH_ADMIN_TEAM` is set (the hard cutover in *Alternatives considered* means that
deployment now has no admin Team at all). Either way the only other symptom is an unexplained 403 on
every admin action, and — because this same list is now also a sign-in credential (see below) — no
admin left who can sign in to fix it. The warning names the retired variable only when it's actually
the cause, so the never-set case isn't blamed on a rename that never happened. It also warns on any
entry that isn't
exactly one `"<github-org>/<team-slug>"` pair — matched against a regular expression that also
rejects an empty half or internal whitespace, not by counting `/`: a space- or semicolon-separated
list parses to a single malformed entry that can never match a real Team, which
is the same "no admin, no visible cause" failure reached by a different mistake. Both print a warning
rather than raising, so a config typo degrades a deployment to no-admin instead of refusing to start
it entirely — consistent with every other env var this module reads, none of which validate their
shape at startup either. The malformed entry stays in the list rather than being dropped: dropping it
would silently narrow the admin roster to whatever remained syntactically valid, a second silent
failure on top of the one the warning already reports.

`oauth_callback` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already fetches the
login's GitHub Team memberships (`identity.teams`, via `fetch_identity`) before it runs the
org-membership gate, because that same fetch also supplies the `editorTeam` role check further down.
The gate and the Team fetch simply weren't ordered to use it together. This item adds one more check
alongside `identity_matches_org`: a login whose `identity.teams` intersects the configured admin Team
list clears the sign-in gate regardless of what `identity_matches_org` returns. An admin Team member
then signs in even when no `orgs:` entry lists their GitHub organization, or when `orgs:` is absent
altogether. A login that satisfies neither check is still rejected exactly as today. Every time the
bypass alone admits a login, `oauth_callback` logs a warning naming the login: the bypass is the one
sign-in path `orgs:` did not authorize, so it is the one path an operator auditing who signed in, and
when, would otherwise have no record of at all.

The membership test behind that check — is any of a login's Teams in the configured admin list — is
also the test `role_for` uses to resolve the admin role, so this item factors it into one shared
`in_admin_team` helper rather than writing the same expression out twice. Two copies of the same rule
in two functions ~120 lines apart could drift apart under a later, independent edit to either one —
a login the gate's copy admits but the role's copy doesn't would sign in and resolve to `viewer`, a
session for a login `orgs:` never authorized and with none of the admin access that was the reason to
admit it. One helper makes that drift impossible by construction.

### Role resolution is unaffected in shape, only in name

`role_for` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already ranks admin above editor
above the viewer base role, from a single admin-Team parameter. This item only widens that parameter
from one Team to a list, checking for any intersection with `identity.teams` (via the shared
`in_admin_team` helper above) instead of a single membership test. The sign-in gate above already
establishes that a bypassing login carries a matching Team, so `role_for` resolves it to admin the
same way it resolves any other admin Team member today. No separate role path is added for the
bypass case.

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
no other org claims their login. `default` carries no special
[targets](../../docs/glossary.md#target-app-device) beyond whatever no other org
has claimed. Admin's `_ADMIN_PATHS` enforcement is already instance-wide regardless of org (BE-0313
§"Admin stays one server-wide tier"), so this placement grants a bypassing admin nothing beyond the
admin role BE-0313 already made server-wide.

This placement inherits an existing sharp edge of the org model rather than introducing a new one:
`DEFAULT_ORG` ([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)) is the literal string
`"default"`, and nothing stops a deployment from also declaring a real org under that same key.
`targets_for_org` already special-cases that string for target ownership regardless of what such an
org's own `targets:` list says, so a deployment naming an org literally `default` already has an
existing collision this item doesn't create. A bypassing admin who matches no other org is placed
under the same key, so on a deployment with a real `default` org, their user row, audit entries, and
object-storage prefix land under that org rather than a neutral catch-all — the same collision,
extended to one more caller. A deployment that relies on the admin-Team bypass should avoid naming
an org literally `default` for this reason, on top of the existing target-ownership reason to avoid
it.

### The underlying Team fetch keeps failing closed, now sometimes at the cost of sign-in itself

`_fetch_teams` ([`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)) already fails
closed to an empty team list on a non-200 response, a network hiccup mid-pagination, or an
unparseable body, and this item adds no new call to GitHub: it reads the same `identity.teams`
BE-0313 already fetches. For a login the org gate already admits, that failure still costs only the
editor/admin role, exactly as before. For a login whose sign-in depends solely on the bypass, the
same fail-closed behavior now costs sign-in itself: a GitHub API outage leaves them unable to prove
admin-Team membership, so they fall back to whatever the org-membership gate alone would have
granted them, rejection if their org isn't reachable through `orgs:` either. This item adds no
login-list fallback for that outage case; see *Alternatives considered*.

### Every admin Team entry is now a sign-in credential, not only a role mapping

Before this item, an entry naming a GitHub organization the deployment doesn't actually control — a
typo in the organization half, or a Team whose organization was later renamed or deleted (GitHub
frees the old login for anyone to register) — simply matched nobody: `role_for` consulted it only
after `identity_matches_org` had already turned away anyone not otherwise in the org roster. After
this item, that same entry *is* part of the sign-in gate. Whoever controls that organization login
can create a Team with the matching slug, sign in through the bypass, and be resolved to admin,
reaching every `_ADMIN_PATHS` endpoint — including `GET /api/config/content`, which can disclose a
config body embedding literal secrets. The malformed-entry warning in *The admin env var becomes a
comma-separated list* cannot catch this: a syntactically well-formed entry naming an organization
nobody here owns validates its shape perfectly. No code can verify who controls a GitHub
organization, so this item's only mitigation is operational: `deploy/self-host/.env.example` and the
self-hosting guide (both languages) now say plainly that every entry must name a GitHub organization
the deployment actually controls, because the value is a sign-in credential now, not only a role
mapping.

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
      list through `SessionManager`, `role_for`, and the server-backend env wiring. Warn loudly at
      startup whenever OAuth is configured but the resulting list is empty — whether nothing was set
      or only the retired singular name was — and separately when an entry is not a well-formed
      `"<github-org>/<team-slug>"` pair, so no admin-losing mistake goes unsignaled.
- [x] Add the admin-Team bypass to the sign-in gate in `oauth_callback`, alongside
      `identity_matches_org`, using the Team list already fetched for role resolution. Log a warning
      naming the login every time the bypass alone admits it, so the one sign-in path `orgs:` did not
      authorize still leaves a record.
- [x] Update the self-hosting and configuration docs (both languages) and `.env.example` to describe
      the renamed variable and the bypass. BE-0313's claim that the `default` org is unreachable
      through OAuth sign-in is superseded in this item's *Detailed design* instead: no `docs/` page
      states it, so none needed the edit. State plainly that every entry must name a GitHub
      organization the deployment actually controls, since the value is now a sign-in credential.
- [x] Tests: sign-in accepted for an admin-Team member with no matching `orgs:` entry and with no
      `orgs:` block at all; resolved role is admin in both cases; a login matching neither the org
      gate nor the admin-Team list is still rejected; the renamed variable parses a multi-Team list;
      a bypassing admin is placed in the `default` org. End to end through the HTTP transport: a
      login matching no `orgs:` entry, admitted only by the bypass, can actually reach an
      admin-gated endpoint (`POST /api/apikey`) — not just receive a session.

## References

- [BE-0313 — GitHub org membership and Team-based RBAC for serve](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) —
  the organization/Team-based sign-in gate and role resolution this item narrows a gap in, including
  the design text that names the gap this item closes ("Admin stays one server-wide tier").
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — `oauth_callback`'s sign-in gate and
  `role_for`'s role resolution, both touched by this item.
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) — `identity_matches_org` and
  `org_for_identity`, the org-membership checks the admin-Team bypass sits alongside.
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py) — `_fetch_orgs` and
  `_fetch_teams`, whose docstrings this item updates: the same fail-closed behavior on a GitHub API
  failure now sometimes costs sign-in itself, not only a role, for a login the bypass alone admits.
- [`bajutsu/serve/state.py`](../../bajutsu/serve/state.py) — `SessionManager`, whose
  `oauth_admin_team` field this item widens to a list.
- [`docs/self-hosting.md`](../../docs/self-hosting.md) — the self-hosting guide's GitHub OAuth
  section, which documented the gap this item closes ("An admin still has to clear the sign-in gate
  above first") until this item removed that caveat.

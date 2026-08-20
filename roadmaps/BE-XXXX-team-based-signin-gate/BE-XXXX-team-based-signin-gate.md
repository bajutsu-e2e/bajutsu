**English** · [日本語](BE-XXXX-team-based-signin-gate-ja.md)

# BE-XXXX — A GitHub Team admits a login, not only a GitHub organization

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-team-based-signin-gate.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **In progress** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Implementing PR | — |
| Topic | Hosting the web UI |
| Related | [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) · [BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) · [BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds a GitHub Team as a third membership axis on a `serve` org, alongside the explicit
`members` logins and the whole-organization `githubOrgs`. An org may name Teams under a new
`githubTeams` field, and the `editorTeam` it already names now admits its direct members as well as
promoting them to editor. A login is placed in the org that admitted it, so the sign-in gate and the
org resolution answer from one ranking rather than two.

## Motivation

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) gives an org two
membership axes: `members`, which names logins one at a time, and `githubOrgs`, which names a whole
GitHub organization. A GitHub organization shared by several teams has nothing in between. Admitting
one of its teams means either listing every member of that team by hand — a roster that drifts the
moment somebody joins or leaves, which is the maintenance cost `githubOrgs` exists to remove — or
listing the whole organization, which admits every other team in it. GitHub already models the unit
the deployment wants: the Team. `serve` reads a login's Teams on every sign-in
(`/user/teams`, for the editor and admin roles), so the membership is on hand and only the gate
declines to consult it.

`editorTeam` sharpens the same gap into a configuration an operator can write by accident. It names
the Team whose members may run, record, and edit scenarios — write access, the strongest per-org
grant there is — yet it admits nobody. A Team named there and nowhere else resolves to "may write
but cannot sign in": every member is turned away at the gate that runs before any role is computed.
The org has declared, in the one field that grants write access, who its editors are, and they are
exactly the people it does not let in.

Leaving the gate on two axes also pushes deployments toward the coarse grant. An operator who wants
one team of a shared organization has `githubOrgs` and a hand-maintained `members` list to choose
between, and the list is the one that decays, so the coarse grant is the one that survives contact
with a growing team.

## Detailed design

### `githubTeams`: a Team as an org's membership axis

`OrgConfig` gains `github_teams` (`githubTeams` in the `orgs:` block and in the API), a list of
flat GitHub Teams each written `"<github-org>/<team-slug>"` — the same shape `editorTeam` and every
`BAJUTSU_OAUTH_ADMIN_TEAMS` entry already take. A direct member of any of them belongs to the org.
Membership at the gate carries no role of its own: an admitted login gets **viewer**, the base role
every signed-in user gets, and `editorTeam` and `BAJUTSU_OAUTH_ADMIN_TEAMS` keep deciding the rest.
So `githubTeams` widens who may sign in and nothing else.

The field is a list rather than a single Team, unlike `editorTeam`. A Team is a narrow unit by
construction, so an org that admits Teams at all will often admit several — one per contributing
team of a shared organization — where the one Team that may *write* is a deliberate singleton.

### `editorTeam` admits as well as promotes

A login is admitted when it is a direct member of the org's `editorTeam`, without that Team being
repeated under `githubTeams`. Requiring the repetition would keep "may write but cannot sign in" a
writable configuration, and nothing is gained by making an operator declare twice a Team the org has
already committed write access to. `OrgConfig.admitting_teams()` returns the union — `github_teams`
plus `editor_team` when it is set — so the gate and every "does this org declare a membership" check
read one accessor and cannot disagree about whether `editorTeam` alone admits anyone.

### One ranking decides both the gate and the placement

The gate and the org resolution used to hold the precedence separately: `identity_matches_org`
answered whether any org admits this login, `org_for_identity` answered which one. Adding an axis to
one and not the other would admit a login through one org's Team and then file it under another —
handing it that other tenant's targets and object-storage prefix, and reading the wrong org's
`editorTeam` when it computes the role, so a login admitted by its editor Team would land as viewer.
Both now delegate to one private `_match_org`, which returns the matching org or None:

1. an explicit `members` entry,
2. an intersection with some org's `githubOrgs`,
3. direct membership in one of some org's admitting Teams.

Teams rank last so that declaring one never relocates a login an existing `members`/`githubOrgs`
entry already placed: adding an axis widens who may sign in without re-filing anyone who already
could. `identity_matches_org` is `_match_org(...) is not None` and `org_for_identity` is
`_match_org(...)` with `default` for None, so the two answers cannot drift apart. Both keep their
names and gain the Team list as a defaulted parameter, since the failure they guard against —
admitting through one org and resolving to another — comes from two rankings, not two names.

### Team matching is case-insensitive everywhere

`role_for`'s `editorTeam` check was a deliberate exact match: BE-0313 recorded the latent case trap
and left widening it out of scope, because a case-mismatched entry cost only the editor role. Once
`editorTeam` admits, the same mismatch costs sign-in, and matching one way but not the other would
admit a login and then hand it viewer. So the one comparison behind the gate, the editor role, and
the admin Team is now `orgs.in_teams`, which case-folds both sides as GitHub itself resolves an org
login and a Team slug; `authz.in_admin_team` delegates to it rather than keeping a second copy.
Folding preserves the nested-Team guarantee, which rests on exact equality of the whole
`"<github-org>/<team-slug>"`: `/user/teams` reports a child Team distinct from its parent, so a Team
nested beneath a configured one still does not match.

### The database is the other producer of the same model

A database-backed deployment keeps membership in the `orgs` table (BE-0375), so `orgs` gains a
nullable `github_teams` JSON column (migration `0016`). Nullable and unseeded, like the columns
`0015` added: an existing row upgrades without a value and reads as "this org admits no Team of its
own", which is what every org meant before the column existed. `editor_team` keeps its own column,
since it decides a role as well as admitting — folding the two would lose which Team may write.

The column threads through the seam the same way the other three do: `OrgRecord`,
`set_org_membership`, `seed_org_membership`, and `orgs_from_db`, so a config-sourced and a
database-sourced org model still resolve identically. `orgs_declaring_membership` counts an entry
declaring only Teams, so the cutover seeds it and the "no org declares any membership yet" warning
does not fire forever on a deployment whose every roster is a Team.

### The denial says what GitHub withheld

`_OrgModel.unmatched` names why nothing matched, and an empty org list stays the primary signal
there — a `/user/orgs` outage looks exactly like it. It now names the Team list too, and only when
that is *also* empty ("GitHub returned no orgs or teams for this login"), which covers the
deployment whose gate is a Team without changing what a login carrying Teams but no orgs is told.
Blaming the org list alone would send an operator to an axis a Team-declared org never consults.

`/user/teams` fails closed — `_fetch_teams` never invents a Team — so while GitHub's Teams API
errors, a login whose only membership is a Team is turned away. That is the direction a gate has to
fail in: one that admitted on an unread Team list would admit everyone for the length of the outage.
BE-0352 already accepted the same trade for the admin-Team bypass; this item widens the set of
logins it applies to, which the self-hosting guide now states where it describes the Team axis.

### The API and the Orgs page carry the fourth field

`POST /api/orgs/<slug>/membership` replaces `{members, githubOrgs, githubTeams, editorTeam}` as one
unit, validating `githubTeams` through the same `_string_list` as `githubOrgs`, and the audit entry
records it — "who could sign in as this tenant, from when" is what an audit of a membership change
has to answer. `GET /api/orgs` returns it, since the page's form prefills from the list and a field
it never showed would be silently emptied by the first save. The Orgs page gains the matching input
and counts the Teams in each row's summary.

## Alternatives considered

**Let `githubTeams` grant editor rather than viewer.** It would collapse two fields into one, but
`editorTeam` is a per-org write grant and `githubTeams` is a per-org read grant; a deployment that
wants a team to *look* would have no way to say so. Admitting at viewer keeps the two grants
independent, and an org that wants both names the same Team in both fields.

**Keep `editorTeam` promotion-only and require the repetition under `githubTeams`.** Explicit, and
it leaves the gate reading exactly one field. But it also leaves "may write but cannot sign in" a
configuration an operator can write, and the failure is silent at write time — it surfaces later, as
a rejected login. The repetition carries no information the org has not already given.

**Rank Teams above `githubOrgs`.** Arguably the narrower axis should win a tie. It would also
relocate, on the next sign-in, every login that a `githubOrgs` entry places today and a Team entry
would place elsewhere — a change of tenant, and so of visible targets and object-storage prefix, as a
side effect of adding an axis. Teams rank last, and letting a login that belongs to more than one org
choose which it means stays the separate item BE-0375 already names it as.

**A single `githubTeam`, mirroring `editorTeam`.** Symmetrical, but a Team is the narrow unit, so an
org admitting Teams commonly admits several; a singleton would push those deployments back to
`githubOrgs`, which is the grant this item exists to avoid.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Add `githubTeams` to `OrgConfig` with `admitting_teams()` unioning it with `editorTeam`, and
      rank the three membership axes once in `orgs._match_org`, which `identity_matches_org` and
      `org_for_identity` both delegate to — so the gate can no longer admit a login through one org
      while the placement files it under another.
- [x] Make the one Team comparison (`orgs.in_teams`) case-insensitive and share it between the gate,
      `role_for`'s editor check, and `authz.in_admin_team`, so a case-mismatched `editorTeam` costs
      neither sign-in nor a role, and the nested-Team guarantee survives the folding.
- [x] Thread the axis through the database producer: migration `0016` adds a nullable
      `orgs.github_teams`, and `OrgRecord` / `set_org_membership` / `seed_org_membership` /
      `orgs_from_db` / `orgs_declaring_membership` carry it, so a config-sourced and a
      database-sourced model resolve identically and a Team-only roster still seeds.
- [x] Name the Team list in `_OrgModel.unmatched`'s denial cause when GitHub returned neither axis,
      so a Team-declared deployment's operator is not sent to inspect an `orgs:` axis it never reads.
- [x] Carry the fourth field through `GET /api/orgs`, `POST /api/orgs/<slug>/membership` (validated,
      audited, returned), and the Orgs page's membership form and row summary.
- [x] Documentation: the `orgs:` reference and the self-hosting RBAC/multi-org sections in both
      languages, plus `architecture.md`'s BE-0375 bullet.

### Log

- Proposed and implemented together (this item's PR).

## References

- [BE-0313 — GitHub org membership and Team-based RBAC for serve](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) —
  the sign-in gate and role resolution this item adds a membership axis to.
- [BE-0352 — Admin GitHub Team env var bypasses the org-membership sign-in gate](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) —
  the first Team-decided sign-in, whose fail-closed trade this item widens to per-org Teams.
- [BE-0375 — Org lifecycle management for serve](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md) —
  the database-sourced org model and the `/api/orgs…` endpoints this item's field threads through.
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) — `OrgConfig.github_teams`,
  `admitting_teams`, `in_teams`, and the shared `_match_org` behind `identity_matches_org` and
  `org_for_identity`.
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — `oauth_callback`'s gate,
  `_OrgModel.unmatched`'s denial cause, `in_admin_team`, and `role_for`.
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py) — `_fetch_teams`, whose
  fail-closed behavior now decides sign-in for a Team-declared org as well as for the admin bypass.
- [`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py) and
  [`migrations/versions/0016_org_github_teams.py`](../../bajutsu/serve/server/migrations/versions/0016_org_github_teams.py) —
  the `orgs.github_teams` column.
- [`bajutsu/serve/operations/orgs.py`](../../bajutsu/serve/operations/orgs.py) and
  [`bajutsu/templates/serve.orgs.mjs`](../../bajutsu/templates/serve.orgs.mjs) — the membership API
  and the Orgs page.

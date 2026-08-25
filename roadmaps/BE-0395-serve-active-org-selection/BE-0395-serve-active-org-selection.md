**English** · [日本語](BE-0395-serve-active-org-selection-ja.md)

# BE-0395 — Choose the active org in serve when a login belongs to several

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0395](BE-0395-serve-active-org-selection.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0395") |
| Implementing PR | [#1749](https://github.com/bajutsu-e2e/bajutsu/pull/1749) |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md), [BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md), [BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md), [BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) |
<!-- /BE-METADATA -->

## Introduction

An **org** is the tenant a hosted `serve` deployment partitions everything by: the runs, the
evidence, the scenarios, the secrets, the registered projects, and the
[targets](../../docs/glossary.md#target-app-device) a signed-in user may reach. Today a user
belongs to exactly one of them. GitHub OAuth sign-in resolves a login to a single org and writes it
to that user's `users.org_id` column, and every request thereafter reads that one column through
`ServeState.org_of`.

That single column is wrong for a user whose GitHub memberships match more than one org. The
resolution picks the first match and never revisits the choice, so the other orgs are unreachable —
not merely inconvenient to reach, but invisible, with nothing in the web UI to say they exist.

We propose that membership become a set and the org become a choice. Sign-in records every org a
login belongs to, together with the role it holds in each; the user picks the active one from a
selector in the web UI header, in place of the org badge that already names the current tenant; and the
next sign-in honors the pick instead of overwriting it. The single-org deployment sees no change:
one eligible org renders the badge exactly as it renders today.

## Motivation

Bajutsu's multi-tenant `serve` was built one axis at a time. BE-0015 introduced the org and gave
each user a column naming theirs. [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)
widened the ways a login can belong to one: an explicit `members` entry, membership in a GitHub
organization the org claims under `githubOrgs`, or direct membership in a GitHub Team it claims
under `githubTeams` / `editorTeams`.
[BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md) moved
the whole membership roster into the database and gave admins a page to edit it. Each step made
overlap more likely, and none of them made the overlap resolvable.

Overlap is not a corner case in the deployment shape those items describe. A consultancy that runs
one Bajutsu deployment for several client tenants staffs an engineer on two of them; a platform team
keeps an org of its own alongside the product org whose GitHub organization it also belongs to. In
both, the person is a legitimate member of two orgs and can work in only the one that happens to
sort first. BE-0375 says so in its own text, and names the blocker: the real answer is to let a
login that belongs to several orgs choose between them, which needs `users.org_id` to stop being a
single column.

Worse than the restriction is the silence around it. The header badge shows one org and offers no
hint that a second exists, so a user whose runs land in the wrong tenant has no way to tell whether
the deployment is misconfigured or whether they are simply looking at the tenant the tie-break
chose. The two failures look identical from the browser, and the second is not a failure at all.

Once this ships, a user who belongs to two orgs signs in, opens the header selector, picks the
second org, and sees that tenant's runs, projects, and targets — then signs out, signs back in, and
lands on the org they picked rather than the one the tie-break would have chosen. A user who
belongs to one org sees the same header they see today.

## Detailed design

### Terminology

An **org** is a tenant, identified by a slug that doubles as its database row id. An **actor** is
the GitHub login behind a request, recovered from the session. A **role** is one of `viewer`,
`editor`, or `admin`, and it decides which endpoints an actor may reach. An **eligible org** is one
whose declared membership admits a given login. The **active org** is the single eligible org that
this actor's requests currently resolve to — the one `ServeState.org_of` answers with, and the one
whose runs, evidence, scenarios, secrets, and projects every tab shows.

### 1. Membership becomes a set, computed at sign-in

`bajutsu/serve/orgs.py` ranks the three membership axes in one place, `_match_org`, and returns the
first org that matches: an explicit `members` entry first, then an intersection with an org's
`githubOrgs`, then direct membership in one of its admitting Teams. We keep that ranking and change
only what it returns. A new `orgs_for_identity` returns every matching org, in the same ranked
order; `org_for_identity` becomes its first element, and `identity_matches_org` becomes a test that
the result is non-empty. No caller's behavior changes, because the head of the ranked list is the
org the old function already returned.

The set has to outlive the OAuth callback, because that callback is the only place a login's GitHub
organization and Team memberships are known — Bajutsu keeps no GitHub token afterward, so nothing
later in the session can recompute them. A new `user_orgs` table records the set: one row per
`(user_id, org_id)` pair, carrying the `role` that pair resolves to. The role belongs on the row
rather than on the user, because a role is per-org by construction: `editorTeams` promotes a member
to `editor` within one org and says nothing about another.

Every sign-in replaces that user's rows wholesale. Recomputing rather than merging is what makes
leaving a GitHub organization take effect on the next sign-in, the same self-healing property
BE-0313 gave the role.

### 2. The active org is a choice that the next sign-in honors

`users.org_id` stays one column and keeps its meaning at every reader: the active org. What changes
is who writes it. Today `oauth_callback` overwrites it on every sign-in with the first match. From
now on it overwrites only when the recorded value is no longer eligible — a user removed from the
org they had selected lands on the head of their new eligible list — and otherwise leaves the
recorded choice alone. Either way, sign-in writes `users.role` from the active org's own
`editorTeams` rather than from the first match's, so a user active in one tenant never carries a
role another tenant's `editorTeams` granted.

Preserving the column is what keeps this change small. `ServeState.org_of(actor)` is the single seam
every org-scoped read and write already goes through, from the run list to the per-org secret store,
so a selection stored where that method already reads needs no new plumbing and cannot leave one tab
scoped to a different tenant than another.

Storing the choice on the user rather than on the session is deliberate, and the alternative fails
for a concrete reason rather than a stylistic one. A session-scoped selection would have to travel
in the session record, which on a hosted deployment is a database row shared across replicas
(`SqlSessionStore`), while `org_of` is keyed by actor and receives no session. Threading the session
through every caller of `org_of` is a far larger change than the feature justifies, and it would
also make the selection evaporate on every re-login — the opposite of what a user who works in one
tenant all week wants.

### 3. Switching orgs

One new endpoint, `POST /api/org`, taking the target slug. It authorizes the switch against the
`user_orgs` rows written at sign-in: a slug with no row for this user is refused with 403, and a
missing row set refuses everything rather than defaulting to permissive — for everyone but an
admin-Team member, whose eligible set unit 4 computes at read time instead of storing. On success it writes both
`users.org_id` and `users.role` from the row, so the role follows the tenant the way it does at
sign-in.

The switch is an ordinary authenticated action, not an admin one: it moves the caller between orgs
that already admit them, and grants nothing their last sign-in did not already establish. It inherits
that sign-in's latency in both directions: a membership revoked since then keeps admitting a switch
until the login next signs in, exactly as it keeps admitting the org they are already acting as —
the next-sign-in latency every membership change in `serve` already has (BE-0313 recomputes the
placement and the role on each login, and `update_org_membership` says so of its own writes). It is still recorded like
every other operationally significant act in `serve` — an `org.switch` event through `oplog`, and an
audit row through `Repository.record_audit` with the destination slug as the target — because a
later reader of the audit log needs to know which tenant an actor was in when they acted, and the
sign-in record alone no longer answers that.

The boot read `GET /api/config` gains an `orgs` field alongside the `actor` and `org` it already
returns: the eligible slugs, sorted by slug. The ranking decides only where a login starts, so it
is not carried into the stored set and the selector's order is simply stable. Disclosing them to the actor whose memberships produced
them reveals nothing they could not obtain by switching, which is the same reasoning that already
lets that endpoint return the actor's own login and active org while the full org roster stays
behind the admin-only `GET /api/orgs`.

### 4. A member of an admin Team is eligible for every org

A member of one of the server-wide admin Teams named by `BAJUTSU_OAUTH_ADMIN_TEAMS` can sign in even
when no org's membership admits them
([BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md)), so their
ranked eligible list is frequently empty. Left alone, the selector would hide from exactly the user
the header badge was built for: the admin who administers several orgs and needs to see which one
their own work lands in.

Their eligible set is therefore every org the deployment has, and it is computed at read time from
`Repository.list_orgs` rather than stored as rows. Storing it would go stale the moment an admin
creates an org on the Orgs page — the one action such a user is most likely to take right before
wanting to switch into the result. The test for "is this an admin-Team member" is `users.role ==
"admin"`, which holds precisely for them: `role_for` returns `admin` for admin-Team membership and
never for anything else. Switching leaves an admin's role at `admin`, since that role comes from a
server-wide Team rather than from any org's membership.

### 5. The header selector

The web UI header already carries an org badge (`#orgbadge`), populated from the boot read and
hidden when no identity is signed in. With two or more eligible orgs it becomes a native `<select>`
listing them, alongside the project switcher that already sits in that row and works the same way;
with one, it stays the read-only badge it is today, so a single-tenant deployment and a
single-org user see no change at all.

Changing the selection posts to `POST /api/org` and reloads the page. A reload rather than a
per-view refresh: every tab is silently scoped to the active org, so a partial update would leave
whichever views were already rendered showing the previous tenant's runs and projects — the exact
confusion this item exists to remove.

### 6. What a switch does not move

A switch changes where the actor's *next* action lands, and moves nothing that already happened. A
run in flight keeps the org it was enqueued under: the job spec carries `job.org`, resolved once at
enqueue and carried to a remote worker, so its evidence and its database row land in the org that
started it even if the actor switches while it runs. Runs, audit entries, and uploaded bundles
already recorded stay where they were recorded.

One pre-existing behavior becomes easier to reach, and we record it rather than leave it to be
discovered. Binding a configuration sets the process-wide `state.config_org` from the binder's org
(`bajutsu/serve/operations/config.py`), so on a deployment where two orgs both bind configurations,
the last binder wins for the whole server. A switcher does not create that behavior and does not
change it; it makes one actor able to reach it without logging out. Fixing it means giving each org
a configuration of its own, which is exactly what
[BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md) proposes, so this item
stops at recording the interaction. The two are independent: an active-org selector works whether or
not the binding is per-org, and BE-0393's per-org memory is worth having whether or not a login can
switch.

## Alternatives considered

**Refuse an ambiguous match at sign-in.** Turning away a login that matches two orgs would make the
overlap loud instead of silent, and it is the honest failure mode for a tie-break nobody chose. It
also turns away logins that sign in successfully today, on deployments whose operators are content
with the tie-break, so it trades a silent restriction for a hard regression.

**Order the orgs so the tie-break is intentional.** A position column on the org table would let an
operator decide which org wins for a login matching several. That is a smaller change, but it
answers a different question: it makes the *deployment* choose, when the user is the one who knows
which tenant they mean today.

**Put the org in the URL or a request header.** Scoping each request to an org named by the client
would allow two tabs in two tenants at once. It would also move the tenant boundary from one
server-side seam to every request's parameters, where a missing or forged value has to be defended
on each endpoint rather than in one method — a large surface to add for a convenience no user has
asked for.

**Store the eligible set in the session instead of a table.** Session records already expire and
already span replicas, so the lifetime is right, and re-login would refresh the set for free. The
`SessionStore` protocol has three implementations (in-memory, Redis, and SQL), so the set would have
to be threaded through all three; and the switch endpoint would still need the per-org role, which
is user state rather than session state. The table costs one migration and touches one
implementation.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1 — `_match_org` became `_match_orgs`, returning every match at its best rank, with
      `orgs_for_identity` exposing the list and `org_for_identity` / `identity_matches_org` reading
      its head and its emptiness; the `user_orgs` table (one row per `(user_id, org_id)` with the
      role held there) and migration `0018`; `set_user_orgs` / `list_user_orgs` on the repository
      seam, the write landing after `upsert_user` so the row it references exists on a first
      sign-in. Tests: every axis ranked, an org matching twice listed once, a login no org admits
      eligible for nothing, and a sign-in recording one role per org.
- [x] 2 — `users.org_selected_at` distinguishes an org a user picked from one resolved for them, so
      sign-in preserves the first and keeps re-resolving the second; `upsert_user` clears the marker
      whenever it relocates the user, since the pick was for the org they have left. Tests: a switch
      surviving the next sign-in, a revoked membership relocating a picked org, and a merely-resolved
      org still re-resolving.
- [x] 3 — `POST /api/org` (`operations.orgs.set_active_org`), authorized against
      `ServeState.eligible_orgs` and refusing an unknown slug exactly as it refuses a non-member's,
      with the `org.switch` `oplog` event and an audit row against the destination; the `orgs` field
      on `GET /api/config`. Tests: the move and its role, the audit row and the event, a refused
      switch recording nothing, and the no-org / no-identity / no-database refusals.
- [x] 4 — `ServeState.eligible_orgs` answers every live org for a `users.role == "admin"` caller,
      read from `list_orgs` rather than stored. Tests: an admin eligible for every org with no
      `user_orgs` row of their own, and one able to switch into an org created after signing in.
- [x] 5 — The header `#orgbadge` gives way to an `#orgsw` `<select>` at two or more eligible orgs,
      and a switch reloads the page rather than refreshing one view. Tests: the switcher shipping
      hidden, exactly one of the two controls visible, and the reload wiring.
- [x] 6 — `docs/web-ui.md` and `docs/self-hosting.md`, with their `docs/ja/` mirrors: the selector,
      the endpoint, the sticky choice, the admin's whole-deployment eligibility, and what a switch
      leaves where it was.

## References

- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — the item
  that introduced the org, the `users.org_id` column, and `ServeState.org_of`.
- [BE-0313 — GitHub org membership and Team-based RBAC for serve](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)
  — the three membership axes this item turns from a first match into a set, and the per-login role
  recomputation it reuses per org.
- [BE-0352 — Admin GitHub Team env var bypasses the org-membership sign-in gate](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md)
  — the bypass that admits a login matching no org, and therefore the reason unit 4 exists.
- [BE-0375 — Database-backed org lifecycle and membership management for serve](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md)
  — the database-backed roster this item reads, and the item that names this one as its follow-up.
- [BE-0393 — Per-org config memory, restored into each session](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md)
  — the proposal that gives each org a configuration of its own, and therefore the item that closes
  the process-wide binding unit 6 records.
- [BE-0225 — Config project hub in serve](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)
  — the project switcher this item's selector sits beside in the header.
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) · [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)
  · [`bajutsu/serve/state.py`](../../bajutsu/serve/state.py) — the membership ranking, the OAuth
  callback, and the `org_of` seam this item changes.

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

An **org** is the tenant that a hosted `serve` deployment partitions everything by. The partition covers the runs, the evidence, the scenarios, the secrets, the registered projects, and the [targets](../../docs/glossary.md#target-app-device) a signed-in user may reach.

Today a user belongs to exactly one org. GitHub OAuth sign-in resolves a login to a single org and writes it to that user's `users.org_id` column. Every later request reads that one column through `ServeState.org_of`.

That single column is wrong for a user whose GitHub memberships match more than one org. The resolution picks the first match and never revisits the choice, so the other orgs are unreachable. They are also invisible: nothing in the web UI says they exist.

We propose that membership become a set and the org become a choice. Sign-in records every org a login belongs to, together with the role the login holds in each. The user picks the active org from a selector in the web UI header, in place of the org badge that already names the current tenant. The next sign-in honors the pick instead of overwriting it.

A single-org deployment sees no change. One eligible org renders the badge as it renders today.

## Motivation

Bajutsu's multi-tenant `serve` grew one axis at a time. BE-0015 introduced the org and gave each user a column naming theirs.

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) widened the ways a login can belong to an org:

- an explicit `members` entry
- membership in a GitHub organization the org claims under `githubOrgs`
- direct membership in a GitHub Team the org claims under `githubTeams` / `editorTeams`

[BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md) moved the whole membership roster into the database and gave admins a page to edit it. Each step made overlap more likely, and none made the overlap resolvable.

Overlap is not a corner case in the deployment shape those items describe. A consultancy runs one Bajutsu deployment for several client tenants and staffs an engineer on two of them. A platform team keeps an org of its own alongside the product org whose GitHub organization it also belongs to. In both cases the person is a legitimate member of two orgs, yet can work only in the one that sorts first.

BE-0375 says so in its own text and names the blocker: a login that belongs to several orgs must be able to choose between them, which needs `users.org_id` to stop being a single column.

The silence around the restriction is worse than the restriction. The header badge shows one org and gives no hint that a second exists. A user whose runs land in the wrong tenant cannot tell a misconfigured deployment from a tie-break that chose a different tenant. The two look identical from the browser, and the second is not a defect at all.

Once this ships, a user who belongs to two orgs signs in, opens the header selector, and picks the second org. The user then sees that tenant's runs, projects, and targets. After signing out and back in, the user lands on the picked org, not on the one the tie-break would have chosen. A user who belongs to one org sees the same header as today.

## Detailed design

### Terminology

- **Org**: a tenant, identified by a slug that doubles as its database row id.
- **Actor**: the GitHub login behind a request, recovered from the session.
- **Role**: one of `viewer`, `editor`, or `admin`. The role decides which endpoints an actor may reach.
- **Eligible org**: an org whose declared membership admits a given login.
- **Active org**: the single eligible org that the actor's requests currently resolve to. `ServeState.org_of` answers with it, and every tab shows its runs, evidence, scenarios, secrets, and projects.

### 1. Membership becomes a set, computed at sign-in

`bajutsu/serve/orgs.py` ranks the three membership axes in one place, `_match_org`, and returns the first org that matches. The ranking is:

1. an explicit `members` entry
2. an intersection with an org's `githubOrgs`
3. direct membership in one of the org's admitting Teams

We keep that ranking and change what the function returns. A new `orgs_for_identity` returns every matching org in the same ranked order. `org_for_identity` becomes its first element, and `identity_matches_org` becomes a test that the result is non-empty. No caller's behavior changes, because the head of the ranked list is the org the old function already returned.

The set must outlive the OAuth callback. That callback is the only place where a login's GitHub organization and Team memberships are known, and Bajutsu keeps no GitHub token afterward. Nothing later in the session can recompute them.

A new `user_orgs` table records the set. It holds one row per `(user_id, org_id)` pair, carrying the `role` that pair resolves to. The role belongs on the row, not on the user, because a role is per-org by construction: `editorTeams` promotes a member to `editor` within one org and says nothing about another.

Every sign-in replaces that user's rows wholesale. Recomputing instead of merging makes leaving a GitHub organization take effect on the next sign-in. BE-0313 gave the role the same self-healing property.

### 2. The active org is a choice that the next sign-in honors

`users.org_id` stays one column and keeps its meaning at every reader: the active org. What changes is who writes it.

Today `oauth_callback` overwrites the column on every sign-in with the first match. From now on it overwrites the column when the recorded value is no longer eligible. A user removed from the selected org lands on the head of the new eligible list. Otherwise the recorded choice stays.

Either way, sign-in writes `users.role` from the active org's own `editorTeams`, not from the first match's. A user active in one tenant therefore never carries a role that another tenant's `editorTeams` granted.

Preserving the column keeps this change small. `ServeState.org_of(actor)` is the single seam that every org-scoped read and write already goes through, from the run list to the per-org secret store. A selection stored where that method already reads needs no new plumbing. It also cannot leave one tab scoped to a different tenant than another.

Storing the choice on the user, not on the session, is deliberate. The alternative fails for a concrete reason, not a stylistic one. A session-scoped selection would have to travel in the session record. On a hosted deployment that record is a database row shared across replicas (`SqlSessionStore`), while `org_of` is keyed by actor and receives no session.

Threading the session through every caller of `org_of` is a far larger change than the feature justifies. It would also make the selection evaporate on every re-login, the opposite of what a user who works in one tenant all week wants.

### 3. Switching orgs

One new endpoint, `POST /api/org`, takes the target slug. It authorizes the switch against the `user_orgs` rows written at sign-in:

- A slug with no row for this user is refused with 403.
- A missing row set refuses everything instead of defaulting to permissive.
- An admin-Team member is the exception. Section 4 computes that member's eligible set at read time instead of storing it as rows.

On success the endpoint writes both `users.org_id` and `users.role` from the row, so the role follows the tenant as it does at sign-in.

The switch is an ordinary authenticated action, not an admin one. It moves the caller between orgs that already admit the caller, and grants nothing that the last sign-in did not already establish.

The switch inherits that sign-in's latency in both directions. A membership revoked since then keeps admitting a switch until the login next signs in, exactly as it keeps admitting the org the login already acts as. Every membership change in `serve` has this next-sign-in latency: BE-0313 recomputes the placement and the role on each login, and `update_org_membership` says so of its own writes.

The switch is still recorded like every other operationally significant act in `serve`:

- an `org.switch` event through `oplog`
- an audit row through `Repository.record_audit`, with the destination slug as the target

A later reader of the audit log needs to know which tenant an actor was in when the actor acted, and the sign-in record alone no longer answers that.

The boot read `GET /api/config` gains an `orgs` field alongside the `actor` and `org` it already returns. The field lists the eligible slugs, sorted by slug. The ranking decides where a login starts, so it does not enter the stored set, and the selector's order is stable.

Disclosing the slugs to the actor whose memberships produced them reveals nothing the actor could not learn by switching. The same reasoning already lets that endpoint return the actor's own login and active org, while the full org roster stays behind the admin-only `GET /api/orgs`.

### 4. A member of an admin Team is eligible for every org

A member of one of the server-wide admin Teams named by `BAJUTSU_OAUTH_ADMIN_TEAMS` can sign in even when no org's membership admits that member ([BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md)). The ranked eligible list of such a member is frequently empty. Left alone, the selector would hide from the very user the header badge was built for: the admin who administers several orgs and needs to see which one their own work lands in.

The eligible set of such a member is therefore every org the deployment has. The set is computed at read time from `Repository.list_orgs` and never stored as rows. A stored set would go stale the moment an admin creates an org on the Orgs page, which is the action such a user is most likely to take right before switching into the result.

The test for "is this an admin-Team member" is `users.role == "admin"`. The test holds for exactly those members, because `role_for` returns `admin` for admin-Team membership and for nothing else.

Switching leaves an admin's role at `admin`. That role comes from a server-wide Team, not from any org's membership.

### 5. The header selector

The web UI header already carries an org badge (`#orgbadge`). The boot read populates the badge, and the badge is hidden when no identity is signed in.

With two or more eligible orgs, the badge becomes a native `<select>` listing them. It sits beside the project switcher that already occupies that row and works the same way. With one eligible org, the badge stays the read-only badge it is today. A single-tenant deployment and a single-org user see no change at all.

Changing the selection posts to `POST /api/org` and reloads the page. The page reloads, instead of refreshing one view, because every tab is silently scoped to the active org. A partial update would leave the already-rendered views showing the previous tenant's runs and projects, the confusion this item exists to remove.

### 6. What a switch does not move

A switch changes where the actor's *next* action lands and moves nothing that already happened.

A run in flight keeps the org it was enqueued under. The job spec carries `job.org`, resolved once at enqueue and carried to a remote worker. Its evidence and its database row therefore land in the org that started it, even if the actor switches while it runs. Runs, audit entries, and uploaded bundles already recorded stay where they were recorded.

One pre-existing behavior becomes easier to reach, and we record it here so nobody has to discover it later. Binding a configuration sets the process-wide `state.config_org` from the binder's org (`bajutsu/serve/operations/config.py`). On a deployment where two orgs both bind configurations, the last binder wins for the whole server.

A switcher neither creates nor changes that behavior. It lets one actor reach the behavior without logging out. Fixing it means giving each org a configuration of its own, which is what [BE-0393](../BE-0393-per-org-config-memory/BE-0393-per-org-config-memory.md) proposes. This item stops at recording the interaction.

The two items are independent. An active-org selector works whether or not the binding is per-org. BE-0393's per-org memory is worth having whether or not a login can switch.

## Alternatives considered

**Refuse an ambiguous match at sign-in.** Turning away a login that matches two orgs would make the overlap loud instead of silent. It is the honest failure mode for a tie-break nobody chose. It also turns away logins that sign in successfully today, on deployments whose operators are content with the tie-break. The option trades a silent restriction for a hard regression.

**Order the orgs so the tie-break is intentional.** A position column on the org table would let an operator decide which org wins for a login matching several. This is a smaller change, but it answers a different question. It makes the *deployment* choose, when the user is the one who knows which tenant they mean today.

**Put the org in the URL or a request header.** Scoping each request to an org named by the client would allow two tabs in two tenants at once. It would also move the tenant boundary from one server-side seam to every request's parameters. A missing or forged value would then need a defense on each endpoint instead of in one method. That is a large surface to add for a convenience no user has asked for.

**Store the eligible set in the session instead of a table.** Session records already expire and already span replicas, so the lifetime is right, and re-login would refresh the set for free. The `SessionStore` protocol has three implementations (in-memory, Redis, and SQL), so the set would have to be threaded through all three. The switch endpoint would still need the per-org role, which is user state, not session state. The table costs one migration and touches one implementation.

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

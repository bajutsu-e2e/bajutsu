**English** · [日本語](BE-0375-serve-org-lifecycle-management-ja.md)

# BE-0375 — Database-backed org lifecycle and membership management for serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0375](BE-0375-serve-org-lifecycle-management.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0375") |
| Implementing PR | [#1636](https://github.com/bajutsu-e2e/bajutsu/pull/1636), [#1663](https://github.com/bajutsu-e2e/bajutsu/pull/1663), [#1664](https://github.com/bajutsu-e2e/bajutsu/pull/1664) |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md), [BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md), [BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets an admin create and delete a `serve` org and edit its membership. The admin works
from the web UI and its application programming interface (API), with no configuration edit and no
redeploy. An org is Bajutsu's multi-tenancy unit. The deployment's configuration file already
declares orgs under an `orgs:` block
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)).

The item moves an org's membership data from the configuration file into the database that `serve`
already runs against once one is wired (`BAJUTSU_DATABASE_URL`). Membership data covers two things:

- which GitHub login or GitHub organization can sign in as this org (`members`/`githubOrgs`)
- which GitHub Team its editors belong to, as `editorTeam` in
  [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)

Once the database holds an org's membership, it is the sole source consulted. The sign-in gate stops
reading the configuration file. A configuration that fails to load no longer turns every sign-in
into a denial.

The item also settles what an admin-created org means for target ownership, which stays in
configuration. An org's targets resolve per org rather than per name. Two orgs may each claim a
target of the same name. Configuration order no longer silently awards the name to one of them.

A deployment with no database keeps reading `orgs:` from configuration exactly as today. This item
changes nothing there.

## Motivation

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) replaced a set of
environment-variable login lists with GitHub's own organization and Team membership. A login list
duplicates a roster that GitHub already maintains, and it drifts from that roster on every hire,
leave, or transfer. BE-0313 left one roster in place: the `orgs:` block itself. The block decides
which GitHub organization or GitHub Team maps to which Bajutsu org, and who its editors are.

Nothing in GitHub corresponds to a Bajutsu org. An org is a boundary this deployment defines, and
this item could not read it off GitHub instead. Keeping the org as data is therefore not the
roster-drift problem that BE-0313 solved. The gap is where the data lives. Today it lives in a file
that a deploy operator edits by hand and ships through a redeploy or a `POST /api/config` rebind.
That is the same mechanism used to change which application `serve` tests. It was not built to
change which customers or teams `serve` serves.

The mechanism does not fit an operator who wants to onboard a new tenant, or move a team's edit
access to a different GitHub Team, without touching the deployment's configuration repository or
its CI/CD pipeline. Every other piece of per-org data already lives in the database once one is
wired:

- registered projects ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md))
- AI provider settings ([BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md))
- secrets
- an audit log

The `orgs` table itself already exists. `org_id` is a foreign key on the `projects`,
`provider_settings`, `secrets`, and `audit_log` tables
([`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)). Today the table is a
passive mirror of configuration. `ensure_org` creates a row with an id, a slug, and a name the first
time a login for that org signs in, and stops there. The row carries none of `members`,
`github_orgs`, or `editor_team`. Onboarding a tenant on a database-backed deployment still needs the
same file edit and redeploy that a database-less one needs. Meanwhile the deployment has a database
sitting idle for exactly this kind of operational, per-org data.

Moving the data also exposes two assumptions that the configuration file was quietly carrying. Both
hold only while orgs are rare and hand-written.

- **The file is always readable.** `oauth_callback` gates sign-in on the parsed `orgs:` block. A
  parse failure fails closed to an empty roster. One malformed line therefore denies every
  non-admin user on a deployment whose database already knows exactly who they are.
- **No two orgs ever claim the same target name.** A reviewer could enforce this across a single
  hand-edited file. No one can enforce it once an admin creates tenants from a web UI that shows no
  other tenant's entry.

Both are cheap to settle while the source of org data is being changed anyway, and neither gets
cheaper later.

## Detailed design

### Terminology

This item reuses "org," "GitHub organization," and "project" exactly as
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)'s own *Terminology*
subsection distinguishes them. It does not restate that distinction here. In short:

- An **org** is Bajutsu's own multi-tenancy unit (an `orgs:` entry).
- A **GitHub organization** is GitHub's identity source for who belongs to that org.
- A **project** is one config-source binding registered under an org. One org can hold many
  projects.

This item is scoped to the org itself, meaning its existence and its membership. The GitHub
organization it maps to and the project it holds stay unchanged.

### 1. Membership moves to the database; target ownership stays in configuration

`OrgConfig` ([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)) has four fields: `members`,
`github_orgs`, `editor_team`, and `targets`. The `targets` field lists which of the deployment's
configured [targets](../../docs/glossary.md#target-app-device) (its apps under test) this org owns.

This item moves the first three fields into three new columns on the `Org` table
([`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)). Together the three
fields are an org's **membership**: who can sign in as this org, and who among them can write. The
columns mirror `OrgConfig`'s own shape: `members` and `github_orgs` are JSON arrays of strings, and
`editor_team` is a nullable string.

`targets` stays in configuration, for two reasons:

- Which application a target names, and which backend and device it runs on, are exactly the
  app-specific differences that prime directive 3 (app-agnostic core) keeps out of any
  admin-editable store.
- A database-backed deployment already has a more actively maintained way to scope an application
  to an org: registering a project under it
  ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)). Moving `targets` too
  would duplicate that mechanism rather than complete this one.

A new function, `orgs_from_db(repository: Repository) -> dict[str, OrgConfig]`, sits beside the
existing `parse_orgs` in the same module. It queries every `Org` row and its membership columns. It
assembles the identical `dict[str, OrgConfig]` shape that `parse_orgs` already produces from YAML.
`targets` is always empty, because a database-sourced org owns no targets under this item.

Once a repository is wired, every membership consumer inside `oauth_callback` reads that
database-sourced dictionary. There is no exception and no fallback to the configuration-parsed one.
The membership consumers are:

- the sign-in gate's `identity_matches_org`
- the `org_for_identity` that places a signed-in user
- the `editor_team` read off the matched `OrgConfig` and passed as an argument into `role_for`
  ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py))
- the denial diagnostics that unit 3 re-reads for that source

Target resolution is the one consumer that keeps reading configuration. `targets_for_org` and the
`org_for_target` that `authz.py`'s `_target_forbidden` calls both need an org's actual target
ownership, and only the configuration-sourced dictionary carries it. Unit 4 covers what that
resolution has to become.

Routing membership through a second producer of the same dictionary shape applies a principle that
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) itself invoked when it
swapped a login list for GitHub Team membership. The policy is recomputed from its source on every
sign-in. Changing the source therefore needs no data migration for the resolution logic itself.

### 2. One org source per deployment, chosen once

`oauth_callback` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) reads the org model once,
near the top, and hands that one dictionary to every check below it. Today the dictionary is always
the `orgs:` block that `load_serve_config_file(state.config)` parses, on every deployment.

This item makes that single read conditional on the deployment's shape and on nothing else:

- `orgs_from_db` when a repository is wired (`state.repository is not None`). That is the same
  condition that already gates every other database-backed seam in `serve`.
- `parse_orgs` against the configuration file otherwise.

The consequence for a database-backed deployment is the one this item is named for. The database
decides who signs in, and the configuration file no longer participates in that decision at all.

The sign-in gate's *placement* is unchanged. The `members`/`github_orgs` match check still runs
before `org_for_identity` is ever called. It still sits above the `if state.repository is not
None:` block, so a database-less, OAuth-configured deployment keeps gating sign-in
([BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)). Only the dictionary
that the check consults now depends on whether a database is wired. The place in `oauth_callback`
where the check runs does not.

Choosing the source before the gate, rather than after it, also severs a coupling that is a live
operational hazard today. `load_serve_config_file` fails closed to `None`, `orgs` collapses to an
empty mapping, and `identity_matches_org` then matches nobody. On a database-backed deployment, a
configuration file that is unreadable, malformed, or not yet bound turns every non-admin sign-in
into a denial. Every one of those users, their orgs, and their roles are already rows in the
database.

After this item, an unloadable configuration on that deployment has two effects. It stops the
seeding of unit 6. It leaves target resolution (unit 4) with nothing to resolve against. It stops
nothing else: sign-in, org placement, and role assignment keep working from the database.

A database-less deployment keeps calling `parse_orgs` against the configuration file exactly as
today, with no admin UI for org management. Three facts explain why it gains nothing from a per-org
membership store:

- That deployment is local and single-user by construction
  ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s own motivation). It has
  no tenant boundary to administer.
- [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) already notes that
  `forbidden_for_role` short-circuits to full access there, regardless of role.
- A database-less org model exists to gate sign-in itself, not to grant a role.

### 3. Denial diagnostics, re-read for a database source

`_unmatched_org_cause` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) names which of five
shapes left a login unmatched. It also decides a log level. A denial or a bypass admission logs at
WARNING when `parsed is None or not orgs`. The reasoning: a configuration-shaped failure is an
operator's problem, and an ordinary roster miss is not.

Three of the five shapes are shapes that only a file can take:

- no configuration is bound yet
- the configuration failed to load
- the configuration declares no `orgs:` block

Neither those causes nor the level survive a database source unread. Carrying them over would tell
an operator to inspect a file that no longer decides anything.

On a database-backed deployment, those three collapse into one: no live `Org` row matches this
login. The failure that replaces them never reaches the cause classifier at all. This item gives
`orgs_from_db` the opposite failure mode from `load_serve_config_file`'s. A database error
propagates and does not fail closed to an empty mapping. When `serve` cannot read the database, it
answers with a 5xx that names the database. It does not deny every user with a message that blames
their GitHub membership.

What remains to report is one of three cases:

- No row in the `orgs` table declares any membership yet. Either nothing was created or seeded, or
  nothing admits anybody. Unit 7's bypass exists for this shape.
- GitHub returned no organizations for this login.
- A real roster does not match the login.

WARNING stays reserved for the operator-actionable case, a table nobody yet belongs to. The level
keys on membership, not on row count. This keeps it from decaying after one use. The bypass sign-in
that the WARNING reports calls `ensure_org` on its way past and leaves a passive `default` row
behind. A level keyed on an empty table would drop to INFO from the second sign-in on, while the
deployment still admits nobody but admin-Team members.

The recovery guard beside them is `not matched_org and parsed is None and state.config is not None`.
It keeps a bypass-admitted user's already-recorded org, instead of relocating the user to `default`
over one failed configuration load. This item removes the guard on this path and does not translate
it. The guard sits inside the `if state.repository is not None:` block, so it only ever ran on a
database-backed deployment. That is exactly the deployment whose org source stops being a file here.

The guard's motivating failure goes away with its cause:

- No configuration load can misplace a user whose org the database decides.
- `orgs_from_db` raises, and never silently presents an empty roster to be misread as one.

A database-less deployment never entered that block to begin with, so it loses no protection either.

### 4. A target's identity becomes (org, target)

Target ownership stays in configuration (unit 1). The way it is *resolved* does not survive an admin
who can create orgs at will. `org_for_target`
([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)) maps a bare target name to the first org
whose `targets` list names it. `_target_forbidden` forbids that target to every other org on exactly
that basis.

Two orgs that both name `checkout` therefore do not each get a `checkout`. Configuration order
silently awards it to whichever entry comes first, and the second org is forbidden every operation
on it. The second org is still shown the target, because `targets_for_org` lists a target whenever
the org's own entry names it. The result is a target that appears in an org's list and refuses every
read of it, with nothing in the configuration file saying which org won.

Under a hand-edited `orgs:` block, that collision was a mistake a reviewer could catch in the same
diff. Once unit 5 lets an admin create a tenant from the web UI, with no view of another tenant's
configuration entry, the collision becomes routine. Its symptom reads like a permissions bug, not
like the name clash it is.

This item therefore makes an org's target ownership resolve per org rather than per name.
`_target_forbidden` asks whether the target appears in *this* org's own list. It no longer asks
which single org the name resolves to. `org_for_target`'s global name-to-org lookup is left with no
caller. Two orgs may then each claim a target named `checkout`, and each is authorized for it.

`_target_forbidden` has to ask through `targets_for_org`, not by reading an `orgs:` entry itself.
`targets_for_org` needs no change, but its fallback keys on the literal `DEFAULT_ORG` slug, not on
"named in no entry":

- `default` gets every target that no entry claims.
- Every *other* org absent from the block falls to the `orgs.get(org) is None` branch and owns
  nothing.

Reading an entry directly would therefore forbid `default` every target it reaches today, since a
deployment typically declares no `default` entry at all.

Routing through `targets_for_org` does change one shape, in the direction of consistency. A
deployment that declares an org *literally named* `default` with its own `targets:` loses
authorization for the targets that entry names. `targets_for_org` decides `default` by its literal
slug before it ever reads an entry. Nothing usable is taken away. `targets_for_org` already refused
to list those targets for that org, so the retired name-based resolution authorized a target that
the same deployment never showed. The unclaimed-target fallback itself is untouched.

Under one bound configuration, the two orgs still share the one `targets:` definition that the name
resolves to. Giving each org a definition of its own needs a configuration bound per org. That is
where [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s per-project config
binding already points, and where this item deliberately stops.

A third behavior changes at the conversion. It is narrower than the two that this item's
Introduction names. It is recorded here instead of being left to discovery.

- `org_for_identity` resolves a login with no explicit `members` entry to the *first* org whose
  `githubOrgs` match.
- "First" is declaration order under `parse_orgs`, and slug order under `orgs_from_db` (`list_orgs`
  sorts by slug).
- Both orders are stable. The same login lands in the same org on every sign-in, which is what the
  tie-break has to guarantee. A deployment where two orgs name the same GitHub organization can see
  the landing org move once.

Two alternatives are both larger than a tie-break:

- Preserving declaration order would need a position column, and an answer for where an org created
  on the Orgs page sits.
- Refusing an ambiguous match outright would turn away logins that sign in today.

The real answer is to let a login that belongs to several orgs choose between them. That needs
`users.org_id` to stop being a single column, which is a separate item.

Making the *identity* `(org, target)` first keeps that later step from having to re-decide who owns
a name. BE-0225's project registry already uses the same key for a project: its `add` and `get` are
keyed by `(org_id, name)`
([`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py)).

The `orgs:` block decides ownership only for the configuration the operator **launched** the server
with. A configuration bound through the API (an uploaded bundle, a composed triple, or a Git
source) is bound *as* an org. `Upload.org` already records which org, and the bundle already lives
under that org's own cache prefix. Every target such a configuration declares belongs to that org
and to no other, and its `orgs:` block is not read at all.

Leaving that block authoritative would have been the same trust problem that this item already
refused for membership, one layer over: a file the deployment does not control deciding what a
tenant may reach. It also failed silently in practice. Consider a bundle uploaded by one org whose
`orgs:` claimed its only target for another. The uploader got an empty target list and no signal,
since `targets_for_org` then handed them only the targets nobody claimed.

One `ServeState.targets_for` answers this for all three readers: the target list, the cross-org
guard, and the per-org scenario store. A fourth reader cannot answer it differently.

That same `orgs.get(org) is None` branch decides what an org created through unit 5's API owns. The
answer is nothing. Such an org has no `orgs:` entry by construction, so `targets_for_org` returns an
empty list for it until someone adds an entry.

This item accepts that gap instead of closing it, and says so plainly. It is the one place where an
admin who onboards a tenant from the web UI still waits on a configuration edit. The org exists,
admits its members, and can be administered immediately. It is authorized for no target until an
`orgs:` entry names some. Closing the gap takes one of two changes:

- giving an org targets that an admin can set, which is the move into the database that unit 1
  rejects
- a configuration bound per org, which is the deferral recorded under *Alternatives considered*

Both changes are larger than this one. Both are better decided once admin-created orgs exist to
motivate them.

### 5. Admin API and UI: create, delete, and edit membership

Four endpoints exist, all admin-only. An org's membership decides who else can sign in and write,
the same sensitivity level that BE-0225 already gives registering or repointing a project. Each
mutation is recorded through the existing `Repository.record_audit` (`org.create` / `org.delete` /
`org.membership.update`, with the org's slug as `target`).

- `GET /api/orgs` — list every live org: slug, name, its `members` / `githubOrgs` / `editorTeam`
  themselves, and its project count. The response carries the rosters, not their sizes, because the
  membership form below replaces all three fields as one unit and so must start from the current
  values. The endpoint is admin-only, the same tier that can already read the whole `orgs:` block
  through `GET /api/config/content`.
- `POST /api/orgs` — create an org from `{slug, name}`. Membership starts empty (no member, no
  GitHub organization, no editor Team), so a freshly created org admits nobody until an admin adds
  to it.
  - The slug `default` is refused with a 409. Three separate places already read `default` as a
    fallback rather than a tenant. `org_for_identity` returns it for any login no org claims,
    every admin-Team bypass admission among them. `targets_for_org` decides it by the literal
    string before reading any entry. `delete_org` below refuses it outright. A real tenant created
    there would take the namespace an admin recovers through, and nothing could undo it through
    this API. The API refuses at the reversible end instead.
  - The created row's `id` is its slug, matching what every existing writer already does.
    `ensure_org(org, slug=org, name=org)` puts one string in all three columns. That same string is
    what `upsert_user(org_id=…)`, `state.org_of()`, and the org-scoped stores carry. So
    `orgs_from_db` keys its dictionary by it, exactly as `parse_orgs` keys by an `orgs:` entry
    name. A generated id distinct from the slug would instead need a slug-to-id lookup on every one
    of those paths, which this item does not introduce.
  - Creation marks the new row seeded immediately. This is the same per-row marker that unit 6 sets
    on cutover. The row is treated as already past cutover, so no later `orgs:` entry for that slug
    seeds or re-seeds it. A later entry therefore cannot overwrite membership that an admin sets
    through this API.
- `POST /api/orgs/<slug>/membership` — replace an org's `{members, githubOrgs, editorTeam}` as one
  unit. The endpoint refuses `default` for the same reason creation does. Membership is what places
  a login in an org, so a roster on the fallback slug makes it a tenant just as surely as creating
  it would. A bypass sign-in's `ensure_org` leaves a live `default` row, so the page lists the row
  and could otherwise reach it. The reservation has to hold on all three verbs, not two.
  the same granularity a config-file edit already has, rather than separate add/remove
  endpoints per list entry.
  - The endpoint is a `POST` and not the `PUT` that a whole-value write would normally take. `PUT`
    is the one body-carrying verb that neither transport implements: the stdlib handler serves
    `GET`/`POST`/`DELETE`, and the FastAPI generator parses a body only for `POST`. Spelling the
    route `PUT` would mean widening both transports for this one route, a cross-cutting change that
    this item has no other need for.
  - Every other whole-value write in `serve` is already a `POST`: `/api/projects/<name>/activate`
    and `/api/provider`.
- `DELETE /api/orgs/<slug>` — delete an org. The endpoint rejects with 409 while the org still owns
  any project (`list_projects` non-empty). An admin must deregister the org's projects first
  (BE-0225's deregister, which retains their run history).

The delete endpoint also has these rules:

- **It rejects the `default` org outright.** `default` is the fallback slug that `serve` hardcodes
  (`DEFAULT_ORG`, [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)). An unmatched bypass
  sign-in keeps resolving to it regardless of table state. Removing it would only leave a
  soft-deleted org that a bypass user keeps landing on.
- **Deleting an emptied, non-default org soft-deletes it.** The `Org` row is marked deleted
  through a `deleted_at` column, the same soft-delete shape that `runs` already uses for a trashed
  run. The row is kept. A hard delete would violate the foreign key that `users`, `runs`, `secrets`,
  `provider_settings`, and `audit_log` still hold on the org's id, even once every project is
  deregistered ([`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)). The
  delete's own audit-log entry would then have nothing left to point at. Soft-deleting the row
  leaves every one of those foreign keys intact.
- **Retiring revokes sessions.** The endpoint revokes every session bound to a login recorded
  under that org. It does so through a `revoke_identities` method added to the `SessionStore` seam
  ([`bajutsu/serve/sessions.py`](../../bajutsu/serve/sessions.py)) and implemented by each of its
  stores. The soft delete alone reaches only the *next* sign-in. `users.org_id` still names the
  retired slug, so a cookie issued beforehand would keep listing that tenant's runs, triggering new
  ones, and reading its secrets until it expired.
- **The revocation gap is newly reachable.** Retiring a tenant used to mean a configuration edit
  plus a redeploy, and the restart dropped every session as a side effect. Making retirement an
  in-process admin action removes that incidental revocation. This item restores it deliberately.
  A session carrying no identity (a shared-token login) belongs to no org and is never touched.
- **Deleted orgs disappear from resolution.** Sign-in resolution and `GET /api/orgs` exclude a
  deleted org from then on, so it can no longer be matched or listed. A user, secret, or provider
  setting still recorded under that org id keeps its row and stays queryable, but admits no new
  sign-in as that org.
- **History is retained.** The org's historical runs and audit-log entries stay under the deleted
  org's id, exactly as a deregistered project's run history survives deregistration
  ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)). An admin action removes
  a tenant's ability to sign in and act. It does not remove the record of what the tenant already
  did.

Re-creating an org at a slug that was soft-deleted is out of this item's scope. `Org.slug` carries a
UNIQUE constraint end-to-end ([`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)),
and a soft-deleted row still occupies it. `POST /api/orgs` therefore rejects that slug with a
dedicated 409 error, and neither reactivates nor reuses the row. Reactivation, should it ever be
needed, is a separate, explicit operation left to a future item. Unit 6's backfill likewise skips
any row already marked deleted (`deleted_at` set). It neither seeds nor revives such a row, because
a soft-deleted org is retired, not merely unseeded.

The org a session acts as is also surfaced outside that page, in the shell header beside the bound
configuration's name. `GET /api/config` is the boot read that every tab already starts from. It
gains the caller's own `actor` and the `org` it resolves to, and the header shows the org with the
login on hover.

Every tab is silently scoped to that org: which targets are runnable, which runs and evidence are
visible, the secrets, and the project list. An admin administering several tenants could otherwise
only infer which tenant their own work lands in. Returning a caller's own identity to that caller
discloses nothing they did not present. The roster itself differs and stays behind the admin-only
`GET /api/orgs`. Both fields are null where there is no signed-in identity (a local or
shared-token `serve`, where `org_of` would answer `default` for everyone), and the header omits the
badge there.

A new admin-only **Orgs** page joins the serve shell, parallel to
[BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page.md)'s
Projects page. It has:

- a list of orgs with create and delete actions (delete is disabled, with the project count shown,
  while the org still owns a project)
- a membership form per org (members, GitHub organizations, editor Team) that calls the `POST`
  endpoint above

Each row renders the display name that the create form collects whenever it differs from the slug.
No endpoint can change `name` afterwards, so a display name that the page never showed would leave
a typo permanent and invisible. Both the API and the page exist only when a repository is wired,
following unit 2's gate.

### 6. One conversion at startup, then a hard cutover

A deployment upgrading from configuration-only org membership needs its existing `orgs:` block
represented in the database before this item's admin UI has anything to show or edit. `serve` does
that once, at startup (`serve()`, after `_configure_oplog` so the warning below reaches the live log
sink). The conversion reads the configuration this server was **launched** with. It runs only while
the `orgs` table holds no row at all, a soft-deleted row included.

For each entry in that block declaring any membership, a dedicated seeding method creates or fills
the row's `members`/`github_orgs`/`editor_team` and stamps a persisted per-row marker. The method is
not `ensure_org`, for three reasons:

- `oauth_callback` and job completion already call `ensure_org(org, slug=org, name=org)` on every
  sign-in and every finished run.
- Those calls have no membership to pass. On a database-backed deployment, where unit 2 makes the
  database the source, they could pass none.
- Widening `ensure_org` into a create-or-update would give one method two meanings, depending on
  which argument is omitted. Or it would let the next sign-in clear membership that an admin set
  through unit 5's membership endpoint.

The per-row marker stays for the same reason it always did. It tracks each row's converted state
directly. An admin who intentionally empties an org's membership is therefore not mistaken for a row
never yet written, and is not re-seeded from a stale configuration value.

**No API bind seeds, and no second startup does.** `bind_config`, `bind_git_config`, the upload and
compose binds, and the project switcher all accept a configuration whose content the deployment
does not own. [BE-0121](../BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist.md)
says as much of the same file's `build:`, which stays ungoverned until `--allow-remote-build` opts
in. Seeding from such a configuration would hand that file authority over who may sign in.

Before this item, that authority existed but was live and reversible. `oauth_callback` re-read the
file on every sign-in, so rebinding to a clean configuration revoked the grant on the next login. A
seeded row outlives the bind, so the same rebind would no longer revoke anything. An admin binding
`github:someone/repo@main` whose `orgs:` declares `partners: {githubOrgs: [attacker-gh]}` would
otherwise grant every member of `attacker-gh` a permanent sign-in.

The empty-table condition closes the same hole from the other side. A restart carrying an edited
configuration cannot add a tenant, revive a retired one, or reshape an existing one behind an
admin's back. One boot converts a deployment. From then on, the database is the sole author of its
own roster, and an org first declared after that is created on the Orgs page.

The cost is named rather than hidden. An org declared only in a configuration bound through the API
admits nobody until an admin creates it. That is the deliberate trade. The alternative is letting a
file the deployment does not control decide who signs in. The Orgs page makes the recovery a
one-step action and not a redeploy.

The Alembic migration adds the `members`/`github_orgs`/`editor_team` columns, and the marker and
soft-delete columns. It does only that: it adds columns and seeds no data. Seeding is `serve`'s own
job at startup. The migration cannot seed, because Alembic's migration environment
([`bajutsu/serve/server/migrations/env.py`](../../bajutsu/serve/server/migrations/env.py)) resolves
only `BAJUTSU_DATABASE_URL`. It never has access to a bound configuration's `orgs:` block.

After the conversion, `oauth_callback` reads membership from the database exclusively. A later edit
to the `orgs:` block has no effect on it. This matches the hard cutover that
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) and
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) both chose
over keeping two independent sources for the same role-deciding data (see *Alternatives
considered*).

`serve` warns, once per boot, whenever all three conditions hold:

- a repository is wired
- a configuration is bound
- that configuration's `orgs:` block has an entry still declaring any of the membership fields
  (`members`/`githubOrgs`/`editorTeam`)

An `orgs:` entry that carries only `targets` stays legitimate and is expected to remain, since
target ownership stays in configuration (unit 1). Warning on a merely non-empty `orgs:` block would
have one of two bad effects. It would fire forever on a correctly configured deployment. Or it would
push an operator to empty `orgs:` and lose the target-ownership data.

The seeding itself also skips a `targets:`-only entry, rather than writing it as an empty roster.
This item's own documentation recommends paring the configuration down after the converting boot.
Doing so before the converting boot must not be the irreversible act of fixing every org at
"admits nobody".

The warning is the same "an operator forgot this is no longer read" signal that
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) already
gives for its own retired environment variable.

A fresh deployment has a repository wired from its first boot and no prior configuration-only
history. It converts against whatever `orgs:` block that first boot binds, typically empty. Every
org it has from that point on is one that an admin creates through unit 5's API.

### 7. The admin-Team bootstrap bypass answers the empty-table case

A freshly created, database-backed deployment has no org until an admin creates one. This raises the
bootstrap question that
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) already
answered for a broken or absent `orgs:` block: who signs in to create the first org?

`BAJUTSU_OAUTH_ADMIN_TEAMS` stays an environment variable, deliberately outside this item's move
into the database. It is the recovery path for exactly the case in which the org model itself,
wherever it lives, cannot yet admit anyone. A member of a configured admin Team clears the sign-in
gate through that bypass regardless of how many `Org` rows exist. The member signs in with the admin
role, which this item leaves unaffected ([BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)'s
"admin stays one server-wide tier"). The member then uses unit 5's API to create the deployment's
first org.

There is no chicken-and-egg problem. The one piece of tenancy data that this item deliberately
leaves outside the database is exactly the piece that makes an empty `orgs` table recoverable.

### 8. A GitHub Team is a membership axis of its own (follow-up)

Added after this item shipped. It changes the same four membership fields that units 1, 5, and 6
move into the database, and would otherwise leave that set inconsistent across the two producers.

An org gains `githubTeams`: a list of flat GitHub Teams, each written `"<github-org>/<team-slug>"`.
The direct members of those Teams belong to the org at **viewer**. Until now, an org could admit a
login in two ways, with nothing in between:

- naming the login under `members`
- naming its whole GitHub organization under `githubOrgs`

Admitting one team of a shared organization meant one of two things. Either it meant a
hand-maintained roster that decays as people join and leave. Or it meant opening the deployment to
every other team in the organization. The Team is the unit that GitHub already models. `serve`
reads a login's Teams on every sign-in for the editor/admin roles. The membership was on hand, and
only the gate declined to consult it.

`editorTeam` now admits as well as promotes. It names the Team allowed to run, record, and edit
scenarios, which is the strongest per-org grant. Yet it admitted nobody. A Team named only there
resolved to "may write but cannot sign in", and every member was turned away at the gate that runs
before any role is computed. `OrgConfig.admitting_teams()` returns the union of `github_teams` and
`editor_team`. The gate and every "does this org declare a membership" check read this one
accessor, unit 6's cutover warning included. They cannot disagree about whether `editorTeam` alone
admits anyone.

Adding the axis to the gate alone would have caused two faults. It would have admitted a login
through one org's Team and then filed it under another. That hands the login the other tenant's
targets and object-storage prefix (unit 4). It also reads the wrong org's `editorTeam` for the
role, so a login admitted by its editor Team would land as viewer.

`identity_matches_org` and `org_for_identity` therefore delegate to one private `_match_org`. It
ranks `members`, then `githubOrgs`, then an org's admitting Teams, and returns the matching org or
None. Teams rank last, so declaring one never relocates a login that an existing entry already
placed.

The one Team comparison behind the gate, the editor role, and the admin Team is now
`orgs.in_teams`. It lowercases both sides, as GitHub itself resolves an org login and a Team slug.
BE-0313 left the `editorTeam` check exact, because a mismatch cost the editor role alone. Once that
field admits, the same mismatch costs sign-in.

The comparison uses `str.lower` and not `str.casefold`. Full folding equates names that GitHub keeps
distinct (`gruß` and `gruss` can be two Teams of one organization). A match now buys sign-in, so
anyone able to create the folded-equal Team would clear the gate. Lowercasing preserves the
nested-Team guarantee, which rests on exact equality of the whole `"<github-org>/<team-slug>"`.

The Team axis is a required parameter on both resolvers, not a defaulted one. A call site able to
omit it is how the gate and the placement would come to consult different axes again. It would
type-check clean while denying or misplacing a Team-admitted login.

`org_for_user` is retired here, for the reason that unit 1 retired `org_for_target`. `_match_org`
scans `members` itself, so the helper kept only its pre-BE-0313 semantics and had no caller.

The database side follows units 1 and 5 unchanged. A nullable `orgs.github_teams` JSON column
(migration `0016`, adding a column and seeding nothing, like `0015`) is carried through `OrgRecord`,
`set_org_membership`, `seed_org_membership`, and `orgs_from_db`. It is replaced as one unit with the
other three through `POST /api/orgs/<slug>/membership` and the Orgs page's form. `editor_team`
keeps its own column, since it decides a role as well as admitting.

Unit 3's denial cause names the Team list too. It does so only when the list is *also* empty
("GitHub returned no orgs or teams for this login"). Blaming the org list alone would send an
operator to an axis that a Team-declared org never consults.

`/user/teams` fails closed. While GitHub's Teams API errors, a login whose only membership is a Team
is turned away. That is the direction a gate has to fail in, and the trade that unit 7's bypass
already accepted.

### 9. The writing Team becomes a list (follow-up)

`editorTeam` becomes `editorTeams`, a list of flat GitHub Teams. Unit 8 gave an org a list of
admitting Teams and left the writing Team a single slot. The other two membership axes had already
outgrown that shape. `githubOrgs` and `githubTeams` are lists because one org may span more than
one GitHub organization. An org shaped that way cannot then name a writing Team in each.

Two ways out existed, and both fail:

- Merge the Teams on GitHub's side.
- Keep one roster by hand. This is the manual maintenance that
  [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) removed.

Naming both Teams under `githubTeams` is not a third way out. That field admits at viewer and grants
nobody write access.

[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) reached the
same conclusion for the server-wide admin Team. It widened `BAJUTSU_OAUTH_ADMIN_TEAM` into
`BAJUTSU_OAUTH_ADMIN_TEAMS`, so one deployment could name an admin Team per GitHub organization.

`OrgConfig` folds a retired singular `editorTeam` key into `editor_teams` and does not refuse it.
This is where the rename departs from BE-0352's. BE-0352 could retire an environment variable
outright, because an unread variable still leaves a configuration that loads. `OrgConfig` is
`extra="forbid"`, so an un-renamed key raises out of `parse_orgs`. `load_serve_config_file` answers
a parse failure with no org model at all, which turns away every login under "user not allowed". A
deployment that missed one key would lock itself out silently. That is the failure BE-0352's
retired-name warning exists to prevent.

The folding has two edge rules:

- A configuration carrying both spellings keeps both Teams. An operator who adds the plural name and
  leaves the singular one behind is the likelier partial rename. Neither spelling should lose the
  role it was written to grant.
- An `editorTeam: ""` folds to nothing. An empty value meant "no editor Team" before the rename, and
  an entry matching no Team would still make `orgs_declaring_membership` report a roster.

The list simplifies the two places that special-cased the single slot. `admitting_teams()` becomes a
plain concatenation of `github_teams` and `editor_teams`. `role_for` drops its `is not None` guard,
since `in_teams` on an empty list is already False.

`POST /api/orgs/<slug>/membership` drops its bespoke "must be a string" check for the shared
`_string_list` validator that the other three fields use. The endpoint now validates four uniform
lists.

The endpoint refuses a retired `editorTeam` field with a 400 and does not fold it. This is the
opposite choice from the configuration surface, and it rests on what refusing costs. `_string_list`
reads a missing `editorTeams` as an empty list. Obeying such a body would strip the org's write
access and answer 200. Refusing costs one request that an operator can fix. Refusing a configuration
key would instead cost every login of the deployment.

Migration `0017` is the first in this series to carry data and not only add columns. Migrations
`0015` and `0016` seeded nothing, because every column they added is reconstructible from the
`orgs:` block. An `editor_team` is not reconstructible. A write through
`POST /api/orgs/<slug>/membership` stamps `membership_seeded_at`, after which `seed_org_membership`
is a no-op forever. That value lives in its row and nowhere else. Dropping the column without the
copy would demote every editor of such an org to viewer, and say nothing.

The downgrade copies back the first entry only. That is the one direction in which a widening is
lossy. Refusing to downgrade a row holding two Teams would instead leave an operator no way back
from the state that this unit makes reachable.

## Alternatives considered

- **Keep org membership in the configuration file, and let an admin trigger a `POST /api/config`
  rebind after editing it out-of-band.** Rejected. This is today's mechanism, and it is exactly the
  dependency this item exists to remove. Every tenant onboarded, and every team moved to a different
  GitHub Team, needs a person with write access to the deployment's configuration repository and a
  redeploy or rebind.
- **A registry seam with both a database-backed and a local, file-backed path, mirroring
  [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s `ProjectRegistry`, so a
  database-less `serve` also gets admin-managed org membership.** Rejected. A database-less `serve`
  is single-user and local by construction. It has no tenant boundary for an admin to manage and no
  role differentiation to protect (unit 2). A second persistence path for a feature that this
  deployment shape has no use for would be pure surface area. `ProjectRegistry` differs: a
  single-user local hub genuinely needs it (multiple configs, not multiple tenants).
- **Move `targets` (an org's target ownership) into the database alongside `members`,
  `github_orgs`, and `editor_team`.** Rejected for this item. `targets` names which application a
  target is and how it runs. That is app-specific data, which prime directive 3 keeps in
  configuration and out of an admin-editable operational store. A database-backed deployment already
  has a differently shaped, more actively maintained mechanism for scoping an application to an org:
  [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s project registry.
- **Leave target ownership resolved by name, and reject a `POST /api/orgs` whose org would collide
  with another org's target names.** Rejected. The collision is not the admin's to avoid. The target
  names an org claims live in the configuration file. The admin creating the org has no view of that
  file. On a deployment that moved membership out of the file precisely so admins need not read it,
  the admin also has no reason to read it. Validating the collision away would make org creation
  fail for a reason unrelated to the org. Unit 4 instead makes the two orgs simply coexist.
- **Give each org its own `targets:` definitions by binding a configuration per org, rather than
  only making the identity `(org, target)`.** Deferred, not rejected. It is the natural end state,
  and [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s per-project config
  binding already points at it. It needs `serve` to hold more than one bound configuration at a time,
  and `state.config` is a single path today. That is a change to config binding, not to the org
  model, and it belongs with the item that makes it.
- **Cascade-delete an org's projects, runs, and audit-log entries when the org itself is deleted.**
  Rejected. The cascade would mirror BE-0225's own choice to retain a project's run history when the
  project is deregistered. An audit log exists precisely so that an admin action is never itself the
  reason a record disappears.
  [BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md)'s future
  per-org dispatch fairness work will also want historical data to stay queryable, whether or not
  the org that produced it still exists.
- **Consult configuration and the database together at every sign-in (a per-request fallback)
  instead of a one-time backfill followed by a hard cutover.** Rejected. BE-0313 and BE-0352 both
  already rejected keeping two independent sources for the same sign-in-deciding data. They did so
  for the roster-drift reason that BE-0313's own motivation states. This item keeps that precedent
  and does not reopen it for org membership specifically.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] 1 — Add `members`/`github_orgs`/`editor_team` columns to the `Org` table (Alembic migration)
      and an `orgs_from_db` builder that assembles the same `dict[str, OrgConfig]` shape
      `parse_orgs` produces from configuration, with `targets` always empty.
- [x] 2 — Choose `oauth_callback`'s org source once, before the sign-in gate: `orgs_from_db` when
      `state.repository is not None`, `parse_orgs` otherwise, with no caller reading both and none
      falling back between them; a database-less deployment's configuration-sourced path is
      unchanged.
- [x] 3 — Re-read the denial diagnostics for a database source: `_unmatched_org_cause`'s three
      configuration-shaped causes collapse to "no live `Org` row matched," `orgs_from_db` propagates
      a database error instead of failing closed to an empty mapping, WARNING keys on a table in
      which no row declares membership yet, and the `parsed is None` org-recovery guard is removed
      rather than translated.
- [x] 4 — Resolve target ownership per org rather than per name; let an API-bound configuration's
      `orgs:` block decide nothing, since such a bind is made *as* an org and that org owns every
      target the file declares, answered for every reader by one `ServeState.targets_for`; and
      record the slug-order tie-break as a third, narrower conversion-time change.
      `_target_forbidden` asks whether
      the target is in this org's own `targets_for_org` list, leaving `org_for_target` with no
      caller (through `targets_for_org`, so `default` keeps the unclaimed targets its literal-slug
      fallback gives it), so two orgs
      may each claim a target of the same name instead of configuration order awarding it to one and
      forbidding the other a target it is still shown.
- [x] 5 — The four `/api/orgs…` endpoints (admin-only, each mutation recorded through `record_audit`)
      and the Orgs admin page (create / delete-when-empty / edit membership, each row rendering the
      display name when it differs from the slug), gated
      on a repository being wired; `POST /api/orgs` refuses the reserved `default` slug and marks the
      new row seeded at creation, so no later `orgs:` entry re-seeds it; delete is a soft delete
      (`Org.deleted_at`) that excludes the org from sign-in resolution and `GET /api/orgs` without
      removing its row or violating any foreign key that still points at it, and revokes every
      session its members hold through the `SessionStore` seam's new `revoke_identities`; `GET
      /api/config` reports the caller's own `actor` and `org`, which the shell header shows as a
      badge beside the bound configuration's name.
- [x] 6 — The one-time conversion from the launch configuration's `orgs:` block into the database:
      at startup only, and only while the `orgs` table holds no row at all (a soft-deleted one
      included), so neither an API-bound configuration nor a later restart carrying an edited one
      can write who may sign in; guarded additionally by a persisted per-row marker on each `Org`
      row rather than inferred from empty membership columns; a `targets:`-only entry skipped rather
      than written as an empty roster; the Alembic migration adds only the membership, marker, and
      soft-delete columns and runs no seeding of its own; the boot warning when an `orgs:` entry
      still declares `members`/`githubOrgs`/`githubTeams`/`editorTeam` (an entry that carries only
      `targets` stays expected and does not warn).
- [x] 7 — Confirm the admin-Team bypass still admits sign-in against an empty or wholly
      unmatching `orgs` table, now that the table rather than the configuration file decides the
      gate, and leave `BAJUTSU_OAUTH_ADMIN_TEAMS` in the environment.
- [x] 8 — Tests: `orgs_from_db` round-trips the same resolution behavior `parse_orgs` gives for an
      equivalent `orgs:` block; the database-less path is unaffected; a configuration that fails to
      load no longer denies sign-in on a database-backed deployment, and a database error surfaces
      as a 5xx rather than a denial; two orgs each claiming a target of the same name are each
      authorized for it; org creation, membership replacement, and delete-while-non-empty through
      the API, all admin-only and audited; the backfill runs once and a later configuration edit to
      `orgs:` has no effect after it; the admin-Team bypass
      ([BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md))
      still admits sign-in and lets that admin create the deployment's first org against an empty
      `orgs` table.
- [x] 9 — Follow-up: add `githubTeams` as an org's third membership axis and let `editorTeam` admit
      as well as promote, ranking all three axes once in `orgs._match_org` so the sign-in gate and
      the org placement cannot admit a login through one org and file it under another, with the
      Team axis required rather than defaulted on both resolvers and `org_for_user` retired; lowercase
      the one Team comparison (`orgs.in_teams`) shared by the gate, the editor role, and the admin
      Team;
      carry the field through migration `0016`, the repository seam, `orgs_declaring_membership`,
      the membership API, and the Orgs page; and name the Team list in unit 3's denial cause when
      GitHub returned neither axis. Tests: a Team-only login admitted *and placed in its Team's org*,
      an `editorTeam`-only login admitted as editor, a case-mismatched Team, a nested Team refused,
      a Team-only login denied while `/user/teams` returns nothing, and the config/database
      resolution parity of unit 8 extended to the Team axis.

- [x] 10 — Follow-up: widen `editorTeam` into `editorTeams`, a list, so an org spanning more than one
      GitHub organization can name a writing Team in each; fold a retired singular `editorTeam` key
      into the list rather than refusing it, since `OrgConfig` is `extra="forbid"` and a parse
      failure leaves `serve` with no org model and every login denied; carry the field through
      migration `0017` — the first here to copy data, since an `editor_team` set through the API is
      reconstructible from nothing — the repository seam, `role_for`, the membership API's shared
      `_string_list` validation, and the Orgs page. Tests: a second `editorTeams` entry both admits
      and promotes, the retired key folds (alone, beside the plural one, and as an empty value), and
      migration `0017` carries a pre-rename row's single Team into a one-element list while leaving a
      row that never had one untouched.

## References

- [BE-0015 — Public hosting of the web UI](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) —
  the `orgs:` multi-tenancy model this item moves into the database, and the `projects`/`runs`
  schema whose `org_id` foreign key this item's `Org` table already anchors.
- [BE-0313 — GitHub org membership and Team-based RBAC for serve](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) —
  the `members`/`githubOrgs`/`editorTeam` fields this item relocates, the *Terminology* subsection
  this item reuses, and the "recompute every login, no data migration" principle this item's
  `orgs_from_db` follows for its new source.
- [BE-0352 — Admin GitHub Team env var bypasses the org-membership sign-in gate](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) —
  the admin-Team bootstrap bypass this item leaves untouched, and the hard-cutover precedent this
  item's backfill follows.
- [BE-0225 — Config project hub in serve (register, list, switch, run)](../BE-0225-config-project-hub/BE-0225-config-project-hub.md) —
  the project registry this item's *Alternatives considered* points to as the existing mechanism
  for scoping an application to an org, and the deregister-retains-history precedent this item's
  org deletion follows.
- [BE-0275 — Projects management page in serve](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page.md) —
  the admin page this item's Orgs page is modeled on.
- [BE-0170 — Weighted fair org dispatch](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md) —
  a `Proposal` this item's admin-created orgs make more consequential, since a dynamically growing
  set of tenants is exactly the shape that item's fairness scheme is for.
- [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py) — `OrgConfig` and `parse_orgs`, which this
  item adds a second producer beside (`orgs_from_db`); `identity_matches_org` and `org_for_identity`
  changed only their source in units 1–7 and now share unit 8's `_match_org`, beside which
  `in_teams` holds the one Team comparison; `org_for_target` loses its only caller here, and
  `org_for_user` in unit 8.
- [`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py) — the `(org_id,
  name)` key a project already carries, which this item's target identity follows.
- [`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py) — the `Org` table this
  item adds membership columns to, `github_teams` among them (unit 8's migration
  [`0016`](../../bajutsu/serve/server/migrations/versions/0016_org_github_teams.py)).
- [`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py) — `_fetch_teams`, whose
  fail-closed behavior decides sign-in for a Team-declared org from unit 8 on, not only for unit 7's
  admin bypass.
- [`bajutsu/serve/server/db.py`](../../bajutsu/serve/server/db.py) — `Repository.ensure_org`, which
  this item leaves an idempotent create and adds a separate seeding method beside, and the
  `ProjectRecord`/`create_project`/`delete_project`
  shape this item's `OrgRecord` and its create/delete/membership-update operations mirror.
- [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py) — `oauth_callback`'s sign-in gate, whose
  data source (not placement) this item changes when a repository is wired; `_unmatched_org_cause`,
  whose causes this item re-reads for that source; and `_target_forbidden`, which this item
  re-resolves per org.
- [`bajutsu/serve/operations/projects.py`](../../bajutsu/serve/operations/projects.py) — the
  endpoint and RBAC shape this item's `/api/orgs…` endpoints follow.

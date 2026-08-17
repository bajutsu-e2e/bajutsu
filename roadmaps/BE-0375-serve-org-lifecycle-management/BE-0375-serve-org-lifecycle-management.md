**English** · [日本語](BE-0375-serve-org-lifecycle-management-ja.md)

# BE-0375 — Database-backed org lifecycle and membership management for serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0375](BE-0375-serve-org-lifecycle-management.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0375") |
| Implementing PR | [#1636](https://github.com/bajutsu-e2e/bajutsu/pull/1636) |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md), [BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md), [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md), [BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets an admin create and delete a `serve` org — Bajutsu's multi-tenancy unit, already
declared under an `orgs:` block in the deployment's configuration file
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)) — and edit its
membership, from the web UI and its application programming interface (API), instead of editing
that configuration file and redeploying. It moves an org's
membership data — which GitHub login or GitHub organization can sign in as this org
(`members`/`githubOrgs`) and which GitHub Team its editors belong to (`editorTeam`,
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)) — from the
configuration file into the database `serve` already runs against once one is wired
(`BAJUTSU_DATABASE_URL`). Once that database holds an org's membership it is the only thing
consulted for it: the sign-in gate itself stops reading the configuration file, so a configuration
that fails to load no longer turns every sign-in into a denial. The item also settles what an
admin-created org means for target ownership, which stays in configuration — an org's targets
resolve per org rather than per name, so two orgs may each claim a target of the same name instead
of configuration order silently awarding it to one of them. A deployment with no database keeps
reading `orgs:` from configuration exactly as today; this item changes nothing there.

## Motivation

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) replaced a set of
environment-variable login lists with GitHub's own organization and Team membership, because a
login list duplicates a roster GitHub already maintains and drifts from it on every hire, leave,
or transfer. That item left one roster in place: the `orgs:` block itself, which decides which
GitHub organization or GitHub Team maps to which Bajutsu org and who its editors are. Nothing in
GitHub corresponds to a Bajutsu org — it is a boundary this deployment defines, not one this item
could instead read off GitHub — so keeping it as data is not the roster-drift problem BE-0313
solved; the gap is only where that data lives. Today it lives in a file a deploy operator edits by
hand and ships through a redeploy or a `POST /api/config` rebind, the same mechanism used to change
which application `serve` tests, not which customers or teams it serves.

That mechanism does not fit an operator who wants to onboard a new tenant, or move a team's
edit access to a different GitHub Team, without touching the deployment's configuration
repository or its CI/CD pipeline. Every other piece of data this deployment already treats as
per-org already lives in the database once one is wired: registered projects
([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)), AI provider settings
([BE-0229](../BE-0229-per-org-provider-settings-resolution/BE-0229-per-org-provider-settings-resolution.md)),
secrets, and an audit log. The `orgs` table itself already exists — `org_id` is a foreign key on
the `projects`, `provider_settings`, `secrets`, and `audit_log` tables
([`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)) — but today it is a
passive mirror of configuration: `ensure_org` creates a row with an id,
a slug, and a name the first time a login for that org signs in, and stops there — it carries none
of `members`, `github_orgs`, or `editor_team`. Onboarding a tenant on a database-backed deployment
still needs the same file edit and redeploy a database-less one does, even though the deployment
already has a database sitting idle for exactly this kind of operational, per-org data.

Moving that data also exposes two assumptions the configuration file was quietly carrying, both of
which hold only while orgs are rare and hand-written. The first is that the file is always
readable: `oauth_callback` gates sign-in on the parsed `orgs:` block, and a parse failure fails
closed to an empty roster, so one malformed line denies every non-admin user on a deployment whose
database already knows exactly who they are. The second is that no two orgs ever claim the same
target name, which a reviewer could enforce across a single hand-edited file and no one can enforce
once an admin creates tenants from a web UI that shows no other tenant's entry. Both are cheap to
settle while the source of org data is being changed anyway, and neither gets cheaper later.

## Detailed design

### Terminology

This item reuses "org," "GitHub organization," and "project" exactly as
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)'s own *Terminology*
subsection distinguishes them, and does not restate that distinction here. In short: an **org**
is Bajutsu's own multi-tenancy unit (an `orgs:` entry); a **GitHub organization** is GitHub's
identity source for who belongs to that org; a **project** is one config-source binding registered
under an org, and one org can hold many projects. This item is scoped to the org itself — its
existence and its membership — not to the GitHub organization it maps to (unchanged) or to the
project it holds (unchanged).

### 1. Membership moves to the database; target ownership stays in configuration

`OrgConfig` ([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)) has four fields: `members`,
`github_orgs`, `editor_team`, and `targets` (which of the deployment's configured
[targets](../../docs/glossary.md#target-app-device) — its apps under test — this org owns). This
item moves the first three, collectively an org's **membership** (who can sign in as this org and
who among them can write), into three new columns on the `Org` table
([`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)): `members` and
`github_orgs` as JSON arrays of strings, `editor_team` as a nullable string, mirroring
`OrgConfig`'s own shape. `targets` stays in configuration: which application a target names, and
which backend and device it runs on, are exactly the app-specific differences prime directive 3
(app-agnostic core) keeps out of any admin-editable store, and a database-backed deployment
already has a more actively maintained way to scope an application to an org — registering a
project under it ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)) — so
moving `targets` too would duplicate that mechanism rather than complete this one.

A new `orgs_from_db(repository: Repository) -> dict[str, OrgConfig]` function, alongside the
existing `parse_orgs` in the same module, queries every `Org` row and its membership columns and
assembles the identical `dict[str, OrgConfig]` shape `parse_orgs` already produces from YAML —
with `targets` always empty, since a database-sourced org owns no targets under this item. Every
membership consumer inside `oauth_callback` reads that database-sourced dictionary once a
repository is wired, with no exception and no fallback to the configuration-parsed one: the
sign-in gate's `identity_matches_org`, the `org_for_identity` that places a signed-in user, the
`editor_team` read off the matched `OrgConfig` and passed as an argument into `role_for`
([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)), and the denial diagnostics unit 3
re-reads for that source. Target resolution is the one consumer that keeps reading configuration —
`targets_for_org` and the `org_for_target` that `authz.py`'s `_target_forbidden` calls both need an
org's actual target ownership, which only the configuration-sourced dictionary carries — and unit 4
covers what that resolution has to become. Routing membership through a second producer of the same
dictionary shape is the same principle
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) itself invoked when it
swapped a login list for GitHub Team membership — a policy is recomputed from its source on every
sign-in, so changing the source needs no data migration for the resolution logic itself.

### 2. One org source per deployment, chosen once

`oauth_callback` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) reads the org model
once, near the top, and hands that one dictionary to every check below it — today always the
`orgs:` block `load_serve_config_file(state.config)` parses, on every deployment. This item makes
that single read conditional on the deployment's shape and on nothing else: `orgs_from_db` when a
repository is wired (`state.repository is not None`, the same condition that already gates every
other database-backed seam in `serve`), `parse_orgs` against the configuration file otherwise. The
consequence for a database-backed deployment is the one this item is named for — the database
decides who signs in, and the configuration file no longer participates in that decision at all.

The sign-in gate's *placement* is unchanged: the `members`/`github_orgs` match check still runs
before `org_for_identity` is ever called, and still sits above the `if state.repository is not
None:` block, so a database-less, OAuth-configured deployment keeps gating sign-in
([BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)). Only the dictionary
that check consults now depends on whether a database is wired, not where in `oauth_callback` the
check itself runs.

Choosing the source before the gate, rather than after it, also severs a coupling that is a live
operational hazard today. `load_serve_config_file` fails closed to `None`, `orgs` collapses to an
empty mapping, and `identity_matches_org` then matches nobody — so on a database-backed deployment
a configuration file that is unreadable, malformed, or not yet bound turns every non-admin sign-in
into a denial, even though every one of those users, their orgs, and their roles are already rows
in the database. After this item, an unloadable configuration on that deployment stops the seeding
of unit 6 and leaves target resolution (unit 4) with nothing to resolve against, and stops nothing
else: sign-in, org placement, and role assignment keep working from the database.

A database-less deployment keeps calling `parse_orgs` against the configuration
file exactly as today, with no admin UI for org management: that deployment is local and
single-user by construction
([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s own motivation), it has
no tenant boundary to administer, and
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) already notes that
`forbidden_for_role` short-circuits to full access there regardless of role — a database-less org
model exists only to gate sign-in itself, not to grant a role, so nothing in this item's design
gives it anything to gain from a per-org membership store.

### 3. Denial diagnostics, re-read for a database source

`_unmatched_org_cause` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) names which of
five shapes left a login unmatched, and it decides a log level: a denial or a bypass admission logs
at WARNING when `parsed is None or not orgs`, on the reasoning that a configuration-shaped failure
is an operator's problem while an ordinary roster miss is not. Three of those five shapes — no
configuration is bound yet, the configuration failed to load, the configuration declares no `orgs:`
block — are shapes only a file can take, so neither the causes nor the level survive a database
source unread. Carrying them over would tell an operator to inspect a file that no longer decides
anything.

On a database-backed deployment those three collapse into one — no live `Org` row this login matches
— and the failure that replaces them never reaches the cause classifier at all.
This item gives `orgs_from_db` the opposite failure mode from `load_serve_config_file`'s: a
database error propagates rather than failing closed to an empty mapping: when `serve` cannot read
the database, it answers with a 5xx that names the database, rather than denying every user with a
message that blames their GitHub membership. What remains to report is therefore: no row in the
`orgs` table declares any membership yet (nothing created or seeded, or nothing that admits anybody
— the shape unit 7's bypass exists for), GitHub returned no organizations for this login, or a real,
unmatching roster. WARNING
stays reserved for the operator-actionable one, a table nobody yet belongs to. Keying it on
membership rather than on row count is what keeps it from decaying after one use: the bypass sign-in
the WARNING reports calls `ensure_org` on its way past, leaving a passive `default` row behind, so a
level keyed on an empty table would drop to INFO from the second sign-in on while the deployment
still admits nobody but admin-Team members.

The recovery guard beside them — `not matched_org and parsed is None and state.config is not None`,
which keeps a bypass-admitted user's already-recorded org rather than relocating them to `default`
over one failed configuration load — is removed on this path rather than translated onto it. It
sits inside the `if state.repository is not None:` block, so it only ever ran on a database-backed
deployment: exactly the deployment whose org source stops being a file here. Its motivating failure
goes away with its cause, since no configuration load can misplace a user whose org the database
decides, and `orgs_from_db` raises rather than silently presenting an empty roster to be misread as
one. A database-less deployment never entered that block to begin with, so it loses no protection
either.

### 4. A target's identity becomes (org, target)

Target ownership stays in configuration (unit 1), but the way it is *resolved* does not survive an
admin who can create orgs at will. `org_for_target`
([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)) maps a bare target name to the first org
whose `targets` list names it, and `_target_forbidden` forbids that target to every other org on
exactly that basis. Two orgs that both name `checkout` therefore do not each get a `checkout`:
configuration order silently awards it to whichever entry comes first, and the second org is
forbidden every operation on it — while still being shown it, because `targets_for_org` lists a
target whenever the org's own entry names it. The result is a target that appears in an org's list
and refuses every read of it, with nothing in the configuration file saying which org won.

Under a hand-edited `orgs:` block that collision was a mistake a reviewer could catch in the same
diff. Once unit 5 lets an admin create a tenant from the web UI, with no view of another tenant's
configuration entry, it becomes routine — and its symptom reads like a permissions bug rather than
the name clash it is.

This item therefore makes an org's target ownership resolve per org rather than per name.
`_target_forbidden` asks whether the target appears in *this* org's own list, instead of which
single org the name resolves to, which leaves `org_for_target`'s global name-to-org lookup with no
caller. It has to ask that through `targets_for_org` rather than by reading an `orgs:` entry
itself. `targets_for_org` needs no change, but its fallback keys on the literal `DEFAULT_ORG` slug,
not on "named in no entry": `default` gets every target no entry claims, while every *other* org
absent from the block falls to the `orgs.get(org) is None` branch and owns nothing. Reading an
entry directly would therefore forbid `default` every target it reaches today, since a deployment
typically declares no `default` entry at all. Routing through `targets_for_org` does change one
shape, in the direction of consistency: a deployment that declares an org *literally named*
`default` with its own `targets:` loses authorization for the targets that entry names, because
`targets_for_org` decides `default` by its literal slug before it ever reads an entry. Nothing is
taken away that was usable — `targets_for_org` already refused to list those targets for that org,
so the retired name-based resolution was authorizing a target the same deployment never showed. The
unclaimed-target fallback itself is untouched. Two orgs may then each claim a target
named `checkout`, and each is authorized for it. Under one bound configuration they still share the
one `targets:` definition that name resolves to; giving each org a definition of its own needs a
configuration bound per org, which is where
[BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s per-project config binding
already points and where this item deliberately stops. A third behavior changes at the conversion, narrower than the two this item's Introduction names and
recorded here rather than left to be discovered: `org_for_identity` resolves a login with no explicit
`members` entry to the *first* org whose `githubOrgs` match, and "first" is declaration order under
`parse_orgs` but slug order under `orgs_from_db` (`list_orgs` sorts by it). Both are stable — the
same login lands in the same org on every sign-in, which is what the tie-break has to be — but a
deployment where two orgs name the same GitHub organization can see it move once. Preserving
declaration order would need a position column and an answer for where an org created on the Orgs
page sits; refusing an ambiguous match outright would turn away logins that sign in today. Both are
larger than a tie-break, and the real answer is to let a login that belongs to several orgs choose
between them, which needs `users.org_id` to stop being a single column — a separate item.

Making the *identity* `(org, target)` first
is what keeps that later step from having to re-decide who owns a name — the same key BE-0225's
project registry already uses for a project, whose `add` and `get` are keyed by `(org_id, name)`
([`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py)).

That same `orgs.get(org) is None` branch decides what an org created through unit 5's API owns, and
the answer is nothing: such an org has no `orgs:` entry by construction, so `targets_for_org`
returns an empty list for it until someone adds one. This item accepts that rather than closing it,
and says so plainly because it is the one place where an admin who onboards a tenant from the web UI
still waits on a configuration edit — the org exists, admits its members, and can be administered
immediately, but is authorized for no target until an `orgs:` entry names some. Closing it means
either giving an org targets an admin can set, which is the move into the database unit 1 rejects,
or a configuration bound per org, which is the deferral recorded under *Alternatives considered*.
Both are larger changes than this one, and both are better decided once admin-created orgs exist to
motivate them.

### 5. Admin API and UI: create, delete, and edit membership

Four endpoints, all admin-only (an org's membership decides who else can sign in and write, the
same sensitivity level BE-0225 already gives registering or repointing a project) and each
mutation recorded through the existing `Repository.record_audit` (`org.create` / `org.delete` /
`org.membership.update`, the org's slug as `target`):

- `GET /api/orgs` — list every live org: slug, name, its `members` / `githubOrgs` / `editorTeam`
  themselves, and its project count. The rosters rather than their sizes, because the membership
  form below replaces all three fields as one unit and so must start from the current values; the
  endpoint is admin-only, the same tier that can already read the whole `orgs:` block through `GET
  /api/config/content`.
- `POST /api/orgs` — create an org from `{slug, name}`; membership starts empty (no member, no
  GitHub organization, no editor Team), so a freshly created org admits nobody until an admin adds
  to it. The slug `default` is refused with a 409: three separate places already read it as a
  fallback rather than a tenant — `org_for_identity` returns it for any login no org claims (every
  admin-Team bypass admission among them), `targets_for_org` decides it by the literal string before
  reading any entry, and `delete_org` below refuses it outright — so a real tenant created there
  would take the namespace an admin recovers through, and nothing could undo it through this API.
  Refused at the reversible end instead. The created row's `id` is its slug, matching what every existing writer already does:
  `ensure_org(org, slug=org, name=org)` puts one string in all three columns, and that same string
  is what `upsert_user(org_id=…)`, `state.org_of()`, and the org-scoped stores carry — so
  `orgs_from_db` keys its dictionary by it exactly as `parse_orgs` keys by an `orgs:` entry name.
  A generated id distinct from the slug would instead need a slug-to-id lookup on every one of
  those paths, which this item does not introduce. Creation marks the new row seeded immediately,
  the same per-row marker unit 6 sets on
  cutover, so the row is treated as already past cutover: no later `orgs:` entry for that slug
  seeds or re-seeds it, and so cannot overwrite membership an admin sets through this API.
- `POST /api/orgs/<slug>/membership` — replace an org's `{members, githubOrgs, editorTeam}` as one
  unit, and refused for `default` for the same reason creation is: membership is what places a login
  in an org, so a roster on the fallback slug makes it a tenant just as surely as creating it would.
  A bypass sign-in's `ensure_org` leaves a live `default` row, so the page lists and could otherwise
  reach it — the reservation has to hold on all three verbs, not two. the same granularity a config-file edit already has, rather than separate add/remove
  endpoints per list entry. `POST` rather than the `PUT` a whole-value write would normally take,
  because `PUT` is the one body-carrying verb neither transport implements — the stdlib handler
  serves `GET`/`POST`/`DELETE` and the FastAPI generator parses a body only for `POST` — so spelling
  it `PUT` would mean widening both transports for this one route, a cross-cutting change this item
  has no other need for. Every other whole-value write in `serve` is already a `POST`
  (`/api/projects/<name>/activate`, `/api/provider`).
- `DELETE /api/orgs/<slug>` — delete an org, rejected with 409 while it still owns any project
  (`list_projects` non-empty): an admin must deregister the org's projects first (BE-0225's
  deregister, which retains their run history). Deletion also rejects the `default` org outright:
  it is the fallback slug `serve` hardcodes (`DEFAULT_ORG`, [`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)),
  so an unmatched bypass sign-in keeps
  resolving to it regardless of table state, and removing it would only leave a soft-deleted org
  that a bypass user keeps landing on. Deleting an emptied, non-default org marks its `Org` row
  deleted (a `deleted_at` column, the same soft-delete shape `runs` already uses for a trashed run)
  rather than removing the row. A hard delete would violate the foreign key that `users`, `runs`,
  `secrets`, `provider_settings`, and `audit_log` still hold on the org's id even once every
  project is deregistered ([`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)),
  and the delete's own audit-log entry would then have nothing left to point at; soft-deleting the
  row instead leaves every one of those foreign keys intact. Retiring also revokes every session bound to a login recorded under that org, through a
  `revoke_identities` method added to the `SessionStore` seam
  ([`bajutsu/serve/sessions.py`](../../bajutsu/serve/sessions.py)) and implemented by each of its
  stores. The soft delete alone reaches only the *next* sign-in — `users.org_id` still names the
  retired slug, so a cookie issued beforehand would keep listing that tenant's runs, triggering new
  ones, and reading its secrets until it expired. That gap is newly reachable: retiring a tenant used
  to mean a configuration edit plus a redeploy, and the restart dropped every session as a side
  effect, so making it an in-process admin action removes an incidental revocation this restores
  deliberately. A session carrying no identity (a shared-token login) belongs to no org and is never
  touched. Sign-in resolution and `GET
  /api/orgs` exclude a deleted org from then on, so it can no longer be matched or listed; a user,
  secret, or provider setting still recorded under that org id keeps its row and stays queryable,
  but admits no new sign-in as that org. Its historical runs and audit-log entries stay retained
  under the deleted org's id, exactly as a deregistered project's run history survives
  deregistration ([BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)) — an
  admin action removes a tenant's ability to sign in and act, not the record of what it already
  did.

Re-creating an org at a slug that was soft-deleted is out of this item's scope: `Org.slug` carries
a UNIQUE constraint end-to-end
([`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py)), and a soft-deleted row
still occupies it, so `POST /api/orgs` rejects that slug with a dedicated 409 error rather than
reactivating or reusing the row; reactivation, should it ever be needed, is a separate, explicit
operation left to a future item. Unit 6's backfill likewise skips any row already marked deleted
(`deleted_at` set): it neither seeds nor revives one, since a soft-deleted org is retired, not
merely unseeded.

The org a session acts as is also surfaced outside that page, in the shell header beside the bound
configuration's name: `GET /api/config` — the boot read every tab already starts from — gains the
caller's own `actor` and the `org` it resolves to, and the header shows the org with the login on
hover. Every tab is silently scoped to that org (which targets are runnable, which runs and evidence
are visible, the secrets, the project list), so an admin administering several tenants can otherwise
only infer which one their own work lands in. Returning a caller's own identity to that caller
discloses nothing they did not present, unlike the roster itself, which stays behind the admin-only
`GET /api/orgs`; both fields are null where there is no signed-in identity (a local or shared-token
`serve`, where `org_of` would answer `default` for everyone), and the header omits the badge there.

A new admin-only **Orgs** page in the serve shell, parallel to
[BE-0275](../BE-0275-serve-projects-management-page/BE-0275-serve-projects-management-page.md)'s
Projects page: a list of orgs with create and delete actions (delete disabled, with the project
count shown, while the org still owns one), and a membership form per org (members, GitHub
organizations, editor Team) that calls the `POST` endpoint above. Each row renders the display name
the create form collects whenever it differs from the slug: no endpoint can change `name` afterwards,
so a display name the page never showed would leave a typo permanent and invisible. Both the API and
the page exist only when a repository is wired, following unit 2's gate.

### 6. One conversion at startup, then a hard cutover

A deployment upgrading from configuration-only org membership needs its existing `orgs:` block
represented in the database before this item's admin UI has anything to show or edit. `serve` does
that once, at startup (`serve()`, after `_configure_oplog` so the warning below reaches the live log
sink), from the configuration this server was **launched** with, and only while the `orgs` table
holds no row at all — a soft-deleted one included. For each entry in that block declaring any
membership, a dedicated seeding method creates or fills the row's
`members`/`github_orgs`/`editor_team` and stamps a persisted per-row marker. Not `ensure_org`:
`oauth_callback` and job completion already call it as `ensure_org(org, slug=org, name=org)` on
every sign-in and every finished run, with no membership to pass and — on a database-backed
deployment, where unit 2 makes the database the source — none they could pass. Widening it into a
create-or-update would either give one method two meanings depending on which argument is omitted,
or let the next sign-in clear membership an admin set through unit 5's membership endpoint. The
per-row marker stays for the same reason it always did: it tracks each row's converted state
directly, so an admin who intentionally empties an org's membership is not mistaken for a row never
yet written and re-seeded from a stale configuration value.

**No API bind seeds, and no second startup does.** `bind_config`, `bind_git_config`, the upload and
compose binds, and the project switcher all accept a configuration whose content the deployment does
not own — [BE-0121](../BE-0121-serve-csrf-host-allowlist/BE-0121-serve-csrf-host-allowlist.md) says
as much of the same file's `build:`, which stays ungoverned until `--allow-remote-build` opts in.
Seeding from one would hand that file authority over who may sign in. Before this item that
authority existed but was live and reversible, because `oauth_callback` re-read the file on every
sign-in, so rebinding to a clean configuration revoked the grant on the next login; a seeded row
outlives the bind, so the same rebind would no longer revoke anything. An admin binding
`github:someone/repo@main` whose `orgs:` declares `partners: {githubOrgs: [attacker-gh]}` would
otherwise grant every member of `attacker-gh` a permanent sign-in. The empty-table condition closes
the same hole from the other side: a restart carrying an edited configuration cannot add a tenant,
revive a retired one, or reshape an existing one behind an admin's back. One boot converts a
deployment; from then on the database is the sole author of its own roster, and an org first
declared after that is created on the Orgs page.

The cost is named rather than hidden: an org declared only in a configuration bound through the API
admits nobody until an admin creates it. That is the deliberate trade — the alternative is letting a
file the deployment does not control decide who signs in — and the Orgs page makes the recovery a
one-step action rather than a redeploy.

The Alembic migration that adds the `members`/`github_orgs`/`editor_team` columns (and the marker and
soft-delete columns) does only that — add columns, not seed data: seeding is `serve`'s own job at
startup, not something the migration can do, since Alembic's migration environment
([`bajutsu/serve/server/migrations/env.py`](../../bajutsu/serve/server/migrations/env.py)) resolves
only `BAJUTSU_DATABASE_URL` and never has access to a bound configuration's `orgs:` block.

After the conversion, `oauth_callback` reads membership from the database exclusively — a later edit
to the `orgs:` block has no effect on it — matching the hard cutover
[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) and
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) both chose
over keeping two independent sources for the same role-deciding data (see *Alternatives
considered*). `serve` warns, once per boot, whenever a repository is wired, a configuration is bound,
and that configuration's `orgs:` block has an entry still declaring any of the membership fields
(`members`/`githubOrgs`/`editorTeam`). An `orgs:` entry that carries only `targets` stays legitimate
and is expected to remain, since target ownership stays in configuration (unit 1), so warning on a
merely non-empty `orgs:` block would either fire forever on a correctly configured deployment or
push an operator to empty `orgs:` and lose that target-ownership data. A `targets:`-only entry is
also skipped by the seeding itself rather than written as an empty roster: paring the configuration
down before the converting boot — exactly what this item's own documentation recommends doing
after it — must not be the irreversible act of fixing every org at "admits nobody". This is the same
"an operator forgot this is no longer read" signal
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) already
gives for its own retired environment variable.

A fresh deployment — a repository wired from its first boot, no prior configuration-only history —
converts against whatever `orgs:` block that first boot binds, typically empty; every org it has
from that point on is one an admin creates through unit 5's API.

### 7. The admin-Team bootstrap bypass answers the empty-table case

A freshly created, database-backed deployment has no org until an admin creates one, which raises
the same bootstrap question
[BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) already
answered for a broken or absent `orgs:` block: who signs in to create the first one?
`BAJUTSU_OAUTH_ADMIN_TEAMS` stays an environment variable, deliberately outside this item's move
into the database — it is the recovery path for exactly the case in which the org model itself,
wherever it lives, cannot yet admit anyone. A member of a configured admin Team clears the sign-in
gate through that bypass regardless of how many `Org` rows exist, signs in with the admin role
(unaffected by this item, [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)'s
"admin stays one server-wide tier"), and uses unit 5's API to create the deployment's first org.
No chicken-and-egg: the one piece of tenancy data this item deliberately leaves outside the
database is exactly the piece that makes an empty `orgs` table recoverable.

## Alternatives considered

- **Keep org membership in the configuration file, and let an admin trigger a `POST
  /api/config` rebind after editing it out-of-band.** Rejected: this is today's mechanism, and it
  is exactly the dependency this item exists to remove — a person with write access to the
  deployment's configuration repository, and a redeploy or rebind, for every tenant onboarded or
  every team moved to a different GitHub Team.
- **A registry seam with both a database-backed and a local, file-backed path, mirroring
  [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s `ProjectRegistry`, so a
  database-less `serve` also gets admin-managed org membership.** Rejected: a database-less `serve`
  is single-user and local by construction, with no tenant boundary for an admin to manage and no
  role differentiation to protect (unit 2) — building a second persistence path for a feature that
  deployment shape has no use for would be pure surface area, unlike `ProjectRegistry`, which a
  single-user local hub genuinely needs (multiple configs, not multiple tenants).
- **Move `targets` (an org's target ownership) into the database alongside `members`,
  `github_orgs`, and `editor_team`.** Rejected for this item: `targets` names which application a
  target is and how it runs, app-specific data prime directive 3 keeps in configuration, not in an
  admin-editable operational store; a database-backed deployment already has a differently-shaped,
  more actively maintained mechanism for scoping an application to an org —
  [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s project registry.
- **Leave target ownership resolved by name, and reject a `POST /api/orgs` whose org would collide
  with another org's target names.** Rejected: the collision is not the admin's to avoid. The
  target names an org claims live in the configuration file, which the admin creating the org has
  no view of and, on a deployment that moved membership out of that file precisely so admins need
  not read it, no reason to. Validating the collision away would also make org creation fail for a
  reason unrelated to the org, while unit 4 makes the two orgs simply coexist.
- **Give each org its own `targets:` definitions by binding a configuration per org, rather than
  only making the identity `(org, target)`.** Deferred, not rejected: it is the natural end state,
  and [BE-0225](../BE-0225-config-project-hub/BE-0225-config-project-hub.md)'s per-project config
  binding already points at it. It needs `serve` to hold more than one bound configuration at a
  time — `state.config` is a single path today — which is a change to config binding rather than to
  the org model, and belongs with the item that makes it, not with this one.
- **Cascade-delete an org's projects, runs, and audit-log entries when the org itself is
  deleted.** Rejected: it mirrors BE-0225's own choice to retain a project's run history when the
  project is deregistered, and an audit log exists precisely so an admin action is never itself the
  reason a record disappears; [BE-0170](../BE-0170-weighted-fair-org-dispatch/BE-0170-weighted-fair-org-dispatch.md)'s
  future per-org dispatch fairness work will also want historical data to stay queryable regardless
  of whether the org that produced it still exists.
- **Consult configuration and the database together at every sign-in (a per-request fallback)
  instead of a one-time backfill followed by a hard cutover.** Rejected: BE-0313 and BE-0352 both
  already rejected keeping two independent sources for the same sign-in-deciding data, for the same
  roster-drift reason BE-0313's own motivation states; this item keeps that precedent rather than
  reopening it for org membership specifically.

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
- [x] 4 — Resolve target ownership per org rather than per name: `_target_forbidden` asks whether
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
      still declares `members`/`githubOrgs`/`editorTeam` (an entry that carries only `targets` stays
      expected and does not warn).
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
  keep their behavior and change only their source, while `org_for_target` loses its only caller.
- [`bajutsu/serve/project_registry.py`](../../bajutsu/serve/project_registry.py) — the `(org_id,
  name)` key a project already carries, which this item's target identity follows.
- [`bajutsu/serve/server/models.py`](../../bajutsu/serve/server/models.py) — the `Org` table this
  item adds membership columns to.
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

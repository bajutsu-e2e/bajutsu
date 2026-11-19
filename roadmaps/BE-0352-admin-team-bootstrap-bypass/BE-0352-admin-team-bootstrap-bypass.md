**English** · [日本語](BE-0352-admin-team-bootstrap-bypass-ja.md)

# BE-0352 — Admin GitHub Team env var bypasses the org-membership sign-in gate

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0352](BE-0352-admin-team-bootstrap-bypass.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0352") |
| Implementing PR | [#1485](https://github.com/bajutsu-e2e/bajutsu/pull/1485) |
| Topic | Hosting the web UI |
| Related | [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets the server-wide admin GitHub Team of `serve` sign in even when the `orgs:` config has
no entry for the admin's own GitHub organization. That includes the case where `orgs:` is missing
entirely.

It renames the environment variable that names the admin Team from `BAJUTSU_OAUTH_ADMIN_TEAM` to
`BAJUTSU_OAUTH_ADMIN_TEAMS`. The new variable is a comma-separated list, so a deployment can name more
than one Team. A match against it now clears the GitHub OAuth sign-in gate directly. Before, a match
only decided a role after the gate had already let a login through.

## Motivation

[BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) gates GitHub OAuth sign-in
on membership in a Bajutsu org declared under `orgs:`. A login passes the gate with an explicit
`members` entry, or as a member of a `githubOrgs`-listed GitHub organization. Only a login that clears
this gate is checked against `BAJUTSU_OAUTH_ADMIN_TEAM` for the admin role.

BE-0313's own design text names the resulting gap. Take an admin Team member whose GitHub organization
is not reachable through any org's `githubOrgs` or `members`. An operations-only GitHub organization
that no `orgs:` entry lists is an example. That member is rejected at sign-in, before the admin Team
is ever consulted. They lose sign-in entirely instead of gaining admin.

The same rejection reaches every login in two further cases. In the first, `orgs:` is missing from the
config altogether. In the second, its `members`/`githubOrgs` entries do not yet cover the deployment's
operators. `identity_matches_org` admits nobody against an empty org roster. A GitHub OAuth deployment
that starts up with no `orgs:` block, or with one an operator has not finished editing, locks out
every admin along with everyone else.

The failure falls on the one person meant to fix it. A broken or incomplete `orgs:` block is the kind
of config mistake an admin exists to correct. The admin repoints the server at a corrected config
source (`POST /api/config`) or uploads a corrected bundle (`POST /api/upload`). BE-0313's design leaves
no path to sign in and make that fix once the block is broken. The deployment waits on a manual edit
to the environment or the config store from outside `serve` itself. No admin can reach the running
server to correct it from the web UI.

## Detailed design

### The admin env var becomes a comma-separated list, and its check moves earlier

`BAJUTSU_OAUTH_ADMIN_TEAM` (singular) is renamed to `BAJUTSU_OAUTH_ADMIN_TEAMS` (plural). The value is
a comma-separated list of GitHub Teams, each written `"<github-org>/<team-slug>"`. A deployment can
then name more than one admin Team, for example one per GitHub organization it operates across.

Renaming avoids leaving two admin-Team variables with different sign-in behavior. This item changes
what an admin Team membership means at sign-in (see below). With a second variable added instead, the
existing single-Team variable would need the same behavior change under its old name. The rename makes
the name change and the behavior change one edit instead of two.

#### Four startup checks

Four startup checks keep this parsing from losing every admin without a trace. Three are gated on
GitHub OAuth being configured (`oauth is not None`), and one is gated the opposite way. The fourth
check is described first, because the other three depend on how `oauth` is built.

**Half-configured OAuth (`"oauth_half_configured"`).** `oauth` is built as
`GitHubOAuthClient(...) if cid and secret and redirect else None`. A single missing or mistyped
`BAJUTSU_OAUTH_GITHUB_*` variable therefore collapses `oauth` to `None`. To the three other checks,
that is indistinguishable from a deliberate token-auth-only deployment that never set any of the
variables. Consider an operator who sets `BAJUTSU_OAUTH_ADMIN_TEAMS` correctly but mistypes one GitHub
OAuth variable. None of the three other checks prints anything, and every GitHub sign-in then 404s.

What follows depends on whether a shared token exists:

- With a token set, this is not a lockout. `POST /api/login` is enabled precisely when
  `oauth is None`, so the deployment silently reverts to the shared-token login it was meant to
  replace. A token session carries no identity, so `forbidden_for_role` short-circuits, and every such
  session has full access. The operator believes GitHub OAuth is gating the server.
- With `BAJUTSU_SERVE_TOKEN` also unset, there is no fallback at all. `SessionManager.check_token` is
  `self.token is not None and secrets.compare_digest(...)`, so `POST /api/login` returns 401. Both
  transports also skip the auth+RBAC gate outright on that same `token is None` (`_gate` in
  `handler.py`, the middleware in `server/app.py`). Every endpoint, including `_ADMIN_PATHS`, is served
  unauthenticated. This shape is strictly worse than the shared-token fallback.

The check fires precisely when `oauth is None` *and* at least one of the three GitHub variables is
set. That describes a half-configured deployment, not a deliberately token-auth-only one. It prints
that GitHub OAuth is only partly configured and that GitHub sign-in will 404 until all three GitHub
variables are set. The message names whichever of the two fallbacks the deployment fell into (`token`
is already a parameter of `_build_server_state`). It also names exactly which of `cid` / `secret` /
`redirect` is unset, instead of listing the whole triple as a checklist. An operator who mistyped a
variable *name* (`_REDIRECT_URL` for `_REDIRECT_URI`, say) would otherwise see all three look present
in a quick `.env` read. That operator would then hunt through the OAuth app registration instead of
their own file.

**Retired name still set (`"admin_team_retired_name"`).** `_build_server_state`
([`bajutsu/serve/__init__.py`](../../bajutsu/serve/__init__.py)) prints a stderr warning whenever
GitHub OAuth is configured and the retired `BAJUTSU_OAUTH_ADMIN_TEAM` is still set. The warning fires
whether or not `BAJUTSU_OAUTH_ADMIN_TEAMS` is also set.

The likelier migration mistake is adding the new name *without* removing the old one, not leaving the
new name unset. An operator remembers the Team they are adding, not that the old singular name must go.
The old Team's members then silently stop being admin while `BAJUTSU_OAUTH_ADMIN_TEAMS` stays
non-empty. Tying this notice to the *empty* case alone would miss that mistake. The check is therefore
unconditional on the retired variable and independent of what the new list resolves to.

**Empty list (`"admin_teams_empty"`).** A separate warning fires whenever the parsed
`oauth_admin_teams` list comes out empty. Either the deployment never set `BAJUTSU_OAUTH_ADMIN_TEAMS`,
or only the retired name was ever set. In the second case, the hard cutover in *Alternatives
considered* means that the deployment now has no admin Team at all. Either way, the other symptom is an
unexplained 403 on every admin action. Because this same list is now also a sign-in credential (see
below), no admin is left who can sign in to fix it.

**Malformed entry (`"admin_teams_malformed"`).** A third warning fires on any entry that is not exactly
one `"<github-org>/<team-slug>"` pair. The check matches a regular expression that rejects an empty
half or internal whitespace. It does not count `/`. A space- or semicolon-separated list parses to a
single malformed entry that can never match a real Team. That is the same "no admin, no visible cause"
failure, reached by a different mistake.

The regular expression does not reject an uppercase character in either half. `in_admin_team`
case-folds both sides of the membership test (see below). An entry whose case differs from GitHub's own
stored case therefore still matches. A slug copied from the Team's display name in the GitHub UI is
an example. Rejecting such an entry would warn an operator to "fix" an entry that already works. Worse,
it would teach them to ignore the warning on the one list where a genuinely broken entry hides.

**Behavior shared by all four checks.** All four print a warning instead of raising. A config typo
therefore degrades a deployment to no-admin, and the deployment still starts. This is a deliberate
departure from the other operator-facing variables this module reads. `BAJUTSU_SESSION_TTL`, the
concurrency caps, and `BAJUTSU_RUN_RETENTION_DAYS` each raise on a malformed value. A server that
refuses to start is no more repairable than one with no admin. Unlike those variables, the mistake here
is also one that an operator can still fix from outside.

The malformed entry stays in the list instead of being dropped. Dropping it would silently narrow the
admin roster to whatever remained syntactically valid. That would be a second silent failure on top of
the one the warning already reports.

Three of the four checks fire only when GitHub OAuth is configured. On a token-auth-only server
backend, `BAJUTSU_OAUTH_ADMIN_TEAMS` decides nothing. A stale or malformed value left in the
environment there must stay quiet. It must not warn about an admin role that this deployment shape
never had.

#### Structured re-emission of the startup warnings

The four checks run inside `_build_server_state`, before anything is configured. At that point their
only option is a bare `print(..., file=sys.stderr)`. That output is unstructured text. It has no
registered `event`, no correlation fields, and no redaction, and it interleaves with the JSON `oplog`
writes to stdout once logging comes up. A log pipeline drops or fails to parse exactly this shape. Yet
it is the condition an operator most needs to alert on: "this deployment has no admin and no way to
sign in and get one."

- **Collection.** `_build_server_state` collects each message it prints onto a new
  `ServeState.startup_warnings` field. The field is a `tuple[tuple[str, str], ...]` of `(check, msg)`,
  not of bare messages. All four checks share the one `"server.startup_warning"` event below. A message
  alone would force an operator's alert to substring-match free text, which breaks silently the next
  time anyone rewords it.
- **The `check` discriminator.** `check` is a stable value (`"oauth_half_configured"`,
  `"admin_team_retired_name"`, `"admin_teams_empty"`, `"admin_teams_malformed"`) that an alert can key
  on. It also lets a deployment that deliberately runs OAuth with no admin Team suppress that one check
  without silencing the other three.
- **The `_warn` closure.** A local `_warn(check, msg)` closure inside `_build_server_state` makes the
  pairing unskippable. Each of the four checks calls it once, instead of repeating `print(...)` and
  `startup_warnings.append((check, msg))` verbatim. A fifth check added later then cannot print a
  warning and forget to collect it. That failure would pass a manual "did it print?" check, while
  `_emit_startup_warnings` silently never emits the warning.
- **Re-emission.** `serve()` calls a new `_emit_startup_warnings(state)` right after
  `_configure_oplog`. This is the same placement `restore_persisted_provider_settings` already uses,
  and for the same reason: "a malformed-file warning reaches the live log sink". The function re-emits
  each warning through `oplog.log_event`, passing `check` as a field alongside the registered `msg`. The
  event is a new `"server.startup_warning"` entry in `oplog.EVENTS`.

`_emit_startup_warnings` is a separately callable boot seam, matching the pattern of its two
neighbors. It is not an inline loop in `serve()`. `serve()` runs an actual server loop and is not
exercised by the fast test suite. An inline loop would therefore be covered only by the *absence* of
a crash. `log_event` raises `ValueError` on an unregistered event by design. A later rename or drop of
`"server.startup_warning"` from `oplog.EVENTS` would leave the whole suite green. The crash would
surface only in a real deployment that has a startup warning to re-emit. It would crash at boot, after
`_configure_oplog` and before `restore_persisted_provider_settings`. That is the very misconfigured
deployment this item exists to help, now unable to start at all instead of merely missing an admin. A
direct test drives `_emit_startup_warnings` instead. It pins each record's `check` field, not just its
message.

The `print` calls stay. Nothing is configured yet when they run. They remain the only way that a
deployment starting up entirely broken (no store, no database) sees anything at all.

[`docs/self-hosting.md`](../../docs/self-hosting.md) names both `"server.startup_warning"` and
`"oauth.denied"` next to the admin-Team migration guidance. It does not name them only in the
*Operational logging* section's event list. An operator reading the migration steps then sees the
alerting path, instead of only "read the first lines of the log."

#### The sign-in gate and the `"oauth.denied"` record

`oauth_callback` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already fetches the login's
GitHub Team memberships (`identity.teams`, via `fetch_identity`) before it runs the org-membership
gate. The same fetch also supplies the `editorTeam` role check further down. The gate and the Team
fetch simply were not ordered to use it together.

This item adds one more check alongside `identity_matches_org`. A login whose `identity.teams`
intersects the configured admin Team list clears the sign-in gate, regardless of what
`identity_matches_org` returns. An admin Team member then signs in even when no `orgs:` entry lists
their GitHub organization, or when `orgs:` is absent altogether.

A login that satisfies neither check is still rejected with the same 403 as today. The rejection is
no longer silent. It is now also recorded through `oplog.log_event`, under a new `"oauth.denied"` entry
in `oplog.EVENTS`. It is not folded into `"oauth.login"`, which stays "login count" (see below). A
rejection is the one failure this item exists to make recoverable: a broken or missing `orgs:` block
plus no matching admin Team. It needs the same audit-style visibility that a successful sign-in gets.
A bare 403 gives an operator nothing to correlate a user's "I can't sign in" report against.

**Naming the unmatched-org cause.** The denial message names which of five shapes left `orgs:`
unmatched. The success record's bypass-admission message (below) names the same five. A shared
`_unmatched_org_cause` helper computes the cause for both, so the two records cannot drift apart as an
earlier revision of this item briefly let them. A denied login is, if anything, the more likely source
of an "I can't sign in" report. It needs at least the same triage that a bypass admission gets.

Three of the five shapes collapse `orgs` to `{}` and deny *every* non-admin login outright:

- no config is bound
- a config failed to load
- a config declares no `orgs:` block

Blaming an org roster that was never read, or was declared empty on purpose, sends an operator chasing
the wrong fix. The missing-block shape is not a corner case. It is this item's own headline scenario
(*Motivation*: "A GitHub OAuth deployment that starts up with no `orgs:` block … locks out every admin
along with everyone else").

**Naming the admin clause.** The trailing admin clause of the message gets the same care. "No admin Team
matched" reads as a real membership miss. It must not fire identically when `admin_teams` is unusable,
meaning empty or consisting entirely of malformed entries. That is the exact state the boot-time
`admin_teams_empty` / `admin_teams_malformed` checks warn about. It is also the state in which no admin
can sign in to fix `orgs:` at all. The message therefore says "no usable admin Team is configured" for
that shape. It is keyed on the same `admin_teams_unusable` predicate as the level (below), so the
message and the level cannot drift apart.

**The five failure paths.** `oauth_callback` has five places where it can end a sign-in without
success, and this item now records all five. The org-gate denial is the fifth. The other four get the
same `"oauth.denied"` event, because the reasoning above applies to them just as much. Without it, a
bare 404/403/502 gives a user's "I can't sign in" report nothing to correlate against:

- OAuth is not configured. A half-configured deployment 404s every GitHub sign-in and silently
  re-enables the shared-token login instead. That is not a lockout, but it is a hazard the operator
  needs to know about.
- A CSRF state mismatch.
- An exchange that raised.
- An exchange that returned no identity.

All four of these earlier records fire at `INFO`. None needs anything that identifies a real GitHub
account:

- `oauth is None` is a static property of the deployment, not a per-request signal.
- `state_param` and `state_cookie` are both caller-supplied (a query value and the caller's own
  `Cookie:` header). An attacker clears `secrets.compare_digest` for free by sending the same fake
  value as both. The attacker then passes the CSRF check with no real auth and reaches whichever of
  the exchange-failure branches a garbage `code` triggers.

A `WARNING` on any of these four would be a per-request signal whose volume an anonymous caller sets.
A loop against this endpoint could then bury a genuine denial under free amplification. Recording them
at `INFO` still leaves every one a record (no silent 404/403/502). An operator's `WARNING`-keyed alert
does not have to filter them out. Repeated CSRF mismatches remain the signature of a login-CSRF attempt
worth watching for. That is a *rate* claim about many records, not a property of any one of them. Its
right home is a counter an operator can threshold, not this event's log level.

No `login` is known yet at these four earlier points, so their records carry no `actor` field. Only the
later, gate-level denial and every successful sign-in do.

**The level of the org-gate denial.** The org-gate denial itself is not unconditionally `WARNING`
either. `GET /api/oauth/login` is unauthenticated, and a GitHub OAuth app authorizes any of GitHub's
own users, not only this deployment's. A stranger with a free account can reach this branch as easily
as the four above. "Needs a real GitHub exchange" bounds the volume at one account, not at the
deployment's own operators.

`WARNING` is reserved for the one shape actually worth paging on: `admin_teams_unusable` (`authz.py`).
It is true when `admin_teams` is empty *or* every entry fails the well-formedness pattern. It is not
`not admin_teams` alone. A non-empty but entirely malformed list is functionally the same lockout that
`_build_server_state`'s `admin_teams_empty` check warns about at boot. A space-separated value that
parses to one `"a/b c/d"` entry is an example, and this item's own malformed-entry test uses exactly
that shape.

The well-formedness pattern is a single module-level constant in `authz.py` (`ADMIN_TEAM_ENTRY_RE`).
`_build_server_state` imports it for its own `admin_teams_malformed` check. A second regex literal
could drift from the first. `in_admin_team` and `_unmatched_org_cause` were factored out for the same
reason. An ordinary denial, where a configured admin Team simply did not match this login, still gets
a record. It is recorded at `INFO`, under the same reasoning as the four earlier failures.

#### The `"oauth.login"` record for successful sign-ins

`oauth_callback` now records every successful sign-in through `oplog.log_event`
([`bajutsu/serve/oplog.py`](../../bajutsu/serve/oplog.py)). The record uses the already-reserved
`"oauth.login"` event, with the login itself as the `actor` correlation field. It is not a bare logging
call. The record carries the same registered event name, redaction, and correlation fields as every
other operationally significant record in `serve`. An operator's alert keyed on `event` can then see it.

`"oauth.login"` was reserved in `oplog.EVENTS` before this item but never emitted. This item makes the
event fire at all. It fires for every sign-in, not only a bypassing one. An event that recorded only
bypasses would make `event=oauth.login` mean "bypass count" instead of "login count." That is the
opposite of what an operator building an alert on that event name would expect.

A per-record `bypass` field is `True` only when the admin-Team bypass, not `orgs:`, admitted the login.
The message varies with it: "admin-Team bypass admitted …" for a bypass, and a plain "… signed in"
otherwise.

**The level.** The level does NOT just mirror `bypass`. This item's own guidance recommends an admin
Team in an operations-only GitHub organization that no `orgs:` entry lists. There, `bypass` is `True`
on *every* admin sign-in, permanently. That is the normal operating condition of a correctly configured
deployment, and not worth paging on. `WARNING` fires only when the org model itself is unusable:

- `parsed is None` (no config bound, or one that failed to load), or
- the config loaded but declares no `orgs:` block at all.

In those shapes, the bypass just admitted a login into a deployment that nobody but an admin Team
member can currently sign in to repair. Every other bypass, and every ordinary sign-in, stays `INFO`.
The `bypass` field then carries real information instead of being a constant `True` on every record.

**The five shapes in the bypass message.** The bypass message names which of five ways `matched_org`
can be `False`:

1. No config is bound yet.
2. The config failed to load.
3. The config declares no `orgs:` block.
4. GitHub reported no orgs for this login.
5. No `orgs:` entry matched.

An operator paged by the `WARNING` needs to know which one, not just that the org gate did not admit
this login. The first two send them to the config. The other three send them to the org roster. One
fixed phrase would hide that distinction.

`_unmatched_org_cause` tells the first two apart by whether `state.config` itself is `None`.
`load_serve_config_file` returns the same `None` immediately when no config path is bound at all. That
is the ordinary, no-error bootstrap state that `serve()` itself treats as normal. It also returns
`None` when a bound path fails to load. Collapsing both into "the config failed to load" would send an
operator hunting a filesystem error in a file that was never supposed to exist yet.

The outright-denied path above names the same five shapes for the same reason. A shared module-level
`_unmatched_org_cause` helper computes the cause once for both call sites. This is the same reasoning
behind factoring `in_admin_team` out below. Two independent copies of the same five-way branch, edited
separately later, could drift as this branch pair itself once did. A sixth shape could land in one copy
and not the other, or a denied login could be told a stale one of the first five.

The bypass remains the one sign-in path that `orgs:` did not authorize. An operator auditing who signed
in, and when, would otherwise have no record of it at all. The `bypass` field lets the same event
stream distinguish it from an ordinary org-gated login.

#### One shared `in_admin_team` helper, case-folded

The membership test behind the gate check asks whether any of a login's Teams is in the configured
admin list. `role_for` uses the same test to resolve the admin role. This item factors it into one
shared `in_admin_team` helper instead of writing the same expression out twice.

Two copies of the same rule in two functions about 120 lines apart could drift apart under a later,
independent edit to either one. Suppose the gate's copy admits a login that the role's copy does not.
That login would sign in and resolve to `viewer`. It would hold a session for a login `orgs:` never
authorized, with none of the admin access that was the reason to admit it. One helper makes that drift
impossible by construction.

`in_admin_team` case-folds both sides of the membership test. GitHub resolves an org login and a Team
slug case-insensitively. A real GitHub org login can be stored mixed-case, even though a Team slug is
always lowercased. An `admin_teams` entry whose organization half carries whatever case GitHub stores
must therefore still match a login's exact-case `identity.teams` membership, and vice versa.

Without folding, this item's own sign-in bypass would carry a latent case-sensitivity trap. Two
examples: an `admin_teams` entry authored from a GitHub org page that happens to display mixed case,
and an entry copied before an org rename changed its stored case. Either would silently stop matching a
login it is supposed to admit. That is the same "no admin, no visible cause" failure the malformed-entry
warning above exists to prevent. That check cannot reach it, because a case mismatch is syntactically
well-formed.

`editorTeam` (`role_for` in [`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) has the identical
shape and is compared against the identical GitHub-reported case. It carries the same latent trap. This
item leaves it uncorrected as a scope decision, not because the value is any less exposed. Widening
`editorTeam` to case-fold too is a role-resolution change outside this item's sign-in-recovery scope.

Folding never turns an empty team name into a match, because `admin_teams` never contains `""`. The
comma-split that builds it filters on `t.strip()`. Folding also does not affect the nested-Team
guarantee below. That guarantee rests on exact string equality, not on a separator count:
`"acme-gh/parent/child"` is a different string from `"acme-gh/parent"`, folded or not.

### Role resolution is unaffected in shape, only in name

`role_for` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already ranks admin above editor
above the viewer base role, from a single admin-Team parameter. This item only widens that parameter
from one Team to a list. It checks for any intersection with `identity.teams`, via the shared
`in_admin_team` helper above, instead of a single membership test.

The sign-in gate above already establishes that a bypassing login carries a matching Team. `role_for`
resolves it to admin the same way it resolves any other admin Team member today. No separate role path
is added for the bypass case.

`role_for` and the org placement below both run only inside `oauth_callback`'s
`if state.repository is not None:` block, exactly as they do today for every other admin Team member.
This item adds no new call to either. On a deployment with no database wired, neither runs, and the
bypass affects only the sign-in gate itself. `forbidden_for_role` already grants full access to any
signed-in user there regardless of role, because `state.repository is None` short-circuits it
(BE-0313). A bypassing login therefore gains only the sign-in that gate already grants any other
admitted login on that deployment shape.

### Org placement for a bypassing admin

An admin who clears the gate through the Team bypass, not through `orgs:` membership, is placed the same
way `org_for_identity` already places any login that matches no `members`/`githubOrgs` entry. The
placement is the shared `default` org.

BE-0313's own text states that the `default` org "becomes unreachable through OAuth sign-in" once an
`orgs:` block is declared, because every login that would have landed there is rejected before
placement is computed. This item makes that statement no longer universally true. A bypassing admin now
reaches `default` precisely because no other org claims their login. `default` carries no special
[targets](../../docs/glossary.md#target-app-device) beyond whatever no other org has claimed. The
`_ADMIN_PATHS` enforcement of admin is already instance-wide regardless of org (BE-0313 §"Admin stays
one server-wide tier"). The placement therefore grants a bypassing admin nothing beyond the admin role
BE-0313 already made server-wide.

#### A config that failed to load must not relocate an existing member

"No other org claims their login" is the case this default is for. It is not the only way the bypass
can admit a login. A failure to load the config itself also makes `identity_matches_org` see no match,
even for a login that a real org *does* claim. `load_serve_config_file` fails closed to `None` on a
transient filesystem error or a config typo, collapsing `orgs` to `{}`. This item's own motivating
scenario, a broken `orgs:` block, produces exactly this failure shape.

Without a correction, one such hiccup would relocate an existing org member to `default` on every
failure. Their user row, audit attribution, and object-storage prefix would all move, until their next
clean login moved them back. The placement logic above never intends that outcome for someone `orgs:`
already claims.

`oauth_callback` avoids this for that one failure shape. The condition has two parts: the bypass, not
`orgs:`, admitted the login, *and* a config path **is** bound but failed to load
(`parsed is None and state.config is not None`). In that case, it keeps whatever org
`state.repository.user_org` already has on record for that login, instead of recomputing one. It falls
to `org_for_identity`'s `default` result only when no prior record exists. That is the genuine
first-time bootstrap case this section is about.

The `state.config is not None` half of the guard matters on its own. `load_serve_config_file` returns
the same `parsed is None` immediately when no config path is bound at all. That is the ordinary
bootstrap state that `serve()` treats as normal, not a transient failure. It stays `None` on every
login until an admin binds a config path. A guard on `parsed is None` alone would pin such a login's org
forever, and it would never re-resolve once a config is finally bound.

A login whose config loaded but still matched nothing in `orgs:` is not the preserved case either. The
config answered, so that login is genuinely unclaimed. Either this is its first sign-in, or an operator
has since removed it from every configured org. That login re-resolves through `org_for_identity` like
any other, exactly as BE-0015 7c-2 already requires role resolution to do on every login. Leaving
`orgs:` must take effect on the next sign-in. The login must not stay pinned to whatever org a
now-departed member happened to hold.

#### Why the preservation does not also guard on an empty `identity.orgs`

This preservation is deliberately **not** also guarded on an empty `identity.orgs`. An empty
`identity.orgs` is equally the shape a failed `/user/orgs` fetch takes (`_fetch_orgs` fails closed to
`[]`). It is just as much the shape of a login that genuinely belongs to no GitHub org at all. A
`members:`-listed bot or ops-only account is an example. `_fetch_orgs`'s `[]` gives no way to tell the
two apart.

Guarding on an empty `identity.orgs` would trade one problem for a worse one. Once such a login is
removed from `members:`, its org would be pinned forever. No future login could ever report a non-empty
`identity.orgs` to escape the guard. That is a permanent wrong state. It silently contradicts the same
recompute-every-login principle stated a few lines below for the role.

Narrowing the guard to `parsed is None` alone accepts a smaller, self-healing cost. A `githubOrgs`-only
member who hits a real `/user/orgs` outage is relocated to `default` for that one login. They move back
on their next clean one, the same as a genuinely unclaimed login would be.

Making the two cases distinguishable would need `_fetch_orgs` to report failure as `None` instead of
`[]`. That changes `_paginate`'s contract (shared with `_fetch_teams`), `Identity.orgs`'s type, and
every fake `OAuthClient` in the test suite. It is a change to code this item did not otherwise touch,
so it is left as a follow-up.

#### An inherited sharp edge: a real org named `default`

This placement inherits an existing sharp edge of the org model. It does not introduce a new one.
`DEFAULT_ORG` ([`bajutsu/serve/orgs.py`](../../bajutsu/serve/orgs.py)) is the literal string
`"default"`, and nothing stops a deployment from also declaring a real org under that same key.
`targets_for_org` already special-cases that string for target ownership, regardless of what such an
org's own `targets:` list says. A deployment naming an org literally `default` already has a collision,
which this item does not create.

A bypassing admin who matches no other org is placed under the same key. On a deployment with a real
`default` org, the admin's user row, audit entries, and object-storage prefix land under that org
instead of a neutral catch-all. That is the same collision, extended to one more caller. A deployment
that relies on the admin-Team bypass should avoid naming an org literally `default`. This reason adds to
the existing target-ownership reason to avoid it.

### The underlying Team fetch keeps failing closed, now sometimes at the cost of sign-in itself

`_fetch_teams` ([`bajutsu/serve/server/oauth.py`](../../bajutsu/serve/server/oauth.py)) already fails
closed to an empty team list on any of three conditions:

- a non-200 response
- a network hiccup mid-pagination
- an unparseable body

This item adds no new call to GitHub. It reads the same `identity.teams` that BE-0313 already fetches.

For a login the org gate already admits, a fetch failure still costs only the editor/admin role,
exactly as before. For a login whose sign-in depends solely on the bypass, the same fail-closed
behavior now costs sign-in itself. A GitHub API outage leaves that login unable to prove admin-Team
membership. They fall back to whatever the org-membership gate alone would have granted them, which is
rejection if their org is not reachable through `orgs:` either. This item adds no login-list fallback
for that outage case; see *Alternatives considered*.

### Every admin Team entry is now a sign-in credential, not only a role mapping

Before this item, an entry could name a GitHub organization the deployment does not control. The entry
might contain a typo in the organization half, or name a Team whose organization was later renamed or
deleted (GitHub frees the old login for anyone to register). Such an entry simply matched nobody.
`role_for` consulted it only after `identity_matches_org` had already turned away anyone not otherwise
in the org roster.

After this item, that same entry *is* part of the sign-in gate. Whoever controls that organization
login can create a Team with the matching slug, sign in through the bypass, and be resolved to admin.
That person reaches every `_ADMIN_PATHS` endpoint, including `GET /api/config/content`, which can
disclose a config body embedding literal secrets.

The malformed-entry warning in *The admin env var becomes a comma-separated list* cannot catch this. A
syntactically well-formed entry naming an organization nobody here owns validates its shape perfectly.
No code can verify who controls a GitHub organization. This item's only mitigation is therefore
operational. `deploy/self-host/.env.example` and the self-hosting guide (both languages) now say plainly
that every entry must name a GitHub organization the deployment actually controls. The value is a
sign-in credential now, not only a role mapping.

## Alternatives considered

- **Adding a login-list environment variable (an `ADMIN_USERS`-style roster) instead of, or alongside,
  the Team-based bypass.** Considered, because a login list survives a GitHub Teams API outage that a
  Team-based bypass cannot. Rejected for this item. BE-0313 already rejected keeping a login-list
  allowlist alongside its organization/Team checks. Its reasoning was that a second, independent grant
  path recreates the roster-drift problem that GitHub-membership-based role-based access control
  (RBAC) exists to remove. That rejection targeted a general escape hatch available to every role.
  The narrower, admin-only bypass this item adds targets a different problem: recovering from a broken
  or absent `orgs:` block. It keeps deriving admin from GitHub Team membership, not from a
  hand-maintained roster. It therefore does not reopen the roster-drift case BE-0313 argued against. A
  login-list variable remains open to propose separately, if the Teams-API-outage gap turns out to
  matter in practice.
- **Leaving `BAJUTSU_OAUTH_ADMIN_TEAM` singular and adding a second, bypass-only variable.** Rejected.
  Two admin-Team variables with different sign-in behavior would coexist for as long as both exist. An
  operator could not tell which one they are using without reading the source. A single renamed
  variable is one behavior to document and reason about.
- **Reading `BAJUTSU_OAUTH_ADMIN_TEAM` as a deprecated fallback when `BAJUTSU_OAUTH_ADMIN_TEAMS` is
  unset, so an existing deployment keeps working across the upgrade without an env-var edit.**
  Rejected. `BAJUTSU_OAUTH_ADMIN_TEAM` is itself BE-0313's own addition. It is part of the same GitHub
  Team-based RBAC design this item continues to refine, not a long-settled variable with a wide
  installed base. BE-0313 already retired `BAJUTSU_OAUTH_ADMINS` outright instead of aliasing it, when
  it replaced the login-list admin roster with this same Team. A hard cutover for its direct successor
  keeps that precedent. A fallback path would be one this design otherwise has no use for. A deployment
  that already sets `BAJUTSU_OAUTH_ADMIN_TEAM` renames it to `BAJUTSU_OAUTH_ADMIN_TEAMS` at upgrade
  time. That is the same kind of adoption step that BE-0313's own `orgs:`-block requirement already
  asks of every OAuth deployment.
- **Scoping the bypass to only the deployment shape with no database wired, where BE-0313 already
  documents the org gate as the sole line of defense.** Rejected. The config-editing recovery scenario
  this item targets, a broken `orgs:` block on an otherwise fully wired server backend, is exactly the
  shape with a database. Scoping the bypass away from it would leave the motivating case unsolved.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Rename `BAJUTSU_OAUTH_ADMIN_TEAM` to `BAJUTSU_OAUTH_ADMIN_TEAMS` (comma-separated), threading the
      list through `SessionManager`, `role_for`, and the server-backend env wiring. Warn loudly at
      startup, only when OAuth is configured: whenever the retired singular name is still set,
      regardless of whether the new plural one also is (the likelier partial-rename mistake);
      whenever the resulting list is empty — whether nothing was set or only the retired name was;
      and separately when an entry is not a well-formed `"<github-org>/<team-slug>"` pair (an empty
      half or internal whitespace, not an uppercase character — `in_admin_team` case-folds, so a
      differently-cased entry still matches) — so no admin-losing mistake goes unsignaled and no
      token-auth-only deployment is warned about an admin role it never had. A fourth check, gated
      the opposite way (`oauth is None` but at least one GitHub OAuth var is set), warns on a
      half-configured deployment: the three checks above cannot reach it, since each reads
      `oauth is None` as "deliberately token-auth-only." Not a lockout — `POST /api/login` re-enables
      on this same `oauth is None`, so the deployment silently reverts to the shared-token login it
      was meant to replace, with full access on every such session — unless no `BAJUTSU_SERVE_TOKEN`
      is set either, in which case both transports skip the auth+RBAC gate outright and every endpoint
      is served unauthenticated; the message names whichever fallback this deployment actually fell
      into. A hazard the operator needs to know about, not one they can infer from an
      admin-team-focused check.
- [x] Add the admin-Team bypass to the sign-in gate in `oauth_callback`, alongside
      `identity_matches_org`, using the Team list already fetched for role resolution. Import
      `Identity` under `TYPE_CHECKING` (an annotation-only need, since `from __future__ import
      annotations` is already in effect), not at module level — a runtime import would drag
      `bajutsu.serve.server` onto the default `bajutsu.serve` / CLI path, which
      `bajutsu/serve/server/__init__.py` states is never supposed to happen; `state.py` already sets
      this precedent for the same module. Record every
      successful sign-in through `oplog.log_event` (the reserved `"oauth.login"` event, the login as
      the `actor` field, and a `bypass` field `True` only for a bypass-only admission), so the one
      sign-in path `orgs:` did not authorize still leaves a record an operator's `event`-keyed alert
      can see. Record every one of the five ways this function ends a sign-in without success under a
      separate `"oauth.denied"` event: OAuth not configured, a CSRF state mismatch, an exchange that
      raised, and an exchange that returned no identity all at `INFO` — none needs a real GitHub
      account to trigger, since `oauth is None` is a static per-deployment property and an attacker
      clears the CSRF check for free by sending the same fake value as both `state_param` and their
      own `Cookie:` header, so a per-request `WARNING` on any of them is a volume an anonymous caller
      sets themselves — and a login clearing neither the org gate nor the bypass — the last naming
      which of five shapes left `orgs:` unmatched via a shared `_unmatched_org_cause` helper (no
      config bound, a config-load failure, a config with no `orgs:` block, GitHub reporting no orgs,
      or a real, unmatching roster — the first two told apart by whether `state.config` itself is
      `None`, since `load_serve_config_file` returns the same `None` for both), so a broken or missing
      `orgs:` block with no matching admin Team is recoverable rather than a bare 404/403/502 with
      nothing to correlate a user's report against. When persisting the identity, keep an existing
      login's already-recorded org rather than relocating it to `default`, but only when a config path
      is bound and failed to load — not when no config is bound at all (a standing state, not a
      hiccup) and not on an empty `/user/orgs` response, which is equally the shape of a login that
      genuinely has no GitHub org.
- [x] Collect `_build_server_state`'s four startup warnings onto a new
      `ServeState.startup_warnings` field instead of only printing them — `tuple[tuple[str, str], ...]`
      of `(check, msg)`, so all four checks sharing one event still let an operator's alert key on a
      stable `check` (`"oauth_half_configured"`, `"admin_team_retired_name"`, `"admin_teams_empty"`,
      `"admin_teams_malformed"`) rather than substring-matching *msg*. Add a new
      `_emit_startup_warnings` boot seam — a separately-callable function alongside
      `restore_persisted_provider_settings`'s and `register_launch_project`'s, not an inline loop in
      `serve()` — that re-emits each through `oplog.log_event` under a new `"server.startup_warning"`
      event right after `_configure_oplog`, passing `check` through as a field, the same placement its
      neighbors already use, so an operator's `event`-keyed alert can see "no admin, no way to sign in
      and get one" too, not only whatever they happen to read from raw boot output.
- [x] Update the self-hosting and configuration docs (both languages) and `.env.example` to describe
      the renamed variable and the bypass. BE-0313's claim that the `default` org is unreachable
      through OAuth sign-in is superseded in this item's *Detailed design* instead: no `docs/` page
      states it, so none needed the edit. State plainly that every entry must name a GitHub
      organization the deployment actually controls, since the value is now a sign-in credential.
      Name the startup warnings themselves in the self-hosting guide (both languages) and
      `.env.example`, so an upgrading operator knows to read the first lines of the log — and name
      `event=server.startup_warning` / `event=oauth.denied` there too, next to the migration steps and
      in `.env.example`, not only in the *Operational logging* section's event list, so the guide
      itself points at the alerting path instead of leaving it to be inferred from the general
      structured-logging reference.
- [x] Tests: sign-in accepted for an admin-Team member with no matching `orgs:` entry and with no
      `orgs:` block at all; resolved role is admin in both cases; a login matching neither the org
      gate nor the admin-Team list is still rejected and logs `"oauth.denied"` naming which of the
      five `orgs:`-unmatched shapes it is (including no config bound at all, distinct from a bound
      config that failed to load); OAuth not configured, a real CSRF state mismatch, a
      no-state probe, a CSRF check bypassed with matching fake values, a raising exchange, and an
      exchange returning no identity each log `"oauth.denied"` at `INFO`; a half-configured OAuth
      deployment warns naming the shared-token fallback when a token is configured, and separately
      naming "unauthenticated" when it is not (and a fully-unset OAuth deployment warns about
      neither); `_build_state`
      returns the collected `startup_warnings` matching what was printed, each entry's `check` field
      distinguishing which of the four checks fired, not just its message; `_emit_startup_warnings`
      actually re-emits each collected warning through `oplog.log_event` under
      `"server.startup_warning"` with `check` carried through as its own field, so a later rename or
      drop of that name from `oplog.EVENTS` has a
      test to fail rather than only a boot-time `ValueError` — a standalone test drives
      `_emit_startup_warnings` directly against a hand-built `ServeState.startup_warnings`, so this
      coverage survives independently of how any particular startup check is spelled or whether it
      still exists, and a second standalone test pins the no-op path when nothing needs to warn; the
      renamed variable
      parses a multi-Team list;
      a bypassing admin is placed in the `default` org; an existing member's recorded org survives a
      failure to load the config itself, but re-resolves to `default` instead when no config is
      bound at all (proving the guard's two halves apart) or when a genuinely revoked member's next
      login re-checks `orgs:` rather than staying pinned, and so does a `githubOrgs`-only member relocated
      by a transient `/user/orgs` failure (the accepted, self-healing cost of that fetch's `[]` being
      ambiguous with a genuine zero-orgs login); the retired singular var warns even when the new
      plural one is also set. End to end through the HTTP transport: a login matching no `orgs:`
      entry, admitted only by the bypass, can actually reach
      an admin-gated endpoint (`POST /api/apikey`) — not just receive a session.

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
  `oauth_admin_team` field this item renames to `oauth_admin_teams` and widens to a tuple of Teams;
  `ServeState`, which this item gives a new `startup_warnings` field.
- [`bajutsu/serve/oplog.py`](../../bajutsu/serve/oplog.py) — `oplog.EVENTS`, into which this item adds
  `"oauth.denied"` and `"server.startup_warning"`.
- [`bajutsu/serve/__init__.py`](../../bajutsu/serve/__init__.py) — `_build_server_state`'s four
  startup checks and `serve()`'s post-`_configure_oplog` re-emission of them.
- [`docs/self-hosting.md`](../../docs/self-hosting.md) — the self-hosting guide's GitHub OAuth
  section, which documented the gap this item closes ("An admin still has to clear the sign-in gate
  above first") until this item removed that caveat.

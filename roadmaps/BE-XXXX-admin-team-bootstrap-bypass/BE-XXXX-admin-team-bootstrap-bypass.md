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

Four startup checks keep this parsing from losing every admin without a trace, three of them gated on
GitHub OAuth being configured (`oauth is not None`) and one gated the opposite way. That fourth check
comes first: `oauth` is built as `GitHubOAuthClient(...) if cid and secret and redirect else None`, so
a single missing or mistyped `BAJUTSU_OAUTH_GITHUB_*` variable collapses to `None` — indistinguishable,
to the three checks below, from a deliberate token-auth-only deployment that never set any of them. An
operator who sets `BAJUTSU_OAUTH_ADMIN_TEAMS` correctly and typos one GitHub OAuth variable gets no
output from any of the three, and every login then 404s: not just no admin, but no sign-in for anyone,
strictly worse than an empty admin-Team list. This check fires precisely when `oauth is None` *and* at
least one of the three GitHub vars is set — a half-configured deployment, not a deliberately
token-auth-only one — printing that GitHub OAuth is only partly configured and that every login will
404 until all three are set.

`_build_server_state`
([`bajutsu/serve/__init__.py`](../../bajutsu/serve/__init__.py)) prints a stderr warning whenever
GitHub OAuth is configured and the retired `BAJUTSU_OAUTH_ADMIN_TEAM` is still set, regardless of
whether `BAJUTSU_OAUTH_ADMIN_TEAMS` also is — the likelier migration mistake is not leaving the new
name unset but adding it *without* removing the old one: an operator remembers the Team they're
adding, not that the old singular name must go, so the old Team's members silently stop being admin
while `BAJUTSU_OAUTH_ADMIN_TEAMS` stays non-empty. Tying this notice to the *empty* case alone would
miss exactly that mistake, so it is its own unconditional check on the retired variable, independent
of whatever the new list resolves to. Separately, it prints a warning whenever the parsed
`oauth_admin_teams` list comes out empty — whether because the deployment never set
`BAJUTSU_OAUTH_ADMIN_TEAMS` at all, or because only the retired name was ever set (the hard cutover in
*Alternatives considered* means that deployment now has no admin Team at all). Either way the only
other symptom is an unexplained 403 on every admin action, and — because this same list is now also a
sign-in credential (see below) — no admin left who can sign in to fix it. It also warns on any entry
that isn't
exactly one `"<github-org>/<team-slug>"` pair — matched against a regular expression that rejects an
empty half or internal whitespace, not by counting `/`: a space- or semicolon-separated list parses
to a single malformed entry that can never match a real Team, which is the same "no admin, no visible
cause" failure reached by a different mistake. The regex does not reject an uppercase character in
either half: `in_admin_team` case-folds both sides of the membership test (see below), so an entry
whose case differs from GitHub's own stored case — a slug copied from the Team's display name in the
GitHub UI, say — still matches. Rejecting it here would warn an operator to "fix" an entry that
already works, and, worse, teach them to ignore this warning on the one list where a genuinely
broken entry hides. All four print a warning
rather than raising, so a config typo degrades a deployment to no-admin instead of refusing to start
it entirely. That is a deliberate departure from the other operator-facing variables this module
reads (`BAJUTSU_SESSION_TTL`, the concurrency caps, `BAJUTSU_RUN_RETENTION_DAYS`), each of which
raises on a malformed value: a server that refuses to start is no more repairable than one with no
admin, and unlike those the mistake here is one an operator can still fix from outside. The malformed
entry stays in the list rather than being dropped: dropping it would silently narrow the admin
roster to whatever remained syntactically valid, a second silent failure on top of the one the
warning already reports. Three of the four checks fire only when GitHub OAuth is configured; on a
token-auth-only
server backend `BAJUTSU_OAUTH_ADMIN_TEAMS` decides nothing, so a stale or malformed value left in the
environment there must stay quiet rather than warn about an admin role that deployment shape never
had.

These four checks run inside `_build_server_state`, before anything is configured, so their only
option at that point is a bare `print(..., file=sys.stderr)` — unstructured text with no registered
`event`, no correlation fields, and no redaction, interleaved with the JSON `oplog` writes to stdout
once logging comes up. That is exactly the shape a log pipeline drops or fails to parse, and it is the
condition an operator most needs to alert on: "this deployment has no admin and no way to sign in and
get one." `_build_server_state` collects each message it prints onto a new `ServeState.startup_warnings`
field, and `serve()` re-emits them through `oplog.log_event`, under a new `"server.startup_warning"`
entry in `oplog.EVENTS`, right after it calls `_configure_oplog` — the same placement
`restore_persisted_provider_settings` already uses, and for the same reason ("a malformed-file warning
reaches the live log sink"). The `print` calls stay: nothing is configured yet at the point they run,
so they are still the only way a deployment starting up entirely broken (no store, no database) sees
anything at all.

`oauth_callback` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)) already fetches the
login's GitHub Team memberships (`identity.teams`, via `fetch_identity`) before it runs the
org-membership gate, because that same fetch also supplies the `editorTeam` role check further down.
The gate and the Team fetch simply weren't ordered to use it together. This item adds one more check
alongside `identity_matches_org`: a login whose `identity.teams` intersects the configured admin Team
list clears the sign-in gate regardless of what `identity_matches_org` returns. An admin Team member
then signs in even when no `orgs:` entry lists their GitHub organization, or when `orgs:` is absent
altogether. A login that satisfies neither check is still rejected with the same 403 as today, but no
longer silently: it is now also recorded through `oplog.log_event`, under a new `"oauth.denied"`
entry in `oplog.EVENTS` rather than folded into `"oauth.login"` (which stays "login count," per the
reasoning below). A rejection is the one failure this item exists to make recoverable — a broken or
missing `orgs:` block plus no matching admin Team — so it needs the same audit-style visibility a
successful sign-in gets, not a bare 403 with nothing an operator can correlate a user's "I can't sign
in" report against. The denial message names which of the same four shapes left `orgs:` unmatched
that the success record's bypass-admission message does (below) — a shared `_unmatched_org_cause`
helper computes it for both, so the two records can't drift the way an earlier revision of this item
briefly let them: a denied login is, if anything, the more likely source of an "I can't sign in"
report, so it needs at least the same triage a bypass admission gets, not less. Two of the four
shapes collapse `orgs` to `{}` and deny *every* non-admin login outright — a config that failed to
load, or one that declares no `orgs:` block at all — so blaming an org roster that was never actually
read, or was declared empty on purpose, sends an operator chasing the wrong fix. The missing-block
shape is not a corner case — it is the item's own headline scenario (*Motivation*: "A
GitHub OAuth deployment that starts up with no `orgs:` block … locks out every admin along with
everyone else"). `oauth_callback` has five places it can end a sign-in without success, and this item
now records all five: the other four — OAuth not configured (a half-configured deployment 404s for
every login, the same "config is broken and nobody can sign in" class of failure this item exists to
make visible), a CSRF state mismatch, an exchange that raised, an exchange that returned no identity —
get the same `"oauth.denied"` event, since the reasoning above applies to them just as much (a bare
404/403/502 a user's "I can't sign in" report can't be correlated against). The CSRF branch is also
worth its own record on a different ground: repeated state mismatches are the signature of a
login-CSRF attempt, not just an expired cookie, and were invisible before this item — but a callback
carrying no state at all (no cookie, no query value) is the cheapest possible unauthenticated request
against this public endpoint, not a presented-and-differed mismatch, so it records at `INFO` rather
than `WARNING`: a loop against it would otherwise write one `WARNING` per request into the same
`event`-keyed stream an operator greps for the gate-level denials, burying the signal this item exists
to add under free amplification anyone can trigger. No `login` is known yet at these four earlier
points, so their records carry no `actor` field — only the later, gate-level denial and every
successful sign-in do. `oauth_callback`
now records every successful sign-in through `oplog.log_event`
([`bajutsu/serve/oplog.py`](../../bajutsu/serve/oplog.py)), under the already-reserved `"oauth.login"`
event and the login itself as the `actor` correlation field — not a bare logging call, so the record
carries the same registered event name, redaction, and correlation fields every other
operationally-significant record in `serve` already does, and an operator's alert keyed on `event`
can actually see it. `"oauth.login"` was reserved in `oplog.EVENTS` before this item but never
actually emitted, so this item is what makes the event fire at all — and it fires for every sign-in,
not only a bypassing one: an event that only ever recorded bypasses would make `event=oauth.login`
mean "bypass count" instead of "login count," the opposite of what an operator building an alert on
that event name would expect. A per-record `bypass` field (`True` only when the admin-Team bypass,
not `orgs:`, is what admitted the login) and the message and level vary accordingly — `WARNING` and
an "admin-Team bypass admitted …" message for a bypass, `INFO` and a plain "… signed in" message
otherwise — so the field carries real information instead of being a constant `True` on every record.
The bypass message also names which of the four ways `matched_org` can be `False`: the config failed
to load, the config declares no `orgs:` block, GitHub reported no orgs for this login, or no `orgs:`
entry matched — because an operator paged by the `WARNING` needs to know which one, not just that the
org gate didn't admit this login. The first two send them to the config; the other two send them to
the org roster instead — a distinction the message would otherwise hide behind one fixed phrase. The
outright-denied path below names the same four shapes for the same reason — a denied login is, if
anything, the more likely source of an "I can't sign in" report — so a shared module-level
`_unmatched_org_cause` helper computes it once for both call sites, the same reasoning behind
factoring `in_admin_team` out below: two independent copies of the same four-way branch, edited
separately later, could drift the way this branch pair itself once did — a fifth shape could land in
one copy and not the other, or a denied login could get told a stale one of the first four.
The bypass remains the one sign-in path `orgs:` did not authorize, so it is the one path an operator
auditing who signed in, and when, would otherwise have no record of at all; the `bypass` field is what
lets that same event stream distinguish it from an ordinary org-gated login.

The membership test behind that check — is any of a login's Teams in the configured admin list — is
also the test `role_for` uses to resolve the admin role, so this item factors it into one shared
`in_admin_team` helper rather than writing the same expression out twice. Two copies of the same rule
in two functions ~120 lines apart could drift apart under a later, independent edit to either one —
a login the gate's copy admits but the role's copy doesn't would sign in and resolve to `viewer`, a
session for a login `orgs:` never authorized and with none of the admin access that was the reason to
admit it. One helper makes that drift impossible by construction.

`in_admin_team` case-folds both sides of that membership test. GitHub resolves an org login and a
Team slug case-insensitively, and a real GitHub org login can be stored mixed-case even though a Team
slug is always lowercased — so an `admin_teams` entry whose organization half carries whatever case
GitHub stores it in must still match a login's exact-case `identity.teams` membership, and vice versa.
Without folding, this item's own sign-in bypass would carry a latent case-sensitivity trap: an
`admin_teams` entry authored from a GitHub org page that happens to display mixed case, or copied
before an org rename changed its stored case, would silently stop matching a login it is supposed to
admit — the same "no admin, no visible cause" failure the malformed-entry warning above already
exists to prevent, just unreachable by that check since a case mismatch is syntactically well-formed.
`editorTeam` ([`bajutsu/serve/authz.py`](../../bajutsu/serve/authz.py)'s `role_for`) has the identical
shape and is compared against the identical GitHub-reported case, so it carries the same latent trap
— left uncorrected here as a scope decision, not because the value is any less exposed: widening
`editorTeam` to case-fold too is a role-resolution change outside this item's sign-in-recovery scope.
Folding never turns an empty team name into a match —
`admin_teams` never contains `""`, since the comma-split that builds it filters on `t.strip()` — and
does not affect the nested-Team guarantee below, which rests on exact string equality
(`"acme-gh/parent/child"` is a different string from `"acme-gh/parent"`, folded or not), not on a
separator count.

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

"No other org claims their login" is the case this default is for, and it is not the only way the
bypass can admit a login: a failure to load the config itself also makes `identity_matches_org` see
no match, for a login a real org *does* claim (`load_serve_config_file` fails closed to `None` on a
transient filesystem error or a config typo, collapsing `orgs` to `{}`) — the failure shape this
item's own motivating scenario, a broken `orgs:` block, actually produces. Without a correction, that
one-off hiccup would relocate an existing org member to `default` on every such failure — their user
row, audit attribution, and object-storage prefix all moving until their next clean login moves them
back, an outcome the placement logic above never intends for someone `orgs:` already claims.
`oauth_callback` avoids this for that one specific failure shape: when the bypass, not `orgs:`, is
what admitted a login, *and* the config failed to load (`parsed is None`) — an unambiguous signal —
it keeps whatever org `state.repository.user_org` already has on record for that login instead of
recomputing one, falling to `org_for_identity`'s `default` result only when no prior record exists —
the genuine first-time bootstrap case this section is actually about. A login whose config loaded but
still matched nothing in `orgs:` is not this case: the config answered, so that login is genuinely
un-claimed, whether because this is its first sign-in or because an operator has since removed it
from every configured org. That login re-resolves through `org_for_identity` like any other, exactly
as BE-0015 7c-2 already requires role resolution to do on every login — leaving `orgs:` must take
effect on the next sign-in, not stay pinned to whatever org a now-departed member happened to hold
before.

This preservation is deliberately **not** also guarded on an empty `identity.orgs`, even though that
is equally the shape a failed `/user/orgs` fetch takes (`_fetch_orgs` fails closed to `[]`): it is
just as much the shape of a login that genuinely belongs to no GitHub org at all — a `members:`
-listed bot or ops-only account, say — and `_fetch_orgs`'s `[]` gives no way to tell the two apart.
Guarding on it would trade one problem for a worse one: such a login's org would be pinned forever
once it is removed from `members:`, since no future login could ever report a non-empty
`identity.orgs` to escape the guard — a permanent wrong state, silently contradicting the same
recompute-every-login principle a few lines below for the role. Narrowing to `parsed is None` alone
accepts a smaller, self-healing cost instead: a `githubOrgs`-only member who hits a real `/user/orgs`
outage is relocated to `default` for that one login and moves back on their next clean one, the same
as a genuinely un-claimed login would be. Making the two cases distinguishable would need
`_fetch_orgs` to report failure as `None` rather than `[]`, which changes `_paginate`'s contract
(shared with `_fetch_teams`), `Identity.orgs`'s type, and every fake `OAuthClient` in the test suite
— a change to code this item did not otherwise touch, so it is left as a follow-up rather than done
here.

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
      startup, only when OAuth is configured: whenever the retired singular name is still set,
      regardless of whether the new plural one also is (the likelier partial-rename mistake);
      whenever the resulting list is empty — whether nothing was set or only the retired name was;
      and separately when an entry is not a well-formed `"<github-org>/<team-slug>"` pair (an empty
      half or internal whitespace, not an uppercase character — `in_admin_team` case-folds, so a
      differently-cased entry still matches) — so no admin-losing mistake goes unsignaled and no
      token-auth-only deployment is warned about an admin role it never had. A fourth check, gated
      the opposite way (`oauth is None` but at least one GitHub OAuth var is set), warns on a
      half-configured deployment: the three checks above cannot reach it, since each reads
      `oauth is None` as "deliberately token-auth-only," and it is strictly worse than an empty
      admin-Team list — every login 404s, not just admin ones.
- [x] Add the admin-Team bypass to the sign-in gate in `oauth_callback`, alongside
      `identity_matches_org`, using the Team list already fetched for role resolution. Record every
      successful sign-in through `oplog.log_event` (the reserved `"oauth.login"` event, the login as
      the `actor` field, and a `bypass` field `True` only for a bypass-only admission), so the one
      sign-in path `orgs:` did not authorize still leaves a record an operator's `event`-keyed alert
      can see. Record every one of the five ways this function ends a sign-in without success under a
      separate `"oauth.denied"` event: OAuth not configured, a CSRF state mismatch (at `INFO` rather
      than `WARNING` when the callback carries no state at all — an anonymous probe, not a
      presented-and-differed mismatch, since anyone can trigger it against this unauthenticated
      endpoint), an exchange that raised or returned no identity, and a login clearing neither the org
      gate nor the bypass — the last naming which of four shapes left `orgs:` unmatched (a config-load
      failure, a config with no `orgs:` block, GitHub reporting no orgs, or a real, unmatching
      roster), so a broken or missing `orgs:` block with no matching admin Team is recoverable rather
      than a bare 404/403/502 with nothing to correlate a user's report against. When persisting the
      identity, keep an existing login's already-recorded org rather than relocating it to `default`,
      but only on a config-load failure — an unambiguous signal — not on an empty `/user/orgs`
      response, which is equally the shape of a login that genuinely has no GitHub org.
- [x] Collect `_build_server_state`'s four startup warnings onto a new
      `ServeState.startup_warnings` field instead of only printing them, and have `serve()` re-emit
      each through `oplog.log_event` under a new `"server.startup_warning"` event right after
      `_configure_oplog` — the same placement `restore_persisted_provider_settings` already uses — so
      an operator's `event`-keyed alert can see "no admin, no way to sign in and get one" too, not
      only whatever they happen to read from raw boot output.
- [x] Update the self-hosting and configuration docs (both languages) and `.env.example` to describe
      the renamed variable and the bypass. BE-0313's claim that the `default` org is unreachable
      through OAuth sign-in is superseded in this item's *Detailed design* instead: no `docs/` page
      states it, so none needed the edit. State plainly that every entry must name a GitHub
      organization the deployment actually controls, since the value is now a sign-in credential.
      Name the startup warnings themselves in the self-hosting guide (both languages) and
      `.env.example`, so an upgrading operator knows to read the first lines of the log.
- [x] Tests: sign-in accepted for an admin-Team member with no matching `orgs:` entry and with no
      `orgs:` block at all; resolved role is admin in both cases; a login matching neither the org
      gate nor the admin-Team list is still rejected and logs `"oauth.denied"` naming which of the
      four `orgs:`-unmatched shapes it is; OAuth not configured, a CSRF state mismatch (at both
      levels), a raising exchange, and an exchange returning no identity each log `"oauth.denied"`
      too; a half-configured OAuth deployment warns (and a fully-unset one does not); `_build_state`
      returns the collected `startup_warnings` matching what was printed; the renamed variable
      parses a multi-Team list;
      a bypassing admin is placed in the `default` org; an existing member's recorded org survives a
      failure to load the config itself, but a genuinely revoked member re-resolves to `default` on
      their next login rather than staying pinned, and so does a `githubOrgs`-only member relocated
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

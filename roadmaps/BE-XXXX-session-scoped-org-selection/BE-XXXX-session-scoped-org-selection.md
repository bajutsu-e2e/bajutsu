**English** · [日本語](BE-XXXX-session-scoped-org-selection-ja.md)

# BE-XXXX — Session-scoped org selection for a login in more than one org

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-XXXX](BE-XXXX-session-scoped-org-selection.md) |
| Author | [@paihu](https://github.com/paihu) |
| Status | **Proposal** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-XXXX") |
| Topic | Hosting the web UI |
| Related | [BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md), [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md), [BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md), [BE-0352](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md), [BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) |
<!-- /BE-METADATA -->

## Introduction

This item lets a signed-in user who belongs to more than one org — Bajutsu's multi-tenancy unit in
`serve`, the boundary that scopes runs, scenarios, secrets, projects, and evidence
([BE-0015](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md)) — choose which org
the current browser session acts as, and switch between them without signing out. Today that choice
does not exist: sign-in resolves one org from GitHub membership, writes it to the user's database
row, and every later request reads it back, so a login that legitimately belongs to two orgs is
pinned to whichever one the resolution order happened to reach first. We make the session, not the
user row, the seat of the answer. A session records the GitHub organizations and Teams its sign-in
observed, plus the org it currently acts as and the role that org grants; a `Caller` value —
the login, the selected org, and the role — is resolved once per request at the authentication
gate and passed explicitly to every operation that scopes by org, replacing the per-operation
lookup of the user row. The header badge that names the acting org becomes a select box listing
the orgs this login may act as, and a switch endpoint re-validates the choice against the current
org roster before it takes effect. A deployment with a single org, no GitHub OAuth login, or no
database keeps behaving exactly as it does today.

## Motivation

Bajutsu resolves a user's org exactly once, at sign-in. `org_for_identity` returns the org whose
`members` list names the login, or else the first org whose `githubOrgs` list intersects the GitHub
organizations the sign-in reported, or else `default`; `upsert_user` stores that answer in
`users.org_id`, and `ServeState.org_of` reads it back on every request that needs a tenant scope.
The resolution is deterministic — the same login lands in the same org on every sign-in — but it is
the deployment's order, not the user's intent, that breaks the tie between two matching orgs.

That gap already has a name in this repository.
[BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md),
which moved org membership from the configuration file into the database, recorded the tie-break as
a known limitation and stated where the real answer lies: letting a login that belongs to several
orgs choose which one it acts as, which requires `users.org_id` to stop being the single place the
answer is kept. That item deferred the work rather than solving it, because changing where the
acting org is read from reaches every org-scoped read and write in `serve`.

The people this gap blocks are the ones a multi-tenant deployment exists for. A contractor who tests
two customers' applications, an operator who belongs to an internal org and a customer's org, and a
reviewer who moves between tenants all hold one GitHub login and one browser session. Their only
present workaround is to have an admin edit membership so that the resolution reaches the other org
— which moves every one of their sessions at once, and undoes the first org's access in the
process.

The choice belongs to the session rather than to the user because the same person may need both
tenants at once: one browser window on a customer's runs, another on the internal org's, each
scoped correctly. Storing the selection on the user row would make the two windows fight over one
value, and a switch in either would silently move the other.

## Detailed design

The work divides into seven units. Each is independently reviewable, and together they cover the
whole change.

### 1. The session record carries the sign-in's GitHub facts and the current selection

`SessionStore` — the seam behind which `serve` keeps login sessions — records only an opaque
session id and the GitHub login bound to it. We widen it to also hold the GitHub organizations and
the GitHub Teams the sign-in observed, the org the session currently acts as, and the role that org
grants. The GitHub facts are what makes a later switch possible at all: the candidate orgs and the
role both derive from them, and they reach `serve` only during the OAuth callback.

Two implementations carry the widened seam: the in-memory store the local `serve` uses, and the
database-backed `SqlSessionStore` a server deployment runs
([BE-0106](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md)). The database store
gains four nullable columns through an Alembic migration, so an existing session row upgrades
without a value and reads as "no GitHub facts recorded, no selection made" — the pre-upgrade
behavior, resolved from the user row as before.

`RedisSessionStore`, the store `SqlSessionStore` replaced, is retired in the same change. No
deployment path constructs it — only its own tests do — and carrying a security-relevant selection
into a store nobody runs would add a place for the selection to be dropped without adding a reader.

### 2. A `Caller` resolved once per request, passed explicitly

`gate.actor_for` answers "which login is this request", and each operation that needs a tenant
scope then asks `ServeState.org_of` to look the org up from the user row. We replace the second
step: the gate resolves a frozen `Caller` value holding the login, the acting org, and the role,
all three read from the session record, and the request carries that value forward. Both transports
— the stdlib handler and the FastAPI application — already build a per-request context object with
an `actor()` accessor, so the `Caller` travels the path the login already travels.

An operation that scopes by org takes the `Caller`; an operation that only attributes an action to
a person keeps taking the login string. `ServeState.org_of` remains for the two cases that have no
selection to honor: a shared-token session, which carries no identity, and a session issued before
this change, which recorded none. In both, the user row answers as it does today.

We pass the value explicitly rather than binding it to an implicit per-request context, because the
stdlib `serve` reuses a thread across requests. A binding left behind by a request that returned
early — a rejected `Host` header, a 401, a 403, a streaming response, or the JSON error boundary —
would be read by the next request on that thread, and the failure that produces is one org reading
another org's artifacts.

### 3. The role travels with the selected org

A role is not a property of a login in this deployment; it is a property of a login *within an
org*, since `editorTeam` is declared per org
([BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md)). The role therefore
has to move with the selection, or a user who is an editor in one org and a viewer in another would
carry one of the two answers into both.

The transport gate rejects a request whose role is too low before any operation runs, and it reads
that role from the user row today. It reads the `Caller`'s role instead. The role is recomputed at
each switch from the Teams the session recorded, the target org's `editorTeam`, and the server-wide
admin Teams — the same `role_for` policy sign-in already applies, given the same inputs.

### 4. The candidate list and the switch endpoint

`GET /api/config`, the boot read the web UI already makes, gains the orgs this session may act as.
The server computes the list from the session's recorded GitHub facts against the *current* org
model, so an admin's membership edit takes effect on the next read rather than being frozen at
sign-in. The list holds only the orgs this login itself may act as: the full roster stays behind the
admin-only `GET /api/orgs`, which is the boundary the configuration read already draws for the
acting org it returns.

`POST /api/session/org` performs the switch. It recomputes the candidates the same way and rejects a
slug outside them, so a client that replays a stale list — or an arbitrary slug — cannot reach an
org its session never qualified for. On success it writes the new org and the recomputed role onto
the session record and records the switch through the audit log, which is what an audit of "who
acted as which tenant, when" needs. The endpoint is available to any signed-in identity, since
choosing among orgs one already qualifies for grants nothing new.

### 5. The header badge becomes a select box

The header shows which org the session acts as, next to the configuration it acts on. When the
session has more than one candidate, the badge becomes a select box holding them; choosing one calls
the switch endpoint and reloads the views whose contents are org-scoped. With a single candidate the
header renders exactly as it does today, so a single-tenant deployment sees no new control.

### 6. Retiring an org, and editing its membership, both reach the live sessions

Deleting an org revokes the sessions of its members, and it finds them through `users.org_id`
([BE-0375](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md)).
A selection the user row does not record would slip through that lookup: a user whose row names org
A, acting as org B in the current session, would keep acting as B after B is deleted. The session
store gains a revoke-by-selected-org query, and org deletion calls it alongside the existing
revocation, so both ways of acting as the retired org end at the same moment.

Editing an org's membership revokes nothing today, because nothing about a live session depended on
membership: the role was recomputed at the next sign-in, and the acting org was read live from the
user row. Both stop being true here — a session carries a role computed from the GitHub Teams its
sign-in observed, and an org it may no longer qualify for. An edit therefore revokes exactly the
sessions whose answer it changes, so a user whose cached answer went stale signs in again and has it
recomputed. The alternative is an editor who keeps editing an org whose `editorTeam` no longer names
them.

Recomputing the answer is what identifies those sessions. The endpoint replaces `members`,
`githubOrgs`, and `editorTeam` as one unit, and it reads the org's current row before the write, so
the roster before and after are both in hand; each live session attached to the org carries the
GitHub organizations and Teams its own sign-in observed. The server therefore recomputes both
answers — may this session still act as this org, and with which role — for every session attached
to it, and revokes the sessions whose answer moved. Dropping one `members` entry reaches that
login's sessions and leaves the org's other members untouched; a pure grant moves nobody's answer,
so it signs out nobody, including the admin performing the edit.

Revoking one user's sessions without editing membership is not part of this item. An admin whose
GitHub-side Team change has to take effect before the session expires makes it take effect through
the membership edit or the org deletion above.

### 7. The security posture this item changes, and its escape hatch

Recording the sign-in's Teams on the session caches a privilege input. Today the role is recomputed
on every login and nowhere else, so leaving a GitHub Team takes effect the next time the user signs
in; with the Teams cached, a session already open keeps the role that Team granted until the session
expires, which is seven days by default.

Two remedies bound that window. An admin who deletes an org, or edits its membership so that a
session's answer changes, revokes that session outright (unit 6), which ends a stale role at once. A Team change made on GitHub's
side, with no corresponding edit here, is the case the window really covers: the deployment learns
of the change only at the next sign-in, so the role granted by a Team the user has left survives
until the session expires or an admin revokes it. We state the window and both remedies in the
hosting documentation rather than leave an operator to infer them, and both language mirrors under
`docs/` are updated in the same change.

## Alternatives considered

**Keep the selection on the user row.** Writing the chosen org back to `users.org_id` would need no
new session state and no `Caller` threading. It also makes one browser window's choice move every
other session that login holds, including a second window deliberately opened on another tenant, and
it leaves the audit record unable to say which tenant a given session was acting as at the time.

**Bind the acting org to an implicit per-request context.** A context variable set at the gate and
read inside `ServeState.org_of` would touch two files instead of the operations that scope by org.
The stdlib `serve` reuses threads and has several paths that return before dispatch, so a binding
that outlives its request is read by the next one, and the resulting failure is a cross-tenant read
rather than a mislabeled log line.

**Carry the org on a string subclass so no signature changes.** A value that is both the login
string and a carrier for the org would let every existing call site stay as written. Any operation
that copies or formats the value — an interpolation, a strip, a round trip through JSON — yields a
plain string, and the org silently falls back to the user row's, which sends a write meant for one
tenant into another. A static type checker cannot flag a single one of those sites, because the
subclass typechecks everywhere the string does.

**Re-resolve the candidates against GitHub on each request.** Asking GitHub for the login's
organizations and Teams per request would keep no privilege input cached at all. It puts a network
call on the critical path of every request and makes a GitHub outage a denial of service for a
deployment whose own database already knows its roster.

**Introduce a membership table joining users to orgs.** BE-0375 framed the fix as `users.org_id`
ceasing to be a single column, which reads as a many-to-many table. Membership is already derivable
from the org roster and the session's GitHub facts, so a second copy of it would have to be kept in
step with the roster the deployment already administers. A membership table becomes worth its cost
when a per-org, per-user grant that GitHub cannot express is needed; nothing in this item requires
one.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [ ] Session record carries the sign-in's GitHub organizations and Teams, the selected org, and the role (in-memory store, `SqlSessionStore`, Alembic migration; `RedisSessionStore` retired).
- [ ] `Caller` resolved at the authentication gate and passed explicitly to org-scoped operations, with `ServeState.org_of` as the fallback for a session that records no selection.
- [ ] The role gate reads the `Caller`'s role, recomputed per switch from the recorded Teams, the target org's `editorTeam`, and the server-wide admin Teams.
- [ ] `GET /api/config` returns this session's candidate orgs; `POST /api/session/org` re-validates a switch against the current org model and records it in the audit log.
- [ ] The header org badge becomes a select box when the session has more than one candidate.
- [ ] Org deletion revokes the org's sessions; a membership edit revokes the sessions whose recomputed eligibility or role changed — by selected org as well as by the user row's org.
- [ ] The role-cache window (seven days by default), and the revocations that bound it, are documented in `docs/` and `docs/ja/`.

## References

- [BE-0015 — Web UI public hosting](../BE-0015-web-ui-public-hosting/BE-0015-web-ui-public-hosting.md) — introduces the org as the multi-tenancy unit and the sign-in that resolves one.
- [BE-0313 — GitHub org and Team RBAC](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) — the sign-in gate and the per-org `editorTeam` this item's role recomputation reuses.
- [BE-0375 — Database-backed org lifecycle and membership management](../BE-0375-serve-org-lifecycle-management/BE-0375-serve-org-lifecycle-management.md) — records the tie-break limitation this item resolves, and owns the session revocation org deletion performs.
- [BE-0352 — Admin Team bootstrap bypass](../BE-0352-admin-team-bootstrap-bypass/BE-0352-admin-team-bootstrap-bypass.md) — the admin Teams that outrank an org's own `editorTeam` in the role policy.
- [BE-0106 — Post-completion worker model](../BE-0106-post-completion-worker-model/BE-0106-post-completion-worker-model.md) — the database-backed session store the new columns extend.

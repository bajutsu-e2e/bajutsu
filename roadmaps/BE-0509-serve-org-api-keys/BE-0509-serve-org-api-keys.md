**English** · [日本語](BE-0509-serve-org-api-keys-ja.md)

# BE-0509 — Issue organization-scoped API keys so a script can call a hosted serve

<!-- BE-METADATA -->
| Field | Value |
|---|---|
| Proposal | [BE-0509](BE-0509-serve-org-api-keys.md) |
| Author | [@0x0c](https://github.com/0x0c) |
| Status | **Implemented** |
| Tracking issue | [Search](https://github.com/bajutsu-e2e/bajutsu/issues?q=is%3Aissue+label%3Aroadmap-tracking+in%3Atitle+"BE-0509") |
| Implementing PR | [#2558](https://github.com/bajutsu-e2e/bajutsu/pull/2558) |
| Topic | Hosting the web UI |
| Related | [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md) |
<!-- /BE-METADATA -->

## Introduction

This item adds organization-scoped API keys to `serve`. An administrator issues a key for one
organization. A script then sends the key as a bearer token and acts as that organization, with the
role of the key at most `editor`. A key opens only the routes that an allowlist names, so it never
becomes a second administrator login.

The item exists for a script that runs on a developer's machine and has no browser session. The item
that imports a finished run into `serve` is the first user of a key.

## Motivation

Once OAuth is configured, `serve` accepts the shared bearer token for worker routes alone
(`_is_worker_route` in `bajutsu/serve/gate.py`). A human session cookie cannot be scripted. The one
machine identity is the OpenID Connect (OIDC) exchange of
[BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md), and it needs an
identity token from a CI provider, which a laptop does not have. A script on a laptop cannot call any
other route of a hosted `serve`.

The observable difference is one request. With a key in hand, a script calls a route on the key
allowlist of an OAuth-protected `serve` and gets `200`. The allowlist starts empty, so the tests add
one route to it. After an administrator revokes the key, the same call gets `401`. A test checks both,
and an audit event records each use.

## Detailed design

### Key format and storage

A key has two parts, an identifier and a secret, written `bajutsu_<id>_<secret>`. The identifier is
`secrets.token_hex(8)`. It is random, it is not secret, and it contains no underscore, so
`value.split("_", 2)` splits a key unambiguously. The secret is `secrets.token_urlsafe(32)`. The
server stores the identifier and a SHA-256 hash of the secret. It also stores the organization, the
role, the creator, the creation time, an expiry, and the last-used time. It never stores the secret. A
request is checked by looking up the identifier and comparing the hash with `hmac.compare_digest`.

The rows live in the repository. A new Alembic migration, the next one under
`bajutsu/serve/server/migrations/versions/`, creates the table. New methods on the `Repository`
protocol and on `sql_repository.py` create a key, list the keys of an organization, get one by
identifier, revoke one, revoke every key of an organization, and touch its last-used time. The table
holds a plain foreign key to the organization, like the other tables that name one.

Retiring an organization is a soft delete, so a cascade would never fire. `delete_org` therefore calls
the new `revoke_org_keys(org_id)` beside `revoke_machine_sessions`. The key lookup also refuses, with
`401`, a key whose organization `get_org` no longer returns.

### Issuing, listing, and revoking

Three routes serve the keys. The path names the organization, so the admin branch of `required_role`
that already gates `/api/orgs/` applies to all three verbs, the way `machine-sessions/revoke` is gated.
A route under the singular `/api/org` would skip that branch and let any signed-in viewer issue keys.

- `POST /api/orgs/<slug>/keys` creates a key from a name, a role of `viewer` or `editor`, and an
  expiry of up to 365 days (default 90). The reply carries the key once, and no later call returns it.
- `GET /api/orgs/<slug>/keys` lists each key's identifier, name, role, creator, expiry, and last-used
  time.
- `DELETE /api/orgs/<slug>/keys/<id>` revokes a key. The next request with it gets `401`. An
  identifier that belongs to another organization answers `404`, never `403`, so the route does not
  confirm that the key exists.

A key never has the `admin` role, so a key cannot issue or revoke keys. Rotation takes two steps: issue
a new key, then revoke the old one.

### The gate and the principal

A bearer value is a key only when it parses to the key shape and its identifier exists. Any other value
falls through to the shared-token check, so an operator's token that happens to start with `bajutsu_`
keeps working. A key that fails the lookup, has expired, or has been revoked gets `401`, with no hint
about which part failed.

A valid key yields a new kind of principal. This item adds the value `api-key` to `PrincipalKind`
and a `role` field to `Principal`. Only the bearer check ever builds one. `kind_from_stored` keeps
narrowing a *stored* kind to `human` or `machine` alone, and `Principal.from_stored` never reads
`role`, so a session row cannot claim the key kind and skip the role gate below. The form is
`Principal(kind="api-key", identity="api-key:<id>", org, role)`. Both backends branch on that
kind before the machine branch and before `forbidden_for_role`, which reads a user row that a key
does not have. Both backends put the organization into the per-request slot that `_machine_org`
uses, so `state.org_for` returns the key's organization and never `default`. Both backends clear
that slot on every request, as the code already does for machine sessions.

The role check runs when `required_role(method, path)` returns a role. The request passes only when
`role_allows(key.role, that role)` is true. The audit and operations log actor is `api-key:<id>`.

The gate then applies a default-deny allowlist, `_API_KEY_PATHS`, shaped like the existing
`_MACHINE_PATHS`. The allowlist starts empty, and each item that adds a key-callable route adds that
route to it.

### Where keys work

The key rows live in the SQL repository. Without a repository, `serve` has no per-organization
identity, and the shared token already grants full access. The key routes therefore answer `409`
there.

### Audit and logs

Issuing, revoking, and each use write an audit event that names the key identifier and never the
secret. `audit_log.actor_id` is a foreign key to `users.id`, and a key has no user row, so
`_record_audit` gains an `api-key` branch beside its machine one. The row carries a null
`actor_id`, and the key identifier goes into `detail`. The last-used time updates at most once a
minute for each key. The key never appears in a URL, a log line, or an error message.

### Tests and documentation

The tests cover these cases:

- Issuing returns the key once, and the stored row holds no secret.
- A viewer session and an editor session each get `403` on all three routes.
- A delete with an identifier from another organization gets `404`.
- A valid key passes an allowlisted route, the request lands in the key's organization and not in
  `default`, and an unlisted route is refused.
- An expired key, a revoked key, and a wrong secret each get `401`.
- A shared token that starts with `bajutsu_` still authenticates a worker.
- A key cannot reach the key routes, and a viewer key cannot call an editor route.
- Retiring an organization makes its keys answer `401`.

`docs/self-hosting.md`, `docs/cli.md`, and their `docs/ja/` mirrors describe the routes.

## Alternatives considered

- **Exchange an OIDC token.** [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md)
  suits CI, where the provider issues the token. A laptop has no provider token.
- **Widen the worker bearer token.** A worker is not a user. Letting that token reach user routes would
  erase the boundary that `_is_worker_route` draws.
- **Per-user personal tokens.** They carry a person's identity, and they need a lifecycle for people who
  leave. An organization key matches how a team shares one credential, and an administrator controls it.
- **Mutual TLS.** It costs more to operate, and it still needs a mapping from certificate to role.

## Progress

> Keep this current as work proceeds. The checklist mirrors the MECE work breakdown in
> *Detailed design* (one box per unit of work); the log records what changed and when
> (oldest first), linking the PRs.

- [x] Store: the Alembic migration, the `Repository` methods, the hashing, and the revocation when an organization is retired.
- [x] Gate: key recognition with the fall-through, the principal, the per-request organization slot, the role check, and the allowlist.
- [x] Routes: issue, list, and revoke under `/api/orgs/<slug>/keys`, with the admin gate, the `404` across organizations, and the `409` outside repository mode.
- [x] Audit: the events and the throttled last-used time.
- [x] Tests: the cases above.
- [x] Documentation: the `docs/` pages and their `docs/ja/` mirrors.

Log:

- [#2558](https://github.com/bajutsu-e2e/bajutsu/pull/2558): the six units landed in one change. A revoked key keeps its row, so its identifier is still
  recognized and answers `401` rather than falling through to the shared-token check. Every admitted
  use is audited, and a refused one at most once a minute for each key. A write route that names no
  role requires `editor` of a key. The admin's issue and revoke entries sit in the acting
  admin's organization, like the other `/api/orgs/…` actions, and name the key's organization in
  their detail. `docs/cli.md` has no section on the HTTP routes, so it carries a short pointer to
  `docs/self-hosting.md`.

## References

- [BE-0414](../BE-0414-ci-oidc-machine-identity/BE-0414-ci-oidc-machine-identity.md): the machine identity that CI uses today.
- [BE-0313](../BE-0313-github-org-team-rbac/BE-0313-github-org-team-rbac.md): the roles and the worker routes that the gate enforces.

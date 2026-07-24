"""Identity, RBAC, and audit for serve (BE-0015).

The "who are you / what may you do / record what was done" slice of the serve operations, split
out of `operations.py` so that god object holds orchestration rather than the auth concern. The
HTTP shells still reach these through the `operations` facade (which re-exports them), so the
transport layer is unchanged. Every function takes the `ServeState`; none touches the transport.
"""

from __future__ import annotations

import secrets
from typing import Any

from bajutsu.serve.helpers import load_serve_config_file
from bajutsu.serve.orgs import identity_matches_org, org_for_identity, org_for_target
from bajutsu.serve.state import ServeState


def login(state: ServeState, token: str) -> tuple[Any, int, str | None]:
    """Validate the shared token and, on success, mint a session id for the shell to set as a
    cookie. Returns ``(payload, status, session_id | None)``.

    Disabled once GitHub OAuth is configured (BE-0313): a human then signs in exclusively through
    `/api/oauth/login`, so the token no longer buys a human session — it authorizes only worker
    traffic. Without OAuth (the single-Mac token deployment) this path is unchanged."""
    if state.auth.oauth is not None:
        return {"error": "token login disabled"}, 404, None
    if not state.auth.check_token(token):
        return {"error": "invalid token"}, 401, None
    return {"ok": True}, 200, state.auth.issue_session()


def oauth_login(state: ServeState) -> tuple[Any, int, str | None]:
    """Begin GitHub OAuth (BE-0015 7b-2). Returns the authorize URL to redirect to plus a fresh CSRF
    *state* value the transport sets as a short-lived cookie and compares on callback. 404 when OAuth
    is not configured. Returns ``(payload, status, state | None)``."""
    if state.auth.oauth is None:
        return {"error": "oauth not configured"}, 404, None
    csrf = secrets.token_urlsafe(24)
    return {"redirect": state.auth.oauth.authorize_url(csrf)}, 200, csrf


def oauth_callback(
    state: ServeState, code: str, state_param: str, state_cookie: str
) -> tuple[Any, int, str | None]:
    """Complete GitHub OAuth (BE-0015 7b-2, BE-0313): verify the CSRF state (the query value must
    match the cookie), exchange the code for a GitHub identity (login + org + Team memberships), gate
    sign-in on GitHub org membership, persist the user under their resolved org with a Team-derived
    role, and on success mint a session bound to that login. Returns
    ``(payload, status, session_id | None)``."""
    if state.auth.oauth is None:
        return {"error": "oauth not configured"}, 404, None
    if not (state_param and state_cookie and secrets.compare_digest(state_param, state_cookie)):
        return {"error": "invalid oauth state"}, 403, None
    try:
        identity = state.auth.oauth.fetch_identity(code)
    except Exception:
        # The exchange talks to GitHub (network / token parsing); a failure is an upstream error,
        # not a 500 — surface it as a clean 502 rather than a traceback.
        return {"error": "oauth exchange failed"}, 502, None
    if identity is None or not identity.login:
        return {"error": "oauth exchange failed"}, 403, None
    login = identity.login
    # Read the config-declared org model once, for both the sign-in gate and the org/role
    # resolution below (BE-0313). Sign-in is gated on GitHub org membership: a login matching no
    # `members`/`githubOrgs` entry is turned away — the org roster is now the allowlist. This runs
    # at the top level, before the database block, so an OAuth-configured but database-less
    # deployment still gates sign-in rather than admitting every GitHub user.
    parsed = load_serve_config_file(state.config)
    orgs = parsed[1] if parsed is not None else {}
    if not identity_matches_org(orgs, login, identity.orgs):
        return {"error": "user not allowed"}, 403, None
    if state.repository is not None:
        # Persist the identity into the system of record, so audit entries and RBAC can reference
        # the user. The org comes from the config-declared org model — an explicit member listing or
        # the user's GitHub org membership. email is unknown from this scope, so we store GitHub's
        # canonical no-reply form (valid + unique per login).
        org = org_for_identity(orgs, login, identity.orgs)
        oc = orgs.get(org)
        editor_team = oc.editor_team if oc is not None else None
        state.repository.ensure_org(org, slug=org, name=org)
        state.repository.upsert_user(
            login,
            org_id=org,
            github_login=login,
            email=f"{login}@users.noreply.github.com",
            # Recompute the role from GitHub Team membership on every login, so leaving a Team takes
            # effect on next login without a data migration (BE-0015 7c-2, BE-0313).
            role=role_for(
                teams=identity.teams,
                editor_team=editor_team,
                admin_team=state.auth.oauth_admin_team,
            ),
        )
    return {"ok": True, "user": login}, 200, state.auth.issue_session(identity=login)


def _target_forbidden(state: ServeState, org: str, target: str) -> bool:
    """True when an actor resolved to *org* may not touch *target* because the target belongs to a
    different org (BE-0015 multi-tenancy). Org scoping applies only on a server backend with a
    system of record; local serve / token mode has no identity to scope to and ignores `orgs:`
    entirely. A target not declared under `targets:` is "unknown", not cross-org — the caller handles it
    as a missing target downstream. *org* is resolved once by the caller (via `ServeState.org_of`)."""
    if state.repository is None:
        return False
    parsed = load_serve_config_file(state.config)
    if parsed is None or target not in parsed[0].targets:
        return False
    return org_for_target(parsed[1], target) != org


def _record_audit(
    state: ServeState, actor: str | None, org: str, action: str, target: str, detail: dict[str, Any]
) -> None:
    """Append an audit entry (who did what, when) when a database is wired and the actor is known.
    A no-op otherwise — local, no database, or a shared-token request with no identity (BE-0015 7c-1).
    *org* is the actor's org, resolved once by the caller."""
    if state.repository is None or not actor:
        return
    state.repository.record_audit(
        org_id=org,
        actor_id=actor,
        action=action,
        target=target,
        detail=detail,
    )


# --- RBAC (BE-0015 7c-2): role-based access control over the mutating endpoints ---

_ROLE_RANK = {"viewer": 0, "editor": 1, "admin": 2}
# Server-wide settings — including binding the active config, from the file browser, Git, or an
# uploaded bundle (BE-0073): each repoints which config the whole server serves.
_ADMIN_PATHS = frozenset(
    {
        "/api/config",
        "/api/upload",
        # The three independently-uploadable artifacts (BE-0268) repoint what a future composed
        # run serves, same as `/api/upload`. `/api/artifacts/exists` (a GET) is deliberately NOT
        # listed here — this set is only ever consulted below the `method != "POST"` guard, so a
        # GET path here would be silently ungated dead code (exactly the mistake `/api/config/content`
        # already works around with its own early-returning case); `/api/artifacts/exists` gets the
        # same explicit early case instead.
        "/api/artifacts/config",
        "/api/artifacts/scenarios",
        "/api/artifacts/binary",
        # Composing a stored triple into the active config (BE-0268) repoints what the whole server
        # serves, exactly like binding an uploaded bundle — the same admin tier as `/api/upload`.
        "/api/compose",
        "/api/apikey",
        "/api/claudecodetoken",
        "/api/gitcredential",
        # Setting a scenario-declared secret (BE-0274) is the same credential-write tier as the three
        # above. Only POST is gated here; GET /api/secrets is describe-only and ungated (this set is
        # consulted only past the `method != "POST"` guard in `required_role`).
        "/api/secrets",
        "/api/provider",
        "/api/ant/login",
    }
)
_EDITOR_PATHS = frozenset(
    {
        "/api/run",
        "/api/record",
        "/api/crawl",
        "/api/scenario",
        "/api/approve",
        "/api/capture/start",
        "/api/capture/mark",
        "/api/capture/finish",
    }
)


def role_for(*, teams: list[str], editor_team: str | None, admin_team: str | None) -> str:
    """The role for a login from its GitHub Team memberships (BE-0313): admin if a member of the
    server-wide *admin_team*, editor if a member of the resolved org's *editor_team*, else viewer
    (the base role every signed-in user gets). *teams* are `"<github-org>/<team-slug>"` direct
    memberships; an unset team never matches. Recomputed on every login (BE-0015 7c-2)."""
    if admin_team is not None and admin_team in teams:
        return "admin"
    if editor_team is not None and editor_team in teams:
        return "editor"
    return "viewer"


def required_role(method: str, path: str) -> str | None:
    """The minimum role a request needs, or None for reads (GET) and the open auth endpoints.
    Cancelling a job or answering its handoff are editor actions (they mutate a running job). The
    gated reads are ``GET /api/config/content``, ``GET /api/artifacts/exists`` and
    ``GET /api/version/checkout`` (all admin), a wider disclosure than their paths."""
    # Config content is the one gated GET: it returns the active config's full body, a wider
    # disclosure than the path-only `/api/config`, and a local/uploaded config may embed literal
    # secrets. Gate it like binding the config (admin) so a viewer/editor can't read it.
    if method == "GET" and path == "/api/config/content":
        return "admin"
    # The per-artifact exists check (BE-0268) confirms whether a given sha256 is already stored —
    # the same admin tier as the upload routes it complements, so a viewer can't probe artifact
    # existence. Needs its own early case, like `/api/config/content` above: it's a GET, and the
    # generic `_ADMIN_PATHS` membership check below is only ever reached past the
    # `method != "POST"` guard, so a GET path added there would silently never gate.
    if method == "GET" and path == "/api/artifacts/exists":
        return "admin"
    # The server's own Git checkout (BE-0272): commit / branch / dirty. The branch name routinely
    # encodes an in-progress BE slug (`claude/<topic>`), so on a shared deployment it leaks what's
    # being worked on — gate it like the other wider-disclosure reads. The version string alone
    # (`/api/version`) stays open. Same early-case reason as the two GETs above.
    if method == "GET" and path == "/api/version/checkout":
        return "admin"
    # Project hub (BE-0225): registering / deregistering a project, or activating one (unit 4's
    # switcher rebinds the live config), all repoint a config binding, so each is an admin action like
    # `/api/config`; triggering a run is an editor action like `/api/run`. Listing is a read. Handled
    # ahead of the `method != "POST"` guard below because deregister is a DELETE.
    if path == "/api/projects" or path.startswith("/api/projects/"):
        if method == "POST" and path.endswith("/run"):
            return "editor"
        if method in ("POST", "DELETE"):
            return "admin"
        return None
    # Run lifecycle (BE-0239): soft-delete (DELETE /api/runs/{id} or /api/crawl/runs/{id}), restore
    # (POST .../restore), and bulk-delete (POST /api/runs/bulk-delete) are editor actions, like
    # triggering a run. Permanent purge (``?purge=true``) is admin, but the query string isn't in the
    # `path` seen here, so that gate lives in the operation (`delete_run`/`bulk_delete_runs`). Handled
    # ahead of the POST-only guard because the soft-delete is a DELETE; the worker upload-urls POST
    # keeps its own no-role handling (falls through to None).
    if path.startswith(("/api/runs/", "/api/crawl/runs/")):
        if method == "DELETE":
            return "editor"
        if method == "POST" and (path == "/api/runs/bulk-delete" or path.endswith("/restore")):
            return "editor"
        return None
    if method != "POST":
        return None
    if path in _ADMIN_PATHS:
        return "admin"
    # Cancelling a job or answering its handoff both mutate a running job's state, so both are
    # editor actions — a viewer must not be able to resume/cancel a paused record (BE-0179).
    if path in _EDITOR_PATHS or (
        path.startswith("/api/jobs/") and path.endswith(("/cancel", "/respond-human"))
    ):
        return "editor"
    return None  # /api/login, /api/oauth/* — authenticated/guarded elsewhere, no role gate


def role_allows(role: str, required: str) -> bool:
    """Whether *role* meets the *required* minimum (viewer < editor < admin)."""
    return _ROLE_RANK.get(role, 0) >= _ROLE_RANK.get(required, 0)


def forbidden_for_role(state: ServeState, login: str, method: str, path: str) -> bool:
    """Whether *login* lacks the role for this request — the transport gate calls it for an
    OAuth-authenticated session when a database is wired. A user with no row defaults to viewer."""
    required = required_role(method, path)
    if required is None or state.repository is None:
        return False  # reads, open endpoints, or no database wired (DB-less = full access)
    role = state.repository.user_role(login) or "viewer"  # an unknown user defaults to viewer
    return not role_allows(role, required)

"""Identity, RBAC, and audit for serve (BE-0015).

The "who are you / what may you do / record what was done" slice of the serve operations, split
out of `operations.py` so that god object holds orchestration rather than the auth concern. The
HTTP shells still reach these through the `operations` facade (which re-exports them), so the
transport layer is unchanged. Every function takes the `ServeState`; none touches the transport.
"""

from __future__ import annotations

import secrets
from typing import Any

from bajutsu.config import org_for_identity, org_for_target
from bajutsu.serve.helpers import load_config_file
from bajutsu.serve.jobs import _DEFAULT_ORG, ServeState


def login(state: ServeState, token: str) -> tuple[Any, int, str | None]:
    """Validate the shared token and, on success, mint a session id for the shell to set as a
    cookie. Returns ``(payload, status, session_id | None)``."""
    if not state.check_token(token):
        return {"error": "invalid token"}, 401, None
    return {"ok": True}, 200, state.issue_session()


def oauth_login(state: ServeState) -> tuple[Any, int, str | None]:
    """Begin GitHub OAuth (BE-0015 7b-2). Returns the authorize URL to redirect to plus a fresh CSRF
    *state* value the transport sets as a short-lived cookie and compares on callback. 404 when OAuth
    is not configured. Returns ``(payload, status, state | None)``."""
    if state.oauth is None:
        return {"error": "oauth not configured"}, 404, None
    csrf = secrets.token_urlsafe(24)
    return {"redirect": state.oauth.authorize_url(csrf)}, 200, csrf


def oauth_callback(
    state: ServeState, code: str, state_param: str, state_cookie: str
) -> tuple[Any, int, str | None]:
    """Complete GitHub OAuth (BE-0015 7b-2): verify the CSRF state (the query value must match the
    cookie), exchange the code for a GitHub identity (login + org memberships), check the login
    against the allowlist, persist the user under their resolved org, and on success mint a session
    bound to that login. Returns ``(payload, status, session_id | None)``."""
    if state.oauth is None:
        return {"error": "oauth not configured"}, 404, None
    if not (state_param and state_cookie and secrets.compare_digest(state_param, state_cookie)):
        return {"error": "invalid oauth state"}, 403, None
    try:
        identity = state.oauth.fetch_identity(code)
    except Exception:
        # The exchange talks to GitHub (network / token parsing); a failure is an upstream error,
        # not a 500 — surface it as a clean 502 rather than a traceback.
        return {"error": "oauth exchange failed"}, 502, None
    if identity is None or not identity.login:
        return {"error": "oauth exchange failed"}, 403, None
    login = identity.login
    if login not in state.oauth_allowed_users:
        return {"error": "user not allowed"}, 403, None
    if state.repository is not None:
        # Persist the identity into the system of record, so audit entries and RBAC can reference
        # the user. The org comes from the config-declared org model — an explicit member listing or
        # the user's GitHub org membership — defaulting to the single `default` org. email is unknown
        # from this scope, so we store GitHub's canonical no-reply form (valid + unique per login).
        org = _org_for_login(state, login, identity.orgs)
        state.repository.ensure_org(org, slug=org, name=org)
        state.repository.upsert_user(
            login,
            org_id=org,
            github_login=login,
            email=f"{login}@users.noreply.github.com",
            # Recompute the role from the env policy on every login, so changing the policy takes
            # effect on next login without a data migration (BE-0015 7c-2).
            role=role_for(login, admins=state.oauth_admins, viewers=state.oauth_viewers),
        )
    return {"ok": True, "user": login}, 200, state.issue_session(identity=login)


def _org_for_login(state: ServeState, login: str, github_orgs: list[str]) -> str:
    """The org to assign *login* at OAuth login, from the config-declared org model (BE-0015
    multi-tenancy): an explicit member listing or a matching GitHub org from *github_orgs*. The
    single `default` org when no `orgs:` block maps them."""
    config = load_config_file(state.config)
    return org_for_identity(config, login, github_orgs) if config is not None else _DEFAULT_ORG


def _target_forbidden(state: ServeState, org: str, target: str) -> bool:
    """True when an actor resolved to *org* may not touch *target* because the target belongs to a
    different org (BE-0015 multi-tenancy). Org scoping applies only on a server backend with a
    system of record; local serve / token mode has no identity to scope to and ignores `orgs:`
    entirely. A target not declared under `targets:` is "unknown", not cross-org — the caller handles it
    as a missing target downstream. *org* is resolved once by the caller (via `ServeState.org_of`)."""
    if state.repository is None:
        return False
    config = load_config_file(state.config)
    if config is None or target not in config.targets:
        return False
    return org_for_target(config, target) != org


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
# uploaded bundle (BE-0073): each repoints which config the whole server serves. AWS SSO sign-in
# (BE-0056) also sets AWS_PROFILE server-wide, so its login/logout are admin too.
_ADMIN_PATHS = frozenset(
    {
        "/api/config",
        "/api/upload",
        "/api/apikey",
        "/api/provider",
        "/api/sso/login",
        "/api/sso/logout",
    }
)
_EDITOR_PATHS = frozenset(
    {
        "/api/run",
        "/api/record",
        "/api/crawl",
        "/api/scenario",
        "/api/approve",
    }
)


def role_for(login: str, *, admins: frozenset[str], viewers: frozenset[str]) -> str:
    """The role for *login* under the env policy: admin if listed, viewer if listed, else editor
    (the default — an allowlisted user can run). Recomputed on every login (BE-0015 7c-2)."""
    if login in admins:
        return "admin"
    if login in viewers:
        return "viewer"
    return "editor"


def required_role(method: str, path: str) -> str | None:
    """The minimum role a request needs, or None for reads (GET) and the open auth endpoints.
    Cancelling a job is an editor action (it stops a run)."""
    if method != "POST":
        return None
    if path in _ADMIN_PATHS:
        return "admin"
    if path in _EDITOR_PATHS or (path.startswith("/api/jobs/") and path.endswith("/cancel")):
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

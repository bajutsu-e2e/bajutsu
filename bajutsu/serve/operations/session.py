"""The session's own org selection: which orgs this login may act as, and switching between them.

A login that belongs to more than one org picks which one the current browser session acts as, and
switches without signing out (session-scoped org selection). The choice lives on the session, so two
windows can hold two tenants at once, and every request resolves its org from the session it arrived
on rather than from the single org the user row records.

Both operations re-derive the candidates from the *current* org roster and the GitHub organizations
the session's own sign-in observed, so an admin's membership edit reaches a session that signed in
before it, and a slug a client replays from a stale list reaches nothing it never qualified for. All
logic is deterministic: a membership list is data, and the role policy is the same machine-checkable
comparison sign-in applies; no LLM enters the path.
"""

from __future__ import annotations

from typing import Any

from bajutsu.serve.authz import _record_audit, org_model, role_for
from bajutsu.serve.orgs import orgs_for_identity
from bajutsu.serve.sessions import Caller
from bajutsu.serve.state import ServeState

# The org roster lives in the database on a server deployment, so a read that fails is an outage of
# ours — answer with the store, not with a denial that blames the caller's GitHub membership.
_STORE_UNAVAILABLE = ({"error": "the org store is unavailable"}, 503)
# Not 401: the request is authenticated (the gate let it through). It simply carries no identity to
# select an org for — a shared-token session belongs to no org.
_NO_IDENTITY = ({"error": "no signed-in identity"}, 403)


def candidate_orgs(state: ServeState, session_id: str | None, caller: Caller | None) -> list[str]:
    """The orgs this session may act as, or an empty list when it may act as none.

    Empty covers every deployment with nothing to choose between: no signed-in identity, a session
    that recorded no GitHub facts (issued before the selection existed), and an unreadable roster —
    all of which leave the acting org exactly as it is today.
    """
    if caller is None or session_id is None:
        return []
    record = state.auth.sessions.context(session_id)
    if record is None or record.login is None:
        return []
    try:
        orgs = org_model(state)
    except Exception:
        # A roster we cannot read offers no choices. The switch endpoint below reports the outage;
        # this one feeds the boot read, which must keep answering.
        return []
    return orgs_for_identity(orgs, record.login, list(record.github_orgs))


def switch_org(
    state: ServeState,
    body: dict[str, Any],
    *,
    session_id: str | None = None,
    caller: Caller | None = None,
) -> tuple[Any, int]:
    """Point this session at the org named by ``{"org": "<slug>"}``, with that org's role.

    Any signed-in identity may call it: choosing among the orgs a login already qualifies for grants
    nothing it could not reach by signing in again. The slug is checked against the candidates
    recomputed here rather than trusted from the client, and the role is recomputed from the GitHub
    Teams the session recorded, so a switch cannot carry another org's role with it.
    """
    if caller is None or session_id is None:
        return _NO_IDENTITY
    record = state.auth.sessions.context(session_id)
    if record is None or record.login is None:
        return _NO_IDENTITY
    slug = body.get("org")
    if not isinstance(slug, str) or not slug.strip():
        return {"error": "org is required"}, 400
    slug = slug.strip()
    try:
        orgs = org_model(state)
    except Exception:  # the store is down, not the caller's membership
        return _STORE_UNAVAILABLE
    candidates = orgs_for_identity(orgs, record.login, list(record.github_orgs))
    if slug not in candidates:
        return {"error": f"not a member of {slug!r}"}, 403
    oc = orgs.get(slug)
    role = role_for(
        teams=list(record.teams),
        editor_team=oc.editor_team if oc is not None else None,
        admin_teams=state.auth.oauth_admin_teams,
    )
    if not state.auth.sessions.select_org(session_id, slug, role):
        return {"error": "session expired"}, 401
    # Audited under the org being *entered*: an audit of a tenant's activity has to open with the
    # moment a session started acting as it.
    _record_audit(state, caller, slug, "session.org", slug, {"role": role})
    return {"org": slug, "role": role, "orgOptions": candidates}, 200

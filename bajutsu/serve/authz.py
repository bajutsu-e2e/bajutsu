"""Identity, RBAC, and audit for serve (BE-0015).

The "who are you / what may you do / record what was done" slice of the serve operations, split
out of `operations.py` so that god object holds orchestration rather than the auth concern. The
HTTP shells still reach these through the `operations` facade (which re-exports them), so the
transport layer is unchanged. Every function takes the `ServeState`; none touches the transport.
"""

from __future__ import annotations

import logging
import re
import secrets
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from bajutsu.serve import oplog
from bajutsu.serve.helpers import load_serve_config_file
from bajutsu.serve.orgs import (
    OrgConfig,
    identity_matches_org,
    in_teams,
    orgs_declaring_membership,
    orgs_for_identity,
    orgs_from_db,
    preferred_org,
)
from bajutsu.serve.state import ServeState

if TYPE_CHECKING:  # keeps the default serve/CLI path free of `serve.server` (server/__init__.py)
    from bajutsu.serve.server.oauth import Identity

_logger = logging.getLogger(__name__)


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


@dataclass(frozen=True)
class _OrgModel:
    """The org model `oauth_callback` resolved for this deployment, and which source produced it.

    One source per deployment, chosen once (BE-0375): the database when a repository is wired,
    the `orgs:` block otherwise — never both, and never a fallback between them. *parsed* and
    *config* carry the config path's own state and are meaningful only when `from_db` is False,
    which is why they are bundled with the flag that decides whether to read them rather than
    threaded through the callers separately.
    """

    orgs: dict[str, OrgConfig]
    from_db: bool
    parsed: tuple[Any, dict[str, OrgConfig]] | None = None
    config: Path | None = None

    def unmatched(self, identity: Identity) -> tuple[str, bool]:
        """Why this model matched nothing for *identity*, and whether that is operator-actionable.

        The cause names what an operator should go look at, so it differs by source: a config-sourced
        model can be unbound, unreadable, or blockless — shapes only a file can take — while a
        database-sourced one collapses all three into a table in which no row declares membership
        yet, since a database `serve` cannot read never reaches here at all (`orgs_from_db` raises
        instead of failing closed).

        The two returns travel together because both records below need them in lock-step: an
        operator-actionable shape is the one worth WARNING about, and an earlier revision let the
        message and the level drift apart by computing them at separate call sites.
        """
        if not self.from_db:
            if self.parsed is None:
                return (
                    # `load_serve_config_file` returns None immediately when no path is bound at all
                    # — the ordinary bootstrap state `serve()` treats as normal ("open a config.yml
                    # in the UI") — not only when a bound path fails to load. Collapsing the two
                    # would send an operator hunting a YAML typo in a file that never existed yet.
                    ("no serve config is bound", True)
                    if self.config is None
                    else ("the serve config failed to load", True)
                )
            if not self.orgs:
                return "the serve config declares no orgs: block", True
        elif not orgs_declaring_membership(self.orgs):
            # Not `not self.orgs`: the bypass sign-in this reports calls `ensure_org` on its way
            # past, so a passive `default` row lands in the table — and every later sign-in would
            # read a non-empty table and quietly drop to INFO while the deployment still admits
            # nobody but admin-Team members. What an operator can act on is that no row declares
            # membership yet, not that no row exists.
            return "no org in the orgs table declares any membership yet", True
        if not identity.orgs:
            # An empty org list stays the primary signal, since a `/user/orgs` outage looks exactly
            # like it. Naming the Team list too, and only when it is *also* empty, keeps that reading
            # intact while covering the deployment whose gate is a `githubTeams`/`editorTeams` entry:
            # there the org list is beside the point, and blaming it alone would send an operator to
            # an `orgs:` axis that entry never consults. A login carrying Teams but no orgs is
            # unchanged -- one fetch came back, so only the other is worth naming.
            return (
                "GitHub returned no orgs or teams for this login"
                if not identity.teams
                else "GitHub returned no orgs for this login"
            ), False
        return "no org membership matched this login", False


def _resolve_org_model(state: ServeState) -> _OrgModel:
    """The one org model this deployment's sign-in resolves against (BE-0375).

    The database once a repository is wired — the same condition that gates every other
    database-backed seam in `serve` — so a configuration that fails to load no longer turns every
    sign-in into a denial on a deployment whose database already knows exactly who its users are.
    A database-less deployment keeps reading the `orgs:` block exactly as before.

    Raises:
        Exception: whatever the repository read failed with, so the caller answers with an error
            naming the database rather than an empty roster that reads as "you don't belong".
    """
    if state.repository is not None:
        return _OrgModel(orgs=orgs_from_db(state.repository), from_db=True)
    parsed = load_serve_config_file(state.config)
    return _OrgModel(
        orgs=parsed[1] if parsed is not None else {},
        from_db=False,
        parsed=parsed,
        config=state.config,
    )


def oauth_callback(
    state: ServeState, code: str, state_param: str, state_cookie: str
) -> tuple[Any, int, str | None]:
    """Complete GitHub OAuth (BE-0015 7b-2, BE-0313): verify the CSRF state (the query value must
    match the cookie), exchange the code for a GitHub identity (login + org + Team memberships), gate
    sign-in on an org's declared membership or on a configured admin Team, persist the user under
    their resolved org with a Team-derived role, and on success mint a session bound to that login.
    Returns ``(payload, status, session_id | None)``."""
    if state.auth.oauth is None:
        # A half-configured deployment (one of the three BAJUTSU_OAUTH_GITHUB_* vars unset) 404s
        # here for every GitHub sign-in -- but that is not a lockout: `login`'s shared-token path is
        # disabled only when `oauth is not None`, so this same `None` re-enables it, and the
        # deployment silently reverts to the shared-token login it was meant to replace (a token
        # session carries no identity, so `forbidden_for_role` short-circuits and has full access).
        # `oauth is None` is a static property of the deployment, not a per-request signal, and this
        # endpoint takes unauthenticated traffic unconditionally -- a loop against it would write one
        # WARNING per request forever, on a deployment that may not even use OAuth. INFO still leaves
        # a record; the loud, once-per-boot signal for this deployment shape is
        # `_build_server_state`'s "oauth is only partly configured" `server.startup_warning`, not a
        # per-request WARNING an anonymous caller sets the volume of.
        oplog.log_event(_logger, "oauth.denied", "oauth not configured", level=logging.INFO)
        return {"error": "oauth not configured"}, 404, None
    if not (state_param and state_cookie and secrets.compare_digest(state_param, state_cookie)):
        # Repeated mismatches are the signature of a login-CSRF attempt, not just an expired
        # cookie -- but that is a rate claim, and nothing available at this point distinguishes an
        # attack from an expired cookie on any single request. `state_param`/`state_cookie` are both
        # caller-supplied (a query value and the caller's own Cookie: header), so gating the level on
        # "both present" filters only the laziest possible probe: an attacker who sends any two
        # non-matching values lands on WARNING just as cheaply as the bare no-state case. Recording
        # at INFO unconditionally is the honest level for a per-request record; a genuine rate signal
        # belongs in a counter an operator can threshold (the GET /metrics surface, BE-0169), not in
        # a log level an anonymous caller picks for themselves.
        oplog.log_event(_logger, "oauth.denied", "oauth state mismatch", level=logging.INFO)
        return {"error": "invalid oauth state"}, 403, None
    try:
        identity = state.auth.oauth.fetch_identity(code)
    except Exception:
        # The exchange talks to GitHub (network / token parsing); a failure is an upstream error,
        # not a 500 — surface it as a clean 502 rather than a traceback. Reaching this line needs no
        # real GitHub auth: an attacker sets *both* `state_param` (a query value) and `state_cookie`
        # (their own Cookie: header) to the same value, clears the CSRF check above for free, and
        # supplies any garbage `code` — GitHub's token endpoint then errors and this branch fires.
        # INFO for the same reason as the branches above: a per-request WARNING an anonymous caller
        # can trigger this cheaply isn't a signal an operator can alert on (BE-0352).
        oplog.log_event(_logger, "oauth.denied", "oauth exchange failed", level=logging.INFO)
        return {"error": "oauth exchange failed"}, 502, None
    if identity is None or not identity.login:
        # Reachable the same caller-controlled way as the exception case above.
        oplog.log_event(
            _logger, "oauth.denied", "oauth exchange returned no identity", level=logging.INFO
        )
        return {"error": "oauth exchange failed"}, 403, None
    login = identity.login
    # Read the org model once, for both the sign-in gate and the org/role resolution below
    # (BE-0313). Sign-in is gated on an org's declared membership: a login matching no
    # `members`/`githubOrgs`/`githubTeams`/`editorTeams` entry is turned away — unless it also matches
    # a configured admin Team, in which case the admin-Team check below admits it regardless. The
    # gate and the placement below read the one ranking in `orgs._match_org`, so a login admitted
    # through one org's Team is never filed under another. This runs at the top level,
    # before the database block, so an OAuth-configured but database-less deployment still gates
    # sign-in rather than admitting every GitHub user. Only the model's *source* depends on whether
    # a database is wired (BE-0375); where the gate itself sits is unchanged.
    try:
        model = _resolve_org_model(state)
    except Exception as exc:
        # The database this deployment's org model lives in is unreadable. Denying every login here
        # would blame their GitHub membership for an outage of ours, so answer with a 5xx that names
        # the store instead — the opposite of `load_serve_config_file`'s fail-closed shape, and the
        # reason `orgs_from_db` propagates rather than collapsing to an empty mapping. The exception
        # *type* is diagnostic enough here; its message can carry the database URL, which has no
        # place in a log an operator greps for sign-in failures. Under its own event, not
        # `oauth.denied`: this request is answered 503, so it is not a denial and must not be
        # counted as one — and an operator alerting on `oauth.denied` at WARNING is watching for an
        # unusable admin Team (nobody left who can sign in and fix `orgs:`), a diagnosis a store
        # outage would send them chasing through `BAJUTSU_OAUTH_ADMIN_TEAMS` for nothing.
        oplog.log_event(
            _logger,
            "oauth.store_unavailable",
            f"the org database could not be read ({type(exc).__name__})",
            level=logging.WARNING,
            actor=login,
        )
        return {"error": "the org store is unavailable"}, 503, None
    orgs = model.orgs
    admin_teams = state.auth.oauth_admin_teams
    # A member of a configured admin Team clears the sign-in gate directly, even when no `orgs:`
    # entry lists their GitHub organization (or `orgs:` is absent entirely) — an admin must be able to
    # sign in and repoint a broken or incomplete `orgs:` config, not be locked out by the same config
    # mistake they exist to fix.
    is_admin_team_member = in_admin_team(identity.teams, admin_teams)
    matched_org = identity_matches_org(orgs, login, identity.orgs, identity.teams)
    if not matched_org and not is_admin_team_member:
        # A rejection gets its own event (not `oauth.login`, which stays "login count"). The
        # message is keyed on the same `admin_teams_unusable` predicate as the level below, so
        # the two can't drift apart (BE-0352).
        admin_note = (
            "no usable admin Team is configured"
            if admin_teams_unusable(admin_teams)
            else "no admin Team matched"
        )
        # `GET /api/oauth/login` is unauthenticated and GitHub authorizes any of its own users, not
        # just this deployment's members -- an ordinary denial (admin_teams configured, this login
        # just isn't in orgs: or it) is reachable by any curious visitor with a free GitHub account,
        # not only the deployment's operators. Reserve WARNING for the shape an operator actually
        # needs paging on: `admin_teams_unusable` -- empty, or every entry malformed, so nobody can
        # sign in to fix orgs: either. A non-empty but entirely malformed list (a space-separated
        # value collapsing to one entry that can never match, say) is functionally the same lockout
        # as an empty one; checking `not admin_teams` alone would call it an ordinary INFO denial.
        # Every other denial still gets a record, just at INFO (BE-0352).
        oplog.log_event(
            _logger,
            "oauth.denied",
            f"{login} rejected: {model.unmatched(identity)[0]}, and {admin_note}",
            level=logging.WARNING if admin_teams_unusable(admin_teams) else logging.INFO,
            actor=login,
        )
        return {"error": "user not allowed"}, 403, None
    if state.repository is not None:
        # Persist the identity into the system of record, so audit entries and RBAC can reference
        # the user. The org comes from the org model resolved above — an explicit member listing or
        # the user's GitHub org membership. email is unknown from this scope, so we store GitHub's
        # canonical no-reply form (valid + unique per login).
        #
        # BE-0313's org-recovery guard (keep a bypass-admitted user's recorded org rather than
        # relocating them to `default` when the config failed to load) is gone with its cause: this
        # block only ever ran on a database-backed deployment, and there the database now decides
        # org placement, so no config load can misplace anyone. `orgs_from_db` raises rather than
        # presenting an empty roster, so there is no silent "everything is empty" state left to
        # mistake for a real one.
        eligible = orgs_for_identity(orgs, login, identity.orgs, identity.teams)
        # Every org this login may act as, each with the role it resolves to there — the set the
        # header's selector offers and the switch endpoint authorizes against. Recomputed here on
        # every sign-in for the same reason the role is: this is the only moment the login's GitHub
        # organization and Team memberships are known, since no GitHub token is kept afterwards.
        memberships = {
            candidate: role_for(
                teams=identity.teams,
                editor_teams=orgs[candidate].editor_teams,
                admin_teams=admin_teams,
            )
            for candidate in eligible
        }
        org = _active_org(state, login, eligible, is_admin_team_member)
        oc = orgs.get(org)
        editor_teams = oc.editor_teams if oc is not None else []
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
                editor_teams=editor_teams,
                admin_teams=admin_teams,
            ),
        )
        # After `upsert_user`, so the row these rows reference exists on a first sign-in.
        state.repository.set_user_orgs(login, memberships)
    # Record every successful sign-in through oplog (not a bare logging call) so it carries the
    # registered `event` name, redaction, and correlation fields every other
    # operationally-significant record in serve already does. `bypass` says which gate admitted
    # this one — the one sign-in path `orgs:` did not authorize is still the interesting case, but
    # emitting the event only for that case would make `event=oauth.login` mean "bypass" instead of
    # "login", the opposite of what an operator's alert on the event name would expect.
    #
    # `not matched_org` alone is not the WARNING signal: this item's own guidance puts a correctly
    # configured admin Team in an operations-only GitHub organization no `orgs:` entry lists, so
    # `matched_org` is `False` on *every* admin sign-in there, permanently -- the normal operating
    # condition of a working deployment, not something worth paging on. What IS worth paging on is
    # the org model itself being unusable -- on the configuration path `parsed is None` (no config
    # bound, or one that failed to load) or `not orgs` (a config that loaded but declares no `orgs:`
    # block at all), on the database path a table in which no row declares membership yet. Either
    # way the bypass just admitted a login into a deployment nobody but an admin Team member can
    # currently sign in to repair. A GitHub-side outage (`not identity.orgs`) or a real, unmatching
    # roster stay INFO: the org model itself is fine there, so there is nothing an admin needs paged
    # in to fix. Key the level on which of `_OrgModel.unmatched`'s shapes this is, not on
    # `matched_org`, so `bypass` keeps varying on what an operator greps while `WARNING` keeps
    # meaning "something is wrong." Cause and level come from the one call, so neither can be
    # rewritten without the other.
    cause, operator_actionable = model.unmatched(identity)
    oplog.log_event(
        _logger,
        "oauth.login",
        (
            f"admin-Team bypass admitted {login}: {cause}"
            if not matched_org
            else f"{login} signed in"
        ),
        level=logging.WARNING if not matched_org and operator_actionable else logging.INFO,
        bypass=not matched_org,
        actor=login,
    )
    return {"ok": True, "user": login}, 200, state.auth.issue_session(identity=login)


def _active_org(
    state: ServeState, login: str, eligible: list[str], is_admin_team_member: bool
) -> str:
    """The org this sign-in lands *login* in: their own pick when it still holds, else the ranking's.

    A user who picked an org from the header selector keeps it across sign-ins, which is the whole
    point of letting them pick — re-resolving on every sign-in would undo the choice on the next
    login. A pick holds only while the org is still one they may act as: an org's membership is the
    deployment's to decide, so losing it must relocate them, exactly as it does for a user who never
    picked anything. An admin admitted by their admin Team may act as any live org (BE-0352 admits
    them without an org's membership, so `eligible` is routinely empty for them), which is why the
    two cases are asked separately here rather than both read off `eligible`.

    An org merely *resolved* for the user at an earlier sign-in is not a pick and gets no such
    protection: `user_selected_org` answers only for a deliberate choice, so a login whose
    `githubOrgs` entry was revoked still re-resolves the way it does today (BE-0015 7c-2).
    """
    repository = state.repository
    assert repository is not None  # the caller's `state.repository is not None` branch
    picked = repository.user_selected_org(login)
    if picked is not None and (
        picked in eligible or (is_admin_team_member and repository.get_org(picked) is not None)
    ):
        return picked
    return preferred_org(eligible)


def _target_forbidden(state: ServeState, org: str, target: str) -> bool:
    """True when an actor resolved to *org* may not touch *target* because *org* does not own it
    (BE-0015 multi-tenancy). Org scoping applies only on a server backend with a system of record;
    local serve / token mode has no identity to scope to and ignores `orgs:` entirely. A target not
    declared under `targets:` is "unknown", not cross-org — the caller handles it as a missing target
    downstream. *org* is resolved once by the caller (via `ServeState.org_of`).

    A target's identity is `(org, target)`, not the name alone (BE-0375): this asks whether *this*
    org owns the target, rather than which single org the name resolves to, so two orgs may each
    claim a `checkout` and each be authorized for it — instead of config order silently awarding it
    to the first and forbidding the second a target `targets_for_org` still shows it. Asked through
    `targets_for_org` rather than by reading an `orgs:` entry directly, because ownership is not
    symmetrical: `default` owns every target no entry claims, a fallback that keys on the literal
    slug, so reading the entry would forbid `default` every target it reaches today.
    """
    if state.repository is None:
        return False
    parsed = load_serve_config_file(state.config)
    if parsed is None or target not in parsed[0].targets:
        return False
    return target not in state.targets_for(org)


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
        # Rebinding the org's remembered config (BE-0404 unit 1) repoints what the whole deployment
        # serves, exactly like binding one — the same admin tier as `/api/config` itself.
        "/api/config/restore",
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
        "/api/scenarios/upload",
        "/api/approve",
        "/api/capture/start",
        "/api/capture/mark",
        "/api/capture/finish",
    }
)


# The one pattern that decides whether an `admin_teams` entry could ever match a real GitHub Team
# ("<github-org>/<team-slug>", no empty half or internal whitespace) -- shared between
# `_build_server_state`'s `admin_teams_malformed` startup check and `admin_teams_unusable` below, so
# the two copies can't drift the way `in_admin_team` and `_unmatched_org_cause` were already factored
# out to prevent. Does not reject an uppercase character in either half; see `in_teams`'s own
# lowercasing for why (BE-0352).
ADMIN_TEAM_ENTRY_RE = re.compile(r"[^\s/]+/[^\s/]+")


def admin_teams_unusable(admin_teams: tuple[str, ...]) -> bool:
    """True when no entry in *admin_teams* could ever match a real Team -- the list is empty, or
    every entry fails `ADMIN_TEAM_ENTRY_RE`. A non-empty but entirely malformed list (e.g. a
    space-separated value that parses to one `"a/b c/d"` entry) is functionally identical to an
    empty one: `in_admin_team` can never match anyone, so a caller that only checks `not
    admin_teams` treats a total lockout as an ordinary, healthy configuration (BE-0352)."""
    return not admin_teams or all(not ADMIN_TEAM_ENTRY_RE.fullmatch(t) for t in admin_teams)


def in_admin_team(teams: Sequence[str], admin_teams: tuple[str, ...]) -> bool:
    """Whether any of *teams* is a server-wide admin Team — the membership test behind both the admin
    role below and `oauth_callback`'s admin-Team sign-in bypass, so the gate that admits a bypassing
    login and the role it resolves to can never drift apart. `orgs.in_teams` does the comparing, the
    same one the per-org sign-in gate uses (BE-0352)."""
    return in_teams(teams, admin_teams)


def role_for(
    *, teams: Sequence[str], editor_teams: Sequence[str], admin_teams: tuple[str, ...]
) -> str:
    """The role for a login from its GitHub Team memberships (BE-0313): admin if a member of any of
    the server-wide *admin_teams*, editor if a member of any of the resolved org's *editor_teams*,
    else viewer (the base role every signed-in user gets). *teams* are `"<github-org>/<team-slug>"`
    direct memberships; empty *editor_teams* or empty *admin_teams* never matches. Both checks go
    through `in_teams`, so both are ASCII-case-insensitive: once `editorTeams` also admits a login at
    the sign-in gate, a case-mismatched entry that used to cost only the editor role would cost
    sign-in itself, and matching one way but not the other would admit a login and then hand it
    viewer. Recomputed on every login (BE-0015 7c-2)."""
    if in_admin_team(teams, admin_teams):
        return "admin"
    if in_teams(teams, editor_teams):
        return "editor"
    return "viewer"


# One branch per gated route class: the count tracks the schema's size, not tangled logic, and a
# split would leave no single place a new gated route class clearly belongs (BE-0386).
def required_role(method: str, path: str) -> str | None:
    """The minimum role a request needs, or None for reads (GET) and the open auth endpoints.
    Cancelling a job or answering its handoff are editor actions (they mutate a running job). The
    gated reads are ``GET /api/config/content``, ``GET /api/artifacts/exists``,
    ``GET /api/compose/current``, ``GET /api/version/checkout`` and ``GET /api/orgs`` (all admin), a
    wider disclosure than their paths."""
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
    # The active composed bind's per-leg shas (compose-picker resume seed). Same admin tier and
    # early-case reason as `/api/artifacts/exists`: a GET disclosing stored digests must not sit in
    # `_ADMIN_PATHS` alone (that set is only consulted past the POST-only guard).
    if method == "GET" and path == "/api/compose/current":
        return "admin"
    # The server's own Git checkout (BE-0272): commit / branch / dirty. The branch name routinely
    # encodes an in-progress BE slug (`claude/<topic>`), so on a shared deployment it leaks what's
    # being worked on — gate it like the other wider-disclosure reads. The version string alone
    # (`/api/version`) stays open. Same early-case reason as the two GETs above.
    if method == "GET" and path == "/api/version/checkout":
        return "admin"
    # Org lifecycle (BE-0375): creating, deleting, or re-membering an org decides who else can sign
    # in and write, so every verb is admin — the list included, since it discloses one tenant's
    # membership to another. Handled ahead of the `method != "POST"` guard below because the list is
    # a GET and the delete a DELETE, both of which that guard would otherwise let through ungated.
    if path == "/api/orgs" or path.startswith("/api/orgs/"):
        return "admin"
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

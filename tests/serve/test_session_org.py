"""Tests for session-scoped org selection: which org a session acts as, and switching between orgs.

A login that belongs to more than one org chooses which one the current session acts as. The choice,
the GitHub facts it is derived from, and the role the chosen org grants all live on the session, so
two windows can hold two tenants and a switch never moves anyone else. These tests drive the
operations directly — sign-in through the injected OAuth seam, the switch through
`ops.switch_org` — and pin what each answer is *for*: the candidate list is the caller's own, a
replayed slug reaches nothing, the role follows the org, and a membership edit ends exactly the
sessions whose answer it changed.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bajutsu.serve import gate
from bajutsu.serve import operations as ops
from bajutsu.serve.operations import session as session_ops
from bajutsu.serve.server.oauth import Identity
from bajutsu.serve.sessions import Caller, SessionIdentity
from bajutsu.serve.state import ServeState, SessionManager

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

# Two orgs one login can belong to: `acme` reaches bob through his GitHub organization and makes its
# maintainers editors; `globex` names him explicitly and grants nobody editor. An explicit `members`
# entry wins the sign-in resolution, so bob starts in `globex` and `acme` is what he switches to.
_ORGS_YAML = """
targets:
  demo: { bundleId: com.example.demo }

orgs:
  acme:
    githubOrgs: [acme-gh]
    editorTeam: acme-gh/scenario-maintainers
    targets: [demo]
  globex:
    members: [bob]
"""


class FakeOAuthClient:
    """The OAuth seam in memory: a fixed identity, no GitHub call."""

    def __init__(self, login: str, orgs: list[str], teams: list[str]) -> None:
        self._identity = Identity(login=login, orgs=list(orgs), teams=list(teams))

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        return None if code == "bad" else self._identity


def _state(
    serve_engine: Callable[..., Engine],
    tmp_path: Path,
    *,
    login: str = "bob",
    orgs: list[str] | None = None,
    teams: list[str] | None = None,
) -> ServeState:
    from bajutsu.serve.operations.config import seed_orgs_from_bound_config
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    config = tmp_path / "serve.config.yaml"
    config.write_text(_ORGS_YAML)
    state = ServeState(
        runs_dir=tmp_path / "runs",
        config=config,
        auth=SessionManager(oauth=FakeOAuthClient(login, orgs or ["acme-gh"], teams or [])),
    )
    state.repository = SqlRepository(engine)
    seed_orgs_from_bound_config(state)
    return state


def _sign_in(state: ServeState) -> str:
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    return sid


def test_sign_in_records_the_github_facts_and_the_starting_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The switch below derives its candidates and its role from what the sign-in saw, so the session
    # has to carry those facts out of the OAuth callback that observed them.
    state = _state(serve_engine, tmp_path, teams=["acme-gh/scenario-maintainers"])
    record = state.auth.sessions.context(_sign_in(state))
    assert record is not None
    assert record.login == "bob"
    assert record.github_orgs == ("acme-gh",)
    assert record.teams == ("acme-gh/scenario-maintainers",)
    assert record.org == "globex" and record.role == "viewer"


def test_the_config_read_offers_every_org_this_session_may_act_as(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # bob reaches `acme` through his GitHub organization and `globex` through an explicit members
    # entry. Both are his own orgs, so the boot read may name them; the roster of everyone else's
    # stays behind the admin-only `GET /api/orgs`.
    state = _state(serve_engine, tmp_path)
    sid = _sign_in(state)
    payload, status = ops.config_info(state, actor=gate.caller_for(state.auth, sid), session_id=sid)
    assert status == 200
    assert payload["org"] == "globex"
    assert payload["orgOptions"] == ["acme", "globex"]


def test_a_session_with_one_org_is_offered_no_choice(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # carol matches `acme` alone. One candidate is not a choice, and the header renders the plain
    # badge it always did.
    state = _state(serve_engine, tmp_path, login="carol")
    sid = _sign_in(state)
    payload, _status = ops.config_info(
        state, actor=gate.caller_for(state.auth, sid), session_id=sid
    )
    assert payload["orgOptions"] == ["acme"]


def test_switching_repoints_this_session_and_recomputes_the_role(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A role is granted per org, so moving into `acme` — whose editorTeam bob is in — must grant the
    # editor `globex` never gave him, rather than carry `globex`'s viewer across the boundary.
    state = _state(serve_engine, tmp_path, teams=["acme-gh/scenario-maintainers"])
    sid = _sign_in(state)
    payload, status = ops.switch_org(
        state, {"org": "acme"}, session_id=sid, caller=gate.caller_for(state.auth, sid)
    )
    assert status == 200
    assert payload["org"] == "acme" and payload["role"] == "editor"
    caller = gate.caller_for(state.auth, sid)
    assert caller is not None
    assert state.org_of(caller) == "acme"
    # The user row still names the org sign-in resolved: the selection is the session's, not the
    # user's, so a second window keeps acting as `globex`.
    assert state.repository is not None
    assert state.repository.user_org("bob") == "globex"


def test_the_role_gate_follows_the_session_not_the_user_row(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The transport gate rejects a request whose role is too low before any operation runs. After a
    # switch it has to ask the session, or bob would keep `globex`'s viewer inside `acme`.
    state = _state(serve_engine, tmp_path, teams=["acme-gh/scenario-maintainers"])
    sid = _sign_in(state)
    caller = gate.caller_for(state.auth, sid)
    assert caller is not None
    assert ops.forbidden_for_role(state, caller, "POST", "/api/run") is True  # viewer in globex
    ops.switch_org(state, {"org": "acme"}, session_id=sid, caller=caller)
    switched = gate.caller_for(state.auth, sid)
    assert switched is not None
    assert ops.forbidden_for_role(state, switched, "POST", "/api/run") is False  # editor in acme


def test_a_slug_the_session_never_qualified_for_is_refused(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The candidates are recomputed here rather than trusted from the client, so replaying a stale
    # list — or inventing a slug — reaches nothing, and the session keeps the org it had.
    state = _state(serve_engine, tmp_path, login="carol")
    sid = _sign_in(state)
    caller = gate.caller_for(state.auth, sid)
    payload, status = ops.switch_org(state, {"org": "globex"}, session_id=sid, caller=caller)
    assert status == 403 and "globex" in payload["error"]
    assert state.org_of(gate.caller_for(state.auth, sid)) == "acme"  # unchanged by the refusal


def test_a_switch_without_an_org_is_a_bad_request(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _state(serve_engine, tmp_path)
    sid = _sign_in(state)
    _payload, status = ops.switch_org(
        state, {}, session_id=sid, caller=gate.caller_for(state.auth, sid)
    )
    assert status == 400


def test_a_session_carrying_no_identity_may_not_select(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A shared-token session belongs to no org, so there is nothing for it to choose between — and
    # the boot read offers it nothing rather than every org in the deployment.
    state = _state(serve_engine, tmp_path)
    sid = state.auth.issue_session()
    _payload, status = ops.switch_org(state, {"org": "acme"}, session_id=sid, caller=None)
    assert status == 403
    assert ops.candidate_orgs(state, sid, None) == []


def test_a_membership_edit_ends_only_the_sessions_whose_answer_it_changed(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A session holds a role computed at sign-in, so an edit that takes an editorTeam away has to
    # reach it. An edit that grants — here, adding a member — changes nobody's answer, and signing
    # the org's existing members out for it would be the cost of a change they never felt.
    state = _state(serve_engine, tmp_path, teams=["acme-gh/scenario-maintainers"])
    sid = _sign_in(state)
    ops.switch_org(state, {"org": "acme"}, session_id=sid, caller=gate.caller_for(state.auth, sid))
    admin = Caller("root")
    assert state.repository is not None
    state.repository.upsert_user("root", org_id="acme", github_login="root", email="root@x")

    grant, status = ops.update_org_membership(
        state,
        "acme",
        {
            "members": ["zoe"],
            "githubOrgs": ["acme-gh"],
            "editorTeam": "acme-gh/scenario-maintainers",
        },
        actor=admin,
    )
    assert status == 200 and grant["sessionsRevoked"] == 0
    assert state.auth.sessions.valid(sid)

    demote, status = ops.update_org_membership(
        state, "acme", {"githubOrgs": ["acme-gh"]}, actor=admin
    )
    assert status == 200 and demote["sessionsRevoked"] == 1
    assert not state.auth.sessions.valid(sid)  # the editor role it held is no longer granted


def test_retiring_an_org_reaches_a_session_that_only_selected_it(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # `users.org_id` finds the members an org resolves at sign-in. bob's row names `globex`, so a
    # lookup through it would miss the session he switched into `acme` — and that session would keep
    # acting as a tenant this deployment has retired.
    state = _state(serve_engine, tmp_path)
    sid = _sign_in(state)
    ops.switch_org(state, {"org": "acme"}, session_id=sid, caller=gate.caller_for(state.auth, sid))
    assert state.repository is not None
    state.repository.upsert_user("root", org_id="globex", github_login="root", email="root@x")

    payload, status = ops.delete_org(state, "acme", actor=Caller("root"))
    assert status == 200 and payload["sessionsRevoked"] == 1
    assert not state.auth.sessions.valid(sid)


def test_a_session_that_recorded_no_selection_still_resolves_from_the_user_row(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A session issued before this behavior existed carries neither GitHub facts nor a selection. It
    # keeps resolving its org from the user row exactly as it did, and the only orgs it is offered
    # are the ones naming its login outright — an org reached through a GitHub organization needs
    # the facts this session never recorded, so `acme` is not among them until the next sign-in.
    state = _state(serve_engine, tmp_path)
    _sign_in(state)  # leaves the user row this fallback reads
    sid = state.auth.sessions.issue("bob", context=SessionIdentity(login="bob"))
    caller = gate.caller_for(state.auth, sid)
    assert caller == Caller(login="bob", org=None, role=None)
    assert state.org_of(caller) == "globex"
    assert ops.candidate_orgs(state, sid, caller) == ["globex"]


def test_a_deployment_without_a_database_offers_no_selection(tmp_path: Path) -> None:
    # Without a database every request already resolves to the single `default` org, so a select box
    # there would be a control that changes nothing — and a switch that answered 200 would claim one.
    config = tmp_path / "serve.config.yaml"
    config.write_text(_ORGS_YAML)
    state = ServeState(
        runs_dir=tmp_path / "runs",
        config=config,
        auth=SessionManager(oauth=FakeOAuthClient("bob", ["acme-gh"], [])),
    )
    sid = _sign_in(state)
    caller = gate.caller_for(state.auth, sid)
    assert ops.candidate_orgs(state, sid, caller) == []
    payload, status = ops.switch_org(state, {"org": "acme"}, session_id=sid, caller=caller)
    assert status == 400 and "database" in payload["error"]


def test_an_org_retired_mid_switch_ends_the_session_rather_than_admitting_it(
    serve_engine: Callable[..., Engine], tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # The one ordering an admin's revocation sweep cannot catch on its own: a switch validated
    # against the roster before the retirement, writing its selection after the sweep has passed.
    # The switch re-reads the roster after its own write, so the session ends here instead of
    # acting as a retired tenant until it expires.
    state = _state(serve_engine, tmp_path)
    sid = _sign_in(state)
    rosters = iter([session_ops.org_model(state), {}])
    monkeypatch.setattr(session_ops, "org_model", lambda _state: next(rosters))

    payload, status = ops.switch_org(
        state, {"org": "acme"}, session_id=sid, caller=gate.caller_for(state.auth, sid)
    )
    assert status == 409 and "sign in again" in payload["error"]
    assert not state.auth.sessions.valid(sid)

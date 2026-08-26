"""Tests for choosing the active org when a login belongs to several.

A login whose GitHub memberships match more than one org used to be pinned to the first match. These
tests cover the three things that changed: sign-in records every org the login may act as (with the
role it holds in each), a switch endpoint moves the caller between them, and a switch the caller made
themselves survives their next sign-in while a merely-resolved org keeps re-resolving."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bajutsu.serve import operations as ops
from bajutsu.serve.orgs import orgs_for_identity, parse_orgs
from bajutsu.serve.server.oauth import Identity, OAuthClient
from bajutsu.serve.state import ServeState, SessionManager

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

    from bajutsu.serve.server.db import Repository

# Two orgs that both claim `alice`: `acme` by an explicit member entry, `globex` through a GitHub org
# she also belongs to. `zenith` claims nobody she can reach, so it is the org a refused switch names.
_ORGS_YAML = """
targets:
  demo: { bundleId: com.example.demo }

orgs:
  acme:
    members: [alice]
    targets: [demo]
  globex:
    githubOrgs: [globex-gh]
    editorTeams: [globex-gh/writers]
  zenith:
    githubOrgs: [zenith-gh]
"""


class FakeOAuthClient:
    """The slice of the OAuth flow the operations use, in memory — no GitHub call."""

    def __init__(
        self,
        login: str = "alice",
        orgs: list[str] | None = None,
        teams: list[str] | None = None,
    ) -> None:
        self._login = login
        self._orgs = orgs or []
        self._teams = teams or []

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/login/oauth/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        return Identity(login=self._login, orgs=list(self._orgs), teams=list(self._teams))


def _db_state(
    serve_engine: Callable[..., Engine],
    tmp_path: Path,
    oauth: OAuthClient,
    admin_teams: list[str] | None = None,
) -> ServeState:
    from bajutsu.serve.operations.config import seed_orgs_from_bound_config
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    config = tmp_path / "serve.config.yaml"
    config.write_text(_ORGS_YAML)
    engine = serve_engine()
    Base.metadata.create_all(engine)
    state = ServeState(
        runs_dir=tmp_path / "runs",
        config=config,
        auth=SessionManager(oauth=oauth, oauth_admin_teams=tuple(admin_teams or ())),
    )
    state.repository = SqlRepository(engine)
    # A database-backed deployment resolves sign-in against the `orgs` table, not the `orgs:` block
    # (BE-0375), and `serve()` seeds that table at startup and at every rebind.
    seed_orgs_from_bound_config(state)
    return state


def _sign_in(state: ServeState) -> int:
    _payload, status, _sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    return status


def _audit(repository: Repository) -> list[tuple[str, str, dict[str, object]]]:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import AuditLog

    engine = repository._engine  # type: ignore[attr-defined]
    with Session(engine) as session:
        rows = session.scalars(select(AuditLog).order_by(AuditLog.id)).all()
        return [(row.action, row.org_id, dict(row.detail or {})) for row in rows]


def test_every_matching_org_is_returned_best_match_first() -> None:
    orgs = parse_orgs(
        {
            "byteam": {"githubTeams": ["acme-gh/qa"]},
            "byorg": {"githubOrgs": ["acme-gh"]},
            "bymember": {"members": ["alice"]},
        }
    )
    # Ranked by axis, not by declaration order: an explicit member entry outranks a GitHub org, which
    # outranks a Team — so adding a Team to an org never relocates a login already placed by name.
    assert orgs_for_identity(orgs, "alice", ["acme-gh"], ["acme-gh/qa"]) == [
        "bymember",
        "byorg",
        "byteam",
    ]


def test_an_org_matching_on_two_axes_appears_once() -> None:
    orgs = parse_orgs({"acme": {"members": ["alice"], "githubOrgs": ["acme-gh"]}})
    assert orgs_for_identity(orgs, "alice", ["acme-gh"], []) == ["acme"]


def test_a_login_no_org_admits_is_eligible_for_nothing() -> None:
    orgs = parse_orgs({"acme": {"members": ["alice"]}})
    assert orgs_for_identity(orgs, "stranger", ["other-gh"], []) == []


def test_sign_in_records_every_eligible_org_with_its_own_role(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The role is per-org: alice writes in `globex` (its `editorTeams` names a Team she is in) and
    # only reads in `acme`, so one stored role could not describe both.
    state = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(orgs=["globex-gh"], teams=["globex-gh/writers"]),
    )
    assert _sign_in(state) == 200
    assert state.eligible_orgs("alice") == {"acme": "viewer", "globex": "editor"}


def test_the_active_org_starts_at_the_best_match(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    assert state.org_of("alice") == "acme"  # the explicit `members` entry outranks `githubOrgs`


def test_switching_moves_the_actor_and_the_role_they_act_with(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(orgs=["globex-gh"], teams=["globex-gh/writers"]),
    )
    assert _sign_in(state) == 200
    assert state.repository is not None
    assert state.repository.user_role("alice") == "viewer"  # acting as `acme`

    payload, status = ops.set_active_org(state, {"org": "globex"}, actor="alice")
    assert status == 200
    assert payload == {"ok": True, "org": "globex", "role": "editor"}
    assert state.org_of("alice") == "globex"
    # The role travels with the tenant: `globex` promotes her, `acme` never did.
    assert state.repository.user_role("alice") == "editor"


def test_a_switch_is_audited_against_the_destination_and_names_the_origin(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    assert state.repository is not None
    _payload, status = ops.set_active_org(state, {"org": "globex"}, actor="alice")
    assert status == 200
    assert _audit(state.repository) == [("org.switch", "globex", {"from": "acme"})]


def test_a_switch_is_recorded_as_its_own_operational_event(
    serve_engine: Callable[..., Engine], tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # Not a second `oauth.login`: no authentication happened, and an operator reconstructing which
    # tenant an actor was acting as needs the moves as well as the sign-ins.
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    with caplog.at_level(logging.INFO):
        _payload, status = ops.set_active_org(state, {"org": "globex"}, actor="alice")
    assert status == 200
    record = next(r for r in caplog.records if getattr(r, "event", None) == "org.switch")
    assert record.levelno == logging.INFO


def test_switching_to_an_org_that_does_not_admit_the_caller_is_refused(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    assert state.repository is not None
    payload, status = ops.set_active_org(state, {"org": "zenith"}, actor="alice")
    assert status == 403
    assert state.org_of("alice") == "acme"  # unmoved
    # The same answer a nonexistent org gets, so the refusal discloses no tenant the caller may not
    # act as.
    assert ops.set_active_org(state, {"org": "no-such-org"}, actor="alice")[1] == 403
    assert "zenith" in str(payload["error"])
    assert _audit(state.repository) == []  # a refused switch records nothing


def test_switching_needs_an_org_a_signed_in_identity_and_a_database(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient())
    assert ops.set_active_org(state, {}, actor="alice")[1] == 400  # no org named
    # A local or shared-token session acts as `default` for everything and has no per-user row.
    assert ops.set_active_org(state, {"org": "acme"}, actor=None)[1] == 403
    state.repository = None
    assert ops.set_active_org(state, {"org": "acme"}, actor="alice")[1] == 400


def test_a_switch_survives_the_next_sign_in(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The point of letting a user pick: re-resolving on every sign-in would undo the choice at the
    # next login, which is what pinned them to the first match to begin with.
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    assert ops.set_active_org(state, {"org": "globex"}, actor="alice")[1] == 200
    assert _sign_in(state) == 200
    assert state.org_of("alice") == "globex"


def test_losing_the_picked_org_relocates_the_actor_on_the_next_sign_in(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A pick holds only while the org still admits the user: membership is the deployment's to
    # decide, so losing it must relocate them exactly as it relocates a user who never picked.
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    assert ops.set_active_org(state, {"org": "globex"}, actor="alice")[1] == 200

    state.auth.oauth = FakeOAuthClient(orgs=[])  # removed from `globex-gh`
    assert _sign_in(state) == 200
    assert state.org_of("alice") == "acme"
    assert state.eligible_orgs("alice") == {"acme": "viewer"}


def test_a_merely_resolved_org_keeps_re_resolving(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The complement: alice never picked anything, so losing the `members` entry that placed her in
    # `acme` moves her to the next best match rather than pinning her where she happened to land.
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    assert state.org_of("alice") == "acme"
    assert state.repository is not None
    state.repository.set_org_membership(
        "acme", members=[], github_orgs=[], github_teams=[], editor_teams=[]
    )
    assert _sign_in(state) == 200
    assert state.org_of("alice") == "globex"


def test_an_admin_team_member_may_act_as_every_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # An admin is admitted by their admin Team rather than by any org's membership (BE-0352), so a
    # stored eligible set would be empty — and the header selector would hide from the one user who
    # administers several tenants.
    state = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )
    assert _sign_in(state) == 200
    assert state.repository is not None
    assert state.repository.list_user_orgs("mallory") == {}  # no org's membership names them
    assert state.eligible_orgs("mallory") == {
        "acme": "admin",
        "default": "admin",
        "globex": "admin",
        "zenith": "admin",
    }
    assert ops.set_active_org(state, {"org": "zenith"}, actor="mallory")[1] == 200
    assert state.org_of("mallory") == "zenith"
    assert _sign_in(state) == 200
    assert state.org_of("mallory") == "zenith"  # the pick survives, as it does for anyone else


def test_an_admin_may_act_as_an_org_created_after_they_signed_in(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # Why the admin's set is computed at read time rather than stored: creating an org on the Orgs
    # page is the action most likely to come right before wanting to switch into the result.
    state = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )
    assert _sign_in(state) == 200
    assert ops.create_org(state, {"slug": "newco"}, actor="mallory")[1] == 200
    assert ops.set_active_org(state, {"org": "newco"}, actor="mallory")[1] == 200
    assert state.org_of("mallory") == "newco"


def test_an_admin_whose_picked_org_was_retired_relocates_on_the_next_sign_in(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # An admin's eligible set is every *live* org, so a pick that outlives its org has to lose its
    # hold — otherwise the one user whose set is not stored as rows would be the one user a retired
    # tenant keeps admitting.
    from datetime import UTC, datetime

    state = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )
    assert _sign_in(state) == 200
    assert ops.set_active_org(state, {"org": "zenith"}, actor="mallory")[1] == 200
    assert state.repository is not None
    assert state.repository.soft_delete_org("zenith", at=datetime.now(UTC))

    assert _sign_in(state) == 200
    assert state.org_of("mallory") == "default"  # no live org admits them by membership
    # The relocation also drops the marker, so the org they landed in is not mistaken for a pick.
    assert state.repository.user_selected_org("mallory") is None


def test_a_relocation_target_is_not_mistaken_for_a_pick(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The complement of a pick surviving: once a revoked membership has relocated the user, the org
    # they were moved *to* is a resolution like any other, so the next ranking change moves them
    # again. Were the marker left standing, the relocation would silently become a choice they never
    # made and pin them there.
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    assert ops.set_active_org(state, {"org": "globex"}, actor="alice")[1] == 200

    state.auth.oauth = FakeOAuthClient(orgs=[])  # removed from `globex-gh`
    assert _sign_in(state) == 200
    assert state.org_of("alice") == "acme"
    assert state.repository is not None
    assert state.repository.user_selected_org("alice") is None

    # `acme` now reaches her through a Team instead, which ranks below `zenith`'s `githubOrgs`.
    state.repository.set_org_membership(
        "acme", members=[], github_orgs=[], github_teams=["acme-gh/qa"], editor_teams=[]
    )
    state.auth.oauth = FakeOAuthClient(orgs=["zenith-gh"], teams=["acme-gh/qa"])
    assert _sign_in(state) == 200
    assert state.org_of("alice") == "zenith"


def test_a_retired_org_drops_out_of_the_eligible_set(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # Retiring an org stops it admitting sign-ins (BE-0375); it must stop being switchable too, or a
    # member's stored row would keep offering a tenant the deployment has withdrawn.
    from datetime import UTC, datetime

    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    assert state.repository is not None
    assert state.repository.soft_delete_org("globex", at=datetime.now(UTC))
    assert state.eligible_orgs("alice") == {"acme": "viewer"}
    assert ops.set_active_org(state, {"org": "globex"}, actor="alice")[1] == 403


def test_the_boot_read_offers_the_eligible_orgs(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient(orgs=["globex-gh"]))
    assert _sign_in(state) == 200
    payload, status = ops.config_info(state, actor="alice")
    assert status == 200
    assert payload["org"] == "acme"
    assert payload["orgs"] == ["acme", "globex"]


def test_the_boot_read_offers_nothing_to_a_session_with_no_identity(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A local or shared-token session acts as `default` for everything, so a selector listing orgs
    # would offer a choice it cannot make.
    state = _db_state(serve_engine, tmp_path, FakeOAuthClient())
    payload, _status = ops.config_info(state, actor=None)
    assert payload["org"] is None
    assert payload["orgs"] == []

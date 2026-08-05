"""Tests for the GitHub OAuth login operations (BE-0015 7b-2, BE-0313).

`oauth_login` / `oauth_callback` are provider-neutral: they drive the injected `OAuthClient` seam,
verify the CSRF state, and gate sign-in on GitHub org membership (BE-0313) before minting a session,
deriving the role from GitHub Team membership. A `FakeOAuthClient` stands in for GitHub so the gate
never makes a network call."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from bajutsu.serve import operations as ops
from bajutsu.serve.server.oauth import Identity
from bajutsu.serve.state import ServeState, SessionManager

if TYPE_CHECKING:
    from collections.abc import Callable

    from sqlalchemy import Engine

# An `orgs:` block that admits `alice` (explicit member) and anyone in the `acme-gh` GitHub org, and
# names the Team whose members become editors. The sign-in gate reads this from `state.config`.
_ORGS_YAML = """
targets:
  demo: { bundleId: com.example.demo }

orgs:
  acme:
    members: [alice]
    githubOrgs: [acme-gh]
    editorTeam: acme-gh/scenario-maintainers
    targets: [demo]
"""


class FakeOAuthClient:
    """The slice of the OAuth flow the operations use, in memory — no GitHub call. `fetch_identity`
    returns None for the code ``"bad"`` (a failed exchange), else the configured login + orgs +
    teams."""

    def __init__(
        self,
        login: str | None = "alice",
        orgs: list[str] | None = None,
        teams: list[str] | None = None,
    ) -> None:
        self._login = login
        self._orgs = orgs or []
        self._teams = teams or []

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/login/oauth/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        if code == "bad" or not self._login:
            return None
        return Identity(login=self._login, orgs=list(self._orgs), teams=list(self._teams))


def _config_file(tmp_path: Path, body: str = _ORGS_YAML) -> Path:
    path = tmp_path / "serve.config.yaml"
    path.write_text(body)
    return path


def _state(
    tmp_path: Path,
    *,
    oauth: object = None,
    config: Path | None = None,
    admin_teams: list[str] | None = None,
) -> ServeState:
    return ServeState(
        runs_dir=tmp_path / "runs",
        config=config,
        auth=SessionManager(oauth=oauth, oauth_admin_teams=tuple(admin_teams or ())),
    )


def test_oauth_login_not_configured(tmp_path: Path) -> None:
    _payload, status, csrf = ops.oauth_login(_state(tmp_path))
    assert status == 404
    assert csrf is None


def test_oauth_login_returns_redirect_carrying_the_state(tmp_path: Path) -> None:
    payload, status, csrf = ops.oauth_login(_state(tmp_path, oauth=FakeOAuthClient()))
    assert status == 200
    assert csrf and csrf in payload["redirect"]  # the CSRF state rides in the authorize URL


def test_oauth_callback_rejects_a_state_mismatch(tmp_path: Path) -> None:
    state = _state(tmp_path, oauth=FakeOAuthClient(), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="x", state_cookie="y")
    assert status == 403
    assert sid is None


def test_oauth_callback_allows_an_org_member_and_binds_identity(tmp_path: Path) -> None:
    # alice is an explicit `members` entry, so the org gate admits her (BE-0313).
    state = _state(tmp_path, oauth=FakeOAuthClient(login="alice"), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert sid is not None
    assert state.auth.valid_session(sid)
    assert state.auth.sessions.identity(sid) == "alice"  # the session is bound to the GitHub login


def test_oauth_callback_rejects_a_user_in_no_org(tmp_path: Path) -> None:
    # mallory is neither an explicit member nor in a `githubOrgs` org, so the gate turns them away.
    state = _state(tmp_path, oauth=FakeOAuthClient(login="mallory"), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 403
    assert sid is None


def test_oauth_callback_rejects_when_no_orgs_block_is_configured(tmp_path: Path) -> None:
    # BE-0313: with no `orgs:` block, the org roster is empty, so every login is rejected — an OAuth
    # deployment must declare one.
    body = "targets:\n  demo: { bundleId: com.example.demo }\n"
    state = _state(
        tmp_path, oauth=FakeOAuthClient(login="alice"), config=_config_file(tmp_path, body)
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 403
    assert sid is None


def test_oauth_callback_rejects_a_failed_exchange(tmp_path: Path) -> None:
    state = _state(tmp_path, oauth=FakeOAuthClient(), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="bad", state_param="s", state_cookie="s")
    assert status == 403
    assert sid is None


class _RaisingOAuthClient:
    """An OAuth client whose exchange raises (e.g. a network error or missing authlib)."""

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        raise RuntimeError("github unreachable")


def _db_state(
    serve_engine: Callable[..., Engine],
    tmp_path: Path,
    oauth: object,
    admin_teams: list[str] | None = None,
    config: Path | None = None,
) -> tuple[ServeState, Engine]:
    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    engine = serve_engine()
    Base.metadata.create_all(engine)
    state = _state(
        tmp_path, oauth=oauth, config=config or _config_file(tmp_path), admin_teams=admin_teams
    )
    state.repository = SqlRepository(engine)
    return state, engine


def _role_after_login(state: ServeState, login: str) -> str | None:
    _payload, status, _sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert state.repository is not None
    return state.repository.user_role(login)


def test_oauth_callback_persists_the_user_under_the_resolved_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # With a database wired, a successful login upserts the user into their resolved org so
    # audit/RBAC can reference them (BE-0015 7c-1).
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import Org, User

    state, engine = _db_state(serve_engine, tmp_path, FakeOAuthClient(login="alice"))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    with Session(engine) as s:
        users = list(s.scalars(select(User)))
        orgs = list(s.scalars(select(Org)))
    assert [u.github_login for u in users] == ["alice"]
    assert [o.slug for o in orgs] == ["acme"]


def test_oauth_callback_base_role_is_viewer(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # BE-0313: a signed-in user in no editor/admin Team gets the base viewer role.
    state, _ = _db_state(serve_engine, tmp_path, FakeOAuthClient(login="alice", teams=[]))
    assert _role_after_login(state, "alice") == "viewer"


def test_oauth_callback_editor_team_membership_promotes_to_editor(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="alice", teams=["acme-gh/scenario-maintainers"]),
    )
    assert _role_after_login(state, "alice") == "editor"


def test_oauth_callback_admin_team_membership_promotes_to_admin(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="alice", teams=["acme-gh/ops"]),
        admin_teams=["acme-gh/ops"],
    )
    assert _role_after_login(state, "alice") == "admin"


def test_oauth_callback_admin_team_bypasses_the_org_gate_with_no_matching_org(
    tmp_path: Path,
) -> None:
    # mallory belongs to no configured org, but is a member of the admin Team: the admin-Team
    # bypass admits her regardless, so she can sign in and repoint a broken `orgs:` config.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        config=_config_file(tmp_path),
        admin_teams=["ops-gh/root"],
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert sid is not None


def test_oauth_callback_admin_team_bypass_logs_a_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # The bypass is the one sign-in path `orgs:` did not authorize, so it's the one an operator
    # auditing sign-ins would otherwise have no record of at all.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        config=_config_file(tmp_path),
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.WARNING):
        ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert "admin-Team bypass admitted mallory" in caplog.text
    # Pin the structured fields too: a bare `_logger.warning(...)` carrying the same message text
    # would pass the assertion above but leave `event`/`actor`/`bypass` off the record, breaking
    # exactly the alert an operator would key on `event=oauth.login` for.
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.login")
    assert record.actor == "mallory"
    assert record.bypass is True


def test_oauth_callback_org_member_does_not_log_a_bypass_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # alice is an explicit org member *and* an admin-Team member: the ordinary org gate admits her,
    # so the bypass never fires -- oauth.login must still record the login, but with bypass=False,
    # not the "admin-Team bypass" message that only a bypassing admission gets.
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="alice", teams=["ops-gh/root"]),
        config=_config_file(tmp_path),
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.INFO):
        ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert "admin-Team bypass" not in caplog.text
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.login")
    assert record.bypass is False
    assert record.actor == "alice"


def test_oauth_callback_admin_team_bypasses_the_org_gate_with_no_orgs_block(
    tmp_path: Path,
) -> None:
    # No `orgs:` block at all — every login would normally be rejected (BE-0313) — but the admin
    # Team bypass still admits an admin so they can fix the config.
    body = "targets:\n  demo: { bundleId: com.example.demo }\n"
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        config=_config_file(tmp_path, body),
        admin_teams=["ops-gh/root"],
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert sid is not None


def test_oauth_callback_admin_team_bypass_resolves_to_admin_role(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # An `orgs:` block IS declared (`_ORGS_YAML`'s `acme`) and mallory matches none of it — the
    # case BE-0313 called unreachable through OAuth sign-in.
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )
    assert _role_after_login(state, "mallory") == "admin"


def test_oauth_callback_admin_team_bypass_resolves_to_admin_role_with_no_orgs_block(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The other case the Progress checklist claims: no `orgs:` block at all, with a database wired
    # (so `role_for` actually runs) — the resolved role must still be admin.
    body = "targets:\n  demo: { bundleId: com.example.demo }\n"
    state, _ = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
        config=_config_file(tmp_path, body),
    )
    assert _role_after_login(state, "mallory") == "admin"


def test_oauth_callback_admin_team_bypass_places_the_user_in_the_default_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import Org

    # An `orgs:` block IS declared (`_ORGS_YAML`'s `acme`) and mallory matches none of it — the
    # case BE-0313 called unreachable through OAuth sign-in, so `default` is created by the bypass.
    state, engine = _db_state(
        serve_engine,
        tmp_path,
        FakeOAuthClient(login="mallory", teams=["ops-gh/root"]),
        admin_teams=["ops-gh/root"],
    )
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    with Session(engine) as s:
        orgs = list(s.scalars(select(Org)))
    assert [o.slug for o in orgs] == ["default"]


def test_oauth_callback_admin_team_bypass_relocates_on_a_transient_orgs_fetch_failure(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # bob reaches `acme` only through `githubOrgs` (not an explicit `members` entry). A later
    # /user/orgs failure makes him look unmatched even though `acme` still claims him -- `authz.py`
    # can't tell that apart from a login that genuinely belongs to no GitHub org at all (a
    # `members:`-listed bot/ops account, say), and guarding on it would pin such a login to its old
    # org forever once revoked, since it could never again report a non-empty orgs list. This is the
    # accepted, self-healing cost of that ambiguity: bob is relocated to `default` for this one
    # login, same as a genuinely un-claimed login, and moves back to `acme` on his next clean login
    # (see test_oauth_callback_admin_team_bypass_keeps_org_when_config_fails_to_load for the
    # unambiguous config-load-failure case, which does not have this cost).
    state, _ = _db_state(
        serve_engine, tmp_path, FakeOAuthClient(login="bob", orgs=["acme-gh"], teams=[])
    )
    assert _role_after_login(state, "bob") == "viewer"
    assert state.repository is not None
    assert state.repository.user_org("bob") == "acme"

    state.auth.oauth = FakeOAuthClient(login="bob", orgs=[], teams=["ops-gh/root"])
    state.auth.oauth_admin_teams = ("ops-gh/root",)
    assert _role_after_login(state, "bob") == "admin"
    assert state.repository.user_org("bob") == "default"

    # His next clean login (orgs answered again) moves him back.
    state.auth.oauth = FakeOAuthClient(login="bob", orgs=["acme-gh"], teams=["ops-gh/root"])
    assert _role_after_login(state, "bob") == "admin"
    assert state.repository.user_org("bob") == "acme"


def test_oauth_callback_admin_team_bypass_reresolves_a_revoked_members_org(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # The complement of the test above: GitHub *did* answer /user/orgs, so a login matching no
    # `orgs:` entry is genuinely un-claimed -- an operator who removed them must see that take
    # effect on the next sign-in (BE-0015 7c-2), not stay pinned to the org they used to hold.
    state, _ = _db_state(
        serve_engine, tmp_path, FakeOAuthClient(login="bob", orgs=["acme-gh"], teams=[])
    )
    assert _role_after_login(state, "bob") == "viewer"
    assert state.repository is not None
    assert state.repository.user_org("bob") == "acme"

    # bob has since been removed from `acme-gh`, but still reports some other org: not a fetch
    # failure, so the bypass admits him and he re-resolves to `default`.
    state.auth.oauth = FakeOAuthClient(login="bob", orgs=["ops-gh"], teams=["ops-gh/root"])
    state.auth.oauth_admin_teams = ("ops-gh/root",)
    assert _role_after_login(state, "bob") == "admin"
    assert state.repository.user_org("bob") == "default"


def test_oauth_callback_admin_team_bypass_keeps_org_when_config_fails_to_load(
    serve_engine: Callable[..., Engine], tmp_path: Path
) -> None:
    # A GitHub API hiccup isn't the only way the bypass can admit a real org member unmatched: the
    # config itself can fail to load (`load_serve_config_file` fails closed to None on a transient
    # filesystem error or a config typo -- exactly the state this item's own motivating scenario, a
    # broken `orgs:` block, produces). carol still reports her real GitHub orgs; it's the org model
    # that's unreadable, not her membership -- she must not be relocated to `default` over that.
    state, _ = _db_state(
        serve_engine, tmp_path, FakeOAuthClient(login="carol", orgs=["acme-gh"], teams=[])
    )
    assert _role_after_login(state, "carol") == "viewer"
    assert state.repository is not None
    assert state.repository.user_org("carol") == "acme"

    state.auth.oauth = FakeOAuthClient(login="carol", orgs=["acme-gh"], teams=["ops-gh/root"])
    state.auth.oauth_admin_teams = ("ops-gh/root",)
    state.config = tmp_path / "missing.yaml"  # never written -- load_serve_config_file -> None
    assert _role_after_login(state, "carol") == "admin"
    assert state.repository.user_org("carol") == "acme"


def test_oauth_callback_rejects_a_login_in_neither_the_org_gate_nor_the_admin_teams(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    state = _state(
        tmp_path,
        oauth=FakeOAuthClient(login="mallory", teams=["some-other/team"]),
        config=_config_file(tmp_path),
        admin_teams=["ops-gh/root"],
    )
    with caplog.at_level(logging.WARNING):
        _payload, status, sid = ops.oauth_callback(
            state, code="ok", state_param="s", state_cookie="s"
        )
    assert status == 403
    assert sid is None
    # The rejection is the one failure this item exists to make recoverable, so it must leave a
    # record too -- under its own event, not `oauth.login`, which stays "login count" (BE-0015 7c-1
    # audit-style visibility, not just a raw 403 with nothing an operator can correlate on).
    record = next(r for r in caplog.records if getattr(r, "event", None) == "oauth.denied")
    assert record.actor == "mallory"


def test_oauth_callback_without_a_database_is_a_no_op(tmp_path: Path) -> None:
    # No repository (the default): login still works, nothing is persisted.
    state = _state(tmp_path, oauth=FakeOAuthClient(login="alice"), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    assert state.repository is None


def test_oauth_callback_surfaces_an_exchange_error_as_502(tmp_path: Path) -> None:
    # A raising exchange (network / token parsing / missing dep) is an upstream error, not a 500.
    state = _state(tmp_path, oauth=_RaisingOAuthClient(), config=_config_file(tmp_path))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 502
    assert sid is None


class _FakeResponse:
    def __init__(self, status: int, body: object, next_url: str | None = None) -> None:
        self.status_code = status
        self._body = body
        self.links = {"next": {"url": next_url}} if next_url else {}

    def json(self) -> object:
        if isinstance(self._body, Exception):
            raise self._body
        return self._body


class _PagingClient:
    """A stand-in httpx client whose collection is paginated across pages."""

    def __init__(self, pages: list[_FakeResponse]) -> None:
        self._pages = pages
        self.calls = 0

    def get(self, url: str, headers: dict[str, str]) -> _FakeResponse:
        resp = self._pages[self.calls]
        self.calls += 1
        return resp


def test_fetch_orgs_follows_pagination() -> None:
    from bajutsu.serve.server.oauth import _fetch_orgs

    client = _PagingClient(
        [
            _FakeResponse(200, [{"login": "acme-gh"}], next_url="https://api.github.test/p2"),
            _FakeResponse(200, [{"login": "globex-gh"}]),
        ]
    )
    assert _fetch_orgs(client, {}) == ["acme-gh", "globex-gh"]
    assert client.calls == 2  # both pages fetched


def test_fetch_orgs_is_non_fatal_on_error() -> None:
    from bajutsu.serve.server.oauth import _fetch_orgs

    assert _fetch_orgs(_PagingClient([_FakeResponse(403, [])]), {}) == []
    assert _fetch_orgs(_PagingClient([_FakeResponse(200, ValueError("bad json"))]), {}) == []
    # A 200 whose body isn't a list (e.g. a GitHub error payload shaped as an object) is non-fatal too.
    assert (
        _fetch_orgs(_PagingClient([_FakeResponse(200, {"message": "bad credentials"})]), {}) == []
    )


def _team(org: str, slug: str) -> dict[str, object]:
    return {"slug": slug, "organization": {"login": org}}


def test_fetch_teams_parses_org_slug_and_follows_pagination() -> None:
    from bajutsu.serve.server.oauth import _fetch_teams

    client = _PagingClient(
        [
            _FakeResponse(
                200,
                [_team("acme-gh", "scenario-maintainers")],
                next_url="https://api.github.test/p2",
            ),
            _FakeResponse(200, [_team("acme-gh", "ops")]),
        ]
    )
    # A Team on a later page still resolves (BE-0313), each as "<org>/<slug>".
    assert _fetch_teams(client, {}) == ["acme-gh/scenario-maintainers", "acme-gh/ops"]
    assert client.calls == 2


def test_fetch_teams_fails_closed_to_empty_on_error() -> None:
    # BE-0313: the opposite failure direction from _fetch_orgs — an empty list leaves the user at
    # viewer, so a failed lookup never grants write access.
    from bajutsu.serve.server.oauth import _fetch_teams

    assert _fetch_teams(_PagingClient([_FakeResponse(403, [])]), {}) == []
    assert _fetch_teams(_PagingClient([_FakeResponse(200, ValueError("bad json"))]), {}) == []
    # A malformed item (missing organization/slug) is skipped, not fatal.
    assert _fetch_teams(_PagingClient([_FakeResponse(200, [{"slug": "x"}])]), {}) == []
    # A 200 whose body isn't a list never grants a role — the fail-closed direction matters most here.
    assert (
        _fetch_teams(_PagingClient([_FakeResponse(200, {"message": "bad credentials"})]), {}) == []
    )

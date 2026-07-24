"""Tests for the GitHub OAuth login operations (BE-0015 7b-2, BE-0313).

`oauth_login` / `oauth_callback` are provider-neutral: they drive the injected `OAuthClient` seam,
verify the CSRF state, and gate sign-in on GitHub org membership (BE-0313) before minting a session,
deriving the role from GitHub Team membership. A `FakeOAuthClient` stands in for GitHub so the gate
never makes a network call."""

from __future__ import annotations

from pathlib import Path

from bajutsu.serve import operations as ops
from bajutsu.serve.server.oauth import Identity
from bajutsu.serve.state import ServeState, SessionManager

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
    admin_team: str | None = None,
) -> ServeState:
    return ServeState(
        runs_dir=tmp_path / "runs",
        config=config,
        auth=SessionManager(oauth=oauth, oauth_admin_team=admin_team),
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
    tmp_path: Path, oauth: object, admin_team: str | None = None
) -> tuple[ServeState, object]:
    from sqlalchemy import create_engine

    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    state = _state(tmp_path, oauth=oauth, config=_config_file(tmp_path), admin_team=admin_team)
    state.repository = SqlRepository(engine)
    return state, engine


def _role_after_login(state: ServeState, login: str) -> str | None:
    _payload, status, _sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert state.repository is not None
    return state.repository.user_role(login)


def test_oauth_callback_persists_the_user_under_the_resolved_org(tmp_path: Path) -> None:
    # With a database wired, a successful login upserts the user into their resolved org so
    # audit/RBAC can reference them (BE-0015 7c-1).
    from sqlalchemy import select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.models import Org, User

    state, engine = _db_state(tmp_path, FakeOAuthClient(login="alice"))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    with Session(engine) as s:
        users = list(s.scalars(select(User)))
        orgs = list(s.scalars(select(Org)))
    assert [u.github_login for u in users] == ["alice"]
    assert [o.slug for o in orgs] == ["acme"]


def test_oauth_callback_base_role_is_viewer(tmp_path: Path) -> None:
    # BE-0313: a signed-in user in no editor/admin Team gets the base viewer role.
    state, _ = _db_state(tmp_path, FakeOAuthClient(login="alice", teams=[]))
    assert _role_after_login(state, "alice") == "viewer"


def test_oauth_callback_editor_team_membership_promotes_to_editor(tmp_path: Path) -> None:
    state, _ = _db_state(
        tmp_path, FakeOAuthClient(login="alice", teams=["acme-gh/scenario-maintainers"])
    )
    assert _role_after_login(state, "alice") == "editor"


def test_oauth_callback_admin_team_membership_promotes_to_admin(tmp_path: Path) -> None:
    state, _ = _db_state(
        tmp_path,
        FakeOAuthClient(login="alice", teams=["acme-gh/ops"]),
        admin_team="acme-gh/ops",
    )
    assert _role_after_login(state, "alice") == "admin"


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

"""Tests for the GitHub OAuth login operations (BE-0015 7b-2).

`oauth_login` / `oauth_callback` are provider-neutral: they drive the injected `OAuthClient` seam,
verify the CSRF state, and enforce the username allowlist before minting a session. A `FakeOAuthClient`
stands in for GitHub so the gate never makes a network call."""

from __future__ import annotations

from pathlib import Path

from bajutsu.serve import operations as ops
from bajutsu.serve.server.oauth import Identity
from bajutsu.serve.state import ServeState, SessionManager


class FakeOAuthClient:
    """The slice of the OAuth flow the operations use, in memory — no GitHub call. `fetch_identity`
    returns None for the code ``"bad"`` (a failed exchange), else the configured login + orgs."""

    def __init__(self, login: str | None = "alice", orgs: list[str] | None = None) -> None:
        self._login = login
        self._orgs = orgs or []

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/login/oauth/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        if code == "bad" or not self._login:
            return None
        return Identity(login=self._login, orgs=list(self._orgs))


def _state(
    tmp_path: Path, *, oauth: object = None, allowed: frozenset[str] = frozenset()
) -> ServeState:
    return ServeState(
        runs_dir=tmp_path / "runs",
        auth=SessionManager(oauth=oauth, oauth_allowed_users=allowed),
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
    state = _state(tmp_path, oauth=FakeOAuthClient(), allowed=frozenset({"alice"}))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="x", state_cookie="y")
    assert status == 403
    assert sid is None


def test_oauth_callback_allows_an_allowlisted_user_and_binds_identity(tmp_path: Path) -> None:
    state = _state(tmp_path, oauth=FakeOAuthClient(login="alice"), allowed=frozenset({"alice"}))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200
    assert sid is not None
    assert state.auth.valid_session(sid)
    assert state.auth.sessions.identity(sid) == "alice"  # the session is bound to the GitHub login


def test_oauth_callback_rejects_a_user_not_on_the_allowlist(tmp_path: Path) -> None:
    state = _state(tmp_path, oauth=FakeOAuthClient(login="mallory"), allowed=frozenset({"alice"}))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 403
    assert sid is None


def test_oauth_callback_rejects_a_failed_exchange(tmp_path: Path) -> None:
    state = _state(tmp_path, oauth=FakeOAuthClient(), allowed=frozenset({"alice"}))
    _payload, status, sid = ops.oauth_callback(state, code="bad", state_param="s", state_cookie="s")
    assert status == 403
    assert sid is None


class _RaisingOAuthClient:
    """An OAuth client whose exchange raises (e.g. a network error or missing authlib)."""

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/authorize?state={state}"

    def fetch_identity(self, code: str) -> Identity | None:
        raise RuntimeError("github unreachable")


def test_oauth_callback_persists_the_user_and_default_org(tmp_path: Path) -> None:
    # With a database wired, a successful login upserts the user into the single default org so
    # audit/RBAC can reference them (BE-0015 7c-1).
    from sqlalchemy import create_engine, select
    from sqlalchemy.orm import Session

    from bajutsu.serve.server.db import SqlRepository
    from bajutsu.serve.server.models import Base, Org, User

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    state = _state(tmp_path, oauth=FakeOAuthClient(login="alice"), allowed=frozenset({"alice"}))
    state.repository = SqlRepository(engine)

    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    with Session(engine) as s:
        users = list(s.scalars(select(User)))
        orgs = list(s.scalars(select(Org)))
    assert [u.github_login for u in users] == ["alice"]
    assert [o.slug for o in orgs] == ["default"]


def test_oauth_callback_without_a_database_is_a_no_op(tmp_path: Path) -> None:
    # No repository (the default): login still works, nothing is persisted.
    state = _state(tmp_path, oauth=FakeOAuthClient(login="alice"), allowed=frozenset({"alice"}))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 200 and sid is not None
    assert state.repository is None


def test_oauth_callback_surfaces_an_exchange_error_as_502(tmp_path: Path) -> None:
    # A raising exchange (network / token parsing / missing dep) is an upstream error, not a 500.
    state = _state(tmp_path, oauth=_RaisingOAuthClient(), allowed=frozenset({"alice"}))
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
    """A stand-in httpx client whose `/user/orgs` is paginated across two pages."""

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

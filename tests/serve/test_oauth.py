"""Tests for the GitHub OAuth login operations (BE-0015 7b-2).

`oauth_login` / `oauth_callback` are provider-neutral: they drive the injected `OAuthClient` seam,
verify the CSRF state, and enforce the username allowlist before minting a session. A `FakeOAuthClient`
stands in for GitHub so the gate never makes a network call."""

from __future__ import annotations

from pathlib import Path

from bajutsu.serve import operations as ops
from bajutsu.serve.jobs import ServeState


class FakeOAuthClient:
    """The slice of the OAuth flow the operations use, in memory — no GitHub call. `fetch_login`
    returns None for the code ``"bad"`` (a failed exchange), else the configured login."""

    def __init__(self, login: str | None = "alice") -> None:
        self._login = login

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/login/oauth/authorize?state={state}"

    def fetch_login(self, code: str) -> str | None:
        return None if code == "bad" else self._login


def _state(
    tmp_path: Path, *, oauth: object = None, allowed: frozenset[str] = frozenset()
) -> ServeState:
    return ServeState(runs_dir=tmp_path / "runs", oauth=oauth, oauth_allowed_users=allowed)


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
    assert state.valid_session(sid)
    assert state.sessions.identity(sid) == "alice"  # the session is bound to the GitHub login


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

    def fetch_login(self, code: str) -> str | None:
        raise RuntimeError("github unreachable")


def test_oauth_callback_surfaces_an_exchange_error_as_502(tmp_path: Path) -> None:
    # A raising exchange (network / token parsing / missing dep) is an upstream error, not a 500.
    state = _state(tmp_path, oauth=_RaisingOAuthClient(), allowed=frozenset({"alice"}))
    _payload, status, sid = ops.oauth_callback(state, code="ok", state_param="s", state_cookie="s")
    assert status == 502
    assert sid is None

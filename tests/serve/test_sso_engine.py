"""Unit tests for NativeSsoEngine's orchestration (BE-0056), with the AWS boundary faked.

The boto3/botocore calls are confined to ``_oidc`` / ``_sso_config`` / ``_token_loader``; these tests
monkeypatch those seams (or feed botocore a fake config) so the device-flow logic — handle
bookkeeping, token-dict assembly, and the pending / complete / error mapping — is exercised without
touching AWS. This is the engine's own logic, distinct from the HTTP wiring in test_http_sso.py.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from bajutsu.serve.sso import NativeSsoEngine, SsoError, _expiry_iso, _is_future


class _FakeOidc:
    """A stand-in for the boto3 ``sso-oidc`` client covering the three create_token outcomes."""

    def __init__(self, *, pending: bool = False, bad: bool = False, refresh: bool = True) -> None:
        self.pending = pending
        self.bad = bad
        self.refresh = refresh

    def register_client(self, **_kw: Any) -> dict[str, Any]:
        return {"clientId": "cid", "clientSecret": "secret", "clientSecretExpiresAt": 1893456000}

    def start_device_authorization(self, **_kw: Any) -> dict[str, Any]:
        return {
            "deviceCode": "dev-code",
            "userCode": "WXYZ-7890",
            "verificationUri": "https://device.sso/",
            "verificationUriComplete": "https://device.sso/?user_code=WXYZ-7890",
            "expiresIn": 600,
            "interval": 5,
        }

    def create_token(self, **_kw: Any) -> dict[str, Any]:
        from botocore.exceptions import ClientError

        if self.pending:
            raise ClientError({"Error": {"Code": "AuthorizationPendingException"}}, "CreateToken")
        if self.bad:
            raise ClientError({"Error": {"Code": "AccessDeniedException"}}, "CreateToken")
        token = {"accessToken": "AT", "expiresIn": 3600}
        if self.refresh:
            token["refreshToken"] = "RT"
        return token


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        region="us-east-1", start_url="https://my.awsapps.com/start", session_name="my"
    )


def _engine(monkeypatch: pytest.MonkeyPatch, oidc: _FakeOidc) -> NativeSsoEngine:
    eng = NativeSsoEngine()
    monkeypatch.setattr(eng, "_sso_config", lambda _profile: _cfg())
    monkeypatch.setattr(eng, "_oidc", lambda _region: oidc)
    return eng


def test_expiry_helpers() -> None:
    future = datetime.now(UTC) + timedelta(hours=1)
    past = datetime.now(UTC) - timedelta(hours=1)
    assert _is_future(future) and not _is_future(past)
    assert _is_future(future.isoformat()) and not _is_future("not-a-date")
    assert _expiry_iso(None) is None
    assert _expiry_iso(future) == future.isoformat()


def test_start_then_complete_saves_token(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = _engine(monkeypatch, _FakeOidc())
    saved: dict[str, Any] = {}
    loader = SimpleNamespace(
        save_token=lambda url, tok, session_name: saved.update(url=url, tok=tok, sn=session_name)
    )
    monkeypatch.setattr(eng, "_token_loader", lambda: loader)

    auth = eng.start("dev")
    assert auth.user_code == "WXYZ-7890"
    assert auth.verification_uri.endswith("user_code=WXYZ-7890")  # the complete form is preferred
    assert auth.handle

    progress = eng.poll(auth.handle)
    assert progress.status == "complete"
    assert progress.session is not None and progress.session.profile == "dev"
    assert progress.session.signed_in
    # Persisted through botocore's loader with the session name + standard fields.
    assert saved["sn"] == "my"
    assert saved["tok"]["accessToken"] == "AT" and saved["tok"]["refreshToken"] == "RT"
    assert saved["tok"]["clientId"] == "cid" and saved["tok"]["startUrl"].endswith("/start")
    # The handle is single-use: a second poll no longer knows it.
    with pytest.raises(SsoError):
        eng.poll(auth.handle)


def test_poll_pending_retains_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = _engine(monkeypatch, _FakeOidc(pending=True))
    auth = eng.start("dev")
    assert eng.poll(auth.handle).status == "pending"
    assert eng.poll(auth.handle).status == "pending"  # still polling, handle kept


def test_poll_error_drops_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = _engine(monkeypatch, _FakeOidc(bad=True))
    auth = eng.start("dev")
    with pytest.raises(SsoError):
        eng.poll(auth.handle)
    with pytest.raises(SsoError):  # handle dropped after the failure
        eng.poll(auth.handle)


def test_poll_unknown_handle(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(SsoError):
        _engine(monkeypatch, _FakeOidc()).poll("nope")


def test_start_propagates_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = NativeSsoEngine()

    def boom(_profile: str) -> Any:
        raise SsoError("bad profile")

    monkeypatch.setattr(eng, "_sso_config", boom)
    with pytest.raises(SsoError):
        eng.start("dev")


def test_status_unset_and_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = NativeSsoEngine()
    assert eng.status(None).signed_in is False  # no profile → not signed in, no AWS touched
    monkeypatch.setattr(eng, "_sso_config", lambda _profile: _cfg())
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        eng, "_token_loader", lambda: lambda _url, session_name: {"expiresAt": future}
    )
    session = eng.status("dev")
    assert session.signed_in and session.profile == "dev" and session.expires_at == future


def test_status_unreadable_cache_is_not_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = NativeSsoEngine()
    monkeypatch.setattr(eng, "_sso_config", lambda _profile: _cfg())

    def no_cache() -> Any:
        def load(_url: str, session_name: str) -> dict[str, Any]:
            raise RuntimeError("no token cached")

        return load

    monkeypatch.setattr(eng, "_token_loader", no_cache)
    assert eng.status("dev").signed_in is False


def test_logout_is_noop() -> None:
    assert NativeSsoEngine().logout("dev") is None


def test_sso_config_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    import botocore.session

    full = {
        "profiles": {"dev": {"sso_session": "my"}, "nosso": {}},
        "sso_sessions": {"my": {"sso_start_url": "https://x/start", "sso_region": "us-east-1"}},
    }
    monkeypatch.setattr(botocore.session, "Session", lambda: SimpleNamespace(full_config=full))
    eng = NativeSsoEngine()
    cfg = eng._sso_config("dev")
    assert cfg.start_url == "https://x/start" and cfg.region == "us-east-1"
    assert cfg.session_name == "my"
    with pytest.raises(SsoError):
        eng._sso_config("ghost")  # unknown profile
    with pytest.raises(SsoError):
        eng._sso_config("nosso")  # profile without an sso_session


def test_oidc_and_token_loader_build() -> None:
    # Both construct real boto3/botocore objects offline (no AWS call), so they're checkable here.
    assert hasattr(NativeSsoEngine()._oidc("us-east-1"), "create_token")
    assert hasattr(NativeSsoEngine()._token_loader(), "save_token")

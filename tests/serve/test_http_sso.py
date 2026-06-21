"""Tests for the bajutsu serve AWS SSO endpoints (/api/sso*), real ThreadingHTTPServer (BE-0056).

A scriptable fake ``SsoEngine`` injected into ``ServeState`` drives the device-authorization flow
deterministically, so the full start -> poll(pending) -> poll(complete) path, status, and sign-out
are exercised without touching AWS. On completion the serve process's ``AWS_PROFILE`` is set (in
memory, never to disk) so a spawned Bedrock job inherits it via os.environ; sign-out clears it.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest
from _shared import _get_json, _post, _serve, project

from bajutsu import serve as srv
from bajutsu.serve.sso import DeviceAuthorization, LoginProgress, SsoError, SsoSession

_EXPIRES = "2099-01-01T00:00:00+00:00"


class _FakeSso:
    """A scriptable ``SsoEngine``: ``start`` hands out a fixed device authorization; ``poll`` is
    pending until ``complete_after`` polls, then complete; ``status`` reflects a completed sign-in."""

    def __init__(self) -> None:
        self.profile: str | None = None
        self.polls = 0
        self.complete_after = 1
        self.signed_in = False
        self.fail_start = False
        self.logged_out: list[str] = []

    def start(self, profile: str) -> DeviceAuthorization:
        if self.fail_start:
            raise SsoError(f"unknown AWS profile: {profile}")
        self.profile = profile
        self.polls = 0
        return DeviceAuthorization(
            verification_uri="https://device.sso.example/?user_code=ABCD-1234",
            user_code="ABCD-1234",
            expires_in=600,
            interval=1,
            handle="h-1",
        )

    def poll(self, handle: str) -> LoginProgress:
        if handle != "h-1":
            raise SsoError("unknown or expired sign-in; start again")
        self.polls += 1
        if self.polls <= self.complete_after:
            return LoginProgress("pending")
        self.signed_in = True
        return LoginProgress("complete", SsoSession(self.profile, True, _EXPIRES))

    def status(self, profile: str | None) -> SsoSession:
        if profile and self.signed_in:
            return SsoSession(profile, True, _EXPIRES)
        return SsoSession(profile, False)

    def logout(self, profile: str) -> None:
        self.logged_out.append(profile)
        self.signed_in = False


def _get_result(port: int, path: str) -> tuple[int, Any]:
    """GET returning (status, parsed-json) for both success and HTTP-error (4xx) responses."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


def _state(tmp_path: Path, engine: _FakeSso) -> srv.ServeState:
    scn_dir, cfg, runs = project(tmp_path)
    return srv.ServeState(
        scenarios_dir=scn_dir, config=cfg, runs_dir=runs, cwd=tmp_path, sso_engine=engine
    )


def test_http_sso_device_flow_sets_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Round-trip a sign-in: not-signed-in -> start -> pending -> complete (AWS_PROFILE lands in the
    process env, not on disk) -> status shows signed in -> sign out clears it."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)  # clean start + auto-restore at teardown
    engine = _FakeSso()
    server, port = _serve(_state(tmp_path, engine))
    try:
        assert _get_json(port, "/api/sso") == {
            "signedIn": False,
            "profile": None,
            "expiresAt": None,
        }

        code, body = _post(port, "/api/sso/login", {"profile": "dev"})
        assert code == 200
        assert body["userCode"] == "ABCD-1234" and body["handle"] == "h-1"
        assert body["verificationUri"].startswith("https://")

        # First poll is pending; the second completes and binds AWS_PROFILE for spawned jobs.
        assert _get_json(port, "/api/sso/login/h-1") == {"status": "pending"}
        code, body = _get_result(port, "/api/sso/login/h-1")
        assert code == 200 and body["status"] == "complete" and body["profile"] == "dev"
        assert os.environ["AWS_PROFILE"] == "dev"
        assert not (tmp_path / ".env").exists()  # nothing persisted to disk

        assert _get_json(port, "/api/sso") == {
            "signedIn": True,
            "profile": "dev",
            "expiresAt": _EXPIRES,
        }

        code, body = _post(port, "/api/sso/logout", {})
        assert code == 200 and body == {"ok": True, "signedIn": False}
        assert "AWS_PROFILE" not in os.environ
        assert engine.logged_out == ["dev"]
    finally:
        server.shutdown()
        server.server_close()


def test_http_sso_login_requires_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A missing or whitespace profile is rejected, and no AWS_PROFILE is set."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    server, port = _serve(_state(tmp_path, _FakeSso()))
    try:
        code, body = _post(port, "/api/sso/login", {})
        assert code == 400 and "profile" in body["error"]
        code, body = _post(port, "/api/sso/login", {"profile": "has space"})
        assert code == 400 and "whitespace" in body["error"]
        assert "AWS_PROFILE" not in os.environ
    finally:
        server.shutdown()
        server.server_close()


def test_http_sso_engine_errors_surface(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """An engine failure on start, and an unknown poll handle, both surface as 400 (not 500)."""
    monkeypatch.delenv("AWS_PROFILE", raising=False)
    engine = _FakeSso()
    engine.fail_start = True
    server, port = _serve(_state(tmp_path, engine))
    try:
        code, body = _post(port, "/api/sso/login", {"profile": "nope"})
        assert code == 400 and "profile" in body["error"]
        code, body = _get_result(port, "/api/sso/login/unknown")
        assert code == 400 and "error" in body
        assert "AWS_PROFILE" not in os.environ
    finally:
        server.shutdown()
        server.server_close()

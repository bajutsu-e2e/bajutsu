"""Unit tests for both `SsoEngine` implementations (BE-0056), with the AWS/CLI boundary faked.

`NativeSsoEngine`'s boto3/botocore calls are confined to the module-level `_sso_config` /
`_token_loader` functions and its own `_oidc`; these tests monkeypatch those seams (or feed
botocore a fake config) so the device-flow logic — handle bookkeeping, token-dict assembly, and
the pending / complete / error mapping — is exercised without touching AWS. `CliSsoEngine`'s `aws`
calls are confined to the injected `popen` / `which` seams, faked here with a stand-in process
object. This is engine-internal logic, distinct from the HTTP wiring in test_http_sso.py.
"""

from __future__ import annotations

import shutil
import subprocess
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest

from bajutsu.serve import sso as sso_module
from bajutsu.serve.sso import (
    CliSsoEngine,
    NativeSsoEngine,
    SsoError,
    _expiry_iso,
    _is_future,
    default_sso_engine,
)


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
    monkeypatch.setattr(sso_module, "_sso_config", lambda _profile: _cfg())
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
    monkeypatch.setattr(sso_module, "_token_loader", lambda: loader)

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
    def boom(_profile: str) -> Any:
        raise SsoError("bad profile")

    monkeypatch.setattr(sso_module, "_sso_config", boom)
    with pytest.raises(SsoError):
        NativeSsoEngine().start("dev")


def test_status_unset_and_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    eng = NativeSsoEngine()
    assert eng.status(None).signed_in is False  # no profile → not signed in, no AWS touched
    monkeypatch.setattr(sso_module, "_sso_config", lambda _profile: _cfg())
    future = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
    monkeypatch.setattr(
        sso_module, "_token_loader", lambda: lambda _url, session_name: {"expiresAt": future}
    )
    session = eng.status("dev")
    assert session.signed_in and session.profile == "dev" and session.expires_at == future


def test_status_unreadable_cache_is_not_signed_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sso_module, "_sso_config", lambda _profile: _cfg())

    def no_cache() -> Any:
        def load(_url: str, session_name: str) -> dict[str, Any]:
            raise RuntimeError("no token cached")

        return load

    monkeypatch.setattr(sso_module, "_token_loader", no_cache)
    assert NativeSsoEngine().status("dev").signed_in is False


def test_native_logout_is_noop() -> None:
    NativeSsoEngine().logout("dev")  # no-op; the assertion is just that it doesn't raise


def test_sso_config_resolution(monkeypatch: pytest.MonkeyPatch) -> None:
    import botocore.session

    full = {
        "profiles": {"dev": {"sso_session": "my"}, "nosso": {}},
        "sso_sessions": {"my": {"sso_start_url": "https://x/start", "sso_region": "us-east-1"}},
    }
    monkeypatch.setattr(botocore.session, "Session", lambda: SimpleNamespace(full_config=full))
    cfg = sso_module._sso_config("dev")
    assert cfg.start_url == "https://x/start" and cfg.region == "us-east-1"
    assert cfg.session_name == "my"
    with pytest.raises(SsoError):
        sso_module._sso_config("ghost")  # unknown profile
    with pytest.raises(SsoError):
        sso_module._sso_config("nosso")  # profile without an sso_session


def test_oidc_and_token_loader_build() -> None:
    # Both construct real boto3/botocore objects offline (no AWS call), so they're checkable here.
    assert hasattr(NativeSsoEngine()._oidc("us-east-1"), "create_token")
    assert hasattr(sso_module._token_loader(), "save_token")


class _FakeProcess:
    """A stand-in for `subprocess.Popen[str]`, driving `CliSsoEngine` without a real `aws` binary.

    `stdout` starts as an iterator over the canned prompt lines (consumed once, as a real pipe
    would be); ``finish`` simulates the process exiting after the caller has moved on to polling.
    """

    def __init__(self, lines: list[str]) -> None:
        self.stdout: Iterator[str] | None = iter(lines)
        self.returncode: int | None = None
        self.killed = False

    def finish(self, returncode: int = 0) -> None:
        self.returncode = returncode

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9


def test_cli_start_reports_aws_missing() -> None:
    eng = CliSsoEngine(which=lambda _name: None)
    with pytest.raises(SsoError, match="AWS CLI"):
        eng.start("dev")


def test_cli_start_then_poll_pending_then_complete(monkeypatch: pytest.MonkeyPatch) -> None:
    proc = _FakeProcess(
        [
            "Attempting to automatically open the SSO authorization page in your browser.\n",
            "If the browser does not open, open the following URL:\n",
            "\n",
            "https://device.sso.us-east-1.amazonaws.com/\n",
            "\n",
            "Then enter the code:\n",
            "WXYZ-7890\n",
        ]
    )
    eng = CliSsoEngine(popen=lambda *_a, **_kw: proc, which=lambda _name: "/usr/local/bin/aws")

    auth = eng.start("dev")
    assert auth.verification_uri == "https://device.sso.us-east-1.amazonaws.com/"
    assert auth.user_code == "WXYZ-7890"
    assert auth.handle

    assert eng.poll(auth.handle).status == "pending"  # `aws sso login` still waiting on approval

    proc.finish(0)
    monkeypatch.setattr(
        sso_module, "_cache_status", lambda profile: sso_module.SsoSession(profile, True)
    )
    progress = eng.poll(auth.handle)
    assert progress.status == "complete"
    assert progress.session is not None and progress.session.profile == "dev"
    with pytest.raises(SsoError):  # single-use handle
        eng.poll(auth.handle)


def test_cli_poll_nonzero_exit_drops_handle() -> None:
    proc = _FakeProcess(["some prompt line with a URL https://device.sso/ and code AB12-CD34\n"])
    eng = CliSsoEngine(popen=lambda *_a, **_kw: proc, which=lambda _name: "/usr/local/bin/aws")
    auth = eng.start("dev")
    proc.finish(1)
    with pytest.raises(SsoError):
        eng.poll(auth.handle)
    with pytest.raises(SsoError):  # handle dropped after the failure
        eng.poll(auth.handle)


def test_cli_poll_unknown_handle() -> None:
    with pytest.raises(SsoError):
        CliSsoEngine().poll("nope")


def test_cli_start_process_exits_without_prompt_is_killed() -> None:
    proc = _FakeProcess(["The config profile (dev) could not be found\n"])
    eng = CliSsoEngine(popen=lambda *_a, **_kw: proc, which=lambda _name: "/usr/local/bin/aws")
    with pytest.raises(SsoError, match="could not be found"):
        eng.start("dev")
    assert proc.killed  # start() kills a process that never printed a usable prompt


def test_cli_start_popen_oserror() -> None:
    def boom(*_a: Any, **_kw: Any) -> subprocess.Popen[str]:
        raise OSError("no such file")

    eng = CliSsoEngine(popen=boom, which=lambda _name: "/usr/local/bin/aws")
    with pytest.raises(SsoError):
        eng.start("dev")


def test_cli_status_delegates_to_shared_cache_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sso_module, "_cache_status", lambda profile: sso_module.SsoSession(profile, True)
    )
    assert CliSsoEngine().status("dev").signed_in is True


def test_cli_logout_shells_out_and_swallows_errors() -> None:
    calls: list[list[str]] = []

    def fake_popen(cmd: list[str], **_kw: Any) -> _FakeProcess:
        calls.append(cmd)
        return _FakeProcess([])

    CliSsoEngine(popen=fake_popen, which=lambda _name: "/usr/local/bin/aws").logout("dev")
    assert calls == [["aws", "sso", "logout", "--profile", "dev"]]

    # No `aws` on PATH: a no-op, not an error.
    CliSsoEngine(popen=fake_popen, which=lambda _name: None).logout("dev")
    assert len(calls) == 1  # unchanged — popen wasn't called this time

    # A failing shell-out is swallowed (best-effort; AWS_PROFILE clearing is what actually matters).
    def raising_popen(*_a: Any, **_kw: Any) -> _FakeProcess:
        raise OSError("no such file")

    CliSsoEngine(popen=raising_popen, which=lambda _name: "/usr/local/bin/aws").logout("dev")


def test_default_sso_engine_prefers_cli_when_present(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shutil, "which", lambda _name: "/usr/local/bin/aws")
    assert isinstance(default_sso_engine(), CliSsoEngine)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    assert isinstance(default_sso_engine(), NativeSsoEngine)

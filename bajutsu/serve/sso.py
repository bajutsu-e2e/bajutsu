"""AWS SSO (IAM Identity Center) sign-in for the Bedrock provider, driven from the web UI (BE-0056).

The web UI starts an SSO **device-authorization** flow and surfaces the verification URL + user code
in the browser; on approval the resulting SSO token is written to AWS's standard token cache, and the
serve operations layer points spawned Bedrock jobs at it via ``AWS_PROFILE``. botocore's own
credential chain then resolves role credentials from that cache at job time — so re-authentication
needs **no** ``serve`` restart (each ``record`` / ``crawl`` job is a fresh subprocess that re-resolves
the chain; see ``bajutsu/serve/jobs.py`` ``_spawn_env``).

Two engines share one ``SsoEngine`` seam and produce the same outcome, a populated standard SSO token
cache: ``NativeSsoEngine`` drives boto3 ``sso-oidc`` directly, while ``CliSsoEngine`` delegates to
`aws sso login` when AWS CLI v2 is present. ``boto3`` / ``botocore`` (the ``anthropic[bedrock]``
extra) are imported **lazily**, only inside the functions that need them, so this module stays on the
default, server-free serve import path (``tests/serve/test_import_guard.py``) — ``CliSsoEngine``
itself needs no extra, since it only shells out. The engine is injected into ``ServeState`` so the
deterministic gate drives a fake without touching AWS. Nothing here runs in the ``run`` / CI gate.
"""

from __future__ import annotations

import contextlib
import os
import queue
import re
import secrets
import shutil
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

AWS_PROFILE_ENV = "AWS_PROFILE"
_CLIENT_NAME = "bajutsu-serve"
# create_token reports these while the user hasn't approved yet — keep polling rather than failing.
_PENDING_CODES = frozenset({"AuthorizationPendingException", "SlowDownException"})
_INSTALL_HINT = (
    "Bedrock SSO needs the anthropic Bedrock extra; install it with `uv sync --extra bedrock`."
)
# `subprocess.Popen` injection point (mirrors `bajutsu/serve/jobs.py`'s `Popen` alias), so tests
# drive `CliSsoEngine` against a fake process instead of a real `aws` binary.
Popen = Callable[..., Any]


class SsoError(RuntimeError):
    """A sign-in could not be started or completed: an unknown/misconfigured profile, a denied or
    expired authorization, AWS unreachable, or the optional AWS SDK extra not installed."""


@dataclass(frozen=True)
class DeviceAuthorization:
    """What the browser needs to approve a sign-in, plus the opaque ``handle`` the poll step resumes
    from. The device code and client secret stay inside the engine — never handed to the client."""

    verification_uri: str  # verificationUriComplete — open in the user's own browser
    user_code: str
    expires_in: int  # seconds the code stays valid
    interval: int  # minimum seconds between polls
    handle: str


@dataclass(frozen=True)
class SsoSession:
    """An SSO session as the UI shows it: the bound profile, whether it currently resolves, and when
    the cached token expires (ISO-8601, when known)."""

    profile: str | None
    signed_in: bool
    expires_at: str | None = None


@dataclass(frozen=True)
class LoginProgress:
    """One poll's outcome: still ``pending`` (keep polling), or ``complete`` with the live session."""

    status: str  # "pending" | "complete"
    session: SsoSession | None = None


class SsoEngine(Protocol):
    """The seam between serve and the AWS SSO mechanism (BE-0056). ``NativeSsoEngine`` drives boto3
    ``sso-oidc``; a CLI-delegation engine (``aws sso login``) can implement the same surface. Injected
    into ``ServeState`` so the gate drives a fake without touching AWS."""

    def start(self, profile: str) -> DeviceAuthorization:
        """Start a sign-in for *profile*; return the verification URL/code + a poll handle."""

    def poll(self, handle: str) -> LoginProgress:
        """Advance a sign-in: pending until the user approves, then complete with the session."""

    def status(self, profile: str | None) -> SsoSession:
        """The current SSO session for *profile* (signed-in + expiry), without signing in."""

    def logout(self, profile: str) -> None:
        """Forget *profile*'s session (best-effort token-cache invalidation)."""


@dataclass(frozen=True)
class _SsoConfig:
    region: str
    start_url: str
    session_name: str


@dataclass
class _Pending:
    """An in-flight device authorization, kept in-engine between ``start`` and ``poll``."""

    profile: str
    cfg: _SsoConfig
    client_id: str
    client_secret: str
    registration_expires_at: int  # epoch seconds (clientSecretExpiresAt from register_client)
    device_code: str


def _expiry_iso(value: Any) -> str | None:
    """Normalize a cached ``expiresAt`` (datetime or ISO string) to an ISO-8601 string for the UI."""
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value) if value else None


def _is_future(value: Any) -> bool:
    """Whether a cached ``expiresAt`` is still in the future (so the session is usable)."""
    if isinstance(value, datetime):
        expiry = value
    else:
        try:
            expiry = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return False
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry > datetime.now(UTC)


def _sso_config(profile: str) -> _SsoConfig:
    """Resolve a profile's ``sso_session`` block (start URL + region) from ``~/.aws/config``. Shared
    by both engines: whichever one signs in, the profile's SSO session is defined the same way."""
    try:
        from botocore.session import Session
    except ImportError as e:
        raise SsoError(_INSTALL_HINT) from e
    full = Session().full_config
    pcfg = full.get("profiles", {}).get(profile)
    if pcfg is None:
        raise SsoError(f"unknown AWS profile: {profile}")
    session_name = pcfg.get("sso_session")
    if not session_name:
        raise SsoError(
            f"profile {profile!r} has no sso_session — run `aws configure sso` to set one up"
        )
    sso = full.get("sso_sessions", {}).get(session_name)
    if not sso or "sso_start_url" not in sso or "sso_region" not in sso:
        raise SsoError(f"sso_session {session_name!r} is missing sso_start_url / sso_region")
    return _SsoConfig(sso["sso_region"], sso["sso_start_url"], session_name)


def _token_loader() -> Any:
    """The standard SSO token cache (``~/.aws/sso/cache``) botocore's credential chain reads —
    shared by both engines, since ``aws sso login`` writes exactly this format too."""
    from botocore.tokens import _sso_json_dumps
    from botocore.utils import JSONFileCache, SSOTokenLoader

    cache_dir = os.path.join(os.path.expanduser("~"), ".aws", "sso", "cache")
    return SSOTokenLoader(cache=JSONFileCache(cache_dir, dumps_func=_sso_json_dumps))


def _cache_status(profile: str | None) -> SsoSession:
    """*profile*'s session, read from the standard SSO token cache rather than by asking either
    engine directly — the native engine writes that cache itself, and ``aws sso login`` (the CLI
    engine) writes the identical format, so both engines report status the same way."""
    if not profile:
        return SsoSession(None, False)
    try:
        cfg = _sso_config(profile)
    except SsoError:
        return SsoSession(profile, False)
    try:
        cached = _token_loader()(cfg.start_url, session_name=cfg.session_name)
    except Exception:  # no/unreadable cache means simply "not signed in"
        return SsoSession(profile, False)
    expires_at = cached.get("expiresAt")
    return SsoSession(profile, _is_future(expires_at), _expiry_iso(expires_at))


class NativeSsoEngine:
    """Drives the AWS SSO device-authorization flow with boto3 ``sso-oidc`` and persists the token via
    botocore's own ``SSOTokenLoader``, so the cache key and format are exactly what the credential
    chain reads back. boto3/botocore are imported lazily here, never at module load.

    The v1 scope (BE-0056) assumes an existing ``sso_session``-based profile (``aws configure sso``).

    NOTE: the AWS calls can't run on the Linux gate (no AWS, optional SDK), so this engine is
    validated against a real IAM Identity Center setup; the serve wiring, operations, and UI are
    gate-tested through an injected fake (``tests/serve/test_http_sso.py``)."""

    def __init__(self) -> None:
        self._pending: dict[str, _Pending] = {}
        self._lock = threading.Lock()

    def _oidc(self, region: str) -> Any:
        try:
            import boto3
        except ImportError as e:  # the anthropic[bedrock] extra (boto3) isn't installed
            raise SsoError(_INSTALL_HINT) from e
        return boto3.client("sso-oidc", region_name=region)

    def start(self, profile: str) -> DeviceAuthorization:
        cfg = _sso_config(profile)
        oidc = self._oidc(cfg.region)
        try:
            reg = oidc.register_client(clientName=_CLIENT_NAME, clientType="public")
            auth = oidc.start_device_authorization(
                clientId=reg["clientId"],
                clientSecret=reg["clientSecret"],
                startUrl=cfg.start_url,
            )
        except Exception as e:  # surface any AWS/transport failure as one error type
            raise SsoError(f"could not start AWS SSO sign-in: {e}") from e
        handle = secrets.token_urlsafe(16)
        with self._lock:
            self._pending[handle] = _Pending(
                profile=profile,
                cfg=cfg,
                client_id=reg["clientId"],
                client_secret=reg["clientSecret"],
                registration_expires_at=int(reg["clientSecretExpiresAt"]),
                device_code=auth["deviceCode"],
            )
        return DeviceAuthorization(
            verification_uri=auth.get("verificationUriComplete") or auth["verificationUri"],
            user_code=auth["userCode"],
            expires_in=int(auth.get("expiresIn", 0)),
            interval=int(auth.get("interval", 5)),
            handle=handle,
        )

    def poll(self, handle: str) -> LoginProgress:
        with self._lock:
            pending = self._pending.get(handle)
        if pending is None:
            raise SsoError("unknown or expired sign-in; start again")
        from botocore.exceptions import ClientError

        oidc = self._oidc(pending.cfg.region)
        try:
            token = oidc.create_token(
                clientId=pending.client_id,
                clientSecret=pending.client_secret,
                grantType="urn:ietf:params:oauth:grant-type:device_code",
                deviceCode=pending.device_code,
            )
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code", "")
            if code in _PENDING_CODES:
                return LoginProgress("pending")
            with self._lock:
                self._pending.pop(handle, None)
            raise SsoError(f"AWS SSO sign-in failed: {code or e}") from e
        self._save_token(pending, token)
        with self._lock:
            self._pending.pop(handle, None)
        expires_at = (datetime.now(UTC) + timedelta(seconds=int(token["expiresIn"]))).isoformat()
        return LoginProgress("complete", SsoSession(pending.profile, True, expires_at))

    def _save_token(self, pending: _Pending, token: Any) -> None:
        """Persist the access token in botocore's standard cache so the credential chain reads it."""
        now = datetime.now(UTC)
        cached = {
            "startUrl": pending.cfg.start_url,
            "region": pending.cfg.region,
            "accessToken": token["accessToken"],
            "expiresAt": now + timedelta(seconds=int(token["expiresIn"])),
            "clientId": pending.client_id,
            "clientSecret": pending.client_secret,
            "registrationExpiresAt": datetime.fromtimestamp(pending.registration_expires_at, UTC),
        }
        if token.get("refreshToken"):
            cached["refreshToken"] = token["refreshToken"]
        _token_loader().save_token(
            pending.cfg.start_url, cached, session_name=pending.cfg.session_name
        )

    def status(self, profile: str | None) -> SsoSession:
        return _cache_status(profile)

    def logout(self, profile: str) -> None:
        """No-op for the native engine: clearing ``AWS_PROFILE`` (done by the operations layer) is
        what stops jobs from using the session. Revoking the cached token is left to
        ``aws sso logout`` — botocore exposes no token-cache delete."""
        return None


_CLI_URL_RE = re.compile(r"https://\S+")
_CLI_CODE_RE = re.compile(r"\b([A-Z0-9]{4}-[A-Z0-9]{4})\b")
_CLI_PROMPT_TIMEOUT = 15.0  # seconds to wait for `aws sso login` to print its verification prompt
_CLI_CODE_TTL = (
    600  # AWS CLI doesn't report the device code's TTL; a conservative default for the UI
)


@dataclass
class _CliPending:
    """An in-flight `aws sso login` process, kept between ``start`` and ``poll``."""

    profile: str
    process: subprocess.Popen[str]


def _read_cli_prompt(process: subprocess.Popen[str]) -> tuple[str, str]:
    """Read *process*'s stdout, on a background thread, until the verification URL + user code both
    appear, the process exits first (a startup failure — an unknown profile, no `sso_session`, …),
    or ``_CLI_PROMPT_TIMEOUT`` elapses. The thread — not the caller — blocks on the pipe, so a
    stalled `aws sso login` can't hang the request past the timeout."""
    assert process.stdout is not None
    lines: queue.Queue[str | None] = queue.Queue()

    def _pump() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            lines.put(line)
        lines.put(None)  # stdout closed (the process exited)

    threading.Thread(target=_pump, daemon=True).start()
    seen: list[str] = []
    url = code = None
    deadline = time.monotonic() + _CLI_PROMPT_TIMEOUT
    while (remaining := deadline - time.monotonic()) > 0:
        try:
            line = lines.get(timeout=remaining)
        except queue.Empty:
            break
        if line is None:
            break
        seen.append(line)
        if url is None and (m := _CLI_URL_RE.search(line)):
            url = m.group(0)
        if code is None and (m := _CLI_CODE_RE.search(line)):
            code = m.group(1)
        if url and code:
            return url, code
    output = "".join(seen).strip()
    raise SsoError(f"aws sso login did not print a verification prompt: {output or 'no output'}")


class CliSsoEngine:
    """Drives AWS SSO sign-in by shelling out to `aws sso login --no-browser` (BE-0056's
    CLI-delegation engine) rather than reimplementing the device-authorization protocol —
    mirroring how `make serve` shells out to idb instead of reimplementing its wire format. AWS
    CLI v2 owns the token cache and its refresh; this engine only starts the process, parses the
    verification URL/code it prints on stdout, and polls whether it has exited.

    `popen` / `which` are injected (mirroring `bajutsu/serve/jobs.py`'s `Popen` seam) so the gate
    drives this engine against a fake process, never a real `aws` binary."""

    def __init__(
        self,
        *,
        popen: Popen = subprocess.Popen,
        which: Callable[[str], str | None] = shutil.which,
    ) -> None:
        self._popen = popen
        self._which = which
        self._pending: dict[str, _CliPending] = {}
        self._lock = threading.Lock()

    def start(self, profile: str) -> DeviceAuthorization:
        if self._which("aws") is None:
            raise SsoError("AWS CLI v2 is not installed; install it or use the native SSO engine")
        try:
            process = self._popen(
                ["aws", "sso", "login", "--no-browser", "--profile", profile],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
        except OSError as e:
            raise SsoError(f"could not start `aws sso login`: {e}") from e
        try:
            verification_uri, user_code = _read_cli_prompt(process)
        except SsoError:
            with contextlib.suppress(OSError, subprocess.SubprocessError):
                process.kill()
                process.wait(timeout=5)  # reap it — a killed-but-unwaited Popen leaks a zombie
            raise
        handle = secrets.token_urlsafe(16)
        with self._lock:
            self._pending[handle] = _CliPending(profile, process)
        return DeviceAuthorization(
            verification_uri=verification_uri,
            user_code=user_code,
            expires_in=_CLI_CODE_TTL,
            interval=5,
            handle=handle,
        )

    def poll(self, handle: str) -> LoginProgress:
        with self._lock:
            pending = self._pending.get(handle)
        if pending is None:
            raise SsoError("unknown or expired sign-in; start again")
        if pending.process.poll() is None:
            return LoginProgress("pending")
        with self._lock:
            self._pending.pop(handle, None)
        if pending.process.returncode != 0:
            raise SsoError(f"aws sso login exited with status {pending.process.returncode}")
        return LoginProgress("complete", _cache_status(pending.profile))

    def status(self, profile: str | None) -> SsoSession:
        return _cache_status(profile)

    def logout(self, profile: str) -> None:
        """Best-effort: shell out to `aws sso logout`, which the native engine cannot do (botocore
        exposes no token-cache delete). Clearing `AWS_PROFILE` (the operations layer) is what
        actually stops jobs from using the session, so a failure here is swallowed."""
        if self._which("aws") is None:
            return
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            self._popen(
                ["aws", "sso", "logout", "--profile", profile],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            ).wait(timeout=10)


def default_sso_engine() -> SsoEngine:
    """The engine ``ServeState`` wires in out of the box: `aws sso login` when AWS CLI v2 is on
    ``PATH`` — the more robust path, since the CLI owns the token cache and its own refresh — else
    the native boto3 ``sso-oidc`` flow (BE-0056's "prefer the CLI when present" auto-selection)."""
    if shutil.which("aws") is not None:
        return CliSsoEngine()
    return NativeSsoEngine()

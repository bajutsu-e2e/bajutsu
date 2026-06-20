"""Tests for `bajutsu serve` token authentication (BE-0051 slice 2).

With no token configured the server is open (the loopback-only legacy behavior). With a token,
every request must present a `Bearer` header or a session cookie obtained from POST /api/login;
the token itself is never put in a URL or stored in the browser.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from _shared import _serve

from bajutsu import serve as srv


def _request(
    port: int,
    path: str,
    *,
    method: str = "GET",
    headers: dict[str, str] | None = None,
    body: Any = None,
) -> tuple[int, dict[str, str], bytes]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=data, method=method, headers=headers or {}
    )
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _state(tmp_path: Path, token: str | None) -> srv.ServeState:
    runs = tmp_path / "runs"
    runs.mkdir()
    return srv.ServeState(runs_dir=runs, root=tmp_path, cwd=tmp_path, token=token)


def test_open_when_no_token(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, None))
    try:
        status, _, _ = _request(port, "/api/runs")
        assert status == 200  # legacy loopback behavior: no auth
    finally:
        server.shutdown()
        server.server_close()


def test_api_requires_auth_when_token_set(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, "s3cret"))
    try:
        status, _, _ = _request(port, "/api/runs")
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()


def test_index_is_open_so_the_login_ui_can_load(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, "s3cret"))
    try:
        status, _, _ = _request(port, "/")
        assert status == 200
    finally:
        server.shutdown()
        server.server_close()


def test_bearer_header_authorizes(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, "s3cret"))
    try:
        ok, _, _ = _request(port, "/api/runs", headers={"Authorization": "Bearer s3cret"})
        bad, _, _ = _request(port, "/api/runs", headers={"Authorization": "Bearer nope"})
        assert ok == 200
        assert bad == 401
    finally:
        server.shutdown()
        server.server_close()


def test_login_sets_cookie_then_cookie_authorizes(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, "s3cret"))
    try:
        status, headers, _ = _request(
            port,
            "/api/login",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={"token": "s3cret"},
        )
        assert status == 200
        set_cookie = headers["Set-Cookie"]
        assert "bajutsu_session=" in set_cookie
        assert "HttpOnly" in set_cookie and "SameSite=Strict" in set_cookie
        cookie = set_cookie.split(";")[0]  # bajutsu_session=<sid>
        authed, _, _ = _request(port, "/api/runs", headers={"Cookie": cookie})
        assert authed == 200
    finally:
        server.shutdown()
        server.server_close()


def test_login_rejects_wrong_token(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, "s3cret"))
    try:
        status, headers, _ = _request(
            port,
            "/api/login",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={"token": "wrong"},
        )
        assert status == 401
        assert "Set-Cookie" not in headers
    finally:
        server.shutdown()
        server.server_close()


def test_stale_cookie_is_rejected(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, "s3cret"))
    try:
        status, _, _ = _request(port, "/api/runs", headers={"Cookie": "bajutsu_session=bogus"})
        assert status == 401
    finally:
        server.shutdown()
        server.server_close()


# --- CSRF (Origin check) + security headers (slice 4) --------------------------------------


def _auth_post(port: int, path: str, *, origin: str | None) -> int:
    headers = {"Content-Type": "application/json", "Authorization": "Bearer s3cret"}
    if origin is not None:
        headers["Origin"] = origin
    return _request(port, path, method="POST", headers=headers, body={"token": "s3cret"})[0]


def test_cross_origin_post_is_blocked(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, "s3cret"))
    try:
        # A mismatched Origin (a cross-site forgery) is rejected even with a valid credential.
        assert _auth_post(port, "/api/login", origin="http://evil.example") == 403
        # Same-origin (Origin matches Host) is allowed; no Origin (API client) is allowed.
        assert _auth_post(port, "/api/login", origin=f"http://127.0.0.1:{port}") == 200
        assert _auth_post(port, "/api/login", origin=None) == 200
    finally:
        server.shutdown()
        server.server_close()


def test_origin_unchecked_on_open_server(tmp_path: Path) -> None:
    # No token configured -> loopback-only open server; the Origin check does not apply.
    server, port = _serve(_state(tmp_path, None))
    try:
        status, _, _ = _request(
            port,
            "/api/login",
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
            body={"token": "x"},
        )
        assert status != 403  # login returns 401 (no token configured), not a CSRF block
    finally:
        server.shutdown()
        server.server_close()


def test_security_headers_on_every_response(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, None))
    try:
        _, headers, _ = _request(port, "/api/runs")
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("X-Frame-Options") == "DENY"
        assert headers.get("Referrer-Policy") == "no-referrer"
    finally:
        server.shutdown()
        server.server_close()


def test_non_object_json_body_is_rejected(tmp_path: Path) -> None:
    # A JSON array (not an object) must 400, not 500 on a downstream `.get(...)`.
    server, port = _serve(_state(tmp_path, None))
    try:
        status, _, _ = _request(
            port,
            "/api/config",
            method="POST",
            headers={"Content-Type": "application/json"},
            body=[1, 2, 3],
        )
        assert status == 400
    finally:
        server.shutdown()
        server.server_close()

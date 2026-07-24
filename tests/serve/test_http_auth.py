"""Tests for `bajutsu serve` token authentication (BE-0051 slice 2).

With no token configured the server is open (the loopback-only legacy behavior). With a token,
every request must present a `Bearer` header or a session cookie obtained from POST /api/login;
the token itself is never put in a URL or stored in the browser.
"""

from __future__ import annotations

import http.client
import json
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from _shared import _serve

from bajutsu import serve as srv


class _FakeOAuth:
    """Stand-in for the GitHub OAuth client — no network."""

    def authorize_url(self, state: str) -> str:
        return f"https://github.test/login/oauth/authorize?state={state}"

    def fetch_login(self, code: str) -> str | None:
        return "alice"


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
    return srv.ServeState(
        runs_dir=runs, root=tmp_path, cwd=tmp_path, auth=srv.SessionManager(token=token)
    )


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


def test_frontend_modules_are_open_so_the_login_ui_can_load(tmp_path: Path) -> None:
    """The serve frontend is ES modules (BE-0247), loaded before login just like the index — so
    every /serve.*.mjs route is auth-exempt (they carry only public UI code). With a token set, a
    module route still 200s while a normal API route 401s, proving the _MODULE_PATHS gate exemption
    is what's doing it (not the no-token open default)."""
    server, port = _serve(_state(tmp_path, "s3cret"))
    try:
        assert _request(port, "/api/runs")[0] == 401  # a gated route: auth required
        for name in srv.handler._JS_MODULES:
            assert _request(port, f"/{name}")[0] == 200, name  # exempt: loads before auth
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


def test_bearer_narrows_to_worker_paths_once_oauth_configured(tmp_path: Path) -> None:
    # BE-0313: with OAuth configured the shared token authorizes only worker traffic on the stdlib
    # backend — a non-worker endpoint rejects the raw Bearer, a worker route still accepts it.
    state = _state(tmp_path, "s3cret")
    state.auth.oauth = _FakeOAuth()
    server, port = _serve(state)
    try:
        headers = {"Authorization": "Bearer s3cret"}
        blocked, _, _ = _request(port, "/api/runs", headers=headers)
        assert blocked == 401  # non-worker endpoint no longer honors the Bearer token
        # A worker route clears the gate (it 404s/errors past it, but never 401 — the gate admitted it).
        worker, _, _ = _request(
            port,
            "/api/worker/lease",
            method="POST",
            headers={**headers, "Content-Type": "application/json"},
            body={"worker_id": "w1"},
        )
        assert worker != 401
    finally:
        server.shutdown()
        server.server_close()


def test_token_login_disabled_once_oauth_configured(tmp_path: Path) -> None:
    # BE-0313: the token-cookie exchange is retired when OAuth is configured — a human then signs in
    # only through /api/oauth/login.
    state = _state(tmp_path, "s3cret")
    state.auth.oauth = _FakeOAuth()
    server, port = _serve(state)
    try:
        status, headers, _ = _request(
            port,
            "/api/login",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={"token": "s3cret"},
        )
        assert status == 404
        assert "Set-Cookie" not in headers
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


def test_cross_origin_post_blocked_even_without_token(tmp_path: Path) -> None:
    # BE-0121: the Origin/CSRF check runs unconditionally, so a cross-origin state-changing POST is
    # blocked even on the no-token loopback default — closing the CSRF-to-arbitrary-config hole.
    server, port = _serve(_state(tmp_path, None))
    try:
        blocked, _, _ = _request(
            port,
            "/api/config",
            method="POST",
            headers={"Content-Type": "application/json", "Origin": "http://evil.example"},
            body={"git": "github:evil/repo@main"},
        )
        assert blocked == 403
        # A non-browser client (no Origin) is still allowed through unchanged.
        no_origin, _, _ = _request(
            port,
            "/api/config",
            method="POST",
            headers={"Content-Type": "application/json"},
            body={"path": "/nonexistent"},
        )
        assert no_origin != 403
    finally:
        server.shutdown()
        server.server_close()


def test_mismatched_host_is_rejected(tmp_path: Path) -> None:
    # BE-0121: DNS-rebinding defense — a request whose Host isn't a bound interface is refused, so a
    # rebound hostname can't reach /api/apikey to probe the API key even without the CSRF bypass.
    server, port = _serve(_state(tmp_path, None))
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/api/apikey", headers={"Host": "attacker.example"})
        assert conn.getresponse().status == 403
        conn.close()
        # The loopback Host a browser actually sends to `make serve` is accepted.
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/api/apikey", headers={"Host": f"127.0.0.1:{port}"})
        assert conn.getresponse().status == 200
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_security_headers_on_every_response(tmp_path: Path) -> None:
    server, port = _serve(_state(tmp_path, None))
    try:
        _, headers, _ = _request(port, "/api/runs")
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert headers.get("X-Frame-Options") == "SAMEORIGIN"
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


def test_oauth_login_redirects_and_sets_state_cookie(tmp_path: Path) -> None:
    # The stdlib handler mirrors the FastAPI app: GET /api/oauth/login 302s to GitHub and stashes
    # the CSRF state in a cookie. Use a raw connection so the 302 isn't auto-followed to github.test.
    state = _state(tmp_path, None)
    state.auth.oauth = _FakeOAuth()
    server, port = _serve(state)
    try:
        conn = http.client.HTTPConnection("127.0.0.1", port)
        conn.request("GET", "/api/oauth/login")
        resp = conn.getresponse()
        assert resp.status == 302
        assert "github.test" in (resp.getheader("Location") or "")
        assert "bajutsu_oauth_state" in (resp.getheader("Set-Cookie") or "")
        conn.close()
    finally:
        server.shutdown()
        server.server_close()

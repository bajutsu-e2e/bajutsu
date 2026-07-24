"""Unit tests for the shared request-gate policy (BE-0253 Part 5).

`bajutsu/serve/gate.py` defines the auth / CSRF / Host / hardening policy once, so the two serve
backends can't drift it. These tests pin each pure decision directly, independent of either
transport, so a change to the policy shows up here rather than only in the two HTTP suites.
"""

from __future__ import annotations

from bajutsu.serve import gate
from bajutsu.serve.state import SessionManager


def test_hardening_headers_are_the_three_documented_ones() -> None:
    assert gate.HARDENING_HEADERS == {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "SAMEORIGIN",
        "Referrer-Policy": "no-referrer",
    }


def test_allowed_hosts_loopback_bind_accepts_every_loopback_name() -> None:
    assert gate.allowed_hosts("127.0.0.1") == frozenset({"localhost", "127.0.0.1", "::1"})


def test_allowed_hosts_named_bind_adds_that_name_plus_loopback() -> None:
    assert gate.allowed_hosts("example.test") == frozenset(
        {"localhost", "127.0.0.1", "::1", "example.test"}
    )


def test_allowed_hosts_wildcard_bind_is_empty_so_enforcement_is_off() -> None:
    for wildcard in ("", "0.0.0.0", "::"):
        assert gate.allowed_hosts(wildcard) == frozenset()


def test_host_allowed_empty_allowlist_accepts_any_host() -> None:
    assert gate.host_allowed(frozenset(), "anything.example") is True


def test_host_allowed_enforces_membership_when_configured() -> None:
    allowed = frozenset({"localhost", "127.0.0.1"})
    assert gate.host_allowed(allowed, "localhost:8765") is True
    assert gate.host_allowed(allowed, "evil.example") is False


def test_csrf_ok_allows_a_request_with_no_origin() -> None:
    assert gate.csrf_ok(None, "localhost:8765") is True


def test_csrf_ok_matches_origin_netloc_against_host() -> None:
    assert gate.csrf_ok("http://localhost:8765", "localhost:8765") is True
    assert gate.csrf_ok("http://evil.example", "localhost:8765") is False


def test_is_open_covers_the_login_ui_login_endpoint_and_frontend_modules() -> None:
    assert gate.is_open("GET", "/") is True
    assert gate.is_open("GET", "/index.html") is True
    assert gate.is_open("GET", "/api/oauth/login") is True
    assert gate.is_open("GET", "/api/oauth/callback") is True
    assert gate.is_open("POST", "/api/login") is True
    # The frontend ES modules load before login (BE-0247), so their GET routes are open too. Matched
    # by shape, so an as-yet-unknown module name is still exempt (it 404s at the serving layer).
    assert gate.is_open("GET", "/serve.core.mjs") is True
    assert gate.is_open("GET", "/serve.author.mjs") is True
    assert gate.is_open("GET", "/serve.future.mjs") is True
    # Everything else is gated — including a non-GET on a module path or a non-module static asset.
    assert gate.is_open("GET", "/api/runs") is False
    assert gate.is_open("POST", "/") is False
    assert gate.is_open("GET", "/api/login") is False
    assert gate.is_open("POST", "/serve.core.mjs") is False
    assert gate.is_open("GET", "/serve.css") is False


def test_is_authorized_by_bearer_token() -> None:
    auth = SessionManager(token="s3cret")
    assert gate.is_authorized(auth, "Bearer s3cret", None, path="/api/runs") is True
    assert gate.is_authorized(auth, "Bearer wrong", None, path="/api/runs") is False
    assert gate.is_authorized(auth, "", None, path="/api/runs") is False


def test_is_authorized_by_session_cookie() -> None:
    auth = SessionManager(token="s3cret")
    sid = auth.issue_session()
    assert gate.is_authorized(auth, "", sid, path="/api/runs") is True
    assert gate.is_authorized(auth, "", "not-a-session", path="/api/runs") is False
    assert gate.is_authorized(auth, "", None, path="/api/runs") is False


class _StubOAuth:
    """A minimal non-None `oauth` so `SessionManager.oauth` reads as configured (BE-0313)."""

    def authorize_url(self, state: str) -> str:
        return ""

    def fetch_identity(self, code: str):  # type: ignore[no-untyped-def]
        return None


_WORKER_PATHS = (
    "/api/worker/lease",
    "/api/worker/heartbeat",
    "/api/worker/result",
    "/api/worker/artifact-urls",
    "/api/worker/scenario-url",
    # A worker also requests evidence upload URLs here (BE-0257), outside the /api/worker/ prefix.
    "/api/runs/20260101-000000/upload-urls",
)


def test_bearer_token_authorizes_any_path_without_oauth() -> None:
    # The single-Mac token deployment (no OAuth): the shared token still reaches every endpoint.
    auth = SessionManager(token="s3cret")
    assert gate.is_authorized(auth, "Bearer s3cret", None, path="/api/config") is True
    for path in _WORKER_PATHS:
        assert gate.is_authorized(auth, "Bearer s3cret", None, path=path) is True


def test_bearer_token_narrows_to_worker_paths_once_oauth_is_configured() -> None:
    # BE-0313: once OAuth is configured the token authorizes only worker traffic, closing the direct
    # Bearer bypass on every other endpoint.
    auth = SessionManager(token="s3cret", oauth=_StubOAuth())
    for path in _WORKER_PATHS:
        assert gate.is_authorized(auth, "Bearer s3cret", None, path=path) is True
    for path in (
        "/api/config",
        "/api/run",
        "/api/runs",
        "/api/apikey",
        # A non-upload /api/runs/ path (an editor action, not worker traffic) must not match the
        # upload-urls shape check — only the exact "/upload-urls" suffix is worker traffic.
        "/api/runs/20260101-000000/restore",
    ):
        assert gate.is_authorized(auth, "Bearer s3cret", None, path=path) is False
    # A valid session cookie still authorizes a non-worker path (the human OAuth session).
    sid = auth.issue_session("alice")
    assert gate.is_authorized(auth, "Bearer s3cret", sid, path="/api/run") is True


def test_actor_for_returns_the_session_identity() -> None:
    auth = SessionManager(token="s3cret")
    sid = auth.issue_session("alice")
    assert gate.actor_for(auth, sid) == "alice"
    # A shared-token session (no identity) and an unknown / absent session have no actor.
    assert gate.actor_for(auth, auth.issue_session()) is None
    assert gate.actor_for(auth, "not-a-session") is None
    assert gate.actor_for(auth, None) is None

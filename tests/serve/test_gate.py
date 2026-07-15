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


def test_is_open_covers_the_login_ui_and_login_endpoints_only() -> None:
    assert gate.is_open("GET", "/") is True
    assert gate.is_open("GET", "/index.html") is True
    assert gate.is_open("GET", "/api/oauth/login") is True
    assert gate.is_open("GET", "/api/oauth/callback") is True
    assert gate.is_open("POST", "/api/login") is True
    # Everything else is gated.
    assert gate.is_open("GET", "/api/runs") is False
    assert gate.is_open("POST", "/") is False
    assert gate.is_open("GET", "/api/login") is False


def test_is_authorized_by_bearer_token() -> None:
    auth = SessionManager(token="s3cret")
    assert gate.is_authorized(auth, "Bearer s3cret", None) is True
    assert gate.is_authorized(auth, "Bearer wrong", None) is False
    assert gate.is_authorized(auth, "", None) is False


def test_is_authorized_by_session_cookie() -> None:
    auth = SessionManager(token="s3cret")
    sid = auth.issue_session()
    assert gate.is_authorized(auth, "", sid) is True
    assert gate.is_authorized(auth, "", "not-a-session") is False
    assert gate.is_authorized(auth, "", None) is False


def test_actor_for_returns_the_session_identity() -> None:
    auth = SessionManager(token="s3cret")
    sid = auth.issue_session("alice")
    assert gate.actor_for(auth, sid) == "alice"
    # A shared-token session (no identity) and an unknown / absent session have no actor.
    assert gate.actor_for(auth, auth.issue_session()) is None
    assert gate.actor_for(auth, "not-a-session") is None
    assert gate.actor_for(auth, None) is None

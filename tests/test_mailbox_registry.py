"""Tests for the mailbox provider registry (bajutsu/common/runner/mailbox.py, BE-0186).

The `email` step reads its inbox through a `MailboxReader` seam; BE-0186 turns the single hardcoded
HTTP path into a registry keyed by transport `kind` (`http`, later `imap`), mirroring the AI
provider registry (BE-0104). These tests cover the built-in `http` default, fail-closed resolution
of an unknown `kind`, that the registry is a real extension point, and the built-in reader's own
`fetch` — its scheme guard, its request bounding, and how each transport fault becomes a step error.
All pure: `urlopen` is stubbed, so nothing here touches the network.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any

import pytest

from bajutsu.common.config import Mailbox
from bajutsu.common.drivers import base
from bajutsu.common.mailbox import MailboxMessage
from bajutsu.common.orchestrator import MailboxReader
from bajutsu.common.runner import mailbox as mb


def test_none_config_yields_no_reader() -> None:
    assert mb.build_mailbox_reader(None, {}) is None


def test_default_kind_is_http() -> None:
    # `kind` omitted -> the built-in HTTP reader, so a pre-BE-0186 `mailbox:` block is unchanged.
    reader = mb.build_mailbox_reader(Mailbox(url="https://inbox.test/api"), {})
    assert reader is not None
    assert type(reader).__name__ == "_HttpMailbox"


def test_explicit_http_kind_resolves_the_same_reader() -> None:
    reader = mb.build_mailbox_reader(Mailbox(kind="http", url="https://inbox.test/api"), {})
    assert reader is not None
    assert type(reader).__name__ == "_HttpMailbox"


def test_unknown_kind_fails_closed() -> None:
    # Fail-closed at resolution (like BE-0104's `_provider_name`), never a silent fallback.
    with pytest.raises(ValueError, match="unknown mailbox kind 'imap'"):
        mb.build_mailbox_reader(Mailbox(kind="imap", url="https://inbox.test/api"), {})


def test_registry_is_a_real_extension_point() -> None:
    """Register a fake transport, resolve it, then remove it (global registry)."""

    class _FakeReader:
        def fetch(self, timeout: float) -> list[MailboxMessage]:
            return []

    def _adapter(cfg: Mailbox, bindings: object) -> MailboxReader:
        return _FakeReader()

    mb.register("fake", _adapter)
    try:
        reader = mb.build_mailbox_reader(Mailbox(kind="fake", url="x"), {})
        assert isinstance(reader, _FakeReader)
    finally:
        mb._ADAPTERS.pop("fake", None)


# --- the built-in http reader's fetch -------------------------------------------------------------


class _FakeResponse:
    """The context-manager response `urlopen` hands back, carrying a fixed body."""

    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> _FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


def _stub_urlopen(
    monkeypatch: pytest.MonkeyPatch, calls: list[tuple[urllib.request.Request, float]], body: bytes
) -> None:
    """Answer every request with *body*, recording the request object and the timeout it was given."""

    def fake(req: urllib.request.Request, timeout: float = 0.0) -> _FakeResponse:
        calls.append((req, timeout))
        return _FakeResponse(body)

    monkeypatch.setattr(urllib.request, "urlopen", fake)


def _raising_urlopen(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> list[object]:
    """Make every request raise *exc*; returns the (initially empty) call log so a test can assert
    the request was in fact attempted."""
    calls: list[object] = []

    def fake(req: urllib.request.Request, timeout: float = 0.0) -> _FakeResponse:
        calls.append(req)
        raise exc

    monkeypatch.setattr(urllib.request, "urlopen", fake)
    return calls


def _reader(cfg: Mailbox, bindings: dict[str, str] | None = None) -> MailboxReader:
    reader = mb.build_mailbox_reader(cfg, bindings or {})
    assert reader is not None
    return reader


def test_a_non_http_url_is_refused_before_any_request(monkeypatch: pytest.MonkeyPatch) -> None:
    """The scheme guard is the reason `urlopen` carries an S310 suppression — so it must hold.

    `url` is operator-supplied config, commonly through a `${secrets.*}` indirection, and `urlopen`
    would happily open `file://` or `ftp://`. The guard rejects anything but http/https, and it has
    to do so *before* the request, not by inspecting a failure afterwards.
    """
    calls = _raising_urlopen(monkeypatch, AssertionError("must not reach urlopen"))
    reader = _reader(Mailbox(url="file:///etc/passwd"))
    with pytest.raises(base.SelectorError, match="must be http/https"):
        reader.fetch(5.0)
    assert calls == []


def test_fetch_normalizes_the_provider_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    # The `messages` path and `fields` map absorb a provider's own JSON shape, so no per-provider
    # code is needed one level up.
    calls: list[tuple[urllib.request.Request, float]] = []
    payload: dict[str, Any] = {
        "data": {
            "items": [
                {
                    "rcpt": "a@example.test",
                    "subj": "Verify",
                    "text": "code 4242",
                    "at": "2026-01-01",
                }
            ]
        }
    }
    _stub_urlopen(monkeypatch, calls, json.dumps(payload).encode("utf-8"))
    reader = _reader(
        Mailbox(
            url="https://inbox.test/api",
            messages="data.items",
            fields={"to": "rcpt", "subject": "subj", "body": "text", "receivedAt": "at"},
        )
    )
    messages = reader.fetch(5.0)
    assert [(m.to, m.subject, m.body, m.received_at) for m in messages] == [
        ("a@example.test", "Verify", "code 4242", "2026-01-01")
    ]


def test_fetch_bounds_the_request_by_the_polls_remaining_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A single request is capped at 30s, and takes the poll's own budget when that is shorter —
    so one slow request can neither hang the step nor outlive its `email.timeout`."""
    calls: list[tuple[urllib.request.Request, float]] = []
    _stub_urlopen(monkeypatch, calls, b"[]")
    reader = _reader(Mailbox(url="https://inbox.test/api"))
    reader.fetch(120.0)
    reader.fetch(2.5)
    assert [timeout for _req, timeout in calls] == [30.0, 2.5]


def test_secret_bindings_reach_the_request(monkeypatch: pytest.MonkeyPatch) -> None:
    # Credentials live in config-referenced secrets, never in the scenario — so the interpolated
    # values, not the `${secrets.*}` templates, are what the request carries.
    calls: list[tuple[urllib.request.Request, float]] = []
    _stub_urlopen(monkeypatch, calls, b"[]")
    reader = _reader(
        Mailbox(
            url="https://inbox.test/${secrets.inbox}",
            headers={"Authorization": "Bearer ${secrets.token}"},
        ),
        {"secrets.inbox": "team", "secrets.token": "s3cr3t"},
    )
    reader.fetch(5.0)
    req, _timeout = calls[0]
    assert req.full_url == "https://inbox.test/team"
    # urllib capitalizes header names it stores.
    assert req.get_header("Authorization") == "Bearer s3cr3t"


def test_an_http_status_becomes_a_step_error_naming_the_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _raising_urlopen(
        monkeypatch,
        urllib.error.HTTPError("https://inbox.test/api", 503, "Unavailable", {}, None),  # type: ignore[arg-type]
    )
    reader = _reader(Mailbox(url="https://inbox.test/api"))
    with pytest.raises(base.SelectorError, match="mailbox returned status 503"):
        reader.fetch(5.0)


def test_a_transport_failure_becomes_a_step_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _raising_urlopen(monkeypatch, urllib.error.URLError("connection refused"))
    reader = _reader(Mailbox(url="https://inbox.test/api"))
    with pytest.raises(base.SelectorError, match="mailbox fetch failed"):
        reader.fetch(5.0)


def test_a_malformed_body_becomes_a_step_error(monkeypatch: pytest.MonkeyPatch) -> None:
    # A 200 carrying HTML (a captive portal, an error page) must fail as a step error like any
    # other fetch fault, not escape as a bare JSONDecodeError.
    calls: list[tuple[urllib.request.Request, float]] = []
    _stub_urlopen(monkeypatch, calls, b"<html>not json</html>")
    reader = _reader(Mailbox(url="https://inbox.test/api"))
    with pytest.raises(base.SelectorError, match="mailbox fetch failed"):
        reader.fetch(5.0)

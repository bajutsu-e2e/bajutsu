"""Tests for utility steps: http, clearKeychain, clearClipboard (BE-0036)."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

from conftest import el

from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Scenario, Step


class FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


def _scenario(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate(data)


# --- schema ---


def test_http_step_parses() -> None:
    step = Step.model_validate(
        {"http": {"url": "https://api.example.com/items", "method": "POST", "status": 201}}
    )
    assert step.http is not None
    assert step.http.method == "POST"
    assert step.http.status == 201


def test_http_step_defaults() -> None:
    step = Step.model_validate({"http": {"url": "https://example.com"}})
    assert step.http is not None
    assert step.http.method == "GET"
    assert step.http.status is None
    assert step.http.save_body is None


def test_clear_keychain_step_parses() -> None:
    step = Step.model_validate({"clearKeychain": {}})
    assert step.clear_keychain is not None


def test_clear_clipboard_step_parses() -> None:
    step = Step.model_validate({"clearClipboard": {}})
    assert step.clear_clipboard is not None


# --- http runtime ---


@contextmanager
def _serving(handler: type[BaseHTTPRequestHandler]) -> Iterator[int]:
    """Run *handler* on a loopback HTTP server, yielding its port. Polls often so teardown's
    `shutdown()` returns promptly instead of blocking on the 0.5s default poll tick."""
    server = HTTPServer(("127.0.0.1", 0), handler)
    Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
    try:
        yield server.server_address[1]
    finally:
        server.shutdown()
        server.server_close()


def test_http_step_succeeds_with_matching_status() -> None:
    handler_calls: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            handler_calls.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'{"ok":true}')

        def log_message(self, *args: object) -> None:
            pass

    with _serving(Handler) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "http ok",
                    "steps": [{"http": {"url": f"http://127.0.0.1:{port}/test", "status": 200}}],
                }
            ),
            clock=FakeClock(),
        )
        assert result.ok, result.failure
        assert handler_calls == ["/test"]


def test_http_step_fails_on_status_mismatch() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(404)
            self.end_headers()

        def log_message(self, *args: object) -> None:
            pass

    with _serving(Handler) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "http fail",
                    "steps": [{"http": {"url": f"http://127.0.0.1:{port}/x", "status": 200}}],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "status" in (result.failure or "").lower()


def test_http_step_saves_body_to_vars() -> None:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"response-data-123")

        def log_message(self, *args: object) -> None:
            pass

    with _serving(Handler) as port:
        driver = FakeDriver([el("x", "X", value="response-data-123")])
        result = run_scenario(
            driver,
            _scenario(
                {
                    "name": "http save",
                    "steps": [
                        {"http": {"url": f"http://127.0.0.1:{port}/data", "saveBody": "resp"}},
                        {"assert": [{"value": {"sel": {"id": "x"}, "equals": "${vars.resp}"}}]},
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert result.ok, result.failure


def test_http_step_rejects_non_http_scheme() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario({"name": "http file", "steps": [{"http": {"url": "file:///etc/passwd"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "http/https" in (result.failure or "").lower()


def test_http_step_handles_connection_error() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario({"name": "http err", "steps": [{"http": {"url": "http://127.0.0.1:1/nope"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "request failed" in (result.failure or "").lower()


# --- clearKeychain / clearClipboard runtime ---


def test_clear_keychain_requires_device_control() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario({"name": "ck", "steps": [{"clearKeychain": {}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "clearKeychain" in (result.failure or "")


def test_clear_clipboard_requires_device_control() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario({"name": "cc", "steps": [{"clearClipboard": {}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "clearClipboard" in (result.failure or "")


# --- env command builders ---


def test_keychain_reset_cmd() -> None:
    from bajutsu.simctl import keychain_reset_cmd

    assert keychain_reset_cmd("U") == ["xcrun", "simctl", "keychain", "U", "reset"]


def test_pbcopy_cmd() -> None:
    from bajutsu.simctl import pbcopy_cmd

    assert pbcopy_cmd("U") == ["xcrun", "simctl", "pbcopy", "U"]

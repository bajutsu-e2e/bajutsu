"""Tests for utility steps: http, clearKeychain, clearClipboard (BE-0036)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from conftest import el

from bajutsu.common.cancellation import not_cancelled
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.actions.handlers import http as http_handler
from bajutsu.common.orchestrator.loop._functions import _do_clocked_step
from bajutsu.common.scenario import HttpRequest, Scenario, Step


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


def test_extract_body_is_a_load_error_that_names_the_replacement() -> None:
    http = {"url": "https://x", "extractBody": [{"var": "a", "path": "p"}]}
    with pytest.raises(ValueError, match=r"(?s)extractBody was removed.*saveBody.*parseJson"):
        _scenario({"name": "old", "steps": [{"http": http}]})


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


# --- http status wait (BE-0515) ---


@pytest.mark.parametrize("method", ["GET", "HEAD"])
def test_http_timeout_accepts_repeatable_methods(method: str) -> None:
    step = Step.model_validate(
        {"http": {"url": "https://example.com", "method": method, "status": 200, "timeout": 30}}
    )
    assert step.http is not None
    assert step.http.timeout == 30


@pytest.mark.parametrize(
    ("fields", "message"),
    [
        ({"timeout": 5}, "timeout requires status"),
        ({"timeout": 5, "status": 200, "method": "POST"}, "requires method GET or HEAD"),
        ({"timeout": 5, "status": 200, "method": "${vars.m}"}, "requires method GET or HEAD"),
        ({"timeout": 5, "status": 200, "method": "get"}, "requires method GET or HEAD"),
        ({"timeout": 0, "status": 200}, "greater than 0"),
        ({"timeout": float("inf"), "status": 200}, "finite number"),
    ],
)
def test_http_timeout_load_errors(fields: dict[str, object], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        Step.model_validate({"http": {"url": "https://example.com", **fields}})


def _status_sequence_handler(statuses: list[int], calls: list[int]) -> type[BaseHTTPRequestHandler]:
    """Answer each GET with the next status (the last one repeats), recording each in *calls*."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            status = statuses[min(len(calls), len(statuses) - 1)]
            calls.append(status)
            self.send_response(status)
            self.end_headers()
            self.wfile.write(f"body-{status}".encode())

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def _wait_scenario(port: int, **fields: object) -> Scenario:
    http = {"url": f"http://127.0.0.1:{port}/code", "status": 200, "timeout": 3, **fields}
    return _scenario({"name": "http wait", "steps": [{"http": http}]})


def test_http_step_waits_for_status_and_saves_only_the_successful_body() -> None:
    calls: list[int] = []
    clock = FakeClock()
    with _serving(_status_sequence_handler([503, 404, 200], calls)) as port:
        scenario = _wait_scenario(port, saveBody="resp")
        scenario.steps.append(
            Step.model_validate(
                {"assert": [{"value": {"sel": {"id": "x"}, "equals": "${vars.resp}"}}]}
            )
        )
        result = run_scenario(FakeDriver([el("x", "X", value="body-200")]), scenario, clock=clock)
    assert result.ok, result.failure
    assert calls == [503, 404, 200]
    assert clock.now() == 2.0


def test_http_step_wait_fails_at_the_deadline_with_the_last_status() -> None:
    calls: list[int] = []
    with _serving(_status_sequence_handler([503], calls)) as port:
        result = run_scenario(FakeDriver([el("x", "X")]), _wait_scenario(port), clock=FakeClock())
    assert not result.ok
    assert "no status 200 within 3s; last status was 503" in (result.failure or "")
    assert calls == [503, 503, 503]


def test_http_step_wait_leaves_on_cancellation() -> None:
    calls: list[int] = []
    with _serving(_status_sequence_handler([503], calls)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _wait_scenario(port, timeout=60),
            clock=FakeClock(),
            cancelled=lambda: bool(calls),
        )
    assert result.failure == "cancelled"
    assert calls == [503]


def test_http_step_wait_fails_at_once_on_a_connection_error() -> None:
    clock = FakeClock()
    result = run_scenario(FakeDriver([el("x", "X")]), _wait_scenario(1), clock=clock)
    assert not result.ok
    assert "request failed" in (result.failure or "")
    assert clock.now() == 0.0


def test_http_wait_bounds_each_request_and_reports_a_cut_off_one_as_the_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A slow server: the first answer takes most of the budget, the second outlasts what is left.
    clock = FakeClock()
    timeouts: list[float] = []

    def send(_request: object, timeout: float) -> tuple[int, str]:
        timeouts.append(timeout)
        if len(timeouts) == 1:
            clock.sleep(20)
            return 503, ""
        clock.sleep(timeout)
        raise base.SelectorError("http: request failed: timed out")

    monkeypatch.setattr(http_handler, "_send", send)
    http = HttpRequest.model_validate({"url": "https://example.com", "status": 200, "timeout": 45})
    with pytest.raises(
        base.SelectorError, match=r"within 45s; last status was 503; last attempt: .*timed out"
    ):
        http_handler._do_http(http, {}, clock)
    assert timeouts == [30.0, 24.0]


def test_http_wait_retries_a_request_that_reaches_the_per_request_ceiling(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    clock = FakeClock()
    timeouts: list[float] = []

    def send(_request: object, timeout: float) -> tuple[int, str]:
        timeouts.append(timeout)
        if len(timeouts) == 1:
            clock.sleep(timeout)
            raise base.SelectorError("http: request failed: timed out")
        return 200, "ready"

    monkeypatch.setattr(http_handler, "_send", send)
    http = HttpRequest.model_validate(
        {"url": "https://example.com", "status": 200, "timeout": 120, "saveBody": "resp"}
    )
    bindings: dict[str, str] = {}
    http_handler._do_http(http, bindings, clock)
    assert timeouts == [30.0, 30.0]
    assert bindings == {"vars.resp": "ready"}


def test_http_step_wait_clears_the_live_selection_like_a_one_shot_http_step() -> None:
    # The wait bypasses `_do_action`, which is where every other step drops a `select`'s selection.
    calls: list[int] = []
    with _serving(_status_sequence_handler([200], calls)) as port:
        scenario = _wait_scenario(port)
        scenario.steps.insert(0, Step.model_validate({"select": {"into": {"id": "x"}}}))
        scenario.steps.append(Step.model_validate({"copy": {}}))
        result = run_scenario(FakeDriver([el("x", "X")]), scenario, clock=FakeClock())
    assert calls == [200]
    assert "copy requires an active selection" in (result.failure or "")


def test_http_wait_runs_without_a_selection_state() -> None:
    calls: list[int] = []
    bindings: dict[str, str] = {}
    with _serving(_status_sequence_handler([200], calls)) as port:
        step = _wait_scenario(port, saveBody="resp").steps[0]
        _do_clocked_step(step, FakeClock(), bindings, not_cancelled, None)
    assert bindings == {"vars.resp": "body-200"}


def test_http_step_reports_a_dropped_connection_as_a_step_failure() -> None:
    # urllib wraps only a failure to send in `URLError`; a server that hangs up before answering
    # raises `RemoteDisconnected` straight through, which must still fail the step cleanly.
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.close_connection = True

        def log_message(self, *args: object) -> None:
            pass

    with _serving(Handler) as port:
        result = run_scenario(FakeDriver([el("x", "X")]), _wait_scenario(port), clock=FakeClock())
    assert not result.ok
    assert "request failed" in (result.failure or "")


# --- saveBody read back by parseJson (BE-0516) ---


def _json_handler(body: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def test_a_saved_response_body_is_read_by_a_following_parse_json_step() -> None:
    body = json.dumps({"data": {"token": "abc123"}}).encode()
    with _serving(_json_handler(body)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X", value="abc123")]),
            _scenario(
                {
                    "name": "login",
                    "steps": [
                        {"http": {"url": f"http://127.0.0.1:{port}/data", "saveBody": "login"}},
                        {
                            "parseJson": {
                                "from": "${vars.login}",
                                "extract": [{"var": "token", "path": "data.token"}],
                            }
                        },
                        {"assert": [{"value": {"sel": {"id": "x"}, "equals": "${vars.token}"}}]},
                    ],
                }
            ),
            clock=FakeClock(),
        )
    assert result.ok, result.failure


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
    from bajutsu.common.backend_cli.simctl import keychain_reset_cmd

    assert keychain_reset_cmd("U") == ["xcrun", "simctl", "keychain", "U", "reset"]


def test_pbcopy_cmd() -> None:
    from bajutsu.common.backend_cli.simctl import pbcopy_cmd

    assert pbcopy_cmd("U") == ["xcrun", "simctl", "pbcopy", "U"]

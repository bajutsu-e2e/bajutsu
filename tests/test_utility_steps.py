"""Tests for utility steps: http, clearKeychain, clearClipboard (BE-0036)."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread

import pytest
from conftest import el

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.actions.handlers.http import (
    _extract_body_fields,
    _parse_path,
    _substitute_path,
    _substitute_var,
)
from bajutsu.common.scenario import Scenario, Step


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
    assert step.http.extract_body is None


def test_http_step_parses_extract_body() -> None:
    step = Step.model_validate(
        {
            "http": {
                "url": "https://example.com",
                "extractBody": [
                    {"var": "token", "path": "data.token"},
                    {"var": "userId", "path": "data.user.id"},
                ],
            }
        }
    )
    assert step.http is not None
    assert step.http.extract_body is not None
    assert [f.var for f in step.http.extract_body] == ["token", "userId"]
    assert [f.path for f in step.http.extract_body] == ["data.token", "data.user.id"]


def test_http_step_rejects_duplicate_extract_body_var() -> None:
    with pytest.raises(ValueError, match="var"):
        _scenario(
            {
                "name": "dup",
                "steps": [
                    {
                        "http": {
                            "url": "https://example.com",
                            "extractBody": [
                                {"var": "a", "path": "p1"},
                                {"var": "a", "path": "p2"},
                            ],
                        }
                    }
                ],
            }
        )


def test_http_step_rejects_extract_body_var_colliding_with_save_body() -> None:
    with pytest.raises(ValueError, match="var"):
        _scenario(
            {
                "name": "dup2",
                "steps": [
                    {
                        "http": {
                            "url": "https://example.com",
                            "saveBody": "a",
                            "extractBody": [{"var": "a", "path": "p1"}],
                        }
                    }
                ],
            }
        )


def test_http_step_rejects_duplicate_extract_body_var_nested_in_if() -> None:
    # The duplicate-var walker recurses into every nested step container (if/forEach/web/app/group,
    # plus before/after/interrupts) — this pins one non-top-level case down so a regression in the
    # walker's branches doesn't go uncaught until an offending step actually runs.
    with pytest.raises(ValueError, match="var"):
        _scenario(
            {
                "name": "dup nested",
                "steps": [
                    {
                        "if": {
                            "condition": {"exists": {"id": "x"}},
                            "then": [
                                {
                                    "http": {
                                        "url": "https://example.com",
                                        "extractBody": [
                                            {"var": "a", "path": "p1"},
                                            {"var": "a", "path": "p2"},
                                        ],
                                    }
                                }
                            ],
                        }
                    }
                ],
            }
        )


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


# --- extractBody runtime (BE-0440) ---


def _json_handler(body: bytes) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def _run_extract(
    port: int, extract_body: list[dict[str, str]], expected_value: str
) -> tuple[bool, str | None]:
    """Run a scenario extracting one field and asserting it landed in `vars.<var>` as expected."""
    var = extract_body[0]["var"]
    driver = FakeDriver([el("x", "X", value=expected_value)])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "extract",
                "steps": [
                    {"http": {"url": f"http://127.0.0.1:{port}/data", "extractBody": extract_body}},
                    {"assert": [{"value": {"sel": {"id": "x"}, "equals": f"${{vars.{var}}}"}}]},
                ],
            }
        ),
        clock=FakeClock(),
    )
    return result.ok, result.failure


def test_http_step_extracts_nested_field() -> None:
    body = json.dumps({"data": {"token": "abc123", "user": {"id": 42}}}).encode()
    with _serving(_json_handler(body)) as port:
        ok, failure = _run_extract(port, [{"var": "token", "path": "data.token"}], "abc123")
        assert ok, failure


def test_http_step_extracts_leading_array_index_and_chained_index() -> None:
    body = json.dumps({"rows": [[10, 20], [30, 40]]}).encode()
    with _serving(_json_handler(body)) as port:
        ok, failure = _run_extract(port, [{"var": "cell", "path": "rows[0][1]"}], "20")
        assert ok, failure

    array_body = json.dumps([{"id": 7}, {"id": 8}]).encode()
    with _serving(_json_handler(array_body)) as port:
        ok, failure = _run_extract(port, [{"var": "id0", "path": "[0].id"}], "7")
        assert ok, failure


def test_http_step_saves_body_and_extracts_field_independently() -> None:
    # Both fields read the same response body from one request: saveBody gets the whole text,
    # extractBody gets the named field, and neither suppresses or overrides the other.
    body = json.dumps({"data": {"token": "abc123"}}).encode()
    with _serving(_json_handler(body)) as port:
        driver = FakeDriver([el("x", "X", value=body.decode()), el("y", "Y", value="abc123")])
        result = run_scenario(
            driver,
            _scenario(
                {
                    "name": "both",
                    "steps": [
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "saveBody": "whole",
                                "extractBody": [{"var": "token", "path": "data.token"}],
                            }
                        },
                        {"assert": [{"value": {"sel": {"id": "x"}, "equals": "${vars.whole}"}}]},
                        {"assert": [{"value": {"sel": {"id": "y"}, "equals": "${vars.token}"}}]},
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert result.ok, result.failure


def test_http_step_extracts_field_using_secrets_substitution() -> None:
    body = json.dumps({"data": {"token": "abc123"}}).encode()
    with _serving(_json_handler(body)) as port:
        driver = FakeDriver([el("x", "X", value="abc123")])
        result = run_scenario(
            driver,
            _scenario(
                {
                    "name": "secrets extract",
                    "steps": [
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [
                                    {"var": "${secrets.name}", "path": "data.${secrets.field}"}
                                ],
                            }
                        },
                        {"assert": [{"value": {"sel": {"id": "x"}, "equals": "${vars.token}"}}]},
                    ],
                }
            ),
            clock=FakeClock(),
            bindings={"secrets.name": "token", "secrets.field": "token"},
        )
        assert result.ok, result.failure


def test_http_step_extracts_non_string_value_as_json_text() -> None:
    body = json.dumps(
        {"data": {"n": 42, "b": True, "nil": None, "obj": {"id": 1}, "arr": [1, 2]}}
    ).encode()
    for path, expected in [
        ("data.n", "42"),
        ("data.b", "true"),
        ("data.nil", "null"),
        ("data.obj", '{"id":1}'),
        ("data.arr", "[1,2]"),
    ]:
        with _serving(_json_handler(body)) as port:
            ok, failure = _run_extract(port, [{"var": "v", "path": path}], expected)
            assert ok, failure


def test_http_step_extract_body_fails_on_missing_key() -> None:
    body = json.dumps({"data": {}}).encode()
    with _serving(_json_handler(body)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "extract missing",
                    "steps": [
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [{"var": "v", "path": "data.missing"}],
                            }
                        }
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "missing" in (result.failure or "").lower()


def test_http_step_extract_body_fails_on_out_of_range_index() -> None:
    body = json.dumps({"items": [1, 2]}).encode()
    with _serving(_json_handler(body)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "extract oor",
                    "steps": [
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [{"var": "v", "path": "items[5]"}],
                            }
                        }
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "out of range" in (result.failure or "").lower()


def test_extract_body_fields_is_all_or_nothing_across_entries() -> None:
    # BE-0440: the first entry resolves cleanly and the second fails, but neither var may land in
    # `bindings` — a cleanup (`after`/`interrupts`) step sharing this same dict would otherwise see
    # a passing entry's var as set with no signal that a sibling entry in the same list failed.
    step = Step.model_validate(
        {
            "http": {
                "url": "https://example.com",
                "extractBody": [
                    {"var": "a", "path": "data.a"},
                    {"var": "b", "path": "data.missing"},
                ],
            }
        }
    )
    assert step.http is not None
    bindings: dict[str, str] = {}
    with pytest.raises(base.SelectorError, match="missing key"):
        _extract_body_fields(step.http, json.dumps({"data": {"a": "ok"}}), bindings)
    assert bindings == {}


def test_http_step_extract_body_fails_on_key_applied_to_non_object() -> None:
    body = json.dumps({"data": "a string"}).encode()
    with _serving(_json_handler(body)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "extract non-obj",
                    "steps": [
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [{"var": "v", "path": "data.id"}],
                            }
                        }
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "non-object" in (result.failure or "").lower()


def test_http_step_extract_body_fails_on_index_applied_to_non_array() -> None:
    body = json.dumps({"items": "a string"}).encode()
    with _serving(_json_handler(body)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "extract non-arr",
                    "steps": [
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [{"var": "v", "path": "items[0]"}],
                            }
                        }
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "non-array" in (result.failure or "").lower()


def test_http_step_extract_body_fails_on_malformed_negative_index() -> None:
    body = json.dumps({"items": [1, 2]}).encode()
    with _serving(_json_handler(body)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "extract neg",
                    "steps": [
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [{"var": "v", "path": "items[-1]"}],
                            }
                        }
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "malformed" in (result.failure or "").lower()


def test_http_step_extract_body_fails_on_non_json_body() -> None:
    with _serving(_json_handler(b"not json")) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "extract bad json",
                    "steps": [
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [{"var": "v", "path": "a"}],
                            }
                        }
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "json" in (result.failure or "").lower()


def _routed_handler(routes: dict[str, bytes]) -> type[BaseHTTPRequestHandler]:
    """Serve a fixed response body per path — for a scenario that seeds `vars.*` via one request
    (`saveBody`) before a second reads it back into an `extractBody` `path`/`var`."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            body = routes.get(self.path)
            if body is None:
                self.send_response(404)
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args: object) -> None:
            pass

    return Handler


def test_http_step_extract_body_fails_on_var_collision_only_substitution_reveals() -> None:
    # Two distinct `${...}` tokens — so the load-time check (comparing raw `var` text) sees no
    # duplicate — that happen to substitute to the same name, revealing the collision only at run
    # time (BE-0440).
    routes = {
        "/name1": b"same",
        "/name2": b"same",
        "/data": json.dumps({"a": 1, "b": 2}).encode(),
    }
    with _serving(_routed_handler(routes)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "extract collide",
                    "steps": [
                        {"http": {"url": f"http://127.0.0.1:{port}/name1", "saveBody": "name1"}},
                        {"http": {"url": f"http://127.0.0.1:{port}/name2", "saveBody": "name2"}},
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [
                                    {"var": "${vars.name1}", "path": "a"},
                                    {"var": "${vars.name2}", "path": "b"},
                                ],
                            }
                        },
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "same" in (result.failure or "")


def test_http_step_extract_body_fails_on_substituted_value_carrying_path_structure() -> None:
    routes = {
        "/idx": b"0].other[0",
        "/data": json.dumps({"items": [{"id": 1}]}).encode(),
    }
    with _serving(_routed_handler(routes)) as port:
        result = run_scenario(
            FakeDriver([el("x", "X")]),
            _scenario(
                {
                    "name": "extract injection",
                    "steps": [
                        {"http": {"url": f"http://127.0.0.1:{port}/idx", "saveBody": "i"}},
                        {
                            "http": {
                                "url": f"http://127.0.0.1:{port}/data",
                                "extractBody": [{"var": "v", "path": "items[${vars.i}].id"}],
                            }
                        },
                    ],
                }
            ),
            clock=FakeClock(),
        )
        assert not result.ok
        assert "'.', '[', or ']'" in (result.failure or "")


# --- extractBody path helpers (unit-level, for the malformed-path cases above) ---


def test_substitute_path_substitutes_a_valid_segment() -> None:
    assert _substitute_path("items[${vars.i}].id", "v", {"vars.i": "1"}) == "items[1].id"


def test_substitute_path_fails_on_an_undeclared_token() -> None:
    # This is the run's terminal substitution layer, so a token with no matching binding is
    # always an author error (a typo'd or not-yet-set variable) — never one left for a later
    # layer, unlike the ordinary whole-step splice's ${...} leave-as-literal convention.
    with pytest.raises(base.SelectorError, match="did not resolve"):
        _substitute_path("items[${vars.i}].id", "v", {})


def test_substitute_path_fails_on_an_empty_substituted_value() -> None:
    # An empty value would otherwise vanish from the path entirely — "${vars.k}[0]" with
    # vars.k == "" collapsing to the syntactically valid "[0]" and silently reading the root
    # array instead of failing — rather than supplying the one segment the grammar requires.
    with pytest.raises(base.SelectorError, match="non-empty path segment"):
        _substitute_path("${vars.k}[0]", "v", {"vars.k": ""})


def test_substitute_var_substitutes_a_declared_token() -> None:
    assert _substitute_var("${vars.name}", {"vars.name": "token"}) == "token"


def test_substitute_var_fails_on_an_undeclared_token() -> None:
    # A `var` names the binding this step must populate; leaving an unresolved token as the
    # literal string (the ordinary whole-step splice's convention) would silently write under
    # that literal text instead of the name the author meant, with the step still reporting
    # success (BE-0440).
    with pytest.raises(base.SelectorError, match="did not fully resolve"):
        _substitute_var("${vars.missing}", {})


def test_substitute_var_fails_on_an_empty_substituted_name() -> None:
    # An empty name would still resolve cleanly and pass the duplicate-var check, but no later
    # `${vars.*}` reference could ever read it back — the same silent-dead-write finding as an
    # unresolved token, just via an empty value instead of a missing binding.
    with pytest.raises(base.SelectorError, match="empty name"):
        _substitute_var("${vars.k}", {"vars.k": ""})


def test_parse_path_fails_on_empty_path() -> None:
    with pytest.raises(base.SelectorError, match="malformed"):
        _parse_path("", "v")


def test_parse_path_fails_on_unterminated_bracket() -> None:
    with pytest.raises(base.SelectorError, match="unterminated"):
        _parse_path("items[0", "v")


def test_parse_path_fails_on_empty_key_segment() -> None:
    with pytest.raises(base.SelectorError, match="empty key segment"):
        _parse_path("a..b", "v")
    with pytest.raises(base.SelectorError, match="empty key segment"):
        _parse_path("data.", "v")
    with pytest.raises(base.SelectorError, match="empty key segment"):
        _parse_path(".data", "v")


def test_parse_path_fails_on_unexpected_character() -> None:
    with pytest.raises(base.SelectorError, match="unexpected"):
        _parse_path("a]b", "v")


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

"""Tests for network observation: the exchange model, the collector, and the
`request` assertion (UI is untouched; these evaluate against exchanges)."""

from __future__ import annotations

import json
import urllib.request

from bajutsu.assertions import evaluate_one
from bajutsu.drivers.fake import FakeDriver
from bajutsu.network import NetworkCollector, NetworkExchange
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Assertion, RequestMatch, load_scenarios


def _ex(method: str = "GET", path: str = "/items", status: int = 200, **kw: object) -> NetworkExchange:
    return NetworkExchange(method=method, path=path, status=status, **kw)


def _req(**kw: object) -> Assertion:
    return Assertion(request=RequestMatch(**kw))


def test_exchange_parses_json_aliases() -> None:
    ex = NetworkExchange.model_validate(
        {"method": "POST", "path": "/login", "status": 200, "durationMs": 12.3,
         "requestHeaders": {"Authorization": "secret"}, "extraIgnored": 1}
    )
    assert ex.method == "POST" and ex.status == 200 and ex.duration_ms == 12.3
    assert ex.request_headers["Authorization"] == "secret"


def test_request_matches_method_path_status() -> None:
    exs = [_ex("POST", "/login", 200), _ex("GET", "/items", 200)]
    assert evaluate_one([], _req(method="post", path="/login", status=200), exs).ok  # case-insensitive
    assert not evaluate_one([], _req(method="POST", path="/login", status=500), exs).ok


def test_request_pathmatches_and_count() -> None:
    exs = [_ex("GET", "/items/1"), _ex("GET", "/items/2")]
    assert evaluate_one([], _req(path_matches="^/items/"), exs).ok            # >= 1 by default
    assert evaluate_one([], _req(path_matches="^/items/", count=2), exs).ok   # exact count
    assert not evaluate_one([], _req(path_matches="^/items/", count=1), exs).ok


def test_request_no_match_fails_with_reason() -> None:
    r = evaluate_one([], _req(method="DELETE"), [_ex("GET", "/x")])
    assert not r.ok and "通信" in r.reason


def test_collector_receives_and_clears() -> None:
    c = NetworkCollector()
    port = c.start()
    try:
        body = json.dumps({"method": "GET", "path": "/items", "status": 200}).encode()
        urllib.request.urlopen(  # noqa: S310 — localhost test server
            urllib.request.Request(f"http://127.0.0.1:{port}/report", data=body, method="POST")
        ).read()
        snap = c.snapshot()
        assert len(snap) == 1 and snap[0].path == "/items"
        c.clear()
        assert c.snapshot() == []
    finally:
        c.stop()


def test_orchestrator_request_assertion_step() -> None:
    scn = load_scenarios(
        "- name: net\n"
        "  steps:\n"
        "    - assert: [ { request: { method: POST, path: /login, status: 200 } } ]\n"
    )[0]
    ok = run_scenario(FakeDriver(), scn, network=lambda: [_ex("POST", "/login", 200)])
    assert ok.ok, ok.failure
    miss = run_scenario(FakeDriver(), scn, network=list)  # no exchanges
    assert not miss.ok and "通信" in (miss.failure or "")

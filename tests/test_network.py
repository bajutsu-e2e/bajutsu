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


def test_request_matches_url_endpoint() -> None:
    exs = [_ex("GET", "/items", 200, url="https://api.example.com/items")]
    assert evaluate_one([], _req(url="https://api.example.com/items"), exs).ok   # exact endpoint
    assert evaluate_one([], _req(url_matches="example.com"), exs).ok             # substring/regex
    assert not evaluate_one([], _req(url="https://other.com/items"), exs).ok


def test_request_matches_body() -> None:
    exs = [_ex("POST", "/post", 200, request_body='{"name":"bajutsu","n":42}')]
    assert evaluate_one([], _req(method="POST", body_matches='"name":"bajutsu"'), exs).ok
    assert not evaluate_one([], _req(method="POST", body_matches="other"), exs).ok
    # An exchange with no request body never matches a bodyMatches criterion.
    assert not evaluate_one([], _req(body_matches="x"), [_ex("GET", "/x", 200)]).ok


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


def test_collector_snapshot_timed_records_receive_order() -> None:
    times = iter([1.0, 2.5])
    c = NetworkCollector(now=lambda: next(times))
    c.add({"method": "GET", "path": "/a", "status": 200})
    c.add({"method": "POST", "path": "/b", "status": 201})
    timed = c.snapshot_timed()
    assert [t for _, t in timed] == [1.0, 2.5]            # receive times preserved
    assert [ex.path for ex, _ in timed] == ["/a", "/b"]   # in arrival order


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


def test_wait_for_request_satisfied() -> None:
    # `wait: { until: { request } }` succeeds as soon as a matching exchange (pinned to
    # an endpoint url) is observed.
    scn = load_scenarios(
        "- name: w\n"
        "  steps:\n"
        '    - wait: { until: { request: { method: GET, url: "https://ex.com/items", status: 200 } }, timeout: 1 }\n'
    )[0]
    res = run_scenario(
        FakeDriver(), scn, network=lambda: [_ex("GET", "/items", 200, url="https://ex.com/items")]
    )
    assert res.ok, res.failure


def test_wait_for_request_times_out() -> None:
    # No matching exchange -> the wait times out and the step (scenario) fails.
    scn = load_scenarios(
        "- name: w\n"
        "  steps:\n"
        "    - wait: { until: { request: { path: /nope } }, timeout: 0 }\n"
    )[0]
    res = run_scenario(FakeDriver(), scn, network=list)  # nothing observed
    assert not res.ok and "request" in (res.failure or "")

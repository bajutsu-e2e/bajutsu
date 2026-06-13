"""Tests for network observation: the exchange model, the collector, and the
`request` assertion (UI is untouched; these evaluate against exchanges)."""

from __future__ import annotations

import json
import urllib.request

from bajutsu.assertions import evaluate, evaluate_one
from bajutsu.drivers.fake import FakeDriver
from bajutsu.network import NetworkCollector, NetworkExchange
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Assertion, RequestMatch, dump_mocks, load_scenarios


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


def test_request_assertions_map_one_to_one() -> None:
    # Two `request` assertions need two distinct exchanges: one exchange satisfies only one.
    get = Assertion(request=RequestMatch(method="GET"))
    one = evaluate([], [get, get], [_ex("GET", "/a", 200)])
    assert sum(r.ok for r in one) == 1           # only one can be satisfied
    assert "1 対 1" in next(r.reason for r in one if not r.ok)
    two = evaluate([], [get, get], [_ex("GET", "/a", 200), _ex("GET", "/b", 200)])
    assert all(r.ok for r in two)                # both satisfied by distinct exchanges


def test_request_assignment_uses_augmenting_not_greedy() -> None:
    # A broad matcher must not steal the only exchange a specific matcher needs: the
    # assignment reshuffles (broad -> the other exchange) so both pass.
    exs = [_ex("GET", "/x", 200, url="https://a.test/x"), _ex("GET", "/x", 200, url="https://b.test/x")]
    broad = Assertion(request=RequestMatch(method="GET"))                  # matches both
    specific = Assertion(request=RequestMatch(method="GET", url_matches="a\\.test"))  # only the first
    assert all(r.ok for r in evaluate([], [broad, specific], exs))


def test_request_count_assertion_stays_independent() -> None:
    # A `count` request is an explicit aggregate, evaluated independently of the 1:1 rule.
    exs = [_ex("GET", "/a", 200), _ex("GET", "/b", 200)]
    res = evaluate([], [Assertion(request=RequestMatch(method="GET", count=2)),
                        Assertion(request=RequestMatch(method="GET"))], exs)
    assert all(r.ok for r in res)


def test_request_no_match_fails_with_reason() -> None:
    r = evaluate_one([], _req(method="DELETE"), [_ex("GET", "/x")])
    assert not r.ok and "通信" in r.reason


def test_mocks_parse_and_serialize() -> None:
    scn = load_scenarios(
        "- name: m\n"
        "  mocks:\n"
        "    - match: { method: GET, urlMatches: example.com }\n"
        "      respond: { status: 418, headers: { Content-Type: text/plain }, body: hi, delayMs: 50 }\n"
        "    - match: { method: POST, pathMatches: /post$ }\n"          # respond defaults to 200/empty
        "  steps: [ { tap: { id: x } } ]\n"
    )[0]
    assert len(scn.mocks) == 2
    first = scn.mocks[0]
    assert first.match.method == "GET" and first.respond.status == 418 and first.respond.body == "hi"
    assert scn.mocks[1].respond.status == 200  # default response
    # Serialized for BAJUTSU_MOCKS with alias keys, unset fields omitted.
    payload = json.loads(dump_mocks(scn.mocks))
    assert payload[0]["match"]["urlMatches"] == "example.com"
    assert payload[0]["respond"] == {"status": 418, "headers": {"Content-Type": "text/plain"},
                                     "body": "hi", "delayMs": 50.0}


def test_exchange_carries_mocked_flag() -> None:
    assert NetworkExchange.model_validate({"method": "GET", "mocked": True}).mocked is True
    assert NetworkExchange(method="GET").mocked is False  # default: a real call


def test_collector_receives_and_clears() -> None:
    c = NetworkCollector()
    port = c.start()
    try:
        body = json.dumps({"method": "GET", "path": "/items", "status": 200}).encode()
        urllib.request.urlopen(
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

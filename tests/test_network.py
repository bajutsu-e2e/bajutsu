"""Tests for network observation: the exchange model, the collector, and the
`request` assertion (UI is untouched; these evaluate against exchanges)."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

from bajutsu.assertions import EvalContext, evaluate, evaluate_one
from bajutsu.drivers.fake import FakeDriver
from bajutsu.evidence.network import NetworkCollector, NetworkExchange, ScreenTransition
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import (
    Assertion,
    CountOp,
    EventMatch,
    RequestMatch,
    ResponseSchemaMatch,
    dump_mocks,
    load_scenarios,
)


def _ex(
    method: str = "GET", path: str = "/items", status: int = 200, **kw: object
) -> NetworkExchange:
    return NetworkExchange(method=method, path=path, status=status, **kw)


def _req(**kw: object) -> Assertion:
    return Assertion(request=RequestMatch(**kw))


def test_exchange_parses_json_aliases() -> None:
    ex = NetworkExchange.model_validate(
        {
            "method": "POST",
            "path": "/login",
            "status": 200,
            "durationMs": 12.3,
            "requestHeaders": {"Authorization": "secret"},
            "extraIgnored": 1,
        }
    )
    assert ex.method == "POST" and ex.status == 200 and ex.duration_ms == 12.3
    assert ex.request_headers["Authorization"] == "secret"


def test_android_interceptor_json_shape_parses() -> None:
    # BE-0283: the wire contract BajutsuNet.kt's OkHttp interceptor must emit. The fast gate can't run
    # Kotlin, so this pins the exact JSON shape from the Python side — a change to NetworkExchange that
    # broke the Android reporter would fail here. The on-device e2e is the runtime proof.
    payload = {
        "method": "GET",
        "url": "http://10.0.2.2:8000/horses",
        "path": "/horses",
        "status": 200,
        "durationMs": 8.0,
        "requestHeaders": {"Authorization": "Bearer demo-secret-abc123"},
        "responseHeaders": {"Content-Type": "application/json"},
        "responseBody": "[]",
    }
    ex = NetworkExchange.model_validate(json.loads(json.dumps(payload)))
    assert ex.method == "GET" and ex.path == "/horses" and ex.status == 200
    assert ex.url == "http://10.0.2.2:8000/horses" and ex.duration_ms == 8.0
    assert ex.request_headers["Authorization"] == "Bearer demo-secret-abc123"
    assert ex.response_headers["Content-Type"] == "application/json"
    assert ex.response_body == "[]" and ex.mocked is False
    # A `request` assertion the showcase e2e uses then matches it (path + method).
    assert evaluate_one([], _req(method="GET", path_matches="/horses$"), [ex]).ok


def test_request_matches_method_path_status() -> None:
    exs = [_ex("POST", "/login", 200), _ex("GET", "/items", 200)]
    assert evaluate_one(
        [], _req(method="post", path="/login", status=200), exs
    ).ok  # case-insensitive
    assert not evaluate_one([], _req(method="POST", path="/login", status=500), exs).ok


def test_request_pathmatches_and_count() -> None:
    exs = [_ex("GET", "/items/1"), _ex("GET", "/items/2")]
    assert evaluate_one([], _req(path_matches="^/items/"), exs).ok  # >= 1 by default
    assert evaluate_one([], _req(path_matches="^/items/", count=2), exs).ok  # exact count
    assert not evaluate_one([], _req(path_matches="^/items/", count=1), exs).ok


def test_request_matches_url_endpoint() -> None:
    exs = [_ex("GET", "/items", 200, url="https://api.example.com/items")]
    assert evaluate_one([], _req(url="https://api.example.com/items"), exs).ok  # exact endpoint
    assert evaluate_one([], _req(url_matches="example.com"), exs).ok  # substring/regex
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
    assert sum(r.ok for r in one) == 1  # only one can be satisfied
    assert "one-to-one" in next(r.reason for r in one if not r.ok)
    two = evaluate([], [get, get], [_ex("GET", "/a", 200), _ex("GET", "/b", 200)])
    assert all(r.ok for r in two)  # both satisfied by distinct exchanges


def test_request_assignment_uses_augmenting_not_greedy() -> None:
    # A broad matcher must not steal the only exchange a specific matcher needs: the
    # assignment reshuffles (broad -> the other exchange) so both pass.
    exs = [
        _ex("GET", "/x", 200, url="https://a.test/x"),
        _ex("GET", "/x", 200, url="https://b.test/x"),
    ]
    broad = Assertion(request=RequestMatch(method="GET"))  # matches both
    specific = Assertion(
        request=RequestMatch(method="GET", url_matches="a\\.test")
    )  # only the first
    assert all(r.ok for r in evaluate([], [broad, specific], exs))


def test_request_count_assertion_stays_independent() -> None:
    # A `count` request is an explicit aggregate, evaluated independently of the 1:1 rule.
    exs = [_ex("GET", "/a", 200), _ex("GET", "/b", 200)]
    res = evaluate(
        [],
        [
            Assertion(request=RequestMatch(method="GET", count=2)),
            Assertion(request=RequestMatch(method="GET")),
        ],
        exs,
    )
    assert all(r.ok for r in res)


def test_request_no_match_fails_with_reason() -> None:
    r = evaluate_one([], _req(method="DELETE"), [_ex("GET", "/x")])
    assert not r.ok and "exchange" in r.reason


def _event(**kw: object) -> Assertion:
    return Assertion(event=EventMatch(**kw))


def test_event_matches_endpoint_and_body_field() -> None:
    exs = [
        _ex(
            "POST",
            "/track",
            200,
            url="https://t.example.com/track",
            request_body='{"name":"purchase_completed","amount":300}',
        ),
        _ex("GET", "/items", 200),
    ]
    # endpoint + structured body field; numeric JSON value matches its string form
    assert evaluate_one(
        [],
        _event(
            url="https://t.example.com/track", body={"name": "purchase_completed", "amount": "300"}
        ),
        exs,
    ).ok
    # a body field that doesn't match → fail
    assert not evaluate_one(
        [], _event(url="https://t.example.com/track", body={"name": "checkout_started"}), exs
    ).ok


def test_event_count_operator() -> None:
    exs = [
        _ex("POST", "/track", request_body='{"name":"tap"}'),
        _ex("POST", "/track", request_body='{"name":"tap"}'),
    ]
    assert evaluate_one(
        [], _event(path="/track", body={"name": "tap"}, count=CountOp(equals=2)), exs
    ).ok
    assert not evaluate_one(
        [], _event(path="/track", body={"name": "tap"}, count=CountOp(equals=1)), exs
    ).ok
    assert evaluate_one(
        [], _event(path="/track", body={"name": "tap"}, count=CountOp(at_least=2)), exs
    ).ok
    assert evaluate_one(
        [], _event(path="/track", body={"name": "tap"}, count=CountOp(at_most=2)), exs
    ).ok
    assert not evaluate_one(
        [], _event(path="/track", body={"name": "tap"}, count=CountOp(at_most=1)), exs
    ).ok


def test_event_default_count_is_at_least_one() -> None:
    exs = [_ex("POST", "/track", request_body='{"name":"tap"}')]
    assert evaluate_one([], _event(path="/track", body={"name": "tap"}), exs).ok
    assert not evaluate_one([], _event(path="/track", body={"name": "other"}), exs).ok


def test_event_body_only_matches_across_exchanges() -> None:
    # No endpoint criterion: every exchange whose JSON body carries the field counts. JSON
    # booleans / null match their JSON-canonical text (`true` / `false` / `null`), not Python repr.
    exs = [
        _ex("POST", "/a", request_body='{"flag":true,"note":null}'),
        _ex("POST", "/b", request_body='{"flag":false}'),
    ]
    assert evaluate_one([], _event(body={"flag": "true", "note": "null"}), exs).ok
    assert not evaluate_one([], _event(body={"flag": "True"}), exs).ok  # not Python repr


def test_event_body_nested_value_matches_compact_json() -> None:
    # A nested array / object field matches its compact JSON form, not a Python repr.
    exs = [_ex("POST", "/track", request_body='{"items":[1,2],"meta":{"k":"v"}}')]
    assert evaluate_one([], _event(path="/track", body={"items": "[1,2]"}), exs).ok
    assert evaluate_one([], _event(path="/track", body={"meta": '{"k":"v"}'}), exs).ok
    assert not evaluate_one([], _event(path="/track", body={"items": "[1, 2]"}), exs).ok  # not repr


def test_event_non_json_body_does_not_crash() -> None:
    exs = [_ex("POST", "/track", request_body="not json"), _ex("POST", "/track", request_body=None)]
    r = evaluate_one([], _event(path="/track", body={"name": "tap"}), exs)
    assert not r.ok and r.reason


def test_event_interpolates_vars_in_body() -> None:
    from bajutsu.orchestrator import _interp_asserts

    a = _event(url="https://t.example.com/track", body={"amount": "${vars.amount}"})
    [interp] = _interp_asserts([a], {"vars.amount": "300"})
    assert interp.event is not None and interp.event.body["amount"] == "300"


def _seq(*reqs: RequestMatch) -> Assertion:
    return Assertion(request_sequence=list(reqs))


def test_request_sequence_matches_in_order() -> None:
    exs = [_ex("POST", "/auth/refresh"), _ex("GET", "/account")]
    assert evaluate_one(
        [], _seq(RequestMatch(path="/auth/refresh"), RequestMatch(path="/account")), exs
    ).ok


def test_request_sequence_allows_interleaving() -> None:
    # Unrelated exchanges between the matched ones don't break the order (subsequence).
    exs = [_ex("POST", "/auth/refresh"), _ex("GET", "/noise"), _ex("GET", "/account")]
    assert evaluate_one(
        [], _seq(RequestMatch(path="/auth/refresh"), RequestMatch(path="/account")), exs
    ).ok


def test_request_sequence_out_of_order_fails() -> None:
    exs = [_ex("GET", "/account"), _ex("POST", "/auth/refresh")]
    r = evaluate_one(
        [], _seq(RequestMatch(path="/auth/refresh"), RequestMatch(path="/account")), exs
    )
    assert not r.ok and "/account" in r.reason  # the second matcher had no later exchange


def test_request_sequence_multiplicity_needs_distinct_exchanges() -> None:
    twice = _seq(RequestMatch(path="/ping"), RequestMatch(path="/ping"))
    assert evaluate_one([], twice, [_ex("GET", "/ping"), _ex("GET", "/ping")]).ok
    assert not evaluate_one([], twice, [_ex("GET", "/ping")]).ok  # only one occurrence


def test_request_sequence_empty_timeline_fails() -> None:
    r = evaluate_one([], _seq(RequestMatch(path="/x")), [])
    assert not r.ok and r.reason


def _schemas_dir(tmp_path: object, name: str, schema: dict[str, object]):  # type: ignore[no-untyped-def]
    d = tmp_path / "schemas"  # type: ignore[operator]
    d.mkdir(exist_ok=True)
    (d / name).write_text(json.dumps(schema), encoding="utf-8")
    return d


def _rs(schema_path: str, **req: object) -> Assertion:
    return Assertion(
        response_schema=ResponseSchemaMatch(request=RequestMatch(**req), schema_path=schema_path)
    )


def test_response_schema_passes_for_conforming_body(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.assertions import SchemaContext

    d = _schemas_dir(
        tmp_path,
        "items.json",
        {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}},
    )
    exs = [_ex("GET", "/api/items", response_body='{"id":1}')]
    r = evaluate_one(
        [],
        _rs("items.json", path="/api/items"),
        exs,
        ctx=EvalContext(schema=SchemaContext(schemas_dir=d)),
    )
    assert r.ok, r.reason


def test_response_schema_fails_for_nonconforming_body(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.assertions import SchemaContext

    d = _schemas_dir(
        tmp_path,
        "items.json",
        {"type": "object", "required": ["id"], "properties": {"id": {"type": "integer"}}},
    )
    exs = [_ex("GET", "/api/items", response_body='{"id":"not-an-int"}')]
    r = evaluate_one(
        [],
        _rs("items.json", path="/api/items"),
        exs,
        ctx=EvalContext(schema=SchemaContext(schemas_dir=d)),
    )
    assert not r.ok and r.reason


def test_response_schema_no_matching_exchange(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.assertions import SchemaContext

    d = _schemas_dir(tmp_path, "x.json", {"type": "object"})
    r = evaluate_one(
        [],
        _rs("x.json", path="/api/items"),
        [_ex("GET", "/other")],
        ctx=EvalContext(schema=SchemaContext(schemas_dir=d)),
    )
    assert not r.ok and "exchange" in r.reason


def test_response_schema_missing_schema_file(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.assertions import SchemaContext

    (tmp_path / "schemas").mkdir()
    exs = [_ex("GET", "/api/items", response_body="{}")]
    r = evaluate_one(
        [],
        _rs("missing.json", path="/api/items"),
        exs,
        ctx=EvalContext(schema=SchemaContext(schemas_dir=tmp_path / "schemas")),
    )
    assert not r.ok and "schema" in r.reason.lower()


def test_response_schema_non_json_body(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.assertions import SchemaContext

    d = _schemas_dir(tmp_path, "x.json", {"type": "object"})
    exs = [_ex("GET", "/api/items", response_body="not json")]
    r = evaluate_one(
        [],
        _rs("x.json", path="/api/items"),
        exs,
        ctx=EvalContext(schema=SchemaContext(schemas_dir=d)),
    )
    assert not r.ok and r.reason


def test_response_schema_malformed_schema_fails_cleanly(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.assertions import SchemaContext

    # An unresolvable $ref must fail the assertion loudly, not crash the run.
    d = _schemas_dir(tmp_path, "bad.json", {"$ref": "#/definitions/missing"})
    exs = [_ex("GET", "/api/items", response_body="{}")]
    r = evaluate_one(
        [],
        _rs("bad.json", path="/api/items"),
        exs,
        ctx=EvalContext(schema=SchemaContext(schemas_dir=d)),
    )
    assert not r.ok and r.reason


def test_response_schema_without_context_fails() -> None:
    exs = [_ex("GET", "/api/items", response_body="{}")]
    r = evaluate_one([], _rs("x.json", path="/api/items"), exs)  # no schema_context
    assert not r.ok and r.reason


def test_response_schema_rejects_path_traversal(tmp_path) -> None:  # type: ignore[no-untyped-def]
    from bajutsu.assertions import SchemaContext

    # a `..` escape (or an absolute path) must be rejected, not read outside the schemas dir
    d = _schemas_dir(tmp_path, "ok.json", {"type": "object"})
    (tmp_path / "secret.json").write_text('{"type": "string"}', encoding="utf-8")
    exs = [_ex("GET", "/api/items", response_body="{}")]
    r = evaluate_one(
        [],
        _rs("../secret.json", path="/api/items"),
        exs,
        ctx=EvalContext(schema=SchemaContext(schemas_dir=d)),
    )
    assert not r.ok and "escapes" in r.reason


def test_mocks_parse_and_serialize() -> None:
    scn = load_scenarios(
        "- name: m\n"
        "  mocks:\n"
        "    - match: { method: GET, urlMatches: example.com }\n"
        "      respond: { status: 418, headers: { Content-Type: text/plain }, body: hi, delayMs: 50 }\n"
        "    - match: { method: POST, pathMatches: /post$ }\n"  # respond defaults to 200/empty
        "  steps: [ { tap: { id: x } } ]\n"
    )[0]
    assert len(scn.mocks) == 2
    first = scn.mocks[0]
    assert (
        first.match.method == "GET" and first.respond.status == 418 and first.respond.body == "hi"
    )
    assert scn.mocks[1].respond.status == 200  # default response
    # Serialized for BAJUTSU_MOCKS with alias keys, unset fields omitted.
    payload = json.loads(dump_mocks(scn.mocks))
    assert payload[0]["match"]["urlMatches"] == "example.com"
    assert payload[0]["respond"] == {
        "status": 418,
        "headers": {"Content-Type": "text/plain"},
        "body": "hi",
        "delayMs": 50.0,
    }


def test_exchange_carries_mocked_flag() -> None:
    assert NetworkExchange.model_validate({"method": "GET", "mocked": True}).mocked is True
    assert NetworkExchange(method="GET").mocked is False  # default: a real call


def test_collector_receives_and_clears() -> None:
    c = NetworkCollector()
    port = c.start()
    try:
        body = json.dumps({"method": "GET", "path": "/items", "status": 200}).encode()
        urllib.request.urlopen(
            urllib.request.Request(
                f"http://127.0.0.1:{port}/report",
                data=body,
                method="POST",
                headers={"Authorization": f"Bearer {c.token}"},
            )
        ).read()
        snap = c.snapshot()
        assert len(snap) == 1 and snap[0].path == "/items"
        c.clear()
        assert c.snapshot() == []
    finally:
        c.stop()


def _post_report(port: int, token: str | None) -> int:
    """POST one exchange to the collector and return the HTTP status (401 on rejection)."""
    body = json.dumps({"method": "GET", "path": "/items", "status": 200}).encode()
    headers = {"Authorization": f"Bearer {token}"} if token is not None else {}
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/report", data=body, method="POST", headers=headers
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return int(resp.status)
    except urllib.error.HTTPError as err:
        with err:  # HTTPError is itself the response object — close its socket/FD
            return int(err.code)


def test_collector_accepts_matching_token() -> None:
    c = NetworkCollector()
    port = c.start()
    try:
        assert _post_report(port, c.token) == 204
        assert len(c.snapshot()) == 1
    finally:
        c.stop()


def test_collector_rejects_unauthenticated_post() -> None:
    """A POST with no token is rejected with 401 and never stored — the run's evidence
    stream stays authenticated (BE-0115)."""
    c = NetworkCollector()
    port = c.start()
    try:
        assert _post_report(port, token=None) == 401
        assert c.snapshot() == []
    finally:
        c.stop()


def test_collector_rejects_mismatched_token() -> None:
    c = NetworkCollector()
    port = c.start()
    try:
        assert _post_report(port, token="wrong-token") == 401
        assert c.snapshot() == []
    finally:
        c.stop()


def test_collector_add_ignores_invalid_data() -> None:
    """Malformed data (validation error) is silently dropped, not propagated."""
    c = NetworkCollector()
    c.add({"status": "not_an_int"})  # status must be int|None — triggers ValidationError
    assert c.snapshot() == []


def test_collector_snapshot_timed_records_receive_order() -> None:
    times = iter([1.0, 2.5])
    c = NetworkCollector(now=lambda: next(times))
    c.add({"method": "GET", "path": "/a", "status": 200})
    c.add({"method": "POST", "path": "/b", "status": 201})
    timed = c.snapshot_timed()
    assert [t for _, t in timed] == [1.0, 2.5]  # receive times preserved
    assert [ex.path for ex, _ in timed] == ["/a", "/b"]  # in arrival order


def _post_transition(port: int, token: str, kind: str = "screenChanged") -> int:
    """POST one screen-transition event to the collector's /transitions endpoint (BE-0310)."""
    body = json.dumps({"kind": kind}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/transitions",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req) as resp:
        return int(resp.status)


def test_collector_transitions_endpoint_is_independent_of_exchanges() -> None:
    """Screen-transition events (BE-0310) land in their own store, alongside network exchanges,
    with the same token auth and receiver — never mixing with `snapshot()`."""
    c = NetworkCollector()
    port = c.start()
    try:
        assert _post_report(port, c.token) == 204
        assert _post_transition(port, c.token) == 204
        assert len(c.snapshot()) == 1  # the exchange only
        transitions = c.transitions_snapshot_timed()
        assert len(transitions) == 1
        assert transitions[0][0].kind == "screenChanged"
    finally:
        c.stop()


def test_collector_transitions_endpoint_rejects_bad_token() -> None:
    c = NetworkCollector()
    port = c.start()
    try:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/transitions",
            data=json.dumps({"kind": "screenChanged"}).encode(),
            method="POST",
            headers={"Authorization": "Bearer wrong-token"},
        )
        try:
            urllib.request.urlopen(req)
            raised = False
        except urllib.error.HTTPError as err:
            raised = err.code == 401
            err.close()
        assert raised
        assert c.transitions_snapshot_timed() == []
    finally:
        c.stop()


def test_collector_clear_drops_both_exchanges_and_transitions() -> None:
    c = NetworkCollector()
    c.add({"method": "GET", "path": "/a", "status": 200})
    c.add_transition({"kind": "screenChanged"})
    assert c.snapshot() and c.transitions_snapshot_timed()
    c.clear()
    assert c.snapshot() == [] and c.transitions_snapshot_timed() == []


def test_collector_add_transition_ignores_invalid_data() -> None:
    """A malformed payload (validation error) is silently dropped, matching `add`'s
    forward-compatible behavior."""
    c = NetworkCollector()
    c.add_transition({"kind": {"nested": True}})  # kind must be a str — triggers ValidationError
    assert c.transitions_snapshot_timed() == []


def test_collector_transitions_snapshot_timed_records_receive_order() -> None:
    times = iter([1.0, 2.5])
    c = NetworkCollector(now=lambda: next(times))
    c.add_transition({"kind": "screenChanged"})
    c.add_transition({"kind": "screenChanged"})
    timed = c.transitions_snapshot_timed()
    assert [t for _, t in timed] == [1.0, 2.5]
    assert all(isinstance(tr, ScreenTransition) for tr, _ in timed)


def test_orchestrator_request_assertion_step() -> None:
    scn = load_scenarios(
        "- name: net\n"
        "  steps:\n"
        "    - assert: [ { request: { method: POST, path: /login, status: 200 } } ]\n"
    )[0]
    ok = run_scenario(FakeDriver(), scn, network=lambda: [_ex("POST", "/login", 200)])
    assert ok.ok, ok.failure
    miss = run_scenario(FakeDriver(), scn, network=list)  # no exchanges
    assert not miss.ok and "exchange" in (miss.failure or "")


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
        "- name: w\n  steps:\n    - wait: { until: { request: { path: /nope } }, timeout: 0 }\n"
    )[0]
    res = run_scenario(FakeDriver(), scn, network=list)  # nothing observed
    assert not res.ok and "request" in (res.failure or "")

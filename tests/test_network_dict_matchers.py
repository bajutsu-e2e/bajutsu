"""Tests for the `query` / `body` key/value dictionary matchers on network assertions (BE-0463)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from bajutsu.common.assertions import evaluate, evaluate_one
from bajutsu.common.assertions.dict_match import dict_mismatch
from bajutsu.common.assertions.network import match_request, request_label
from bajutsu.common.evidence.network import NetworkExchange
from bajutsu.common.orchestrator import _interp_asserts
from bajutsu.common.report.richtext import _request_parts
from bajutsu.common.scenario import (
    Assertion,
    CountOp,
    EventMatch,
    Mock,
    RequestMatch,
    ResponseSchemaMatch,
)

_ORDER = (
    '{"user":{"id":42,"email":"a@b.c","admin":false},"items":[{"sku":"A-1","qty":1}],"note":null}'
)


def _ex(
    url: str = "https://api.test/orders", body: str | None = None, **kw: Any
) -> NetworkExchange:
    path = url.split("://", 1)[-1].partition("/")[2].partition("?")[0]
    return NetworkExchange(
        method="POST", url=url, path=f"/{path}", status=201, requestBody=body, **kw
    )


def _req(**kw: Any) -> RequestMatch:
    return RequestMatch.model_validate(kw)


# --- grammar (load-time validation) ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("kw", "message"),
    [
        ({"query": {}}, "must not be empty"),
        ({"body": {}}, "must not be empty"),
        ({"body": {"qty": 1}}, "must be a quoted string"),
        ({"body": {"ok": True}}, "must be a quoted string"),
        ({"body": {"note": None}}, "must be a quoted string"),
        ({"body": {"$matches": "x"}}, "keys name fields, not operators"),
        ({"query": {"$exists": True}}, "keys name fields, not operators"),
        ({"body": {"id": {"$equals": "1"}}}, "unknown operator"),
        ({"body": {"id": {"$matches": "x", "name": "y"}}}, "mixes operator keys"),
        ({"body": {"id": {"$matches": "("}}}, "not a valid regex"),
        ({"body": {"id": {"$matches": 1}}}, "must be a regex string"),
        ({"body": {"id": {"$exists": "yes"}}}, "must be true or false"),
        ({"query": {"page": {"n": "2"}}}, "not a map"),
        ({"query": {"page": ["2"]}}, "not a list"),
        ({"body": {"items": [{"qty": 1}]}}, r"body\.items\[0\]\.qty must be a quoted string"),
    ],
)
def test_malformed_dictionary_is_a_validation_error(kw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        _req(**kw)


def test_dictionary_alone_counts_as_a_criterion() -> None:
    assert _req(query={"page": "2"}).query == {"page": "2"}
    assert EventMatch.model_validate({"body": {"name": "x"}}).body == {"name": "x"}
    assert EventMatch.model_validate({"query": {"v": "1"}}).query == {"v": "1"}
    with pytest.raises(ValidationError, match="query/body"):
        _req()
    with pytest.raises(ValidationError, match="query/body"):
        EventMatch.model_validate({})


def test_double_dollar_key_is_a_plain_key() -> None:
    m = _req(body={"$$ref": "#/a", "meta": {"$$id": {"$exists": True}}})
    assert match_request(_ex(body='{"$ref":"#/a","meta":{"$id":1}}'), m)
    assert not match_request(_ex(body='{"ref":"#/a","meta":{"$id":1}}'), m)


def test_mock_refuses_dictionaries() -> None:
    for field in ("query", "body"):
        with pytest.raises(ValidationError, match="does not support query/body"):
            Mock.model_validate({"match": {"path": "/x", field: {"k": "v"}}})
    assert Mock.model_validate({"match": {"path": "/x", "bodyMatches": "k"}}).match.path == "/x"


# --- query ----------------------------------------------------------------------------------------


def test_query_ignores_parameter_order_and_decodes() -> None:
    ex = _ex("https://api.test/orders?sort=asc&page=2&q=a+b%21&debug&empty=")
    assert match_request(ex, _req(query={"page": "2", "sort": "asc"}))
    assert match_request(ex, _req(query={"q": "a b!"}))
    # A key with no or an empty value is present with the value "".
    assert match_request(ex, _req(query={"debug": "", "empty": {"$exists": True}}))
    assert not match_request(ex, _req(query={"page": "3"}))
    assert match_request(ex, _req(query={"sort": {"$matches": "^(asc|desc)$"}}))
    assert match_request(ex, _req(query={"missing": {"$exists": False}}))
    assert not match_request(ex, _req(query={"page": {"$exists": False}}))


def test_query_repeated_key_matches_any_occurrence() -> None:
    ex = _ex("https://api.test/orders?tag=a&tag=b")
    assert match_request(ex, _req(query={"tag": "b"}))
    assert match_request(ex, _req(query={"tag": {"$matches": "^a$"}}))
    assert dict_mismatch(ex, {"tag": "c"}, None) == 'query.tag: expected "c", got ["a", "b"]'


# --- body -----------------------------------------------------------------------------------------


def test_body_ignores_key_order_and_formatting() -> None:
    pretty = (
        '{\n  "items": [ { "qty": 1, "sku": "A-1" } ],\n  "user": { "email": "a@b.c", "id": 42 }\n}'
    )
    m = _req(body={"user": {"id": "42"}, "items": [{"sku": "A-1", "qty": "1"}]})
    assert match_request(_ex(body=_ORDER), m)
    assert match_request(_ex(body=pretty), m)


@pytest.mark.parametrize(
    ("body", "ok"),
    [
        ({"user": {"admin": "false"}, "note": "null"}, True),  # JSON booleans / null read as text
        ({"user": {"email": {"$exists": True}}}, True),
        ({"coupon": {"$exists": False}}, True),
        ({"user": {"email": {"$exists": False}}}, False),
        ({"user": {"email": {"$matches": "@b\\."}}}, True),
        ({"user": {"id": {"$matches": "^4", "$exists": True}}}, True),
        ({"items": [{"sku": "A-1"}]}, True),  # a plain map is a subset of the object
        ({"items": []}, False),  # a list matches element by element, same length
        ({"items": [{"sku": "A-1"}, {"sku": "B-2"}]}, False),
        ({"user": "x"}, False),  # a scalar compares against the compact JSON text of an object
        ({"user": {"id": {"nested": "42"}}}, False),
        ({"items": {"sku": "A-1"}}, False),
    ],
)
def test_body_value_grammar(body: dict[str, Any], ok: bool) -> None:
    assert match_request(_ex(body=_ORDER), _req(body=body)) is ok


def test_scalar_against_object_compares_compact_json() -> None:
    m = _req(body={"items": '[{"sku":"A-1","qty":1}]'})
    assert match_request(_ex(body=_ORDER), m)


@pytest.mark.parametrize("body", [None, "not json", "[1, 2]", '"text"'])
def test_non_object_body_fails_every_body_criterion(body: str | None) -> None:
    ex = _ex(body=body)
    assert not match_request(ex, _req(body={"x": {"$exists": False}}))
    expected = (
        "body: request has no body" if body is None else "body: request body is not a JSON object"
    )
    assert dict_mismatch(ex, None, {"x": "1"}) == expected


def test_pathologically_deep_body_is_not_json_rather_than_a_crash() -> None:
    ex = _ex(body="[" * 100_000)
    assert ex.request_json_object is None
    assert not match_request(ex, _req(body={"x": {"$exists": False}}))


@pytest.mark.parametrize(
    ("query", "body", "expected"),
    [
        ({"gone": {"$matches": "x"}}, None, "query.gone: expected to match /x/, got nothing"),
        (None, {"coupon": {"$matches": "x"}}, "body.coupon: expected to match /x/, got nothing"),
        (None, {"coupon": {"$exists": True, "$matches": "x"}}, "body.coupon: expected present"),
        (
            None,
            {"note": {"$exists": False, "$matches": "n"}},
            'body.note: expected absent, got "null"',
        ),
        ({"tag": {"$exists": False}}, None, 'query.tag: expected absent, got ["a", "b"]'),
    ],
)
def test_operators_on_absent_and_conflicting_keys(
    query: dict[str, Any] | None, body: dict[str, Any] | None, expected: str
) -> None:
    ex = _ex("https://api.test/orders?tag=a&tag=b", body=_ORDER)
    miss = dict_mismatch(ex, query, body)
    assert miss is not None
    assert miss.startswith(expected)


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({"user": {"id": "43"}}, 'body.user.id: expected "43", got "42"'),
        ({"user": {"name": "a"}}, 'body.user.name: expected "a", got nothing'),
        ({"user": {"id": {"$matches": "^9"}}}, 'body.user.id: expected to match /^9/, got "42"'),
        ({"user": {"email": {"$exists": False}}}, 'body.user.email: expected absent, got "a@b.c"'),
        ({"coupon": {"$exists": True}}, "body.coupon: expected present, got nothing"),
        ({"user": {"id": {"x": "1"}}}, 'body.user.id: expected an object, got "42"'),
        ({"items": [{"sku": "A-1", "qty": "2"}]}, 'body.items[0].qty: expected "2", got "1"'),
        ({"items": []}, "body.items: expected an array of 0 elements"),
        ({"gone": {"k": "v"}}, "body.gone: expected a value, got nothing"),
    ],
)
def test_mismatch_names_the_first_failing_key(body: dict[str, Any], expected: str) -> None:
    miss = dict_mismatch(_ex(body=_ORDER), None, body)
    assert miss is not None
    assert miss.startswith(expected)


def test_query_is_checked_before_body_and_both_must_hold() -> None:
    ex = _ex("https://api.test/orders?page=2", body=_ORDER)
    assert match_request(ex, _req(query={"page": "2"}, body={"user": {"id": "42"}}))
    assert dict_mismatch(ex, {"page": "3"}, {"user": {"id": "0"}}) == (
        'query.page: expected "3", got "2"'
    )


def test_long_observed_value_is_cut() -> None:
    ex = _ex(body='{"blob":"' + "x" * 500 + '"}')
    miss = dict_mismatch(ex, None, {"blob": "y"})
    assert miss is not None
    assert miss.endswith("…")
    assert len(miss) < 140


# --- assertions, reporting -------------------------------------------------------------------------


def test_request_failure_names_the_key_for_a_single_candidate() -> None:
    exs = [_ex("https://api.test/orders?page=3", body=_ORDER), _ex("https://api.test/other")]
    a = Assertion(request=_req(path="/orders", query={"page": "2"}))
    r = evaluate_one([], a, exs)
    assert not r.ok
    assert r.reason == 'query.page: expected "2", got "3"'
    assert 'query={"page":"2"}' in r.detail


def test_request_failure_keeps_count_message_when_ambiguous_or_counted() -> None:
    exs = [_ex("https://api.test/orders?page=3"), _ex("https://api.test/orders?page=4")]
    r = evaluate_one([], Assertion(request=_req(path="/orders", query={"page": "2"})), exs)
    assert r.reason == "no matching exchange (observed 2)"
    r = evaluate_one([], Assertion(request=_req(path="/orders", query={"page": "2"}, count=1)), exs)
    assert r.reason == "expected count=1 but matched 0"
    # No candidate at the endpoint at all: the count message again.
    r = evaluate_one([], Assertion(request=_req(path="/nowhere", query={"page": "2"})), exs)
    assert r.reason == "no matching exchange (observed 2)"


def test_request_failure_keeps_count_message_when_dictionaries_hold() -> None:
    exs = [_ex("https://api.test/orders?page=2")]
    r = evaluate_one(
        [], Assertion(request=_req(path="/orders", query={"page": "2"}, status=500)), exs
    )
    assert r.reason == "no matching exchange (observed 1)"


def test_assigned_requests_name_the_key_too() -> None:
    exs = [_ex("https://api.test/orders?page=3"), _ex("https://api.test/items")]
    results = evaluate(
        [],
        [
            Assertion(request=_req(path="/orders", query={"page": "2"})),
            Assertion(request=_req(path="/items")),
        ],
        exs,
    )
    assert results[0].reason == 'query.page: expected "2", got "3"'
    assert results[1].ok


def test_event_flat_body_keeps_its_meaning_and_refuses_empty() -> None:
    exs = [_ex("https://t.test/track", body='{"name":"buy","ok":true,"ref":null,"n":300}')]
    flat = EventMatch.model_validate(
        {"path": "/track", "body": {"name": "buy", "ok": "true", "ref": "null", "n": "300"}}
    )
    assert evaluate_one([], Assertion(event=flat), exs).ok
    with pytest.raises(ValidationError, match="must not be empty"):
        EventMatch.model_validate({"path": "/track", "body": {}})


def test_event_nested_body_and_query() -> None:
    exs = [_ex("https://t.test/track?v=2", body='{"name":"buy","ctx":{"plan":"pro"}}')]
    ok = EventMatch.model_validate(
        {"path": "/track", "query": {"v": "2"}, "body": {"ctx": {"plan": "pro"}}}
    )
    assert evaluate_one([], Assertion(event=ok), exs).ok
    bad = EventMatch.model_validate({"path": "/track", "body": {"ctx": {"plan": "free"}}})
    r = evaluate_one([], Assertion(event=bad), exs)
    assert r.reason == 'body.ctx.plan: expected "free", got "pro"'
    assert 'body={"ctx":{"plan":"free"}}' in r.detail
    counted = bad.model_copy(update={"count": CountOp(equals=1)})
    assert evaluate_one([], Assertion(event=counted), exs).reason.startswith("expected count==1")


def test_sequence_schema_and_label_reuse_the_dictionaries() -> None:
    exs = [_ex("https://api.test/a?step=1"), _ex("https://api.test/b?step=2")]
    seq = [_req(query={"step": "1"}), _req(query={"step": "2"})]
    assert evaluate_one([], Assertion(requestSequence=seq), exs).ok
    assert not evaluate_one([], Assertion(requestSequence=list(reversed(seq))), exs).ok
    schema = ResponseSchemaMatch.model_validate(
        {"request": {"query": {"step": "2"}}, "schema": "order.json"}
    )
    assert [match_request(ex, schema.request) for ex in exs] == [False, True]
    label = request_label(_req(path="/a", query={"step": "1"}, body={"$$ref": {"$exists": True}}))
    assert label == '/a query={"step":"1"} body={"$$ref":{"$exists":true}}'


def test_richtext_renders_dictionaries() -> None:
    _, comparison = _request_parts({"path": "/a", "query": {"page": "2"}, "status": 200})
    texts = "".join(text for _, text in comparison)
    assert 'query ⊇ {"page":"2"}' in texts
    assert "status == 200" in texts


def test_substitution_round_trips_the_grammar() -> None:
    a = Assertion(
        request=_req(
            path="/orders",
            query={"page": "${vars.page}"},
            body={"$$ref": {"$exists": True}, "items": [{"sku": "${vars.sku}"}]},
        )
    )
    [out] = _interp_asserts([a], {"vars.page": "2", "vars.sku": "A-1"})
    assert out.request is not None
    assert out.request.query == {"page": "2"}
    assert out.request.body == {"$$ref": {"$exists": True}, "items": [{"sku": "A-1"}]}
    [same] = _interp_asserts([out], {"vars.page": "9"})  # no token left: returned as is
    assert same == out

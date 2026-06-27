"""Tests for the scenario assertion models."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.scenario import (
    Assertion,
    Wait,
    WaitRequest,
)


def test_wait_forms() -> None:
    assert Wait.model_validate({"for": {"id": "x"}, "timeout": 10}).for_ is not None
    assert Wait.model_validate({"until": "screenChanged", "timeout": 5}).until == "screenChanged"
    gone = Wait.model_validate({"until": {"gone": {"id": "spinner"}}, "timeout": 15})
    assert gone.until is not None
    req = Wait.model_validate(
        {"until": {"request": {"method": "GET", "status": 200}}, "timeout": 8}
    )
    assert isinstance(req.until, WaitRequest) and req.until.request.method == "GET"
    url_req = Wait.model_validate(
        {"until": {"request": {"url": "https://x.com/items"}}, "timeout": 8}
    )
    assert (
        isinstance(url_req.until, WaitRequest)
        and url_req.until.request.url == "https://x.com/items"
    )
    with pytest.raises(ValidationError):  # an empty request matcher is rejected
        Wait.model_validate({"until": {"request": {}}, "timeout": 8})
    with pytest.raises(ValidationError):  # timeout required
        Wait.model_validate({"for": {"id": "x"}})
    with pytest.raises(ValidationError):  # both for and until not allowed
        Wait.model_validate({"for": {"id": "x"}, "until": "screenChanged", "timeout": 1})


def test_assertion_one_kind() -> None:
    with pytest.raises(ValidationError):
        Assertion.model_validate({"exists": {"id": "a"}, "disabled": {"id": "b"}})


def test_clipboard_parses_equals_and_matches() -> None:
    assert Assertion.model_validate({"clipboard": {"equals": "X"}}).clipboard is not None
    assert Assertion.model_validate({"clipboard": {"matches": r"\d+"}}).clipboard is not None


def test_clipboard_requires_exactly_one_operator() -> None:
    with pytest.raises(ValidationError):  # neither equals nor matches
        Assertion.model_validate({"clipboard": {}})
    with pytest.raises(ValidationError):  # both
        Assertion.model_validate({"clipboard": {"equals": "X", "matches": "Y"}})


def test_clipboard_is_an_exclusive_assertion_kind() -> None:
    with pytest.raises(ValidationError):
        Assertion.model_validate({"clipboard": {"equals": "X"}, "exists": {"id": "a"}})


def test_event_parses_endpoint_body_and_count() -> None:
    a = Assertion.model_validate(
        {
            "event": {
                "url": "https://t.example.com/track",
                "body": {"name": "purchase_completed", "amount": "300"},
                "count": {"equals": 1},
            }
        }
    )
    assert a.event is not None
    assert a.event.url == "https://t.example.com/track"
    assert a.event.body["name"] == "purchase_completed"
    assert a.event.count is not None and a.event.count.equals == 1


def test_event_requires_a_criterion() -> None:
    with pytest.raises(ValidationError):  # neither endpoint nor body
        Assertion.model_validate({"event": {"count": {"equals": 1}}})


def test_event_count_requires_exactly_one_operator() -> None:
    with pytest.raises(ValidationError):
        Assertion.model_validate({"event": {"path": "/t", "count": {"equals": 1, "atLeast": 2}}})
    with pytest.raises(ValidationError):
        Assertion.model_validate({"event": {"path": "/t", "count": {}}})


def test_event_is_an_exclusive_assertion_kind() -> None:
    with pytest.raises(ValidationError):  # event + another kind rejected
        Assertion.model_validate({"event": {"path": "/t"}, "exists": {"id": "a"}})


def test_request_sequence_parses_a_list_of_matchers() -> None:
    a = Assertion.model_validate(
        {
            "requestSequence": [
                {"method": "POST", "urlMatches": ".*/auth/refresh"},
                {"method": "GET", "urlMatches": ".*/api/account"},
            ]
        }
    )
    assert a.request_sequence is not None
    assert len(a.request_sequence) == 2
    assert a.request_sequence[0].method == "POST"


def test_request_sequence_rejects_empty_list() -> None:
    with pytest.raises(ValidationError):
        Assertion.model_validate({"requestSequence": []})


def test_request_sequence_is_an_exclusive_kind() -> None:
    with pytest.raises(ValidationError):
        Assertion.model_validate({"requestSequence": [{"path": "/x"}], "exists": {"id": "a"}})


def test_response_schema_parses_request_and_schema() -> None:
    a = Assertion.model_validate(
        {
            "responseSchema": {
                "request": {"method": "GET", "urlMatches": ".*/api/items"},
                "schema": "schemas/items.json",
            }
        }
    )
    assert a.response_schema is not None
    assert a.response_schema.request.method == "GET"
    assert a.response_schema.schema_path == "schemas/items.json"


def test_response_schema_requires_request_and_schema() -> None:
    with pytest.raises(ValidationError):  # missing request
        Assertion.model_validate({"responseSchema": {"schema": "x.json"}})
    with pytest.raises(ValidationError):  # missing schema
        Assertion.model_validate({"responseSchema": {"request": {"path": "/x"}}})


def test_response_schema_is_an_exclusive_kind() -> None:
    with pytest.raises(ValidationError):
        Assertion.model_validate(
            {
                "responseSchema": {"request": {"path": "/x"}, "schema": "x.json"},
                "exists": {"id": "a"},
            }
        )


def test_text_match_one_operator() -> None:
    Assertion.model_validate({"value": {"sel": {"id": "c"}, "equals": "3"}})
    with pytest.raises(ValidationError):
        Assertion.model_validate({"value": {"sel": {"id": "c"}, "equals": "3", "contains": "x"}})


def test_count_alias() -> None:
    a = Assertion.model_validate({"count": {"sel": {"idMatches": "row.*"}, "atLeast": 2}})
    assert a.count is not None
    assert a.count.at_least == 2


def test_visual_assertion_parses() -> None:
    a = Assertion.model_validate({"visual": {"baseline": "counter.png"}})
    assert a.visual is not None
    assert a.visual.baseline == "counter.png"
    assert a.visual.threshold == 0.0  # default


def test_visual_assertion_with_exclude_and_threshold() -> None:
    a = Assertion.model_validate(
        {
            "visual": {
                "baseline": "home.png",
                "threshold": 0.5,
                "exclude": [{"x": 0, "y": 0, "w": 390, "h": 54}],
            }
        }
    )
    assert a.visual is not None
    assert a.visual.threshold == 0.5
    assert len(a.visual.exclude) == 1
    assert a.visual.exclude[0].w == 390


def test_visual_assertion_requires_baseline() -> None:
    with pytest.raises(ValidationError):
        Assertion.model_validate({"visual": {}})

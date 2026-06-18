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

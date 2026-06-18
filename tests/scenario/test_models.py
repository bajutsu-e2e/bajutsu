"""Tests for the scenario schema models.

Verify that each documented form is accepted, malformed forms are rejected, and a
Selector can be converted for resolution.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.drivers import base
from bajutsu.scenario import (
    Assertion,
    Scenario,
    Selector,
    Step,
    Swipe,
    Wait,
    WaitRequest,
    dump_scenarios,
    load_scenarios,
)


def test_preconditions_default() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.preconditions.erase is False  # no full wipe by default; reinstall keeps the app fresh
    assert s.preconditions.reinstall == "clean"  # uninstall + install by default


def test_preconditions_reinstall_validated() -> None:
    s = Scenario.model_validate(
        {
            "name": "x",
            "preconditions": {"erase": True, "reinstall": "overwrite"},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert s.preconditions.erase is True and s.preconditions.reinstall == "overwrite"
    with pytest.raises(ValidationError):  # only clean | overwrite are accepted
        Scenario.model_validate(
            {"name": "x", "preconditions": {"reinstall": "bogus"}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_dismiss_alerts_default_unset() -> None:
    # On by default, but kept None when unset so a dumped scenario stays clean.
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.dismiss_alerts is None
    assert "dismissAlerts" not in dump_scenarios([s])


def test_dismiss_alerts_bool_and_object_forms() -> None:
    off = Scenario.model_validate(
        {"name": "x", "dismissAlerts": False, "steps": [{"tap": {"id": "a"}}]}
    )
    assert off.dismiss_alerts is not None
    assert off.dismiss_alerts.enabled is False  # bare bool is shorthand for {enabled: <bool>}

    instr = Scenario.model_validate(
        {
            "name": "x",
            "dismissAlerts": {"instruction": "tap Allow"},
            "steps": [{"tap": {"id": "a"}}],
        }
    )
    assert instr.dismiss_alerts is not None
    assert instr.dismiss_alerts.enabled is True  # object form stays on unless enabled: false
    assert instr.dismiss_alerts.instruction == "tap Allow"

    # The object form round-trips (the bool form normalizes to {enabled: false}).
    rt = load_scenarios(dump_scenarios([instr]))[0]
    assert rt.dismiss_alerts is not None and rt.dismiss_alerts.instruction == "tap Allow"

    with pytest.raises(ValidationError):  # extra="forbid" rejects unknown keys
        Scenario.model_validate(
            {"name": "x", "dismissAlerts": {"bogus": 1}, "steps": [{"tap": {"id": "a"}}]}
        )


def test_selector_alias_and_as_selector() -> None:
    sel = Selector.model_validate({"idMatches": "result.row.*"})
    assert sel.id_matches == "result.row.*"
    assert sel.as_selector() == {"idMatches": "result.row.*"}


def test_selector_resolves_via_base() -> None:
    # Bridge a scenario Selector into base resolution (the determinism core).
    elements: list[base.Element] = [
        {
            "identifier": "settings.open",
            "label": "設定",
            "traits": ["button"],
            "value": None,
            "frame": (0.0, 0.0, 10.0, 10.0),
        },
    ]
    sel = Selector.model_validate({"id": "settings.open"})
    assert base.resolve_unique(elements, sel.as_selector())["label"] == "設定"


def test_empty_selector_rejected() -> None:
    with pytest.raises(ValidationError):
        Selector.model_validate({})


def test_step_requires_exactly_one_action() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"tap": {"id": "a"}, "wait": {"for": {"id": "b"}, "timeout": 1}})
    with pytest.raises(ValidationError):
        Step.model_validate({"capture": ["screenshot"]})  # no action


def test_unknown_key_rejected() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"tapp": {"id": "a"}})  # typo rejected by extra=forbid


def test_network_filter_domains() -> None:
    s = Scenario.model_validate(
        {
            "name": "n",
            "steps": [{"tap": {"id": "a"}}],
            "network": {"filter": {"domains": ["example.com", "api.example.com"]}},
        }
    )
    assert s.network is not None and s.network.filter is not None
    assert s.network.filter.domains == ["example.com", "api.example.com"]
    # Unset is allowed (shows every exchange in Steps).
    assert Scenario.model_validate({"name": "n", "steps": [{"tap": {"id": "a"}}]}).network is None


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


def test_swipe_forms() -> None:
    assert Swipe.model_validate({"on": {"id": "list"}, "direction": "up"}).direction == "up"
    assert Swipe.model_validate({"from": [1, 2], "to": [3, 4]}).to == (3, 4)
    with pytest.raises(ValidationError):  # mixing not allowed
        Swipe.model_validate({"on": {"id": "list"}, "from": [1, 2], "to": [3, 4]})


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


def test_capture_token_validation() -> None:
    Step.model_validate({"tap": {"id": "a"}, "capture": ["screenshot.after", "network"]})
    with pytest.raises(ValidationError):
        Step.model_validate({"tap": {"id": "a"}, "capture": ["bogus"]})
    with pytest.raises(ValidationError):
        Step.model_validate({"tap": {"id": "a"}, "capture": ["screenshot.whenever"]})


def test_capture_policy_and_redact() -> None:
    data = {
        "name": "rules",
        "steps": [{"tap": {"id": "a"}}],
        "capturePolicy": [
            {"on": {"action": "tap", "idMatches": "*.submit"}, "capture": ["network"]},
            {"on": {"event": "screenChanged"}, "capture": ["elements"]},
            {"on": {"result": "error"}, "capture": ["video"]},
        ],
        "redact": {"headers": ["Authorization"], "fields": ["token"]},
    }
    s = Scenario.model_validate(data)
    assert len(s.capture_policy) == 3
    assert s.redact is not None
    assert s.redact.headers == ["Authorization"]


def test_trigger_idmatches_requires_action() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate(
            {
                "name": "x",
                "steps": [{"tap": {"id": "a"}}],
                "capturePolicy": [
                    {"on": {"event": "screenChanged", "idMatches": "*.x"}, "capture": ["elements"]}
                ],
            }
        )


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


def test_extract_on_step() -> None:
    step = Step.model_validate(
        {"tap": {"id": "counter.inc"}, "extract": {"count": {"sel": {"id": "counter.value"}}}}
    )
    assert step.extract is not None
    assert "count" in step.extract
    assert step.extract["count"].sel.id == "counter.value"
    assert step.extract["count"].prop == "value"


def test_extract_with_explicit_prop() -> None:
    step = Step.model_validate(
        {"tap": {"id": "ok"}, "extract": {"title": {"sel": {"id": "header"}, "prop": "label"}}}
    )
    assert step.extract is not None and step.extract["title"].prop == "label"


def test_extract_rejects_invalid_prop() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate(
            {"tap": {"id": "ok"}, "extract": {"x": {"sel": {"id": "f"}, "prop": "color"}}}
        )


def test_extract_requires_sel() -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"tap": {"id": "ok"}, "extract": {"x": {"prop": "value"}}})


def test_step_without_extract() -> None:
    step = Step.model_validate({"tap": {"id": "ok"}})
    assert step.extract is None

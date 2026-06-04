"""Tests for the scenario schema.

Verify that each documented form is accepted, malformed forms are rejected, and a
Selector can be converted for resolution.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from simyoke.drivers import base
from simyoke.scenario import (
    Assertion,
    Scenario,
    Selector,
    Step,
    Swipe,
    Wait,
    load_scenarios,
)

# Top-level example.
SCENARIO_YAML = """
- name: 設定を開いて再生成する
  preconditions:
    erase: true
    launchEnv: { SEARCH_SHOW_SETTINGS: "1" }
    deeplink: "searchsample://settings"
    locale: "ja_JP"
  steps:
    - tap: { id: settings.open }
    - tap: { id: settings.reindex }
      capture: [screenshot.after, deviceLog]
  expect:
    - exists: { label: "正規化設定が変更されています", negate: true }
"""


def test_load_scenario_example() -> None:
    scenarios = load_scenarios(SCENARIO_YAML)
    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.name == "設定を開いて再生成する"
    assert s.preconditions.erase is True
    assert s.preconditions.launch_env == {"SEARCH_SHOW_SETTINGS": "1"}
    assert len(s.steps) == 2
    assert s.steps[1].capture == ["screenshot.after", "deviceLog"]
    assert s.expect[0].exists is not None
    assert s.expect[0].exists.negate is True
    assert s.expect[0].exists.sel.label == "正規化設定が変更されています"


def test_preconditions_default() -> None:
    s = Scenario.model_validate({"name": "x", "steps": [{"tap": {"id": "a"}}]})
    assert s.preconditions.erase is True  # clean by default


def test_selector_alias_and_as_selector() -> None:
    sel = Selector.model_validate({"idMatches": "result.row.*"})
    assert sel.id_matches == "result.row.*"
    assert sel.as_selector() == {"idMatches": "result.row.*"}


def test_selector_resolves_via_base() -> None:
    # Bridge a scenario Selector into base resolution (the determinism core).
    elements: list[base.Element] = [
        {"identifier": "settings.open", "label": "設定", "traits": ["button"],
         "value": None, "frame": (0.0, 0.0, 10.0, 10.0)},
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


def test_wait_forms() -> None:
    assert Wait.model_validate({"for": {"id": "x"}, "timeout": 10}).for_ is not None
    assert Wait.model_validate({"until": "screenChanged", "timeout": 5}).until == "screenChanged"
    gone = Wait.model_validate({"until": {"gone": {"id": "spinner"}}, "timeout": 15})
    assert gone.until is not None
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


def test_capture_policy_on_key_is_not_yaml_bool() -> None:
    # `on` must stay a string key, not YAML 1.1 boolean True.
    yaml_text = """
- name: rules
  steps:
    - tap: { id: home.title }
  capturePolicy:
    - on: { action: tap, idMatches: "*.submit" }
      capture: [network]
"""
    s = load_scenarios(yaml_text)[0]
    assert s.capture_policy[0].on.action == "tap"


def test_trigger_idmatches_requires_action() -> None:
    with pytest.raises(ValidationError):
        Scenario.model_validate({
            "name": "x",
            "steps": [{"tap": {"id": "a"}}],
            "capturePolicy": [{"on": {"event": "screenChanged", "idMatches": "*.x"}, "capture": ["elements"]}],
        })

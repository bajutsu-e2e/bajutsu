"""Tests for the scenario schema.

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
    apply_setups,
    dump_scenario_file,
    dump_scenarios,
    load_scenario_file,
    load_scenarios,
)

# Top-level example.
SCENARIO_YAML = """
- name: 設定を開いて再生成する
  preconditions:
    erase: true
    launchEnv: { SAMPLE_SCREEN: "settings" }
    deeplink: "bajutsusample://settings"
    locale: "ja_JP"
  steps:
    - tap: { id: settings.open }
    - tap: { id: settings.reindex }
      capture: [screenshot.after, deviceLog]
  expect:
    - exists: { label: "正規化設定が変更されています", negate: true }
"""


def test_scenario_file_descriptions() -> None:
    text = (
        "description: file-level note\n"
        "scenarios:\n"
        "  - name: a\n"
        "    description: per-scenario note\n"
        "    steps:\n"
        "      - tap: { id: x }\n"
    )
    sf = load_scenario_file(text)
    assert sf.description == "file-level note"
    assert sf.scenarios[0].description == "per-scenario note"
    # load_scenarios still returns just the scenarios (file description dropped)
    assert load_scenarios(text)[0].description == "per-scenario note"


def test_scenario_file_bare_list_has_no_description() -> None:
    sf = load_scenario_file("- name: a\n  steps:\n    - tap: { id: x }\n")
    assert sf.description is None and sf.scenarios[0].name == "a"
    assert sf.scenarios[0].description is None


def test_scenario_file_round_trips_with_descriptions() -> None:
    sf = load_scenario_file(
        "description: top\nscenarios:\n  - name: a\n    description: d\n    steps:\n      - tap: { id: x }\n"
    )
    rt = load_scenario_file(dump_scenario_file(sf.scenarios, sf.description))
    assert rt.description == "top" and rt.scenarios[0].description == "d"
    # without a file description, dump_scenario_file emits the bare list form
    assert load_scenario_file(dump_scenario_file(sf.scenarios)).description is None


def test_load_scenario_example() -> None:
    scenarios = load_scenarios(SCENARIO_YAML)
    assert len(scenarios) == 1
    s = scenarios[0]
    assert s.name == "設定を開いて再生成する"
    assert s.preconditions.erase is True
    assert s.preconditions.launch_env == {"SAMPLE_SCREEN": "settings"}
    assert len(s.steps) == 2
    assert s.steps[1].capture == ["screenshot.after", "deviceLog"]
    assert s.expect[0].exists is not None
    assert s.expect[0].exists.negate is True
    assert s.expect[0].exists.sel.label == "正規化設定が変更されています"


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


def test_dump_round_trip() -> None:
    text = """
- name: round trip
  preconditions:
    launchEnv: { K: "1" }
  steps:
    - tap: { id: home.go }
    - type: { text: "hi", into: { id: home.field } }
    - wait: { for: { id: home.done }, timeout: 5 }
    - assert:
        - exists: { id: home.done }
  expect:
    - value: { sel: { id: counter }, equals: "3" }
    - exists: { id: spinner, negate: true }
  capturePolicy:
    - on: { action: tap, idMatches: "*.go" }
      capture: [elements]
"""
    reloaded = load_scenarios(dump_scenarios(load_scenarios(text)))
    assert len(reloaded) == 1
    s = reloaded[0]
    assert s.name == "round trip"
    assert s.steps[0].tap is not None and s.steps[0].tap.id == "home.go"
    assert s.steps[1].type is not None and s.steps[1].type.into is not None
    assert s.expect[0].value is not None and s.expect[0].value.equals == "3"
    assert s.expect[1].exists is not None and s.expect[1].exists.negate is True
    assert s.capture_policy[0].on.action == "tap"


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


def _step(sid: str) -> Step:
    return Step.model_validate({"tap": {"id": sid}})


def test_apply_setups_prepends_prelude_steps() -> None:
    scns = [
        Scenario.model_validate(
            {"name": "a", "preconditions": {"setup": "login.yaml"}, "steps": [{"tap": {"id": "x"}}]}
        ),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "y"}}]}),  # no setup
    ]
    seen: list[str] = []

    def resolve(ref: str) -> list[Step]:
        seen.append(ref)
        return [_step("auth.email"), _step("auth.submit")]

    apply_setups(scns, default_setup=None, resolve=resolve)
    assert [s.tap.id for s in scns[0].steps if s.tap] == ["auth.email", "auth.submit", "x"]
    assert [s.tap.id for s in scns[1].steps if s.tap] == ["y"]  # untouched
    assert seen == ["login.yaml"]


def test_apply_setups_default_is_shared_and_resolved_once() -> None:
    scns = [
        Scenario.model_validate({"name": "a", "steps": [{"tap": {"id": "x"}}]}),
        Scenario.model_validate({"name": "b", "steps": [{"tap": {"id": "y"}}]}),
    ]
    count = 0

    def resolve(ref: str) -> list[Step]:
        nonlocal count
        count += 1
        return [_step("prelude")]

    apply_setups(scns, default_setup="common.yaml", resolve=resolve)
    assert scns[0].steps[0].tap and scns[0].steps[0].tap.id == "prelude"
    assert scns[1].steps[0].tap and scns[1].steps[0].tap.id == "prelude"
    assert count == 1  # the shared default is resolved once and cached


# --- visual assertion model ---


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

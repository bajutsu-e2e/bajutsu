"""Tests for the `installApp` and `setPrimaryTarget` steps at load time (BE-0447, unit 3).

Covers both steps' shapes, the static tracking of the current primary through a scenario's
top-level steps (every later step and `expect` entry that omits `target` resolves to it), where a
`setPrimaryTarget` may sit, the rule that `installApp.from` names a later member of the step's own
device group, the once-per-scenario install, the explicit device an `interrupts` recovery's
`installApp` needs once the primary moves, and how component expansion treats both steps.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from bajutsu.common.scenario import Scenario, Step, expand_components, load_component

_UPDATE = {"targets": [["old", "new"]], "primaryTarget": "old"}


def _scenario(**fields: object) -> Scenario:
    return Scenario.model_validate({"name": "s", **fields})


def _update(steps: list[dict[str, object]], **fields: object) -> Scenario:
    return _scenario(**_UPDATE, steps=steps, **fields)


_INSTALL: dict[str, object] = {"installApp": {"from": "new"}}
_MOVE: dict[str, object] = {"setPrimaryTarget": {"target": "new"}}


# --- shapes --------------------------------------------------------------------------------------


def test_install_app_keeps_data_by_default() -> None:
    step = Step.model_validate(_INSTALL)
    assert step.install_app is not None
    assert step.install_app.from_ == "new" and step.install_app.keep_data is True
    assert Step.model_validate({"installApp": {"from": "new", "keepData": False}}).install_app


def test_set_primary_target_refuses_a_target_modifier() -> None:
    with pytest.raises(ValidationError, match="takes its target as an argument"):
        Step.model_validate({"target": "old", **_MOVE})


# --- the current primary moves in order ----------------------------------------------------------


def test_steps_after_set_primary_target_resolve_to_the_new_primary() -> None:
    s = _update(
        [{"tap": {"id": "a"}}, _INSTALL, _MOVE, {"foreground": {}}, {"tap": {"id": "b"}}],
        expect=[{"exists": {"id": "x"}}],
    )
    assert [st.resolved_target for st in s.steps] == ["old", "old", None, "new", "new"]


def test_a_nested_body_resolves_to_the_primary_in_force_at_its_wrapper() -> None:
    s = _update(
        [
            _INSTALL,
            _MOVE,
            {"if": {"condition": {"exists": {"id": "x"}}, "then": [{"tap": {"id": "a"}}]}},
        ]
    )
    assert s.steps[2].if_ is not None
    assert s.steps[2].if_.then[0].resolved_target == "new"


def test_before_and_after_resolve_to_the_declared_primary() -> None:
    s = _update(
        [_INSTALL, _MOVE],
        before=[{"tap": {"id": "a"}}],
        after=[{"on": "always", "steps": [{"tap": {"id": "b"}}]}],
    )
    assert s.before[0].resolved_target == "old"
    assert s.after[0].steps[0].resolved_target == "old"


def test_set_primary_target_must_name_a_declared_target() -> None:
    with pytest.raises(ValidationError, match="is not one of the scenario's declared targets"):
        _update([{"setPrimaryTarget": {"target": "other"}}])


@pytest.mark.parametrize(
    "steps",
    [
        [{"if": {"condition": {"exists": {"id": "x"}}, "then": [_MOVE]}}],
        [{"forEach": {"sel": {"id": "row"}, "as": "r", "steps": [_MOVE]}}],
        [{"target": "old", "web": {"within": {"id": "wv"}, "steps": [_MOVE]}}],
    ],
    ids=["if", "forEach", "web"],
)
def test_set_primary_target_is_refused_off_the_top_level(steps: list[dict[str, object]]) -> None:
    with pytest.raises(ValidationError, match="setPrimaryTarget is"):
        _update(steps)


def test_set_primary_target_is_refused_in_before_after_and_interrupts() -> None:
    with pytest.raises(ValidationError, match="only among a scenario's top-level steps"):
        _update([{"tap": {"id": "a"}}], before=[_MOVE])
    with pytest.raises(ValidationError, match="only among a scenario's top-level steps"):
        _update([{"tap": {"id": "a"}}], after=[{"on": "always", "steps": [_MOVE]}])
    with pytest.raises(ValidationError, match="only among a scenario's top-level steps"):
        _update(
            [{"tap": {"id": "a"}}],
            interrupts=[{"condition": {"exists": {"id": "x"}}, "steps": [_MOVE]}],
        )


def test_set_primary_target_is_refused_inside_a_target_group() -> None:
    with pytest.raises(ValidationError, match="not allowed inside a target group"):
        _update([{"target": "old", "steps": [_MOVE]}])


# --- installApp.from -----------------------------------------------------------------------------


def test_install_app_from_must_be_a_later_member() -> None:
    with pytest.raises(ValidationError, match="must name a later member"):
        _update([{"installApp": {"from": "old"}}])


def test_install_app_from_a_listed_starting_member_is_refused() -> None:
    with pytest.raises(ValidationError, match="must name a later member"):
        _scenario(
            targets=[["app", "auth"]],
            primaryTarget="app",
            installs=["auth"],
            steps=[{"installApp": {"from": "auth"}}],
        )


def test_install_app_from_another_groups_member_is_refused() -> None:
    with pytest.raises(ValidationError, match="must name a later member"):
        _scenario(
            targets=[["old", "new"], ["web", "b1", "b2"]],
            primaryTarget="old",
            installs=["b1"],
            steps=[{"installApp": {"from": "b2"}}],
        )


def test_install_app_picks_its_device_through_its_own_target() -> None:
    s = _scenario(
        targets=["old", ["web", "b1", "b2"]],
        primaryTarget="old",
        installs=["b1"],
        steps=[{"target": "web", "installApp": {"from": "b2"}}],
    )
    assert s.steps[0].resolved_target == "web"


def test_install_app_may_target_the_member_it_installs() -> None:
    # The step's own target only picks the device, so naming the not-yet-installed member is fine.
    s = _update([{"target": "new", **_INSTALL}])
    assert s.steps[0].resolved_target == "new"


def test_a_member_installs_once_at_the_top_level() -> None:
    with pytest.raises(ValidationError, match="already installed by an earlier top-level"):
        _update([_INSTALL, _INSTALL])


def test_install_app_is_refused_inside_a_web_block() -> None:
    with pytest.raises(ValidationError, match="installApp is not allowed inside a web: or app:"):
        _update([{"web": {"within": {"id": "wv"}, "steps": [_INSTALL]}}])


def test_an_interrupt_recovery_install_names_its_device_once_the_primary_moves() -> None:
    entry = {"condition": {"exists": {"id": "x"}}, "steps": [_INSTALL]}
    with pytest.raises(ValidationError, match="must name its device"):
        _update([_MOVE], interrupts=[entry])
    # Named on the entry, the device is fixed whenever the entry fires.
    _update([_MOVE], interrupts=[{**entry, "target": "old"}])
    # With no setPrimaryTarget, the omitted device is the declared primary's.
    _update([{"tap": {"id": "a"}}], interrupts=[entry])


# --- component expansion -------------------------------------------------------------------------


_SWITCH = load_component(
    """
steps:
  - installApp: { from: new }
  - setPrimaryTarget: { target: new }
"""
)


def _expanded(steps: list[dict[str, object]]) -> Scenario:
    s = _update(steps)
    expand_components([s], lambda name: _SWITCH)
    return s


def test_a_component_set_primary_target_at_the_top_level_moves_the_primary() -> None:
    s = _expanded([{"target": "old", "use": {"component": "switch"}}, {"tap": {"id": "a"}}])
    assert [st.resolved_target for st in s.steps] == ["old", None, "new"]
    # The caller's `target` is never stamped onto a routing-only step.
    assert s.steps[1].target is None


def test_a_component_set_primary_target_under_if_is_refused() -> None:
    with pytest.raises(ValueError, match="only among a scenario's top-level steps"):
        _expanded(
            [
                {
                    "if": {
                        "condition": {"exists": {"id": "x"}},
                        "then": [{"use": {"component": "switch"}}],
                    }
                }
            ]
        )


def test_a_use_step_defers_the_from_check_to_after_expansion() -> None:
    # Before expansion the component may hide a `setPrimaryTarget` that moves the install's device,
    # so the load-time pass leaves `from` alone after a top-level `use:`; the pass expansion runs
    # afterwards checks it with every step in its place.
    s = _update(
        [{"target": "old", "use": {"component": "switch"}}, {"installApp": {"from": "old"}}]
    )
    with pytest.raises(ValueError, match="must name a later member"):
        expand_components([s], lambda name: _SWITCH)


def test_a_top_level_group_hides_the_primary_from_the_load_time_pass() -> None:
    # The group's own `setPrimaryTarget` moves the device the install below it runs on; the
    # load-time pass cannot follow it inside the group, so it leaves `from` to after expansion.
    s = _scenario(
        targets=["a", ["b", "b1", "b2"]],
        primaryTarget="a",
        installs=["b1"],
        steps=[
            {
                "group": {
                    "name": "switch",
                    "steps": [
                        {"setPrimaryTarget": {"target": "b"}},
                        {"installApp": {"from": "b2"}},
                    ],
                }
            }
        ],
    )
    expand_components([s], lambda name: _SWITCH)
    assert [st.resolved_target for st in s.steps] == [None, "b"]


def test_a_components_target_group_refuses_set_primary_target() -> None:
    grouped = load_component(
        """
steps:
  - target: old
    steps:
      - setPrimaryTarget: { target: new }
"""
    )
    s = _update([{"use": {"component": "grouped"}}])
    with pytest.raises(ValueError, match="setPrimaryTarget is not allowed inside a target group"):
        expand_components([s], lambda name: grouped)


def test_an_install_with_no_device_group_is_refused() -> None:
    with pytest.raises(ValidationError, match="has no device group to install into"):
        _scenario(steps=[_INSTALL])


def test_the_final_primary_is_the_one_the_walk_ended_on() -> None:
    assert _update([_INSTALL, _MOVE]).final_primary == "new"
    assert _update([{"tap": {"id": "a"}}]).final_primary == "old"

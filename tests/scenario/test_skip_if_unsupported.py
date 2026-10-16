"""The `skipIfUnsupported` step modifier's load-time rules and expansion (BE-0457)."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from bajutsu.common.scenario import Scenario, Step, expand_components, load_component
from bajutsu.common.scenario.serialize import dump_block

_REASON = "a real device cannot spoof its location"
_SET_LOCATION: dict[str, Any] = {"setLocation": {"lat": 35.68, "lon": 139.76}}
_MANUAL: dict[str, Any] = {"manual": {"label": "solve the CAPTCHA"}}


def test_modifier_parses_on_a_leaf_step_and_is_not_an_action() -> None:
    step = Step.model_validate({**_SET_LOCATION, "skipIfUnsupported": _REASON})
    assert step.skip_if_unsupported == _REASON
    assert step.set_location is not None


@pytest.mark.parametrize("reason", ["", "   "])
def test_blank_reason_is_rejected(reason: str) -> None:
    with pytest.raises(ValidationError, match="reason must say why"):
        Step.model_validate({**_SET_LOCATION, "skipIfUnsupported": reason})


@pytest.mark.parametrize(
    "block",
    [
        {"if": {"condition": {"exists": {"id": "a"}}, "then": [_SET_LOCATION]}},
        {"forEach": {"sel": {"id": "row"}, "as": "x", "steps": [_SET_LOCATION]}},
        {"group": {"name": "geo", "steps": [_SET_LOCATION]}},
        {"steps": [_SET_LOCATION], "target": "ios"},
    ],
)
def test_modifier_is_allowed_on_blocks(block: dict[str, Any]) -> None:
    step = Step.model_validate({**block, "skipIfUnsupported": _REASON})
    assert step.skip_if_unsupported == _REASON


def test_modifier_is_allowed_on_use() -> None:
    step = Step.model_validate({"use": {"component": "geo"}, "skipIfUnsupported": _REASON})
    assert step.skip_if_unsupported == _REASON


def test_use_still_rejects_other_modifiers() -> None:
    with pytest.raises(ValidationError, match="use steps take no modifiers"):
        Step.model_validate(
            {"use": {"component": "geo"}, "name": "n", "skipIfUnsupported": _REASON}
        )


def test_manual_step_is_rejected() -> None:
    with pytest.raises(ValidationError, match="not supported on a manual step"):
        Step.model_validate({**_MANUAL, "skipIfUnsupported": _REASON})


def test_set_primary_target_is_rejected() -> None:
    with pytest.raises(ValidationError, match="setPrimaryTarget"):
        Step.model_validate({"setPrimaryTarget": {"target": "ios"}, "skipIfUnsupported": _REASON})


@pytest.mark.parametrize(
    "block",
    [
        {"group": {"name": "g", "steps": [_SET_LOCATION, _MANUAL]}},
        {
            "if": {
                "condition": {"exists": {"id": "a"}},
                "then": [{"group": {"name": "g", "steps": [_MANUAL]}}],
            }
        },
        {"forEach": {"sel": {"id": "row"}, "as": "x", "steps": [_MANUAL]}},
        {"web": {"within": {"id": "wv"}, "steps": [_MANUAL]}},
        {"app": {"bundleId": "com.example", "steps": [_MANUAL]}},
    ],
)
def test_block_holding_a_manual_step_is_rejected(block: dict[str, Any]) -> None:
    with pytest.raises(ValidationError, match="holding a manual step"):
        Step.model_validate({**block, "skipIfUnsupported": _REASON})


# --- expansion: a covered `group` / `use` / target group is one skip unit ---------------------

_GEO = load_component(
    """
steps:
  - setLocation: { lat: 35.68, lon: 139.76 }
  - assert: [{ exists: { id: tokyo-banner } }]
"""
)
_TAKES_OVER = load_component("steps:\n  - manual: { label: solve the CAPTCHA }\n")


def _expanded(steps: list[dict[str, Any]], **fields: object) -> list[Step]:
    scenario = Scenario.model_validate({"name": "s", "steps": steps, **fields})
    expand_components([scenario], {"geo": _GEO, "takes-over": _TAKES_OVER}.__getitem__)
    return scenario.steps


def _units(steps: list[Step]) -> list[tuple[str | None, int | None]]:
    return [(s.skip_if_unsupported, s.skip_unit_id) for s in steps]


def test_a_covered_group_stamps_one_unit_on_every_step() -> None:
    steps = _expanded(
        [
            {"tap": {"id": "first"}},
            {
                "group": {
                    "name": "geo",
                    "steps": [_SET_LOCATION, {"assert": [{"exists": {"id": "t"}}]}],
                },
                "skipIfUnsupported": _REASON,
            },
        ]
    )
    (first, *covered) = _units(steps)
    assert first == (None, None)
    assert {reason for reason, _ in covered} == {_REASON}
    assert len({unit for _, unit in covered}) == 1 and covered[0][1] is not None


def test_a_covered_use_stamps_one_unit_on_the_components_steps() -> None:
    steps = _expanded([{"use": {"component": "geo"}, "skipIfUnsupported": _REASON}])
    units = _units(steps)
    assert len(units) == 2 and len(set(units)) == 1 and units[0][0] == _REASON


def test_a_covered_target_group_stamps_one_unit() -> None:
    steps = _expanded(
        [
            {
                "steps": [_SET_LOCATION, {"tap": {"id": "a"}}],
                "target": "ios",
                "skipIfUnsupported": _REASON,
            }
        ],
        targets=["ios"],
    )
    units = _units(steps)
    assert len(units) == 2 and len(set(units)) == 1 and units[0][0] == _REASON


def test_separate_covered_steps_get_separate_units() -> None:
    steps = _expanded([{**_SET_LOCATION, "skipIfUnsupported": _REASON}] * 2)
    ids = [unit for _, unit in _units(steps)]
    assert None not in ids and ids[0] != ids[1]


def test_a_covered_step_in_an_if_body_gets_its_own_unit() -> None:
    steps = _expanded(
        [
            {
                "if": {
                    "condition": {"exists": {"id": "a"}},
                    "then": [{**_SET_LOCATION, "skipIfUnsupported": _REASON}],
                }
            }
        ]
    )
    assert steps[0].if_ is not None
    assert steps[0].if_.then[0].skip_unit_id is not None


def test_an_enclosing_unit_wins_and_an_equal_reason_is_accepted() -> None:
    inner = {**_SET_LOCATION, "skipIfUnsupported": _REASON}
    steps = _expanded(
        [
            {
                "group": {"name": "g", "steps": [inner, {"tap": {"id": "a"}}]},
                "skipIfUnsupported": _REASON,
            }
        ]
    )
    assert len(set(_units(steps))) == 1


def test_a_different_reason_inside_a_covered_group_is_refused() -> None:
    inner = {**_SET_LOCATION, "skipIfUnsupported": "another reason"}
    with pytest.raises((ValidationError, ValueError), match="conflicts with the enclosing block"):
        _expanded([{"group": {"name": "g", "steps": [inner]}, "skipIfUnsupported": _REASON}])


def test_a_covered_use_reaching_a_manual_step_is_refused() -> None:
    with pytest.raises(ValueError, match="cannot cover a manual step"):
        _expanded([{"use": {"component": "takes-over"}, "skipIfUnsupported": _REASON}])


def test_units_survive_a_dump_and_reload() -> None:
    steps = _expanded([{"use": {"component": "geo"}, "skipIfUnsupported": _REASON}])
    scenario = Scenario.model_validate(
        {"name": "s", "steps": [s.model_dump(by_alias=True, exclude_none=True) for s in steps]}
    )
    assert _units(scenario.steps) == _units(steps)


# --- numbering: deterministic, collision-free, and kept out of author YAML -------------------


def test_numbering_ignores_what_else_loaded_first() -> None:
    def dumped() -> str:
        steps = _expanded([{**_SET_LOCATION, "skipIfUnsupported": _REASON}])
        return dump_block(steps) + repr(_units(steps))

    # Loading other covered scenarios in between must not shift this one's ids.
    first = dumped()
    _expanded([{"use": {"component": "geo"}, "skipIfUnsupported": _REASON}])
    assert dumped() == first


def test_a_persisted_id_never_collides_with_a_fresh_unit() -> None:
    scenario = Scenario.model_validate(
        {
            "name": "s",
            "setup": [{**_SET_LOCATION, "skipIfUnsupported": _REASON, "_skipUnitId": 1}],
            "steps": [{**_SET_LOCATION, "skipIfUnsupported": "another reason"}],
        }
    )
    assert scenario.setup[0].skip_unit_id != scenario.steps[0].skip_unit_id


def test_dump_block_leaves_the_unit_id_out_of_author_yaml() -> None:
    steps = _expanded(
        [
            {
                "if": {
                    "condition": {"exists": {"id": "a"}},
                    "then": [{**_SET_LOCATION, "skipIfUnsupported": _REASON}],
                }
            }
        ]
    )
    assert "_skipUnitId" not in dump_block(steps)


def test_a_covered_if_whose_body_uses_a_manual_component_is_refused() -> None:
    block = {
        "if": {"condition": {"exists": {"id": "a"}}, "then": [{"use": {"component": "takes-over"}}]}
    }
    with pytest.raises(ValueError, match="holding a manual step"):
        _expanded([{**block, "skipIfUnsupported": _REASON}])

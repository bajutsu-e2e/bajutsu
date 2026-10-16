"""`unsupported_findings`: each unsupported construct with the skip unit covering it (BE-0457)."""

from __future__ import annotations

from typing import Any

from bajutsu.common.capability import capability_preflight
from bajutsu.common.drivers import base
from bajutsu.common.scenario import Component, Scenario, expand_components

_REASON = "a real device cannot spoof its location"
_SET_LOCATION: dict[str, Any] = {"setLocation": {"lat": 35.68, "lon": 139.76}}


def _no_components(ref: str) -> Component:
    raise KeyError(ref)


# The baseline read path and nothing else: every device-control step is unsupported.
_LEAN = frozenset({base.Capability.QUERY, base.Capability.ELEMENTS})


def _findings(
    steps: list[dict[str, Any]], **fields: object
) -> list[capability_preflight.UnsupportedFinding]:
    scenario = Scenario.model_validate({"name": "s", "steps": steps, **fields})
    return capability_preflight.unsupported_findings(scenario, _LEAN)


def test_reasons_match_unsupported() -> None:
    scenario = Scenario.model_validate({"name": "s", "steps": [_SET_LOCATION]})
    assert [f.reason for f in capability_preflight.unsupported_findings(scenario, _LEAN)] == (
        capability_preflight.unsupported(scenario, _LEAN)
    )


def test_an_uncovered_step_has_no_unit() -> None:
    (finding,) = _findings([_SET_LOCATION])
    assert finding.location == "step 1"
    assert finding.skip_unit is None


def test_a_covered_step_carries_its_unit() -> None:
    (finding,) = _findings([{**_SET_LOCATION, "skipIfUnsupported": _REASON}])
    assert finding.skip_unit is not None and finding.skip_unit[0] == _REASON


def test_a_step_in_a_covered_block_carries_the_blocks_unit() -> None:
    steps = [
        {
            "if": {
                "condition": {"exists": {"id": "a"}},
                "then": [{"tap": {"id": "b"}}, _SET_LOCATION],
            },
            "skipIfUnsupported": _REASON,
        }
    ]
    (finding,) = _findings(steps)
    assert finding.location == "step 1 > if > then[1]"
    assert finding.skip_unit is not None and finding.skip_unit[0] == _REASON


def test_scenario_permissions_are_never_covered() -> None:
    findings = _findings(
        [{**_SET_LOCATION, "skipIfUnsupported": _REASON}], permissions={"camera": "grant"}
    )
    permission = next(f for f in findings if f.location == "scenario.permissions")
    assert permission.skip_unit is None


def test_the_baseline_is_never_covered() -> None:
    scenario = Scenario.model_validate(
        {"name": "s", "steps": [{"tap": {"id": "a"}, "skipIfUnsupported": _REASON}]}
    )
    (finding,) = capability_preflight.unsupported_findings(scenario, frozenset())[:1]
    assert finding.location == "" and finding.skip_unit is None


def test_run_preflight_fails_only_uncovered_findings() -> None:
    scenario = Scenario.model_validate(
        {
            "name": "s",
            "steps": [
                {
                    "group": {"name": "g", "steps": [_SET_LOCATION, {"push": {"payload": {}}}]},
                    "skipIfUnsupported": _REASON,
                },
                {**_SET_LOCATION, "skipIfUnsupported": _REASON},
                _SET_LOCATION,
            ],
        }
    )
    expand_components([scenario], _no_components)
    failing, units = capability_preflight.run_preflight(scenario, _LEAN)
    assert len(failing) == 1 and failing[0].startswith("step 4:")
    # The group's two findings share one unit; the lone covered step forms a second.
    assert len(units) == 2

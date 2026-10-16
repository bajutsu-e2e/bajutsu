"""`skipIfUnsupported` at run time: preflight exclusion and step-loop skips (BE-0457).

A real-device XCUITest target drops the simctl-backed device-control capabilities, so a
`setLocation` there is the canonical unsupported step. The lease is a fake driver: these tests are
about what the preflight and the step loop decide, not about any device.
"""

from __future__ import annotations

from typing import Any, Literal

from _runner import _ios_eff, _lease

from bajutsu.common.config import Effective, XcuitestConfig
from bajutsu.common.runner import Lease, run_all
from bajutsu.common.runner.types import TargetPool
from bajutsu.common.scenario import Component, Scenario, expand_components

_REASON = "a real device cannot spoof its location"
_SET_LOCATION: dict[str, Any] = {"setLocation": {"lat": 35.68, "lon": 139.76}}
_TAP_OK: dict[str, Any] = {"tap": {"id": "ok"}}
_ASSERT_OK: dict[str, Any] = {"assert": [{"exists": {"id": "ok"}}]}


def _no_components(ref: str) -> Component:
    raise KeyError(ref)


def _run(
    steps: list[dict[str, Any]],
    device: Literal["simulator", "device"] = "device",
    **fields: object,
) -> Any:
    scenario = Scenario.model_validate({"name": "geo", "steps": steps, **fields})
    # `group:` expands at load time, as the scenario loader does before any run.
    expand_components([scenario], _no_components)
    eff = _ios_eff(xcuitest=XcuitestConfig(testRunner="Runner.xctestrun", deviceType=device))
    (result,) = run_all(eff, [scenario], _lease, actuator="xcuitest")
    return result


def test_a_covered_unsupported_step_is_skipped_and_the_rest_runs() -> None:
    result = _run([_TAP_OK, {**_SET_LOCATION, "skipIfUnsupported": _REASON}, _ASSERT_OK])
    assert result.ok, result.failure
    assert [(s.action, s.skipped) for s in result.steps] == [
        ("tap", False),
        ("set_location", True),
        ("assert_", False),
    ]
    assert result.steps[1].reason == _REASON


def test_an_uncovered_unsupported_step_still_fails_before_the_lease() -> None:
    def lease_must_not_run(eff: Effective, s: Scenario) -> Lease:
        raise AssertionError("the preflight must reject the scenario before any lease")

    scenario = Scenario.model_validate(
        {"name": "geo", "steps": [{**_SET_LOCATION, "skipIfUnsupported": _REASON}, _SET_LOCATION]}
    )
    eff = _ios_eff(xcuitest=XcuitestConfig(testRunner="Runner.xctestrun", deviceType="device"))
    (result,) = run_all(eff, [scenario], lease_must_not_run, actuator="xcuitest")
    assert not result.ok
    assert "step 2: setLocation" in (result.failure or "")
    assert "step 1:" not in (result.failure or "")


def test_a_supported_covered_step_runs_normally() -> None:
    # The modifier only fires on a capability gap: the same tap on a target that supports it runs.
    result = _run([{**_TAP_OK, "skipIfUnsupported": _REASON}, _ASSERT_OK])
    assert result.ok and not any(s.skipped for s in result.steps)


def test_a_covered_group_is_skipped_as_one_unit() -> None:
    group = {"group": {"name": "geo", "steps": [_SET_LOCATION, _ASSERT_OK]}}
    result = _run([_TAP_OK, {**group, "skipIfUnsupported": _REASON}, _ASSERT_OK])
    assert result.ok, result.failure
    assert [s.skipped for s in result.steps] == [False, True, True, False]


def test_a_covered_if_block_is_skipped_whole() -> None:
    block = {"if": {"condition": {"exists": {"id": "ok"}}, "then": [_TAP_OK, _SET_LOCATION]}}
    result = _run([{**block, "skipIfUnsupported": _REASON}, _ASSERT_OK])
    assert result.ok, result.failure
    assert [(s.action, s.skipped) for s in result.steps] == [("if_", True), ("assert_", False)]


def test_a_scenario_whose_every_assertion_was_skipped_fails() -> None:
    group = {"group": {"name": "geo", "steps": [_SET_LOCATION, _ASSERT_OK]}}
    result = _run([_TAP_OK, {**group, "skipIfUnsupported": _REASON}])
    assert not result.ok
    assert "every assertion was skipped" in (result.failure or "")


def test_an_expect_keeps_a_scenario_with_skipped_asserts_checked() -> None:
    group = {"group": {"name": "geo", "steps": [_SET_LOCATION, _ASSERT_OK]}}
    result = _run(
        [_TAP_OK, {**group, "skipIfUnsupported": _REASON}], expect=[{"exists": {"id": "ok"}}]
    )
    assert result.ok, result.failure


def test_nothing_is_skipped_on_a_simulator_target() -> None:
    # The Simulator supports setLocation, so the covered step runs instead of being skipped.
    result = _run([{**_SET_LOCATION, "skipIfUnsupported": _REASON}, _ASSERT_OK], device="simulator")
    assert not any(s.skipped for s in result.steps)


# --- two targets: each judged on its own routed steps (BE-0428) -------------------------------


def _xcuitest_eff(device: Literal["simulator", "device"]) -> Effective:
    return _ios_eff(xcuitest=XcuitestConfig(testRunner="Runner.xctestrun", deviceType=device))


def _two_targets(steps: list[dict[str, Any]]) -> Any:
    scenario = Scenario.model_validate(
        {
            "name": "geo",
            "targets": ["sim", "dev"],
            "steps": steps,
            "expect": [{"exists": {"id": "ok"}, "target": "sim"}],
        }
    )
    expand_components([scenario], _no_components)
    pools = {
        "sim": TargetPool(_xcuitest_eff("simulator"), _lease, "xcuitest"),
        "dev": TargetPool(_xcuitest_eff("device"), _lease, "xcuitest"),
    }
    (result,) = run_all(_xcuitest_eff("simulator"), [scenario], _lease, targets=pools)
    return result


def test_only_the_target_lacking_the_capability_skips() -> None:
    covered = {**_SET_LOCATION, "skipIfUnsupported": _REASON}
    result = _two_targets([{**covered, "target": "dev"}, {**covered, "target": "sim"}])
    # The Simulator target supports setLocation, so its step runs — and the fake driver behind the
    # lease, having no device control, fails it: proof the step was executed, not skipped.
    assert [(s.target, s.skipped) for s in result.steps] == [("dev", True), ("sim", False)]
    assert not result.steps[1].ok


def test_an_uncovered_gap_on_one_target_still_fails_the_scenario_up_front() -> None:
    result = _two_targets(
        [
            {**_SET_LOCATION, "target": "dev", "skipIfUnsupported": _REASON},
            {**_SET_LOCATION, "target": "dev"},
        ]
    )
    assert not result.ok and result.steps == []
    assert "target 'dev'" in (result.failure or "")


def test_a_unit_spanning_targets_skips_whole_when_one_target_lacks_the_capability() -> None:
    group = {
        "group": {
            "name": "geo",
            "steps": [{**_TAP_OK, "target": "sim"}, {**_SET_LOCATION, "target": "dev"}],
        },
        "skipIfUnsupported": _REASON,
    }
    result = _two_targets([group])
    assert result.ok, result.failure
    assert [s.skipped for s in result.steps] == [True, True]


# --- phases ---------------------------------------------------------------------------------


def test_a_covered_step_in_setup_is_skipped() -> None:
    result = _run([_ASSERT_OK], setup=[{**_SET_LOCATION, "skipIfUnsupported": _REASON}])
    assert result.ok, result.failure
    assert [s.skipped for s in result.before_outcomes] == [True]


def test_a_skip_that_leaves_a_live_assert_does_not_fail_the_scenario() -> None:
    result = _run([{**_SET_LOCATION, "skipIfUnsupported": _REASON}, _ASSERT_OK])
    assert result.ok, result.failure


def test_a_covered_foreach_inside_a_group_is_skipped_with_its_asserts() -> None:
    loop = {"forEach": {"sel": {"id": "ok"}, "as": "x", "steps": [_SET_LOCATION, _ASSERT_OK]}}
    group = {"group": {"name": "g", "steps": [_TAP_OK, loop]}, "skipIfUnsupported": _REASON}
    result = _run([group])
    # The `forEach` held the only `assert`, so the scenario checked nothing.
    assert not result.ok and "every assertion was skipped" in (result.failure or "")
    assert [s.skipped for s in result.steps] == [True, True]


def test_a_scenario_whose_every_step_was_skipped_fails() -> None:
    result = _run([{**_SET_LOCATION, "skipIfUnsupported": _REASON}])
    assert not result.ok
    assert "every step was skipped" in (result.failure or "")


def test_a_covered_step_in_cleanup_is_skipped() -> None:
    rule = {"on": "always", "steps": [{**_SET_LOCATION, "skipIfUnsupported": _REASON}]}
    result = _run([_ASSERT_OK], cleanup=[rule])
    assert result.ok, result.failure
    assert [s.skipped for s in result.after_outcomes] == [True]


def test_a_skipped_step_keeps_its_place_on_the_timeline() -> None:
    result = _run([_TAP_OK, {**_SET_LOCATION, "skipIfUnsupported": _REASON}, _ASSERT_OK])
    starts = [s.started_at for s in result.steps]
    assert starts == sorted(starts) and starts[1] > 0

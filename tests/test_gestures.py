"""Gesture primitives: doubleTap / pinch / rotate across the DSL, drivers,
orchestrator (capability gating), and codegen.

pinch / rotate need multi-touch; a single-touch actuator (idb) must fail the step
with a clear reason rather than silently approximating, and the only on-device path
for those gestures is the generated XCUITest.
"""

from __future__ import annotations

import pytest

from bajutsu.codegen import to_xcuitest
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.drivers.idb import IdbDriver, tap_cmd
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import load_scenarios


def _el(identifier: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": None,
        "traits": [],
        "value": None,
        "frame": (0.0, 0.0, 100.0, 40.0),
    }


# --- DSL parsing ---


def test_parse_gesture_steps() -> None:
    scenarios = load_scenarios(
        "- name: g\n  steps:\n"
        "    - doubleTap: { id: gest.dt }\n"
        "    - pinch: { sel: { id: gest.zoom }, scale: 2.0 }\n"
        "    - rotate: { sel: { id: gest.rot }, radians: 1.57 }\n"
    )
    steps = scenarios[0].steps
    assert steps[0].double_tap is not None and steps[0].double_tap.id == "gest.dt"
    assert steps[1].pinch is not None and steps[1].pinch.scale == 2.0
    assert steps[2].rotate is not None and steps[2].rotate.radians == 1.57


def test_pinch_scale_must_be_positive() -> None:
    with pytest.raises(ValueError):
        load_scenarios("- name: g\n  steps:\n    - pinch: { sel: { id: a }, scale: 0 }\n")


def test_step_is_one_action() -> None:
    with pytest.raises(ValueError):
        load_scenarios("- name: g\n  steps:\n    - doubleTap: { id: a }\n      tap: { id: b }\n")


# --- FakeDriver records the gestures (orchestrator dispatch) ---


def test_orchestrator_dispatches_gestures() -> None:
    driver = FakeDriver(screen=[_el("gest.dt"), _el("gest.zoom"), _el("gest.rot")])
    scenario = load_scenarios(
        "- name: g\n  steps:\n"
        "    - doubleTap: { id: gest.dt }\n"
        "    - pinch: { sel: { id: gest.zoom }, scale: 2.0 }\n"
        "    - rotate: { sel: { id: gest.rot }, radians: 1.57 }\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    kinds = [a[0] for a in driver.actions]
    assert kinds == ["double_tap", "pinch", "rotate"]


# --- Capability gating: a single-touch actuator declines pinch / rotate ---


def test_pinch_fails_without_multitouch_capability() -> None:
    driver = IdbDriver("U", run=lambda a: "[]")  # idb advertises no MULTI_TOUCH
    scenario = load_scenarios("- name: g\n  steps:\n    - pinch: { sel: { id: a }, scale: 2.0 }\n")[
        0
    ]
    result = run_scenario(driver, scenario)
    assert not result.ok
    assert "multiTouch" in (result.failure or "")


def test_idb_double_tap_is_two_taps() -> None:
    calls: list[list[str]] = []

    def run(args: list[str]) -> str:
        if "describe-all" in args:
            return '[{"AXUniqueId":"a","frame":{"x":0,"y":0,"width":100,"height":40}}]'
        calls.append(args)
        return ""

    IdbDriver("U", run=run).double_tap({"id": "a"})
    assert calls == [tap_cmd("U", 50, 20), tap_cmd("U", 50, 20)]


def test_idb_pinch_rotate_unsupported() -> None:
    driver = IdbDriver("U", run=lambda a: "[]")
    with pytest.raises(base.UnsupportedAction):
        driver.pinch({"id": "a"}, 2.0)
    with pytest.raises(base.UnsupportedAction):
        driver.rotate({"id": "a"}, 1.57)
    assert base.Capability.MULTI_TOUCH not in driver.capabilities()


# --- codegen -> XCUITest ---


def test_codegen_emits_gesture_calls() -> None:
    scenarios = load_scenarios(
        "- name: g\n  steps:\n"
        "    - doubleTap: { id: gest.dt }\n"
        "    - pinch: { sel: { id: gest.zoom }, scale: 2.0 }\n"
        "    - pinch: { sel: { id: gest.out }, scale: 0.5 }\n"
        "    - rotate: { sel: { id: gest.rot }, radians: 1.57 }\n"
    )
    code = to_xcuitest(scenarios, "GestUITests")
    assert 'el("gest.dt").doubleTap()' in code
    assert 'el("gest.zoom").pinch(withScale: 2.0, velocity: 1.0)' in code
    assert 'el("gest.out").pinch(withScale: 0.5, velocity: -1.0)' in code
    assert 'el("gest.rot").rotate(1.57, withVelocity: 1.0)' in code
    assert "TODO" not in code

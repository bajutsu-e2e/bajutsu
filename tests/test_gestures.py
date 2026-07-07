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
    with pytest.raises(ValueError, match="scale"):
        load_scenarios("- name: g\n  steps:\n    - pinch: { sel: { id: a }, scale: 0 }\n")


def test_step_is_one_action() -> None:
    with pytest.raises(ValueError, match="exactly one"):
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


# --- swipe amount: how far a directional scroll travels ---


def _swipe_points(spec: str) -> tuple[base.Point, base.Point]:
    """Run a swipe step against a 400x800 fake screen (a `list` at y 300..500) and return its
    resolved (from, to) points."""
    win: base.Element = {"identifier": None, "label": None, "traits": ["application"], "value": None,
                         "frame": (0.0, 0.0, 400.0, 800.0)}  # fmt: skip
    lst: base.Element = {"identifier": None, "label": "list", "traits": ["table"], "value": None,
                         "frame": (0.0, 300.0, 400.0, 200.0)}  # fmt: skip
    driver = FakeDriver(screen=[win, lst])
    result = run_scenario(driver, load_scenarios(f"- name: s\n  steps:\n    - {spec}\n")[0])
    assert result.ok, result.failure
    frm, to = next(arg for kind, arg in driver.actions if kind == "swipe")
    return frm, to


def _swipe_travel(spec: str) -> float:
    """The vertical distance a directional swipe travels (independent of where it starts)."""
    frm, to = _swipe_points(spec)
    return abs(frm[1] - to[1])


def test_swipe_amount_scales_scroll_distance() -> None:
    default = _swipe_travel("swipe: { on: { label: list }, direction: up }")
    half = _swipe_travel("swipe: { on: { label: list }, direction: up, amount: 0.5 }")
    assert default == 100.0  # the small default nudge
    assert half == 400.0 and half > default  # 0.5 of the 800pt screen height


def test_swipe_begins_on_the_element() -> None:
    # A directional swipe must put its `down` ON the target, not offset by half the travel — else a
    # swipe that grabs a small handle (a resize divider) lands beside it and drags nothing. The list
    # spans y 300..500 (center 400) with room in both directions, so the gesture starts exactly at
    # the center and travels the default nudge upward from there.
    frm, to = _swipe_points("swipe: { on: { label: list }, direction: up }")
    assert frm == (200.0, 400.0)  # down on the element center
    assert to == (200.0, 300.0)  # up by the 100pt default nudge


def test_swipe_amount_must_be_a_screen_fraction() -> None:
    with pytest.raises(ValueError, match=r"within 0"):
        load_scenarios(
            "- name: s\n  steps:\n    - swipe: { on: { id: a }, direction: up, amount: 2 }\n"
        )


def test_swipe_amount_only_with_direction_form() -> None:
    with pytest.raises(ValueError, match="amount applies only"):
        load_scenarios(
            "- name: s\n  steps:\n    - swipe: { from: [0, 0], to: [0, 10], amount: 0.5 }\n"
        )


# --- Capability gating: a single-touch actuator declines pinch / rotate ---


def test_pinch_fails_without_multitouch_capability() -> None:
    driver = IdbDriver("U", run=lambda a: "[]")  # idb advertises no MULTI_TOUCH
    scenario = load_scenarios("- name: g\n  steps:\n    - pinch: { sel: { id: a }, scale: 2.0 }\n")[
        0
    ]
    result = run_scenario(driver, scenario)
    assert not result.ok
    assert "multi-touch" in (result.failure or "")


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

"""Gesture primitives: doubleTap / pinch / rotate across the DSL, drivers,
orchestrator (capability gating), and codegen.

pinch / rotate need multi-touch; a single-touch actuator must fail the step
with a clear reason rather than silently approximating.
"""

from __future__ import annotations

import pytest

from bajutsu.codegen import to_xcuitest
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
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


# --- handleSystemAlert: the orchestrator dispatches to the driver (BE-0316) ---


def _button(label: str) -> base.Element:
    return {
        "identifier": None,
        "label": label,
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 100.0, 40.0),
    }


def test_orchestrator_dispatches_handle_system_alert() -> None:
    driver = FakeDriver(screen=[])
    driver.system_alert_buttons = [_button("Allow"), _button("Don't Allow")]
    scenario = load_scenarios(
        "- name: a\n  steps:\n    - handleSystemAlert: { sel: { label: Allow }, timeout: 5 }\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    assert ("handle_system_alert", ({"label": "Allow"}, 5.0)) in driver.actions


def test_handle_system_alert_fails_the_step_when_no_prompt_appears() -> None:
    driver = FakeDriver(screen=[])  # no system_alert_buttons seeded → the prompt never appears
    scenario = load_scenarios(
        "- name: a\n  steps:\n    - handleSystemAlert: { sel: { label: Allow }, timeout: 5 }\n"
    )[0]
    result = run_scenario(driver, scenario)
    assert not result.ok  # fails loudly, never a silent pass (prime directive 2)


# --- back: a cross-backend navigation step (BE-0210) ---


def test_orchestrator_dispatches_back() -> None:
    # `back` resolves no selector, so an empty screen still dispatches — the handler calls the
    # driver's platform-correct back (Android keyevent / iOS OS BackButton / web history).
    driver = FakeDriver(screen=[])
    scenario = load_scenarios("- name: b\n  steps:\n    - back: {}\n")[0]
    result = run_scenario(driver, scenario)
    assert result.ok, result.failure
    assert driver.actions == [("back", None)]


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
    frm, to = next(arg for kind, arg in driver.actions if kind == "scroll")
    return frm, to


def _swipe_travel(spec: str) -> float:
    """The vertical distance a directional swipe travels (independent of where it starts)."""
    frm, to = _swipe_points(spec)
    return abs(frm[1] - to[1])


def test_swipe_amount_scales_scroll_distance() -> None:
    default = _swipe_travel("swipe: { on: { label: list }, direction: up }")
    half = _swipe_travel("swipe: { on: { label: list }, direction: up, amount: 0.5 }")
    assert default == 100.0  # the default fraction (0.125) of the 800pt reference screen
    assert half == 400.0 and half > default  # 0.5 of the 800pt screen height


def _swipe_travel_on(screen_h: float, spec: str) -> float:
    """The vertical travel of a directional swipe on a screen of the given height (width 400)."""
    win: base.Element = {"identifier": None, "label": None, "traits": ["application"], "value": None,
                         "frame": (0.0, 0.0, 400.0, screen_h)}  # fmt: skip
    lst: base.Element = {"identifier": None, "label": "list", "traits": ["table"], "value": None,
                         "frame": (0.0, screen_h / 3, 400.0, screen_h / 4)}  # fmt: skip
    driver = FakeDriver(screen=[win, lst])
    result = run_scenario(driver, load_scenarios(f"- name: s\n  steps:\n    - {spec}\n")[0])
    assert result.ok, result.failure
    frm, to = next(arg for kind, arg in driver.actions if kind == "scroll")
    return abs(frm[1] - to[1])


def test_swipe_default_travel_is_screen_relative() -> None:
    # The default swipe (no `amount`) travels a fraction of the screen, not a fixed count, so it
    # scrolls the same proportion of a dense device (Android's 2400px screen) as of a sparse one
    # (iOS's ~900pt) — a fixed count scrolls ~2.6x less of the Android screen, so a swipe sized for
    # iOS barely moves an Android list (BE-0208).
    spec = "swipe: { on: { label: list }, direction: up }"
    assert _swipe_travel_on(800.0, spec) == 100.0  # 0.125 of 800
    assert _swipe_travel_on(2400.0, spec) == 300.0  # 0.125 of 2400 — scales with the screen


def test_swipe_begins_on_the_element() -> None:
    # A directional swipe must put its `down` ON the target, not offset by half the travel — else a
    # swipe that grabs a small handle (a resize divider) lands beside it and drags nothing. The list
    # spans y 300..500 (center 400) with room in both directions, so the gesture starts exactly at
    # the center and travels the default fraction upward from there.
    frm, to = _swipe_points("swipe: { on: { label: list }, direction: up }")
    assert frm == (200.0, 400.0)  # down on the element center
    assert to == (200.0, 300.0)  # up by 0.125 of the 800pt screen (100pt) from the center


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


# --- drag: an element-anchored pointer drag, distinct from swipe's scroll (BE-0227) ---


def test_drag_is_a_real_pointer_drag_not_a_scroll() -> None:
    # `drag` shares swipe's directional endpoint math but drives `driver.swipe` (a real drag), so the
    # fake driver records "swipe", not "scroll" — the seam that lets web move a grabbed handle.
    win: base.Element = {"identifier": None, "label": None, "traits": ["application"], "value": None,
                         "frame": (0.0, 0.0, 400.0, 800.0)}  # fmt: skip
    handle: base.Element = {"identifier": "divider", "label": None, "traits": [], "value": None,
                            "frame": (100.0, 300.0, 10.0, 200.0)}  # fmt: skip
    driver = FakeDriver(screen=[win, handle])
    result = run_scenario(
        driver,
        load_scenarios(
            "- name: s\n  steps:\n    - drag: { on: { id: divider }, direction: right }\n"
        )[0],
    )
    assert result.ok, result.failure
    [(kind, (frm, to))] = driver.actions
    assert kind == "swipe"  # a pointer drag, not a scroll
    assert to[0] > frm[0] and to[1] == frm[1]  # travels right, level


def test_drag_amount_must_be_a_screen_fraction() -> None:
    with pytest.raises(ValueError, match=r"within 0"):
        load_scenarios(
            "- name: s\n  steps:\n    - drag: { on: { id: a }, direction: up, amount: 2 }\n"
        )


def test_drag_requires_on_and_direction() -> None:
    # drag is element-anchored only — no {from,to} form (that stays swipe's coordinate escape hatch).
    with pytest.raises(ValueError, match=r"Field required|Extra inputs"):
        load_scenarios("- name: s\n  steps:\n    - drag: { from: [0, 0], to: [0, 10] }\n")


# --- Capability gating: a single-touch actuator declines pinch / rotate ---


class _SingleTouchFake(FakeDriver):
    """A fake driver that advertises no MULTI_TOUCH, standing in for a single-touch backend.

    Every real backend advertises multiTouch (BE-0290), so the orchestrator's
    capability gate is exercised against this stand-in rather than a concrete driver.
    """

    def capabilities(self) -> set[str]:
        return super().capabilities() - {base.Capability.MULTI_TOUCH}


def test_pinch_fails_without_multitouch_capability() -> None:
    win: base.Element = {"identifier": "a", "label": None, "traits": [], "value": None,
                         "frame": (0.0, 0.0, 100.0, 40.0)}  # fmt: skip
    driver = _SingleTouchFake(screen=[win])
    scenario = load_scenarios("- name: g\n  steps:\n    - pinch: { sel: { id: a }, scale: 2.0 }\n")[
        0
    ]
    result = run_scenario(driver, scenario)
    assert not result.ok
    assert "multi-touch" in (result.failure or "")


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

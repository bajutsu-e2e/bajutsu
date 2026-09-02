"""Gesture primitives: doubleTap / pinch / rotate across the DSL, drivers,
orchestrator (capability gating), and codegen.

pinch / rotate need multi-touch; a single-touch actuator must fail the step
with a clear reason rather than silently approximating.
"""

from __future__ import annotations

import pytest

from bajutsu.codegen import to_xcuitest
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.scenario import load_scenarios
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver


def _points(arg: object) -> tuple[base.Point, base.Point]:
    """One logged gesture's (from, to) points."""
    assert isinstance(arg, tuple) and len(arg) == 2
    return arg


def _scroll_points(driver: FakeDriver) -> tuple[base.Point, base.Point]:
    """The (from, to) points of the driver's first scroll.

    `FakeDriver.actions` logs `(kind, arg)` with `arg` typed `object`, so the read asserts the shape
    rather than casting it away (BE-0388).
    """
    return _points(next(arg for kind, arg in driver.actions if kind == "scroll"))


def _el(identifier: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": None,
        "traits": [],
        "value": None,
        "frame": (0.0, 0.0, 100.0, 40.0),
        "nativeZ": None,
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
        "nativeZ": None,
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
                         "frame": (0.0, 0.0, 400.0, 800.0), "nativeZ": None}  # fmt: skip
    lst: base.Element = {"identifier": None, "label": "list", "traits": ["table"], "value": None,
                         "frame": (0.0, 300.0, 400.0, 200.0), "nativeZ": None}  # fmt: skip
    driver = FakeDriver(screen=[win, lst])
    result = run_scenario(driver, load_scenarios(f"- name: s\n  steps:\n    - {spec}\n")[0])
    assert result.ok, result.failure
    return _scroll_points(driver)


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
                         "frame": (0.0, 0.0, 400.0, screen_h), "nativeZ": None}  # fmt: skip
    lst: base.Element = {"identifier": None, "label": "list", "traits": ["table"], "value": None,
                         "frame": (0.0, screen_h / 3, 400.0, screen_h / 4), "nativeZ": None}  # fmt: skip
    driver = FakeDriver(screen=[win, lst])
    result = run_scenario(driver, load_scenarios(f"- name: s\n  steps:\n    - {spec}\n")[0])
    assert result.ok, result.failure
    frm, to = _scroll_points(driver)
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


# --- the anchor read: a directional gesture resolves its target above the driver ---


class _LaggingReadDriver(FakeDriver):
    """A backend whose bare `query()` still describes the screen from before the last gesture.

    Stands in for Android, where the accessibility update naming the new frames is published after
    the gesture has already moved the content, so a read taken in between is self-consistently stale.
    """

    def __init__(self, stale: list[base.Element], settled: list[base.Element]) -> None:
        super().__init__(screen=stale)
        self._settled = settled

    def settled_query(self) -> list[base.Element]:
        return list(self._settled)


def _anchor_frames(list_top: float) -> list[base.Element]:
    win: base.Element = {"identifier": None, "label": None, "traits": ["application"], "value": None,
                         "frame": (0.0, 0.0, 400.0, 800.0), "nativeZ": None}  # fmt: skip
    lst: base.Element = {"identifier": "list", "label": None, "traits": ["table"], "value": None,
                         "frame": (0.0, list_top, 400.0, 200.0), "nativeZ": None}  # fmt: skip
    return [win, lst]


def test_a_directional_swipe_anchors_on_the_settled_read_where_a_backend_offers_one() -> None:
    # `tap` and the other selector-addressed actuators hand the driver a selector, so the driver
    # settles the tree itself before resolving a coordinate. A directional swipe cannot: its endpoints
    # are computed here and the driver receives two coordinates. On a backend whose reads lag, taking
    # the bare read anchors the gesture on the previous screen — so the handler asks for the driver's
    # actuation-grade read where one is offered.
    driver = _LaggingReadDriver(_anchor_frames(300.0), _anchor_frames(227.0))
    result = run_scenario(
        driver,
        load_scenarios(
            "- name: s\n  steps:\n    - swipe: { on: { id: list }, direction: up, amount: 0.05 }\n"
        )[0],
    )
    assert result.ok, result.failure
    [(kind, arg)] = driver.actions
    assert kind == "scroll"
    frm, _to = _points(arg)
    assert frm == (200.0, 327.0)  # the settled centre, not the stale 400.0


def test_a_backend_that_reports_no_settled_read_keeps_its_single_query() -> None:
    # Not implementing the protocol means "one `query()` is already good enough to actuate from", so
    # the synchronous backends keep the exact read they take today rather than paying a second one.
    driver = FakeDriver(screen=_anchor_frames(300.0))
    assert not isinstance(driver, base.SettledReadProvider)
    result = run_scenario(
        driver,
        load_scenarios(
            "- name: s\n  steps:\n    - swipe: { on: { id: list }, direction: up, amount: 0.05 }\n"
        )[0],
    )
    assert result.ok, result.failure
    [(_, arg)] = driver.actions
    frm, _to = _points(arg)
    assert frm == (200.0, 400.0)


# --- drag: an element-anchored pointer drag, distinct from swipe's scroll (BE-0227) ---


def test_drag_is_a_real_pointer_drag_not_a_scroll() -> None:
    # `drag` shares swipe's directional endpoint math but drives `driver.swipe` (a real drag), so the
    # fake driver records "swipe", not "scroll" — the seam that lets web move a grabbed handle.
    win: base.Element = {"identifier": None, "label": None, "traits": ["application"], "value": None,
                         "frame": (0.0, 0.0, 400.0, 800.0), "nativeZ": None}  # fmt: skip
    handle: base.Element = {"identifier": "divider", "label": None, "traits": [], "value": None,
                            "frame": (100.0, 300.0, 10.0, 200.0), "nativeZ": None}  # fmt: skip
    driver = FakeDriver(screen=[win, handle])
    result = run_scenario(
        driver,
        load_scenarios(
            "- name: s\n  steps:\n    - drag: { on: { id: divider }, direction: right }\n"
        )[0],
    )
    assert result.ok, result.failure
    [(kind, arg)] = driver.actions
    assert kind == "swipe"  # a pointer drag, not a scroll
    frm, to = _points(arg)
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
                         "frame": (0.0, 0.0, 100.0, 40.0), "nativeZ": None}  # fmt: skip
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

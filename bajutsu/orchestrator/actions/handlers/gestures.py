"""Gesture and text-entry handlers: tap, double-tap, long-press, type, swipe, pinch, rotate."""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.elements import screen_size_from_elements
from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.scenario import Step

_SWIPE_DIST = 100.0


def _center(frame: base.Frame) -> base.Point:
    x, y, w, h = frame
    return (x + w / 2, y + h / 2)


def _target(center: base.Point, direction: str) -> base.Point:
    cx, cy = center
    if direction == "up":
        return (cx, cy - _SWIPE_DIST)
    if direction == "down":
        return (cx, cy + _SWIPE_DIST)
    if direction == "left":
        return (cx - _SWIPE_DIST, cy)
    return (cx + _SWIPE_DIST, cy)  # right


def _require_multi_touch(driver: base.Driver, action: str) -> None:
    """Fail clearly before a two-finger gesture if the actuator can't do multi-touch
    (e.g. idb), rather than emitting a single-touch approximation that silently passes."""
    if base.Capability.MULTI_TOUCH not in driver.capabilities():
        raise base.UnsupportedAction(
            f"{action} requires a multi-touch capable backend (idb supports single touch only; use codegen→XCUITest instead)"
        )


@_handler("tap")
def _do_tap(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.tap is not None
    driver.tap(step.tap.as_selector())


@_handler("tap_point")
def _do_tap_point(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.tap_point is not None
    # Scale the normalized [0,1] point by the live screen size — the same helper the crawl and the
    # alert guard use, so every coordinate tap replays against one screen-size definition.
    w, h = screen_size_from_elements(driver.query())
    driver.tap_point((step.tap_point.x * w, step.tap_point.y * h))


@_handler("double_tap")
def _do_double_tap(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.double_tap is not None
    driver.double_tap(step.double_tap.as_selector())


@_handler("long_press")
def _do_long_press(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.long_press is not None
    driver.long_press(step.long_press.sel.as_selector(), step.long_press.duration)


@_handler("type")
def _do_type(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.type is not None
    if step.type.into is not None:
        driver.tap(step.type.into.as_selector())
    driver.type_text(step.type.text)


@_handler("swipe")
def _do_swipe(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.swipe is not None
    sw = step.swipe
    if sw.from_ is not None and sw.to is not None:
        driver.swipe(sw.from_, sw.to)
    elif sw.on is not None and sw.direction is not None:
        el = base.resolve_unique(driver.query(), sw.on.as_selector())
        center = _center(el["frame"])
        driver.swipe(center, _target(center, sw.direction))


@_handler("pinch")
def _do_pinch(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.pinch is not None
    _require_multi_touch(driver, "pinch")
    driver.pinch(step.pinch.sel.as_selector(), step.pinch.scale)


@_handler("rotate")
def _do_rotate(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.rotate is not None
    _require_multi_touch(driver, "rotate")
    driver.rotate(step.rotate.sel.as_selector(), step.rotate.radians)

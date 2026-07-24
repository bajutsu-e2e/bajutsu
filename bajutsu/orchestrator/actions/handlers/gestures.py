"""Gesture and text-entry handlers: tap, double-tap, long-press, type, swipe, pinch, rotate."""

from __future__ import annotations

from bajutsu.drivers import base
from bajutsu.elements import screen_size_from_elements
from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.scenario import Step

# The default directional swipe travels a fraction of the screen, not a fixed coordinate count, so
# it scrolls a consistent proportion of any device regardless of its coordinate unit — iOS reports
# frames in points, Android in raw pixels, web in CSS pixels. A fixed count scrolls far less of a
# dense Android screen (2400px) than of an iOS one (~900pt), so a swipe sized for iOS barely moves an
# Android list; a screen fraction keeps the scroll reach at parity across backends (BE-0208). 0.125
# reproduces the previous 100-unit nudge on the historical 800-tall reference screen.
_SWIPE_FRACTION = 0.125  # default travel as a fraction of the screen when `amount` isn't given
_SWIPE_MARGIN = 4.0  # keep both gesture endpoints this far inside the screen edges


def _scroll_gesture(
    center: base.Point, direction: str, amount: float | None, screen: base.Point
) -> tuple[base.Point, base.Point]:
    """The (from, to) points for a directional swipe that travels `amount` of the screen.

    `amount` is a fraction of the screen (height for up/down, width for left/right); ``None`` uses
    the default fraction. The gesture *begins on* `center` when there is room, and travels a segment of
    that length in the direction (`up`/`left` toward the smaller coordinate), so a bigger `amount`
    scrolls proportionally further. Beginning on the element — rather than centering the travel
    across it — is what lets a swipe grab a small handle (e.g. a resize divider) it would otherwise
    straddle and miss. Only when a travel would overrun a screen edge does the segment slide back on
    (moving the start off `center` in that case), which keeps the travelled distance intact.
    """
    cx, cy = center
    sw, sh = screen
    vertical = direction in ("up", "down")
    dim = sh if vertical else sw
    dist = (amount if amount is not None else _SWIPE_FRACTION) * dim
    span = min(dist, max(0.0, dim - 2 * _SWIPE_MARGIN))
    anchor = cy if vertical else cx
    start = min(max(anchor, _SWIPE_MARGIN), dim - _SWIPE_MARGIN)
    end = start - span if direction in ("up", "left") else start + span
    if end < _SWIPE_MARGIN:
        start += _SWIPE_MARGIN - end
        end = _SWIPE_MARGIN
    elif end > dim - _SWIPE_MARGIN:
        start -= end - (dim - _SWIPE_MARGIN)
        end = dim - _SWIPE_MARGIN
    return ((cx, start), (cx, end)) if vertical else ((start, cy), (end, cy))


def _require_multi_touch(driver: base.Driver, action: str) -> None:
    """Fail clearly before a two-finger gesture if the actuator can't do multi-touch
    (e.g. a single-touch backend), rather than emitting a single-touch approximation that silently passes."""
    if base.Capability.MULTI_TOUCH not in driver.capabilities():
        raise base.UnsupportedAction(
            f"{action} requires a multi-touch capable backend; this backend supports single touch only"
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


@_handler("select_option")
def _do_select_option(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.select_option is not None
    driver.select_option(step.select_option.sel.as_selector(), step.select_option.option)


@_handler("clear")
def _do_clear(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.clear is not None
    sel = step.clear.into.as_selector()
    # Read the field's current length, then focus it and backspace exactly that many characters, so
    # the clear is agnostic to whatever it held (BE-0265). Nothing to delete on an empty field.
    current = base.resolve_unique(driver.query(), sel)["value"] or ""
    driver.tap(sel)
    if current:
        driver.delete_text(len(current))


@_handler("delete")
def _do_delete(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.delete is not None
    driver.tap(step.delete.into.as_selector())
    driver.delete_text(step.delete.count)


@_handler("select")
def _do_select(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.select is not None
    # `mode` is always "all" for now (BE-0265): focus the field, then platform select-all.
    driver.tap(step.select.into.as_selector())
    driver.select_all()


@_handler("copy_")
def _do_copy(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    # The "is a selection live?" precondition is enforced in `_do_action` (uniform across backends);
    # here we just actuate the platform copy of whatever `select` left selected.
    assert step.copy_ is not None
    driver.copy_selection()


def _directional_endpoints(
    driver: base.Driver, sel: base.Selector, direction: str, amount: float | None
) -> tuple[base.Point, base.Point]:
    """Resolve `sel` and compute the `(from, to)` a directional gesture on it travels.

    Shared by `swipe` (which scrolls the result) and `drag` (which pointer-drags it) — the endpoint
    math is identical; only what the driver does with them differs.
    """
    elements = driver.query()
    el = base.resolve_unique(elements, sel)
    return _scroll_gesture(
        base.frame_center(el["frame"]), direction, amount, screen_size_from_elements(elements)
    )


@_handler("swipe")
def _do_swipe(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.swipe is not None
    sw = step.swipe
    if sw.from_ is not None and sw.to is not None:
        # Coordinate form: a literal pointer drag (canvas / map pan / drag handle), realized as-is.
        driver.swipe(sw.from_, sw.to)
    elif sw.on is not None and sw.direction is not None:
        # Directional form means "scroll": route to `driver.scroll`, so the web backend can realize
        # it as a real scroll (wheel / touch) rather than a page-inert mouse drag (BE-0227). To drag
        # a grabbed element (a resize handle) in a direction instead, use the `drag` action.
        frm, to = _directional_endpoints(driver, sw.on.as_selector(), sw.direction, sw.amount)
        driver.scroll(frm, to)


@_handler("drag")
def _do_drag(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.drag is not None
    d = step.drag
    # A real pointer drag of the element in a direction (BE-0227): same endpoints as a directional
    # swipe, but `driver.swipe` (an actual drag) — so on web the grabbed element moves, where a
    # directional swipe would only wheel-scroll the page.
    frm, to = _directional_endpoints(driver, d.on.as_selector(), d.direction, d.amount)
    driver.swipe(frm, to)


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


@_handler("handle_system_alert")
def _do_handle_system_alert(
    driver: base.Driver, step: Step, _r: object, _c: object, _b: object
) -> None:
    # Tap an iOS SpringBoard permission prompt deterministically (BE-0316). Preflight has already
    # rejected the step on any backend without HANDLE_SYSTEM_ALERT; the driver raises UnsupportedAction
    # as the mid-run backstop, so no extra capability guard is needed here (mirrors select_option).
    assert step.handle_system_alert is not None
    hsa = step.handle_system_alert
    driver.handle_system_alert(hsa.sel.as_selector(), hsa.timeout)

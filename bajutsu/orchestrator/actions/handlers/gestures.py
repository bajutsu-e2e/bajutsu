"""Gesture and text-entry handlers: tap, double-tap, long-press, type, swipe, pinch, rotate."""

from __future__ import annotations

from collections.abc import Callable

from bajutsu.drivers import base
from bajutsu.elements import screen_size_from_elements
from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.orchestrator.actions.handlers._gesture_math import _scroll_gesture
from bajutsu.orchestrator.actions.handlers.scroll import scroll_until_tappable
from bajutsu.scenario import Step

# The recovery scroll's step bound: small, well under `scroll`'s own default of 15, for the first
# direction tried below. A later direction gets a multiple of this bound, since it must first undo
# the offset its predecessors left behind before it can make any net progress of its own. This is a
# safety net for the common case (a transient overlay, a sticky header/footer settling out of the
# way) — not a search. An author who already knows a target needs scrolling in a specific direction,
# through a specific container, still writes the explicit `scroll` action; this net only insures
# against the obstruction the author did not expect.
_TAP_RECOVERY_MAX_SCROLLS = 3

# Tried in this order: `down` first, since a bottom-anchored obstruction (a toast, a snackbar, a
# sticky footer) is the more common case, then `up` as a fallback for a top-anchored one (a sticky
# header) that `down` alone cannot clear — `down`'s content motion moves the target *toward* the top
# of the screen, so it drives a target stuck under a header further underneath it, never out.
_TAP_RECOVERY_DIRECTIONS = ("down", "up")


def _tap_with_recovery(
    actuate: Callable[[], None], driver: base.Driver, sel: base.Selector
) -> None:
    """Call `actuate()`; on `ElementNotTappable`, try a bounded scroll in each direction, then retry once.

    `scroll_until_tappable` (not `scroll_to_target`) is the reason this recovery does anything at
    all: an occluded target's frame center is already inside the viewport — that is exactly why it
    is occluded rather than off-screen — so a stop condition of mere on-screen presence would return
    immediately without a single scroll step.

    A single fixed direction cannot clear every obstruction: `down` (content moving toward the top
    of the screen) clears a bottom-anchored cover but drives a target under a top-anchored one
    further underneath it. `_TAP_RECOVERY_DIRECTIONS` tries `down`, then — only once `down` is
    exhausted without success — `up`. `up` starts from the offset `down`'s own steps left behind, so
    it needs to retrace that ground before it can make any net progress of its own; each direction's
    bound is therefore `_TAP_RECOVERY_MAX_SCROLLS * (i + 1)` rather than a flat, independent bound.
    The first direction to make `sel` tappable wins and the actuation is retried immediately. This is
    still a bounded safety net, not a search in an author-chosen direction: an author who already
    knows a target needs scrolling through a specific container still writes the explicit `scroll`
    action.

    Any failure along the recovery path — both directions' scroll bounds exhausted while still not
    tappable, or the retried `actuate()` finding the target still not tappable — surfaces as a single
    `ElementNotTappable`. The first attempt's own exception (which names what covered the target,
    via `base.raise_if_covered`) is interpolated into that message rather than dropped, so the
    fact a CI log needs to avoid reproducing the screen by hand survives; the last direction's scroll
    failure that triggered the recovery is chained (`raise … from`) alongside it. It never falls back
    to the misleading `ElementNotFound` a scroll timeout would otherwise raise.
    """
    try:
        actuate()
        return
    except base.ElementNotTappable as obstruction_exc:
        obstruction = obstruction_exc
    exhausted: Exception | None = None
    for i, direction in enumerate(_TAP_RECOVERY_DIRECTIONS):
        # A later direction starts from the offset its predecessors left behind, so it has to undo
        # those steps before it makes any net progress of its own — hence the widened bound.
        try:
            scroll_until_tappable(driver, sel, direction, None, _TAP_RECOVERY_MAX_SCROLLS * (i + 1))
        except base.ElementNotFound as exc:
            exhausted = exc
            continue
        try:
            actuate()
        except base.ElementNotTappable as exc:
            # The retry raced: the cover re-settled between the stop check and the actuation.
            # Keep the newer, more accurate obstruction and let the next direction try.
            obstruction = exc
            exhausted = base.ElementNotTappable(
                f"scroll: {sel!r} became tappable but was covered again on the retry"
            )
            continue
        return
    assert exhausted is not None
    raise base.ElementNotTappable(
        f"still not tappable after a bounded scroll attempt: {obstruction}"
    ) from exhausted


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
    sel = step.tap.as_selector()
    _tap_with_recovery(lambda: driver.tap(sel), driver, sel)


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
    sel = step.double_tap.as_selector()
    _tap_with_recovery(lambda: driver.double_tap(sel), driver, sel)


@_handler("long_press")
def _do_long_press(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.long_press is not None
    sel = step.long_press.sel.as_selector()
    duration = step.long_press.duration
    _tap_with_recovery(lambda: driver.long_press(sel, duration), driver, sel)


@_handler("type")
def _do_type(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.type is not None
    if step.type.into is not None:
        sel = step.type.into.as_selector()
        _tap_with_recovery(lambda: driver.tap(sel), driver, sel)
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
    _tap_with_recovery(lambda: driver.tap(sel), driver, sel)
    if current:
        driver.delete_text(len(current))


@_handler("delete")
def _do_delete(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.delete is not None
    sel = step.delete.into.as_selector()
    _tap_with_recovery(lambda: driver.tap(sel), driver, sel)
    driver.delete_text(step.delete.count)


@_handler("select")
def _do_select(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.select is not None
    # `mode` is always "all" for now (BE-0265): focus the field, then platform select-all.
    sel = step.select.into.as_selector()
    _tap_with_recovery(lambda: driver.tap(sel), driver, sel)
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

    The read is the driver's actuation-grade one where it offers a distinct one
    (`SettledReadProvider`), because this is the only selector-addressed actuation whose target is
    resolved above the driver: `tap` and the rest hand the driver a selector, so the driver settles
    the tree itself, while this hands it two coordinates it can no longer trace back to an element.
    On Android a bare read taken shortly after a gesture still describes the pre-gesture screen, so
    two consecutive directional swipes would anchor the second one on the first one's starting
    frames. A backend that does not implement the protocol keeps its single `query()` unchanged.
    """
    elements = (
        driver.settled_query() if isinstance(driver, base.SettledReadProvider) else driver.query()
    )
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
    if hsa.sel is None:
        # The `prompt`/`choice` form is resolved against the run's locale before dispatch (BE-0320).
        # Reaching here means a caller ran the step without that resolution (`record`'s replay), and
        # the label it needs is unknowable from the step alone — fail loudly rather than skip a tap.
        raise base.UnsupportedAction(
            "handleSystemAlert prompt/choice needs the run's locale to resolve its button label; "
            "this caller does not supply one — name the button with sel.label instead (BE-0320)"
        )
    driver.handle_system_alert(hsa.sel.as_selector(), hsa.timeout)

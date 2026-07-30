"""The `scroll` action: a bounded, non-inertial scroll-and-re-query until a target is on-screen (BE-0326).

Unlike `swipe`'s directional form (a single gesture), `scroll` is a condition wait: it scrolls one
non-inertial step, re-queries the tree, and stops when `to` resolves with its frame center inside the
viewport. It fails deterministically — no fixed `sleep` — when it spends `maxScrolls` steps, or once a
step no longer changes the scrolled region and re-reading confirms the region has stopped (it has
bottomed out and the target is not there). The re-read is what keeps a backend whose tree lags the
gesture it already applied (`ReadLagProvider`; Android) from reporting a late tree as the end of the
content. The loop lives in its own handler module so the driver conformance suite (BE-0114) can
drive it against each real backend, and so parallel work touches a focused file rather than the
shared gestures monolith.
"""

from __future__ import annotations

import time

from bajutsu.drivers import base
from bajutsu.elements import screen_size_from_elements
from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.orchestrator.actions.handlers.gestures import _scroll_gesture
from bajutsu.scenario import Step

# `scroll.direction` names the direction the *content* moves; the finger pushes the opposite way, so
# revealing below-the-fold content (`down`) is a finger swipe *up*. This inversion — the reason
# `scroll` is a distinct verb from `swipe`, whose `direction` is the finger's — is applied here once.
_CONTENT_TO_FINGER = {"down": "up", "up": "down", "right": "left", "left": "right"}

# Per-step travel as a fraction of the viewport. Larger than `swipe`'s 0.125 default: a non-inertial
# step carries no momentum, so each step must cover real ground, while ~40% overlap between steps
# keeps a target straddling the fold from slipping between two consecutive viewports unqueried.
_STEP_FRACTION = 0.6

# Cadence for re-reading a region that looks unchanged, within the backend's own `read_lag` budget.
# Small enough that a tree catching up is noticed promptly, and irrelevant on a backend that reports no
# lag — there the re-read loop never runs at all.
_CHANGE_POLL_S = 0.1


def _viewport(driver: base.Driver, elements: list[base.Element]) -> base.Point:
    """The true viewport size for the stop condition.

    A `ViewportProvider` (web, fake) reports it directly, since its queried tree keeps off-screen
    nodes and so overshoots the viewport. Any other backend queries only on-screen elements, so the
    screen extent is the viewport.
    """
    if isinstance(driver, base.ViewportProvider):
        return driver.viewport()
    return screen_size_from_elements(elements)


def _center_in_viewport(frame: base.Frame, viewport: base.Point) -> bool:
    """Whether the frame's center — the point a following coordinate `tap` aims at — is on-screen."""
    cx, cy = base.frame_center(frame)
    vw, vh = viewport
    return 0.0 <= cx <= vw and 0.0 <= cy <= vh


def _region_signature(
    elements: list[base.Element], within: base.Selector | None
) -> tuple[tuple[str | None, str | None, base.Frame], ...]:
    """A comparable snapshot of the scrolled region, for end-of-content detection.

    The region is the whole tree, or — with `within` — the elements spatially inside the scrollable
    container. A scroll that moves content changes some frame; a scroll against a bottomed-out region
    changes nothing, so two identical signatures mean the region has ended (BE-0259: reuses the tree
    the loop already queried, adding no `query()`).
    """
    if within is None:
        region = elements
    else:
        scopes = [c["frame"] for c in base.find_all(elements, within)]
        region = [el for el in elements if any(base._contains(s, el["frame"]) for s in scopes)]
    return tuple((el["identifier"], el["label"], el["frame"]) for el in region)


def _step_endpoints(
    elements: list[base.Element], direction: str, within: base.Selector | None, viewport: base.Point
) -> tuple[base.Point, base.Point]:
    """The `(from, to)` for one non-inertial scroll step, anchored on the `within` container or screen.

    Reuses `swipe`'s clamped endpoint math (`_scroll_gesture`) after translating the content
    `direction` into the finger direction it expects; `within` moves only the anchor, so the gesture
    starts inside the container the scenario named rather than on an outer surface.
    """
    if within is not None:
        anchor = base.frame_center(base.resolve_unique(elements, within)["frame"])
    else:
        anchor = (viewport[0] / 2, viewport[1] / 2)
    return _scroll_gesture(anchor, _CONTENT_TO_FINGER[direction], _STEP_FRACTION, viewport)


def _resolve_target(elements: list[base.Element], to: base.Selector) -> base.Element | None:
    """The uniquely resolved target, or None when it is absent from the current tree.

    An ambiguous selector propagates (fail loudly, determinism first): the loop never taps whichever
    matched first. Absence is the ordinary "not yet scrolled into view" case, so it returns None
    rather than raising, letting the loop scroll and re-query.
    """
    try:
        return base.resolve_unique(elements, to)
    except base.ElementNotFound:
        return None


def _region_after_step(
    driver: base.Driver,
    before: tuple[tuple[str | None, str | None, base.Frame], ...],
    within: base.Selector | None,
) -> list[base.Element]:
    """The tree once the step's result shows in it, or the last read once the backend's lag is spent.

    Returns as soon as the region differs from `before`, so a step that visibly landed costs exactly
    one read. Only an unchanged region re-reads, and only for as long as the backend admits its reads
    can lag (`ReadLagProvider`) — a backend that reports no lag returns its first read, leaving the
    end-of-content failure as immediate as it was. Whether this ever reports a change is what tells the
    caller a late tree from a region that really has stopped.
    """
    elements = driver.query()
    lag = driver.read_lag() if isinstance(driver, base.ReadLagProvider) else 0.0
    deadline = time.monotonic() + lag
    while _region_signature(elements, within) == before and time.monotonic() < deadline:
        time.sleep(_CHANGE_POLL_S)
        elements = driver.query()
    return elements


def scroll_to_target(
    driver: base.Driver,
    to: base.Selector,
    direction: str,
    within: base.Selector | None,
    max_scrolls: int,
) -> None:
    """Scroll until `to` resolves on-screen, or fail at a bound (BE-0326).

    Raises:
        ElementNotFound: `to` is still off-screen after `max_scrolls` steps, or the scrolled region
            bottomed out with `to` absent — a step no longer changed it, and re-reading within the
            backend's `read_lag` budget never saw it move.
        AmbiguousSelector: `to` resolves to more than one element — propagated from `resolve_unique`
            rather than tapping whichever matched first.
    """
    elements = driver.query()
    viewport = _viewport(driver, elements)
    scrolls = 0
    while True:
        target = _resolve_target(elements, to)
        if target is not None and _center_in_viewport(target["frame"], viewport):
            return
        if scrolls >= max_scrolls:
            raise base.ElementNotFound(
                f"scroll: {to!r} not on-screen after {max_scrolls} scroll(s)"
            )
        before = _region_signature(elements, within)
        frm, dest = _step_endpoints(elements, direction, within, viewport)
        driver.scroll(frm, dest)
        scrolls += 1
        elements = _region_after_step(driver, before, within)
        if _region_signature(elements, within) == before:
            raise base.ElementNotFound(
                f"scroll: {to!r} not found; the region stopped changing (end of content)"
            )


@_handler("scroll")
def _do_scroll(driver: base.Driver, step: Step, _r: object, _c: object, _b: object) -> None:
    assert step.scroll is not None
    s = step.scroll
    scroll_to_target(
        driver,
        s.to.as_selector(),
        s.direction,
        s.within.as_selector() if s.within is not None else None,
        s.max_scrolls,
    )

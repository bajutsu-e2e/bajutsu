"""The `scroll` action: a bounded, non-inertial scroll-and-re-query until a target is on-screen (BE-0326).

Unlike `swipe`'s directional form (a single gesture), `scroll` is a condition wait: it scrolls one
non-inertial step, re-queries the tree, and stops when `to` resolves with its frame center inside the
viewport. It fails deterministically — no fixed `sleep` — when it spends `maxScrolls` steps, or once
two consecutive reads *show* that the region has stopped (it has bottomed out and the target is not
there).

What the loop may conclude from a pair of reads is the subject of BE-0329. A backend that clips an
element's frame to the visible area (Android) reports the same frame while the content scrolls behind
an element taller than the screen, so "nothing changed" is not by itself proof that the region ended.
The loop therefore ends the region only on evidence: an element it has watched move is still there and
standing still, or nothing in the region is clipped at all, or — where the tree can show neither —
the rendered screen did not change either. When both reads have something in view and nothing the
first read held in view is on screen at all in the second — not even partly — the opposite error has
happened: a step carried the target past the viewport, so the loop shrinks the step and looks back
instead of reporting the target missing.

The loop lives in its own handler module so the driver conformance suite (BE-0114) can drive it
against each real backend, and so parallel work touches a focused file rather than the shared gestures
monolith.
"""

from __future__ import annotations

import hashlib
import logging
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import NamedTuple

from bajutsu.drivers import base
from bajutsu.elements import screen_size_from_elements
from bajutsu.orchestrator.actions._registry import _handler
from bajutsu.orchestrator.actions.handlers.gestures import _SWIPE_FRACTION, _scroll_gesture
from bajutsu.scenario import Step

_logger = logging.getLogger(__name__)

# `scroll.direction` names the direction the *content* moves; the finger pushes the opposite way, so
# revealing below-the-fold content (`down`) is a finger swipe *up*. This inversion — the reason
# `scroll` is a distinct verb from `swipe`, whose `direction` is the finger's — is applied here once.
_CONTENT_TO_FINGER = {"down": "up", "up": "down", "right": "left", "left": "right"}

# Reversing a content direction (for the look-back step below) is the same involution as translating
# one into a finger direction, so the single mapping above serves both readings.
_REVERSE = _CONTENT_TO_FINGER

# The frame index the scroll axis runs along: `y` for a vertical scroll, `x` for a horizontal one.
# Every position comparison is scoped to that axis, so a row that shifts sideways is not read as
# scrolling.
_AXIS = {"down": 1, "up": 1, "right": 0, "left": 0}

# Per-step travel as a fraction of the viewport. Larger than `swipe`'s 0.125 default: a non-inertial
# step carries no momentum, so each step must cover real ground, while the remaining ~40% overlap
# between steps keeps a target straddling the fold from slipping between two consecutive viewports
# unqueried. That overlap holds only while the content travels no further than the gesture asks —
# a property no backend measures, so the loop also detects a step that shared nothing with the one
# before it and shrinks the fraction toward the floor below (BE-0329).
_STEP_FRACTION = 0.6

# Floor for the shrunk step: `swipe`'s own default travel. Below it a step would move so little that a
# clipped frame is even less likely to reveal motion, so the loop fails naming the overshoot instead.
_MIN_STEP_FRACTION = _SWIPE_FRACTION

# Cadence for re-reading a region that looks unchanged, within the backend's own `read_lag` budget,
# and for re-capturing a screen that is still being drawn. Small enough that a tree or a render
# catching up is noticed promptly, and irrelevant on a backend that reports no lag — there the
# re-read loop never runs at all.
_CHANGE_POLL_S = 0.1

# Ceiling on waiting for the rendered screen to stop changing before its checksum is trusted. Only
# ever spent on a step the element tree could not judge, and generous relative to a non-inertial
# gesture, which leaves nothing animating: a screen still moving after this is one whose own motion
# (a spinner, a caret) the checksum cannot tell from scrolling, so the loop declines to judge it.
_RENDER_SETTLE_S = 1.0

# An element's identity across two reads. The label is part of it so a row carrying no identifier is
# still trackable, but a label is never *evidence*: a clock or a spinner whose text ticks changes this
# key, which drops the element from both reads' tracked sets rather than counting as motion.
_Key = tuple[str | None, str | None]


class _RegionView(NamedTuple):
    """What one read says about the scrolled region, in the terms the step decisions need (BE-0329).

    Three projections, because the decisions ask three different questions of one read.

    `positions` is the scroll-axis position of every unambiguously named element the bounds do not cut
    off — whether it is in view or entirely outside, since either way the frame it reports belongs to
    the content. Motion is read from these. `in_view` narrows that to the elements strictly inside the
    bounds, the ones a step can be said to have carried away, and the only ones whose stillness the
    loop will take as evidence. `identities` is the region's identifier multiset, which changes when
    rows enter or leave a lazy tree even though no position moved. `cut_off` holds the frames the bounds
    cut off, whose true extent is therefore unknown: each one may reach far past the bound and be
    scrolling content the read cannot show. `visible` is every key the bounds show any part of, in view
    or cut off, which is how two reads are asked whether their viewports overlapped at all. `axis` is
    kept because the decisions compare extents along it, not only positions.
    """

    axis: int
    positions: dict[_Key, float]
    in_view: dict[_Key, base.Frame]
    identities: Counter[str | None]
    cut_off: tuple[base.Frame, ...]
    visible: frozenset[_Key]


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


def _region(
    elements: list[base.Element], within: base.Selector | None, viewport: base.Point
) -> tuple[list[base.Element], base.Frame]:
    """The elements the loop compares across a step, and the rectangle they are clipped to.

    Without `within` the region is the whole tree, clipped to the viewport. With it, the region is the
    elements spatially inside that container, clipped to its frame — and the container itself is
    excluded, because its own edges sit *on* those bounds and would make every such region look
    partly clipped.

    A read that does not show exactly one container yields an empty region rather than raising. This
    projection runs on every read, including the ones `_region_after_step` polls while a lagging tree
    catches up, so a read that momentarily omits the container must decide nothing instead of failing
    the scroll; `_step_endpoints` is what resolves the container uniquely and fails loudly when it is
    really absent or ambiguous.
    """
    if within is None:
        return elements, (0.0, 0.0, viewport[0], viewport[1])
    containers = base.find_all(elements, within)
    if len(containers) != 1:
        return [], (0.0, 0.0, 0.0, 0.0)
    container = containers[0]
    bounds = container["frame"]
    return [
        el for el in elements if el is not container and base._contains(bounds, el["frame"])
    ], bounds


def _unclipped(frame: base.Frame, bounds: base.Frame, axis: int) -> bool:
    """Whether the frame's two edges along `axis` both sit strictly inside the region bounds.

    A backend that clips an element to the visible area reports a clipped edge exactly *at* the
    bounds, so only an element strictly inside reports a position belonging to the content rather than
    to the screen. An element entirely outside the bounds fails this too, deliberately: a clipping
    backend would never have reported it, so the loop must not rest a decision on one.
    """
    lo, size = frame[axis], frame[axis + 2]
    b_lo, b_size = bounds[axis], bounds[axis + 2]
    return lo > b_lo and lo + size < b_lo + b_size


def _cut_off(frame: base.Frame, bounds: base.Frame, axis: int) -> bool:
    """Whether the element reaches or crosses a region bound, so its true extent is unknown.

    Distinct from `_unclipped`'s negation, which also covers an element lying wholly outside the
    bounds: that one is simply out of view and can hide nothing, while an element cut off by a bound
    may reach far past it.
    """
    lo, size = frame[axis], frame[axis + 2]
    b_lo, b_size = bounds[axis], bounds[axis + 2]
    overlaps = lo < b_lo + b_size and lo + size > b_lo
    return overlaps and not _unclipped(frame, bounds, axis)


def _region_view(
    elements: list[base.Element], within: base.Selector | None, viewport: base.Point, axis: int
) -> _RegionView:
    """Project one read onto the region terms above (BE-0259: reuses the tree, adding no `query()`)."""
    region, bounds = _region(elements, within, viewport)
    positions: dict[_Key, float] = {}
    in_view: dict[_Key, base.Frame] = {}
    ambiguous: set[_Key] = set()
    cut_off: list[base.Frame] = []
    visible: set[_Key] = set()
    for el in region:
        frame = el["frame"]
        key = (el["identifier"], el["label"])
        if _cut_off(frame, bounds, axis):
            cut_off.append(frame)
            visible.add(key)
            continue
        if key in ambiguous:
            continue
        if key in positions:
            # Two elements share this key, so it names no single position: drop it rather than take
            # whichever came first (the same discipline `resolve_unique` applies to actuation).
            ambiguous.add(key)
            del positions[key]
            in_view.pop(key, None)
            continue
        positions[key] = frame[axis]
        if _unclipped(frame, bounds, axis):
            in_view[key] = frame
            visible.add(key)
    return _RegionView(
        axis=axis,
        positions=positions,
        in_view=in_view,
        identities=Counter(el["identifier"] for el in region),
        cut_off=tuple(cut_off),
        visible=frozenset(visible),
    )


def _moved(before: _RegionView, after: _RegionView) -> set[_Key]:
    """The elements whose scroll-axis position changed across the step.

    Each one is anchored to the content rather than to the screen, so the loop remembers them: an
    element it has watched move is the evidence the end-of-content decision rests on.
    """
    shared = before.positions.keys() & after.positions.keys()
    return {k for k in shared if before.positions[k] != after.positions[k]}


def _region_moved(before: _RegionView, after: _RegionView) -> bool:
    """Whether the step moved the content, by any of the three ways a pair of reads can show it.

    A position travelled; rows entered or left the tree; or something crossed a region bound, leaving
    the in-view set and taking its position with it. A changed label is none of them — a ticking clock
    is not scrolling — which is why the identity multiset counts identifiers only, why a label change
    merely swaps one key for another, and why the third test counts the in-view set rather than
    comparing its keys.
    """
    return (
        bool(_moved(before, after))
        or before.identities != after.identities
        or len(before.in_view) != len(after.in_view)
    )


def _stopped(before: _RegionView, after: _RegionView, movers: set[_Key]) -> str | None:
    """The evidence that the content stands still, or None when neither kind is there.

    Call only for a step that did not move the region. Two kinds of evidence end it.

    Either the bounds cut nothing off, so no frame's extent is in doubt and an unchanged read of
    something means what it says. An empty region is not that: a read describing nothing is not an
    observation. On a tree that reports a window or a root view spanning the screen, nothing meets this,
    which is why the mover below carries the real work.

    Or an element the loop has watched move is still there, in view, and no cut-off element could be a
    scroll container it does not belong to. A mover is anchored to the content it moved with, so it
    speaks for that content and for no other; a cut-off element speaks against it only when it is
    *larger* along the scroll axis and does not contain it, since only something larger could hold
    content of its own that the mover is outside of. That is what separates the list a mover sits in
    (larger, containing) and the neighbouring row the screen edge cut off (no larger, so no container)
    from a collapsing app bar's list: chrome that shifts once and then pins is a mover by then, and the
    list it never belonged to is exactly a larger cut-off element that excludes it. A position-fixed
    element never enters the mover set at all.
    """
    if before.in_view and not before.cut_off:
        return "the region's bounds cut nothing off, so an unchanged read cannot be hiding motion"
    size = before.axis + 2
    for key in movers & (before.in_view.keys() & after.in_view.keys()):
        frame = before.in_view[key]
        if all(c[size] <= frame[size] or base._contains(c, frame) for c in before.cut_off):
            return "an element the loop had watched move is still there and has stopped"
    return None


def _overshot(before: _RegionView, after: _RegionView) -> bool:
    """Whether the step advanced at least a full viewport, so the target may have passed unseen.

    Nothing that was in view before is on screen at all after — not even partly. Surviving *partly* is
    what separates a step that flung from an ordinary step on a screen showing one element at a time: a
    carousel card, or a row nearly as tall as the viewport, is replaced in the in-view set by its
    neighbour after far less than a viewport of travel, and the card it replaced is still there at the
    edge. Only when the previous viewport's contents have left the screen entirely can the target have
    passed between two reads.

    Both reads must also have something in view for the inference to hold: a read with nothing in view
    is a region the loop cannot observe (an element taller than the viewport covering it), which is the
    case above where the tree shows nothing, not evidence of travel — reading it as overshoot would fire
    on the legitimate step that scrolls into such an element, and on the one that scrolls back out.
    """
    return (
        bool(before.in_view) and bool(after.in_view) and not (before.in_view.keys() & after.visible)
    )


def _render_digest(driver: base.Driver) -> str | None:
    """A checksum of the screen as drawn, or None when this backend cannot supply one.

    The fallback evidence for a region whose every element is clipped: the tree cannot show that such
    a region moved, and pixels can — a `screencap` checksum is what proved it during the Android
    investigation. Deliberately not `screenshots.screenshot_bytes`, which right-sizes the image for a
    model and is documented as serving the AI paths only; this needs the capture untouched, and it
    stays plain arithmetic on bytes, so no model enters the `run` path.

    A capture that fails is reported as None rather than raised, at a level that matches what it means:
    a *failed* capture is a warning, while a capture that simply wrote nothing is expected on the fake
    and headless drivers, which record a screenshot without producing a file, so it logs at debug.
    Either way the checksum only ever *restores* an immediate failure that the loop would otherwise
    reach by spending `maxScrolls`, so a backend that cannot capture keeps the slower deterministic
    outcome instead of failing the scroll for a reason unrelated to the scenario. An empty capture must
    not become a digest, or a backend writing zero bytes would report the same checksum on every step
    and end a region that is still moving. A crashed backend is the one thing this does not absorb —
    that is the runner's to recover from (`BackendCrashError`).
    """
    if base.Capability.SCREENSHOT not in driver.capabilities():
        return None
    path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
            path = tmp.name
        driver.screenshot(path)
        data = Path(path).read_bytes()
        if not data:
            _logger.debug(
                "scroll: %s recorded a screenshot without writing one, so the rendered screen cannot "
                "judge this step",
                driver.name,
            )
            return None
        return hashlib.sha256(data).hexdigest()
    except base.BackendCrashError:
        raise
    except Exception as exc:
        _logger.warning(
            "scroll: screen capture failed, so the region cannot be judged: %s", exc, exc_info=True
        )
        return None
    finally:
        if path is not None:
            Path(path).unlink(missing_ok=True)


def _settled_render(driver: base.Driver) -> str | None:
    """The screen's checksum once two consecutive captures agree, or None if it never settles.

    A condition wait, not a fixed `sleep`: it returns as soon as the drawing has stopped, and gives up
    at `_RENDER_SETTLE_S` on a screen whose own animation keeps the checksum moving — there a capture
    cannot tell scrolling from a spinner, so the loop declines to conclude anything from it. The two
    captures being compared are always a poll apart, so two frames of one animation cannot pass for a
    settled screen just because the backend captures faster than it draws.
    """
    digest = _render_digest(driver)
    if digest is None:
        return None
    deadline = time.monotonic() + _RENDER_SETTLE_S
    while True:
        time.sleep(_CHANGE_POLL_S)
        previous, digest = digest, _render_digest(driver)
        if digest is None:
            return None
        if digest == previous:
            return digest
        if time.monotonic() >= deadline:
            return None


def _step_endpoints(
    elements: list[base.Element],
    direction: str,
    within: base.Selector | None,
    viewport: base.Point,
    fraction: float,
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
    return _scroll_gesture(anchor, _CONTENT_TO_FINGER[direction], fraction, viewport)


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
    before: _RegionView,
    within: base.Selector | None,
    viewport: base.Point,
    axis: int,
) -> list[base.Element]:
    """The tree once the step's result shows in it, or the last read once the backend's lag is spent.

    Returns as soon as the region has moved, so a step that visibly landed costs exactly one read.
    Only a region that still looks unmoved re-reads, and only for as long as the backend admits its
    reads can lag (`ReadLagProvider`) — a backend that reports no lag returns its first read, leaving
    the end-of-content failure as immediate as it was.

    A region that holds elements but reports no position for any of them skips the wait entirely
    (BE-0329): waiting cannot produce a signal the tree is unable to show, and spending the whole
    budget on every such step would make the handful of steps behind an element taller than the screen
    cost seconds each. The rendered screen is what judges that case instead. A region with no elements
    at all still waits, because there the wait *can* produce a signal — a read of nothing is the shape
    a wedged accessibility bridge or a mid-transition dump takes, and the elements arriving is exactly
    the change this loop exists to notice.
    """
    elements = driver.query()
    if not before.positions and before.identities:
        return elements
    lag = driver.read_lag() if isinstance(driver, base.ReadLagProvider) else 0.0
    deadline = time.monotonic() + lag
    while time.monotonic() < deadline:
        view = _region_view(elements, within, viewport, axis)
        # A read that describes nothing where the last one described something is a degraded read, not
        # a result: an emptied region satisfies no decision, so treat it as "not yet" and read again.
        if (view.identities or not before.identities) and _region_moved(before, view):
            break
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
    """Scroll until `to` resolves on-screen, or fail at a bound (BE-0326, BE-0329).

    Raises:
        ElementNotFound: `to` is still off-screen after `max_scrolls` steps — the message says whether
            the loop could observe the region's motion at all — or two reads showed the region
            standing still with `to` absent (it bottomed out), or a step overshot the viewport even at
            the smallest step the loop will take.
        AmbiguousSelector: `to` resolves to more than one element — propagated from `resolve_unique`
            rather than tapping whichever matched first.
    """
    axis = _AXIS[direction]
    elements = driver.query()
    viewport = _viewport(driver, elements)
    # Elements watched moving during this call. It outlives each step because the evidence does: a row
    # that scrolled ten steps ago is still anchored to the content when it comes to rest.
    movers: set[_Key] = set()
    fraction = _STEP_FRACTION
    previous: _RegionView | None = None
    # Settled checksum from the last step no read could judge, kept for comparison against the next
    # such step — two of them in a row with the same render are the end of the content.
    digest: str | None = None
    reversed_step = False  # the step just taken was a look-back, which may not trigger another
    reverse_next = False
    unobserved = False  # the last step's motion could be neither seen nor ruled out
    scrolls = 0
    while True:
        target = _resolve_target(elements, to)
        if target is not None and _center_in_viewport(target["frame"], viewport):
            return
        view = _region_view(elements, within, viewport, axis)
        # Judge the step that produced this read — but only now that the target is known absent, so a
        # step that revealed it is never second-guessed.
        if previous is not None:
            movers |= _moved(previous, view)
            if not reversed_step and _overshot(previous, view):
                if fraction <= _MIN_STEP_FRACTION:
                    raise base.ElementNotFound(
                        f"scroll: {to!r} not found; a step overshot the region — nothing in view "
                        f"survived it — even at the smallest step ({_MIN_STEP_FRACTION} of the "
                        "viewport)"
                    )
                # Look at the span that passed, and observe the content more finely from here on.
                fraction = max(fraction / 2, _MIN_STEP_FRACTION)
                reverse_next = True
                digest, unobserved = None, False
            elif _region_moved(previous, view):
                digest, unobserved = None, False
            elif (evidence := _stopped(previous, view, movers)) is not None:
                raise base.ElementNotFound(
                    f"scroll: {to!r} not found; the region did not change and {evidence} "
                    "(end of content)"
                )
            else:
                # The tree cannot say whether the region moved, so ask the rendered screen, which
                # can: a checksum unchanged across a step is the end of the content, and one that
                # changed is the motion the tree could not show. The capture happens only here, on a
                # step no read could judge, so a scroll that never hits this case pays nothing — and
                # the second such step in a row is what yields the verdict, which is why the digest is
                # carried forward rather than taken before every gesture.
                settled = _settled_render(driver)
                if settled is not None and digest is not None:
                    if settled == digest:
                        raise base.ElementNotFound(
                            f"scroll: {to!r} not found; neither the region nor the rendered screen "
                            "changed across two steps (end of content)"
                        )
                    unobserved = False
                else:
                    unobserved = True
                digest = settled
        if scrolls >= max_scrolls:
            # Only ever says what was actually the case: `unobserved` is the last step's outcome, and
            # it means neither source of evidence answered — not that any particular one was tried.
            detail = (
                "; whether the region moved could not be observed on the last step, from either its "
                "element tree or the rendered screen"
                if unobserved
                else ""
            )
            raise base.ElementNotFound(
                f"scroll: {to!r} not on-screen after {max_scrolls} scroll(s){detail}"
            )
        step_direction = _REVERSE[direction] if reverse_next else direction
        frm, dest = _step_endpoints(elements, step_direction, within, viewport, fraction)
        driver.scroll(frm, dest)
        scrolls += 1
        reversed_step, reverse_next = reverse_next, False
        previous = view
        elements = _region_after_step(driver, previous, within, viewport, axis)


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

"""The `scroll` action (BE-0326, BE-0329): schema parse/validation and the non-inertial handler loop.

The handler runs over a `FakeDriver` in scrollable mode — a real viewport + clamped offset, no device
— so the stop condition (`to`'s center in the viewport), the `maxScrolls` bound, the end-of-content
fail-fast, and `within` scoping are all exercised on the fast gate. BE-0329's decisions are pure
functions over the element lists the loop already holds, so what the loop may conclude from a pair of
reads is tested directly, and the cases a real backend produces — a frame clipped to the screen, a
tree that lags its gesture, a step that flings — are modelled by the doubles below.
"""

from __future__ import annotations

import hashlib
import itertools
from pathlib import Path

import pytest

from bajutsu.common.orchestrator.actions._registry import _do_action, _step_label
from bajutsu.common.orchestrator.actions.handlers import scroll
from bajutsu.common.orchestrator.actions.handlers.scroll import (
    _region_moved,
    _region_view,
    _render_digest,
    _settled_render,
    _unclipped,
)
from bajutsu.common.scenario import Scroll, Step, load_scenarios
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver

_ROW_H = 90.0
_VIEWPORT: base.Point = (300.0, 800.0)
# Rows start below the viewport's top edge. An element flush with the region bounds is
# indistinguishable from one a backend clipped to them (BE-0329), so a list meant to stand for an
# ordinary, fully visible one has to sit strictly inside — which is what lets the loop trust an
# unchanged read as the end of the content.
_TOP_INSET = 10.0


def _scroll_gestures(driver: FakeDriver) -> list[tuple[base.Point, base.Point]]:
    """Each scroll the driver performed, as its (from, to) points.

    `FakeDriver.actions` logs `(kind, arg)` with `arg` typed `object`, so the read asserts the shape
    rather than casting it away: a scroll logged as anything else fails the test loudly (BE-0388).
    """
    gestures: list[tuple[base.Point, base.Point]] = []
    for kind, arg in driver.actions:
        if kind != "scroll":
            continue
        assert isinstance(arg, tuple) and len(arg) == 2
        gestures.append(arg)
    return gestures


def _row(i: int, *, prefix: str = "row", x: float = 0.0, w: float = 280.0) -> base.Element:
    return {
        "identifier": f"{prefix}.{i}",
        "label": f"{prefix} {i}",
        "traits": [],
        "value": None,
        "frame": (x, _TOP_INSET + i * _ROW_H, w, _ROW_H),
        "nativeZ": None,
    }


def _el(identifier: str | None, frame: base.Frame, *, label: str | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": [],
        "value": None,
        "frame": frame,
        "nativeZ": None,
    }


def _scrollable(rows: list[base.Element]) -> FakeDriver:
    return FakeDriver(screen=rows, viewport=_VIEWPORT)


def _scroll_count(driver: FakeDriver) -> int:
    return sum(1 for kind, _ in driver.actions if kind == "scroll")


# --- schema ---


def test_parse_scroll_defaults() -> None:
    steps = load_scenarios("- name: s\n  steps:\n    - scroll: { to: { id: a } }\n")[0].steps
    assert steps[0].scroll is not None
    s = steps[0].scroll
    assert s.to.id == "a"
    assert s.direction == "down"  # default reveals below-the-fold content
    assert s.within is None
    assert s.amount is None  # omitted, the loop keeps its own default step (BE-0400)
    assert s.max_scrolls == 15  # default bound


def test_parse_scroll_all_fields() -> None:
    steps = load_scenarios(
        "- name: s\n  steps:\n"
        "    - scroll: { to: { label: Log out }, direction: up, "
        "within: { id: list }, amount: 0.2, maxScrolls: 25 }\n"
    )[0].steps
    s = steps[0].scroll
    assert s is not None
    assert s.direction == "up" and s.within is not None and s.within.id == "list"
    assert s.amount == 0.2
    assert s.max_scrolls == 25  # camelCase alias maps to the snake_case attribute


def test_max_scrolls_must_be_positive() -> None:
    with pytest.raises(ValueError, match=r"greater than 0"):
        Scroll.model_validate({"to": {"id": "a"}, "maxScrolls": 0})


@pytest.mark.parametrize("amount", [0.0, -0.5, 1.5])
def test_scroll_amount_must_be_a_viewport_fraction(amount: float) -> None:
    # The same range `swipe` and `drag` validate for their own `amount` (BE-0400): a fraction, so it
    # reads the same on every screen size, and a positive one, so a step always asks for some travel.
    with pytest.raises(ValueError, match=r"within 0\.\.1"):
        Scroll.model_validate({"to": {"id": "a"}, "amount": amount})


def test_scroll_amount_accepts_a_full_viewport() -> None:
    # The range deliberately does not stop at the default 0.6: BE-0329's overshoot detection bounds a
    # large step for every fraction, so a screen that scrolls unusually slowly may ask for a full one.
    assert Scroll.model_validate({"to": {"id": "a"}, "amount": 1.0}).amount == 1.0


def test_direction_literal_is_validated() -> None:
    with pytest.raises(ValueError, match=r"direction"):
        Scroll.model_validate({"to": {"id": "a"}, "direction": "sideways"})


def test_step_label_surfaces_the_scroll_target() -> None:
    # The progress / step label must name `to` (a scroll's primary target), like every other action
    # surfaces its target — not fall back to a bare "scroll".
    step = Step(scroll=Scroll.model_validate({"to": {"id": "notice.row.20"}, "direction": "down"}))
    assert _step_label(step, "scroll") == "scroll notice.row.20"


# --- handler loop over the scrollable FakeDriver ---


def test_returns_without_scrolling_when_target_already_on_screen() -> None:
    driver = _scrollable([_row(i) for i in range(20)])
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.0"}})))
    assert _scroll_count(driver) == 0  # row 0 starts on-screen; no scroll needed


def test_reveals_a_target_after_scrolling() -> None:
    driver = _scrollable([_row(i) for i in range(20)])  # content 1800 tall over an 800 viewport
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.19"}})))
    assert _scroll_count(driver) >= 1
    # After the loop returns, the target's center is inside the viewport.
    frame = base.resolve_unique(driver.query(), {"id": "row.19"})["frame"]
    cx, cy = base.frame_center(frame)
    assert 0.0 <= cx <= _VIEWPORT[0] and 0.0 <= cy <= _VIEWPORT[1]


def test_fails_at_max_scrolls_when_content_keeps_moving() -> None:
    # 100 rows (content 9000 tall) so the region keeps changing every step: the bound, not
    # end-of-content, is what stops the loop.
    driver = _scrollable([_row(i) for i in range(100)])
    with pytest.raises(base.ElementNotFound, match="after 2 scroll"):
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.99"}, "maxScrolls": 2}))
        )
    assert _scroll_count(driver) == 2


def test_fails_fast_on_end_of_content() -> None:
    # A short, non-scrollable screen: the first scroll cannot move it, so the loop fails at once on
    # end-of-content rather than spending the whole (default 15) bound. Nothing in the region is
    # clipped, which is what licenses that conclusion from a single unchanged read (BE-0329).
    driver = _scrollable([_row(0), _row(1)])
    with pytest.raises(base.ElementNotFound, match="end of content"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))
    assert _scroll_count(driver) == 1  # one probe scroll, then fail — not 15


# --- what two reads may be concluded from (BE-0329, pure functions) ---


def test_an_element_flush_with_the_region_bounds_is_not_unclipped() -> None:
    # The whole point of the predicate: a backend that clips reports the clipped edge *at* the bounds,
    # so an element touching them may be far taller than it looks and its position may belong to the
    # screen rather than to the content. Only a frame strictly inside is evidence.
    bounds: base.Frame = (0.0, 0.0, 300.0, 800.0)
    assert _unclipped((0.0, 10.0, 280.0, 90.0), bounds, 1)
    assert not _unclipped((0.0, 0.0, 280.0, 90.0), bounds, 1)  # flush with the top edge
    assert not _unclipped((0.0, 710.0, 280.0, 90.0), bounds, 1)  # flush with the bottom edge
    assert not _unclipped((0.0, 0.0, 280.0, 1400.0), bounds, 1)  # taller than the region
    assert not _unclipped((0.0, -200.0, 280.0, 90.0), bounds, 1)  # entirely outside
    # The test is per-axis: the same frame is unclipped horizontally, where it has room.
    assert _unclipped((10.0, 0.0, 280.0, 1400.0), bounds, 0)


def test_within_scopes_the_region_and_its_bounds() -> None:
    container = _el("list", (0.0, 100.0, 300.0, 300.0))
    inside = _el("in", (0.0, 150.0, 280.0, 90.0))
    reaching = _el("reaching", (0.0, 310.0, 280.0, 90.0))  # its bottom edge is the container's
    outside = _el("out", (0.0, 500.0, 280.0, 90.0))
    elements = [container, inside, reaching, outside]
    scoped = _region_view(elements, {"id": "list"}, _VIEWPORT, 1)
    # Scoped to the container: `out` is not in the region at all, and `reaching` touches the bounds, so
    # it may be an element the backend clipped to them. The container itself is excluded — its own
    # edges are the bounds, so keeping it would make every `within` region look partly clipped.
    assert set(scoped.in_view) == {("in", None)}
    assert scoped.cut_off == (reaching["frame"],)  # its bottom edge is the container's
    # The same elements against the viewport: the bounds are what decide, and nothing reaches those.
    whole = _region_view(elements, None, _VIEWPORT, 1)
    assert set(whole.in_view) == {("list", None), ("in", None), ("reaching", None), ("out", None)}
    assert not whole.cut_off


def test_a_read_that_does_not_show_the_within_container_decides_nothing() -> None:
    # This projection runs on every polled read, so a read that momentarily lacks the container (or
    # shows two) must not raise out of the loop that exists to ride such a read out. It yields nothing
    # to judge instead; the gesture's own `resolve_unique` is what fails loudly on a real absence.
    row = _el("in", (0.0, 150.0, 280.0, 90.0))
    for elements in ([row], [_el("list", (0.0, 0.0, 300.0, 400.0)), row] * 2):
        view = _region_view(elements, {"id": "list"}, _VIEWPORT, 1)
        assert not view.positions and not view.cut_off and not view.identities


def test_a_label_only_change_is_not_motion() -> None:
    # A clock that ticks or a spinner whose text changes is not scrolling. The old comparison read any
    # difference as motion, which is why a stopped region could keep being scrolled.
    before = _region_view([_el("row", (0.0, 10.0, 280.0, 90.0), label="12:00")], None, _VIEWPORT, 1)
    after = _region_view([_el("row", (0.0, 10.0, 280.0, 90.0), label="12:01")], None, _VIEWPORT, 1)
    assert not _region_moved(before, after)


def test_a_moved_frame_or_a_changed_row_set_is_motion() -> None:
    tracked = _region_view([_el("row", (0.0, 10.0, 280.0, 90.0))], None, _VIEWPORT, 1)
    travelled = _region_view([_el("row", (0.0, 40.0, 280.0, 90.0))], None, _VIEWPORT, 1)
    assert _region_moved(tracked, travelled)
    # A lazy tree reports no shared frame at all when rows enter or leave, so the identity multiset
    # carries the motion there.
    entered = _region_view(
        [_el("row", (0.0, 10.0, 280.0, 90.0)), _el("next", (0.0, 110.0, 280.0, 90.0))],
        None,
        _VIEWPORT,
        1,
    )
    assert _region_moved(tracked, entered)


def test_a_sideways_shift_is_not_motion_along_a_vertical_scroll() -> None:
    before = _region_view([_el("row", (0.0, 10.0, 280.0, 90.0))], None, _VIEWPORT, 1)
    after = _region_view([_el("row", (12.0, 10.0, 280.0, 90.0))], None, _VIEWPORT, 1)
    assert not _region_moved(before, after)
    # The same pair along the horizontal axis is motion, so the axis is what scopes the comparison.
    sideways_before = _region_view([_el("row", (10.0, 10.0, 100.0, 90.0))], None, _VIEWPORT, 0)
    sideways_after = _region_view([_el("row", (40.0, 10.0, 100.0, 90.0))], None, _VIEWPORT, 0)
    assert _region_moved(sideways_before, sideways_after)


def test_an_element_travelling_into_the_bounds_is_motion() -> None:
    # A tracked element that reaches the region bounds leaves the tracked set, taking its position
    # with it, and the identity multiset does not change. Without counting the clipped elements the
    # step would read as no motion at all — and, with nothing clipped beforehand, as end of content.
    before = _region_view([_el("row", (0.0, 700.0, 280.0, 90.0))], None, _VIEWPORT, 1)
    after = _region_view([_el("row", (0.0, 720.0, 280.0, 90.0))], None, _VIEWPORT, 1)
    assert not after.in_view  # 720 + 90 reaches the viewport's bottom edge, so it is cut off
    assert _region_moved(before, after)


# --- a region the tree cannot show motion in (BE-0329) ---


class _ClippedDriver(FakeDriver):
    """A scrollable fake reporting one screen-sized frame however far its content scrolls.

    Android's clipped bounds in miniature: UI Automator reports the visible part of an element, so a
    row taller than the screen reports the whole screen both before and after a step, while the
    content behind it moves. The rendered screen is the only thing that differs, which is what the
    `screencap` checksum showed during the investigation — so this fake writes a render derived from
    its true scroll offset rather than from the frozen tree it reports.
    """

    def __init__(self, content_h: float, lag: float = 0.0) -> None:
        super().__init__(
            screen=[_el("tall", (0.0, 0.0, _VIEWPORT[0], content_h))], viewport=_VIEWPORT
        )
        self._lag = lag

    def read_lag(self) -> float:
        return self._lag

    def query(self) -> list[base.Element]:
        return [_el("tall", (0.0, 0.0, _VIEWPORT[0], _VIEWPORT[1]))]

    def screenshot(self, path: str) -> None:
        super().screenshot(path)
        Path(path).write_bytes(f"offset={self._scroll_offset}".encode())


class _NoScreenshotDriver(_ClippedDriver):
    """The clipped region above on a backend that cannot capture a screen at all."""

    CAPABILITIES = frozenset(FakeDriver.CAPABILITIES - {base.Capability.SCREENSHOT})


class _EmptyCaptureDriver(_ClippedDriver):
    """A backend that declares SCREENSHOT and writes no bytes — the fake and headless drivers' shape."""

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"")


class _ScriptedRenderDriver(_ClippedDriver):
    """A backend whose captures follow a script, for the settle rule the checksum rests on."""

    def __init__(self, captures: list[bytes]) -> None:
        super().__init__(content_h=4000.0)
        self._captures = captures
        self.captured = 0

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(self._captures[min(self.captured, len(self._captures) - 1)])
        self.captured += 1


def test_a_clipped_region_that_keeps_moving_is_never_called_the_end_of_content() -> None:
    # The defect: the tree is byte-identical across a step that did move the content, and the old
    # comparison turned that into "end of content" on a target the loop could still have reached.
    driver = _ClippedDriver(content_h=4000.0)
    with pytest.raises(base.ElementNotFound, match="after 3 scroll") as failure:
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}, "maxScrolls": 3}))
        )
    assert "end of content" not in str(failure.value)
    assert _scroll_count(driver) == 3


def test_the_rendered_screen_confirms_the_end_of_content_the_tree_cannot() -> None:
    # Same frozen tree, but the content has nowhere to go, so the render does not change either. Two
    # such steps in a row are the evidence the tree could not give, which keeps the failure prompt
    # instead of spending the whole bound.
    driver = _ClippedDriver(content_h=_VIEWPORT[1])
    with pytest.raises(base.ElementNotFound, match="neither the region nor the rendered screen"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))
    assert _scroll_count(driver) == 2  # one step to capture a render, one to compare it — not 15


def test_a_moving_step_between_two_unjudged_ones_discards_the_render() -> None:
    # The verdict needs two *consecutive* unjudged steps. A step the tree judged in between describes a
    # different screen, so keeping its predecessor's checksum would end a region that had moved since.
    class _MovesOnItsSecondStep(_ClippedDriver):
        """A blind region whose second step adds a row, with a render that never changes at all.

        Steps 1 and 3 are unjudged and 2 is not, so a digest that outlived step 2 would be compared
        against step 3's and end the region — while the screen it described is two steps stale.
        """

        def __init__(self) -> None:
            super().__init__(content_h=_VIEWPORT[1])  # no room to scroll: the render never changes
            self._reads = 0

        def query(self) -> list[base.Element]:
            self._reads += 1
            extra = [_el("row", (0.0, 20.0, 280.0, 40.0))] if self._reads >= 3 else []
            return [*super().query(), *extra]

    driver = _MovesOnItsSecondStep()
    with pytest.raises(base.ElementNotFound, match="after 3 scroll") as failure:
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}, "maxScrolls": 3}))
        )
    assert "end of content" not in str(failure.value)


def test_the_bound_says_when_the_regions_motion_could_not_be_observed() -> None:
    # A backend with no capture to fall back on: the loop spends the bound rather than guessing, and
    # the failure says the motion could not be observed, so an author does not read it as a claim that
    # the list ended.
    driver = _NoScreenshotDriver(content_h=4000.0)
    with pytest.raises(base.ElementNotFound, match="could not be observed on the last step"):
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}, "maxScrolls": 2}))
        )


def test_the_bound_stays_silent_about_observation_when_the_region_was_observable() -> None:
    # The detail above is a claim about the last step, so it must be absent when that step was judged.
    driver = _scrollable([_row(i) for i in range(100)])
    with pytest.raises(base.ElementNotFound) as failure:
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.99"}, "maxScrolls": 2}))
        )
    assert "could not be observed" not in str(failure.value)


# --- the checksum the blind region rests on (BE-0329 unit 4) ---


def test_a_screen_still_being_drawn_is_not_read_as_settled() -> None:
    # Two captures must agree before either is trusted, or a frame caught mid-draw would stand for the
    # screen — and, matching the step before by chance, would end a region that is still moving.
    driver = _ScriptedRenderDriver([b"drawing", b"done", b"done"])
    assert _settled_render(driver) == hashlib.sha256(b"done").hexdigest()
    assert driver.captured == 3  # the mid-draw capture was compared, not returned


def test_a_screen_that_never_settles_yields_no_checksum(monkeypatch: pytest.MonkeyPatch) -> None:
    # A spinner or a caret keeps the checksum moving, and a capture cannot tell that from scrolling, so
    # the loop is given nothing rather than a guess. Bounded, so the step costs a known amount.
    monkeypatch.setattr(scroll, "_RENDER_SETTLE_S", 0.05)
    driver = _ScriptedRenderDriver([b"a", b"b", b"c", b"d", b"e", b"f", b"g", b"h", b"i", b"j"])
    assert _settled_render(driver) is None


def test_an_empty_capture_is_not_a_checksum() -> None:
    # A backend that writes no bytes would otherwise report the same digest on every step, which is the
    # false "end of content" this item exists to remove — now reached through the render instead.
    driver = _EmptyCaptureDriver(content_h=4000.0)
    assert _render_digest(driver) is None
    with pytest.raises(base.ElementNotFound, match="after 3 scroll") as failure:
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}, "maxScrolls": 3}))
        )
    assert "end of content" not in str(failure.value)


def test_a_failed_capture_degrades_instead_of_failing_the_scroll() -> None:
    # The checksum only ever restores a *faster* failure, so losing it must not fail the scroll for a
    # reason the scenario has nothing to do with.
    class _Failing(_ClippedDriver):
        def screenshot(self, path: str) -> None:
            raise OSError("no space left on device")

    assert _render_digest(_Failing(content_h=4000.0)) is None


def test_a_crashed_backend_is_not_absorbed_by_the_capture() -> None:
    # A crash is the runner's to recover from (it re-leases and retries the scenario), so it must not be
    # swallowed into "the screen could not judge this step" and leave the run against a dead backend.
    class _Crashing(_ClippedDriver):
        def screenshot(self, path: str) -> None:
            raise base.BackendCrashError("device offline")

    with pytest.raises(base.BackendCrashError):
        _render_digest(_Crashing(content_h=4000.0))


# --- what a mover may speak for (BE-0329) ---


class _AndroidishDriver(FakeDriver):
    """A scrollable fake shaped like a real Android tree, where the list is the clipped element.

    UI Automator reports the visible part of an element, so the list container reports a frame flush
    with the screen's bottom edge however far its content scrolls. Above it sits an app bar that shifts
    once on the first scroll and then pins — the standard collapsing toolbar. The bar is unclipped and,
    after that first shift, an element the loop has watched move: exactly what a mover must not be
    taken for, since it sits outside the list it never belonged to.
    """

    _BAR = (0.0, 14.0, _VIEWPORT[0], 90.0)
    _LIST: base.Frame = (0.0, 110.0, _VIEWPORT[0], _VIEWPORT[1] - 110.0)

    def __init__(self, rows: int, *, expose_rows: bool, reveal_at: float | None = None) -> None:
        super().__init__(screen=[_row(i, x=10.0, w=200.0) for i in range(rows)], viewport=_VIEWPORT)
        self._expose_rows = expose_rows
        self._reveal_at = reveal_at

    def query(self) -> list[base.Element]:
        shift = 6.0 if self._scroll_offset[1] > 0 else 0.0
        x, y, w, h = self._BAR
        tree = [_el("appbar", (x, y - shift, w, h)), _el("list", self._LIST)]
        if self._reveal_at is not None and self._scroll_offset[1] >= self._reveal_at:
            tree.append(_el("target", (10.0, 300.0, 200.0, 90.0)))
        if self._expose_rows:
            tree += [el for el in super().query() if base.contains(self._LIST, el["frame"])]
        return tree

    def screenshot(self, path: str) -> None:
        super().screenshot(path)
        Path(path).write_bytes(f"offset={self._scroll_offset}".encode())


def test_a_mover_inside_the_clipped_list_ends_the_region_at_once() -> None:
    # The sound condition, doing its job: the list's own frame is clipped, so nothing licenses a verdict
    # from an unchanged read *except* a row that scrolled with the content and has now stopped. The
    # failure names that evidence, and arrives without spending the bound or capturing a screen.
    driver = _AndroidishDriver(rows=20, expose_rows=True)
    with pytest.raises(base.ElementNotFound, match="had watched move is still there") as failure:
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))
    assert "end of content" in str(failure.value)
    assert _scroll_count(driver) < 15  # the content bottoms out, and the row says so
    assert not [kind for kind, _ in driver.actions if kind == "screenshot"]


def test_chrome_that_shifts_once_and_pins_cannot_end_the_region() -> None:
    # The regression this condition must not have: an app bar collapses on the first scroll and then
    # holds still, becoming a mover that stands still forever. Reading it as evidence ends the list
    # while it is still scrolling — the very defect BE-0329 exists to remove, re-entered through the
    # mover set. A mover speaks only for a clipped element that contains it, and the bar is outside the
    # list, so the loop keeps going and reaches the target.
    # The bar shifts on step 1 and pins from step 2, so step 2 is the read that used to end the region;
    # the target arrives on step 3, which the loop only reaches by declining to end it.
    driver = _AndroidishDriver(rows=20, expose_rows=False, reveal_at=1000.0)
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "target"}})))
    assert _scroll_count(driver) == 3


class _StickyHeaderDriver(_ClippedDriver):
    """The clipped region above, plus a position-fixed header that never moves with the content."""

    def query(self) -> list[base.Element]:
        return [_el("header", (0.0, 5.0, _VIEWPORT[0], 40.0)), *super().query()]


def test_a_sticky_header_standing_still_is_not_the_end_of_content() -> None:
    # A fixed header sits unclipped inside the region and never moves, by design. Reading it as
    # evidence would end the region while the list scrolls behind it — so the decision needs an
    # element the loop has *watched move*, which a sticky header never becomes.
    driver = _StickyHeaderDriver(content_h=4000.0)
    with pytest.raises(base.ElementNotFound) as failure:
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}, "maxScrolls": 3}))
        )
    assert "end of content" not in str(failure.value)
    assert _scroll_count(driver) == 3


def test_a_blind_region_does_not_spend_the_read_lag_budget() -> None:
    # Reconciling with the read-lag re-query (BE-0326): waiting can only confirm what the tree is able
    # to show, and this tree can show nothing, so the wait is skipped instead of burning the whole
    # budget on every step. One read per step, as on a backend that reports no lag.
    driver = _ClippedDriver(content_h=4000.0, lag=5.0)
    assert isinstance(driver, base.ReadLagProvider)
    reads = 0
    plain_query = driver.query

    def counting_query() -> list[base.Element]:
        nonlocal reads
        reads += 1
        return plain_query()

    driver.query = counting_query  # type: ignore[method-assign]
    with pytest.raises(base.ElementNotFound):
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}, "maxScrolls": 2}))
        )
    assert reads == 3  # the loop's opening read, then one read of each step's result — no polling


# --- a step that carries the target past the viewport (BE-0329) ---


class _FlingingDriver(FakeDriver):
    """A scrollable fake whose content travels further than the gesture asked, as momentum would.

    No shipped backend is known to fling today, which is exactly the problem: the loop's correctness
    rested on a per-backend property nothing measured. This models the measured case — a chained pan
    whose contacts flung — so the recovery is covered on the fast gate.
    """

    def __init__(self, rows: int, overshoot: float) -> None:
        super().__init__(screen=[_row(i) for i in range(rows)], viewport=_VIEWPORT)
        self._overshoot = overshoot

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        flung = (
            frm[0] + (to[0] - frm[0]) * self._overshoot,
            frm[1] + (to[1] - frm[1]) * self._overshoot,
        )
        super().scroll(frm, flung)


def test_an_overshooting_step_shrinks_the_step_and_looks_back() -> None:
    # The step carries `row.10` clean past the viewport, so nothing that was in view survives it. The
    # loop halves the step and reverses once to query the span that passed, rather than reporting a
    # row that exists as missing.
    driver = _FlingingDriver(rows=40, overshoot=3.0)
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.10"}})))
    gestures = _scroll_gestures(driver)
    assert len(gestures) == 2
    (_, first_from_y), (_, first_to_y) = gestures[0]
    (_, back_from_y), (_, back_to_y) = gestures[1]
    assert first_to_y < first_from_y  # the finger went up: content down, revealing later rows
    assert back_to_y > back_from_y  # the look-back went the other way
    # Asked for half the travel, so the shrink is pinned by the gesture and not by the step count —
    # which leaves `_STEP_FRACTION` free to be retuned without failing this test.
    assert abs(back_to_y - back_from_y) == pytest.approx(abs(first_to_y - first_from_y) / 2)
    frame = base.resolve_unique(driver.query(), {"id": "row.10"})["frame"]
    assert 0.0 <= base.frame_center(frame)[1] <= _VIEWPORT[1]


def test_an_overshoot_that_survives_the_smallest_step_fails_naming_it() -> None:
    # A backend that flings whatever it is asked for cannot be worked around, so the loop stops at the
    # floor and says the step overshot — not that the target is absent, which would blame the scenario
    # for a gesture defect.
    driver = _FlingingDriver(rows=400, overshoot=10.0)
    with pytest.raises(base.ElementNotFound, match="overshot the region"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))


# --- the author-chosen step size (BE-0400) ---


def test_amount_sets_the_step_the_loop_starts_from() -> None:
    # The field's whole content: the first gesture travels `amount` of the viewport instead of the
    # loop's own default. Asserted on the gesture, not on the step count, so the default stays free to
    # be retuned.
    default = _scrollable([_row(i) for i in range(20)])
    _do_action(default, Step(scroll=Scroll.model_validate({"to": {"id": "row.19"}})))
    chosen = _scrollable([_row(i) for i in range(20)])
    _do_action(chosen, Step(scroll=Scroll.model_validate({"to": {"id": "row.19"}, "amount": 0.2})))

    def _first_travel(driver: FakeDriver) -> float:
        (_, from_y), (_, to_y) = _scroll_gestures(driver)[0]
        return abs(to_y - from_y)

    assert _first_travel(default) == pytest.approx(scroll._STEP_FRACTION * _VIEWPORT[1])
    assert _first_travel(chosen) == pytest.approx(0.2 * _VIEWPORT[1])


def test_a_small_amount_reaches_a_target_the_default_step_overshoots() -> None:
    # An author who already knows the screen needs a finer step should not have to spend an overshoot
    # and a look-back to get one. Against the same flinging backend the default step overshoots on
    # (`test_an_overshooting_step_shrinks_the_step_and_looks_back`), a small `amount` arrives with the
    # recovery never triggered — every gesture goes the one way, and none is a look-back.
    driver = _FlingingDriver(rows=40, overshoot=3.0)
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.10"}, "amount": 0.1})))
    gestures = _scroll_gestures(driver)
    assert gestures, "expected at least one scroll"
    assert all(to_y < from_y for (_, from_y), (_, to_y) in gestures)
    frame = base.resolve_unique(driver.query(), {"id": "row.10"})["frame"]
    assert 0.0 <= base.frame_center(frame)[1] <= _VIEWPORT[1]


def test_an_overshooting_amount_above_the_floor_halves_from_where_amount_put_it() -> None:
    # The recovery shrinks the step the author chose, not the default one: an `amount` that overshoots
    # halves to half of *itself*. Asserted on the two gestures' travel rather than on the step count,
    # so the case pins the arithmetic and not `_STEP_FRACTION`.
    overshoot = 4.0  # enough that 0.3 of the viewport still carries everything in view off it
    driver = _FlingingDriver(rows=40, overshoot=overshoot)
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.10"}, "amount": 0.3})))
    gestures = _scroll_gestures(driver)
    assert len(gestures) == 2
    (_, first_from_y), (_, first_to_y) = gestures[0]
    (_, back_from_y), (_, back_to_y) = gestures[1]
    # The fake logs the flung endpoints, not the requested ones, so each travel carries the constant
    # overshoot factor — which is why the asserted distances divide it back out.
    assert abs(first_to_y - first_from_y) == pytest.approx(overshoot * 0.3 * _VIEWPORT[1])
    assert back_to_y > back_from_y  # the look-back went the other way
    assert abs(back_to_y - back_from_y) == pytest.approx(overshoot * 0.15 * _VIEWPORT[1])


@pytest.mark.parametrize("amount", [0.05, scroll._MIN_STEP_FRACTION])
def test_an_amount_at_or_below_the_floor_fails_on_its_first_overshoot_naming_the_step_it_took(
    amount: float,
) -> None:
    # The floor stays fixed regardless of `amount`, so a step already at or below it has nothing to
    # shrink to: the first detected overshoot fails the call outright rather than halving toward a
    # floor it has reached. The message must name the step actually taken — the floor would name a
    # step a call below it never took. Both sides of the floor comparison are covered, since the
    # boundary case is exactly where a `<` would silently pass the call on to a halving that cannot
    # shrink it.
    driver = _FlingingDriver(rows=400, overshoot=30.0)
    with pytest.raises(base.ElementNotFound, match=rf"even at {amount} of the viewport"):
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}, "amount": amount}))
        )
    assert _scroll_count(driver) == 1  # failed on the first overshoot, with no shrink attempted


def test_scrolling_into_an_element_taller_than_the_viewport_is_not_an_overshoot() -> None:
    # The step that fills the viewport with one clipped element leaves nothing in view, but that is
    # the region the loop cannot observe — not evidence of travel. Reading it as an overshoot would
    # shrink the step and reverse on an ordinary screen, and would report a fling that never happened.
    rows = [_row(i) for i in range(8)]
    tall = _el("tall", (0.0, _TOP_INSET + 8 * _ROW_H, 280.0, 2400.0))
    driver = _scrollable([*rows, tall])
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "tall"}})))
    gestures = _scroll_gestures(driver)
    assert gestures, "expected at least one scroll"
    # Every gesture went the same way: no look-back was triggered, and none was needed.
    assert all(to_y < from_y for (_, from_y), (_, to_y) in gestures)


# --- a backend whose reads lag the gesture (ReadLagProvider, BE-0326) ---


class _LaggingDriver(FakeDriver):
    """A scrollable fake whose first read after each scroll still describes the previous screen.

    The Android failure mode in miniature: the gesture moves the content, but the tree naming the new
    frames is published a moment later, so a read taken in between returns the pre-scroll screen.
    """

    def __init__(self, screen: list[base.Element], viewport: base.Point, lag: float) -> None:
        super().__init__(screen=screen, viewport=viewport)
        self._lag = lag
        self._stale: list[base.Element] | None = None

    def read_lag(self) -> float:
        return self._lag

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        self._stale = super().query()  # the screen as it looks *before* this gesture lands
        super().scroll(frm, to)

    def query(self) -> list[base.Element]:
        if self._stale is not None:
            stale, self._stale = self._stale, None
            return stale
        return super().query()


def test_a_late_tree_is_not_mistaken_for_the_end_of_content() -> None:
    # The regression: with the target genuinely below the fold, every step's first read describes the
    # pre-scroll screen. Read once and that looks exactly like a bottomed-out region, so the loop used
    # to fail with "end of content" while the content had in fact moved. Re-reading inside the
    # backend's own `read_lag` budget sees the real result, so the target is still revealed.
    driver = _LaggingDriver([_row(i) for i in range(20)], _VIEWPORT, lag=1.0)
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.19"}})))
    frame = base.resolve_unique(driver.query(), {"id": "row.19"})["frame"]
    cx, cy = base.frame_center(frame)
    assert 0.0 <= cx <= _VIEWPORT[0] and 0.0 <= cy <= _VIEWPORT[1]


def test_a_lagging_backend_still_reports_a_real_end_of_content() -> None:
    # The budget tolerates a late tree without blunting the verdict: on a screen that cannot scroll,
    # re-reading never sees a change, so the loop still fails with end-of-content rather than
    # spending `maxScrolls`.
    driver = _LaggingDriver([_row(0), _row(1)], _VIEWPORT, lag=0.3)
    with pytest.raises(base.ElementNotFound, match="end of content"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))
    assert _scroll_count(driver) == 1


def test_a_backend_that_reports_no_lag_is_read_exactly_once_per_step() -> None:
    # Not implementing ReadLagProvider means "my reads do not lag", so the re-read loop must not run
    # at all: one read per step keeps the synchronous backends as fail-fast (and as cheap) as before.
    driver = _scrollable([_row(0), _row(1)])
    assert not isinstance(driver, base.ReadLagProvider)
    reads = 0
    plain_query = driver.query

    def counting_query() -> list[base.Element]:
        nonlocal reads
        reads += 1
        return plain_query()

    driver.query = counting_query  # type: ignore[method-assign]
    with pytest.raises(base.ElementNotFound, match="end of content"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))
    assert reads == 2  # the loop's opening read, then one read of the step's result — no polling


def test_ambiguous_target_fails_rather_than_scrolling_forever() -> None:
    # Two elements share an id at different positions — a genuine duplicate-id ambiguity, not a
    # content-identical duplicate registration (which resolve_unique now collapses instead of
    # flagging ambiguous).
    driver = _scrollable([_row(0), _el("row.0", (0.0, _TOP_INSET + _ROW_H, 280.0, _ROW_H))])
    with pytest.raises(base.AmbiguousSelector):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.0"}})))


def test_within_anchors_the_gesture_on_the_container() -> None:
    # A container on the right half; the gesture must start inside it, not at the screen center. The
    # rows fit inside it along both axes, so they are the region the loop compares (the container
    # itself is excluded: its edges sit on the region bounds, which no element can be judged from).
    container: base.Element = _el("list", (200.0, 0.0, 200.0, 800.0))
    rows = [_row(i, prefix="c", x=220.0, w=160.0) for i in range(20)]
    driver = FakeDriver(screen=[container, *rows], viewport=(400.0, 800.0))
    _do_action(
        driver,
        Step(scroll=Scroll.model_validate({"to": {"id": "c.19"}, "within": {"id": "list"}})),
    )
    scrolls = _scroll_gestures(driver)
    assert scrolls, "expected at least one scroll"
    (from_x, _from_y), _ = scrolls[0]
    assert from_x == 300.0  # the container's center x (200 + 200/2), not the screen center (200)


def test_a_transient_read_without_the_within_container_is_ridden_out() -> None:
    # The container vanishing from one read is what a lagging or mid-transition tree looks like. The
    # loop must ride it out rather than fail the scroll with a message about `within`, which names
    # neither the target nor the step.
    container = _el("list", (200.0, 0.0, 200.0, 800.0))
    rows = [_row(i, prefix="c", x=220.0, w=160.0) for i in range(20)]

    class _DropsTheContainerOnce(FakeDriver):
        def __init__(self) -> None:
            super().__init__(screen=[container, *rows], viewport=(400.0, 800.0))
            self._reads = 0

        def read_lag(self) -> float:
            return 0.5  # a backend that admits its reads can lag is one whose reads may be degraded

        def query(self) -> list[base.Element]:
            self._reads += 1
            tree = super().query()
            return tree[1:] if self._reads == 2 else tree

    driver = _DropsTheContainerOnce()
    _do_action(
        driver,
        Step(scroll=Scroll.model_validate({"to": {"id": "c.19"}, "within": {"id": "list"}})),
    )
    frame = base.resolve_unique(driver.query(), {"id": "c.19"})["frame"]
    assert 0.0 <= base.frame_center(frame)[1] <= 800.0


def test_a_horizontal_scroll_compares_positions_along_its_own_axis() -> None:
    # `direction` picks the axis every comparison is scoped to. Mapped to the wrong one, a carousel's
    # motion is invisible and the first step reports the end of the content.
    columns = [_el(f"col.{i}", (10.0 + i * 200.0, 10.0, 180.0, 400.0)) for i in range(10)]
    driver = FakeDriver(screen=columns, viewport=_VIEWPORT)
    _do_action(
        driver,
        Step(scroll=Scroll.model_validate({"to": {"id": "col.9"}, "direction": "right"})),
    )
    frame = base.resolve_unique(driver.query(), {"id": "col.9"})["frame"]
    assert 0.0 <= base.frame_center(frame)[0] <= _VIEWPORT[0]
    gestures = _scroll_gestures(driver)
    assert all(from_y == to_y for (_, from_y), (_, to_y) in gestures)  # a sideways gesture


def test_a_lazy_tree_shows_its_motion_through_the_rows_it_drops() -> None:
    # A native lazy list drops off-screen rows instead of reporting them off-viewport, so no frame is
    # shared across a step and the identity multiset is what carries the motion.
    class _LazyDriver(FakeDriver):
        def query(self) -> list[base.Element]:
            viewport: base.Frame = (0.0, 0.0, _VIEWPORT[0], _VIEWPORT[1])
            return [el for el in super().query() if base.contains(viewport, el["frame"])]

    driver = _LazyDriver(screen=[_row(i) for i in range(20)], viewport=_VIEWPORT)
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.19"}})))
    frame = base.resolve_unique(driver.query(), {"id": "row.19"})["frame"]
    assert 0.0 <= base.frame_center(frame)[1] <= _VIEWPORT[1]


def test_a_late_read_that_only_relabels_does_not_end_the_wait() -> None:
    # The re-query waits for *motion*, not for any difference. A tree that ticks a clock while the
    # gesture's result is still pending would otherwise release the wait on nothing.
    class _RelabelsWhileLagging(FakeDriver):
        """A lagging fake whose first read after a scroll is the old screen with one label changed."""

        def __init__(self) -> None:
            super().__init__(screen=[_row(i) for i in range(20)], viewport=_VIEWPORT)
            self._stale: list[base.Element] | None = None
            self._tick = 0

        def read_lag(self) -> float:
            return 1.0

        def scroll(self, frm: base.Point, to: base.Point) -> None:
            self._tick += 1
            self._stale = [
                {**el, "label": f"clock {self._tick}"} if i == 0 else el
                for i, el in enumerate(super().query())
            ]
            super().scroll(frm, to)

        def query(self) -> list[base.Element]:
            if self._stale is not None:
                stale, self._stale = self._stale, None
                return stale
            return super().query()

    driver = _RelabelsWhileLagging()
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.19"}})))
    frame = base.resolve_unique(driver.query(), {"id": "row.19"})["frame"]
    assert 0.0 <= base.frame_center(frame)[1] <= _VIEWPORT[1]


def test_a_read_of_nothing_is_not_the_end_of_content() -> None:
    # An empty read is a backend degradation (a wedged accessibility bridge, a null-root dump), not a
    # list that ended. Reporting it as end-of-content would blame the scenario for the backend, so the
    # loop re-reads inside the declared budget and, failing that, spends the bound.
    class _ReadsNothing(FakeDriver):
        def __init__(self) -> None:
            super().__init__(screen=[_row(i) for i in range(20)], viewport=_VIEWPORT)

        def read_lag(self) -> float:
            return 0.2

        def query(self) -> list[base.Element]:
            return []

    driver = _ReadsNothing()
    with pytest.raises(base.ElementNotFound, match="after 2 scroll") as failure:
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.19"}, "maxScrolls": 2}))
        )
    assert "end of content" not in str(failure.value)


def test_scrolling_out_of_an_element_taller_than_the_viewport_is_not_an_overshoot() -> None:
    # The mirror of the step that scrolls *into* such an element: here nothing was in view before and
    # rows are in view after. Reading that as an overshoot would shrink the step and look back on an
    # ordinary screen, undoing progress the loop had just made.
    tall = _el("tall", (0.0, 0.0, 280.0, 2400.0))
    rows = [_el(f"row.{i}", (0.0, 2400.0 + 10.0 + i * _ROW_H, 280.0, _ROW_H)) for i in range(10)]
    driver = _scrollable([tall, *rows])
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "row.9"}})))
    gestures = _scroll_gestures(driver)
    assert all(to_y < from_y for (_, from_y), (_, to_y) in gestures)  # no look-back was triggered


def test_a_duplicated_key_is_tracked_from_neither_element() -> None:
    # Two rows a selector cannot tell apart name no single position, so keeping either one would let a
    # step's travel be read off the wrong element — fabricating a mover, or fabricating the overlap
    # that hides an overshoot.
    twins = [_el("row", (0.0, 10.0, 280.0, 90.0)), _el("row", (0.0, 200.0, 280.0, 90.0))]
    view = _region_view(twins, None, _VIEWPORT, 1)
    assert not view.positions and not view.in_view
    assert view.visible == {("row", None)}  # both are on screen; neither speaks for the other


def test_a_list_of_near_full_screen_cards_is_not_read_as_overshooting() -> None:
    # One card fills most of the viewport, so every step replaces the whole in-view set — which is not a
    # fling: the card that left is still on screen at the edge. Reading it as an overshoot would halve
    # the step and look back on every step of an ordinary card list.
    cards = [_el(f"card.{i}", (10.0, 10.0 + i * 700.0, 280.0, 690.0)) for i in range(6)]
    driver = _scrollable(cards)
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "card.5"}})))
    gestures = _scroll_gestures(driver)
    assert all(to_y < from_y for (_, from_y), (_, to_y) in gestures)  # no look-back was triggered


def test_a_look_back_step_cannot_trigger_another_look_back() -> None:
    # The look-back is a recovery, not a state the loop can get stuck oscillating in: the step that
    # reverses is exempt from the overshoot test, so two of them never follow one another.
    driver = _FlingingDriver(rows=400, overshoot=10.0)
    with pytest.raises(base.ElementNotFound, match="overshot the region"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))
    gestures = _scroll_gestures(driver)
    back = [to_y > from_y for (_, from_y), (_, to_y) in gestures]
    assert any(back), "expected the recovery to look back at least once"
    assert not any(a and b for a, b in itertools.pairwise(back))

"""The `scroll` action (BE-0326, BE-0329): schema parse/validation and the non-inertial handler loop.

The handler runs over a `FakeDriver` in scrollable mode — a real viewport + clamped offset, no device
— so the stop condition (`to`'s center in the viewport), the `maxScrolls` bound, the end-of-content
fail-fast, and `within` scoping are all exercised on the fast gate. BE-0329's decisions are pure
functions over the element lists the loop already holds, so what the loop may conclude from a pair of
reads is tested directly, and the cases a real backend produces — a frame clipped to the screen, a
tree that lags its gesture, a step that flings — are modelled by the doubles below.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator.actions._registry import _do_action, _step_label
from bajutsu.orchestrator.actions.handlers.scroll import (
    _region_moved,
    _region_view,
    _unclipped,
)
from bajutsu.scenario import Scroll, Step, load_scenarios

_ROW_H = 90.0
_VIEWPORT: base.Point = (300.0, 800.0)
# Rows start below the viewport's top edge. An element flush with the region bounds is
# indistinguishable from one a backend clipped to them (BE-0329), so a list meant to stand for an
# ordinary, fully visible one has to sit strictly inside — which is what lets the loop trust an
# unchanged read as the end of the content.
_TOP_INSET = 10.0


def _row(i: int, *, prefix: str = "row", x: float = 0.0, w: float = 280.0) -> base.Element:
    return {
        "identifier": f"{prefix}.{i}",
        "label": f"{prefix} {i}",
        "traits": [],
        "value": None,
        "frame": (x, _TOP_INSET + i * _ROW_H, w, _ROW_H),
    }


def _el(identifier: str | None, frame: base.Frame, *, label: str | None = None) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": [],
        "value": None,
        "frame": frame,
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
    assert s.max_scrolls == 15  # default bound


def test_parse_scroll_all_fields() -> None:
    steps = load_scenarios(
        "- name: s\n  steps:\n"
        "    - scroll: { to: { label: Log out }, direction: up, "
        "within: { id: list }, maxScrolls: 25 }\n"
    )[0].steps
    s = steps[0].scroll
    assert s is not None
    assert s.direction == "up" and s.within is not None and s.within.id == "list"
    assert s.max_scrolls == 25  # camelCase alias maps to the snake_case attribute


def test_max_scrolls_must_be_positive() -> None:
    with pytest.raises(ValueError, match=r"greater than 0"):
        Scroll.model_validate({"to": {"id": "a"}, "maxScrolls": 0})


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
    assert set(scoped.positions) == {("in", None)}
    assert not scoped.all_in_view
    # The same elements against the viewport: the bounds are what decide, and nothing reaches those.
    whole = _region_view(elements, None, _VIEWPORT, 1)
    assert set(whole.positions) == {("list", None), ("in", None), ("reaching", None), ("out", None)}
    assert whole.all_in_view


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
    with pytest.raises(base.ElementNotFound, match="rendered screen both stopped changing"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))
    assert _scroll_count(driver) == 2  # one step to capture a render, one to compare it — not 15


def test_the_bound_says_when_the_regions_motion_could_not_be_observed() -> None:
    # A backend with no capture to fall back on: the loop spends the bound rather than guessing, and
    # the failure names why, so an author does not read it as a claim that the list ended.
    driver = _scrollable([_el("tall", (0.0, 0.0, _VIEWPORT[0], 4000.0))])
    driver.CAPABILITIES = frozenset(driver.CAPABILITIES - {base.Capability.SCREENSHOT})  # type: ignore[misc]
    with pytest.raises(base.ElementNotFound, match="could not be observed"):
        _do_action(
            driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}, "maxScrolls": 2}))
        )


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
    gestures = [arg for kind, arg in driver.actions if kind == "scroll"]
    assert len(gestures) == 2
    (_, first_from_y), (_, first_to_y) = gestures[0]
    (_, back_from_y), (_, back_to_y) = gestures[1]
    assert first_to_y < first_from_y  # the finger went up: content down, revealing later rows
    assert back_to_y > back_from_y  # the look-back went the other way
    frame = base.resolve_unique(driver.query(), {"id": "row.10"})["frame"]
    assert 0.0 <= base.frame_center(frame)[1] <= _VIEWPORT[1]


def test_an_overshoot_that_survives_the_smallest_step_fails_naming_it() -> None:
    # A backend that flings whatever it is asked for cannot be worked around, so the loop stops at the
    # floor and says the step overshot — not that the target is absent, which would blame the scenario
    # for a gesture defect.
    driver = _FlingingDriver(rows=400, overshoot=10.0)
    with pytest.raises(base.ElementNotFound, match="overshot the region"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))


def test_scrolling_into_an_element_taller_than_the_viewport_is_not_an_overshoot() -> None:
    # The step that fills the viewport with one clipped element leaves nothing in view, but that is
    # the region the loop cannot observe — not evidence of travel. Reading it as an overshoot would
    # shrink the step and reverse on an ordinary screen, and would report a fling that never happened.
    rows = [_row(i) for i in range(8)]
    tall = _el("tall", (0.0, _TOP_INSET + 8 * _ROW_H, 280.0, 2400.0))
    driver = _scrollable([*rows, tall])
    _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "tall"}})))
    gestures = [arg for kind, arg in driver.actions if kind == "scroll"]
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
    driver = _scrollable([_row(0), _row(0)])  # two elements share an id
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
    scrolls = [arg for kind, arg in driver.actions if kind == "scroll"]
    assert scrolls, "expected at least one scroll"
    (from_x, _from_y), _ = scrolls[0]
    assert from_x == 300.0  # the container's center x (200 + 200/2), not the screen center (200)

"""The `scroll` action (BE-0326): schema parse/validation and the bounded, non-inertial handler loop.

The handler runs over a `FakeDriver` in scrollable mode — a real viewport + clamped offset, no device
— so the stop condition (`to`'s center in the viewport), the `maxScrolls` bound, the end-of-content
fail-fast, and `within` scoping are all exercised on the fast gate.
"""

from __future__ import annotations

import pytest

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator.actions._registry import _do_action, _step_label
from bajutsu.scenario import Scroll, Step, load_scenarios

_ROW_H = 90.0
_VIEWPORT: base.Point = (300.0, 800.0)


def _row(i: int, *, prefix: str = "row", x: float = 0.0) -> base.Element:
    return {
        "identifier": f"{prefix}.{i}",
        "label": f"{prefix} {i}",
        "traits": [],
        "value": None,
        "frame": (x, i * _ROW_H, 280.0, _ROW_H),
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
    # end-of-content rather than spending the whole (default 15) bound.
    driver = _scrollable([_row(0), _row(1)])
    with pytest.raises(base.ElementNotFound, match="end of content"):
        _do_action(driver, Step(scroll=Scroll.model_validate({"to": {"id": "missing"}})))
    assert _scroll_count(driver) == 1  # one probe scroll, then fail — not 15


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
    # A container on the right half; the gesture must start inside it, not at the screen center.
    container: base.Element = {
        "identifier": "list",
        "label": None,
        "traits": [],
        "value": None,
        "frame": (200.0, 0.0, 200.0, 800.0),
    }
    rows = [_row(i, prefix="c", x=220.0) for i in range(20)]
    driver = FakeDriver(screen=[container, *rows], viewport=(400.0, 800.0))
    _do_action(
        driver,
        Step(scroll=Scroll.model_validate({"to": {"id": "c.19"}, "within": {"id": "list"}})),
    )
    scrolls = [arg for kind, arg in driver.actions if kind == "scroll"]
    assert scrolls, "expected at least one scroll"
    (from_x, _from_y), _ = scrolls[0]
    assert from_x == 300.0  # the container's center x (200 + 200/2), not the screen center (200)

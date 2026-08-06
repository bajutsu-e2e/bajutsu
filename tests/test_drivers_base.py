"""Tests for the shared driver helpers in `bajutsu.drivers.base`.

`wait_until` is the single deadline-polling loop every backend shares (BE-0118): each
driver's `wait_for` is a single-shot check, and this helper turns it into a timeout-honouring
wait uniformly, so a `timeout` means the same real seconds regardless of which backend drives.
"""

from __future__ import annotations

import pytest

from bajutsu.drivers import base


class LateDriver:
    """A stub driver whose selector matches only after `appear_after` single-shot checks.

    Simulates an element that renders slightly after the call — the case the base settle
    loop handles and Playwright's single check did not. `poll=0` keeps the test instant.
    """

    name = "late"

    def __init__(self, appear_after: int) -> None:
        self._appear_after = appear_after
        self.checks = 0

    def wait_for(self, sel: base.Selector) -> bool:
        self.checks += 1
        return self.checks > self._appear_after


def test_wait_until_polls_until_a_late_element_appears() -> None:
    driver = LateDriver(appear_after=2)
    assert base.wait_until(driver, {"id": "spinner"}, timeout=5, poll=0) is True
    assert driver.checks >= 3  # kept polling past the not-yet-present checks


def test_wait_until_returns_true_when_already_present() -> None:
    driver = LateDriver(appear_after=0)
    assert base.wait_until(driver, {"id": "home"}, timeout=1, poll=0) is True
    assert driver.checks == 1  # first check already matched


def test_wait_until_times_out_when_never_present() -> None:
    driver = LateDriver(appear_after=10_000)
    assert base.wait_until(driver, {"id": "nope"}, timeout=0, poll=0) is False


def test_wait_until_rejects_a_negative_poll() -> None:
    # A negative poll is caller misuse; fail loudly with a clear message rather than let
    # time.sleep raise its opaque ValueError deep in the loop.
    with pytest.raises(ValueError, match="poll must be non-negative"):
        base.wait_until(LateDriver(appear_after=0), {"id": "x"}, timeout=1, poll=-1)


def _element(identifier: str, frame: base.Frame = (0.0, 0.0, 10.0, 10.0)) -> base.Element:
    return {"identifier": identifier, "label": None, "traits": [], "value": None, "frame": frame}


class ScreenDriver:
    """A stub driver whose `query()` returns a fixed element list.

    Backs `default_wait_for`, the single-shot check every real backend delegates to (BE-0251).
    """

    name = "screen"

    def __init__(self, elements: list[base.Element]) -> None:
        self._elements = elements

    def query(self) -> list[base.Element]:
        return self._elements


def test_default_wait_for_true_when_the_selector_matches_the_current_screen() -> None:
    driver = ScreenDriver([_element("home")])
    assert base.default_wait_for(driver, {"id": "home"}) is True


def test_default_wait_for_false_when_the_selector_is_absent() -> None:
    driver = ScreenDriver([_element("home")])
    assert base.default_wait_for(driver, {"id": "missing"}) is False


def test_frame_center_is_the_frame_midpoint() -> None:
    assert base.frame_center((10.0, 20.0, 8.0, 40.0)) == (14.0, 40.0)


def test_gesture_anchor_is_the_center_and_a_quarter_of_the_smaller_side() -> None:
    # half = min(w, h) / 4 = min(8, 40) / 4 = 2.0; center = (0 + 8/2, 0 + 40/2).
    assert base.gesture_anchor((0.0, 0.0, 8.0, 40.0)) == (4.0, 20.0, 2.0)


def test_topmost_at_point_none_when_nothing_covers_the_target() -> None:
    target = _element("target", (0.0, 0.0, 10.0, 10.0))
    elements = [_element("sibling", (100.0, 100.0, 10.0, 10.0)), target]
    assert base.topmost_at_point(elements, base.frame_center(target["frame"]), target) is None


def test_topmost_at_point_finds_a_later_partially_overlapping_element() -> None:
    # Document order as the paint-order proxy: an element listed after the target is treated as
    # drawn on top, so a later sibling overlapping the target's center covers it — even though its
    # frame is neither a subset nor a superset of the target's (so it can't be mistaken for either
    # a descendant or, per the module docstring's reasoning, an ancestor).
    target = _element("target", (0.0, 0.0, 100.0, 20.0))
    covering = _element("sticky-header", (0.0, 0.0, 300.0, 10.0))
    elements = [target, covering]
    result = base.topmost_at_point(elements, base.frame_center(target["frame"]), target)
    assert result is covering


def test_topmost_at_point_known_limitation_exact_same_frame_reads_as_a_descendant() -> None:
    # Documented limitation: two unrelated elements sharing the target's exact frame (e.g. a
    # disabled-state overlay div sized to match its button exactly) are indistinguishable from a
    # same-size wrapper/descendant pair using geometry alone, so this reads as "not covering" —
    # the same conservative default a real descendant gets. This test pins that known trade-off
    # rather than leaving it to bit-rot silently.
    target = _element("target", (0.0, 0.0, 10.0, 10.0))
    same_frame = _element("overlay", (0.0, 0.0, 10.0, 10.0))
    elements = [target, same_frame]
    result = base.topmost_at_point(elements, base.frame_center(target["frame"]), target)
    assert result is None


def test_topmost_at_point_finds_a_larger_later_backdrop() -> None:
    # The primary case this function exists for: a same-size-or-larger overlay (a modal backdrop, a
    # sticky header) drawn after the target it covers. Its frame geometrically *contains* the
    # target's, exactly the way a real container ancestor's would — but a real ancestor can never be
    # listed after its own descendant in a pre-order document walk, so this is correctly read as an
    # unrelated overlay, not mistaken for a container (unlike a naive full-list containment scan).
    target = _element("button", (10.0, 10.0, 10.0, 10.0))
    backdrop = _element("modal-backdrop", (0.0, 0.0, 100.0, 100.0))
    elements = [target, backdrop]
    result = base.topmost_at_point(elements, base.frame_center(target["frame"]), target)
    assert result is backdrop


def test_topmost_at_point_ignores_an_earlier_overlapping_element() -> None:
    # An overlapping element earlier in document order — including one whose frame would
    # geometrically contain the target's, like a real container — is never considered: only the
    # positions after `target` are scanned.
    earlier = _element("card", (0.0, 0.0, 50.0, 50.0))
    target = _element("button", (5.0, 5.0, 10.0, 10.0))
    elements = [earlier, target]
    assert base.topmost_at_point(elements, base.frame_center(target["frame"]), target) is None


def test_topmost_at_point_excludes_the_targets_own_descendant() -> None:
    # A child nested inside the target's own frame is not an obstruction — tapping through it
    # still taps the target (e.g. an icon inside a button). Unlike an ancestor, a descendant does
    # come after `target` in document order, so this exclusion still needs the geometric check.
    target = _element("button", (0.0, 0.0, 20.0, 20.0))
    child = _element("icon", (5.0, 5.0, 5.0, 5.0))
    elements = [target, child]
    assert base.topmost_at_point(elements, base.frame_center(target["frame"]), target) is None


def test_topmost_at_point_ignores_a_non_overlapping_later_element() -> None:
    target = _element("target", (0.0, 0.0, 10.0, 10.0))
    elsewhere = _element("elsewhere", (100.0, 100.0, 10.0, 10.0))
    elements = [target, elsewhere]
    assert base.topmost_at_point(elements, base.frame_center(target["frame"]), target) is None

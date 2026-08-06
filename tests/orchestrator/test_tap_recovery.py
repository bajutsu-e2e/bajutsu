"""`_tap_with_recovery`: the bounded scroll safety net for a resolved-but-occluded tap target.

`FakeDriver.tap` (and `double_tap` / `long_press`) now enforces the same `topmost_at_point`
occlusion check a real backend's tap path enforces, so a scripted occlusion drives the same
orchestrator-level recovery loop these backends go through. Most test drivers below model a target
that starts covered by a fixed-position overlay and moves clear of it (or never does) as a `down`
`scroll()` is called — the FakeDriver counterpart of scrolling a row out from under a bottom-anchored
cover (a toast, a snackbar, a sticky footer), the direction `_tap_with_recovery` tries first. `down`
moves on-screen content toward the top of the screen (`y` *decreasing*) — the same content-to-finger
inversion `scroll.py`'s own `_CONTENT_TO_FINGER` applies — so these drivers *decrease*
`target["frame"]`'s `y` by a fixed amount per call. `_ClearsTopOverlayOnlyViaUpwardRecoveryDriver`
below instead models a top-anchored cover (a sticky header) that only the `up` fallback direction
clears, and moves `y` in whichever way the requested direction's finger travel actually implies.
"""

from __future__ import annotations

import pytest
from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario


class _ClearsOverlayAfterTwoScrollsDriver(FakeDriver):
    """`target` starts under a bottom-anchored `overlay`; each `down` `scroll()` moves `target`'s `y`
    up (toward the top of the screen), clearing the overlay after two calls — comfortably inside the
    recovery net's per-direction bound of three."""

    def __init__(self) -> None:
        target = el("target", frame=(10.0, 170.0, 100.0, 20.0))
        overlay = el("overlay", frame=(0.0, 165.0, 300.0, 30.0))  # covers y in [165, 195]
        super().__init__([target, overlay])
        self.scroll_calls = 0

    def viewport(self) -> base.Point:
        return (400.0, 200.0)

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        super().scroll(frm, to)
        self.scroll_calls += 1
        target = base.resolve_unique(self.screen, {"id": "target"})
        x, y, w, h = target["frame"]
        target["frame"] = (x, y - 10.0, w, h)


class _NeverClearsOverlayDriver(FakeDriver):
    """`target` moves on every `scroll()`, so the loop never mistakes this for end-of-content, but
    `overlay` is tall enough — in both directions — that it stays covered no matter how far `target`
    moves, regardless of which direction `_tap_with_recovery` is currently trying.

    `target`'s starting center, (60.0, 180.0), already sits inside the (400.0, 200.0) viewport —
    deliberately, so this pins the exact regression the roadmap item calls out:
    `scroll_until_tappable`'s stop condition must be `is_tappable` itself, never
    `scroll_to_target`'s default `_center_in_viewport`. Were the stop condition ever accidentally
    swapped back to `_center_in_viewport`, this driver's very first check would already read
    "on-screen" as true and the recovery would wrongly report success with zero scrolls — the
    assertions below (both directions' bounds are *spent*, not skipped) are what would catch that
    regression.
    """

    def __init__(self) -> None:
        target = el("target", frame=(10.0, 170.0, 100.0, 20.0))
        overlay = el("overlay", frame=(0.0, -1000.0, 300.0, 2000.0))  # always covers, either way
        super().__init__([target, overlay])
        self.scroll_calls = 0

    def viewport(self) -> base.Point:
        return (400.0, 200.0)

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        super().scroll(frm, to)
        self.scroll_calls += 1
        target = base.resolve_unique(self.screen, {"id": "target"})
        x, y, w, h = target["frame"]
        target["frame"] = (x, y - 10.0, w, h)


class _ClearsOverlayButScrollsPastTheViewportDriver(FakeDriver):
    """`target` clears `overlay` only after it has already scrolled past the viewport's top.

    `FakeDriver.is_tappable` (like adb's and the live iOS route's) checks `topmost_at_point` alone,
    with no notion of the viewport at all — so by the time `target` stops overlapping `overlay`,
    `is_tappable` on its own would already read `True` even though `target`'s center has moved past
    `viewport()`'s top edge. This pins the companion regression to the one
    `_NeverClearsOverlayDriver` pins above: `scroll_until_tappable`'s stop condition must require
    `_center_in_viewport` *and* `is_tappable`, not `is_tappable` alone — otherwise the recovery loop
    can "succeed" by scrolling the target out of view instead of clear of the obstruction, and a
    coordinate tap that follows lands outside the viewport, silently touching nothing. `target` never
    actually becomes tappable-and-on-screen, so both recovery directions exhaust their bound in turn.
    """

    def __init__(self) -> None:
        target = el("target", frame=(10.0, 70.0, 100.0, 20.0))
        overlay = el("overlay", frame=(0.0, -20.0, 300.0, 120.0))  # covers y in [-20, 100]
        super().__init__([target, overlay])
        self.scroll_calls = 0

    def viewport(self) -> base.Point:
        return (400.0, 100.0)

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        super().scroll(frm, to)
        self.scroll_calls += 1
        target = base.resolve_unique(self.screen, {"id": "target"})
        x, y, w, h = target["frame"]
        target["frame"] = (x, y - 70.0, w, h)


class _ClearsTopOverlayOnlyViaUpwardRecoveryDriver(FakeDriver):
    """`target` starts under a top-anchored `overlay` (a sticky header) that the `down` direction
    `_tap_with_recovery` tries first cannot clear — it only nudges `target` deeper under the header
    without ever escaping it — and that only the `up` fallback direction clears, within its own
    bound. Pins the direction-gap fix: a fixed `down`-only recovery would exhaust its bound here and
    never retry `up`, so this driver only ever reports tappable once the fallback direction runs.

    Unlike the drivers above, `scroll()` here reads the requested direction back out of `frm`/`to`
    rather than assuming `down`: `scroll.py`'s `_CONTENT_TO_FINGER` inversion means a `down` content
    scroll is a finger swipe *up* (`to`'s `y` smaller than `frm`'s), and `up` a finger swipe *down*
    (`to`'s `y` larger) — the same sign `_step_endpoints` produces for a real backend.
    """

    def __init__(self) -> None:
        target = el("target", frame=(10.0, 10.0, 100.0, 20.0))
        overlay = el("overlay", frame=(0.0, 0.0, 300.0, 25.0))  # covers y in [0, 25]
        super().__init__([target, overlay])
        self.scroll_calls = 0

    def viewport(self) -> base.Point:
        return (400.0, 200.0)

    def scroll(self, frm: base.Point, to: base.Point) -> None:
        super().scroll(frm, to)
        self.scroll_calls += 1
        target = base.resolve_unique(self.screen, {"id": "target"})
        x, y, w, h = target["frame"]
        # `down` (finger up, `to`'s y < `frm`'s) drives `target` only 5px deeper under the header,
        # never clear of it within the bound; `up` (finger down) moves it 15px clear per step.
        delta = 15.0 if to[1] > frm[1] else -5.0
        target["frame"] = (x, y + delta, w, h)


def _run(driver: FakeDriver, action: dict[str, object]) -> tuple[bool, str, list[str]]:
    result = run_scenario(
        driver, _scenario({"name": "recovery", "steps": [action]}), clock=FakeClock()
    )
    (step,) = result.steps
    return step.ok, step.reason, [a.gesture for a in step.actuations]


def test_tap_succeeds_without_recovery_when_already_tappable() -> None:
    driver = FakeDriver([el("target", frame=(0.0, 0.0, 10.0, 10.0))])
    ok, reason, gestures = _run(driver, {"tap": {"id": "target"}})
    assert (ok, reason) == (True, "")
    assert gestures == ["tap"]


def test_tap_recovers_after_scrolling_clears_the_overlay() -> None:
    driver = _ClearsOverlayAfterTwoScrollsDriver()
    ok, reason, gestures = _run(driver, {"tap": {"id": "target"}})
    assert (ok, reason) == (True, "")
    assert driver.scroll_calls == 2
    # The retried tap actually lands: the actuation log shows the two scroll steps then the tap.
    assert gestures == ["scroll", "scroll", "tap"]


def test_tap_fails_as_element_not_tappable_when_recovery_is_exhausted() -> None:
    driver = _NeverClearsOverlayDriver()
    ok, reason, _gestures = _run(driver, {"tap": {"id": "target"}})
    assert ok is False
    assert "target" in reason  # the selector repr, not a generic message
    # Never reads as the misleading ElementNotFound a scroll timeout would otherwise raise:
    # `_tap_with_recovery`'s own message, not `scroll_to_target`'s "scroll: … not on-screen".
    assert reason.startswith("still not tappable")
    # The first attempt's own ElementNotTappable (naming what covered the target, via
    # `base.raise_if_covered`) is interpolated in, not dropped — the one fact a CI log would
    # otherwise force reproducing the screen by hand to learn.
    assert "'overlay'" in reason


def test_tap_recovery_never_succeeds_by_scrolling_the_target_out_of_view() -> None:
    driver = _ClearsOverlayButScrollsPastTheViewportDriver()
    ok, reason, _gestures = _run(driver, {"tap": {"id": "target"}})
    assert ok is False
    assert reason.startswith("still not tappable")
    # Both directions' bounds are spent (3 + 3), not stopped early on a false "clear".
    assert driver.scroll_calls == 6


def test_tap_recovery_never_exceeds_its_own_bound() -> None:
    # However far short of clearing falls, the safety net never scrolls past its bound — three steps
    # per direction, `down` then `up` — searching for a way out; that would make it a search, not a
    # bounded net.
    driver = _NeverClearsOverlayDriver()
    _run(driver, {"tap": {"id": "target"}})
    assert driver.scroll_calls == 6


def test_tap_recovery_falls_back_to_up_when_down_cannot_clear_a_top_anchored_cover() -> None:
    # `down` alone (the pre-fix behavior) would exhaust its bound against `overlay` and never
    # retry: this pins that the fallback `up` direction is what makes the tap actually recover.
    driver = _ClearsTopOverlayOnlyViaUpwardRecoveryDriver()
    ok, reason, gestures = _run(driver, {"tap": {"id": "target"}})
    assert (ok, reason) == (True, "")
    # 3 `down` scrolls exhaust without clearing the header, then 2 `up` scrolls clear it.
    assert driver.scroll_calls == 5
    assert gestures == ["scroll", "scroll", "scroll", "scroll", "scroll", "tap"]


def test_ambiguous_selector_never_reaches_recovery() -> None:
    # Two elements share the id, both covered by nothing: the failure is ambiguity, resolved before
    # any tappability question is even asked, so no scroll is ever attempted.
    driver = FakeDriver(
        [
            el("dup", frame=(0.0, 0.0, 10.0, 10.0)),
            el("dup", frame=(0.0, 20.0, 10.0, 10.0)),
        ]
    )
    ok, reason, gestures = _run(driver, {"tap": {"id": "dup"}})
    assert ok is False
    assert "AmbiguousSelector" in reason or "件一致" in reason
    assert gestures == []  # no scroll, no tap — nothing was ever actuated


def test_double_tap_also_recovers_through_the_same_wrapper() -> None:
    driver = _ClearsOverlayAfterTwoScrollsDriver()
    ok, reason, gestures = _run(driver, {"doubleTap": {"id": "target"}})
    assert (ok, reason) == (True, "")
    assert gestures == ["scroll", "scroll", "doubleTap"]


def test_long_press_also_recovers_through_the_same_wrapper() -> None:
    driver = _ClearsOverlayAfterTwoScrollsDriver()
    ok, reason, gestures = _run(driver, {"longPress": {"sel": {"id": "target"}, "duration": 0.1}})
    assert (ok, reason) == (True, "")
    assert gestures == ["scroll", "scroll", "longPress"]


@pytest.mark.parametrize(
    ("step", "expected_gestures"),
    [
        (
            {"type": {"into": {"id": "target"}, "text": "x"}},
            ["scroll", "scroll", "tap", "typeText"],
        ),
        ({"clear": {"into": {"id": "target"}}}, ["scroll", "scroll", "tap"]),
        (
            {"delete": {"into": {"id": "target"}, "count": 3}},
            ["scroll", "scroll", "tap", "deleteText"],
        ),
        ({"select": {"into": {"id": "target"}}}, ["scroll", "scroll", "tap", "selectAll"]),
    ],
    ids=["type", "clear", "delete", "select"],
)
def test_focus_tap_also_recovers_through_the_same_wrapper(
    step: dict[str, object], expected_gestures: list[str]
) -> None:
    # `_do_type` / `_do_clear` / `_do_delete` / `_do_select` all route their focus-tap through
    # `_tap_with_recovery` too — the same wrapper `_do_tap` / `_do_double_tap` / `_do_long_press`
    # use above, not four more copies of the bare `driver.tap(sel)` call. `docs/selectors.md`
    # promises the check and the recovery for these focus-taps explicitly, so a regression here
    # (reverting any of the four to a bare `driver.tap(sel)`) would silently drop advertised
    # behavior without `make check` ever noticing.
    driver = _ClearsOverlayAfterTwoScrollsDriver()
    ok, reason, gestures = _run(driver, step)
    assert (ok, reason) == (True, "")
    assert gestures == expected_gestures

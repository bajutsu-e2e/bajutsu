"""Tests for the proactive notification-banner sweep (BE-0416 Units 2/3/8).

Unlike the interruption-monitor path (Unit 4, covered in `test_native_alert_guard.py`), nothing
here ever blocks a step: a banner sitting on screen with no interaction never reaches XCUITest's
interruption monitor at all — a plain query never invokes it — so this guard is what clears it
before it corrupts a screenshot. Covered against a `FakeDriver`, whose `notification_banner` field
and `notification_banner_frame()` method stand in for the out-of-process SpringBoard element the
real XCUITest driver queries.
"""

from __future__ import annotations

import logging

import pytest
from _orch import FakeClock, _scenario

from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import AlertGuardConfig, run_scenario

_BANNER_FRAME: base.Frame = (8.0, 58.7, 386.0, 78.7)


def _swipes(driver: FakeDriver) -> list[tuple[base.Point, base.Point]]:
    """The `(frm, to)` of each swipe the driver performed, in order.

    `FakeDriver.actions` logs `(kind, arg)` with `arg` typed `object`, so the read asserts the
    shape rather than casting it away (BE-0388).
    """
    swipes: list[tuple[base.Point, base.Point]] = []
    for kind, arg in driver.actions:
        if kind != "swipe":
            continue
        assert isinstance(arg, tuple) and len(arg) == 2
        swipes.append(arg)
    return swipes


def test_a_showing_banner_is_swiped_away_before_the_steps_own_shot() -> None:
    # No `systemAlertHandling` at all — proving the "no known use for a toggle" design: an ordinary
    # scenario with no opt-in still gets the banner cleared.
    driver = FakeDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario({"name": "a", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    swipes = _swipes(driver)
    assert swipes == [base.notification_banner_swipe_points(_BANNER_FRAME)]


def test_no_swipe_when_no_banner_is_present() -> None:
    driver = FakeDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    result = run_scenario(
        driver,
        _scenario({"name": "b", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert _swipes(driver) == []


def test_a_backend_without_the_capability_is_left_unchanged() -> None:
    # The capability gate: a backend that never advertises HANDLE_NOTIFICATION_BANNER must never be
    # asked, even with a banner seeded — the same opportunistic no-op `dismiss_blocking_tip` follows.
    class _NonCapableDriver(FakeDriver):
        def capabilities(self) -> set[str]:
            return super().capabilities() - {base.Capability.HANDLE_NOTIFICATION_BANNER}

    driver = _NonCapableDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario({"name": "c", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert _swipes(driver) == []


def test_a_banner_that_auto_dismisses_before_the_swipe_is_never_swiped() -> None:
    # The banner is present on the first read (the one that decides to swipe at all) but gone by
    # the re-check immediately before the gesture — the same race `RunnerUITest.swift`'s own
    # `banner.exists` guard exists to catch, so a flick is never delivered at its former position.
    reads = {"n": 0}

    class _AutoDismissingDriver(FakeDriver):
        def notification_banner_frame(self) -> base.Frame | None:
            reads["n"] += 1
            return _BANNER_FRAME if reads["n"] == 1 else None

    driver = _AutoDismissingDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    result = run_scenario(
        driver,
        _scenario({"name": "n", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert _swipes(driver) == []


def test_the_query_is_not_paid_on_every_step_within_the_poll_interval() -> None:
    # Three plain steps run at (near-)zero elapsed clock time — nothing here sleeps — so a large
    # poll_interval must rate-limit the query to the first step alone, not one call per step.
    probes = {"n": 0}

    class _CountingProbe(FakeDriver):
        def notification_banner_frame(self) -> base.Frame | None:
            probes["n"] += 1
            return None

    driver = _CountingProbe(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "d",
                "steps": [
                    {"assert": [{"exists": {"id": "home"}}]},
                    {"assert": [{"exists": {"id": "home"}}]},
                    {"assert": [{"exists": {"id": "home"}}]},
                ],
            }
        ),
        clock=FakeClock(),
        alert_guard=AlertGuardConfig(poll_interval=10.0),
    )
    assert result.ok, result.failure
    assert probes["n"] == 1, f"expected exactly one query across three steps, got {probes['n']}"


def test_the_query_fires_again_once_the_interval_elapses() -> None:
    # A `wait` step burns real (fake-clock) time between the two `assert` steps, past the interval —
    # proving the query re-fires on the interval rather than only once per scenario.
    probes = {"n": 0}

    class _CountingProbe(FakeDriver):
        def notification_banner_frame(self) -> base.Frame | None:
            probes["n"] += 1
            return None

    driver = _CountingProbe(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "e",
                "steps": [
                    {"assert": [{"exists": {"id": "home"}}]},
                    {"wait": {"for": {"id": "never.appears"}, "timeout": 2.0}},
                    {"assert": [{"exists": {"id": "home"}}]},
                ],
            }
        ),
        clock=FakeClock(),
        alert_guard=AlertGuardConfig(poll_interval=1.0),
    )
    assert not result.ok  # the middle `wait` times out by design, to burn clock time
    assert probes["n"] == 2, f"expected the query to re-fire after the interval, got {probes['n']}"


def test_swipe_points_are_anchored_to_the_measured_frame() -> None:
    # Pure geometry (BE-0416 Unit 3): the center of the frame, ending above its top edge.
    points = base.notification_banner_swipe_points(_BANNER_FRAME)
    assert points is not None
    frm, to = points
    x, y, w, h = _BANNER_FRAME
    assert frm == (x + w / 2, y + h / 2)
    assert to[0] == frm[0]  # straight up, same x
    assert to[1] < y  # ends above the banner's own top edge


def test_swipe_never_ends_past_the_top_margin() -> None:
    # A banner near the very top of the screen must not end the swipe within a few points of the
    # top edge, where SpringBoard would claim the drag as its own notification-shade gesture
    # instead — a tall enough frame that the sufficient-travel check does not itself reject it.
    points = base.notification_banner_swipe_points((0.0, 5.0, 100.0, 90.0))
    assert points is not None
    _frm, to = points
    assert to[1] >= 8.0


def test_a_degenerate_frame_is_left_alone() -> None:
    # A banner caught mid-appearance, close enough to the top margin and short enough that the
    # resulting gesture would travel less than the distance a real dismissal needs.
    assert base.notification_banner_swipe_points((0.0, 5.0, 100.0, 4.0)) is None, (
        "fixture no longer exercises the insufficient-travel case"
    )
    driver = FakeDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = (0.0, 5.0, 100.0, 4.0)
    result = run_scenario(
        driver,
        _scenario({"name": "f", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert _swipes(driver) == []


def test_the_swipe_is_confirmed_without_waiting_out_the_full_deadline() -> None:
    # The banner disappears (as a real swipe would clear it) on the very next query — the confirm
    # loop must notice immediately rather than spinning to its full clearance deadline.
    class _ClearingDriver(FakeDriver):
        def swipe(self, frm: base.Point, to: base.Point) -> None:
            super().swipe(frm, to)
            self.notification_banner = None

    driver = _ClearingDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = _BANNER_FRAME
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario({"name": "g", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=clock,
    )
    assert result.ok, result.failure
    assert len(_swipes(driver)) == 1
    assert clock.now() < 2.0, "the confirm loop burned its full clearance deadline"


def test_the_sweep_swipes_before_an_expect_phase_visual_capture(tmp_path: object) -> None:
    # `_capture_visual_actual` runs at the scenario's `expect` phase, not per step — a banner
    # sitting on screen there is not reached by the per-step sweep, so `run_scenario` clears it
    # again immediately before that capture (BE-0416 Unit 8).
    from pathlib import Path

    from bajutsu.common.assertions import EvalContext, VisualContext
    from bajutsu.common.evidence.redaction import Redactor
    from bajutsu.common.evidence.sink import RunArtifactWriter

    assert isinstance(tmp_path, Path)
    driver = FakeDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = _BANNER_FRAME
    vc = VisualContext(
        screenshot_path=tmp_path / "00-h" / "shot.png",
        baselines_dir=tmp_path / "baselines",
        writer=RunArtifactWriter(tmp_path, Redactor(None)),
        prefix="00-h",
    )
    run_scenario(
        driver,
        _scenario({"name": "h", "steps": [], "expect": [{"visual": {"baseline": "home.png"}}]}),
        clock=FakeClock(),
        ctx=EvalContext(visual=vc),
    )
    assert _swipes(driver) == [base.notification_banner_swipe_points(_BANNER_FRAME)]


def test_the_swipe_is_drained_from_the_expect_phase_at_once(tmp_path: object) -> None:
    # An expect-phase swipe left in the driver's own actuation log would otherwise strand until
    # some later drain (the next scenario's first step, since a lease's driver outlives one
    # scenario) picks it up as a phantom actuation nobody performed there.
    from pathlib import Path

    from bajutsu.common.assertions import EvalContext, VisualContext
    from bajutsu.common.evidence.redaction import Redactor
    from bajutsu.common.evidence.sink import RunArtifactWriter
    from bajutsu.common.orchestrator.types import drain_actuations

    assert isinstance(tmp_path, Path)
    driver = FakeDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = _BANNER_FRAME
    vc = VisualContext(
        screenshot_path=tmp_path / "00-j" / "shot.png",
        baselines_dir=tmp_path / "baselines",
        writer=RunArtifactWriter(tmp_path, Redactor(None)),
        prefix="00-j",
    )
    result = run_scenario(
        driver,
        _scenario({"name": "j", "steps": [], "expect": [{"visual": {"baseline": "home.png"}}]}),
        clock=FakeClock(),
        ctx=EvalContext(visual=vc),
    )
    assert any(a.gesture == "swipe" for a in result.expect_actuations), (
        "the expect-phase swipe was never drained into the scenario's own result"
    )
    # Nothing left behind in the driver's own log for a later step or scenario to misattribute.
    assert drain_actuations(driver).records == []


def test_the_expect_phase_sweeps_dropped_actuations_are_disclosed(tmp_path: object) -> None:
    # `_clear_notification_banner_before_visual_capture` has no `StepOutcome` of its own to carry
    # a truncated drain the way a step's `outcome.dropped_actuations` does, so a driver whose
    # bounded actuation log overflowed during the sweep's own swipe must surface that count on
    # `RunResult.dropped_expect_actuations` instead of discarding it.
    from pathlib import Path

    from bajutsu.common.assertions import EvalContext, VisualContext
    from bajutsu.common.drivers.actuation import Drained
    from bajutsu.common.evidence.redaction import Redactor
    from bajutsu.common.evidence.sink import RunArtifactWriter

    assert isinstance(tmp_path, Path)

    class _OverflowingDriver(FakeDriver):
        def drain_actuations(self) -> Drained:
            drained = super().drain_actuations()
            return Drained(records=drained.records, dropped=drained.dropped + 3)

    driver = _OverflowingDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = _BANNER_FRAME
    vc = VisualContext(
        screenshot_path=tmp_path / "00-m" / "shot.png",
        baselines_dir=tmp_path / "baselines",
        writer=RunArtifactWriter(tmp_path, Redactor(None)),
        prefix="00-m",
    )
    result = run_scenario(
        driver,
        _scenario({"name": "m", "steps": [], "expect": [{"visual": {"baseline": "home.png"}}]}),
        clock=FakeClock(),
        ctx=EvalContext(visual=vc),
    )
    assert result.dropped_expect_actuations == 3, (
        "the expect-phase sweep's own dropped actuations were discarded instead of disclosed"
    )


def test_a_short_travel_frame_is_left_alone_even_when_not_fully_inverted() -> None:
    # Not every insufficient swipe inverts outright: a banner still sliding in near the top margin
    # can leave the endpoint *above* the start (not inverted) while travelling far less than the
    # distance the Swift interruption-monitor path measured sufficient to dismiss a settled banner.
    frame: base.Frame = (0.0, -20.0, 386.0, 78.7)
    assert base.notification_banner_swipe_points(frame) is None, (
        "fixture no longer exercises a short (but not inverted) travel"
    )
    driver = FakeDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = frame
    result = run_scenario(
        driver,
        _scenario({"name": "k", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert _swipes(driver) == []


def test_the_sweep_is_stranded_actuation_free_across_a_web_block() -> None:
    # The sweep always actuates the native driver, even inside a `web` block whose own step
    # actuates a different (WebView) driver — the merge must fold the native swipe in after
    # whatever the block's own step recorded, matching the order the two really happened in.
    class _WebBridge:
        def query_dom(self, webview_id: str) -> list[base.Element]:
            return [
                {
                    "identifier": "go",
                    "label": "Go",
                    "traits": ["button"],
                    "value": None,
                    "frame": (0.0, 0.0, 50.0, 20.0),
                    "nativeZ": None,
                }
            ]

        def tap_element(self, webview_id: str, point: base.Point) -> None:
            pass

        def type_text(self, webview_id: str, text: str) -> None:
            pass

        def scroll_to(self, webview_id: str, element_id: str) -> None:
            pass

    driver = FakeDriver(
        [
            {
                "identifier": "checkout.webview",
                "label": "WebView",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 400.0, 800.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = _BANNER_FRAME
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "l",
                "steps": [
                    {
                        "web": {
                            "within": {"id": "checkout.webview"},
                            "steps": [{"tap": {"id": "go"}}],
                        }
                    }
                ],
            }
        ),
        clock=FakeClock(),
        webview_bridge=_WebBridge(),
    )
    assert result.ok, result.failure
    kinds = [a.gesture for a in result.steps[0].actuations]
    assert "tap" in kinds, kinds
    assert kinds[-1] == "swipe", (
        f"expected the native sweep's swipe last, after the web block's own actuations, got {kinds}"
    )
    assert kinds.index("tap") < kinds.index("swipe")


def test_an_unconfirmed_clearance_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    # A swipe that never clears within the deadline must not fail silently: a corrupted `after.png`
    # or a failed `visual` diff with nothing pointing at the banner reproduces, in miniature, the
    # invisibility this whole mechanism exists to end.
    driver = FakeDriver(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    driver.notification_banner = _BANNER_FRAME  # never clears, even after the swipe
    clock = FakeClock()
    with caplog.at_level(logging.WARNING):
        result = run_scenario(
            driver,
            _scenario({"name": "m", "steps": [{"assert": [{"exists": {"id": "home"}}]}]}),
            clock=clock,
        )
    assert result.ok, result.failure
    assert clock.now() >= 2.0, "the confirm loop must have run to its full clearance deadline"
    assert any("still on screen" in r.message for r in caplog.records), (
        "the unconfirmed clearance was never logged"
    )


def test_no_expect_phase_query_without_a_visual_assertion() -> None:
    # `_clear_notification_banner_before_visual_capture` is gated on an actual `visual` assertion —
    # a scenario with a plain `expect` must pay no extra query at that phase.
    probes = {"n": 0}

    class _CountingProbe(FakeDriver):
        def notification_banner_frame(self) -> base.Frame | None:
            probes["n"] += 1
            return None

    driver = _CountingProbe(
        [
            {
                "identifier": "home",
                "label": "Home",
                "traits": [],
                "value": None,
                "frame": (0.0, 0.0, 10.0, 10.0),
                "nativeZ": None,
            }
        ]
    )
    result = run_scenario(
        driver,
        _scenario({"name": "i", "steps": [], "expect": [{"exists": {"id": "home"}}]}),
        clock=FakeClock(),
        alert_guard=AlertGuardConfig(poll_interval=10.0),
    )
    assert result.ok, result.failure
    assert probes["n"] == 0

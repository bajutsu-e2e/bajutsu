"""Tests for the TipKit tip guard — dismissing a framework-owned popover that blocks a step.

A TipKit tip is not the app's own view, so no selector an author writes can name it; the driver
recognizes it and the orchestrator only asks "was one dismissed?". Both halves of the guard are
covered here against a FakeDriver: the mid-wait dismiss that clears a tip while a wait is still
blocked, and the post-failure retry for a tip already up when a step was attempted.

The tip is modelled the way it behaves on-device (measured): while it is showing, the content it
covers is *absent from the tree*, not merely occluded — so a blocked step fails as `ElementNotFound`
rather than `ElementNotTappable`, and a plain `is_tappable` occlusion model would not reproduce it.
"""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.orchestrator.types import AlertEvent

# Stands in for TipKit's dismiss region. The real identifier lives in the XCUITest driver; this is
# only what the fake was seeded with, so no iOS-specific name leaks into an orchestrator test.
_SCRIM = "tip.scrim"


def _tip_driver(*, covered: list[dict[str, object]]) -> FakeDriver:
    """A driver whose screen is a showing tip, hiding `covered` until the scrim is tapped."""

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == _SCRIM:
            d.screen = list(covered)

    driver = FakeDriver([el(_SCRIM, "dismiss popup")], react=react)
    driver.tipkit_dismiss_id = _SCRIM
    return driver


def test_tip_is_dismissed_mid_wait_and_the_wait_resumes_to_its_target() -> None:
    # Asserts on the clock, not just the outcome: the post-failure retry would *also* make this pass,
    # so the mid-wait gate is only proven by the wait finishing without burning its whole timeout.
    driver = _tip_driver(covered=[el("home.title", "Home")])
    clock = FakeClock()
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "a",
                "iosTipKitHandling": True,
                "steps": [{"wait": {"for": {"id": "home.title"}, "timeout": 10}}],
            }
        ),
        clock=clock,
    )
    assert result.ok, result.failure
    assert ("tap", {"id": _SCRIM}) in driver.actions
    assert clock.now() < 10, "the wait timed out and was rescued by the retry, not cleared mid-wait"


def test_a_step_blocked_by_a_tip_is_retried_once_after_the_dismiss() -> None:
    # No preceding wait, so the mid-wait gate never runs: this is the post-failure path, and the
    # target is absent from the tree until the tip goes, exactly as measured on-device.
    driver = _tip_driver(covered=[el("stable.refresh", "Refresh", ["button"])])
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "b",
                "iosTipKitHandling": True,
                "steps": [{"tap": {"id": "stable.refresh"}}],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    taps = [a.get("id") for k, a in driver.actions if k == "tap"]
    assert taps == [_SCRIM, "stable.refresh"]  # tip cleared, then the step's own act


def test_the_guard_is_off_unless_the_scenario_asks_for_it() -> None:
    # The default-off promise: with the tip showing and no `iosTipKitHandling`, the step fails as it
    # does today rather than being silently rescued.
    driver = _tip_driver(covered=[el("stable.refresh", "Refresh", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "c", "steps": [{"tap": {"id": "stable.refresh"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert ("tap", {"id": _SCRIM}) not in driver.actions


def test_a_tip_and_a_system_alert_are_both_recovered_in_one_step() -> None:
    # The two end-of-step guards are checked in sequence, not as an `elif` ladder: dismissing the tip
    # must not consume the failure and leave the alert — the case the alert guard exists for — unhandled.
    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == _SCRIM:
            # The tip goes, but the target is still behind the alert until the alert guard fires.
            d.screen = [el("sys.alert", "Allow")]

    driver = FakeDriver([el(_SCRIM, "dismiss popup")], react=react)
    driver.tipkit_dismiss_id = _SCRIM
    dismissed: list[str] = []

    def alert_guard(d: base.Driver) -> AlertEvent | None:
        # Stands in for the SpringBoard dismiss: clears the alert so the retry can find the target.
        assert isinstance(d, FakeDriver)
        if any(e["identifier"] == "sys.alert" for e in d.screen):
            dismissed.append("sys.alert")
            d.screen = [el("stable.refresh", "Refresh", ["button"])]
            return AlertEvent(label="Allow")
        return None

    result = run_scenario(
        driver,
        _scenario(
            {"name": "e", "iosTipKitHandling": True, "steps": [{"tap": {"id": "stable.refresh"}}]}
        ),
        clock=FakeClock(),
        alert_guard=alert_guard,
    )
    assert result.ok, result.failure
    assert dismissed == ["sys.alert"], (
        "the tip dismiss swallowed the failure and starved the alert guard"
    )


def test_two_dismiss_regions_fail_the_step_rather_than_aborting_the_run() -> None:
    # The driver refuses to guess between two dismiss regions (prime directive 2). That refusal is
    # raised outside `_run_step_body`'s own handler here, so unconverted it would unwind past
    # `run_scenario` and discard the verdicts of every scenario that already passed.
    driver = FakeDriver(
        [
            el(_SCRIM, "dismiss popup", frame=(0.0, 0.0, 402.0, 874.0)),
            el(_SCRIM, "dismiss popup", frame=(0.0, 0.0, 100.0, 100.0)),
        ]
    )
    driver.tipkit_dismiss_id = _SCRIM
    result = run_scenario(
        driver,
        _scenario(
            {"name": "f", "iosTipKitHandling": True, "steps": [{"tap": {"id": "stable.refresh"}}]}
        ),
        clock=FakeClock(),
    )
    assert not result.ok  # a failed step, not a raised exception


def test_a_refused_dismiss_tap_fails_the_step_rather_than_aborting_the_run() -> None:
    # The other way the dismiss refuses: the scrim resolves but its own point is not reachable, so
    # the tap raises `ElementNotTappable`. That is *not* a `SelectorError` (`_run_step_body`'s own
    # net lists the two separately), so it needs naming separately from the ambiguous case above —
    # miss it and the same whole-run abort returns through this path. Raised from the driver rather
    # than staged geometrically: a real scrim is full-screen, and the fake's document-order proxy
    # reads anything covering a full-screen frame's center as its descendant, so no seeded tree can
    # produce this refusal.
    class _RefusingDriver(FakeDriver):
        def dismiss_blocking_tip(self, tree: list[base.Element] | None = None) -> bool:
            raise base.ElementNotTappable("element resolved but covered by another element")

    driver = _RefusingDriver([el(_SCRIM, "dismiss popup", frame=(0.0, 0.0, 402.0, 874.0))])
    driver.tipkit_dismiss_id = _SCRIM
    result = run_scenario(
        driver,
        _scenario(
            {"name": "g", "iosTipKitHandling": True, "steps": [{"tap": {"id": "stable.refresh"}}]}
        ),
        clock=FakeClock(),
    )
    assert not result.ok  # a failed step, not a raised exception
    assert "covered by another element" in (result.failure or "")


def test_a_step_failing_with_no_tip_present_is_not_retried() -> None:
    # Nothing to dismiss, so the guard must not hand a genuine selector error a second attempt —
    # that is what would let a real failure look flaky instead of loud. A failed tap actuates
    # nothing, so counting attempts needs the resolution itself counted, not the action log.
    attempts = 0

    class _CountingDriver(FakeDriver):
        def tap(self, sel: base.Selector) -> None:
            nonlocal attempts
            attempts += 1
            super().tap(sel)

    driver = _CountingDriver([el("home.title", "Home")])
    driver.tipkit_dismiss_id = _SCRIM
    result = run_scenario(
        driver,
        _scenario(
            {"name": "d", "iosTipKitHandling": True, "steps": [{"tap": {"id": "does.not.exist"}}]}
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert attempts == 1, f"a failure with no tip present was retried ({attempts} attempts)"

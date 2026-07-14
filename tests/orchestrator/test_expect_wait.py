"""The trailing `expect` is a condition wait, not a single snapshot read (BE-0245).

A value an action mirrors into the accessibility tree can land a beat after the action returns
(Compose recomposes the `content-desc` asynchronously). A single end-of-scenario `query()` then
races that update: a slow read (`uiautomator dump`, ≈ 2.4 s) incidentally waited it out, but the
resident channel's ≈ 0.1 s read caught the pre-update value and failed a correct run. So `expect`
polls `query()` until the assertions pass or a wall-clock deadline — a condition wait, no fixed
sleep. Its budget is the lane's wait floor (`BAJUTSU_MIN_WAIT_TIMEOUT`), the same knob every other
condition wait honors: zero (a single read) on lanes that don't set it, and the Android e2e lane's
window where the race lives.
"""

from __future__ import annotations

import pytest
from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario

_FLOOR = "BAJUTSU_MIN_WAIT_TIMEOUT"


def _double_tap_scenario() -> object:
    # Mirrors demos/showcase/scenarios/gestures.yaml: double-tap a target, then expect the
    # mirrored counter to read "1".
    return _scenario(
        {
            "name": "x",
            "steps": [{"doubleTap": {"id": "doubletap"}}],
            "expect": [{"value": {"sel": {"id": "doubletap.value"}, "equals": "1"}}],
        }
    )


def test_expect_waits_for_an_async_mirrored_value(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(_FLOOR, "5")  # the lane floor gives `expect` a wait budget
    driver = FakeDriver(
        [el("doubletap", "Double Tap", ["button"]), el("doubletap.value", value="0")]
    )

    def on_sleep(t: float) -> None:
        # The counter mirror flips one poll after the action, as a fast resident read would observe.
        if t >= 0.1:
            driver.screen = [
                el("doubletap", "Double Tap", ["button"]),
                el("doubletap.value", value="1"),
            ]

    result = run_scenario(driver, _double_tap_scenario(), clock=FakeClock(on_sleep))
    assert result.ok, result.failure


def test_expect_fails_after_the_deadline_when_the_value_never_arrives(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "1")
    driver = FakeDriver(
        [el("doubletap", "Double Tap", ["button"]), el("doubletap.value", value="0")]
    )
    clock = FakeClock()  # no on_sleep: the mirror never updates
    result = run_scenario(driver, _double_tap_scenario(), clock=clock)
    assert not result.ok
    assert result.failure is not None and result.failure.startswith("expect:")
    # A bounded condition wait: it polled past the deadline rather than reading once or looping
    # forever.
    assert clock.now() >= 1.0


def test_expect_is_single_shot_when_no_wait_floor_is_set() -> None:
    # Zero regression off the Android lane: with no floor the budget is zero, so a failing value
    # `expect` fails on the first read exactly as before — no poll, no wall-clock cost.
    driver = FakeDriver(
        [el("doubletap", "Double Tap", ["button"]), el("doubletap.value", value="0")]
    )
    clock = FakeClock()
    result = run_scenario(driver, _double_tap_scenario(), clock=clock)
    assert not result.ok
    assert clock.now() == 0.0


def test_expect_that_already_holds_returns_without_waiting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "5")  # even with a budget, a passing expect never sleeps
    driver = FakeDriver(
        [el("doubletap", "Double Tap", ["button"]), el("doubletap.value", value="1")]
    )
    clock = FakeClock()
    result = run_scenario(driver, _double_tap_scenario(), clock=clock)
    assert result.ok, result.failure
    assert clock.now() == 0.0  # passed on the first read, no poll sleep


def test_expect_does_not_wait_on_a_failure_a_tree_reread_cannot_fix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A network `responseSchema` (no matching exchange) can't be helped by re-reading the UI tree,
    # so even with a wait budget the condition wait must not idle to the deadline for it — it fails
    # on the first read.
    monkeypatch.setenv(_FLOOR, "5")
    driver = FakeDriver([el("ok", "OK", ["button"])])
    clock = FakeClock()
    scenario = _scenario(
        {
            "name": "x",
            "steps": [{"tap": {"id": "ok"}}],
            "expect": [
                {"responseSchema": {"request": {"path": "/api/items"}, "schema": "items.json"}}
            ],
        }
    )
    result = run_scenario(driver, scenario, clock=clock)
    assert not result.ok
    assert clock.now() == 0.0  # no poll sleep: a tree re-read cannot satisfy a network assertion

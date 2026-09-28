"""Tests for the app (cross-app UI control) step in the run loop."""

from __future__ import annotations

from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.cancellation import CANCELLED_FAILURE
from bajutsu.common.drivers import base
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario


def test_app_step_taps_element_in_the_foreign_apps_tree() -> None:
    native_screen = [el("native-only", "Native")]
    driver = FakeDriver(native_screen)
    driver.apps["com.example.a"] = [el("confirm", "Confirm", ["button"])]
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "app tap",
                "steps": [
                    {
                        "app": {
                            "bundleId": "com.example.a",
                            "steps": [{"tap": {"id": "confirm"}}],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    # `confirm` exists only on the foreign app's seeded tree, not the native screen — this can only
    # pass if the tap actually resolved against the swapped-in tree.
    assert result.ok, result.failure
    assert [a[1] for a in driver.actions if a[0] == "tap"] == [{"id": "confirm"}]


def test_native_step_after_app_step_runs_on_the_restored_screen() -> None:
    native_screen = [el("native-only", "Native", ["button"])]
    driver = FakeDriver(native_screen)
    driver.apps["com.example.a"] = [el("confirm", "Confirm", ["button"])]
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "app then native",
                "steps": [
                    {
                        "app": {
                            "bundleId": "com.example.a",
                            "steps": [{"tap": {"id": "confirm"}}],
                        },
                    },
                    {"tap": {"id": "native-only"}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert [a[1] for a in driver.actions if a[0] == "tap"] == [
        {"id": "confirm"},
        {"id": "native-only"},
    ]
    assert driver.screen == native_screen


def test_app_step_nested_returns_to_immediate_parent_not_the_outermost_screen() -> None:
    # The exact sequence the feasibility spike measured by hand (Safari -> Maps -> back -> ...):
    # entering a second app from inside the first, then leaving it, must land back on the *first*
    # app's own tree, not unconditionally on the native one.
    native_screen = [el("native-only", "Native")]
    driver = FakeDriver(native_screen)
    driver.apps["com.example.a"] = [el("a-only", "A only", ["button"])]
    driver.apps["com.example.b"] = [el("b-only", "B only", ["button"])]
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "nested app",
                "steps": [
                    {
                        "app": {
                            "bundleId": "com.example.a",
                            "steps": [
                                {
                                    "app": {
                                        "bundleId": "com.example.b",
                                        "steps": [{"tap": {"id": "b-only"}}],
                                    },
                                },
                                # Only resolvable if leaving B restored A's tree, not native's.
                                {"tap": {"id": "a-only"}},
                            ],
                        },
                    },
                    {"tap": {"id": "native-only"}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert result.ok, result.failure
    assert [a[1] for a in driver.actions if a[0] == "tap"] == [
        {"id": "b-only"},
        {"id": "a-only"},
        {"id": "native-only"},
    ]
    assert driver.screen == native_screen


def test_app_step_failure_still_restores_the_previous_screen() -> None:
    # `leave_app` runs in a `finally`: a failing nested step must not leave the device
    # foregrounded on the wrong app for the rest of the run.
    native_screen = [el("native-only", "Native")]
    driver = FakeDriver(native_screen)
    driver.apps["com.example.a"] = [el("a-only", "A only")]
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "app failure",
                "steps": [
                    {
                        "app": {
                            "bundleId": "com.example.a",
                            "steps": [{"tap": {"id": "nonexistent"}}],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert driver.screen == native_screen


class _RaisingEnterAppDriver(FakeDriver):
    """A `FakeDriver` whose `enter_app` raises, standing in for a real bundle id that never
    reaches the foreground (the real `XcuitestDriver` raises `ElementNotFound` there) — a path
    `FakeDriver.enter_app` itself never takes by design, so `_handle_app`'s own
    exception handling needs this stub to exercise it."""

    def enter_app(self, bundle_id: str) -> None:
        raise base.ElementNotFound(f"app did not reach the foreground: {bundle_id!r}")


def test_app_step_enter_failure_fails_the_step_without_crashing_the_run() -> None:
    native_screen = [el("native-only", "Native")]
    driver = _RaisingEnterAppDriver(native_screen)
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "app enter failure",
                "steps": [
                    {"app": {"bundleId": "com.example.missing", "steps": [{"tap": {"id": "x"}}]}},
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "did not reach the foreground" in (result.failure or "")


def test_app_step_with_no_seeded_tree_swaps_to_an_empty_screen() -> None:
    # An unseeded bundle id is not a fixture error: the fake swaps to an empty screen
    # rather than raising, matching its "unseeded = empty" convention elsewhere — so a selector
    # that matched the native screen must not resolve inside the block.
    native_screen = [el("native-only", "Native")]
    driver = FakeDriver(native_screen)
    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "app unseeded",
                "steps": [
                    {
                        "app": {
                            "bundleId": "com.example.unseeded",
                            "steps": [{"tap": {"id": "native-only"}}],
                        },
                    },
                ],
            }
        ),
        clock=FakeClock(),
    )
    assert not result.ok  # "native-only" is not on the unseeded (empty) foreign screen
    assert driver.screen == native_screen  # restored despite the nested failure


class _RaisingLeaveAppDriver(FakeDriver):
    """A `FakeDriver` whose `leave_app` always raises, standing in for a real device that never
    re-reaches the foreground on the way out. Used to prove that a leave failure during cleanup
    does not replace whatever exception was already propagating out of the block — a real
    `XcuitestDriver.leave_app` failure must not turn a cancelled run into a plain step failure."""

    def __init__(self, screen: list[base.Element]) -> None:
        super().__init__(screen)
        self.leave_app_calls = 0

    def leave_app(self) -> None:
        self.leave_app_calls += 1
        raise base.ElementNotFound("app did not reach the foreground while leaving")


def test_app_step_cancelled_mid_block_still_wins_over_a_leave_app_failure() -> None:
    native_screen = [el("native-only", "Native")]
    driver = _RaisingLeaveAppDriver(native_screen)
    driver.apps["com.example.a"] = [el("a-only", "A only")]
    clock = FakeClock()
    reads = 0

    def cancelled() -> bool:
        nonlocal reads
        reads += 1
        return reads > 1  # read 1 is the app: step's own boundary; read 2 is the wait's first poll

    result = run_scenario(
        driver,
        _scenario(
            {
                "name": "app cancelled mid-block",
                "steps": [
                    {
                        "app": {
                            "bundleId": "com.example.a",
                            "steps": [{"wait": {"for": {"id": "never"}, "timeout": 100.0}}],
                        },
                    },
                ],
            }
        ),
        clock=clock,
        cancelled=cancelled,
    )
    # The cancellation is what the caller must see — not a leave_app failure layered on top of it.
    assert result.failure == CANCELLED_FAILURE
    assert driver.leave_app_calls == 1  # cleanup was still attempted, and its failure was logged

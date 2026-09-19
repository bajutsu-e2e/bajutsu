"""Tests for `FakeDriver.enter_app`/`leave_app`'s in-memory app-stack simulation.

The orchestrator-level nesting/restore behavior is covered end to end in
`tests/orchestrator/test_app_step.py`; these tests exercise the fake in isolation, including the
one path a scenario never reaches through `_handle_app` (a `leave_app()` with no matching
`enter_app()`).
"""

from __future__ import annotations

from conftest import el

from bajutsu.common.drivers.fake import FakeDriver


def test_enter_app_swaps_to_the_seeded_tree_and_records_the_call() -> None:
    native = [el("native-only")]
    driver = FakeDriver(native)
    driver.apps["com.example"] = [el("foreign-only")]

    driver.enter_app("com.example")

    assert driver.screen == [el("foreign-only")]
    assert ("enter_app", "com.example") in driver.actions


def test_enter_app_with_no_seeded_tree_swaps_to_empty() -> None:
    driver = FakeDriver([el("native-only")])
    driver.enter_app("com.example.unseeded")
    assert driver.screen == []


def test_leave_app_restores_the_previous_screen() -> None:
    native = [el("native-only")]
    driver = FakeDriver(native)
    driver.apps["com.example"] = [el("foreign-only")]
    driver.enter_app("com.example")

    driver.leave_app()

    assert driver.screen == native
    assert ("leave_app", None) in driver.actions


def test_leave_app_with_no_matching_enter_app_is_a_no_op() -> None:
    native = [el("native-only")]
    driver = FakeDriver(native)
    driver.leave_app()  # tolerated, not raised — mirrors the real backend's own tolerance
    assert driver.screen == native

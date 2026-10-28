"""The runner reads an `attribute` assertion's element beside each tree it polls (BE-0466).

The read follows `_poll_asserts`: an attribute a page settles a beat after an action passes within
the lane's wait floor, and an `if` / `interrupts` condition reads it against the tree it decides on.
"""

from __future__ import annotations

import pytest
from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.scenario import Interrupt

_FLOOR = "BAJUTSU_MIN_WAIT_TIMEOUT"


def _state(value: str) -> dict[str, object]:
    return {"attribute": {"sel": {"id": "cart"}, "name": "data-state", "equals": value}}


def _driver(state: str | None) -> FakeDriver:
    driver = FakeDriver([el("cart", "Cart", ["button"]), el("go", "Go", ["button"])])
    if state is not None:
        driver.attributes[id(driver.screen[0])] = {"data-state": state}
    return driver


def test_an_attribute_that_settles_late_passes_within_the_wait_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(_FLOOR, "5")
    driver = _driver("loading")

    def on_sleep(t: float) -> None:
        if t >= 0.1:  # the page flips the attribute one poll after the action
            driver.attributes[id(driver.screen[0])] = {"data-state": "ready"}

    sc = _scenario({"name": "a", "steps": [{"tap": {"id": "go"}}, {"assert": [_state("ready")]}]})
    result = run_scenario(driver, sc, clock=FakeClock(on_sleep))
    assert result.ok, result.failure


def test_an_attribute_that_never_settles_fails_with_the_actual_value() -> None:
    driver = _driver("loading")
    sc = _scenario({"name": "a", "steps": [{"tap": {"id": "go"}}], "expect": [_state("ready")]})
    result = run_scenario(driver, sc, clock=FakeClock())
    assert not result.ok
    assert result.failure is not None
    assert "expected equals='ready' but actual='loading'" in result.failure


def test_an_ambiguous_selector_fails_without_a_read() -> None:
    driver = FakeDriver([el("cart", "A", ["button"]), el("cart", "B", ["button"])])
    sc = _scenario({"name": "a", "steps": [{"assert": [_state("ready")]}]})
    result = run_scenario(driver, sc, clock=FakeClock())
    assert not result.ok
    assert result.failure is not None and "2 elements matched" in result.failure


def test_an_if_condition_reads_the_attribute_to_pick_the_branch() -> None:
    for state, want in (("ready", "go"), ("idle", "cart")):
        driver = _driver(state)
        sc = _scenario(
            {
                "name": "a",
                "steps": [
                    {
                        "if": {
                            "condition": _state("ready"),
                            "then": [{"tap": {"id": "go"}}],
                            "else": [{"tap": {"id": "cart"}}],
                        }
                    }
                ],
            }
        )
        result = run_scenario(driver, sc, clock=FakeClock())
        assert result.ok, result.failure
        assert [a for a in driver.actions if a[0] == "tap"] == [("tap", {"id": want})]


def test_an_interrupt_condition_reads_the_attribute_to_fire() -> None:
    driver = _driver("open")

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and getattr(arg, "get", lambda _k: None)("id") == "cart":
            d.attributes[id(d.screen[0])] = {"data-state": "closed"}
            d.screen = [*d.screen, el("home.title", "Home")]

    driver._react = react  # wired after seeding, so the hook can reach the seeded element
    entry = Interrupt.model_validate(
        {"condition": _state("open"), "steps": [{"tap": {"id": "cart"}}]}
    )
    sc = _scenario({"name": "a", "steps": [{"wait": {"for": {"id": "home.title"}, "timeout": 10}}]})
    result = run_scenario(driver, sc, clock=FakeClock(), interrupts=[entry])
    assert result.ok, result.failure
    assert ("tap", {"id": "cart"}) in driver.actions

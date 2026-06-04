"""Tests for the orchestrator run loop.

Use FakeDriver (in-memory backend) and FakeClock (sleep advances time) to test
act -> wait -> verify deterministically without a Simulator.
"""

from __future__ import annotations

from collections.abc import Callable

from simyoke.drivers import base
from simyoke.drivers.fake import FakeDriver
from simyoke.orchestrator import run_scenario
from simyoke.scenario import Scenario


class FakeClock:
    """Advance logical time on sleep; `on_sleep` mutates the world over time."""

    def __init__(self, on_sleep: Callable[[float], None] | None = None) -> None:
        self._t = 0.0
        self.on_sleep = on_sleep

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds
        if self.on_sleep is not None:
            self.on_sleep(self._t)


def _el(
    identifier: str | None = None,
    label: str | None = None,
    traits: list[str] | None = None,
    value: str | None = None,
) -> base.Element:
    return {
        "identifier": identifier,
        "label": label,
        "traits": traits or [],
        "value": value,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def _scenario(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate(data)


def test_happy_path_tap_and_expect() -> None:
    driver = FakeDriver([_el("home.title", "ホーム"), _el("settings.open", "設定", ["button"])])
    result = run_scenario(
        driver,
        _scenario({
            "name": "open settings",
            "steps": [{"tap": {"id": "settings.open"}}],
            "expect": [{"exists": {"id": "home.title"}}],
        }),
        clock=FakeClock(),
    )
    assert result.ok
    assert driver.actions == [("tap", {"id": "settings.open"})]


def test_react_transition_then_expect() -> None:
    home = [_el("settings.open", "設定", ["button"])]
    settings = [_el("settings.reindex", "再生成", ["button"]), _el("settings.title", "設定")]

    def react(d: FakeDriver, kind: str, arg: object) -> None:
        if kind == "tap" and arg == {"id": "settings.open"}:
            d.screen = settings

    driver = FakeDriver(home, react=react)
    result = run_scenario(
        driver,
        _scenario({
            "name": "drill into settings",
            "steps": [{"tap": {"id": "settings.open"}}, {"tap": {"id": "settings.reindex"}}],
            "expect": [{"exists": {"id": "settings.title"}}],
        }),
        clock=FakeClock(),
    )
    assert result.ok


def test_tap_not_found_fails_and_stops() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"id": "missing"}}, {"tap": {"id": "a"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert result.failure is not None and "step 0" in result.failure
    assert len(result.steps) == 1  # stops after the failing step


def test_tap_ambiguous_fails() -> None:
    driver = FakeDriver([_el("row.1", "A", ["cell"]), _el("row.2", "B", ["cell"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"tap": {"idMatches": "row.*"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "件一致" in result.steps[0].reason  # ambiguous


def test_assert_step_intermediate() -> None:
    driver = FakeDriver([_el("counter", "c", ["staticText"], value="3")])
    ok = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"assert": [{"value": {"sel": {"id": "counter"}, "equals": "3"}}]}]}),
        clock=FakeClock(),
    )
    assert ok.ok
    bad = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"assert": [{"value": {"sel": {"id": "counter"}, "equals": "4"}}]}]}),
        clock=FakeClock(),
    )
    assert not bad.ok
    assert bad.steps[0].assertion_results[0].ok is False


def test_wait_for_appears() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        if t >= 0.1 and all(e["identifier"] != "ready" for e in driver.screen):
            driver.screen = [*driver.screen, _el("ready", "R")]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "ready"}, "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_timeout() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"for": {"id": "never"}, "timeout": 0.2}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "timeout" in result.steps[0].reason


def test_wait_until_gone() -> None:
    driver = FakeDriver([_el("spinner", "")])

    def on_sleep(t: float) -> None:
        if t >= 0.1:
            driver.screen = []

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": {"gone": {"id": "spinner"}}, "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_screen_changed() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])

    def on_sleep(t: float) -> None:
        if t >= 0.1:
            driver.screen = [_el("b", "B", ["button"])]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "screenChanged", "timeout": 1.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok


def test_wait_settled_waits_for_a_stable_screen() -> None:
    driver = FakeDriver([_el("home", "Home", ["button"])])

    def on_sleep(t: float) -> None:
        if t < 0.15:  # a transition still in progress: the frame keeps moving
            driver.screen = [{
                "identifier": "home", "label": "Home", "traits": ["button"],
                "value": None, "frame": (t, 0.0, 10.0, 10.0),
            }]

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 2.0}}]}),
        clock=FakeClock(on_sleep),
    )
    assert result.ok and result.steps[0].ok


def test_wait_settled_proceeds_on_blank_screen() -> None:
    driver = FakeDriver([])  # collapsed / covered: never settles, but must not fail the step

    result = run_scenario(
        driver,
        _scenario({"name": "x", "steps": [{"wait": {"until": "settled", "timeout": 0.3}}]}),
        clock=FakeClock(),
    )
    assert result.ok and result.steps[0].ok


def test_type_and_swipe_actions() -> None:
    driver = FakeDriver([_el("search.field", "検索", ["textField"]), _el("list", "", ["table"])])
    result = run_scenario(
        driver,
        _scenario({
            "name": "x",
            "steps": [
                {"type": {"text": "hello", "into": {"id": "search.field"}}},
                {"swipe": {"on": {"id": "list"}, "direction": "up"}},
                {"swipe": {"from": [1, 2], "to": [3, 4]}},
            ],
        }),
        clock=FakeClock(),
    )
    assert result.ok
    assert [a[0] for a in driver.actions] == ["tap", "type", "swipe", "swipe"]


def test_expect_failure() -> None:
    driver = FakeDriver([_el("a", "A", ["button"])])
    result = run_scenario(
        driver,
        _scenario({
            "name": "x",
            "steps": [{"tap": {"id": "a"}}],
            "expect": [{"exists": {"id": "missing"}}],
        }),
        clock=FakeClock(),
    )
    assert not result.ok
    assert result.failure is not None and result.failure.startswith("expect:")

"""Tests for extended device-control primitives: background, overrideStatusBar, clearStatusBar (BE-0035)."""

from __future__ import annotations

from conftest import el

from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import run_scenario
from bajutsu.scenario import Scenario, Step


class FakeClock:
    def __init__(self) -> None:
        self._t = 0.0

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self._t += seconds


def _scenario(data: dict[str, object]) -> Scenario:
    return Scenario.model_validate(data)


# --- schema ---


def test_background_step_parses() -> None:
    step = Step.model_validate({"background": {}})
    assert step.background is not None


def test_override_status_bar_step_parses() -> None:
    step = Step.model_validate(
        {"overrideStatusBar": {"time": "9:41", "batteryLevel": 100, "wifiBars": 3}}
    )
    assert step.override_status_bar is not None
    assert step.override_status_bar.time == "9:41"
    assert step.override_status_bar.battery_level == 100
    assert step.override_status_bar.wifi_bars == 3


def test_clear_status_bar_step_parses() -> None:
    step = Step.model_validate({"clearStatusBar": {}})
    assert step.clear_status_bar is not None


# --- runtime (requires control) ---


def test_background_requires_device_control() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario({"name": "bg", "steps": [{"background": {}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "background" in (result.failure or "")


def test_override_status_bar_requires_device_control() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario({"name": "osb", "steps": [{"overrideStatusBar": {"time": "9:41"}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "overrideStatusBar" in (result.failure or "")


def test_clear_status_bar_requires_device_control() -> None:
    result = run_scenario(
        FakeDriver([el("x", "X")]),
        _scenario({"name": "csb", "steps": [{"clearStatusBar": {}}]}),
        clock=FakeClock(),
    )
    assert not result.ok
    assert "clearStatusBar" in (result.failure or "")


# --- env command builders ---


def test_home_cmd() -> None:
    from bajutsu.env import home_cmd

    # simctl has no Home-button command; launching SpringBoard backgrounds the app instead.
    assert home_cmd("U") == ["xcrun", "simctl", "launch", "U", "com.apple.springboard"]


def test_status_bar_override_cmd() -> None:
    from bajutsu.env import status_bar_override_cmd

    cmd = status_bar_override_cmd("U", time="9:41", battery_level=100)
    assert cmd[:5] == ["xcrun", "simctl", "status_bar", "U", "override"]
    assert "--time" in cmd and "9:41" in cmd
    assert "--batteryLevel" in cmd and "100" in cmd


def test_status_bar_override_cmd_empty() -> None:
    from bajutsu.env import status_bar_override_cmd

    assert status_bar_override_cmd("U") == ["xcrun", "simctl", "status_bar", "U", "override"]


def test_status_bar_clear_cmd() -> None:
    from bajutsu.env import status_bar_clear_cmd

    assert status_bar_clear_cmd("U") == ["xcrun", "simctl", "status_bar", "U", "clear"]

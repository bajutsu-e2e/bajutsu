"""Tests for the system-alert guard, the Claude locator, and the orchestrator hook."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from simyoke.alerts import AlertDecision, ClaudeAlertLocator, SystemAlertGuard
from simyoke.drivers import base
from simyoke.drivers.fake import FakeDriver
from simyoke.orchestrator import run_scenario
from simyoke.scenario import load_scenarios


def _window(w: float = 402.0, h: float = 874.0) -> base.Element:
    return {
        "identifier": None,
        "label": "App",
        "traits": ["application"],
        "value": None,
        "frame": (0.0, 0.0, w, h),
    }


class ShotDriver(FakeDriver):
    """FakeDriver whose screenshot writes real bytes so the guard can read them."""

    def screenshot(self, path: str) -> None:
        Path(path).write_bytes(b"\x89PNG\r\n\x1a\n fake")
        self.actions.append(("screenshot", path))


class StubLocator:
    def __init__(self, decision: AlertDecision) -> None:
        self.decision = decision
        self.seen: list[tuple[bytes, str | None]] = []

    def locate(self, screenshot_png: bytes, instruction: str | None) -> AlertDecision:
        self.seen.append((screenshot_png, instruction))
        return self.decision


# --- SystemAlertGuard ---


def test_guard_taps_normalized_point_when_present() -> None:
    driver = ShotDriver([_window(402.0, 874.0)])
    guard = SystemAlertGuard(StubLocator(AlertDecision(present=True, x=0.5, y=0.25)))
    assert guard.dismiss(driver) is True
    # normalized (0.5, 0.25) maps to point space (402, 874)
    assert ("tap_point", (201.0, 218.5)) in driver.actions
    assert any(a[0] == "screenshot" for a in driver.actions)


def test_guard_noop_when_absent() -> None:
    driver = ShotDriver([_window()])
    assert SystemAlertGuard(StubLocator(AlertDecision(present=False))).dismiss(driver) is False
    assert not any(a[0] == "tap_point" for a in driver.actions)


def test_guard_passes_instruction_to_locator() -> None:
    driver = ShotDriver([_window()])
    locator = StubLocator(AlertDecision(present=False))
    SystemAlertGuard(locator, instruction="tap Save").dismiss(driver)
    assert locator.seen[0][1] == "tap Save"


def test_guard_noop_when_no_screenshot() -> None:
    driver = FakeDriver([_window()])  # base FakeDriver writes no bytes -> no screenshot
    locator = StubLocator(AlertDecision(present=True, x=0.5, y=0.5))
    assert SystemAlertGuard(locator).dismiss(driver) is False
    assert locator.seen == []  # never reached the locator


# --- ClaudeAlertLocator (fake Anthropic client) ---


class _Block:
    def __init__(self, inp: dict[str, Any]) -> None:
        self.type = "tool_use"
        self.name = "resolve_alert"
        self.input = inp


class _Message:
    def __init__(self, block: _Block) -> None:
        self.content = [block]


class _Messages:
    def __init__(self, message: _Message, calls: list[dict[str, Any]]) -> None:
        self._message = message
        self._calls = calls

    def create(self, **kwargs: Any) -> _Message:
        self._calls.append(kwargs)
        return self._message


class FakeClient:
    def __init__(self, inp: dict[str, Any]) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = _Messages(_Message(_Block(inp)), self.calls)


def test_locator_parses_present_decision() -> None:
    client = FakeClient({"present": True, "x": 0.31, "y": 0.62, "label": "Not Now"})
    decision = ClaudeAlertLocator(client=client).locate(b"\x89PNG", "tap Save")
    assert decision.present is True
    assert decision.label == "Not Now"
    assert abs(decision.x - 0.31) < 1e-9 and abs(decision.y - 0.62) < 1e-9
    call = client.calls[0]
    assert call["tool_choice"] == {"type": "any"}
    content = call["messages"][0]["content"]
    assert any(c["type"] == "image" for c in content)
    assert "tap Save" in next(c["text"] for c in content if c["type"] == "text")


def test_locator_absent_decision() -> None:
    decision = ClaudeAlertLocator(client=FakeClient({"present": False})).locate(b"x", None)
    assert decision.present is False


# --- orchestrator on_blocked retry ---

_TAP_GO = "- name: t\n  steps:\n    - tap: { id: go }\n"


def test_on_blocked_retries_step_after_recovery() -> None:
    target: base.Element = {
        "identifier": "go",
        "label": "Go",
        "traits": ["button"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }
    driver = FakeDriver([])  # empty screen -> the tap fails to resolve

    def on_blocked(d: base.Driver) -> bool:
        assert isinstance(d, FakeDriver)
        d.screen = [target]  # "dismiss the alert": the app reappears
        return True

    result = run_scenario(driver, load_scenarios(_TAP_GO)[0], on_blocked=on_blocked)
    assert result.ok is True
    assert ("tap", {"id": "go"}) in driver.actions


def test_failure_stands_without_handler() -> None:
    driver = FakeDriver([])
    result = run_scenario(driver, load_scenarios(_TAP_GO)[0])
    assert result.ok is False

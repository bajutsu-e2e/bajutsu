"""Tests for the system-alert guard, the Claude locator, and the orchestrator hook."""

from __future__ import annotations

import struct

from conftest import FakeBackend, FakeBlock, ShotDriver

from bajutsu.agents.alerts import AlertDecision, ClaudeAlertLocator, SystemAlertGuard
from bajutsu.agents.protocols import Proposal
from bajutsu.ai.base import AnyTool, ImagePart, TextPart
from bajutsu.drivers import base
from bajutsu.drivers.fake import FakeDriver
from bajutsu.orchestrator import AlertEvent, AlertGuardConfig, run_scenario
from bajutsu.record import record as record_loop
from bajutsu.scenario import Step, load_scenarios


def _window(w: float = 402.0, h: float = 874.0) -> base.Element:
    return {
        "identifier": None,
        "label": "App",
        "traits": ["application"],
        "value": None,
        "frame": (0.0, 0.0, w, h),
    }


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
    guard = SystemAlertGuard(
        StubLocator(AlertDecision(present=True, x=0.5, y=0.25, label="Not Now"))
    )
    # The dismissal returns an AlertEvent carrying the tapped button (for the report).
    assert guard.dismiss(driver) == AlertEvent(label="Not Now")
    # normalized (0.5, 0.25) maps to point space (402, 874)
    assert ("tap_point", (201.0, 218.5)) in driver.actions
    assert any(a[0] == "screenshot" for a in driver.actions)


def test_guard_noop_when_absent() -> None:
    driver = ShotDriver([_window()])
    assert SystemAlertGuard(StubLocator(AlertDecision(present=False))).dismiss(driver) is None
    assert not any(a[0] == "tap_point" for a in driver.actions)


def test_guard_passes_instruction_to_locator() -> None:
    driver = ShotDriver([_window()])
    locator = StubLocator(AlertDecision(present=False))
    SystemAlertGuard(locator, instruction="tap Save").dismiss(driver)
    assert locator.seen[0][1] == "tap Save"


class _RaisingLocator:
    """A locator that fails (e.g. no API key / a transient API error)."""

    def locate(self, screenshot_png: bytes, instruction: str | None) -> AlertDecision:
        raise RuntimeError("no ANTHROPIC_API_KEY")


def test_guard_is_best_effort_when_locator_raises() -> None:
    # The guard is on by default, so a failing locator must no-op (return None), never crash a run.
    driver = ShotDriver([_window()])
    assert SystemAlertGuard(_RaisingLocator()).dismiss(driver) is None
    assert not any(a[0] == "tap_point" for a in driver.actions)


def test_guard_noop_when_no_screenshot() -> None:
    driver = FakeDriver([_window()])  # base FakeDriver writes no bytes -> no screenshot
    locator = StubLocator(AlertDecision(present=True, x=0.5, y=0.5))
    assert SystemAlertGuard(locator).dismiss(driver) is None
    assert locator.seen == []  # never reached the locator


# --- ClaudeAlertLocator (fake AI backend, BE-0104) ---


def _resolve_alert(inp: dict[str, object]) -> FakeBackend:
    """A fake backend whose single tool call is the alert locator's `resolve_alert`."""
    return FakeBackend(FakeBlock("resolve_alert", dict(inp)))


def _png(width: int, height: int) -> bytes:
    """A minimal PNG whose IHDR advertises the given pixel size (enough for png_size)."""
    ihdr = struct.pack(">II", width, height) + b"\x08\x06\x00\x00\x00"
    return b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + ihdr


def test_locator_normalizes_pixel_coordinates() -> None:
    backend = _resolve_alert({"present": True, "x": 374, "y": 1611, "label": "Not Now"})
    decision = ClaudeAlertLocator(backend=backend).locate(_png(1206, 2622), "tap Save")
    assert decision.present is True and decision.label == "Not Now"
    assert abs(decision.x - 374 / 1206) < 1e-6
    assert abs(decision.y - 1611 / 2622) < 1e-6
    request = backend.requests[0]
    assert isinstance(request.tool_choice, AnyTool)
    content = request.messages[0].content
    assert any(isinstance(c, ImagePart) for c in content)
    text = next(c.text for c in content if isinstance(c, TextPart))
    assert "1206x2622" in text and "tap Save" in text


def test_locator_absent_decision() -> None:
    decision = ClaudeAlertLocator(backend=_resolve_alert({"present": False})).locate(
        _png(10, 10), None
    )
    assert decision.present is False


def test_locator_redacts_instruction_before_send() -> None:
    # BE-0047: the (possibly user-supplied) --alert-instruction is redacted before it reaches the
    # model. The screenshot beside it is sent as-is — images cannot be pixel-masked.
    from bajutsu.evidence.redaction import Redactor
    from bajutsu.scenario import Redact

    backend = _resolve_alert({"present": False})
    redactor = Redactor(Redact(), values=["sk-secret-token"])
    ClaudeAlertLocator(backend=backend, redactor=redactor).locate(
        _png(10, 10), "tap Save then enter sk-secret-token"
    )
    text = next(c.text for c in backend.requests[0].messages[0].content if isinstance(c, TextPart))
    assert "sk-secret-token" not in text
    assert "[REDACTED]" in text


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

    def on_blocked(d: base.Driver) -> AlertEvent | None:
        assert isinstance(d, FakeDriver)
        d.screen = [target]  # "dismiss the alert": the app reappears
        return AlertEvent(label="Not Now")

    result = run_scenario(
        driver, load_scenarios(_TAP_GO)[0], alert_guard=AlertGuardConfig(vision=on_blocked)
    )
    assert result.ok is True
    assert ("tap", {"id": "go"}) in driver.actions
    # The dismissal is recorded on the retried step's outcome (for the report).
    assert result.steps[0].alerts == [AlertEvent(label="Not Now")]


def test_failure_stands_without_handler() -> None:
    driver = FakeDriver([])
    result = run_scenario(driver, load_scenarios(_TAP_GO)[0])
    assert result.ok is False


def _el(identifier: str) -> base.Element:
    return {
        "identifier": identifier,
        "label": identifier,
        "traits": ["staticText"],
        "value": None,
        "frame": (0.0, 0.0, 10.0, 10.0),
    }


def test_on_blocked_retries_expect_after_recovery() -> None:
    # A system alert can cover the screen exactly when expect runs; the guard must
    # clear it there too, not only during steps.
    here, later = _el("here"), _el("later")
    driver = FakeDriver([here])  # 'later' is missing until the alert is dismissed

    def on_blocked(d: base.Driver) -> AlertEvent | None:
        assert isinstance(d, FakeDriver)
        d.screen = [here, later]
        return AlertEvent(label="Allow")

    yaml = (
        "- name: e\n"
        "  steps:\n    - wait: { for: { id: here }, timeout: 1 }\n"
        "  expect:\n    - exists: { id: later }\n"
    )
    result = run_scenario(
        driver, load_scenarios(yaml)[0], alert_guard=AlertGuardConfig(vision=on_blocked)
    )
    assert result.ok is True
    # The expect-phase dismissal is recorded on the run result (not on any step).
    assert result.expect_alerts == [AlertEvent(label="Allow")]


# --- record loop + alert guard ---


class _FastClock:
    def now(self) -> float:
        return 0.0

    def sleep(self, seconds: float) -> None:
        pass


class _ScriptAgent:
    def __init__(self, *proposals: Proposal) -> None:
        self._proposals = proposals
        self._i = 0

    def next_action(self, observation: object) -> Proposal:
        proposal = self._proposals[min(self._i, len(self._proposals) - 1)]
        self._i += 1
        return proposal


def test_record_guard_clears_blocking_before_agent_acts() -> None:
    driver = FakeDriver([_window()])  # blocked: a prompt collapsed the tree
    calls = {"n": 0}

    def guard(d: base.Driver) -> bool:
        calls["n"] += 1
        assert isinstance(d, FakeDriver)
        d.screen = [_el("go")]  # dismissing the prompt reveals the app
        return True

    agent = _ScriptAgent(
        Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]),
        Proposal(done=True, expect=[]),
    )
    scenario = record_loop(
        driver, "reach go", agent, clock=_FastClock(), with_screenshot=False, alert_guard=guard
    )
    assert calls["n"] >= 1
    assert scenario.steps and scenario.steps[0].tap is not None
    assert scenario.steps[0].tap.id == "go"


def test_record_guard_not_called_when_app_is_visible() -> None:
    driver = FakeDriver([_el("go")])  # actionable already; nothing blocking
    calls = {"n": 0}

    def guard(d: base.Driver) -> bool:
        calls["n"] += 1
        return False

    agent = _ScriptAgent(
        Proposal(steps=[Step.model_validate({"tap": {"id": "go"}})]),
        Proposal(done=True, expect=[]),
    )
    record_loop(
        driver, "reach go", agent, clock=_FastClock(), with_screenshot=False, alert_guard=guard
    )
    assert calls["n"] == 0

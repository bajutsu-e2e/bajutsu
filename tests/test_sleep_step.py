"""Tests for the `sleep` step: a capped, reasoned fixed pause kept visible in every surface."""

from __future__ import annotations

import pytest
from conftest import el
from pydantic import ValidationError

from bajutsu.analysis.audit import audit_scenario
from bajutsu.codegen.playwright import to_playwright
from bajutsu.codegen.uiautomator import to_uiautomator
from bajutsu.codegen.xcuitest import to_xcuitest
from bajutsu.common.agents.claude import TOOLS
from bajutsu.common.cancellation import RunCancelled
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.actions._registry import _step_label
from bajutsu.common.orchestrator.loop._functions import _do_sleep
from bajutsu.common.report.rows import _action_data, _step_detail
from bajutsu.common.scenario import STEP_ACTIONS, Scenario, Step, load_scenarios
from bajutsu.common.scenario.models.actions import MAX_SLEEP_SECONDS
from bajutsu.record.loop import execute
from bajutsu.triage import heuristic as triage
from bajutsu.triage.heuristic import Fix


class FakeClock:
    def __init__(self) -> None:
        self._t = 0.0
        self.slept: list[float] = []

    def now(self) -> float:
        return self._t

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._t += seconds


def _sleep(seconds: float = 2, reason: str = "the server throttles retries") -> dict[str, object]:
    return {"sleep": {"seconds": seconds, "reason": reason}}


# --- schema ---


def test_sleep_is_a_step_action() -> None:
    step = Step.model_validate(_sleep())
    assert "sleep" in STEP_ACTIONS
    assert step.sleep is not None
    assert step.sleep.seconds == 2
    assert step.sleep.reason == "the server throttles retries"


@pytest.mark.parametrize(
    "payload",
    [
        {"seconds": 0, "reason": "x"},
        {"seconds": -1, "reason": "x"},
        {"seconds": MAX_SLEEP_SECONDS + 1, "reason": "x"},
        {"seconds": 1, "reason": "   "},
        {"seconds": 1},
        {"reason": "x"},
    ],
)
def test_sleep_rejects_an_unbounded_or_unexplained_pause(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"sleep": payload})


@pytest.mark.parametrize("seconds", [True, "2"])
def test_sleep_refuses_a_non_number(seconds: object) -> None:
    with pytest.raises(ValidationError):
        Step.model_validate({"sleep": {"seconds": seconds, "reason": "x"}})


def test_sleep_accepts_the_cap_itself() -> None:
    step = Step.model_validate(_sleep(MAX_SLEEP_SECONDS))
    assert step.sleep is not None and step.sleep.seconds == MAX_SLEEP_SECONDS


def test_the_over_cap_message_points_at_wait() -> None:
    with pytest.raises(ValidationError, match="use `wait`"):
        Step.model_validate(_sleep(MAX_SLEEP_SECONDS + 1))


# --- run ---


def test_run_pauses_for_the_stated_seconds_and_passes() -> None:
    clock = FakeClock()
    scenario = Scenario.model_validate({"name": "pause", "steps": [_sleep(2)]})
    result = run_scenario(FakeDriver([el("x", "X")]), scenario, clock=clock)
    assert result.ok, result.failure
    # The pause is the run's only sleep, delivered in quarter-second slices.
    assert clock.slept == [0.25] * 8


def test_the_pause_is_sliced_and_sums_to_the_requested_seconds() -> None:
    clock = FakeClock()
    _do_sleep(1.0, clock, lambda: False)
    assert len(clock.slept) > 1
    assert sum(clock.slept) == pytest.approx(1.0)


def test_a_cancelled_run_leaves_the_pause_early() -> None:
    clock = FakeClock()
    with pytest.raises(RunCancelled):
        _do_sleep(MAX_SLEEP_SECONDS, clock, lambda: clock.now() >= 0.5)
    assert clock.now() < 1.0


def test_record_replay_honors_the_pause() -> None:
    clock = FakeClock()
    execute(FakeDriver([el("x", "X")]), Step.model_validate(_sleep(3)), clock)
    assert clock.slept == [3]


def test_the_progress_label_carries_the_reason() -> None:
    step = Step.model_validate(_sleep(2.5, "animation outlives settle"))
    assert _step_label(step, "sleep") == "sleep 2.5s — animation outlives settle"


# --- report ---


def test_the_report_row_shows_seconds_and_reason() -> None:
    detail = _step_detail({"sleep": {"seconds": 2, "reason": "throttle"}})
    assert detail["parts"] == [("num", "2s"), ("", " — "), ("str", "throttle")]
    assert _action_data({"sleep": {"seconds": 2, "reason": "throttle"}}, None) == {
        "label": "sleep",
        "cls": "act-wait",
    }


# --- audit ---


def _audit(text: str) -> tuple[str, list[str]]:
    report = audit_scenario(load_scenarios(text)[0])
    return report.grade, [f.kind for f in report.findings]


def test_each_sleep_is_a_fixed_sleep_finding() -> None:
    grade, kinds = _audit(
        "- name: x\n  steps:\n"
        "    - tap: { id: home.start }\n"
        "    - sleep: { seconds: 2, reason: throttle }\n"
        "    - sleep: { seconds: 1, reason: throttle }\n"
    )
    assert kinds.count("fixed-sleep") == 2
    assert grade == "Stable"  # three seconds of pause stays under the moderate threshold


def test_fixed_sleep_past_ten_seconds_grades_moderate() -> None:
    grade, _ = _audit(
        "- name: x\n  steps:\n"
        "    - tap: { id: home.start }\n"
        "    - sleep: { seconds: 6, reason: throttle }\n"
        "    - group: { name: g, steps: [{ sleep: { seconds: 6, reason: throttle } }] }\n"
    )
    assert grade == "Moderate"


def test_every_written_sleep_is_found_wherever_it_sits() -> None:
    grade, kinds = _audit(
        "- name: x\n"
        "  before: [{ sleep: { seconds: 4, reason: seed } }]\n"
        "  interrupts:\n"
        "    - condition: { exists: { id: promo } }\n"
        "      steps: [{ sleep: { seconds: 1, reason: fade } }]\n"
        "  steps:\n"
        "    - tap: { id: home.start }\n"
        "    - if:\n"
        "        condition: { exists: { id: home.start } }\n"
        "        then: [{ sleep: { seconds: 2, reason: a } }]\n"
        "        else: [{ sleep: { seconds: 2, reason: b } }]\n"
        "    - forEach:\n"
        "        sel: { idMatches: 'row.*' }\n        as: row\n"
        "        steps: [{ sleep: { seconds: 1, reason: row } }]\n"
        "  after:\n"
        "    - on: always\n"
        "      steps: [{ sleep: { seconds: 1, reason: teardown } }]\n"
    )
    assert kinds.count("fixed-sleep") == 6
    assert grade == "Moderate"  # eleven written seconds, counted once each


def test_a_triage_fix_adding_a_sleep_is_flagged_as_laxer() -> None:
    yaml = "- name: s\n  steps:\n    - tap: { id: submit }\n"
    fix = Fix(
        "addStep",
        "pause",
        "    - tap: { id: submit }\n",
        "    - sleep: { seconds: 2, reason: flaky }\n    - tap: { id: submit }\n",
    )
    assert any("sleep" in w for w in triage.flag_laxer(yaml, fix))


# --- AI authoring ---


def test_the_recording_agent_is_never_offered_sleep() -> None:
    assert not any("sleep" in tool.name for tool in TOOLS)


# --- codegen ---

_CODEGEN = """
- name: pause
  steps:
    - sleep: { seconds: 1.5, reason: "server\\nthrottle" }
"""


def test_xcuitest_emits_a_fixed_pause_with_its_reason() -> None:
    out = to_xcuitest(load_scenarios(_CODEGEN), "DemoUITests")
    assert "// fixed pause: server throttle" in out
    assert "Thread.sleep(forTimeInterval: 1.5)" in out


def test_uiautomator_emits_a_fixed_pause_with_its_reason() -> None:
    out = to_uiautomator(load_scenarios(_CODEGEN), "DemoUITest", "com.example")
    assert "// fixed pause: server throttle" in out
    assert "Thread.sleep(1500)" in out


def test_playwright_emits_a_fixed_pause_with_its_reason() -> None:
    out = to_playwright(load_scenarios(_CODEGEN), "demo", "http://localhost")
    assert "// fixed pause: server throttle" in out
    assert "await page.waitForTimeout(1500);" in out

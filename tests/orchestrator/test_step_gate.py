"""Tests for `bajutsu run --step`: the prompt that holds the step loop at a boundary.

The gate decides only *where the run waits* and whether an operator touched the app; the verdict
stays the scenario's own assertions, so a hand-driven run is failed rather than judged.
"""

from __future__ import annotations

import inspect
import re
from collections.abc import Iterable
from dataclasses import replace

import pytest
import typer
from _orch import FakeClock, _scenario
from conftest import el

from bajutsu.common.cancellation import CANCELLED_FAILURE, RunCancelled
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.drivers.xcuitest import XcuitestRunnerCrashError
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.types import RunResult, StepPause
from bajutsu.common.report.manifest import scenario_result_dict
from bajutsu.common.runner import run_all
from bajutsu.repl.session import ACTUATING_VERBS, ReplSession
from bajutsu.run.cli import _build_step_gate
from bajutsu.run.step_gate import PromptStepGate

_TWO_TAPS: dict[str, object] = {
    "name": "x",
    "steps": [{"name": "first", "tap": {"id": "a"}}, {"name": "second", "tap": {"id": "b"}}],
}


_CTRL_C = "<ctrl-c>"


class _Terminal:
    """Scripted operator input, and everything the gate printed."""

    def __init__(self, lines: Iterable[str]) -> None:
        self._lines = iter(lines)
        self.prompts = 0
        self.said: list[str] = []

    def read_line(self, _prompt: str) -> str:
        self.prompts += 1
        try:
            line = next(self._lines)
        except StopIteration:
            raise EOFError from None
        if line == _CTRL_C:
            raise KeyboardInterrupt
        return line

    def say(self, line: str) -> None:
        self.said.append(line)


def _gate(
    term: _Terminal,
    *,
    pause_first: bool = False,
    breaks: tuple[str, ...] = (),
    break_on_fail: bool = False,
) -> PromptStepGate:
    return PromptStepGate(
        pause_first=pause_first,
        breaks=breaks,
        break_on_fail=break_on_fail,
        read_line=term.read_line,
        say=term.say,
    )


def _run(gate: PromptStepGate, scenario: dict[str, object] = _TWO_TAPS) -> RunResult:
    driver = FakeDriver([el("a", "A", ["button"]), el("b", "B", ["button"])])
    return run_scenario(driver, _scenario(scenario), clock=FakeClock(), step_gate=gate)


def test_step_stops_before_every_step_and_the_run_still_passes() -> None:
    term = _Terminal(["", "next"])
    result = _run(_gate(term, pause_first=True))
    assert term.prompts == 2
    assert [m for m in term.said if m.startswith("⏸")] == [
        "⏸ before step 0 [first]: tap a",
        "⏸ before step 1 [second]: tap b",
    ]
    assert result.ok
    assert result.interactive == ""


def test_continue_runs_free_until_the_end() -> None:
    term = _Terminal(["continue"])
    result = _run(_gate(term, pause_first=True))
    assert term.prompts == 1
    assert result.ok


def test_break_by_name_skips_earlier_steps_without_a_prompt() -> None:
    term = _Terminal(["c"])
    result = _run(_gate(term, breaks=("second",)))
    assert term.prompts == 1
    assert term.said[0].startswith("⏸ before step 1 [second]")
    assert result.ok


def test_break_by_index_stops_at_that_step() -> None:
    term = _Terminal(["c"])
    _run(_gate(term, breaks=("1",)))
    assert term.said[0].startswith("⏸ before step 1")


def test_next_after_a_breakpoint_keeps_stepping() -> None:
    term = _Terminal(["next", "c"])
    _run(_gate(term, breaks=("0",)))
    assert term.prompts == 2  # stopped at step 0, `next` stopped again before step 1


def test_unreached_breaks_names_a_typo() -> None:
    term = _Terminal(["c"])
    gate = _gate(term, breaks=("second", "nope", "99"))
    _run(gate)
    assert gate.unreached_breaks() == ["99", "nope"]


@pytest.mark.parametrize("verb", ["quit", "q", "exit"])
def test_quit_ends_the_run_as_cancelled_before_the_step_runs(verb: str) -> None:
    term = _Terminal([verb])
    result = _run(_gate(term, pause_first=True))
    assert not result.ok
    assert result.failure == CANCELLED_FAILURE
    assert result.steps == []
    assert "run ended at the prompt" in term.said


def test_end_of_input_ends_the_run_like_quit() -> None:
    result = _run(_gate(_Terminal([]), pause_first=True))
    assert result.failure == CANCELLED_FAILURE


def test_reading_the_app_does_not_taint_the_verdict() -> None:
    term = _Terminal(["tree", "find A", "c"])
    gate = _gate(term, pause_first=True)
    result = _run(gate)
    assert result.ok
    assert gate.manual_action is None
    assert any("A" in line for line in term.said)


def test_acting_on_the_app_fails_a_run_whose_assertions_all_passed() -> None:
    term = _Terminal(["tap b", "c"])
    gate = _gate(term, pause_first=True)
    result = _run(gate)
    assert not result.ok
    assert result.interactive == "tap"
    assert result.failure == "interactive: tap"
    # Every step itself ran and passed; the failure is the stamp, not an assertion.
    assert [o.ok for o in result.steps] == [True, True]


def test_the_first_manual_command_is_the_one_recorded() -> None:
    term = _Terminal(["back", "tap b", "c"])
    result = _run(_gate(term, pause_first=True))
    assert result.interactive == "back"


def test_a_real_failure_keeps_its_own_reason_over_the_interactive_stamp() -> None:
    term = _Terminal(["tap b", "c"])
    scenario: dict[str, object] = {"name": "x", "steps": [{"tap": {"id": "missing"}}]}
    result = _run(_gate(term, pause_first=True), scenario)
    assert not result.ok
    assert result.interactive == "tap"
    assert result.failure is not None
    assert not result.failure.startswith("interactive:")


def test_a_command_the_shell_rejects_is_reported_and_the_prompt_stays_open() -> None:
    term = _Terminal(["tap nope", "c"])
    gate = _gate(term, pause_first=True)
    result = _run(gate)
    assert any(line.startswith("error:") for line in term.said)
    assert term.prompts == 2
    # The attempt is recorded even though it raised: a tap that errored may still have moved the app.
    assert result.interactive == "tap"


def test_break_on_fail_stops_once_on_the_failed_step() -> None:
    term = _Terminal(["c"])
    scenario: dict[str, object] = {"name": "x", "steps": [{"tap": {"id": "missing"}}]}
    result = _run(_gate(term, break_on_fail=True), scenario)
    assert term.prompts == 1
    assert any(line.startswith("✘ step 0 failed") for line in term.said)
    assert not result.ok


def test_a_failure_inside_an_if_stops_once_not_once_per_level() -> None:
    term = _Terminal(["c", "c", "c"])
    scenario: dict[str, object] = {
        "name": "x",
        "steps": [
            {
                "if": {
                    "condition": {"exists": {"id": "a"}},
                    "then": [{"tap": {"id": "missing"}}],
                }
            }
        ],
    }
    _run(_gate(term, break_on_fail=True), scenario)
    assert sum(line.startswith("✘") for line in term.said) == 1


@pytest.mark.parametrize("lines", [["quit"], []], ids=["quit", "end-of-input"])
def test_ending_the_run_at_a_failure_keeps_the_real_failure(lines: list[str]) -> None:
    scenario: dict[str, object] = {"name": "x", "steps": [{"tap": {"id": "missing"}}]}
    result = _run(_gate(_Terminal(lines), break_on_fail=True), scenario)
    assert result.failure is not None
    assert result.failure.startswith("step 0 (tap)")


def test_ctrl_c_at_the_prompt_ends_the_run() -> None:
    result = _run(_gate(_Terminal([_CTRL_C]), pause_first=True))
    assert result.failure == CANCELLED_FAILURE


def test_interrupt_recovery_steps_never_prompt() -> None:
    # Recovery runs from inside a step already under way, so a pause there would spend that step's
    # own timeout; only the scenario's one authored step may stop the loop.
    driver = FakeDriver(
        [el("ov", "overlay"), el("scn.btn", "Scn", ["button"]), el("go", "Go", ["button"])]
    )
    scenario = _scenario(
        {
            "name": "x",
            "interrupts": [
                {"condition": {"exists": {"id": "ov"}}, "steps": [{"tap": {"id": "scn.btn"}}]}
            ],
            "steps": [{"tap": {"id": "go"}}],
        }
    )
    term = _Terminal(["n", "n", "n", "n"])
    run_scenario(
        driver,
        scenario,
        clock=FakeClock(),
        interrupts=list(scenario.interrupts),
        step_gate=_gate(term, pause_first=True),
    )
    assert term.prompts == 1


def test_before_step_reports_whether_it_held_the_loop() -> None:
    term = _Terminal(["c"])
    gate = _gate(term, breaks=("1",))
    pause = StepPause(index=0, name=None, label="tap a", driver=FakeDriver([]))
    assert gate.before_step(pause) is False
    assert gate.before_step(replace(pause, index=1)) is True


def test_a_value_typed_at_the_prompt_never_reaches_the_result() -> None:
    # `type` can carry a credential typed in by hand that is no configured secret; the result is
    # persisted and uploaded, so it records the verb alone.
    result = _run(_gate(_Terminal(["type a hunter2", "c"]), pause_first=True))
    assert result.interactive == "type"
    assert "hunter2" not in (result.failure or "")


class _CrashingDriver(FakeDriver):
    def tap(self, target: object) -> None:
        raise XcuitestRunnerCrashError("runner gone")


def test_a_dead_backend_at_the_prompt_propagates_instead_of_prompting_again() -> None:
    gate = _gate(_Terminal(["tap a", "c"]), pause_first=True)
    driver = _CrashingDriver([el("a", "A", ["button"])])
    with pytest.raises(XcuitestRunnerCrashError):
        gate.before_step(StepPause(index=0, name=None, label="tap a", driver=driver))


class _InterruptingDriver(FakeDriver):
    def tap(self, target: object) -> None:
        raise KeyboardInterrupt


def test_ctrl_c_during_a_command_ends_the_run() -> None:
    gate = _gate(_Terminal(["tap a"]), pause_first=True)
    driver = _InterruptingDriver([el("a", "A", ["button"])])
    with pytest.raises(RunCancelled):
        gate.before_step(StepPause(index=0, name=None, label="tap a", driver=driver))


def test_ctrl_c_during_a_command_at_a_failure_keeps_the_failure() -> None:
    gate = _gate(_Terminal(["tap a"]), break_on_fail=True)
    driver = _InterruptingDriver([el("a", "A", ["button"])])
    pause = StepPause(index=0, name=None, label="tap a", driver=driver, failure="step 0 (tap): x")
    gate.after_failure(pause)  # returns rather than raising RunCancelled


def test_every_repl_verb_is_classified_as_acting_or_reading() -> None:
    # A new `repl` verb lands in `dispatch` and fails this until it is classified: an acting verb left
    # out of ACTUATING_VERBS would let a hand-driven run pass.
    source = inspect.getsource(ReplSession.dispatch)
    case_lines = [ln for ln in source.splitlines() if ln.strip().startswith("case ")]
    verbs = {v for ln in case_lines for v in re.findall(r'"([a-z]+)"', ln)}
    reading = {"tree", "find", "screenshot", "help", "exit", "quit"}
    assert verbs == ACTUATING_VERBS | reading
    assert not (ACTUATING_VERBS & reading)


def test_run_all_refuses_a_shared_gate_across_scenarios() -> None:
    with pytest.raises(ValueError, match="exactly one scenario"):
        run_all(
            None,  # type: ignore[arg-type]
            [_scenario(_TWO_TAPS), _scenario(_TWO_TAPS)],
            lambda *_a, **_k: None,  # type: ignore[arg-type]
            step_gate=_gate(_Terminal([])),
        )


def test_no_gate_changes_nothing() -> None:
    driver = FakeDriver([el("a", "A", ["button"]), el("b", "B", ["button"])])
    result = run_scenario(driver, _scenario(_TWO_TAPS), clock=FakeClock())
    assert result.ok
    assert result.interactive == ""


def test_result_json_carries_the_interactive_command() -> None:
    plain = _run(_gate(_Terminal(["c"]), pause_first=True))
    driven = _run(_gate(_Terminal(["tap a", "c"]), pause_first=True))
    assert scenario_result_dict(plain)["scenario"]["interactive"] == ""  # type: ignore[index]
    assert scenario_result_dict(driven)["scenario"]["interactive"] == "tap"  # type: ignore[index]


def test_the_flags_build_no_gate_when_none_is_given() -> None:
    assert (
        _build_step_gate(
            step=False, break_at=[], break_on_fail=False, scenarios=[], engines=[""], workers=1
        )
        is None
    )


def test_the_flags_are_refused_without_a_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    # The CI / piped case the prompt must never hang on, pinned rather than left to pytest's own stdin.
    monkeypatch.setattr("sys.stdin.isatty", lambda: False, raising=False)
    with pytest.raises(typer.Exit) as exc:
        _build_step_gate(
            step=True,
            break_at=[],
            break_on_fail=False,
            scenarios=[_scenario(_TWO_TAPS)],
            engines=[""],
            workers=1,
        )
    assert exc.value.exit_code == 2


def test_the_flags_are_refused_for_a_suite_or_a_pool(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.stdin.isatty", lambda: True, raising=False)
    monkeypatch.setattr("sys.stderr.isatty", lambda: True, raising=False)
    two = [_scenario(_TWO_TAPS), _scenario(_TWO_TAPS)]
    for scenarios, engines, workers in [
        (two, [""], 1),
        ([two[0]], ["chromium", "webkit"], 1),
        ([two[0]], [""], 2),
    ]:
        with pytest.raises(typer.Exit):
            _build_step_gate(
                step=True,
                break_at=[],
                break_on_fail=False,
                scenarios=scenarios,
                engines=engines,
                workers=workers,
            )

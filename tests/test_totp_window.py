"""Tests for the `totp` step's `minRemaining` window wait (BE-0474), on fake clocks — no real waiting."""

from __future__ import annotations

import base64
from functools import partial

import pytest
from conftest import el

from bajutsu.common.cancellation import RunCancelled
from bajutsu.common.drivers.fake import FakeDriver
from bajutsu.common.orchestrator import run_scenario
from bajutsu.common.orchestrator.actions._registry import _step_label
from bajutsu.common.orchestrator.loop import _functions
from bajutsu.common.orchestrator.loop._functions import _SLEEP_SLICE, _wait_for_totp_window
from bajutsu.common.orchestrator.types import RunResult
from bajutsu.common.report.rows import _step_run_row
from bajutsu.common.scenario import Scenario, Step

# A window boundary, so each test states its start as an offset into the window.
_BOUNDARY = 1_800_000_000.0


class Clocks:
    """A monotonic fake clock with a wall clock tied to it, which a test can make jump or wake late."""

    def __init__(self, phase: float, *, late: float = 0.0) -> None:
        self.mono = 0.0
        self.wall_t = _BOUNDARY + phase
        self.late = late
        self.slept: list[float] = []
        # (after this many sleeps, shift the wall clock by this much) — a one-off NTP correction.
        self.jump: tuple[int, float] | None = None

    def now(self) -> float:
        return self.mono

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        # A late wake-up overshoots on both clocks: the host was busy, the wall clock didn't move.
        self.mono += seconds + self.late
        self.wall_t += seconds + self.late
        if self.jump is not None and len(self.slept) == self.jump[0]:
            self.wall_t += self.jump[1]

    def wall(self) -> float:
        return self.wall_t


def _wait(c: Clocks, min_remaining: int, cancelled: bool = False) -> str | None:
    return _wait_for_totp_window(min_remaining, c, lambda: cancelled, wall=c.wall)


def test_enough_time_left_does_not_wait() -> None:
    c = Clocks(phase=5)  # 25s left
    assert _wait(c, 10) is None
    assert c.slept == []


def test_too_little_time_left_waits_for_the_boundary() -> None:
    c = Clocks(phase=25)  # 5s left
    assert _wait(c, 10) is None
    assert c.wall_t == pytest.approx(_BOUNDARY + 30)
    assert max(c.slept) <= _SLEEP_SLICE


def test_min_remaining_30_ends_on_the_boundary() -> None:
    # Just past a boundary the remaining time is 30 - δ, which never reaches 30: the wait must end on
    # the boundary itself or it would never terminate.
    c = Clocks(phase=0.5)
    assert _wait(c, 30) is None
    assert c.wall_t == pytest.approx(_BOUNDARY + 30)


def test_exactly_on_a_boundary_does_not_wait_even_for_30() -> None:
    c = Clocks(phase=0)
    assert _wait(c, 30) is None
    assert c.slept == []


def test_a_late_wake_up_records_no_warning() -> None:
    c = Clocks(phase=29.9, late=2.0)
    assert _wait(c, 30) is None
    assert c.wall_t >= _BOUNDARY + 30


def test_a_backward_jump_ends_within_the_bound_and_warns() -> None:
    c = Clocks(phase=25)
    c.jump = (2, -12.0)
    warning = _wait(c, 10)
    # The monotonic deadline still ends the wait: the original 5s to the boundary plus one slice.
    assert c.mono <= 5 + _SLEEP_SLICE + 1e-9
    assert warning is not None
    assert warning.startswith("totp: wall clock jumped -12s during window wait;")
    assert warning.endswith("s left")


def test_a_forward_jump_ends_and_warns() -> None:
    c = Clocks(phase=25)
    c.jump = (1, 62.0)  # lands at 25.25 + 62 = 87.25, in a later window's tail
    warning = _wait(c, 10)
    assert c.mono <= 5 + _SLEEP_SLICE + 1e-9
    assert warning == "totp: wall clock jumped +62s during window wait; 3s left"


def test_a_cancelled_run_leaves_the_wait() -> None:
    c = Clocks(phase=25)
    with pytest.raises(RunCancelled):
        _wait(c, 10, cancelled=True)
    assert c.slept == []


# --- the run loop ---

# The RFC 6238 test seed, built rather than written so no key-shaped literal sits in the file.
_SECRET = base64.b32encode(b"1234567890" * 2).decode()


def _totp_step(min_remaining: int | None = 10, name: str | None = None) -> dict[str, object]:
    totp: dict[str, object] = {"secret": _SECRET, "into": {"var": "code"}}
    if min_remaining is not None:
        totp["minRemaining"] = min_remaining
    return {"totp": totp, **({"name": name} if name else {})}


def _run(c: Clocks, monkeypatch: pytest.MonkeyPatch, step: dict[str, object]) -> RunResult:
    # The loop calls the wait with the real wall clock; tie it to the fake one instead.
    monkeypatch.setattr(
        _functions, "_wait_for_totp_window", partial(_wait_for_totp_window, wall=c.wall)
    )
    scenario = Scenario.model_validate({"name": "otp", "steps": [step]})
    return run_scenario(FakeDriver([el("x", "X")]), scenario, clock=c)


def test_the_run_waits_before_generating_the_code(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Clocks(phase=25)
    result = _run(c, monkeypatch, _totp_step(10))
    assert result.ok, result.failure
    assert c.wall_t == pytest.approx(_BOUNDARY + 30)
    assert result.steps[0].warnings == ()


def test_without_min_remaining_the_run_never_waits(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Clocks(phase=29)
    result = _run(c, monkeypatch, _totp_step(None))
    assert result.ok, result.failure
    assert c.wall_t == pytest.approx(_BOUNDARY + 29)


def test_a_jump_warning_rides_a_passing_step(monkeypatch: pytest.MonkeyPatch) -> None:
    c = Clocks(phase=25)
    c.jump = (1, 62.0)
    result = _run(c, monkeypatch, _totp_step(10))
    [step] = result.steps
    # Evidence only: the verdict and its failure text never see the warning.
    assert step.ok and step.reason == ""
    assert step.warnings == ("totp: wall clock jumped +62s during window wait; 3s left",)
    assert _step_run_row(0, None, step, None, 0.0)["warnings"] == list(step.warnings)


# --- the live label ---


def test_the_label_names_the_requirement() -> None:
    assert _step_label(Step.model_validate(_totp_step(10)), "totp") == "totp (≥10s left)"


def test_a_named_step_keeps_the_requirement_in_its_label() -> None:
    step = Step.model_validate(_totp_step(10, name="Sign in code"))
    assert _step_label(step, "totp") == "Sign in code (totp ≥10s left)"


def test_without_min_remaining_the_label_is_unchanged() -> None:
    step = Step.model_validate(_totp_step(None, name="Sign in code"))
    assert _step_label(step, "totp") == "Sign in code"

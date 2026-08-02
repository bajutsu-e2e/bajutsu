"""The shared backend-crash recovery bookkeeping (BE-0334 Unit 1).

`recovery.py` holds the fault classification and the count + wall-clock retry decision that the
run pipeline and the on-device conformance harness both drive, so the two recovery paths cannot
drift. These unit tests pin that logic directly (no Simulator, no pipeline) — the pipeline's own
crash-recovery tests then re-exercise it through `run_all`.
"""

from __future__ import annotations

from bajutsu.drivers import base, xcuitest
from bajutsu.runner.recovery import (
    CrashRecoveryBudget,
    _default_crash_recovery_budget,
    _default_crash_retries,
    is_infrastructure_fault,
)


class _AdvancingClock:
    """A clock whose `now()` moves only when the test advances it — a respawn's wall-clock cost,
    injected deterministically so the budget can be exercised without a real delay."""

    def __init__(self) -> None:
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_backend_crash_is_an_infrastructure_fault() -> None:
    # The base crash type and its backend-specific subclass are infrastructure: the harness re-leases
    # and retries rather than failing the contract.
    assert is_infrastructure_fault(base.BackendCrashError("runner died"))
    assert is_infrastructure_fault(
        xcuitest.XcuitestRunnerCrashError("channel timed out", method="tap")
    )


def test_contract_violations_are_not_infrastructure_faults() -> None:
    # A selector that resolved wrongly, an actuator that refused, a failed assertion, a bare error:
    # none is infrastructure, so each keeps failing immediately (BE-0334's asymmetry).
    for exc in (
        base.AmbiguousSelector("two matches"),
        base.ElementNotFound("no match"),
        base.SelectorError("selector failed"),
        base.UnsupportedAction("backend cannot"),
        AssertionError("contract violated"),
        RuntimeError("something else"),
    ):
        assert not is_infrastructure_fault(exc)


def test_budget_rides_out_every_attempt_within_the_count() -> None:
    # retries=2 → three attempts total; the two respawns after a crash are allowed, the third crash
    # exhausts the count and stops (with no wall-clock budget in play).
    budget = CrashRecoveryBudget(retries=2, budget=None, now=_AdvancingClock().now)
    assert budget.total_attempts == 3
    first = budget.on_crash(1)
    assert first.will_retry and not first.budget_spent
    second = budget.on_crash(2)
    assert second.will_retry and not second.budget_spent
    third = budget.on_crash(3)
    assert not third.will_retry and not third.budget_spent  # count exhausted, not the wall-clock


def test_budget_stops_respawning_once_the_wall_clock_is_spent() -> None:
    # The count alone would allow five respawns; a 300s wall-clock budget with each respawn "taking"
    # 200s stops recovery once the budget is spent — well before the count — and marks it budget_spent.
    clock = _AdvancingClock()
    budget = CrashRecoveryBudget(retries=5, budget=300.0, now=clock.now)
    clock.advance(200.0)  # the first respawn's readiness wait burns 200s
    first = budget.on_crash(1)  # deadline set here: 200 + 300 = 500; now=200 < 500 → retry
    assert first.will_retry and not first.budget_spent
    clock.advance(200.0)  # now=400 < 500
    second = budget.on_crash(2)
    assert second.will_retry and not second.budget_spent
    clock.advance(200.0)  # now=600 ≥ 500 → budget spent, count still allows it
    third = budget.on_crash(3)
    assert not third.will_retry and third.budget_spent


def test_budget_never_blocks_the_first_respawn() -> None:
    # The deadline is set at the first crash, so `now < deadline` always holds there: a genuine one-off
    # is ridden out even under a tight budget. A respawn that comes back at once burns no wall-clock.
    clock = _AdvancingClock()
    budget = CrashRecoveryBudget(retries=2, budget=0.001, now=clock.now)
    first = budget.on_crash(1)
    assert first.will_retry and not first.budget_spent


def test_crash_retries_default_reads_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("BAJUTSU_CRASH_RETRIES", raising=False)
    assert _default_crash_retries() == 1  # unset → the pre-knob default
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "2")
    assert _default_crash_retries() == 2
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "0")
    assert _default_crash_retries() == 0  # explicit opt-out of recovery
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "nope")
    assert _default_crash_retries() == 1  # invalid → the default, never a crash


def test_crash_recovery_budget_default_reads_the_environment(monkeypatch) -> None:
    monkeypatch.delenv("BAJUTSU_CRASH_RECOVERY_BUDGET", raising=False)
    assert _default_crash_recovery_budget() is None  # unset → unbounded
    monkeypatch.setenv("BAJUTSU_CRASH_RECOVERY_BUDGET", "300")
    assert _default_crash_recovery_budget() == 300.0
    monkeypatch.setenv("BAJUTSU_CRASH_RECOVERY_BUDGET", "0")
    assert _default_crash_recovery_budget() is None  # non-positive → unbounded, not "no recovery"
    monkeypatch.setenv("BAJUTSU_CRASH_RECOVERY_BUDGET", "nope")
    assert _default_crash_recovery_budget() is None  # invalid → unbounded

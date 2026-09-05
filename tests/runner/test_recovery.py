"""The shared backend-crash recovery bookkeeping (BE-0334 Unit 1).

`recovery.py` holds the fault classification and the count + wall-clock retry decision that the
run pipeline and the on-device conformance harness both drive, so the two recovery paths cannot
drift. These unit tests pin that logic directly (no Simulator, no pipeline) — the pipeline's own
crash-recovery tests then re-exercise it through `run_all`.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from bajutsu.common.backend_cli import simctl
from bajutsu.common.devices import errors as device_errors
from bajutsu.common.drivers import base, xcuitest
from bajutsu.common.runner.recovery import (
    CrashRecoveryBudget,
    RunCrashRecoveryBudget,
    _default_crash_recovery_budget,
    _default_crash_retries,
    _default_run_crash_recovery_budget,
    guarded_teardown,
    is_host_fault,
    recovers_by_respawn,
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


# The exception types that are neither a host fault nor a respawn's to repair: a selector that
# resolved wrongly, an actuator that refused, a failed assertion, a bare error. Each keeps failing
# immediately (BE-0334's asymmetry).
_CONTRACT_VIOLATIONS = (
    base.AmbiguousSelector("two matches"),
    base.ElementNotFound("no match"),
    base.SelectorError("selector failed"),
    base.UnsupportedAction("backend cannot"),
    AssertionError("contract violated"),
    RuntimeError("something else"),
)


def test_a_backend_crash_recovers_by_respawn() -> None:
    # The base crash type and its backend-specific subclass are what a fresh lease repairs: the
    # harness re-leases and retries rather than failing the contract.
    assert recovers_by_respawn(base.BackendCrashError("runner died"))
    assert recovers_by_respawn(xcuitest.XcuitestRunnerCrashError("channel timed out", method="tap"))


def test_contract_violations_recover_by_nothing() -> None:
    for exc in _CONTRACT_VIOLATIONS:
        assert not recovers_by_respawn(exc)


def test_a_wedged_device_does_not_recover_by_respawn() -> None:
    # BE-0378's whole point: a timeout is the host's fault, but a respawn rebuilds the device out of
    # the same `simctl` calls that just stalled, so the retry predicate must keep excluding it.
    assert not recovers_by_respawn(simctl.DeviceTimeout("get_app_container timed out after 60s"))


def test_a_wedged_device_and_a_crash_are_both_host_faults() -> None:
    # The diagnosis is the wider set: everything a respawn recovers, plus the wedge it cannot.
    assert is_host_fault(simctl.DeviceTimeout("get_app_container timed out after 60s"))
    assert is_host_fault(base.BackendCrashError("runner died"))
    assert is_host_fault(xcuitest.XcuitestRunnerCrashError("channel timed out", method="tap"))


def test_a_wedge_is_diagnosed_by_its_neutral_type_not_the_ios_one() -> None:
    # BE-0374: the predicate names `device_errors.DeviceTimeout`, so the backend that adopts that base
    # next is covered here with no edit. A bare neutral timeout is not a simctl error at all, so this
    # fails the moment the predicate goes back to naming the iOS type.
    assert is_host_fault(device_errors.DeviceTimeout("device operation timed out after 60s"))


def test_contract_violations_are_not_host_faults() -> None:
    # A device fault that is *not* a timeout stays out too: a `simctl.DeviceError` says the app is not
    # installed or the udid is wrong, which is this run's own wiring rather than the host wedging.
    for exc in (*_CONTRACT_VIOLATIONS, simctl.DeviceError("app is not installed")):
        assert not is_host_fault(exc)


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


def test_crash_retries_default_reads_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("BAJUTSU_CRASH_RETRIES", raising=False)
    assert _default_crash_retries() == 1  # unset → the pre-knob default
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "2")
    assert _default_crash_retries() == 2
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "0")
    assert _default_crash_retries() == 0  # explicit opt-out of recovery
    monkeypatch.setenv("BAJUTSU_CRASH_RETRIES", "nope")
    assert _default_crash_retries() == 1  # invalid → the default, never a crash


def test_crash_recovery_budget_default_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BAJUTSU_CRASH_RECOVERY_BUDGET", raising=False)
    assert _default_crash_recovery_budget() is None  # unset → unbounded
    monkeypatch.setenv("BAJUTSU_CRASH_RECOVERY_BUDGET", "300")
    assert _default_crash_recovery_budget() == 300.0
    monkeypatch.setenv("BAJUTSU_CRASH_RECOVERY_BUDGET", "0")
    assert _default_crash_recovery_budget() is None  # non-positive → unbounded, not "no recovery"
    monkeypatch.setenv("BAJUTSU_CRASH_RECOVERY_BUDGET", "nope")
    assert _default_crash_recovery_budget() is None  # invalid → unbounded


def test_run_budget_never_exhausted_when_unbounded() -> None:
    # None (the default) is unbounded: `exhausted()` stays False no matter how much recovery time is
    # billed against it.
    budget = RunCrashRecoveryBudget(budget=None)
    assert budget.exhausted() is False
    budget.add_recovery_time(10_000.0)
    assert budget.exhausted() is False


def test_run_budget_normalizes_a_non_positive_budget_passed_directly_to_unbounded() -> None:
    # `_default_run_crash_recovery_budget` maps `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET=0` to None
    # (unbounded); a caller passing 0 (or a negative value) straight to the constructor must read the
    # same way, or `exhausted()` would return True before any recovery time is billed — inverting the
    # never-block-the-first-crash rule `exhausted()` documents.
    for non_positive in (0.0, -5.0):
        budget = RunCrashRecoveryBudget(budget=non_positive)
        assert budget.budget is None
        assert budget.exhausted() is False


def test_run_budget_exhausts_once_accumulated_recovery_time_meets_it() -> None:
    # The budget bills *actual recovery time spent*, not wall-clock elapsed since some earlier crash:
    # a 100s budget exhausts once 100s has actually been billed via add_recovery_time, regardless of
    # how that total was split across separate calls (separate scenarios' recovery episodes).
    budget = RunCrashRecoveryBudget(budget=100.0)
    budget.add_recovery_time(40.0)
    assert budget.exhausted() is False
    budget.add_recovery_time(59.0)  # 99.0 total, still under
    assert budget.exhausted() is False
    budget.add_recovery_time(1.0)  # 100.0 total, meets the budget
    assert budget.exhausted() is True


def test_run_budget_ignores_healthy_time_between_unrelated_crashes() -> None:
    # A long, perfectly healthy stretch between two unrelated one-off crashes must not itself erode
    # the budget — the object has no clock of its own, only `add_recovery_time` moves the total, so a
    # stretch with nothing billed changes nothing (guards the exact bug an earlier, deadline-based
    # design had: a single armed-at-first-crash deadline treated wall-clock elapsed as if it were all
    # recovery time).
    budget = RunCrashRecoveryBudget(budget=100.0)
    budget.add_recovery_time(50.0)  # scenario 1's crash cost 50s of real recovery time
    assert budget.exhausted() is False  # still only 50s billed, nowhere near the 100s budget
    budget.add_recovery_time(50.0)  # scenario 8's crash costs another 50s
    assert budget.exhausted() is True  # 100s billed in total, now it is exhausted


def test_run_budget_never_blocks_the_first_crash() -> None:
    # The run's very first crash always sees an empty accumulator (0.0 billed so far), so `exhausted()`
    # reads False right up to the point a caller has actually billed the whole budget — the same
    # never-block-the-first-respawn property `CrashRecoveryBudget` gives per scenario, here true by
    # construction (a positive budget can never be `<= 0.0`) rather than a special case.
    budget = RunCrashRecoveryBudget(budget=0.001)
    assert budget.exhausted() is False


def test_run_budget_given_up_only_after_mark_given_up_not_on_exhaustion_alone() -> None:
    # `exhausted()` trips on cumulative time alone — including time billed by a recovery that
    # ultimately succeeded — but the latch must stay unset until a caller explicitly reports (via
    # `mark_given_up`) that a scenario's own crash-retry loop actually failed because this budget was
    # the binding constraint. A device that recovered slowly, but did recover, must not latch out every
    # later scenario.
    budget = RunCrashRecoveryBudget(budget=100.0)
    budget.add_recovery_time(100.0)  # exhausts the budget via a recovery that succeeded
    assert budget.exhausted() is True
    assert budget.given_up_cause() is None  # nothing has failed yet — no false-positive latch
    budget.mark_given_up("the run-level crash-recovery budget of 100s is exhausted")
    assert budget.given_up_cause() == "the run-level crash-recovery budget of 100s is exhausted"


def test_the_latch_carries_a_cause_that_is_not_the_budget() -> None:
    # BE-0374 gives the latch a second cause: a device preparation that timed out. It must be
    # recordable on a budget that was never configured at all — the default — since a wedged host is
    # not a budget concern and reporting one would format a `None` into the failure message.
    budget = RunCrashRecoveryBudget(budget=None)
    budget.mark_given_up("an earlier scenario's device preparation timed out")
    assert budget.given_up_cause() == "an earlier scenario's device preparation timed out"
    assert budget.exhausted() is False  # an unbounded budget still never exhausts


def test_the_latch_is_write_once() -> None:
    # `run_all`'s `workers > 1` path can run several scenarios' crash-retry loops concurrently, so two
    # could abandon recovery for different reasons within the same window. The first cause to actually
    # establish the latch is the one every later scenario should read back — not whichever call
    # happened to land last, which would make the reported cause depend on scheduling.
    budget = RunCrashRecoveryBudget(budget=None)
    budget.mark_given_up("an earlier scenario's device preparation timed out")
    budget.mark_given_up("the run-level crash-recovery budget of 100s is exhausted")
    assert budget.given_up_cause() == "an earlier scenario's device preparation timed out"


def test_mark_given_up_rejects_an_empty_cause() -> None:
    # The cause is read verbatim into an operator-facing failure message; an empty one would silently
    # latch the run with no explanation of why.
    budget = RunCrashRecoveryBudget(budget=None)
    with pytest.raises(ValueError, match="non-empty"):
        budget.mark_given_up("")


def test_run_crash_recovery_budget_default_reads_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BAJUTSU_RUN_CRASH_RECOVERY_BUDGET", raising=False)
    assert _default_run_crash_recovery_budget() is None  # unset → unbounded
    monkeypatch.setenv("BAJUTSU_RUN_CRASH_RECOVERY_BUDGET", "900")
    assert _default_run_crash_recovery_budget() == 900.0
    monkeypatch.setenv("BAJUTSU_RUN_CRASH_RECOVERY_BUDGET", "0")
    assert _default_run_crash_recovery_budget() is None  # non-positive → unbounded
    monkeypatch.setenv("BAJUTSU_RUN_CRASH_RECOVERY_BUDGET", "nope")
    assert _default_run_crash_recovery_budget() is None  # invalid → unbounded


def _raising(exc: BaseException) -> Callable[[], None]:
    def teardown() -> None:
        raise exc

    return teardown


def test_guarded_teardown_warns_on_called_process_error(caplog: pytest.LogCaptureFixture) -> None:
    import logging
    import subprocess

    with caplog.at_level(logging.WARNING):
        guarded_teardown(
            _raising(subprocess.CalledProcessError(1, ["xcrun"])),
            mid_run=False,
            what="tearing down the warm runner on UDID",
        )
    assert any("tearing down the warm runner on UDID failed" in r.message for r in caplog.records)


def test_guarded_teardown_warns_on_os_error(caplog: pytest.LogCaptureFixture) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        guarded_teardown(
            _raising(ProcessLookupError("gone")),
            mid_run=False,
            what="tearing down the warm runner on UDID",
        )
    assert any("tearing down the warm runner on UDID failed" in r.message for r in caplog.records)


def test_guarded_teardown_warns_on_a_device_timeout_rather_than_calling_it_a_defect(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A wedged `simctl terminate` at teardown must not become a wiring defect (BE-0363).

    `mid_run=False` re-raises a defect, and the pool's `shutdown()` raises it from `run`'s
    `finally` — which would replace the results a finished run was about to return. Only
    `DeviceTimeout` is warned here; an ordinary `simctl.DeviceError` stays a defect.
    """
    import logging

    from bajutsu.common.backend_cli import simctl

    with caplog.at_level(logging.WARNING):
        guarded_teardown(
            _raising(simctl.DeviceTimeout("device operation timed out after 60s: terminate")),
            mid_run=False,
            what="tearing down the warm runner on UDID",
        )
    assert any("tearing down the warm runner on UDID failed" in r.message for r in caplog.records)


def test_guarded_teardown_still_treats_a_plain_device_error_as_a_defect() -> None:
    import pytest

    from bajutsu.common.backend_cli import simctl

    with pytest.raises(simctl.DeviceError, match="refused"):
        guarded_teardown(
            _raising(simctl.DeviceError("the device refused it")),
            mid_run=False,
            what="tearing down the warm runner on UDID",
        )


def test_guarded_teardown_propagates_unexpected_when_not_mid_run() -> None:
    import pytest

    with pytest.raises(AttributeError, match="missing"):
        guarded_teardown(
            _raising(AttributeError("missing")),
            mid_run=False,
            what="tearing down the warm runner on UDID",
        )


def test_guarded_teardown_swallows_unexpected_when_mid_run(
    caplog: pytest.LogCaptureFixture,
) -> None:
    import logging

    with caplog.at_level(logging.WARNING):
        guarded_teardown(
            _raising(AttributeError("missing")),
            mid_run=True,
            what="tearing down the discarded on-device lease",
        )
    assert any(
        "tearing down the discarded on-device lease failed" in r.message for r in caplog.records
    )

"""Tests for the fault-injection scaffolding the on-device lanes share (BE-0305).

`tests/fault_injection.py` runs only on a device lane, so nothing else in the fast gate executes it —
yet its whole job is to fail loudly when a lane's own machinery misbehaves, and every one of those
guarantees is on a path the lanes themselves never take when they pass. A defect in it would surface
as a red macOS or emulator job, diagnosed one metered CI round-trip at a time. So the module is
covered here, device-free, exactly as `tests/runner/test_backend_crash_recovery.py` covers the sibling
scaffolding for the conformance lane.
"""

from __future__ import annotations

import logging
import threading

import fault_injection
import pytest

_LOGGER = "tests.fault_injection.probe"  # a logger of this suite's own; no product code involved
_NEEDLE = "the layer engaged"


def _emit(message: str = _NEEDLE, level: int = logging.WARNING) -> None:
    logging.getLogger(_LOGGER).log(level, message)


def test_a_watched_record_is_captured_and_reported() -> None:
    with fault_injection.watch(_LOGGER, _NEEDLE) as log:
        _emit()
        _emit("something else entirely")
    assert log.seen
    assert log.mentions("something else")
    assert _NEEDLE in log.report()


def test_nothing_logged_says_so_rather_than_reporting_an_empty_block() -> None:
    # The report is what a failing lane prints, so its empty case has to read as an answer.
    with fault_injection.watch(_LOGGER, _NEEDLE) as log:
        pass
    assert log.seen is False
    assert log.report().strip() == "(nothing logged)"


def test_a_record_below_the_loggers_level_is_still_captured() -> None:
    # A lane keys on records the driver emits at its own chosen level; one later demoted to INFO must
    # keep arriving, or the lane would stall out its trigger budget and fail for an unrelated-looking
    # reason.
    logging.getLogger(_LOGGER).setLevel(logging.WARNING)
    with fault_injection.watch(_LOGGER, _NEEDLE) as log:
        _emit(level=logging.INFO)
    assert log.seen


def test_the_watched_logger_is_restored_even_when_the_body_raises() -> None:
    # A leaked handler or a left-open level would bleed into every later case in the lane.
    logger = logging.getLogger(_LOGGER)
    logger.setLevel(logging.ERROR)
    handlers, level = list(logger.handlers), logger.level
    with pytest.raises(RuntimeError), fault_injection.watch(_LOGGER, _NEEDLE):
        raise RuntimeError("the case failed")
    assert logger.handlers == handlers
    assert logger.level == level


def test_the_lift_runs_while_the_body_is_still_inside_the_faulted_call() -> None:
    # The reason the lift is on a background thread at all: a layer that recovers *within* one call can
    # only be observed recovering if the fault is lifted while that call is still running. A refactor to
    # a synchronous post-block lift would pass every other test here and quietly disarm both lanes.
    lifted = threading.Event()
    with (
        fault_injection.watch(_LOGGER, _NEEDLE) as log,
        fault_injection.lifted_when_reached(log, lifted.set, timeout=5),
    ):
        _emit()  # the driver reporting it reached the layer
        assert lifted.wait(timeout=5), "the lift did not run during the body"


def test_a_lift_that_raises_is_reported_as_a_failure() -> None:
    # The fault may still be in place, so this cannot be allowed to die with the lift thread.
    def lift() -> None:
        raise RuntimeError("the restore command failed")

    with (
        fault_injection.watch(_LOGGER, _NEEDLE) as log,
        pytest.raises(AssertionError, match="RuntimeError: the restore command failed"),
        fault_injection.lifted_when_reached(log, lift, timeout=5),
    ):
        _emit()


def test_a_lift_that_never_finishes_is_reported_as_a_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # A wedged restore command leaves the fault injected just as surely as one that raised.
    monkeypatch.setattr(fault_injection, "_JOIN_TIMEOUT_S", 0.1)
    wedged = threading.Event()  # never set: the lift blocks until the process ends

    def lift() -> None:
        wedged.wait(30)

    with (
        fault_injection.watch(_LOGGER, _NEEDLE) as log,
        pytest.raises(AssertionError, match="did not finish"),
        fault_injection.lifted_when_reached(log, lift, timeout=5),
    ):
        _emit()


def test_the_bodys_own_failure_is_never_masked_by_a_failed_lift() -> None:
    # The body's exception is the real result; the lift's is a note about the harness. A refactor that
    # moved the raises inside the `finally` would invert that and hide every genuine failure.
    def lift() -> None:
        raise RuntimeError("the restore command failed too")

    with (  # noqa: PT012 - the record must be emitted before the raise; that ordering is the test
        fault_injection.watch(_LOGGER, _NEEDLE) as log,
        pytest.raises(ValueError, match="the real failure"),
        fault_injection.lifted_when_reached(log, lift, timeout=5),
    ):
        _emit()
        raise ValueError("the real failure")


def test_the_lift_still_runs_when_the_record_never_arrives() -> None:
    # A renamed driver record must leave the device healthy: the lane then fails on its own `mentions`
    # assertion, which names what it wanted and what it saw, rather than on a fault left in place.
    lifted = threading.Event()
    with (
        fault_injection.watch(_LOGGER, _NEEDLE) as log,
        fault_injection.lifted_when_reached(log, lifted.set, timeout=0.1),
    ):
        _emit("a record the lane does not key on")
    assert lifted.is_set()
    assert log.seen is False  # so the lane's own assertion is what fails, loudly and specifically


def test_a_record_predating_the_fault_refuses_to_run_the_case() -> None:
    # The precondition that keeps the lane honest: with a matching record already captured, the waiter
    # falls straight through, the lift lands before the driver ever meets the fault, and `mentions`
    # would be satisfied by the stale record — green, having tested nothing.
    def lift() -> None:
        pass  # never reached: the precondition refuses the case before the thread starts

    with fault_injection.watch(_LOGGER, _NEEDLE) as log:
        _emit()  # the record lands *before* the fault, which is the mistake under test
        with (
            pytest.raises(AssertionError, match="already logged before the fault"),
            fault_injection.lifted_when_reached(log, lift, timeout=5),
        ):
            pass


def test_a_broken_record_does_not_raise_out_of_the_handler() -> None:
    # A handler must never turn a scaffolding defect into a failure of the driver it observes, so a
    # record it cannot render goes to `handleError` (stderr) rather than up the caller's stack. Handed
    # to the handler directly: pytest attaches a capture handler of its own that deliberately re-raises
    # a render failure, so going through `logging.warning` would measure pytest, not this module.
    class Unformattable:
        def __str__(self) -> str:
            raise ValueError("cannot render")

    log = fault_injection.FaultLog(_NEEDLE)
    broken = logging.LogRecord(
        _LOGGER, logging.WARNING, __file__, 0, "%s", (Unformattable(),), None
    )
    log.handle(broken)  # must not propagate
    log.handle(logging.LogRecord(_LOGGER, logging.WARNING, __file__, 0, _NEEDLE, (), None))
    assert log.seen

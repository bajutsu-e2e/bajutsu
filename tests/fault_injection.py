"""Shared scaffolding for the on-device fault-injection lanes (BE-0305).

A resilience layer is only proven by the condition it was built for, so these lanes inject the real
fault — a device whose screen is off and whose accessibility tree really is empty, a runner process
really stopped by a signal — rather than a fabricated count sequence or a raised exception. That
leaves one problem the synthetic fixtures never had: *when* to lift the fault. Lift it too early and
the driver never sees it; too late and the fault escalates past the layer under test. A wall-clock
delay would decide which layer is exercised by how long a `sleep` happened to be, which is exactly
the flakiness the project's determinism rule rejects.

So a lane lifts the fault on the driver's own report that it reached the layer being tested: the
retry that logs its attempt, the crash-recovery that logs the crash it is about to ride out. `watch`
captures those records and `lifted_when_reached` runs the restore the moment one of them names the
layer — a condition wait on observed behavior, with no fixed sleep anywhere. The evidence a lane
asserts on is `seen` / `mentions`, read from the captured records, never the event the threads signal
each other with — so the scaffolding can force its waiter loose on the way out without ever
manufacturing the evidence.

Everything the scaffolding itself can get wrong fails loudly rather than quietly weakening a lane: a
restore that raises, or one still running when the block ends, is re-raised as a failure naming the
fault that may still be in place, because a fault left injected would poison every later case (and a
developer's device). The one thing deliberately *not* raised here is a record that never arrived — the
lane's own `mentions` assertion says that far more specifically, naming the record it wanted and
everything it saw.

Not collected by pytest itself (no `test_` filename); the lanes import it, and
`tests/test_fault_injection.py` exercises it on the fast gate, where no device is involved.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

_logger = logging.getLogger(__name__)

# How long to wait for the lift thread to finish once the waiter has been released. Separate from the
# caller's trigger budget, which funds waiting for a *record* that may legitimately take the channel's
# whole retry budget to arrive: by the time this is waited on, the lift has already been signalled, so
# anything beyond a few seconds is a wedged restore command rather than slow progress — and on a
# metered runner, charging the trigger budget twice for one failing case is pure dead time.
_JOIN_TIMEOUT_S = 30.0


class FaultLog(logging.Handler):
    """A driver logger's records, plus the event that releases a lane's restore thread.

    `logging` serializes `emit` under the handler's lock, so records are appended from one thread at a
    time; the lane's restore thread reads them through `mentions` / `seen` concurrently, which is safe
    on a list's atomic append and read.
    """

    def __init__(self, needle: str) -> None:
        super().__init__(level=logging.NOTSET)  # the logger's level decides; never filter here
        self.needle = needle
        self.records: list[str] = []
        # Purely a signal: `lifted_when_reached` also sets it to release its waiter on the way out, so
        # it says "stop waiting", never "the record arrived". `seen` is the latter, and the only thing
        # a lane may assert on.
        self.proceed = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        # A handler must not raise into the code it observes (`Handler.handle` does not guard `emit`):
        # a defect here would surface as a failure of the driver under test rather than of this module.
        try:
            message = record.getMessage()
            self.records.append(message)
            if self.needle in message:
                self.proceed.set()
        except Exception:  # pragma: no cover - the standard handler contract for a broken record
            self.handleError(record)

    @property
    def seen(self) -> bool:
        """Whether the watched record really arrived — the fact `proceed` cannot be trusted for."""
        return self.mentions(self.needle)

    def mentions(self, needle: str) -> bool:
        """Whether any captured record names `needle` — the evidence a lane asserts on."""
        return any(needle in record for record in self.records)

    def report(self) -> str:
        """The captured records as one block, for an assertion message that says what was seen."""
        return "\n".join(f"  {record}" for record in self.records) or "  (nothing logged)"


@contextmanager
def watch(logger_name: str, needle: str) -> Iterator[FaultLog]:
    """Capture `logger_name`'s records for the duration of the block, watching for `needle`.

    The logger is opened to `DEBUG` while the block runs: a lane keys on records the driver emits at
    its own chosen level, so a record later demoted from `WARNING` to `INFO` would otherwise stop
    arriving — and the lane would stall out its whole trigger budget before failing for a reason that
    looks nothing like "the level moved".
    """
    log = FaultLog(needle)
    logger = logging.getLogger(logger_name)
    previous = logger.level
    logger.addHandler(log)
    logger.setLevel(logging.DEBUG)
    try:
        yield log
    finally:
        logger.setLevel(previous)
        logger.removeHandler(log)


@contextmanager
def lifted_when_reached(
    log: FaultLog, lift: Callable[[], None], *, timeout: float
) -> Iterator[None]:
    """Lift the injected fault as soon as `log` reports the layer under test was reached.

    The lift runs on a background thread, so it happens while the driver is still inside the faulted
    call — the only way a layer that recovers *within* one call can be observed recovering. It also
    always runs before the block returns, including when the driver raised or when the record never
    arrived: `timeout` bounds that wait and the fault is lifted regardless, so a renamed record leaves
    the device healthy and the lane fails on `mentions` rather than on a stopped process or a dark
    screen inherited by the next case.

    Raises:
        AssertionError: if the watched record had already arrived before the fault was injected (the
            lift would fire at once and the lane would prove nothing), or if the lift raised or had not
            finished in time — either way the fault may still be in place, which no later case could
            survive and none would diagnose. Never raised over the body's own exception, which is the
            real failure.
    """
    # The caller must inject the fault *after* opening `watch`, on a log with no matching record yet.
    # Otherwise the waiter falls straight through, the lift lands before the driver ever meets the
    # fault, and the lane's `mentions` assertion is satisfied by the stale record: green, tested nothing.
    if log.seen:
        raise AssertionError(
            f"{log.needle!r} was already logged before the fault was injected, so the lift would fire "
            f"immediately and the case would prove nothing:\n{log.report()}"
        )
    lifted = threading.Event()
    failure: BaseException | None = None

    def wait_and_lift() -> None:
        nonlocal failure
        try:
            log.proceed.wait(timeout=timeout)
            lift()
        # Caught wholesale on purpose: whatever the lift raised, the fault may still be in place, so it
        # is carried out to the caller below rather than dying with this thread. Logged here too, since
        # a body that raised takes precedence and would otherwise discard this cause entirely.
        except BaseException as exc:
            failure = exc
            _logger.warning("lifting the injected fault raised", exc_info=True)
        finally:
            lifted.set()

    threading.Thread(target=wait_and_lift, daemon=True).start()
    try:
        yield
    finally:
        # Release the waiter even when the layer was never reached. Setting `proceed` cannot fake the
        # lane's evidence: that is `log.seen` / `log.mentions`, read from the captured records.
        log.proceed.set()
        finished = lifted.wait(timeout=_JOIN_TIMEOUT_S)
    # Past the `finally`, so a failure raised by the body — the interesting one — is never masked.
    if not finished:
        raise AssertionError(
            f"lifting the injected fault did not finish within {_JOIN_TIMEOUT_S}s; it may still be in "
            "place (a later case would fail for reasons of its own)"
        )
    if failure is not None:
        raise AssertionError(
            f"lifting the injected fault raised {type(failure).__name__}: {failure}; the fault may "
            "still be in place"
        ) from failure

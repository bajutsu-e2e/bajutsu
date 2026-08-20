"""Cooperative cancellation: a `SIGTERM` a run answers at a safe boundary (BE-0370).

Python's default disposition for `SIGTERM` ends the process at once, so a `bajutsu run` cancelled
from the `serve` Web UI died mid-scenario — before `_assemble_report` wrote `manifest.json` and
before the final `PASS/FAIL runs/<id>/manifest.json` line `serve` reads the run id from. The
cancelled attempt then left no trace in the run history at all.

This module holds the whole cancellation vocabulary: the read-only source the runner and the
orchestrator poll, the exception a poll loop raises to unwind to the nearest safe boundary, and the
signal-to-event bridge `bajutsu run`'s entry point installs. It imports nothing from Bajutsu, so
every layer — the deterministic core, the CLI, and `serve` — can reach it.

Nothing here judges a run: a cancelled scenario is failed by the deterministic pipeline
(`failure: "cancelled"`), never by a model, and the checks ride poll loops that are already bounded
rather than introducing a wait of their own.
"""

from __future__ import annotations

import logging
import os
import signal
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

_logger = logging.getLogger(__name__)

# Reports whether cancellation has been requested. Injected the way `NetworkSource` /
# `TransitionSource` are, so the runner and the orchestrator read one process-wide fact without
# holding global state of their own — and a test drives it with a plain callable.
CancelSource = Callable[[], bool]

# The `RunResult.failure` a cancelled scenario carries, so the report and the Web UI can tell a
# cancelled scenario from an assertion failure without reading prose. A scenario the cancel simply
# stopped carries exactly this; one where the cancel interrupted something the report should still
# name — a backend crash whose recovery the cancel cut short — leads with it and appends that detail,
# so the test is a prefix rather than equality.
CANCELLED_FAILURE = "cancelled"


class RunCancelled(Exception):
    """Raised inside a scenario's step loop or a condition wait once cancellation is requested.

    Unwinds to `run_scenario`, which turns it into `RunResult(ok=False, failure="cancelled")` — so a
    poll loop needs one exit line rather than a new return shape, and the scenario's evidence is
    still finalized by the sink teardown the unwind passes through.
    """


def not_cancelled() -> bool:
    """The default `CancelSource`: nothing has asked this run to stop."""
    return False


# The grace window `serve` gives a cancelled run to close itself out, and the fallback for a `run`
# invoked with no window passed to it. It has to exceed the longest single driver call, since a
# scenario blocked inside one notices cancellation only once that call returns: the XCUITest
# channel's actuation timeout is 30s, and a read that rides the BE-0207 transient retry is bounded
# by its own 60s recovery timeout. Overridable per deployment without a code change.
GRACE_ENV = "BAJUTSU_CANCEL_GRACE"
DEFAULT_GRACE_SECONDS = 60.0

# How far beyond the grace window it receives the handler's own deadline sits. The two must not be
# picked independently: were the internal one shorter, the run would kill itself before the manifest
# was written — reproducing the silent gap this item removes, for every ordinary `serve` cancel too,
# since `serve`'s longer window could never rescue a run that already killed itself.
HANDLER_MARGIN_SECONDS = 10.0


def grace_seconds() -> float:
    """The cancellation grace window in seconds, from `BAJUTSU_CANCEL_GRACE` or the default.

    `serve` reads it to size both the window it waits before escalating to an unconditional kill and
    the value it passes down to the run it spawns, so the two are one number rather than two that
    could drift.
    """
    raw = os.environ.get(GRACE_ENV)
    if not raw:
        return DEFAULT_GRACE_SECONDS
    try:
        value = float(raw)
    except ValueError:
        return DEFAULT_GRACE_SECONDS
    return value if value > 0 else DEFAULT_GRACE_SECONDS


def handler_deadline(grace: float) -> float:
    """The seconds the signal handler gives a graceful shutdown before it kills the process itself.

    Strictly beyond *grace* (the window an external escalator — `serve` — is already waiting), so
    the internal backstop can never fire first and pre-empt the cooperative path.
    """
    return grace + HANDLER_MARGIN_SECONDS


@contextmanager
def graceful_sigterm() -> Iterator[CancelSource]:
    """Answer `SIGTERM` by requesting cancellation instead of dying at once.

    Every `SIGTERM` this process receives is answered, not only `serve`'s Cancel button: a
    `docker stop`, a systemd unit stop, and a CI job cancellation all reach the same handler. The
    shutdown stays bounded with no external escalator watching: setting the event also arms an
    interval timer for `handler_deadline`, and a shutdown still unfinished by then restores
    `SIGTERM`'s default disposition and re-raises it, so a genuinely wedged runner dies exactly as
    it would have without this handler. A second `SIGTERM` during the window is a no-op rather than
    a hard kill, so an operator clicking Cancel twice does not lose the manifest.

    Yields the `CancelSource` the run polls. When the handler cannot be installed — not the main
    thread, or a platform with no interval timer — it warns and yields `not_cancelled`, leaving the
    process on the default disposition it had before: today's immediate termination, disclosed
    rather than silently assumed away.
    """
    event = threading.Event()
    deadline = handler_deadline(grace_seconds())

    def _expire(_signum: int, _frame: object) -> None:
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        signal.raise_signal(signal.SIGTERM)

    def _request(_signum: int, _frame: object) -> None:
        if event.is_set():
            return  # already shutting down; re-arming the timer would extend the bound
        event.set()
        signal.setitimer(signal.ITIMER_REAL, deadline)

    try:
        # SIGALRM first: it is the call that fails on a platform without it, so a failure leaves no
        # half-installed handler behind. `ValueError` is the not-the-main-thread case, which would
        # fail on either call.
        previous_alarm = signal.signal(signal.SIGALRM, _expire)
        previous_term = signal.signal(signal.SIGTERM, _request)
    except (ValueError, AttributeError) as exc:
        _logger.warning(
            "cooperative cancellation is unavailable (%s); SIGTERM keeps its default disposition, "
            "so a cancelled run writes no report",
            exc,
        )
        yield not_cancelled
        return
    try:
        yield event.is_set
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGTERM, previous_term)
        signal.signal(signal.SIGALRM, previous_alarm)

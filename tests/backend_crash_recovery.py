"""Infrastructure-fault recovery for the on-device driver conformance suite (BE-0334).

The driver conformance suite (`tests/test_driver_conformance_ondevice.py`) drives a real Simulator
through a module-scoped lease it obtains by calling `launch_driver` directly — a pytest harness, not
a `bajutsu run`, so it inherits none of the pipeline's crash recovery. A resident-runner crash
(`base.BackendCrashError`) is infrastructure, not a contract verdict; this plugin re-leases a fresh
device (a cold respawn) and re-runs the affected test, bounded by the same count + wall-clock budget
the pipeline uses (`bajutsu.runner.recovery`), so the two recovery paths cannot drift. A contract
violation is not a `BackendCrashError`, so it is never retried — it keeps failing immediately.

A suite opts in by marking its module `backend_crash_recovery` and exposing a module-scoped
`_backend_launch` fixture (a zero-arg callable returning a fresh `base.Driver`, i.e. a cold spawn);
the `_backend_lease_holder` fixture here wraps it in a `LeaseHolder` the plugin re-leases between
attempts. The plugin is inert for any test the marker does not cover.

Every respawn is reported (BE-0334 Unit 4): announced inline in the job log as it happens, and
counted into a JSON report at `BAJUTSU_BACKEND_RECOVERY_REPORT` (an uploaded CI artifact) — so a
degrading lane is visible as a rising count rather than merely looking slower, and a maintainer can
tell whether the underlying fault is getting worse or staying rare.
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from _pytest.runner import runtestprotocol

from bajutsu.drivers import base
from bajutsu.runner.recovery import (
    CrashRecoveryBudget,
    RetryDecision,
    _default_crash_recovery_budget,
    _default_crash_retries,
    is_infrastructure_fault,
)

_logger = logging.getLogger(__name__)

RECOVER_MARKER = "backend_crash_recovery"

# A path to write the JSON recovery report to (an uploaded CI artifact). Unset (the default, and the
# fast gate) writes nothing — the plugin only ever *counts*, it never gates.
_REPORT_ENV = "BAJUTSU_BACKEND_RECOVERY_REPORT"

# Set by the makereport wrapper on a report whose exception is an infrastructure fault, carrying the
# crash message. `None`/absent means "not a backend crash" (fail immediately), which is all the
# protocol hook reads it for — the message rides along for whoever surfaces it.
_CRASH_ATTR = "_backend_crash_reason"

# Every crash the recovery loop saw, in order, accumulated across the session for the report.
_EVENTS: pytest.StashKey[list[dict[str, object]]] = pytest.StashKey()


class LeaseHolder:
    """A module-scoped device lease that re-leases on demand (BE-0334).

    `driver` lazily launches on first use and re-launches after `invalidate()`, so a cold respawn
    after a crash is a property access rather than a fixture rebuild. In the common (crash-free) case
    the launch happens once and is reused across the whole module — the amortization the module scope
    exists for.
    """

    def __init__(self, launch: Callable[[], base.Driver]) -> None:
        self._launch = launch
        self._driver: base.Driver | None = None

    @property
    def driver(self) -> base.Driver:
        if self._driver is None:
            self._driver = self._launch()
        return self._driver

    def invalidate(self) -> None:
        """Discard the current (dead) lease so the next `driver` access cold-respawns."""
        dead, self._driver = self._driver, None
        if dead is not None:
            # The lease is already dead; a failed close must not mask the crash that prompted it. Log
            # it, though — a close that fails every respawn is how leaked Simulators/ports would show.
            try:
                dead.close()
            except Exception:
                _logger.debug("closing the crashed lease failed; ignoring", exc_info=True)

    def close(self) -> None:
        self.invalidate()


# The module's live holder, keyed by module path, so the protocol hook can re-lease it between
# attempts without threading it through the report objects. The suite runs serially (`-n0`), and the
# stash is per-session, so a plain dict is safe. Populated by `_backend_lease_holder`.
_HOLDERS: pytest.StashKey[dict[object, LeaseHolder]] = pytest.StashKey()


@pytest.fixture(scope="module")
def _backend_lease_holder(
    request: pytest.FixtureRequest, _backend_launch: Callable[[], base.Driver]
):
    holder = LeaseHolder(_backend_launch)
    registry = request.session.stash.setdefault(_HOLDERS, {})
    registry[request.path] = holder
    yield holder
    holder.close()
    registry.pop(request.path, None)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{RECOVER_MARKER}: on-device suite that recovers a Simulator infrastructure fault by "
        "re-leasing (BE-0334)",
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Tag a report whose exception is an infrastructure fault, so the protocol hook can recover it.

    The classification has to happen here, where the live exception is in hand: a `TestReport` keeps
    only a rendered `longrepr`, not the exception object, so the protocol hook downstream cannot tell
    a `BackendCrashError` from a contract violation without this tag.
    """
    report = yield
    if call.excinfo is not None and is_infrastructure_fault(call.excinfo.value):
        setattr(report, _CRASH_ATTR, str(call.excinfo.value))
    return report


def _crash_reason(reports: list[pytest.TestReport]) -> str | None:
    """The message of the first infrastructure-fault report in `reports`, or None if there is none."""
    for report in reports:
        reason = getattr(report, _CRASH_ATTR, None)
        if reason is not None:
            return reason
    return None


def pytest_runtest_protocol(item: pytest.Item, nextitem: pytest.Item | None) -> bool | None:
    # Inert unless the item's module opted in: the default protocol runs for everything else.
    if item.get_closest_marker(RECOVER_MARKER) is None:
        return None

    budget = CrashRecoveryBudget(
        _default_crash_retries(), _default_crash_recovery_budget(), time.monotonic
    )
    item.ihook.pytest_runtest_logstart(nodeid=item.nodeid, location=item.location)
    attempt = 1
    while True:
        # Run setup+call+teardown without logging, so a crashed attempt leaves no failure on the
        # record until recovery is exhausted — only the terminal outcome is published. A crash in any
        # phase re-runs the *whole* item (as the pipeline re-runs the whole scenario), so a teardown
        # crash after a passing call replays that call — safe here, the conformance tests are idempotent.
        reports = runtestprotocol(item, nextitem=nextitem, log=False)
        reason = _crash_reason(reports)
        if reason is None:
            _publish(item, reports)
            break
        decision = budget.on_crash(attempt)
        _record_crash(item, attempt, budget.total_attempts, decision, reason)
        if not decision.will_retry:
            # Recovery is over and the test never passed: publish the failing reports so the crash
            # fails loudly (BE-0049), rather than being absorbed into a slow green. Discard the dead
            # lease so the *next* test cold-respawns onto a fresh device instead of inheriting the dead
            # runner — one crash fails only its own test, it does not cascade across the module (Unit 3).
            _publish(item, reports)
            _invalidate_holder(item)
            break
        # Discard the dead lease so the re-run cold-respawns onto a fresh device, then reset the
        # item's fixture request so its function-scoped fixtures resolve again on the next attempt.
        _invalidate_holder(item)
        item._initrequest()  # type: ignore[attr-defined]
        attempt += 1
    item.ihook.pytest_runtest_logfinish(nodeid=item.nodeid, location=item.location)
    return True


def _record_crash(
    item: pytest.Item,
    attempt: int,
    total_attempts: int,
    decision: RetryDecision,
    reason: str,
) -> None:
    """Announce a crash in the job log and record it for the report (BE-0334 Unit 4)."""
    events = item.session.stash.setdefault(_EVENTS, [])
    events.append(
        {
            "nodeid": item.nodeid,
            "attempt": attempt,
            "totalAttempts": total_attempts,
            "willRetry": decision.will_retry,
            "budgetSpent": decision.budget_spent,
            "reason": reason,
        }
    )
    # Write the line inline (via the terminal reporter, not the captured per-test log) so a respawn is
    # visible in the job log even on a test that then recovers to green.
    if decision.will_retry:
        line = (
            f"⟳ {item.nodeid}: backend crashed (attempt {attempt}/{total_attempts}), "
            f"respawning and retrying: {reason}"
        )
    elif decision.budget_spent:
        line = (
            f"✘ {item.nodeid}: backend crashed and did not recover within the crash-recovery "
            f"budget (spent respawning across {attempt} attempt(s)): {reason}"
        )
    else:
        line = (
            f"✘ {item.nodeid}: backend crashed and did not recover across "
            f"{total_attempts} attempts: {reason}"
        )
    _logger.warning(line)
    reporter = item.config.pluginmanager.getplugin("terminalreporter")
    if reporter is not None:
        reporter.write_line(line)


def pytest_sessionfinish(session: pytest.Session) -> None:
    # Only when a report path is configured (a CI lane); the fast gate leaves it unset and writes
    # nothing. Written even with zero crashes, so the lane always uploads a clean, present artifact.
    path = os.environ.get(_REPORT_ENV)
    if not path:
        return
    events = session.stash.get(_EVENTS, [])
    by_test: dict[object, list[dict[str, object]]] = {}
    for event in events:
        by_test.setdefault(event["nodeid"], []).append(event)
    # A test is "exhausted" if any of its crashes gave up (a will_retry=False event); otherwise it
    # crashed but ultimately recovered to green.
    exhausted = sum(1 for evs in by_test.values() if any(not e["willRetry"] for e in evs))
    summary = {
        "respawns": sum(1 for e in events if e["willRetry"]),
        "recovered": len(by_test) - exhausted,
        "exhausted": exhausted,
        "events": events,
    }
    Path(path).write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")


def _publish(item: pytest.Item, reports: list[pytest.TestReport]) -> None:
    for report in reports:
        item.ihook.pytest_runtest_logreport(report=report)


def _invalidate_holder(item: pytest.Item) -> None:
    registry = item.session.stash.get(_HOLDERS, {})
    holder = registry.get(item.path)
    if holder is not None:
        holder.invalidate()

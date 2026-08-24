"""Infrastructure-fault recovery for the on-device driver conformance suite (BE-0334).

The driver conformance suite (`tests/test_driver_conformance_ondevice.py`) drives a real Simulator
through a module-scoped lease it obtains by calling `launch_driver` directly — a pytest harness, not
a `bajutsu run`, so it inherits none of the pipeline's crash recovery. A resident-runner crash
(`base.BackendCrashError`) is infrastructure, not a contract verdict; this plugin re-leases a fresh
device (a cold respawn) and re-runs the affected test, bounded by the same count + wall-clock budget
the pipeline uses (`bajutsu.runner.recovery`), so the two recovery paths cannot drift. A contract
violation is not a `BackendCrashError`, so it is never retried — it keeps failing immediately.

A suite opts in by marking its module `backend_crash_recovery` and exposing a module-scoped
`_backend_launch` fixture (a zero-arg callable returning a fresh `(base.Driver, teardown)` pair —
the driver and the platform teardown that reaches the runner process it lives in; BE-0342); the
`_backend_lease_holder` fixture here wraps it in a `LeaseHolder` the plugin re-leases between
attempts. The plugin is inert for any test the marker does not cover.

A wedged CoreSimulator is the host's fault too, but not a respawn's to repair (BE-0378), so it takes
the other outcome: the failure stands, and the plugin names it a host fault rather than leaving it to
read as a conformance verdict. The lease is deliberately left alone — discarding it would make the
next test pay the cold respawn this fault does not warrant.

Both outcomes are reported (BE-0334 Unit 4, BE-0378): announced inline in the job log as they
happen, and counted into a JSON report at `BAJUTSU_BACKEND_RECOVERY_REPORT` (an uploaded CI
artifact) — so a degrading lane is visible as a rising count rather than merely looking slower, and a
maintainer can tell whether the underlying fault is getting worse or staying rare.
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
    guarded_teardown,
    is_host_fault,
    recovers_by_respawn,
)

_logger = logging.getLogger(__name__)

RECOVER_MARKER = "backend_crash_recovery"

# A path to write the JSON recovery report to (an uploaded CI artifact). Unset (the default, and the
# fast gate) writes nothing — the plugin only ever *counts*, it never gates.
_REPORT_ENV = "BAJUTSU_BACKEND_RECOVERY_REPORT"

# Set by the makereport wrapper on a report whose exception a respawn may repair, carrying the crash
# message. `None`/absent means "not a backend crash" (fail immediately), which is all the protocol
# hook reads it for — the message rides along for whoever surfaces it.
_CRASH_ATTR = "_backend_crash_reason"

# The same, for a host fault no respawn repairs (BE-0378): the failure stands, and this tag is what
# lets the protocol hook report it as the host's rather than the driver contract's.
_HOST_FAULT_ATTR = "_backend_host_fault_reason"

# Every crash the recovery loop saw, in order, accumulated across the session for the report.
_EVENTS: pytest.StashKey[list[dict[str, object]]] = pytest.StashKey()

# The zero-arg teardown a suite's launch thunk returns alongside its driver (BE-0342): the platform's
# own environment teardown, not `driver.close()` — only the web driver implements that, and on iOS
# the runner process belongs to the environment.
LeaseTeardown = Callable[[], None]
LeaseLaunch = Callable[[], tuple[base.Driver, LeaseTeardown]]


class LeaseHolder:
    """A module-scoped device lease that re-leases on demand (BE-0334, BE-0342).

    `driver` lazily launches on first use and re-launches after `invalidate()`, so a cold respawn
    after a crash is a property access rather than a fixture rebuild. In the common (crash-free) case
    the launch happens once and is reused across the whole module — the amortization the module scope
    exists for. Discard runs the launch thunk's teardown so the runner process is actually gone
    before the next lease starts.
    """

    def __init__(self, launch: LeaseLaunch) -> None:
        self._launch = launch
        self._driver: base.Driver | None = None
        self._teardown: LeaseTeardown | None = None
        self._generation = 0

    @property
    def driver(self) -> base.Driver:
        if self._driver is None:
            self._driver, self._teardown = self._launch()
            # Only a launch that returned counts: a bring-up that raised leased nothing, so nothing
            # a caller could have memoised against this identity ever existed (BE-0378).
            self._generation += 1
        return self._driver

    @property
    def generation(self) -> int:
        """How many leases this holder has taken — the current lease's identity, 0 before the first.

        A caller that caches something belonging to the *installed app* rather than to the holder —
        the conformance harness's data-container path — memoises it against this number, so a cold
        respawn's `clean` reinstall drops the cache along with the container it named (BE-0378).
        """
        return self._generation

    def invalidate(self) -> None:
        """Discard the current (dead) lease so the next `driver` access cold-respawns."""
        # Mid-run: a teardown failure must not mask the crash that prompted the discard (BE-0342).
        self._discard(mid_run=True)

    def close(self) -> None:
        """Final module release — a wiring defect fails the teardown rather than being swallowed."""
        self._discard(mid_run=False)

    def _discard(self, *, mid_run: bool) -> None:
        self._driver = None
        dead_teardown, self._teardown = self._teardown, None
        if dead_teardown is None:
            return
        guarded_teardown(
            dead_teardown,
            mid_run=mid_run,
            what="tearing down the discarded on-device lease",
        )


# The module's live holder, keyed by module path, so the protocol hook can re-lease it between
# attempts without threading it through the report objects. The suite runs serially (`-n0`), and the
# stash is per-session, so a plain dict is safe. Populated by `_backend_lease_holder`.
_HOLDERS: pytest.StashKey[dict[object, LeaseHolder]] = pytest.StashKey()


@pytest.fixture(scope="module")
def _backend_lease_holder(request: pytest.FixtureRequest, _backend_launch: LeaseLaunch):
    holder = LeaseHolder(_backend_launch)
    registry = request.session.stash.setdefault(_HOLDERS, {})
    registry[request.path] = holder
    yield holder
    holder.close()
    registry.pop(request.path, None)


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers",
        f"{RECOVER_MARKER}: on-device suite that recovers a crashed backend by re-leasing "
        "(BE-0334), and reports a wedged host as a host fault instead (BE-0378)",
    )


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo):
    """Tag a report the protocol hook must act on: a crash to recover, or a host fault to report.

    The classification has to happen here, where the live exception is in hand: a `TestReport` keeps
    only a rendered `longrepr`, not the exception object, so the protocol hook downstream cannot tell
    a `BackendCrashError` from a contract violation without this tag. The `elif` is what makes the
    second tag mean "a host fault *no respawn repairs*" — a crash is both, and the retry decision
    outranks the diagnosis (BE-0378).
    """
    report = yield
    if call.excinfo is not None:
        exc = call.excinfo.value
        if recovers_by_respawn(exc):
            setattr(report, _CRASH_ATTR, str(exc))
        elif is_host_fault(exc):
            setattr(report, _HOST_FAULT_ATTR, str(exc))
    return report


def _tagged_reason(reports: list[pytest.TestReport], attr: str) -> str | None:
    """The message of the first report in `reports` carrying `attr`, or None if none does."""
    for report in reports:
        reason = getattr(report, attr, None)
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
        reason = _tagged_reason(reports, _CRASH_ATTR)
        if reason is None:
            # No respawn-recoverable crash, so this attempt is terminal either way — but a wedged
            # host still gets named, so the red check reads as the host's fault rather than as a
            # conformance verdict. The lease is left intact on purpose (BE-0378): discarding it would
            # charge the next test a cold respawn, the very remedy this fault does not warrant.
            host_fault = _tagged_reason(reports, _HOST_FAULT_ATTR)
            if host_fault is not None:
                _record_host_fault(item, host_fault)
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
            "kind": "crash",
            "nodeid": item.nodeid,
            "attempt": attempt,
            "totalAttempts": total_attempts,
            "willRetry": decision.will_retry,
            "budgetSpent": decision.budget_spent,
            "reason": reason,
        }
    )
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
    _announce(item, line)


def _record_host_fault(item: pytest.Item, reason: str) -> None:
    """Announce a host fault the lane deliberately did not retry, and record it (BE-0378 unit 3).

    The reason carries the command and the deadline it exceeded, since that is what
    `simctl.DeviceTimeout`'s own message says — enough for a maintainer reading either the job log
    or the uploaded report to see the wedge without opening the failing test.
    """
    events = item.session.stash.setdefault(_EVENTS, [])
    events.append({"kind": "hostFault", "nodeid": item.nodeid, "reason": reason})
    _announce(
        item,
        f"✘ {item.nodeid}: host fault, not a verdict — reported and deliberately not retried, "
        f"since a respawn is built from the very calls that stalled: {reason}",
    )


def _announce(item: pytest.Item, line: str) -> None:
    # Write the line inline (via the terminal reporter, not the captured per-test log) so the event is
    # visible in the job log even on a test that then recovers to green.
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
    # The retry tallies below count crashes only: a host fault took no attempt and had none to
    # exhaust, so folding it in would read as a recovery that never happened (BE-0378).
    crashes = [e for e in events if e["kind"] == "crash"]
    by_test: dict[object, list[dict[str, object]]] = {}
    for event in crashes:
        by_test.setdefault(event["nodeid"], []).append(event)
    # A test is "exhausted" if any of its crashes gave up (a will_retry=False event); otherwise every
    # crash chose to retry, so the infra fault was recovered — though a later genuine (non-crash)
    # failure can still redden the test, which "recovered" does not distinguish.
    exhausted = sum(1 for evs in by_test.values() if any(not e["willRetry"] for e in evs))
    summary = {
        "respawns": sum(1 for e in crashes if e["willRetry"]),
        "recovered": len(by_test) - exhausted,
        "exhausted": exhausted,
        # Beside the respawn count, so a degrading host shows up as a rising wedge count rather than
        # as a red required check somebody re-ran (BE-0378).
        "hostFaults": len(events) - len(crashes),
        "events": events,
    }
    target = Path(path)
    try:
        # Best-effort observability that only ever counts, never gates: create the parent (a lane may
        # point at a not-yet-created artifacts dir) and swallow any OS error — an unwritable report path
        # must not raise out of sessionfinish and fail an otherwise-green suite.
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    except OSError:
        _logger.warning(
            "could not write the backend-recovery report to %s; skipping", path, exc_info=True
        )


def _publish(item: pytest.Item, reports: list[pytest.TestReport]) -> None:
    for report in reports:
        item.ihook.pytest_runtest_logreport(report=report)


def _invalidate_holder(item: pytest.Item) -> None:
    registry = item.session.stash.get(_HOLDERS, {})
    holder = registry.get(item.path)
    if holder is not None:
        holder.invalidate()

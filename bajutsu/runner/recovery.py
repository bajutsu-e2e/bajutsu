"""Shared backend-crash recovery bookkeeping (BE-0334, BE-0342).

`bajutsu run` recovers from a Simulator infrastructure fault: a mid-run `base.BackendCrashError`
— a resident-runner crash, a readiness gate that crashed the runner, a lease bring-up that died —
is treated as infrastructure, not a verdict. The dead lease is discarded, a fresh device is leased
in a cold respawn, and the affected work re-runs, bounded by a retry *count* (`crash_retries`) and
an optional wall-clock *budget* (`crash_recovery_budget`). A contract violation — a mis-resolved
selector, a refused actuator, a failed assertion — is not infrastructure and keeps failing at once.

That decision logic first lived inline in the pipeline's scenario loop. The on-device driver
conformance suite (`tests/test_driver_conformance_ondevice.py`) needs the *same* recovery, so it is
extracted here; the pipeline drives it today, and Unit 2 wires the conformance harness onto it, so
the two cannot then drift into different notions of "an infrastructure fault" or "how many respawns
are left" (BE-0334). The classification rests on the exception type the driver already raises, so it
stays a deterministic branch on a Python class: no model sits on the `run`/CI verdict.

The guarded teardown helper (BE-0342) is the same seam: the pool's own teardown sites — a device's
environment and its collector socket, at every point the pool starts, switches, releases, or tears
one down — `launch_driver`'s guard for a launch that failed after `env.start`, and the on-device
suites' lease discard share one policy for an
already-gone resource — a runner that had already exited, an unreachable `xcrun`, a socket the OS
already tore down — so the two recovery paths cannot drift into different notions of "an expected
teardown failure" either.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass

from bajutsu.drivers import base

_logger = logging.getLogger(__name__)


def guarded_teardown(teardown: Callable[[], None], *, mid_run: bool, what: str) -> None:
    """Run `teardown`, warning on an expected process failure instead of re-raising.

    A runner that had already exited, an unreachable `xcrun`, and a collector socket the OS already
    tore down all surface as `CalledProcessError` or `OSError`; those are always logged at warning
    and swallowed. The pair covers the subprocess-driven backends (simctl, adb) and the collector's
    own socket close; `PlaywrightDriver.close()` suppresses its own already-gone-target failure
    before it ever reaches this function, the same way `reset_context`/`relaunch` already do, so an
    ordinary dead browser never lands in the branch below either — and
    `XcuitestLiveEnvironment.teardown()` suppresses its own already-expired-session `WebDriverError`
    the same way, since that class subclasses `RuntimeError`, not `OSError`. Anything
    else is a wiring defect: at most call sites (`mid_run=True`) it is also swallowed into a warning
    so it cannot mask the fault that prompted the teardown, or abandon cleanup the caller still owes
    (the pool's `free.put(udid)`, on a site that runs ahead of its own `try`); only `mid_run=False`
    re-raises it, for a caller that must still fail on a wiring defect — the pool's `shutdown()`
    catches that re-raise so the rest of its sweep still runs, then raises it once the sweep is done
    (BE-0342).

    Args:
        teardown: The zero-arg callable that tears the environment (or warm resident) down, or runs
            other best-effort cleanup on a failure path (the pool's own repeated `adopt_replacement()`
            call, which re-keys pool state rather than tearing anything down).
        mid_run: False when the caller must still see a wiring defect (and is responsible for any
            cleanup it owes first), True when re-raising would mask the fault that prompted this
            teardown or abandon cleanup the caller cannot resume.
        what: A short description of the teardown site, used as the warning's subject.
    """
    try:
        teardown()
    except (subprocess.CalledProcessError, OSError) as exc:
        _logger.warning("%s failed: %s", what, exc)
    except Exception:
        if mid_run:
            _logger.warning("%s failed", what, exc_info=True)
            return
        raise


# The default backend-crash retry budget, overridable per lane without a code change. A resident
# XCUITest runner crashes more on a loaded/contended CI host (the XCTest host's accessibility bridge
# under actuation), so a lane on such hardware can raise the budget for infrastructure crashes — the
# crash is the test host dying, not an app verdict, so riding it out is not flakiness-by-absorption
# (BE-0049): work that crashes every attempt still fails once the budget is spent. Default 1
# (two attempts), the value before this knob existed, so an unset environment is unchanged.
_CRASH_RETRIES_ENV = "BAJUTSU_CRASH_RETRIES"
_DEFAULT_CRASH_RETRIES = 1


def _default_crash_retries() -> int:
    """The backend-crash retry budget from `BAJUTSU_CRASH_RETRIES`, or the default when unset/invalid."""
    raw = os.environ.get(_CRASH_RETRIES_ENV)
    if not raw:
        return _DEFAULT_CRASH_RETRIES
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_CRASH_RETRIES


# A wall-clock ceiling (seconds) on how long recovery may spend *respawning* after a backend crash,
# across all its `crash_retries`. `crash_retries` alone caps the retry *count*, not the time: a
# runner that crashes and never comes back makes each cold respawn pay a fresh cold-startup ceiling,
# so the count budget silently becomes count x that ceiling — enough to blow a job's `timeout-minutes`
# with a silent hang instead of a loud failure. This budget caps that: once the wall-clock spent
# respawning is exhausted, recovery stops and the work fails loudly (BE-0049), even if retries remain.
# It never blocks the first respawn (the deadline is set at the first crash), so a genuine one-off — a
# respawn that comes back quickly — is still ridden out; the budget bites only when respawns are slow,
# which is exactly the hang. Unset (the default) is unbounded: the count is the only cap, so every
# lane not opting in is byte-for-byte unchanged.
_CRASH_RECOVERY_BUDGET_ENV = "BAJUTSU_CRASH_RECOVERY_BUDGET"


def _default_crash_recovery_budget() -> float | None:
    """The crash-recovery wall-clock budget (s) from the env, or None (unbounded) when unset/invalid.

    A non-positive or unparseable value reads as unbounded, never as zero: the budget only ever
    *reduces* recovery, and disabling recovery entirely is `crash_retries=0`'s job, not this knob's.
    """
    raw = os.environ.get(_CRASH_RECOVERY_BUDGET_ENV)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


def is_infrastructure_fault(exc: BaseException) -> bool:
    """Whether `exc` is a Simulator infrastructure fault (recover) rather than a contract violation.

    The distinction is exactly the exception type the driver already raises: a runner crash, a
    readiness gate that killed the runner, and a lease bring-up that died all surface as
    `base.BackendCrashError` (or a subclass), so recovery is a deterministic `isinstance` branch. A
    mis-resolved selector (`SelectorError`), a refused actuator (`UnsupportedAction`), and a failed
    contract assertion are *not* infrastructure and must keep failing immediately.
    """
    return isinstance(exc, base.BackendCrashError)


@dataclass(frozen=True)
class RetryDecision:
    """The outcome of recording one crash: whether to respawn, and why recovery would stop.

    `budget_spent` distinguishes "the wall-clock budget ran out while the count still allowed a
    respawn" from "the retry count is exhausted", so the caller can report the honest end state.
    """

    will_retry: bool
    budget_spent: bool


class CrashRecoveryBudget:
    """The count + wall-clock retry decision for one unit of recoverable work (a scenario, a test).

    Construct one per unit of work (its wall-clock deadline is per-unit state), then call `on_crash`
    with the 1-based attempt number each time that unit's backend crashes. The budget itself performs
    no leasing or respawning — it only decides whether the caller's respawn loop should continue — so
    the pipeline and the conformance harness share the *decision* while each keeps its own loop and
    failure prose.
    """

    def __init__(self, retries: int, budget: float | None, now: Callable[[], float]) -> None:
        self._retries = retries
        self._budget = budget
        self._now = now
        # Set lazily at the first crash so the first respawn is never blocked by the budget.
        self._deadline: float | None = None

    @property
    def total_attempts(self) -> int:
        """The initial attempt plus every respawn the count allows (`retries + 1`)."""
        return self._retries + 1

    def on_crash(self, attempt: int) -> RetryDecision:
        """Record a crash on the 1-based `attempt` and decide whether another respawn is allowed."""
        # Capture the clock once so both deadline initialisation and the budget check see the same
        # instant — a double call could in theory advance past a tiny budget between the two reads.
        t = self._now()
        # Start the recovery clock at the first crash: `t < deadline` holds here, so the first
        # respawn always proceeds; the budget can only stop a *later* respawn once earlier ones have
        # burned the wall-clock (a slow, never-recovering runner).
        if self._budget is not None and self._deadline is None:
            self._deadline = t + self._budget
        within_count = attempt <= self._retries
        within_budget = self._deadline is None or t < self._deadline
        # The count would allow another respawn but the wall-clock budget is spent: a distinct end
        # state from "attempts exhausted", which the caller surfaces in its failure message.
        return RetryDecision(
            will_retry=within_count and within_budget,
            budget_spent=within_count and not within_budget,
        )

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
the two cannot then drift into different notions of "what a respawn recovers" or "how many respawns
are left" (BE-0334). The classification rests on the exception type the driver already raises, so it
stays a deterministic branch on a Python class: no model sits on the `run`/CI verdict.

Two questions hang off that classification, and BE-0378 gives each its own predicate here so their
consumers cannot drift apart: `recovers_by_respawn` decides a retry, and `is_host_fault` diagnoses a
failure the host caused. A wedged CoreSimulator answers the two differently — it is the host's
fault, and a respawn built out of the very `simctl` calls that just stalled is the wrong instrument
for it — which is exactly why one predicate can no longer serve both.

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
import threading
from collections.abc import Callable
from dataclasses import dataclass

from bajutsu import simctl
from bajutsu.common.drivers import base

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
    the same way, since that class subclasses `RuntimeError`, not `OSError`. A `simctl.DeviceTimeout`
    joins them (BE-0363): a `simctl terminate` that exceeded its deadline says the device is wedged,
    which is a fact about the host and not about this run's wiring, and it reaches here from the two
    environment teardowns that deliberately let a timeout through (`_terminate_app_under_test` /
    `_terminate_runner_app`, and `IosEnvironment.teardown`'s own `Env.terminate`). Treating it as a
    defect below would let a run whose every scenario passed end with no verdicts at all, since
    `mid_run=False`'s re-raise surfaces inside `run`'s `finally: shutdown()` and a raising `finally`
    replaces the results being returned. The timeout still gets its warning, and the discard path
    BE-0363 audited — `_spawn_cold_with_retry`, which calls `discard()` directly — is untouched by
    this, so the signal still reaches the retry that acts on it. Anything
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
    except (subprocess.CalledProcessError, OSError, simctl.DeviceTimeout) as exc:
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


def recovers_by_respawn(exc: BaseException) -> bool:
    """Whether re-leasing a fresh device may repair `exc` — the retry decision, and only that.

    The distinction is exactly the exception type the driver already raises: a runner crash, a
    readiness gate that killed the runner, and a lease bring-up that died all surface as
    `base.BackendCrashError` (or a subclass), so recovery is a deterministic `isinstance` branch. A
    mis-resolved selector (`SelectorError`), a refused actuator (`UnsupportedAction`), and a failed
    contract assertion are *not* infrastructure and must keep failing immediately.

    Narrower than `is_host_fault` on purpose (BE-0378): a `simctl.DeviceTimeout` is the host's fault
    but not a respawn's to fix, since a respawn rebuilds the device out of the same `simctl` calls
    that just stalled — far too heavy an answer to a stall that outlives one deadline and not the
    next. Read this to decide a retry; read `is_host_fault` to report one.
    """
    return isinstance(exc, base.BackendCrashError)


def is_host_fault(exc: BaseException) -> bool:
    """Whether `exc` says something about the host rather than about the code under test.

    The diagnosis, never the retry decision (BE-0378) — call `recovers_by_respawn` for that. It
    covers everything a respawn recovers, plus the wedged CoreSimulator BE-0363 named: a
    `simctl.DeviceTimeout` is raised by a subprocess deadline, never by an assertion, so it can no
    more be a verdict about the app than a runner crash can. A lane that reports it as such shows a
    degrading host as a rising wedge count rather than as a conformance failure somebody re-ran.

    Names `simctl.DeviceTimeout` rather than a platform-neutral type because none exists yet;
    BE-0374 proposes `device_errors.DeviceTimeout` as a second base for the iOS type, and this
    predicate names that instead once it lands, covering every backend that adopts it.
    """
    return isinstance(exc, base.BackendCrashError | simctl.DeviceTimeout)


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


# A wall-clock ceiling (seconds) on how long crash recovery may spend respawning across a *whole*
# run, not just one scenario. `crash_recovery_budget` resets for every new scenario, so a device that
# keeps degrading pays that budget again and again — each respawn its own cold-startup ceiling — until
# a job's own CI `timeout-minutes` cancels it with no diagnosable cause rather than a clean failure
# (an incident `.github/workflows/ios-e2e.yml` already documents). This budget bounds the cumulative
# spend instead: unset (the default) is unbounded, so a lane not opting in is unchanged.
_RUN_CRASH_RECOVERY_BUDGET_ENV = "BAJUTSU_RUN_CRASH_RECOVERY_BUDGET"


def _default_run_crash_recovery_budget() -> float | None:
    """The run-level crash-recovery wall-clock budget (s) from the env, or None (unbounded) when unset/invalid.

    Same unset-or-invalid-reads-as-unbounded parsing as `_default_crash_recovery_budget`.
    """
    raw = os.environ.get(_RUN_CRASH_RECOVERY_BUDGET_ENV)
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value > 0 else None


class RunCrashRecoveryBudget:
    """Wall-clock ceiling on *actual recovery time* accumulated across a whole run, not one scenario.

    `CrashRecoveryBudget` resets a fresh deadline for every scenario, so a device that keeps
    degrading pays each scenario's own budget again — this class shares one accumulator across every
    scenario in a run instead. It bills only the time a scenario's own crash-retry loop actually
    spends recovering (`bajutsu/common/runner/pipeline.py`'s `run_one` times its own retry loop and reports
    the elapsed seconds via `add_recovery_time`), not wall-clock elapsed since some earlier crash: an
    earlier design armed a single deadline at the first crash and never re-armed it, so a long,
    perfectly healthy stretch between two unrelated one-off crashes silently ate into the same
    budget, and a scenario whose backend crashed only once, late in the run, could be denied even its
    first retry — exactly the "residual one-off crash" `crash_retries` exists to ride out. Billing
    the accumulated total instead means 600s means 600s actually spent recovering.

    Deliberately keeps no notion of an in-progress "episode" as shared state: `run_all`'s
    `workers > 1` path can run several scenarios' crash-retry loops concurrently
    (`bajutsu/common/runner/pool.py`'s `lease_defect_lock` guards shared state across that same
    `ThreadPoolExecutor` for the same reason this class needs a lock), and a single shared
    start-of-episode timestamp would let two concurrent recoveries corrupt each other's timing —
    whichever finished first would end "the" episode out from under the other. Each `run_one` call
    times its own loop with a local variable instead (never shared), and only ever calls into this
    class for a threadsafe read (`exhausted`) or a single atomic add (`add_recovery_time`) once its
    own loop is done — so accumulation is correct under any amount of concurrency. Note what the
    total measures, though: a *sum of per-scenario recovery seconds*, not elapsed wall-clock. Under
    `run_all`'s `workers > 1` path, N scenarios recovering at once bill N x the real time, so a
    parallel lane must size its budget against `workers x` the serial figure or it exhausts that
    much sooner and denies a later scenario even its first retry.

    `budget` is public (not `_budget`) so a caller that needs the configured seconds for a failure
    message (`bajutsu/common/runner/pipeline.py`'s `run_one`) reads it straight from the one object that
    also enforces it, rather than keeping a second field of its own in sync by hand.

    `exhausted()` alone is a weaker signal than it looks: the accumulated total bills recovery time
    regardless of outcome (`add_recovery_time` runs whether the scenario that just recovered went on
    to pass or ultimately failed), so it can cross the budget from a single recovery that *succeeded*
    — a device that took a long time to come back but is now healthy. `given_up()`/`mark_given_up()`
    track the stronger signal a caller needs before refusing every later scenario a first attempt: a
    scenario's own crash-retry loop has actually ended in failure *because* this budget, specifically,
    was the binding constraint. Only that — not mere exhaustion — is real evidence the device itself
    is not going to recover.
    """

    def __init__(self, budget: float | None) -> None:
        # Non-positive reads as unbounded, the same way `_default_run_crash_recovery_budget` reads
        # `BAJUTSU_RUN_CRASH_RECOVERY_BUDGET=0` — so a caller pinning the budget directly can never
        # invert the never-block-the-first-crash rule `exhausted()` documents below.
        self.budget = budget if budget is None or budget > 0 else None
        self._spent = 0.0
        self._given_up = False
        self._lock = threading.Lock()

    def exhausted(self) -> bool:
        """Whether the accumulated recovery time already meets the budget. Always `False` when unbounded.

        A budget of exactly 0 seconds accumulated never exhausts a positive budget (`_default_run_crash_recovery_budget`
        never returns a non-positive value, so this only ever compares a real elapsed total against a
        real ceiling) — the run's very first crash always sees `_spent == 0.0`, so it is never blocked
        by this check alone. Says nothing about whether the device can still recover — see the class
        docstring — so a caller deciding whether to skip a *future* scenario's first attempt should
        read `given_up()` instead.
        """
        with self._lock:
            return self.budget is not None and self._spent >= self.budget

    def given_up(self) -> bool:
        """Whether some earlier scenario's crash-retry loop actually failed because this budget was spent.

        The signal that later scenarios should stop paying their own first attempt against the same
        device, unlike bare `exhausted()`, which a successful-but-slow recovery can also trip.
        """
        with self._lock:
            return self._given_up

    def mark_given_up(self) -> None:
        """Record that a scenario's crash-retry loop ended in failure because this budget was spent.

        Called once, from the one place `run_one` already determines that (its `run_budget_spent`
        failure branch), never on a recovery that succeeded.
        """
        with self._lock:
            self._given_up = True

    def add_recovery_time(self, seconds: float) -> None:
        """Bill `seconds` of actual recovery time against the shared run-level total.

        Called once per scenario whose backend crashed at least once, after its own crash-retry loop
        ends (pass or fail) — a scenario that never crashes never calls this, so the common case costs
        no lock acquisition at all.
        """
        with self._lock:
            self._spent += seconds

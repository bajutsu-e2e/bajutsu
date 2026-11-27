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

from ._functions import _CRASH_RECOVERY_BUDGET_ENV as _CRASH_RECOVERY_BUDGET_ENV
from ._functions import _CRASH_RETRIES_ENV as _CRASH_RETRIES_ENV
from ._functions import _DEFAULT_CRASH_RETRIES as _DEFAULT_CRASH_RETRIES
from ._functions import _RUN_CRASH_RECOVERY_BUDGET_ENV as _RUN_CRASH_RECOVERY_BUDGET_ENV
from ._functions import _default_crash_recovery_budget as _default_crash_recovery_budget
from ._functions import _default_crash_retries as _default_crash_retries
from ._functions import _default_run_crash_recovery_budget as _default_run_crash_recovery_budget
from ._functions import _logger as _logger
from ._functions import guarded_teardown, is_host_fault, recovers_by_respawn
from .crash_recovery_budget import CrashRecoveryBudget
from .retry_decision import RetryDecision
from .run_crash_recovery_budget import RunCrashRecoveryBudget

__all__ = [
    "CrashRecoveryBudget",
    "RetryDecision",
    "RunCrashRecoveryBudget",
    "guarded_teardown",
    "is_host_fault",
    "recovers_by_respawn",
]

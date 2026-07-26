"""Orchestrator — the deterministic Tier-2 run loop.

Each step runs as act -> (wait) -> verify. Pass/fail comes from machine assertions only; no AI
is involved. Execution stops at the first failure.

Split by concern (BE-0043): `types` (results/protocols), `actions` (one-shot effects), `waits`
(condition polling), `substitution` (${...} tokens), `evidence_rules` (capturePolicy / extract),
and `loop` (the run loop itself). The public API — and the internals the test suite drives
directly — are re-exported here, so `from bajutsu.orchestrator import ...` is unchanged.
"""

from __future__ import annotations

from bajutsu.orchestrator.actions import _action_of, _do_action
from bajutsu.orchestrator.loop import run_scenario
from bajutsu.orchestrator.substitution import _interp_asserts, _interp_step
from bajutsu.orchestrator.types import (
    DEFAULT_ALERT_POLL_INTERVAL,
    AlertEvent,
    AlertGuardConfig,
    BlockedHandler,
    Clock,
    DeviceControl,
    MailboxReader,
    NetworkSource,
    ProgressFn,
    RealClock,
    RelaunchFn,
    RunResult,
    SkippedCapture,
    StepOutcome,
    scenario_slug,
)
from bajutsu.orchestrator.waits import _POLL, _wait

__all__ = [
    "DEFAULT_ALERT_POLL_INTERVAL",
    "_POLL",
    "AlertEvent",
    "AlertGuardConfig",
    "BlockedHandler",
    "Clock",
    "DeviceControl",
    "MailboxReader",
    "NetworkSource",
    "ProgressFn",
    "RealClock",
    "RelaunchFn",
    "RunResult",
    "SkippedCapture",
    "StepOutcome",
    "_action_of",
    "_do_action",
    "_interp_asserts",
    "_interp_step",
    "_wait",
    "run_scenario",
    "scenario_slug",
]

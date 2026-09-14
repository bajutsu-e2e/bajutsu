"""Shared types for the orchestrator: protocols, result dataclasses, and injected callables.

These carry no run logic, so they can be imported by every other orchestrator module (and by
the runner) without a cycle.
"""

from ._functions import _BLOCKED_SCREEN_NOTE as _BLOCKED_SCREEN_NOTE
from ._functions import _UNCLEARED_PROMPT_NOTE as _UNCLEARED_PROMPT_NOTE
from ._functions import _UNDECLARED_INTERRUPTION_NOTE as _UNDECLARED_INTERRUPTION_NOTE
from ._functions import _UNHANDLED_ALERT_NOTE as _UNHANDLED_ALERT_NOTE
from ._functions import _alert_button as _alert_button
from ._functions import _no_network as _no_network
from ._functions import (
    alert_block_note,
    drain_actuations,
    drain_interruptions,
    identified_alert_rules,
    match_alert_rule,
    matching_alert_rule,
    push_interruption_policy,
    sanitize_source_stem,
    scenario_slug,
    selector_names_button,
    subtract_labels,
    uncleared_prompt_note,
    undeclared_interruption_note,
)
from ._shared import BlockedHandler, NetworkSource, ProgressFn, RelaunchFn, WallClock
from .alert_event import AlertEvent
from .alert_guard_config import _NATIVE_TAP_TIMEOUT as _NATIVE_TAP_TIMEOUT
from .alert_guard_config import (
    DEFAULT_ALERT_POLL_INTERVAL,
    AlertGuardConfig,
    NativeAlertState,
    NotTappable,
)
from .clock import Clock
from .device_control import DeviceControl
from .drained_interruption_events import DrainedInterruptionEvents
from .mailbox_reader import MailboxReader
from .real_clock import RealClock
from .resolved_alert_rule import ResolvedAlertRule
from .run_result import RunResult
from .selection_state import SelectionState
from .skipped_capture import SkippedCapture
from .step_outcome import StepOutcome
from .undeclared_interruption import UndeclaredInterruption

__all__ = [
    "DEFAULT_ALERT_POLL_INTERVAL",
    "AlertEvent",
    "AlertGuardConfig",
    "BlockedHandler",
    "Clock",
    "DeviceControl",
    "DrainedInterruptionEvents",
    "MailboxReader",
    "NativeAlertState",
    "NetworkSource",
    "NotTappable",
    "ProgressFn",
    "RealClock",
    "RelaunchFn",
    "ResolvedAlertRule",
    "RunResult",
    "SelectionState",
    "SkippedCapture",
    "StepOutcome",
    "UndeclaredInterruption",
    "WallClock",
    "alert_block_note",
    "drain_actuations",
    "drain_interruptions",
    "identified_alert_rules",
    "match_alert_rule",
    "matching_alert_rule",
    "push_interruption_policy",
    "sanitize_source_stem",
    "scenario_slug",
    "selector_names_button",
    "subtract_labels",
    "uncleared_prompt_note",
    "undeclared_interruption_note",
]

"""Condition waits: poll the screen (or the observed network) until satisfied, never a fixed
sleep — this is what keeps the run deterministic without `sleep`."""

from ._alert_guard_gate import _GUARD_DEBOUNCE_POLLS as _GUARD_DEBOUNCE_POLLS
from ._alert_guard_gate import _TREE_DISMISS_MAX_TAPS as _TREE_DISMISS_MAX_TAPS
from ._alert_guard_gate import _TREE_RETAP_DELAY as _TREE_RETAP_DELAY
from ._alert_guard_gate import _AlertGuardGate as _AlertGuardGate
from ._functions import _DISMISS_SETTLE_TIMEOUT as _DISMISS_SETTLE_TIMEOUT
from ._functions import _FLOOR_ENV as _FLOOR_ENV
from ._functions import _POLL as _POLL
from ._functions import _SETTLE_POLLS as _SETTLE_POLLS
from ._functions import _STEP_TAP_TIMEOUT as _STEP_TAP_TIMEOUT
from ._functions import _SYSTEM_ALERT_POLL as _SYSTEM_ALERT_POLL
from ._functions import _TRANSITION_QUIESCENCE as _TRANSITION_QUIESCENCE
from ._functions import _TREE_DISMISS_DECLINE_GIVEUP_FLOOR as _TREE_DISMISS_DECLINE_GIVEUP_FLOOR
from ._functions import _adaptive_sleep as _adaptive_sleep
from ._functions import _alert_timeout_reason as _alert_timeout_reason
from ._functions import _decline_giveup as _decline_giveup
from ._functions import _effective_timeout as _effective_timeout
from ._functions import _exists as _exists
from ._functions import _timeout_floor as _timeout_floor
from ._functions import _wait as _wait
from ._functions import _wait_settled as _wait_settled
from ._functions import _wait_settled_by_signal as _wait_settled_by_signal
from ._functions import _with_block_note as _with_block_note
from ._functions import describe_wait, settle_after_alert_dismiss, wait_for_system_alert
from ._heartbeat import _TICK_INTERVAL as _TICK_INTERVAL
from ._heartbeat import _Heartbeat as _Heartbeat
from ._shared import WaitTick
from ._shared import _logger as _logger
from .wait_trace import WaitTrace

__all__ = [
    "WaitTick",
    "WaitTrace",
    "describe_wait",
    "settle_after_alert_dismiss",
    "wait_for_system_alert",
]

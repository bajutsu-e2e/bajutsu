"""The deterministic Tier-2 run loop: act -> (wait) -> verify, per step.

Pass/fail comes from machine assertions only; no AI is involved. Execution stops at the first
failure. Backend-agnostic via base.Driver (real driver or FakeDriver); evidence, relaunch, and
device control are injected by the runner.
"""

from ._functions import _EMAIL_POLL as _EMAIL_POLL
from ._functions import _READ_ONCE_KINDS as _READ_ONCE_KINDS
from ._functions import _TIP_MAX_DISMISSES as _TIP_MAX_DISMISSES
from ._functions import _capture_visual_actual as _capture_visual_actual
from ._functions import _clipboard_for as _clipboard_for
from ._functions import _dismiss_blocking_tip as _dismiss_blocking_tip
from ._functions import _dispatch_after as _dispatch_after
from ._functions import _do_email as _do_email
from ._functions import _evaluate_expect as _evaluate_expect
from ._functions import _fail_reason as _fail_reason
from ._functions import _hides_touch_markers as _hides_touch_markers
from ._functions import _poll_asserts as _poll_asserts
from ._functions import _resolve_video_start_offset as _resolve_video_start_offset
from ._functions import _run_for_each as _run_for_each
from ._functions import _run_if as _run_if
from ._functions import _run_step_body as _run_step_body
from ._functions import _run_steps as _run_steps
from ._functions import _settle_extract_read as _settle_extract_read
from ._functions import _tip_poll_hook as _tip_poll_hook
from ._functions import run_scenario
from ._interrupt_guard import _INTERRUPT_MAX_FIRES as _INTERRUPT_MAX_FIRES
from ._interrupt_guard import _InterruptGuard as _InterruptGuard
from ._loop_config import _LoopConfig as _LoopConfig
from ._screen_read import _ScreenRead as _ScreenRead
from ._shared import _ExecSteps as _ExecSteps
from ._shared import _logger as _logger
from ._step_counter import _StepCounter as _StepCounter
from ._step_runner import _StepRunner as _StepRunner
from .step_loop_state import StepLoopState
from .target_roster import MemberStatus, TargetRoster

__all__ = ["MemberStatus", "StepLoopState", "TargetRoster", "run_scenario"]

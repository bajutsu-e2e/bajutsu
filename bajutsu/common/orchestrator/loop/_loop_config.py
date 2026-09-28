"""The run-invariant inputs the step loop reads but never mutates."""

from __future__ import annotations

from dataclasses import dataclass

from bajutsu.common.assertions import EvalContext
from bajutsu.common.cancellation import CancelSource, not_cancelled
from bajutsu.common.drivers import base
from bajutsu.common.drivers.webview import DomSource
from bajutsu.common.evidence import EvidenceSink
from bajutsu.common.evidence.network import Collector, TransitionSource
from bajutsu.common.orchestrator.types import (
    AlertGuardConfig,
    Clock,
    DeviceControl,
    MailboxReader,
    NetworkSource,
    ProgressFn,
    RelaunchFn,
)
from bajutsu.common.scenario import Interrupt, Scenario


@dataclass(frozen=True)
class _LoopConfig:
    """The run-invariant inputs the step loop reads but never mutates.

    Kept apart from `StepLoopState` (the mutable, shared bookkeeping) so a step handler takes the
    whole loop context as two values: `self.state` for what changes step to step, `self.cfg` for
    what is fixed for the run.
    """

    driver: base.Driver
    scenario: Scenario
    clock: Clock
    sink: EvidenceSink
    alert_guard: AlertGuardConfig | None
    wants_screen_changed: bool
    # Added to a monotonic `clock.now()` instant to get its wall-clock epoch — the same conversion
    # `pipeline.py` applies to network receive times (`RunResult.wall_offset_s`). The loop carries
    # only this delta, never the wall instant itself, so no timing decision can read a wall clock.
    wall_offset_s: float
    sid: str
    network: NetworkSource
    relaunch: RelaunchFn | None
    control: DeviceControl | None
    progress: ProgressFn | None
    mailbox: MailboxReader | None
    ctx: EvalContext | None
    webview_bridge: DomSource | None
    transitions: TransitionSource
    interrupts: list[Interrupt] | None
    locale: str | None
    capture: list[str] | None
    # Which lifecycle phase this loop is running (BE-0392): "" for the scenario's own `steps`,
    # "before" / "after" for the hook phases. Each phase counts its steps from zero, so the label
    # also namespaces their evidence `step_id`s — without it a hook's `step0` would write into the
    # directory the scenario's own first step already owns.
    phase: str = ""
    # Whether this run has been asked to stop (BE-0370). Read at each step boundary below and handed
    # to the poll loops that back `wait` / `assert`, so cancellation is noticed at a point the
    # pipeline already tolerates a pause rather than partway through an actuation.
    cancelled: CancelSource = not_cancelled
    # The in-app control channel and whether this scenario hides touch markers (BE-0365), both read
    # only by a step-level `visual` assert's capture — mirroring the same two inputs
    # `_capture_visual_actual` already takes at `expect`. `channel` is per-target (BE-0428), replaced
    # in `_config_for` from `TargetRuntime.channel`; `hide_markers` is a property of the whole
    # visual-capture group, not of one target (see `TargetRuntime`'s own docstring), so it is read
    # from the primary's launch env once and never replaced per target.
    channel: Collector | None = None
    hide_markers: bool = False

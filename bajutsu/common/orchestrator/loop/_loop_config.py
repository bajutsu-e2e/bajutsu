"""The run-invariant inputs the step loop reads but never mutates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from bajutsu.common.assertions import EvalContext
from bajutsu.common.cancellation import CancelSource, not_cancelled
from bajutsu.common.drivers import base
from bajutsu.common.drivers.webview import DomSource
from bajutsu.common.evidence import EvidenceSink
from bajutsu.common.evidence.network import TransitionSource
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
    # The lease's own sweep for the platform's report of an app crash (BE-0424), called the moment a
    # driver confirms one rather than after the scenario finishes: a teardown `relaunch` in the same
    # `after` phase re-stamps the launch marker the sweep matches against, so reading it any later
    # would widen the window past the crash it exists to attribute. A callable the loop only invokes,
    # like `relaunch` and `mailbox` above — never state it writes through, so it belongs here and not
    # on `StepLoopState`. `None` on every caller with no lease behind it.
    capture_app_crash: Callable[[], list[tuple[str, bytes]]] | None = None
    # Which lifecycle phase this loop is running (BE-0392): "" for the scenario's own `steps`,
    # "before" / "after" for the hook phases. Each phase counts its steps from zero, so the label
    # also namespaces their evidence `step_id`s — without it a hook's `step0` would write into the
    # directory the scenario's own first step already owns.
    phase: str = ""
    # Whether this run has been asked to stop (BE-0370). Read at each step boundary below and handed
    # to the poll loops that back `wait` / `assert`, so cancellation is noticed at a point the
    # pipeline already tolerates a pause rather than partway through an actuation.
    cancelled: CancelSource = not_cancelled

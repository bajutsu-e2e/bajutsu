"""The pause point `bajutsu run --step` hangs on the step loop."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bajutsu.common.drivers import base


@dataclass(frozen=True)
class StepPause:
    """What a gate is told at a step boundary: which step, and the live driver to look at."""

    # The step's index in the run's shared counter — the `step N` a failure reason names.
    index: int
    # The step's authored `name`, when it has one (a `--break <name>` target).
    name: str | None
    label: str
    driver: base.Driver
    # The failed step's reason; set only for a pause *after* the step, `None` before it.
    failure: str | None = None


class StepGate(Protocol):
    """Hold the step loop at a boundary while an operator inspects the app (`run --step`).

    The loop calls a gate and never reads its decision: a gate returns to let the run go on, and
    raises `RunCancelled` to end it, so the verdict still comes only from the scenario's own
    assertions. A gate that lets the operator actuate the app reports it through `manual_action`,
    which the loop stamps on the result so a hand-driven run cannot pass as a green one.
    """

    @property
    def manual_action(self) -> str | None:
        """The verb of the first command that acted on the app during a pause, or `None`."""

    def before_step(self, pause: StepPause) -> bool:
        """Called before a step runs; may block, and raises `RunCancelled` to end the run.

        Returns:
            Whether the gate held the loop. The app may have changed while it waited, so the loop
            then reads the next step's `before` fresh instead of reusing the previous `after`.
        """

    def after_failure(self, pause: StepPause) -> None:
        """Called once a step has failed, before the failure ends the scenario.

        Never ends the run itself: the failure already does, and replacing its reason with
        `cancelled` would hide the very failure the operator stopped to look at.
        """

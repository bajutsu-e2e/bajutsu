"""How a backend that answers alerts itself paces its presses on one showing (BE-0461)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AlertPacing:
    """The pacing values the orchestrator pushes beside an interruption policy.

    The orchestrator owns them (`AlertGuardConfig.pacing`) and the backend only applies them, so a
    backend that answers an alert inside a request presses and gives up on it exactly as the
    orchestrator's own guard would.

    Attributes:
        poll_interval: The least time between two checks of the system alert surface.
        retap_delay: The least time before a showing that was pressed is pressed again.
        max_taps: How many presses one showing gets before it is recorded as given up.
        give_up: How long a present but unhittable in-tree button is retried before its showing is
            recorded as given up.
    """

    poll_interval: float
    retap_delay: float
    max_taps: int
    give_up: float

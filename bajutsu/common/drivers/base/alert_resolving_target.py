"""The seam for a backend that answers system alerts inside the requests that read the screen."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from .resolving_read import ResolveMode, ResolvingRead, SystemAlertResolution


@runtime_checkable
class AlertResolvingTarget(Protocol):
    """A backend that answers the alert the pushed policy names when a request reads the screen.

    A narrow opt-in beside `InterruptionPolicyTarget`, advertised by `Capability.RESOLVE_ALERTS`
    (BE-0461). The orchestrator reads the tree through it under a governing guard, so an alert
    clears where the screen is read instead of on a watcher's clock of its own. `Driver.query` stays
    a pure read on every backend, so evidence keeps showing the real screen.
    """

    def query_resolving(self, mode: ResolveMode, *, force_check: bool = False) -> ResolvingRead:
        """Answer the alert the policy names, then read the tree.

        Args:
            mode: `true` for the full resolution; `inTree` to leave SpringBoard alone.
            force_check: Skip the SpringBoard check's rate limit — a recovery read after a refused
                actuation or a failed check.

        Raises:
            XcuitestChannelError: The backend's reply lacks its records (a backend build predating
                the capability), so whether anything was pressed is unknown.
        """
        ...

    def resolve_system_alert(self, labels: Sequence[str]) -> SystemAlertResolution:
        """Answer the SpringBoard alert by policy, only while it still offers exactly *labels*.

        A `handleSystemAlert` step sends this for an alert it judged not to be its own, with the
        labels its own last read saw, so its own alert arriving in that one's place is never
        answered by policy.
        """
        ...

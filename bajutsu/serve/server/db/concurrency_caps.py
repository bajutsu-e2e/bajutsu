"""The server's caps on jobs in flight, as the one value a lease applies to a group's jobs."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ConcurrencyCaps:
    """How many jobs may run at once: in the deployment, per user, and per org. Each `<= 0` is
    unlimited (BE-0051, BE-0015 7c-3, BE-0016 Tier B)."""

    total: int = 0
    per_user: int = 0
    per_org: int = 0

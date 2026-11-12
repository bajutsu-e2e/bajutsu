"""A job written to the queue as part of a group — the boundary type a group registration takes."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class QueuedJob:
    """One job of a group registration (BE-0484): what the jobs table stores for it."""

    id: str
    spec: dict[str, Any]
    # The tokens a worker must advertise to lease the job (BE-0166), stored as given.
    capabilities: list[str] = field(default_factory=list)
    actor: str | None = None

"""A job a worker has leased — the boundary type the seam hands out."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class LeasedJob:
    """A job that has been leased by a worker — the boundary type the seam hands out."""

    id: str
    org_id: str
    spec: dict[str, Any]
    # The tokens the job was routed by, handed to the worker so it re-checks them at claim (BE-0467).
    capabilities: list[str] = field(default_factory=list)
    # The `run-set` group and what its jobs share (BE-0484), so a worker shares uploads between the
    # jobs it holds; None for a job outside a group.
    group_id: str | None = None
    compat_key: str | None = None

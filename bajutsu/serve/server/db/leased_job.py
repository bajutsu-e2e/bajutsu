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

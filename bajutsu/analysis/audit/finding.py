"""One determinism risk found in a scenario, located and explained for a human to fix."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Finding:
    """One determinism risk in a scenario, located and explained for a human to fix."""

    where: str  # the step/assertion the risk is in (e.g. "tap", "expect: value")
    kind: (
        str  # fragile-selector | moderate-selector | coordinate-gesture | loose-wait | fixed-sleep
    )
    detail: str

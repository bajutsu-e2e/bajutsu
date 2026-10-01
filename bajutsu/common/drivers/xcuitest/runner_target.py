"""Which app a runner shared by a device group currently addresses (BE-0447)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class RunnerTarget:
    """The bundle id the shared runner's base app is set to right now.

    One per device: every member's driver holds the same instance, so a driver retargets the runner
    only when the last request came from another member, never on every call.
    """

    current: str

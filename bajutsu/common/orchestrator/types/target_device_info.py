"""Which device one declared target ran on, for a multi-target run's report (BE-0428)."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TargetDeviceInfo:
    """The device facts `RunResult` carries once per scenario, carried once per declared target.

    A multi-target run has no single answer for "which backend / which device", so the singular
    fields on `RunResult` stay empty and one of these is recorded per declared target instead.
    Field for field the same information, so a report renders one target's row exactly as it
    renders a single-target run's header.
    """

    backend: str = ""
    engine: str = ""
    device: str = ""
    device_name: str = ""
    device_runtime: str = ""

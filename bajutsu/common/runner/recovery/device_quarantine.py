"""The run-wide allowance for quarantining a device after an ordinary failure (BE-0376)."""

from __future__ import annotations

import threading


class DeviceQuarantine:
    """Grants the ordinary-failure quarantine at most once per run (BE-0376).

    The crash retry's own per-scenario `replaced` allowance bounds what one scenario can mint, and
    no more: under it alone, a host whose capture pipeline stalls chronically would mint one
    `bajutsu-recovered-*` device per failing scenario. A replacement that stalls again is evidence
    that the host, not the device, is degraded, and one more device does not fix a host. So this path
    spends one run-wide allowance on top of the per-scenario one, and only this path does: the crash
    retry keeps its own per-scenario rung unchanged.

    Locked although the gate in front of it (`device_replacement_supported`) already implies one
    worker, so the allowance stays sound if that gate ever widens past a pool of one device.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spent = False

    def claim(self) -> bool:
        """Spend the run's one quarantine, answering whether this caller got it."""
        with self._lock:
            if self._spent:
                return False
            self._spent = True
            return True
